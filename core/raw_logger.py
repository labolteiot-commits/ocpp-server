"""
Log OCPP brut par borne (Phase 0.2 — sprint OCPP 2026-04-22).

La lib `ocpp` (MobilityHouse) émet deux types de logs INFO depuis son module
`ocpp.charge_point`:
    LOGGER.info("%s: receive message %s", self.id, raw_msg)
    LOGGER.info("%s: send %s",            self.id, raw_msg)

Ce module ajoute un handler rotatif par borne qui filtre ces logs par
charge_point_id et les écrit dans logs/ocpp_raw/<id>.log. Permet d'auditer
CALL/CALLRESULT/CALLERROR brut sans polluer le log global.

Usage:
    from core.raw_logger import attach, detach
    attach("BORNE-CABANON4")   # à l'ouverture de la session WS
    detach("BORNE-CABANON4")   # à la fermeture
"""
from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

from config import BASE_DIR

_RAW_DIR = BASE_DIR / "logs" / "ocpp_raw"
_RAW_DIR.mkdir(parents=True, exist_ok=True)

_HANDLERS: dict[str, logging.Handler] = {}
_MAX_BYTES = 5 * 1024 * 1024   # 5 MB par fichier
_BACKUP_COUNT = 5               # 5 fichiers de rotation = 25 MB max par borne


class _ChargerFilter(logging.Filter):
    """
    N'accepte que les records dont le charge_point_id correspond.

    La lib ocpp émet logger.info("%s: ...", self.id, ...) donc le charger_id
    est dans record.args[0]. Fallback : préfixe textuel "ID: " dans le message.
    """
    def __init__(self, charger_id: str):
        super().__init__()
        self._charger_id = charger_id
        self._prefix = f"{charger_id}:"

    def filter(self, record: logging.LogRecord) -> bool:
        if record.args:
            first = record.args[0] if isinstance(record.args, tuple) else None
            if first is not None and str(first) == self._charger_id:
                return True
        try:
            return record.getMessage().startswith(self._prefix)
        except Exception:
            return False


def _safe_filename(charger_id: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in charger_id) or "unknown"


def attach(charger_id: str) -> None:
    """Attache un handler rotatif 'logs/ocpp_raw/<id>.log' au logger 'ocpp'."""
    if not charger_id or charger_id in _HANDLERS:
        return
    path = _RAW_DIR / f"{_safe_filename(charger_id)}.log"
    h = logging.handlers.RotatingFileHandler(
        path, maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT, encoding="utf-8"
    )
    h.setLevel(logging.INFO)
    h.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    ))
    h.addFilter(_ChargerFilter(charger_id))
    logging.getLogger("ocpp").addHandler(h)
    _HANDLERS[charger_id] = h


def detach(charger_id: str) -> None:
    """Retire le handler (fin de session WS)."""
    h = _HANDLERS.pop(charger_id, None)
    if h is None:
        return
    logging.getLogger("ocpp").removeHandler(h)
    try:
        h.close()
    except Exception:
        pass


def log_path(charger_id: str) -> Path:
    """Retourne le chemin du fichier de log pour une borne (utile pour l'API)."""
    return _RAW_DIR / f"{_safe_filename(charger_id)}.log"
