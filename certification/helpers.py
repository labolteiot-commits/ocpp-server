"""Helpers partagés par les tests de certification.

``TestContext`` encapsule l'accès au serveur OCPP, la DB, l'activity log,
le bus d'events, et les primitives high-level (prompt technicien,
wait_for_status, etc.) utilisées par chaque cas de test.

Principes :
  * Pas d'accès direct aux handlers ChargePoint — on passe TOUJOURS par
    la surface REST interne (fonctions importées depuis api.routes.*) ou
    par des SELECT sur la DB.
  * Les tests déclarent leurs besoins (profils, véhicule, etc.) dans
    ``TestCase.requires`` — le runner skippe avant d'appeler ``run``.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy import desc, select

from db.database import AsyncSessionLocal
from db.models import (
    Charger,
    ChargerActivityLog,
    ChargingProfileSnapshot,
    FirmwareUpdate,
    MeterValue,
    OcppTag,
    Reservation,
    Session,
    StatusNotificationLog,
)


# ──────────────────────────── TestContext ────────────────────────────


@dataclass
class TestContext:
    """Contexte fourni à chaque test via ``run(ctx)``.

    Provides :
      * ``charger_id`` / ``supported_profiles`` : métadonnées borne cible.
      * ``api`` : dict de fonctions ``call_action(...)`` qui proxy vers les
         méthodes ChargePoint (server-side). Utilise le ChargePoint
         connecté en direct (via OCPPServer.get_cp).
      * ``prompt_technician(msg, timeout_s)`` : blocking await.
      * ``wait_for_status(...)`` / ``wait_for_meter_value(...)`` : helpers.
      * ``log(level, msg)`` : publie un event log sur le bus.
      * ``details`` : dict libre que le test remplit pour le rapport.
    """
    charger_id: str
    supported_profiles: List[str]
    bus: Any  # certification.events.EventBus
    ocpp_server: Any  # core.ocpp_server.OCPPServer
    options: Dict[str, Any] = field(default_factory=dict)
    details: Dict[str, Any] = field(default_factory=dict)
    # Mémoire du test (ex: transaction_id du StartTx → réutilisé par remote-stop)
    state: Dict[str, Any] = field(default_factory=dict)

    def log(self, level: str, message: str, **extra) -> None:
        event = {"event": "log", "level": level, "message": message}
        if extra:
            event["extra"] = extra
        asyncio.create_task(self.bus.publish(event))

    # ──────────────── ChargePoint access ────────────────

    def get_cp(self):
        """Retourne le ChargePoint connecté, ou None si offline.

        Essaie les deux noms de méthodes : ``get_charger`` (OCPPServer
        actuel) et ``get_cp`` (nom alternatif utilisé dans certains forks).
        """
        srv = self.ocpp_server
        for name in ("get_charger", "get_cp"):
            fn = getattr(srv, name, None)
            if callable(fn):
                try:
                    cp = fn(self.charger_id)
                    if cp is not None:
                        return cp
                except Exception:
                    continue
        # Fallback : accès direct au dict interne
        try:
            return srv._charge_points.get(self.charger_id)
        except Exception:
            return None

    def is_online(self) -> bool:
        return self.get_cp() is not None

    def supports(self, profile: str) -> bool:
        if not self.supported_profiles:
            # Pas encore détecté -> permissif (comme StateMixin._supports_profile)
            return True
        return profile in self.supported_profiles

    # ──────────────── Prompt technicien ────────────────

    async def prompt_technician(
        self, message: str, timeout_s: float = 300, buttons: Optional[List[str]] = None
    ) -> str:
        """Envoie un prompt au technicien et attend sa réponse.

        Retourne 'done', 'skip', 'abort' ou 'timeout'.
        """
        if self.options.get("no_prompts"):
            # Mode quick : simule un 'skip' immédiat → le test doit gérer
            return "skip"
        prompt_id = self.bus.new_prompt()
        await self.bus.publish({
            "event": "prompt_technician",
            "prompt_id": prompt_id,
            "message": message,
            "timeout_s": timeout_s,
            "buttons": buttons or ["done", "skip"],
        })
        return await self.bus.wait_prompt(prompt_id, timeout_s=timeout_s)

    # ──────────────── DB queries (read-only) ────────────────

    async def get_last_activity(
        self,
        *,
        action: Optional[str] = None,
        direction: Optional[str] = None,
        since: Optional[datetime] = None,
        limit: int = 20,
    ) -> List[ChargerActivityLog]:
        async with AsyncSessionLocal() as db:
            q = select(ChargerActivityLog).where(
                ChargerActivityLog.charger_id == self.charger_id
            )
            if action:
                q = q.where(ChargerActivityLog.action == action)
            if direction:
                q = q.where(ChargerActivityLog.direction == direction)
            if since is not None:
                q = q.where(ChargerActivityLog.timestamp >= since)
            q = q.order_by(desc(ChargerActivityLog.timestamp)).limit(limit)
            r = await db.execute(q)
            return list(r.scalars())

    async def wait_for_activity(
        self,
        *,
        action: str,
        direction: Optional[str] = None,
        since: Optional[datetime] = None,
        timeout_s: float = 30,
        poll_s: float = 0.5,
    ) -> Optional[ChargerActivityLog]:
        """Poll l'activity log jusqu'à trouver un message, puis retourne-le."""
        deadline = time.monotonic() + timeout_s
        since = since or (datetime.now(timezone.utc) - timedelta(seconds=5))
        while time.monotonic() < deadline:
            rows = await self.get_last_activity(
                action=action, direction=direction, since=since, limit=5
            )
            if rows:
                return rows[0]
            await asyncio.sleep(poll_s)
        return None

    async def wait_for_status(
        self, status: str, *, timeout_s: float = 30, poll_s: float = 0.5
    ) -> Optional[StatusNotificationLog]:
        """Attends que la borne publie un StatusNotification avec ``status``."""
        deadline = time.monotonic() + timeout_s
        since = datetime.now(timezone.utc) - timedelta(seconds=5)
        target = status.lower()
        while time.monotonic() < deadline:
            async with AsyncSessionLocal() as db:
                q = (
                    select(StatusNotificationLog)
                    .where(StatusNotificationLog.charger_id == self.charger_id)
                    .where(StatusNotificationLog.received_at >= since)
                    .order_by(desc(StatusNotificationLog.received_at))
                    .limit(5)
                )
                r = await db.execute(q)
                for row in r.scalars():
                    if (row.status or "").lower() == target:
                        return row
            await asyncio.sleep(poll_s)
        return None

    async def get_latest_session(self) -> Optional[Session]:
        async with AsyncSessionLocal() as db:
            q = (
                select(Session)
                .where(Session.charger_id == self.charger_id)
                .order_by(desc(Session.start_time))
                .limit(1)
            )
            r = await db.execute(q)
            return r.scalar_one_or_none()

    async def get_latest_profile_snapshot(
        self, *, connector_id: Optional[int] = None
    ) -> Optional[ChargingProfileSnapshot]:
        async with AsyncSessionLocal() as db:
            q = select(ChargingProfileSnapshot).where(
                ChargingProfileSnapshot.charger_id == self.charger_id
            )
            if connector_id is not None:
                q = q.where(ChargingProfileSnapshot.connector_id == connector_id)
            q = q.order_by(desc(ChargingProfileSnapshot.applied_at)).limit(1)
            r = await db.execute(q)
            return r.scalar_one_or_none()

    # ──────────────── Helpers tags / reservations ────────────────

    async def ensure_tag(
        self,
        id_tag: str,
        *,
        active: bool = True,
        note: str = "certif",
        expiry_date: Optional[datetime] = None,
    ) -> None:
        """UPSERT un ``OcppTag`` dans la whitelist serveur.

        ``expiry_date`` : si fourni, écrit la colonne ``expiry_date`` du tag.
        Passer une valeur *passée* permet de simuler un tag expiré pour
        ``auth.authorize_expired``. Passer ``None`` laisse la colonne telle
        qu'elle est (ne l'efface PAS — utiliser ``delete_tag`` pour purger).
        """
        async with AsyncSessionLocal() as db:
            r = await db.execute(select(OcppTag).where(OcppTag.id_tag == id_tag))
            row = r.scalar_one_or_none()
            if row is None:
                row = OcppTag(
                    id_tag=id_tag, active=active, max_active_tx=1, note=note
                )
                if expiry_date is not None:
                    row.expiry_date = expiry_date
                db.add(row)
            else:
                row.active = active
                row.note = note
                if expiry_date is not None:
                    row.expiry_date = expiry_date
            await db.commit()

    async def delete_tag(self, id_tag: str) -> None:
        async with AsyncSessionLocal() as db:
            r = await db.execute(select(OcppTag).where(OcppTag.id_tag == id_tag))
            row = r.scalar_one_or_none()
            if row is not None:
                await db.delete(row)
                await db.commit()

    async def cancel_active_reservations(self) -> int:
        from db.models import ReservationStatus
        async with AsyncSessionLocal() as db:
            q = select(Reservation).where(
                Reservation.charger_id == self.charger_id,
                Reservation.status == ReservationStatus.ACTIVE,
            )
            r = await db.execute(q)
            rows = list(r.scalars())
            for row in rows:
                row.status = ReservationStatus.CANCELLED
            await db.commit()
            return len(rows)

    # ──────────────── Utility ────────────────

    @staticmethod
    def now_utc() -> datetime:
        return datetime.now(timezone.utc)

    # ──────────────── Pacing / propagation ────────────────
    #
    # Ces helpers existent parce qu'un `Accepted` OCPP n'est qu'un ACK
    # protocole : il ne garantit ni que le contacteur a bougé, ni que
    # le DC-DC a rampé, ni que la borne a commencé à mesurer.
    # Toute assertion métier doit attendre la propagation physique puis
    # échantillonner des MeterValues consécutifs dans la tolérance.

    async def settle(
        self, seconds: float, reason: str = "propagation OCPP"
    ) -> None:
        """Pause explicite tracée dans le rapport (propagation physique).

        À utiliser après chaque `Accepted` impactant du hardware :
          * 5 s après RemoteStart (contacteur + mesure)
          * 30 s après SetChargingProfile (rampe DC-DC)
          * 5 s après ChangeAvailability / ClearChargingProfile
        """
        self.log("info", f"settle {seconds:.1f}s — {reason}")
        await asyncio.sleep(max(0.0, float(seconds)))

    async def wait_for_preparing(self, timeout_s: float = 45) -> bool:
        """Attend un StatusNotification ∈ {Preparing, Charging, SuspendedEV}.

        Un véhicule branché passe Preparing avant Charging. Si le véhicule
        démarre directement, on accepte Charging comme preuve de présence.
        SuspendedEV = câble branché mais EV ne tire pas — valide aussi.
        """
        deadline = time.monotonic() + timeout_s
        since = datetime.now(timezone.utc) - timedelta(seconds=5)
        targets = {"preparing", "charging", "suspendedev", "suspendedevse"}
        while time.monotonic() < deadline:
            async with AsyncSessionLocal() as db:
                q = (
                    select(StatusNotificationLog)
                    .where(StatusNotificationLog.charger_id == self.charger_id)
                    .where(StatusNotificationLog.received_at >= since)
                    .order_by(desc(StatusNotificationLog.received_at))
                    .limit(10)
                )
                r = await db.execute(q)
                for row in r.scalars():
                    if (row.status or "").lower() in targets:
                        return True
            await asyncio.sleep(0.5)
        return False

    async def wait_for_charging_stable(self, timeout_s: float = 30) -> bool:
        """Attend un StatusNotification Charging (état stabilisé)."""
        result = await self.wait_for_status("Charging", timeout_s=timeout_s)
        return result is not None

    async def get_connector_status(
        self, connector_id: int = 1, lookback_s: float = 300
    ) -> Optional[str]:
        """Retourne le statut courant d'un connecteur (depuis StatusNotificationLog).

        Cherche la notification la plus récente dans la fenêtre lookback_s.
        Retourne le statut en minuscules (ex: 'preparing') ou None si inconnu.
        """
        since = datetime.now(timezone.utc) - timedelta(seconds=lookback_s)
        async with AsyncSessionLocal() as db:
            q = (
                select(StatusNotificationLog)
                .where(StatusNotificationLog.charger_id == self.charger_id)
                .where(StatusNotificationLog.connector_id == connector_id)
                .where(StatusNotificationLog.received_at >= since)
                .order_by(desc(StatusNotificationLog.received_at))
                .limit(1)
            )
            r = await db.execute(q)
            row = r.scalar_one_or_none()
            return (row.status or "").lower() if row else None

    async def wait_for_ev_engaged(
        self, timeout_s: float = 30, since: "datetime | None" = None
    ) -> tuple[bool, str]:
        """Attend que le connecteur 1 entre dans un état EV-engagé.

        États acceptés : Charging, SuspendedEV, SuspendedEVSE.
        - Charging     = courant actif, EV en charge normale.
        - SuspendedEV  = session ouverte, EV refuse la charge (batterie pleine ?).
        - SuspendedEVSE = EVSE suspend la charge (ex: limitation amont).

        Retourne (engaged: bool, status: str). ``status`` vaut la valeur
        observée en minuscules, ou '' si timeout.
        """
        deadline = time.monotonic() + timeout_s
        since = since or (datetime.now(timezone.utc) - timedelta(seconds=90))
        targets = {"charging", "suspendedev", "suspendedevse"}
        while time.monotonic() < deadline:
            async with AsyncSessionLocal() as db:
                q = (
                    select(StatusNotificationLog)
                    .where(StatusNotificationLog.charger_id == self.charger_id)
                    .where(StatusNotificationLog.connector_id == 1)
                    .where(StatusNotificationLog.received_at >= since)
                    .order_by(desc(StatusNotificationLog.received_at))
                    .limit(10)
                )
                r = await db.execute(q)
                for row in r.scalars():
                    s = (row.status or "").lower()
                    if s in targets:
                        return True, s
            await asyncio.sleep(0.5)
        return False, ""

    async def get_recent_meter_values(
        self, since: datetime, limit: int = 50
    ) -> List[MeterValue]:
        """Retourne les MeterValues récents pour la borne courante.

        Jointure via Session.charger_id — les MeterValues sont rattachés
        à une session active qui appartient au charger_id.
        """
        async with AsyncSessionLocal() as db:
            q = (
                select(MeterValue)
                .where(MeterValue.charger_id == self.charger_id)
                .where(MeterValue.timestamp >= since)
                .order_by(desc(MeterValue.timestamp))
                .limit(limit)
            )
            r = await db.execute(q)
            return list(r.scalars())

    async def wait_for_current_within(
        self,
        expected_a: float,
        *,
        tolerance_a: float = 2.0,
        tolerance_pct: float = 0.10,
        min_consecutive: int = 3,
        timeout_s: float = 60,
        poll_s: float = 5.0,
    ) -> tuple[bool, List[float]]:
        """Poll MeterValues jusqu'à observer ``min_consecutive`` lectures
        consécutives de courant dans la tolérance.

        Tolérance = ``max(tolerance_a, expected_a * tolerance_pct)``.
        Défauts conformes à la politique usager : ±max(2A, 10%).

        Retourne ``(ok, samples)``. ``samples`` = les dernières lectures
        examinées (pour reporting, même en cas d'échec).
        """
        tol = max(float(tolerance_a), abs(float(expected_a)) * float(tolerance_pct))
        lo, hi = float(expected_a) - tol, float(expected_a) + tol
        deadline = time.monotonic() + timeout_s
        start = datetime.now(timezone.utc) - timedelta(seconds=10)
        consecutive: List[float] = []
        last_seen: List[float] = []
        last_id = 0
        while time.monotonic() < deadline:
            rows = await self.get_recent_meter_values(since=start, limit=20)
            # Rows triés DESC (récent→vieux) — on remet en ordre chrono
            rows = list(reversed(rows))
            for mv in rows:
                if mv.id <= last_id:
                    continue
                last_id = mv.id
                if mv.current_a is None:
                    continue
                val = float(mv.current_a)
                last_seen.append(val)
                if lo <= val <= hi:
                    consecutive.append(val)
                    if len(consecutive) >= min_consecutive:
                        return True, consecutive
                else:
                    consecutive = []
            await asyncio.sleep(poll_s)
        return False, last_seen[-min_consecutive:] if last_seen else []

    async def wait_for_power_within(
        self,
        expected_w: float,
        *,
        tolerance_w: float = 500.0,
        tolerance_pct: float = 0.10,
        min_consecutive: int = 3,
        timeout_s: float = 60,
        poll_s: float = 5.0,
    ) -> tuple[bool, List[float]]:
        """Idem ``wait_for_current_within`` mais sur ``power_w``.

        Tolérance = ``max(tolerance_w, expected_w * tolerance_pct)``.
        Défauts conformes à la politique usager : ±max(500W, 10%).
        """
        tol = max(float(tolerance_w), abs(float(expected_w)) * float(tolerance_pct))
        lo, hi = float(expected_w) - tol, float(expected_w) + tol
        deadline = time.monotonic() + timeout_s
        start = datetime.now(timezone.utc) - timedelta(seconds=10)
        consecutive: List[float] = []
        last_seen: List[float] = []
        last_id = 0
        while time.monotonic() < deadline:
            rows = await self.get_recent_meter_values(since=start, limit=20)
            rows = list(reversed(rows))
            for mv in rows:
                if mv.id <= last_id:
                    continue
                last_id = mv.id
                if mv.power_w is None:
                    continue
                val = float(mv.power_w)
                last_seen.append(val)
                if lo <= val <= hi:
                    consecutive.append(val)
                    if len(consecutive) >= min_consecutive:
                        return True, consecutive
                else:
                    consecutive = []
            await asyncio.sleep(poll_s)
        return False, last_seen[-min_consecutive:] if last_seen else []


# ──────────────────────────── Charger capabilities ────────────────────────────


async def get_supported_profiles(charger_id: str) -> List[str]:
    """Lit ``chargers.supported_profiles`` et retourne la liste CSV parsée."""
    async with AsyncSessionLocal() as db:
        r = await db.execute(select(Charger).where(Charger.id == charger_id))
        c = r.scalar_one_or_none()
        if c is None:
            return []
        raw = getattr(c, "supported_profiles", None)
        if not raw:
            return []
        return [s.strip() for s in raw.split(",") if s.strip()]


async def charger_exists(charger_id: str) -> bool:
    async with AsyncSessionLocal() as db:
        r = await db.execute(select(Charger.id).where(Charger.id == charger_id))
        return r.scalar_one_or_none() is not None


async def get_charger_firmware(charger_id: str) -> Optional[str]:
    async with AsyncSessionLocal() as db:
        r = await db.execute(select(Charger).where(Charger.id == charger_id))
        c = r.scalar_one_or_none()
        return c.firmware_version if c else None
