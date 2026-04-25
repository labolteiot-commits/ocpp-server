# core/charge_point/state.py
# StateMixin — helpers internes (snapshots, drift, post-boot, etc.).
# Extrait de core/charge_point.py dans le refactor Phase 1 (2026-04-23).
from __future__ import annotations
import asyncio
from datetime import datetime, timezone

from ocpp.v16 import call
from ocpp.v16.enums import (
    AuthorizationStatus, MessageTrigger, AvailabilityType,
    RemoteStartStopStatus,
)
from sqlalchemy import select, update
from sqlalchemy import func as sa_func

from db.database import AsyncSessionLocal
from db.models import (
    Charger, Connector, Session, Event,
    ChargerStatus, ConnectorStatus, SessionStatus,
    ChargingProfileSnapshot,
    Reservation, ReservationStatus,
    OcppTag, ConfigSnapshot,
    FirmwareUpdate, OverrideIncident,
    LocalListVersion,
)
from core.logging import log

VEHICLE_PRESENT_STATUSES = {"Charging","Preparing","Finishing","SuspendedEV","SuspendedEVSE"}
STATUS_WAIT_TIMEOUT = 15.0

# Sprint 29 Volet D — clés surveillées contre le drift cloud fabricant
# (TechnoVE/Grizzl-E peuvent rebasculer ces valeurs dans notre dos)
ANTI_OVERRIDE_WATCHED_KEYS = [
    "HeartbeatInterval", "MeterValueSampleInterval",
    "AuthorizeRemoteTxRequests", "LocalAuthorizeOffline",
    "LocalPreAuthorize", "AllowOfflineTxForUnknownId",
    "StopTransactionOnInvalidId", "ConnectionTimeOut",
]


class StateMixin:
    def _add_task(self, coro):
        task = asyncio.create_task(coro)
        self._boot_tasks.add(task)
        task.add_done_callback(self._boot_tasks.discard)
        return task
    def _cancel_boot_tasks(self):
        for t in list(self._boot_tasks):
            if not t.done(): t.cancel()
        self._boot_tasks.clear()
    async def _log_event(self, event_type, payload):
        try:
            async with AsyncSessionLocal() as db:
                db.add(Event(charger_id=self.id, type=event_type, payload=payload,
                             timestamp=datetime.now(timezone.utc)))
                await db.commit()
        except Exception as e:
            log.warning("_log_event failed", id=self.id, error=str(e))
    async def _safe_call(self, request, *, context=""):
        """Wrapper qui absorbe les exceptions JSON si lenient_json_parsing=True (Grizzl-E 5.x)."""
        try:
            return await self.call(request)
        except Exception as e:
            if self.profile.lenient_json_parsing:
                log.warning(f"Réponse invalide ignorée ({context})", id=self.id, error=str(e))
                return None
            raise
    @staticmethod
    def _normalize_status(s):
        return {"available":"Available","preparing":"Preparing","charging":"Charging",
                "suspendedev":"SuspendedEV","suspendedevse":"SuspendedEVSE",
                "finishing":"Finishing","reserved":"Reserved","unavailable":"Unavailable",
                "faulted":"Faulted"}.get(s.lower(), s)
    # ── A4 : Parsing MeterValue conforme OCPP 1.6J §7.22.5 ────────────────────
    # Mapping measurand → (champ DB, unité de base attendue).
    _MEASURAND_FIELDS = {
        "Energy.Active.Import.Register":   ("energy_wh",          "Wh"),
        "Energy.Active.Export.Register":   ("energy_export_wh",   "Wh"),
        "Energy.Reactive.Import.Register": ("energy_reactive_wh", "varh"),
        "Power.Active.Import":             ("power_w",            "W"),
        "Power.Active.Export":             ("power_export_w",     "W"),
        "Power.Offered":                   ("power_offered_w",    "W"),
        "Current.Import":                  ("current_a",          "A"),
        "Current.Export":                  ("current_export_a",   "A"),
        "Current.Offered":                 ("current_offered_a",  "A"),
        "Voltage":                         ("voltage_v",          "V"),
        "SoC":                             ("soc_percent",        "Percent"),
        "Temperature":                     ("temperature_c",      "Celsius"),
        "Frequency":                       ("frequency_hz",       "Hertz"),
    }
    # Facteurs vers l'unité de base (W, Wh, A, V). Température gérée à part.
    _UNIT_MULTIPLIER = {
        "Wh": 1.0, "kWh": 1000.0,
        "varh": 1.0, "kvarh": 1000.0,
        "W": 1.0, "kW": 1000.0,
        "VA": 1.0, "kVA": 1000.0,
        "var": 1.0, "kvar": 1000.0,
        "A": 1.0,
        "V": 1.0,
        "Percent": 1.0,
        "Hertz": 1.0,
    }
    @classmethod
    def _normalize_sample(cls, sv: dict):
        """Convertit un SampledValue en (champ_DB, valeur_base, phase) ou None."""
        measurand = sv.get("measurand", "Energy.Active.Import.Register")
        entry = cls._MEASURAND_FIELDS.get(measurand)
        if not entry:
            return None
        field, default_unit = entry
        try:
            v = float(sv.get("value"))
        except (TypeError, ValueError):
            return None
        unit = sv.get("unit") or default_unit
        if field == "temperature_c":
            if unit in ("K", "Kelvin"):
                v = v - 273.15
            elif unit in ("F", "Fahrenheit"):
                v = (v - 32.0) * 5.0 / 9.0
            # Celsius → inchangé
        else:
            v = v * cls._UNIT_MULTIPLIER.get(unit, 1.0)
        return field, v, sv.get("phase")
    def _parse_meter_value(self, mv):
        """
        Parse un MeterValue OCPP 1.6J en dict normalisé en unités de base (W, Wh, A, V, %, °C).

        - Respecte `unit` (kWh→Wh, kW→W, K/F→°C, etc.).
        - Ne considère que les contextes Sample.Periodic / Sample.Clock pour
          l'instantané (les Transaction.Begin/End / Trigger restent accessibles
          via raw JSON dans MeterValue.raw).
        - Agrégation 3-phase :
            * Power   → somme des phases L1+L2+L3
            * Current → max des phases (limite breaker)
            * Voltage → moyenne des phases
          Si un sample composé (pas de `phase`, ou L1-N / L1-L2) existe déjà, il
          est prioritaire.
        """
        result: dict = {}
        p_by_phase: dict[str, float] = {}
        i_by_phase: dict[str, float] = {}
        v_by_phase: dict[str, float] = {}

        for sv in mv.get("sampled_value", []):
            ctx = sv.get("context", "Sample.Periodic")
            if ctx not in ("Sample.Periodic", "Sample.Clock"):
                continue
            norm = self._normalize_sample(sv)
            if norm is None:
                continue
            field, value, phase = norm

            if phase in {"L1", "L2", "L3"} and field in {"power_w", "current_a", "voltage_v"}:
                if field == "power_w":
                    p_by_phase[phase] = value
                elif field == "current_a":
                    i_by_phase[phase] = value
                elif field == "voltage_v":
                    v_by_phase[phase] = value
                continue
            if field not in result:
                result[field] = value

        if "power_w" not in result and p_by_phase:
            result["power_w"] = sum(p_by_phase.values())
        if "current_a" not in result and i_by_phase:
            result["current_a"] = max(i_by_phase.values())
        if "voltage_v" not in result and v_by_phase:
            result["voltage_v"] = sum(v_by_phase.values()) / len(v_by_phase)
        return result
    # ── A9 : Persistance ChargingProfileSnapshot + replay ─────────────────────
    # Un snapshot est conservé pour chaque (charger_id, connector_id, purpose).
    # UPSERT après chaque SetChargingProfile réussi ; replay au boot si différent
    # de _default_max_amps (le scheduler peut avoir ajusté le profil avant
    # déconnexion — on ré-applique la dernière valeur connue au reconnect).
    @staticmethod
    def _extract_limit_amps(profile_dict: dict) -> float | None:
        try:
            periods = profile_dict.get("charging_schedule", {}).get("charging_schedule_period", [])
            if not periods:
                return None
            return float(periods[0].get("limit"))
        except (TypeError, ValueError, AttributeError):
            return None
    async def _persist_profile_snapshot(self, connector_id: int, profile_dict: dict) -> None:
        """UPSERT dernier profil appliqué — ré-utilisé au reconnect (A9)."""
        purpose = (
            profile_dict.get("charging_profile_purpose")
            or profile_dict.get("chargingProfilePurpose")
        )
        if not purpose:
            return
        try:
            async with AsyncSessionLocal() as db:
                r = await db.execute(
                    select(ChargingProfileSnapshot).where(
                        ChargingProfileSnapshot.charger_id == self.id,
                        ChargingProfileSnapshot.connector_id == connector_id,
                        ChargingProfileSnapshot.purpose == purpose,
                    )
                )
                snap = r.scalar_one_or_none()
                now = datetime.now(timezone.utc)
                if snap is None:
                    db.add(ChargingProfileSnapshot(
                        charger_id=self.id, connector_id=connector_id,
                        purpose=purpose, profile_json=profile_dict, applied_at=now,
                    ))
                else:
                    snap.profile_json = profile_dict
                    snap.applied_at = now
                    # A8/A9 correctness (Sprint 28) : ré-apply d'un profil
                    # précédemment expiré doit remettre expired_at à NULL,
                    # sinon A9 replay au prochain boot ignorerait silencieusement
                    # le nouveau profil appliqué.
                    snap.expired_at = None
                await db.commit()
        except Exception as e:
            log.warning("_persist_profile_snapshot failed",
                        id=self.id, connector=connector_id, purpose=purpose, error=str(e))
    async def _load_last_profile_snapshot(self) -> dict | None:
        """Retourne le dernier snapshot pertinent (non expiré) pour le profile actuel."""
        p = self.profile
        purpose = "TxDefaultProfile" if p.use_tx_default_profile else "ChargePointMaxProfile"
        connector_id = p.profile_connector_id
        try:
            async with AsyncSessionLocal() as db:
                r = await db.execute(
                    select(ChargingProfileSnapshot).where(
                        ChargingProfileSnapshot.charger_id == self.id,
                        ChargingProfileSnapshot.connector_id == connector_id,
                        ChargingProfileSnapshot.purpose == purpose,
                        # A10 : ignorer les snapshots marqués expirés (clear opérateur)
                        ChargingProfileSnapshot.expired_at.is_(None),
                    )
                )
                snap = r.scalar_one_or_none()
                return snap.profile_json if snap else None
        except Exception as e:
            log.warning("_load_last_profile_snapshot failed", id=self.id, error=str(e))
            return None
    async def _expire_profile_snapshots(
        self,
        connector_id: int | None = None,
        profile_id: int | None = None,
    ) -> int:
        """A8 — marque les snapshots correspondants comme expirés (audit trail).

        Appelée après un ClearChargingProfile accepté par la borne. Sans ça,
        A9 ré-appliquerait au boot un profil que l'opérateur a pourtant effacé.

        - connector_id=None → tous les connecteurs de la borne
        - profile_id=None   → tous les purposes ; sinon match sur profile_json['charging_profile_id']
        """
        try:
            async with AsyncSessionLocal() as db:
                q = select(ChargingProfileSnapshot).where(
                    ChargingProfileSnapshot.charger_id == self.id,
                    ChargingProfileSnapshot.expired_at.is_(None),
                )
                if connector_id is not None:
                    q = q.where(ChargingProfileSnapshot.connector_id == connector_id)
                r = await db.execute(q)
                now = datetime.now(timezone.utc)
                n = 0
                for snap in r.scalars().all():
                    if profile_id is not None:
                        try:
                            pid = int((snap.profile_json or {}).get("charging_profile_id"))
                        except (TypeError, ValueError):
                            pid = None
                        if pid != profile_id:
                            continue
                    snap.expired_at = now
                    n += 1
                await db.commit()
                if n:
                    log.info("A8 — snapshots expirés",
                             id=self.id, connector_id=connector_id,
                             profile_id=profile_id, count=n)
                return n
        except Exception as e:
            log.warning("_expire_profile_snapshots failed",
                        id=self.id, connector_id=connector_id, error=str(e))
            return 0
    def _build_charging_profile(self, amps, profile_id=99, purpose=None, stack_level=None):
        # BUG-1 FIX : charging_profile_id doit être l'entier profile_id, PAS la purpose string
        purpose_str = purpose or ("TxDefaultProfile" if self.profile.use_tx_default_profile else "ChargePointMaxProfile")
        sl = stack_level if stack_level is not None else self.profile.profile_stack_level
        return {
            "charging_profile_id":      profile_id,       # ← int, pas purpose_str
            "stack_level":              sl,
            "charging_profile_purpose": purpose_str,
            "charging_profile_kind":    "Absolute",
            "charging_schedule": {
                "charging_rate_unit":       "A",
                "charging_schedule_period": [{"start_period": 0, "limit": amps}],
            },
        }
    async def _init_transaction_id(self):
        try:
            async with AsyncSessionLocal() as db:
                r = await db.execute(select(sa_func.max(Session.transaction_id))
                                     .where(Session.charger_id==self.id, Session.transaction_id>0))
                max_id = r.scalar_one_or_none()
                if max_id is not None and max_id >= self._next_transaction_id:
                    self._next_transaction_id = max_id + 1
                    log.info("TX ID init from DB", id=self.id, next_id=self._next_transaction_id)
        except Exception as e:
            log.warning("_init_transaction_id failed", id=self.id, error=str(e))
    async def _handle_status_transition(self, prev, new):
        if new in VEHICLE_PRESENT_STATUSES and prev not in VEHICLE_PRESENT_STATUSES:
            self._start_meter_polling()
            # BUG-4 FIX : re-appliquer le profil quand un véhicule se branche APRÈS le boot.
            # Au boot, _post_boot_sequence applique le profil si la borne était Available.
            # Si le véhicule se branche ensuite (Available → Preparing), rien ne ré-envoie
            # le profil → la borne charge à sa limite hardware (ex: 48A pour TechnoVE).
            if self._default_max_amps is not None and not self._boot_lock:
                self._add_task(self._apply_profile_on_connect())

        if new in {"Available","Unavailable","Faulted"} and prev in VEHICLE_PRESENT_STATUSES:
            self._stop_meter_polling()
            if self._synthetic_session_id is not None:
                await self._close_synthetic_session()
    async def _apply_profile_on_connect(self):
        """
        Ré-applique le profil de charge dès qu'un véhicule se branche (Available→Preparing).

        Différences avec _post_boot_sequence :
        - On est APRÈS le boot : defer_profile_if_occupied n'est plus pertinent.
        - Pour TechnoVE (use_tx_default_profile=True) en Preparing : envoyer TxDefaultProfile
          SANS le cycle Inop/Op (qui provoque un reboot en état Preparing).
        - Si boot_lock est actif : ne pas démarrer automatiquement.
        - Lancer _auto_remote_start si profile.send_remote_start_after_boot et pas de verrou.
        """
        p = self.profile
        # Laisser le temps au StatusNotification d'être traité
        await asyncio.sleep(2)

        # Ne rien faire si le connecteur n'est plus en état véhicule-présent
        if self._connector1_status not in VEHICLE_PRESENT_STATUSES:
            return

        # Appliquer le profil de courant
        try:
            status = await self.set_current_limit(self._default_max_amps)
            log.info("Profil appliqué — vehicle connect",
                     id=self.id, amps=self._default_max_amps,
                     profile=p.name, status=status)
        except Exception as e:
            log.warning("_apply_profile_on_connect échoué", id=self.id, error=str(e))

        # Lancer RemoteStart auto si configuré et pas de verrou serveur
        if (p.send_remote_start_after_boot
                and not self._boot_lock
                and not self._server_lock_active
                and not self._active_transactions):
            await asyncio.sleep(p.remote_start_delay)
            if self._connector1_status in VEHICLE_PRESENT_STATUSES and not self._active_transactions:
                log.info("Auto-RemoteStart post-connect", id=self.id, profile=p.name)
                self._add_task(self._do_remote_start(1, self._default_max_amps, self._local_id_tag))
    # ── Sprint 29 Volet A : Authorize whitelist ──────────────────────────────
    async def _authorize_id_tag(self, id_tag: str) -> dict:
        """Retourne un id_tag_info conforme OCPP 1.6J §5.2 selon la whitelist.

        Comportement selon enforce_whitelist (colonne chargers, défaut=0) :
        - enforce_whitelist=0 (défaut) : whitelist vide → Accepted (rétrocompat prod).
        - enforce_whitelist=1 : whitelist vide → Blocked + log d'alerte (protection).
        """
        try:
            async with AsyncSessionLocal() as db:
                charger = await db.get(Charger, self.id)
                enforce = bool(charger.enforce_whitelist) if charger and charger.enforce_whitelist else False
                any_tag = await db.scalar(select(sa_func.count()).select_from(OcppTag))
                if not any_tag:
                    if enforce:
                        log.error("[AUTH] enforce_whitelist=True mais aucun tag configuré — REJET sécurité",
                                  id=self.id, id_tag=id_tag)
                        return {"status": AuthorizationStatus.blocked}
                    return {"status": AuthorizationStatus.accepted}
                tag = await db.scalar(select(OcppTag).where(OcppTag.id_tag == id_tag))
                if tag is None:
                    return {"status": AuthorizationStatus.invalid}
                if not tag.active:
                    return {"status": AuthorizationStatus.blocked}
                if tag.expiry_date:
                    exp = tag.expiry_date
                    if exp.tzinfo is None:
                        exp = exp.replace(tzinfo=timezone.utc)
                    if exp < datetime.now(timezone.utc):
                        return {"status": AuthorizationStatus.expired}
                info = {"status": AuthorizationStatus.accepted}
                if tag.parent_id_tag:
                    info["parent_id_tag"] = tag.parent_id_tag
                if tag.expiry_date:
                    exp = tag.expiry_date
                    if exp.tzinfo is None:
                        exp = exp.replace(tzinfo=timezone.utc)
                    info["expiry_date"] = exp.isoformat()
                return info
        except Exception as e:
            log.warning("_authorize_id_tag failed — fallback Accepted",
                        id=self.id, id_tag=id_tag, error=str(e))
            return {"status": AuthorizationStatus.accepted}
    # ── Sprint 28 A7 : consommation de réservation sur StartTransaction ─────
    async def _consume_reservation(self, connector_id: int, id_tag: str) -> int | None:
        """Cherche une réservation ACTIVE matchant (id_tag, connector_id ou 0)
        et la marque USED. Retourne l'id DB de la réservation consommée, ou None.
        """
        try:
            async with AsyncSessionLocal() as db:
                now = datetime.now(timezone.utc)
                # Match : même idTag + (connector spécifique OU 0 = toute la borne)
                r = await db.execute(
                    select(Reservation).where(
                        Reservation.charger_id == self.id,
                        Reservation.id_tag == id_tag,
                        Reservation.status == ReservationStatus.ACTIVE,
                        Reservation.connector_id.in_([connector_id, 0]),
                        Reservation.expiry_date > now,
                    ).order_by(Reservation.connector_id.desc())  # préfère match connector-spécifique
                )
                res = r.scalars().first()
                if res is None:
                    return None
                res.status = ReservationStatus.USED
                res.updated_at = now
                await db.commit()
                log.info("Réservation consommée (StartTx match)",
                         id=self.id, reservation_id=res.reservation_id,
                         connector_id=connector_id, id_tag=id_tag)
                return res.id
        except Exception as e:
            log.warning("_consume_reservation failed",
                        id=self.id, id_tag=id_tag, error=str(e))
            return None
    # ── Cleanup orphan sessions (BUG-9) ──────────────────────────────────────
    async def _cleanup_orphan_sessions_at_boot(self):
        now = datetime.now(timezone.utc)
        vehicle_present = self._connector1_status in VEHICLE_PRESENT_STATUSES
        try:
            async with AsyncSessionLocal() as db:
                r = await db.execute(select(Session).where(Session.charger_id==self.id, Session.status==SessionStatus.ACTIVE))
                sessions = r.scalars().all()
                if not sessions: return
                closed=[]; restored=[]
                for s in sessions:
                    if not vehicle_present or (s.transaction_id and s.transaction_id<0):
                        s.stop_time=now; s.stop_reason="ServerRestart"; s.status=SessionStatus.COMPLETED
                        closed.append(s.transaction_id)
                    elif s.transaction_id and s.transaction_id not in self._active_transactions:
                        self._active_transactions[s.transaction_id]=s.id; restored.append(s.transaction_id)
                await db.commit()
                if closed: log.info("Sessions orphelines fermées",id=self.id,closed=closed)
                if restored: log.info("Sessions OCPP restaurées",id=self.id,restored=restored)
        except Exception as e:
            log.warning("_cleanup_orphan_sessions_at_boot failed", id=self.id, error=str(e))
    # ── Session synthétique ───────────────────────────────────────────────────
    async def _open_synthetic_session(self):
        if self._synthetic_session_id: return
        if self._connector1_status not in VEHICLE_PRESENT_STATUSES:
            log.warning("_open_synthetic_session ignorée",id=self.id,status=self._connector1_status); return
        async with AsyncSessionLocal() as db:
            r = await db.execute(select(Connector).where(Connector.charger_id==self.id,Connector.connector_id==1))
            conn=r.scalar_one_or_none()
            mr = await db.execute(select(sa_func.min(Session.transaction_id)).where(Session.charger_id==self.id))
            min_tx=mr.scalar_one_or_none()
            stx_id=(min_tx-1) if (min_tx is not None and min_tx<0) else -1
            s=Session(transaction_id=stx_id,charger_id=self.id,connector_id=conn.id if conn else 1,
                      id_tag="LOCAL",meter_start=0.0,start_time=datetime.now(timezone.utc),status=SessionStatus.ACTIVE)
            db.add(s); await db.commit(); await db.refresh(s)
            self._synthetic_session_id=s.id
        await self.server.broadcast_transaction(self.id,"start",{"transaction_id":stx_id,"connector_id":1,"id_tag":"LOCAL","meter_start":0,"synthetic":True})
    async def _close_synthetic_session(self):
        if not self._synthetic_session_id: return
        async with AsyncSessionLocal() as db:
            r = await db.execute(select(Session).where(Session.id==self._synthetic_session_id))
            s=r.scalar_one_or_none()
            if s: s.stop_time=datetime.now(timezone.utc); s.stop_reason="Local"; s.status=SessionStatus.COMPLETED; await db.commit()
        self._synthetic_session_id=None
        await self.server.broadcast_transaction(self.id,"stop",{"transaction_id":None,"meter_stop":0,"reason":"Local"})
    # ── Polling MeterValues ───────────────────────────────────────────────────
    def _start_meter_polling(self):
        if self._meter_poll_task and not self._meter_poll_task.done(): return
        self._meter_poll_task=asyncio.create_task(self._meter_poll_loop())
    def _stop_meter_polling(self):
        if self._meter_poll_task and not self._meter_poll_task.done():
            self._meter_poll_task.cancel(); self._meter_poll_task=None
    async def _meter_poll_loop(self):
        log.info("Démarrage polling MeterValues",id=self.id,profile=self.profile.name)
        await asyncio.sleep(5)
        if self.profile.meter_values_require_transaction:
            if not self._active_transactions and not self._synthetic_session_id:
                await self._open_synthetic_session()
        while True:
            try:
                await self._safe_call(call.TriggerMessage(requested_message=MessageTrigger.meter_values,connector_id=1),context="TriggerMessage MeterValues")
            except Exception as e:
                log.warning("Polling MeterValues failed",id=self.id,error=str(e))
            await asyncio.sleep(60)
    # ── Post-boot sequence (profile-driven) ───────────────────────────────────
    async def _post_boot_sequence(self):
        try: await asyncio.wait_for(self._status_received.wait(),timeout=STATUS_WAIT_TIMEOUT)
        except asyncio.TimeoutError: log.warning("Timeout StatusNotification",id=self.id)
        await self._init_transaction_id()
        await self._cleanup_orphan_sessions_at_boot()
        p=self.profile; occupied=self._connector1_status in VEHICLE_PRESENT_STATUSES
        # A9 : replay du dernier profil connu (si la dernière valeur appliquée
        # par le scheduler diffère du default_max_amps, on la remet au reconnect).
        boot_amps = self._default_max_amps
        if boot_amps is not None:
            last = await self._load_last_profile_snapshot()
            last_amps = self._extract_limit_amps(last) if last else None
            if last_amps is not None and last_amps != boot_amps:
                log.info("A9 replay profil persisté",id=self.id,
                         snapshot_amps=last_amps,default_amps=boot_amps,profile=p.name)
                boot_amps = last_amps
        # Step 1: charging profile
        if boot_amps is not None:
            can_send = not (p.defer_profile_if_occupied and occupied)
            if can_send:
                prof={"charging_profile_id":99,"stack_level":p.profile_stack_level,
                      "charging_profile_purpose":"TxDefaultProfile" if p.use_tx_default_profile else "ChargePointMaxProfile",
                      "charging_profile_kind":"Absolute",
                      "charging_schedule":{"charging_rate_unit":"A","charging_schedule_period":[{"start_period":0,"limit":boot_amps}]}}
                try:
                    resp=await self._safe_call(call.SetChargingProfile(connector_id=p.profile_connector_id,cs_charging_profiles=prof),context="SetChargingProfile boot")
                    log.info("Profil appliqué au boot",id=self.id,amps=boot_amps,profile=p.name,status=resp.status if resp else "none")
                    if resp and getattr(resp, "status", None) == "Accepted":
                        await self._persist_profile_snapshot(p.profile_connector_id, prof)
                        self._scheduler_applied_amps = boot_amps
                except Exception as e: log.warning("SetChargingProfile boot failed",id=self.id,error=str(e))
            else: log.info("SetChargingProfile différé — connecteur occupé",id=self.id,profile=p.name)
        # Step 2: ChangeAvailability
        skip_av = p.skip_availability_when_occupied and occupied
        if not skip_av:
            try: await self._safe_call(call.ChangeAvailability(connector_id=1,type=AvailabilityType.operative),context="ChangeAvailability boot")
            except Exception as e: log.warning("ChangeAvailability failed",id=self.id,error=str(e))
        else: log.info("ChangeAvailability skippé — connecteur occupé",id=self.id,profile=p.name)
        # Step 3: trigger status
        await asyncio.sleep(1)
        try: await self._safe_call(call.TriggerMessage(requested_message=MessageTrigger.status_notification,connector_id=1),context="TriggerMessage Status")
        except Exception as exc: log.warning("TriggerMessage Status failed", id=self.id, error=str(exc))
        await asyncio.sleep(2)
        real=self._connector1_status; vp=real in VEHICLE_PRESENT_STATUSES
        log.info("Statut final post-boot",id=self.id,status=real,vehicle_present=vp,profile=p.name)
        # Step 4: configure meter values
        await self._configure_meter_values()
        # Sprint 29 Volet D — baseline + vérification initiale du drift config
        # (après ChangeConfiguration, avant local list / lock). Best-effort.
        try:
            await self._capture_config_snapshot(watched_only=False)
        except Exception as e:
            log.warning("capture_config_snapshot post-boot failed",
                        id=self.id, error=str(e))
        # Sprint 31 Volet A — sync LocalAuthList seulement si la borne annonce
        # le profil LocalAuthListManagement. Sinon on ne tente rien.
        try:
            if await self._supports_profile("LocalAuthListManagement"):
                self._add_task(self._sync_local_auth_list(update_type="Full"))
        except Exception as e:
            log.warning("sync_local_auth_list post-boot skipped", id=self.id, error=str(e))
        # Step 5: local list
        if p.requires_local_list: self._add_task(self._populate_local_list())
        # Step 6: lock or poll
         # Step 6: lock or poll
        if vp:
            self._start_meter_polling()
            if p.send_remote_start_after_boot and not self._boot_lock:
                self._add_task(self._auto_remote_start_after_boot())
        elif self._boot_lock:
            try: await self._safe_call(call.ChangeAvailability(connector_id=1,type=AvailabilityType.inoperative),context="boot lock")
            except Exception as e: log.warning("Boot lock failed",id=self.id,error=str(e))

        # Step 7: appliquer le profil si il a été différé au boot (borne déjà en Preparing)
        # set_current_limit() envoie TxDefaultProfile sans cycle Inop/Op → safe en Preparing
        if p.defer_profile_if_occupied and occupied and self._default_max_amps is not None and not self._boot_lock:
            await asyncio.sleep(3)
            if self._connector1_status in VEHICLE_PRESENT_STATUSES:
                try:
                    status = await self.set_current_limit(self._default_max_amps)
                    log.info("Profil différé appliqué post-boot", id=self.id,
                             amps=self._default_max_amps, profile=p.name, status=status)
                except Exception as e:
                    log.warning("Profil différé post-boot échoué", id=self.id, error=str(e))
    async def _configure_meter_values(self):
        p=self.profile
        if p.skip_measurand_detection:
            final=p.fixed_measurands
            log.info("Measurands fixes (skip detection)",id=self.id,profile=p.name,measurands=final)
        else:
            DESIRED=["Energy.Active.Import.Register","Power.Active.Import","Current.Import","Voltage","SoC","Temperature"]
            final=DESIRED
            try:
                r=await self._safe_call(call.GetConfiguration(key=["MeterValuesSampledData","MeterValuesSampledDataMaxLength"]),context="GetConfiguration MeterValues")
                if r:
                    ml=len(DESIRED); supp=DESIRED
                    for item in r.configuration_key or []:
                        if item["key"]=="MeterValuesSampledDataMaxLength":
                            try: ml=int(item.get("value",str(len(DESIRED))))
                            except Exception: pass
                        if item["key"]=="MeterValuesSampledData":
                            c=[m.strip() for m in item.get("value","").split(",") if m.strip()]
                            if c: supp=c
                    final=[m for m in DESIRED if m in supp][:ml] or ["Energy.Active.Import.Register","Power.Active.Import"]
            except Exception as exc:
                log.warning("GetConfiguration MeterValues failed", id=self.id, error=str(exc))
                final=["Energy.Active.Import.Register","Power.Active.Import"]
        self._active_measurands=final
        # A2 : HeartbeatInterval propagé depuis DB (override par borne) ou profile par défaut
        hb = getattr(self, "_heartbeat_interval", None) or p.heartbeat_interval
        configs=[("MeterValueSampleInterval","60"),("MeterValuesSampledData",",".join(final)),
                 ("HeartbeatInterval",str(hb)),("StopTransactionOnEVSideDisconnect","true"),
                 ("ConnectionTimeOut","60"),("Cst_MeterValuesInTxOnly","false")]
        for key,val in configs:
            if p.skip_meter_config and "Meter" in key: continue
            try:
                resp=await self._safe_call(call.ChangeConfiguration(key=key,value=val),context=f"ChangeConfiguration {key}")
                if resp and resp.status=="Accepted": self._config_cache[key]=val
            except Exception as exc: log.warning("ChangeConfiguration failed", id=self.id, key=key, error=str(exc))
    async def _fetch_configuration(self):
        await asyncio.sleep(10)
        try:
            r=await self._safe_call(call.GetConfiguration(key=[]),context="GetConfiguration full")
            if r:
                for item in r.configuration_key or []: self._config_cache[item["key"]]=item.get("value","")
                await self.server.broadcast_config(self.id,self._config_cache)
        except Exception as exc: log.warning("_fetch_configuration failed", id=self.id, error=str(exc))
    async def _populate_local_list(self):
        await asyncio.sleep(15)
        # M1 fix : OCPP 1.6 §6.10 — list_version DOIT être > version actuelle de la borne.
        # On lit la version live via GetLocalListVersion ; fallback DB ; sinon timestamp epoch.
        next_version = 1
        try:
            r = await self._safe_call(call.GetLocalListVersion(), context="GetLocalListVersion preflight")
            current = int(getattr(r, "list_version", 0) or 0)
            next_version = max(current + 1, 1)
        except Exception:
            try:
                async with AsyncSessionLocal() as db:
                    row = await db.execute(
                        select(LocalListVersion).where(LocalListVersion.charger_id == self.id)
                    )
                    rec = row.scalar_one_or_none()
                    if rec is not None:
                        next_version = int(getattr(rec, "version", 0) or 0) + 1
            except Exception:
                next_version = int(datetime.now(timezone.utc).timestamp()) & 0x7FFFFFFF
        try:
            await self._safe_call(call.SendLocalList(list_version=next_version, update_type="Full",
                local_authorization_list=[{"id_tag":self._local_id_tag,"id_tag_info":{"status":AuthorizationStatus.accepted}}]),
                context="SendLocalList")
        except Exception as e: log.warning("SendLocalList failed",id=self.id,error=str(e))
    async def _auto_remote_start_after_boot(self):
        if self._auto_start_pending: return
        self._auto_start_pending=True
        try:
            await asyncio.sleep(20)
            # Ne pas relancer si l'opérateur a manuellement arrêté la charge
            if self._server_lock_active:
                log.info("Auto-RemoteStart annulé — verrou serveur actif", id=self.id); return
            if self._connector1_status not in VEHICLE_PRESENT_STATUSES or self._active_transactions: return
            log.info("Auto-RemoteStart",id=self.id,profile=self.profile.name)
            await self._do_remote_start(1,self._default_max_amps,self._local_id_tag)
        finally: self._auto_start_pending=False
    async def _do_remote_start(self, connector_id, amps, id_tag, charging_profile=None):
        """Séquence RemoteStart unifiée — pilotée par self.profile."""
        p=self.profile
        # 1. Appliquer le profil AVANT RemoteStart
        if amps is not None:
            # BUG-3 FIX : TxProfile SANS transaction_id est invalide (OCPP spec §7.3).
            # SetChargingProfile avant RemoteStart doit être TxDefaultProfile ou ChargePointMaxProfile.
            pre_purpose = "TxDefaultProfile" if p.use_tx_default_profile else "ChargePointMaxProfile"
            pre_connector = p.profile_connector_id  # 1 pour TxDefault, 0 pour MaxProfile
            # m5 fix : profil Absolute requiert start_schedule (OCPP 1.6 §3.13.1)
            now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            prof = {
                "charging_profile_id":      99,
                "stack_level":              p.profile_stack_level,
                "charging_profile_purpose": pre_purpose,
                "charging_profile_kind":    "Absolute",
                "valid_from":               now_iso,
                "charging_schedule": {
                    "charging_rate_unit":       "A",
                    "start_schedule":           now_iso,
                    "charging_schedule_period": [{"start_period": 0, "limit": amps}],
                },
            }
            try:
                resp = await self._safe_call(
                    call.SetChargingProfile(connector_id=pre_connector, cs_charging_profiles=prof),
                    context="SetChargingProfile pre-RemoteStart",
                )
                log.info("Profil pré-RemoteStart", id=self.id, amps=amps,
                         purpose=pre_purpose, status=resp.status if resp else "none")
                if resp and getattr(resp, "status", None) == "Accepted":
                    await self._persist_profile_snapshot(pre_connector, prof)
                    self._scheduler_applied_amps = amps
            except Exception as e:
                log.warning("SetChargingProfile pre-RemoteStart failed", id=self.id, error=str(e))
            await asyncio.sleep(1)
        # 2. ChangeAvailability si nécessaire
        st=self._connector1_status
        needs_av = not(p.skip_availability_when_occupied and st in VEHICLE_PRESENT_STATUSES) and st not in {"Available",*VEHICLE_PRESENT_STATUSES}
        if needs_av:
            try: await self._safe_call(call.ChangeAvailability(connector_id=connector_id,type=AvailabilityType.operative),context="ChangeAvailability RemoteStart")
            except Exception as e: log.warning("ChangeAvailability RemoteStart failed",id=self.id,error=str(e))
            await asyncio.sleep(self._remote_start_delay)
        else: await asyncio.sleep(0.5)
        # 3. RemoteStartTransaction
        kwargs={"connector_id":connector_id,"id_tag":id_tag}
        if not p.no_profile_in_remote_start and charging_profile and amps is None:
            kwargs["charging_profile"]=charging_profile
        resp=await self._safe_call(call.RemoteStartTransaction(**kwargs),context="RemoteStartTransaction")
        if resp is None: log.error("RemoteStart réponse None",id=self.id,profile=p.name); return False
        success=resp.status==RemoteStartStopStatus.accepted
        log.info("RemoteStart",id=self.id,profile=p.name,connector=connector_id,amps=amps,success=success)
        return success
    async def _lock_after_stop(self):
        self._stop_meter_polling(); await asyncio.sleep(5)
        try: await self._safe_call(call.ChangeAvailability(connector_id=1,type=AvailabilityType.inoperative),context="lock after stop")
        except Exception as exc: log.warning("_lock_after_stop failed", id=self.id, error=str(exc))
    # ── Sprint 29 Volet B : GetDiagnostics + DiagnosticsStatusNotification ──

    # ── Sprint 30 Volet A : UpdateFirmware ──────────────────────────────────

    async def _complete_firmware_update_on_boot(self, new_firmware_version: str) -> None:
        """Sprint 30 Volet A — au BootNotification suivant une installation,
        capte la nouvelle version annoncée et marque l'update Installed.

        On matche un FirmwareUpdate dans les 24h avec status ∈ {Installing,
        Downloaded, Downloading} (une borne qui reboot pendant Download est
        aussi considérée comme candidate). Best-effort, non-bloquant.
        """
        if not new_firmware_version:
            return
        try:
            from datetime import timedelta
            now = datetime.now(timezone.utc)
            cutoff = now - timedelta(hours=24)
            async with AsyncSessionLocal() as db:
                r = await db.execute(
                    select(FirmwareUpdate)
                    .where(
                        FirmwareUpdate.charger_id == self.id,
                        FirmwareUpdate.requested_at >= cutoff,
                        FirmwareUpdate.status.in_([
                            "Installing", "Downloaded", "Downloading", "Requested",
                        ]),
                    )
                    .order_by(FirmwareUpdate.requested_at.desc())
                    .limit(1)
                )
                fw = r.scalar_one_or_none()
                if fw is None:
                    return
                # Seulement si la version a réellement changé par rapport à
                # firmware_version_before (si capturée au moment de l'API)
                if fw.firmware_version_before and fw.firmware_version_before == new_firmware_version:
                    # Même version → installation non complétée, ne pas marquer Installed
                    log.info("FirmwareUpdate — version inchangée au boot",
                             id=self.id, fw_id=fw.id, version=new_firmware_version)
                    return
                fw.firmware_version_after = new_firmware_version
                fw.status = "Installed"
                fw.status_updated_at = now
                await db.commit()
                log.info("FirmwareUpdate complété au boot",
                         id=self.id, fw_id=fw.id,
                         before=fw.firmware_version_before,
                         after=new_firmware_version)
        except Exception as e:
            log.warning("_complete_firmware_update_on_boot failed",
                        id=self.id, error=str(e))
    # ── Sprint 30 Volet C : anti-override Current.Import drift ──────────────

    async def _check_current_drift(self, connector_id: int,
                                    observed_current: float) -> None:
        """Compare le courant observé à la dernière limite persistée.

        Si `observed > expected + 2A` pendant 3 samples consécutifs, logge un
        WARNING, persiste un `OverrideIncident` et re-push `SetChargingProfile`
        avec la limite attendue. Non-bloquant, best-effort.

        Ne vérifie que pendant une transaction active (éviter faux positifs
        sur courant résiduel / mesure-à-vide).
        """
        DRIFT_TOLERANCE_A = 2.0
        DRIFT_CONSECUTIVE_SAMPLES = 3

        # Ne vérifier que pendant une transaction OCPP active
        active_tx_ids = [tid for tid in self._active_transactions if tid > 0]
        if not active_tx_ids:
            self._current_drift_samples.pop(connector_id, None)
            return
        if observed_current is None or observed_current <= 0.1:
            # Courant nul ou très faible → pas de risque d'override
            self._current_drift_samples[connector_id] = 0
            return

        # Récupérer la limite attendue depuis le dernier ChargingProfileSnapshot
        try:
            async with AsyncSessionLocal() as db:
                r = await db.execute(
                    select(ChargingProfileSnapshot)
                    .where(
                        ChargingProfileSnapshot.charger_id == self.id,
                        ChargingProfileSnapshot.expired_at.is_(None),
                    )
                    .order_by(ChargingProfileSnapshot.applied_at.desc())
                    .limit(5)
                )
                snaps = r.scalars().all()
        except Exception as e:
            log.warning("_check_current_drift — lookup snapshot failed",
                        id=self.id, error=str(e))
            return

        if not snaps:
            return

        # Prendre la limite la plus récente avec limit_amps extrait
        expected_amps = None
        for snap in snaps:
            extracted = self._extract_limit_amps(snap.profile_json)
            if extracted is not None and extracted > 0:
                expected_amps = extracted
                break
        if expected_amps is None:
            return

        delta = observed_current - expected_amps
        if delta > DRIFT_TOLERANCE_A:
            # Drift détecté sur ce sample → incrémenter compteur
            count = self._current_drift_samples.get(connector_id, 0) + 1
            self._current_drift_samples[connector_id] = count
            if count < DRIFT_CONSECUTIVE_SAMPLES:
                return

            # Seuil atteint → incident + auto-heal
            log.warning(
                "[S30-override] Current drift detected",
                charger=self.id, connector=connector_id,
                observed=round(observed_current, 2),
                expected=round(expected_amps, 2),
                delta=round(delta, 2),
                samples=count,
            )
            tx_id = active_tx_ids[0] if active_tx_ids else None
            now = datetime.now(timezone.utc)

            # Re-apply expected limit (best-effort)
            reapplied = False
            reapply_status = None
            try:
                reapply_status = await self.set_current_limit(expected_amps)
                reapplied = (reapply_status == "Accepted")
            except Exception as e:
                reapply_status = f"Error:{str(e)[:30]}"

            # Persister l'incident
            try:
                async with AsyncSessionLocal() as db:
                    db.add(OverrideIncident(
                        charger_id=self.id,
                        connector_id=connector_id,
                        transaction_id=tx_id,
                        expected_amps=expected_amps,
                        observed_amps=observed_current,
                        delta_amps=delta,
                        samples_consecutive=count,
                        reapplied=reapplied,
                        reapply_status=reapply_status,
                        detected_at=now,
                        notes=f"Auto-heal via set_current_limit({expected_amps}A)",
                    ))
                    await db.commit()
            except Exception as e:
                log.warning("OverrideIncident persist failed",
                            id=self.id, error=str(e))

            # Reset compteur après intervention (laisser 3 samples avant nouvelle alerte)
            self._current_drift_samples[connector_id] = 0
        else:
            # Sous le seuil → reset compteur
            self._current_drift_samples[connector_id] = 0
    # ── Sprint 29 Volet D : Anti-cloud-override drift detection ─────────────

    async def _capture_config_snapshot(self, watched_only: bool = False) -> int:
        """Lit GetConfiguration (toutes clés ou watched subset) et UPSERT
        dans `config_snapshots`. Détecte les drifts vs `expected_value` et
        re-push la valeur attendue via ChangeConfiguration (auto-heal).

        Première capture (expected IS NULL) : pour les watched keys, set
        `expected = observed` comme baseline. Retourne le nombre de drifts
        détectés + guéris.
        """
        try:
            items = await self.get_configuration(
                ANTI_OVERRIDE_WATCHED_KEYS if watched_only else None
            )
        except Exception as e:
            log.warning("_capture_config_snapshot — GetConfiguration failed",
                        id=self.id, error=str(e))
            return 0

        if not items:
            return 0

        drifts = 0
        now = datetime.now(timezone.utc)
        try:
            async with AsyncSessionLocal() as db:
                for item in items:
                    key = item.get("key") if isinstance(item, dict) else getattr(item, "key", None)
                    if not key:
                        continue
                    value = item.get("value") if isinstance(item, dict) else getattr(item, "value", None)
                    readonly = bool(item.get("readonly")) if isinstance(item, dict) else bool(getattr(item, "readonly", False))
                    value_str = str(value) if value is not None else None

                    r = await db.execute(
                        select(ConfigSnapshot).where(
                            ConfigSnapshot.charger_id == self.id,
                            ConfigSnapshot.key == key,
                        )
                    )
                    snap = r.scalar_one_or_none()
                    if snap is None:
                        expected = value_str if key in ANTI_OVERRIDE_WATCHED_KEYS else None
                        snap = ConfigSnapshot(
                            charger_id=self.id,
                            key=key,
                            expected_value=expected,
                            observed_value=value_str,
                            readonly=readonly,
                            drift_count=0,
                            captured_at=now,
                        )
                        db.add(snap)
                        continue

                    snap.observed_value = value_str
                    snap.readonly = readonly
                    snap.captured_at = now

                    # Drift detection : expected défini + différent de observed
                    if (
                        snap.expected_value is not None
                        and not snap.readonly
                        and value_str != snap.expected_value
                    ):
                        snap.drift_count = (snap.drift_count or 0) + 1
                        snap.last_drift_at = now
                        log.warning(
                            "Config drift détecté — auto-heal",
                            id=self.id, key=key,
                            expected=snap.expected_value, observed=value_str,
                            drift_count=snap.drift_count,
                        )
                        drifts += 1
                        # Auto-heal : re-push notre valeur attendue.
                        # Pas bloquant : si la borne refuse, on ré-essaiera au prochain cycle.
                        try:
                            heal_status = await self.change_configuration(
                                key, snap.expected_value
                            )
                            if heal_status == "Accepted":
                                snap.last_heal_at = now
                                log.info("Config auto-heal accepté",
                                         id=self.id, key=key,
                                         value=snap.expected_value)
                            else:
                                log.warning("Config auto-heal refusé",
                                            id=self.id, key=key,
                                            value=snap.expected_value,
                                            status=heal_status)
                        except Exception as e:
                            log.warning("Config auto-heal exception",
                                        id=self.id, key=key, error=str(e))
                await db.commit()
        except Exception as e:
            log.warning("_capture_config_snapshot persist failed",
                        id=self.id, error=str(e))
        return drifts
    # ── Déconnexion ────────────────────────────────────────────────────────────

    # ── Sprint 31 Volet B : SupportedFeatureProfiles detection ──────────────
    async def _detect_supported_profiles(self) -> list[str] | None:
        """Lit la config OCPP 'SupportedFeatureProfiles' et persiste dans
        chargers.supported_profiles. Appelé au premier boot ou si colonne NULL.

        Valeurs possibles : Core, FirmwareManagement, LocalAuthListManagement,
        Reservation, SmartCharging, RemoteTrigger.
        """
        try:
            items = await self.get_configuration(keys=["SupportedFeatureProfiles"])
        except Exception as e:
            log.warning("_detect_supported_profiles — GetConfiguration failed",
                        id=self.id, error=str(e))
            return None
        if not items:
            return None
        raw = None
        for item in items:
            key = item.get("key") if isinstance(item, dict) else getattr(item, "key", None)
            if key == "SupportedFeatureProfiles":
                raw = item.get("value") if isinstance(item, dict) else getattr(item, "value", None)
                break
        if not raw:
            return None
        profiles = [p.strip() for p in str(raw).split(",") if p.strip()]
        try:
            async with AsyncSessionLocal() as db:
                await db.execute(
                    update(Charger).where(Charger.id == self.id)
                    .values(supported_profiles=",".join(profiles))
                )
                await db.commit()
            log.info("[S31-caps] profiles détectés",
                     id=self.id, profiles=profiles)
        except Exception as e:
            log.warning("_detect_supported_profiles persist failed",
                        id=self.id, error=str(e))
        return profiles

    async def _supports_profile(self, profile_name: str) -> bool:
        """True si la borne supporte le feature profile demandé.

        Default True si colonne NULL (rétrocompat — avant S31, on assumait
        que la borne supportait tout).
        """
        try:
            async with AsyncSessionLocal() as db:
                r = await db.execute(
                    select(Charger.supported_profiles).where(Charger.id == self.id)
                )
                raw = r.scalar_one_or_none()
            if raw is None or raw == "":
                return True  # rétrocompat
            profiles = [p.strip() for p in str(raw).split(",") if p.strip()]
            return profile_name in profiles
        except Exception:
            return True  # fallback permissif

    # ── Sprint 31 Volet A : LocalAuthList full sync (§5.3, §6.9) ────────────
    async def _sync_local_auth_list(self, update_type: str = "Full") -> dict:
        """Synchronise la whitelist locale de la borne avec la table OcppTag.

        Stratégie simple (v1) : toujours Full. Une version ultérieure pourra
        calculer un diff avec le snapshot précédent pour Differential.

        Retourne un dict {status, list_version, tag_count, type}.
        """
        # Gate capability detection (rétrocompat : True si inconnu)
        supports = await self._supports_profile("LocalAuthListManagement")
        if not supports:
            log.info("[S31-locallist] skip — LocalAuthListManagement non supporté",
                     id=self.id)
            return {"status": "NotSupported", "list_version": None,
                    "tag_count": 0, "type": update_type}

        # Lire les OcppTags actifs + bumper la version
        now = datetime.now(timezone.utc)
        try:
            async with AsyncSessionLocal() as db:
                r = await db.execute(
                    select(OcppTag).where(OcppTag.active == True)  # noqa: E712
                    .order_by(OcppTag.id_tag)
                )
                tags = r.scalars().all()

                r2 = await db.execute(
                    select(LocalListVersion).where(LocalListVersion.charger_id == self.id)
                )
                state_row = r2.scalar_one_or_none()
                new_version = (state_row.server_version if state_row else 0) + 1
        except Exception as e:
            log.warning("_sync_local_auth_list — DB read failed",
                        id=self.id, error=str(e))
            return {"status": "DBError", "list_version": None,
                    "tag_count": 0, "type": update_type}

        # Construire la liste OCPP
        auth_list = []
        for t in tags:
            info = {"status": "Accepted"}
            if t.parent_id_tag:
                info["parent_id_tag"] = t.parent_id_tag
            if t.expiry_date:
                exp = t.expiry_date
                if exp.tzinfo is None:
                    exp = exp.replace(tzinfo=timezone.utc)
                info["expiry_date"] = exp.isoformat()
            auth_list.append({"id_tag": t.id_tag, "id_tag_info": info})

        # Envoyer SendLocalList
        ocpp_status = None
        try:
            ocpp_status = await self.send_local_list(
                list_version=new_version,
                update_type=update_type,
                local_auth_list=auth_list,
            )
        except Exception as e:
            log.warning("SendLocalList failed",
                        id=self.id, version=new_version, error=str(e))
            ocpp_status = "Failed"

        # UPSERT local_list_versions
        try:
            async with AsyncSessionLocal() as db:
                r2 = await db.execute(
                    select(LocalListVersion).where(LocalListVersion.charger_id == self.id)
                )
                state_row = r2.scalar_one_or_none()
                if state_row is None:
                    db.add(LocalListVersion(
                        charger_id=self.id,
                        server_version=new_version,
                        last_sync_at=now,
                        last_sync_status=str(ocpp_status),
                        last_sync_type=update_type,
                        tag_count_sent=len(auth_list),
                    ))
                else:
                    state_row.server_version = new_version
                    state_row.last_sync_at = now
                    state_row.last_sync_status = str(ocpp_status)
                    state_row.last_sync_type = update_type
                    state_row.tag_count_sent = len(auth_list)
                await db.commit()
        except Exception as e:
            log.warning("_sync_local_auth_list persist failed",
                        id=self.id, error=str(e))

        log.info("[S31-locallist] sync",
                 id=self.id, version=new_version,
                 count=len(auth_list), type=update_type, status=str(ocpp_status))
        return {
            "status": str(ocpp_status),
            "list_version": new_version,
            "tag_count": len(auth_list),
            "type": update_type,
        }

    # ──────────────────────────────────────────────────────────────────────────
    # Contrôle du courant — méthodes principales (profile-aware)
    # ──────────────────────────────────────────────────────────────────────────
    @property
    def _is_technove(self) -> bool:
        """Compatibilité ascendante — chargers.py/scheduler.py."""
        if hasattr(self, 'profile') and self.profile.name != "Generic":
            return "TECHNOVE" in self.profile.name.upper()
        return "TECHNOVE" in (getattr(self, '_manufacturer', '') or "").upper()
