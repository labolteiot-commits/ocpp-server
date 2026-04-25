# core/activity_logger.py
"""
Sprint 32 — Capture unifiée de TOUS les messages OCPP en DB.

Architecture :
  * Un handler logging.Handler personnalisé est attaché au logger 'ocpp'
    (lib MobilityHouse). La lib émet :
        LOGGER.info("%s: receive message %s", self.id, raw_msg)
        LOGGER.info("%s: send %s",            self.id, raw_msg)
  * Le handler parse chaque message JSON OCPP `[msg_type, unique_id, ...]`,
    pousse dans une asyncio.Queue.
  * Un worker async consomme la queue par batch (jusqu'à 50 lignes ou 1s)
    et INSERT en DB. Pas bloquant pour le hot path OCPP.

Corrélation CALL ↔ CALLRESULT :
  * Cache mémoire {unique_id → action} alimenté à chaque CALL.
  * Lors d'un CALLRESULT (msg_type=3) ou CALLERROR (msg_type=4),
    on récupère l'Action depuis le cache (TTL ~5 min, taille max 10k).

Coexiste avec `core/raw_logger.py` (qui écrit en fichier) — les deux
handlers sont attachés au même logger sans interférence.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections import OrderedDict
from datetime import datetime
from typing import Optional

from core.logging import log

# ── Constantes ──────────────────────────────────────────────────────────────

MSG_TYPE_CALL        = 2
MSG_TYPE_CALLRESULT  = 3
MSG_TYPE_CALLERROR   = 4

_QUEUE_MAXSIZE       = 5000          # safety : si la DB lague, on drop au-delà
_BATCH_MAX_SIZE      = 50
_BATCH_MAX_WAIT_S    = 1.0
_CORR_CACHE_MAX      = 10_000
_CORR_CACHE_TTL_S    = 300           # 5 min — assez pour qu'un CALLRESULT trouve son CALL

# Regex match sur les 2 patterns émis par la lib ocpp/charge_point.py :
#   "%s: receive message %s"  → direction='in'
#   "%s: send %s"             → direction='out'
_RE_RECEIVE = re.compile(r"^(?P<id>[^:]+):\s*receive message\s+(?P<msg>.+)$", re.DOTALL)
_RE_SEND    = re.compile(r"^(?P<id>[^:]+):\s*send\s+(?P<msg>.+)$",            re.DOTALL)


# ── Cache de corrélation unique_id → action ─────────────────────────────────

class _CorrelationCache:
    """OrderedDict avec TTL + cap. Mémorise quel Action correspond à un unique_id
    pour pouvoir étiqueter les CALLRESULT/CALLERROR qui n'embarquent pas l'action.
    """
    def __init__(self, max_size: int = _CORR_CACHE_MAX, ttl_s: float = _CORR_CACHE_TTL_S):
        self._cache: "OrderedDict[str, tuple[str, float]]" = OrderedDict()
        self._max = max_size
        self._ttl = ttl_s

    def put(self, unique_id: str, action: str) -> None:
        if not unique_id or not action:
            return
        now = time.monotonic()
        if unique_id in self._cache:
            self._cache.move_to_end(unique_id)
        self._cache[unique_id] = (action, now)
        # Cap LRU
        while len(self._cache) > self._max:
            self._cache.popitem(last=False)

    def get(self, unique_id: str) -> Optional[str]:
        if not unique_id:
            return None
        entry = self._cache.get(unique_id)
        if entry is None:
            return None
        action, ts = entry
        if (time.monotonic() - ts) > self._ttl:
            self._cache.pop(unique_id, None)
            return None
        return action

    def __len__(self) -> int:
        return len(self._cache)


# ── Handler logging → asyncio.Queue ─────────────────────────────────────────

class _ActivityCaptureHandler(logging.Handler):
    """Handler attaché au logger 'ocpp'. Parse chaque message et pousse
    un dict dans la queue. Pas de await ici — c'est sync, exécuté dans
    le thread d'émission du log (qui se trouve être l'event loop pour
    nos appels async, mais on garde la sémantique propre).
    """
    def __init__(self, queue: asyncio.Queue, corr_cache: _CorrelationCache):
        super().__init__(level=logging.INFO)
        self._queue = queue
        self._corr = corr_cache

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage()
        except Exception:
            return

        # Match les 2 patterns
        direction: Optional[str] = None
        m = _RE_RECEIVE.match(msg)
        if m:
            direction = "in"
        else:
            m = _RE_SEND.match(msg)
            if m:
                direction = "out"
        if direction is None:
            return  # autre log de la lib ocpp (debug, autre)

        charger_id = m.group("id").strip()
        raw_json   = m.group("msg").strip()

        # Parse le JSON OCPP : [msg_type, unique_id, ...]
        try:
            arr = json.loads(raw_json)
        except Exception:
            return  # message malformé — pas d'audit possible
        if not isinstance(arr, list) or len(arr) < 2:
            return

        try:
            msg_type = int(arr[0])
        except Exception:
            return
        unique_id = str(arr[1]) if len(arr) >= 2 else None

        action: Optional[str]            = None
        payload_obj                       = None
        error_code: Optional[str]         = None
        error_description: Optional[str]  = None
        connector_id: Optional[int]       = None
        transaction_id: Optional[int]     = None

        if msg_type == MSG_TYPE_CALL:
            # [2, uniqueId, action, payload]
            if len(arr) >= 4:
                action = str(arr[2])
                payload_obj = arr[3]
                if unique_id and action:
                    self._corr.put(unique_id, action)
        elif msg_type == MSG_TYPE_CALLRESULT:
            # [3, uniqueId, payload]
            if len(arr) >= 3:
                payload_obj = arr[2]
            action = self._corr.get(unique_id) if unique_id else None
        elif msg_type == MSG_TYPE_CALLERROR:
            # [4, uniqueId, errorCode, errorDescription, errorDetails]
            if len(arr) >= 4:
                error_code = str(arr[2])
                error_description = str(arr[3])
            if len(arr) >= 5:
                payload_obj = arr[4]
            action = self._corr.get(unique_id) if unique_id else None
        else:
            return  # type inconnu

        # Extraction connector_id / transaction_id si présents dans payload
        if isinstance(payload_obj, dict):
            cid = payload_obj.get("connectorId") or payload_obj.get("connector_id")
            if isinstance(cid, int):
                connector_id = cid
            tid = payload_obj.get("transactionId") or payload_obj.get("transaction_id")
            if isinstance(tid, int):
                transaction_id = tid

        try:
            payload_str = json.dumps(payload_obj, default=str, ensure_ascii=False) \
                if payload_obj is not None else None
        except Exception:
            payload_str = None

        item = {
            "charger_id":        charger_id,
            "direction":         direction,
            "msg_type":          msg_type,
            "action":            action,
            "unique_id":         unique_id,
            "payload":           payload_str,
            "error_code":        error_code,
            "error_description": error_description,
            "connector_id":      connector_id,
            "transaction_id":    transaction_id,
            "timestamp":         datetime.utcnow(),
        }
        try:
            self._queue.put_nowait(item)
        except asyncio.QueueFull:
            # Drop silencieux : préfère perdre 1 ligne d'audit que bloquer le hot path OCPP
            pass


# ── Worker batch INSERT ─────────────────────────────────────────────────────

async def _worker_loop(queue: asyncio.Queue) -> None:
    """Consomme la queue par batch et INSERT en DB."""
    from db.database import AsyncSessionLocal
    from db.models import ChargerActivityLog
    from sqlalchemy import select
    from db.models import Charger

    log.info("[S32-activity] worker démarré")

    # Cache des charger_id existants en DB (pour ne pas violer la FK quand
    # une borne se connecte avant son insertion en DB par auto-register).
    known_chargers: set[str] = set()

    async def _refresh_known(db) -> None:
        try:
            r = await db.execute(select(Charger.id))
            for row in r.all():
                known_chargers.add(row[0])
        except Exception:
            pass

    while True:
        try:
            # Attendre le 1er item
            first = await queue.get()
            batch = [first]
            # Drainer jusqu'à BATCH_MAX_SIZE ou 1s écoulée
            deadline = asyncio.get_event_loop().time() + _BATCH_MAX_WAIT_S
            while len(batch) < _BATCH_MAX_SIZE:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    break
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=remaining)
                    batch.append(item)
                except asyncio.TimeoutError:
                    break

            # Persist
            try:
                async with AsyncSessionLocal() as db:
                    if not known_chargers:
                        await _refresh_known(db)
                    rows_to_add = []
                    unknowns = set()
                    for item in batch:
                        cid = item["charger_id"]
                        if cid not in known_chargers:
                            unknowns.add(cid)
                    if unknowns:
                        # Refresh + filter — si toujours inconnus, on skip
                        # (bornes inconnues = pas de FK valide)
                        await _refresh_known(db)
                    for item in batch:
                        if item["charger_id"] not in known_chargers:
                            continue
                        rows_to_add.append(ChargerActivityLog(**item))
                    if rows_to_add:
                        db.add_all(rows_to_add)
                        await db.commit()
            except Exception as e:
                log.warning("[S32-activity] insert batch échoué",
                            error=str(e), batch_size=len(batch))
        except asyncio.CancelledError:
            log.info("[S32-activity] worker stoppé")
            break
        except Exception as e:
            log.warning("[S32-activity] boucle worker erreur", error=str(e))
            await asyncio.sleep(1)


# ── État global du module ───────────────────────────────────────────────────

_queue: Optional[asyncio.Queue] = None
_handler: Optional[_ActivityCaptureHandler] = None
_worker_task: Optional[asyncio.Task] = None
_corr_cache: Optional[_CorrelationCache] = None


def init_capture() -> None:
    """À appeler UNE FOIS au démarrage du serveur, AVANT que les bornes ne
    se connectent. Idempotent."""
    global _queue, _handler, _corr_cache
    if _handler is not None:
        return
    _queue = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
    _corr_cache = _CorrelationCache()
    _handler = _ActivityCaptureHandler(_queue, _corr_cache)
    logging.getLogger("ocpp").addHandler(_handler)
    log.info("[S32-activity] handler attaché au logger 'ocpp'")


async def start_worker() -> None:
    """Démarre le worker asyncio. À appeler dans un contexte async (ocpp_server.start())."""
    global _worker_task
    if _queue is None:
        init_capture()
    if _worker_task is None or _worker_task.done():
        _worker_task = asyncio.create_task(_worker_loop(_queue))


async def purge_activity(days: int = 90) -> int:
    """Purge les lignes activity_log plus vieilles que N jours."""
    from datetime import timedelta
    from db.database import AsyncSessionLocal
    from sqlalchemy import text
    cutoff = datetime.utcnow() - timedelta(days=days)
    async with AsyncSessionLocal() as db:
        r = await db.execute(
            text("DELETE FROM charger_activity_log WHERE timestamp < :cutoff"),
            {"cutoff": cutoff},
        )
        await db.commit()
        deleted = r.rowcount or 0
        if deleted:
            log.info("[S32-activity] purge", deleted=deleted, days=days)
        return deleted


def stats() -> dict:
    """État courant pour debug/monitoring."""
    return {
        "queue_size":  _queue.qsize() if _queue else 0,
        "queue_max":   _QUEUE_MAXSIZE,
        "corr_cached": len(_corr_cache) if _corr_cache else 0,
        "worker_running": _worker_task is not None and not _worker_task.done(),
    }
