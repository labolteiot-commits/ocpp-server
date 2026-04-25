from __future__ import annotations
import asyncio
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from ocpp.routing import on
from ocpp.v16 import ChargePoint as OcppChargePoint
from ocpp.v16 import call_result, call
from ocpp.v16.enums import (
    Action, RegistrationStatus, AuthorizationStatus,
    RemoteStartStopStatus, ResetStatus, ResetType,
    MessageTrigger, AvailabilityType, AvailabilityStatus,
    ConfigurationStatus, ClearCacheStatus,
    DataTransferStatus,
)

from sqlalchemy import select, update
from sqlalchemy import func as sa_func
from db.database import AsyncSessionLocal
from db.models import (
    Charger, Connector, Session, MeterValue, Event,
    ChargerStatus, ConnectorStatus, SessionStatus
)
from core.logging import log

if TYPE_CHECKING:
    from core.ocpp_server import OCPPServer


# Statuts indiquant qu'un véhicule est présent
VEHICLE_PRESENT_STATUSES = {"Charging", "Preparing", "Finishing", "SuspendedEV", "SuspendedEVSE"}
# Statuts actifs (charge en cours)
CHARGING_STATUSES = {"Charging", "SuspendedEV", "SuspendedEVSE"}


class ChargePoint(OcppChargePoint):

    def __init__(self, charge_point_id: str, websocket, server: "OCPPServer"):
        super().__init__(charge_point_id, websocket)
        self.server                 = server
        self.ip_address             = websocket.remote_address[0] if websocket.remote_address else "unknown"
        self._active_transactions:  dict[int, int]  = {}
        self._next_transaction_id:  int             = 1
        self._last_energy:          dict[int, dict] = {}
        self._config_cache:         dict[str, str]  = {}
        self._active_measurands:    list[str]       = []
        self._boot_lock:            bool            = True
        self._default_max_amps:     Optional[float] = None
        self._connector1_status:    str             = "Unknown"
        # Tâche de polling MeterValues pour les sessions sans transaction OCPP
        self._meter_poll_task:      Optional[asyncio.Task] = None
        # ID de session synthétique (charge sans StartTransaction OCPP)
        self._synthetic_session_id: Optional[int]   = None
        # Informations fabricant (remplies au BootNotification)
        self._manufacturer:         Optional[str]   = None
        self._model:                Optional[str]   = None
        # Quirks par fabricant — peuvent être surchargés via la DB
        self._remote_start_delay:   float           = 1.0   # délai (s) après ChangeAvailability
        self._local_id_tag:         str             = "ADMIN"  # idTag pour RemoteStart et liste locale
        # Tâches de boot trackées — annulées à chaque déconnexion pour éviter les zombies
        self._boot_tasks:           set[asyncio.Task] = set()

    # ──────────────────────────────────────────────────────
    # Utilitaires
    # ──────────────────────────────────────────────────────

    @property
    def _is_technove(self) -> bool:
        """Détecte les bornes TechnoVE pour appliquer des quirks fabricant."""
        return "TECHNOVE" in (self._manufacturer or "").upper()

    def _add_task(self, coro) -> asyncio.Task:
        """Crée une tâche trackée qui se supprime automatiquement à la fin.
        Toutes les tâches créées via cette méthode sont annulées sur déconnexion.
        """
        task = asyncio.create_task(coro)
        self._boot_tasks.add(task)
        task.add_done_callback(self._boot_tasks.discard)
        return task

    def _cancel_boot_tasks(self) -> None:
        """Annule toutes les tâches de boot en cours (évite les zombies après déconnexion)."""
        for task in list(self._boot_tasks):
            if not task.done():
                task.cancel()
        self._boot_tasks.clear()

    async def _log_event(self, event_type: str, payload: dict) -> None:
        async with AsyncSessionLocal() as db:
            db.add(Event(
                charger_id=self.id,
                type=event_type,
                payload=payload,
                timestamp=datetime.now(timezone.utc),
            ))
            await db.commit()

    @staticmethod
    def _normalize_status(status: str) -> str:
        mapping = {
            "available":     "Available",
            "preparing":     "Preparing",
            "charging":      "Charging",
            "suspendedev":   "SuspendedEV",
            "suspendedevse": "SuspendedEVSE",
            "finishing":     "Finishing",
            "reserved":      "Reserved",
            "unavailable":   "Unavailable",
            "faulted":       "Faulted",
        }
        return mapping.get(status.lower(), status)

    def _parse_meter_value(self, mv: dict) -> dict:
        result = {}
        measurands = {
            "Energy.Active.Import.Register":  "energy_wh",
            "Energy.Active.Export.Register":  "energy_export_wh",
            "Power.Active.Import":            "power_w",
            "Power.Active.Export":            "power_export_w",
            "Power.Offered":                  "power_offered_w",
            "Current.Import":                 "current_a",
            "Current.Export":                 "current_export_a",
            "Current.Offered":                "current_offered_a",
            "Voltage":                        "voltage_v",
            "Frequency":                      "frequency_hz",
            "SoC":                            "soc_percent",
            "Temperature":                    "temperature_c",
        }
        for sv in mv.get("sampled_value", []):
            measurand = sv.get("measurand", "Energy.Active.Import.Register")
            key = measurands.get(measurand)
            if key:
                try:
                    result[key] = float(sv["value"])
                except (ValueError, KeyError):
                    pass
        return result

    def _build_charging_profile(self, amps: float, profile_id: int = 1,
                                 purpose: str = "TxDefaultProfile") -> dict:
        """Construit un profil de charge OCPP."""
        return {
            "charging_profile_id":      profile_id,
            "stack_level":              0,
            "charging_profile_purpose": purpose,
            "charging_profile_kind":    "Absolute",
            "charging_schedule": {
                "charging_rate_unit": "A",
                "charging_schedule_period": [
                    {"start_period": 0, "limit": amps}
                ],
            },
        }

    # ──────────────────────────────────────────────────────
    # Handlers — Borne → Serveur
    # ──────────────────────────────────────────────────────

    @on(Action.BootNotification)
    async def on_boot_notification(
        self, charge_point_vendor: str, charge_point_model: str, **kwargs
    ) -> call_result.BootNotification:

        log.info("BootNotification", id=self.id,
                 vendor=charge_point_vendor, model=charge_point_model)

        # ── Annuler les tâches de boot en cours (reconnexion rapide sans close propre) ──
        # Sans ça, les tâches du boot précédent continuent sur le vieux WebSocket mort
        # et causent des warnings "no close frame" + race conditions.
        self._cancel_boot_tasks()

        async with AsyncSessionLocal() as db:
            charger = await db.get(Charger, self.id)
            if charger is None:
                charger = Charger(
                    id=self.id,
                    manufacturer=charge_point_vendor,
                    model=charge_point_model,
                    serial_number=kwargs.get("charge_point_serial_number"),
                    firmware_version=kwargs.get("firmware_version"),
                    ip_address=self.ip_address,
                    status=ChargerStatus.AVAILABLE,
                    boot_lock=True,
                    default_max_amps=None,
                )
                db.add(charger)
                log.info("Nouvelle borne enregistrée", id=self.id)
            else:
                charger.manufacturer     = charge_point_vendor
                charger.model            = charge_point_model
                charger.firmware_version = kwargs.get("firmware_version", charger.firmware_version)
                charger.ip_address       = self.ip_address
                charger.status           = ChargerStatus.AVAILABLE

            self._boot_lock        = charger.boot_lock
            self._default_max_amps = charger.default_max_amps
            await db.commit()

        # ── Fermer les sessions ACTIVE orphelines (déconnexion brutale précédente) ──
        # Sans ça, la session synthétique transaction_id=-1 reste ACTIVE en DB et
        # provoque un UNIQUE constraint quand la borne reconnecte et en crée une nouvelle.
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Session).where(
                    Session.charger_id == self.id,
                    Session.status     == SessionStatus.ACTIVE,
                )
            )
            orphans = result.scalars().all()
            for s in orphans:
                s.status      = SessionStatus.COMPLETED
                s.stop_time   = datetime.now(timezone.utc)
                s.stop_reason = "PowerLoss"
            if orphans:
                log.info("Sessions orphelines fermées au reconnect",
                         id=self.id, count=len(orphans))
            await db.commit()

        # Réinitialiser l'état de session synthétique (nouvelle connexion = état neuf)
        self._synthetic_session_id = None

        # ── Détection fabricant et calcul des valeurs par défaut fabricant ──
        self._manufacturer = charge_point_vendor
        self._model        = charge_point_model

        # Délai entre ChangeAvailability et RemoteStartTransaction
        if charger.remote_start_delay is not None:
            self._remote_start_delay = charger.remote_start_delay
        elif self._is_technove:
            self._remote_start_delay = 3.0
        else:
            self._remote_start_delay = 1.0

        self._local_id_tag = charger.local_id_tag or "ADMIN"

        log.info("Profil fabricant calculé", id=self.id,
                 manufacturer=self._manufacturer,
                 remote_start_delay=self._remote_start_delay,
                 local_id_tag=self._local_id_tag)

        await self._log_event("BootNotification", {
            "vendor": charge_point_vendor, "model": charge_point_model, **kwargs
        })

        # Créer les tâches de boot via _add_task() pour qu'elles soient trackées
        # et annulées automatiquement à la prochaine déconnexion.
        self._add_task(self._post_boot_sequence())
        self._add_task(self._fetch_configuration())

        return call_result.BootNotification(
            current_time=datetime.now(timezone.utc).isoformat(),
            interval=30,
            status=RegistrationStatus.accepted,
        )

    @on(Action.Heartbeat)
    async def on_heartbeat(self, **kwargs) -> call_result.Heartbeat:
        now = datetime.now(timezone.utc)
        log.debug("Heartbeat", id=self.id)
        async with AsyncSessionLocal() as db:
            await db.execute(
                update(Charger).where(Charger.id == self.id)
                .values(last_heartbeat=now)
            )
            await db.commit()
        await self.server.broadcast_heartbeat(self.id, now.isoformat())
        return call_result.Heartbeat(current_time=now.isoformat())

    @on(Action.StatusNotification)
    async def on_status_notification(
        self, connector_id: int, error_code: str, status: str, **kwargs
    ) -> call_result.StatusNotification:

        normalized = self._normalize_status(status)
        log.info("StatusNotification", id=self.id,
                 connector=connector_id, status=normalized, error=error_code)

        prev_status = self._connector1_status
        if connector_id == 1:
            self._connector1_status = normalized

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Connector).where(
                    Connector.charger_id   == self.id,
                    Connector.connector_id == connector_id,
                )
            )
            connector = result.scalar_one_or_none()
            if connector is None:
                connector = Connector(charger_id=self.id, connector_id=connector_id)
                db.add(connector)

            try:
                connector.status = ConnectorStatus(normalized)
            except ValueError:
                connector.status = ConnectorStatus.UNAVAILABLE

            connector.error_code = error_code if error_code != "NoError" else None

            if connector_id == 0:
                try:
                    await db.execute(
                        update(Charger).where(Charger.id == self.id)
                        .values(status=ChargerStatus(normalized))
                    )
                except ValueError:
                    pass

            await db.commit()

        await self.server.broadcast_status(self.id, connector_id, normalized)
        await self._log_event("StatusNotification", {
            "connector_id": connector_id, "status": normalized,
            "error_code": error_code, **kwargs,
        })

        # Gérer les transitions de statut du connecteur 1
        if connector_id == 1:
            await self._handle_status_transition(prev_status, normalized)

        return call_result.StatusNotification()

    async def _handle_status_transition(self, prev: str, new: str) -> None:
        """Réagit aux changements de statut du connecteur 1."""

        # Véhicule branché → démarrer le polling MeterValues
        if new in VEHICLE_PRESENT_STATUSES and prev not in VEHICLE_PRESENT_STATUSES:
            log.info("Véhicule détecté → démarrage polling MeterValues", id=self.id)
            self._start_meter_polling()

        # Véhicule débranché → arrêter le polling et fermer session synthétique
        if new in {"Available", "Unavailable", "Faulted"} and prev in VEHICLE_PRESENT_STATUSES:
            log.info("Véhicule parti → arrêt polling MeterValues", id=self.id)
            self._stop_meter_polling()
            if self._synthetic_session_id is not None:
                await self._close_synthetic_session()

    @on(Action.Authorize)
    async def on_authorize(self, id_tag: str, **kwargs) -> call_result.Authorize:
        log.info("Authorize", id=self.id, id_tag=id_tag)
        return call_result.Authorize(
            id_tag_info={"status": AuthorizationStatus.accepted}
        )

    @on(Action.StartTransaction)
    async def on_start_transaction(
        self, connector_id: int, id_tag: str, meter_start: int,
        timestamp: str, **kwargs
    ) -> call_result.StartTransaction:

        transaction_id = self._next_transaction_id
        self._next_transaction_id += 1
        log.info("StartTransaction", id=self.id, connector=connector_id,
                 id_tag=id_tag, transaction_id=transaction_id)

        # Fermer la session synthétique si elle existait
        if self._synthetic_session_id is not None:
            await self._close_synthetic_session()

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Connector).where(
                    Connector.charger_id   == self.id,
                    Connector.connector_id == connector_id,
                )
            )
            connector_db = result.scalar_one_or_none()
            session = Session(
                transaction_id=transaction_id,
                charger_id=self.id,
                connector_id=connector_db.id if connector_db else connector_id,
                id_tag=id_tag,
                meter_start=meter_start / 1000.0,
                start_time=datetime.fromisoformat(timestamp.replace("Z", "+00:00")),
                status=SessionStatus.ACTIVE,
            )
            db.add(session)
            await db.commit()
            await db.refresh(session)
            self._active_transactions[transaction_id] = session.id

        await self.server.broadcast_transaction(self.id, "start", {
            "transaction_id": transaction_id,
            "connector_id":   connector_id,
            "id_tag":         id_tag,
            "meter_start":    meter_start,
        })
        await self._log_event("StartTransaction", {
            "transaction_id": transaction_id,
            "connector_id":   connector_id,
            "id_tag":         id_tag,
            "meter_start":    meter_start,
        })
        return call_result.StartTransaction(
            transaction_id=transaction_id,
            id_tag_info={"status": AuthorizationStatus.accepted},
        )

    @on(Action.StopTransaction)
    async def on_stop_transaction(
        self, meter_stop: int, timestamp: str, transaction_id: int,
        reason: str = "Local", **kwargs
    ) -> call_result.StopTransaction:

        log.info("StopTransaction", id=self.id,
                 transaction_id=transaction_id, reason=reason)

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Session).where(
                    Session.transaction_id == transaction_id,
                    Session.charger_id     == self.id,   # évite collision avec autre borne
                )
            )
            session = result.scalar_one_or_none()
            if session:
                meter_stop_kwh    = meter_stop / 1000.0
                session.meter_stop  = meter_stop_kwh
                session.energy_wh   = (meter_stop_kwh - session.meter_start) * 1000
                session.stop_time   = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                session.stop_reason = reason
                session.status      = SessionStatus.COMPLETED
                await db.commit()
            self._active_transactions.pop(transaction_id, None)

        await self.server.broadcast_transaction(self.id, "stop", {
            "transaction_id": transaction_id,
            "meter_stop":     meter_stop,
            "reason":         reason,
        })
        await self._log_event("StopTransaction", {
            "transaction_id": transaction_id,
            "meter_stop":     meter_stop,
            "reason":         reason,
        })
        return call_result.StopTransaction(
            id_tag_info={"status": AuthorizationStatus.accepted}
        )

    @on(Action.MeterValues)
    async def on_meter_values(
        self, connector_id: int, meter_value: list, **kwargs
    ) -> call_result.MeterValues:

        transaction_id = kwargs.get("transaction_id")
        log.debug("MeterValues", id=self.id, connector=connector_id, count=len(meter_value))

        async with AsyncSessionLocal() as db:
            # Chercher session OCPP active d'abord
            session_db = None
            if transaction_id:
                result = await db.execute(
                    select(Session).where(
                        Session.transaction_id == transaction_id,
                        Session.charger_id     == self.id,   # évite collision avec autre borne
                    )
                )
                session_db = result.scalar_one_or_none()

            # Utiliser session synthétique si pas de transaction OCPP
            if session_db is None and self._synthetic_session_id is not None:
                result = await db.execute(
                    select(Session).where(Session.id == self._synthetic_session_id)
                )
                session_db = result.scalar_one_or_none()

            for mv in meter_value:
                parsed = self._parse_meter_value(mv)

                # Calcul puissance par delta si non remontée
                if parsed.get("power_w") is None and parsed.get("energy_wh") is not None:
                    now  = datetime.now(timezone.utc)
                    last = self._last_energy.get(connector_id)
                    if last:
                        delta_wh = parsed["energy_wh"] - last["energy_wh"]
                        delta_h  = (now - last["time"]).total_seconds() / 3600
                        if delta_h > 0 and delta_wh >= 0:
                            parsed["power_w"] = round(delta_wh / delta_h, 1)
                    self._last_energy[connector_id] = {
                        "energy_wh": parsed["energy_wh"],
                        "time":      now,
                    }

                record = MeterValue(
                    session_id=session_db.id if session_db else None,
                    timestamp=datetime.fromisoformat(
                        mv.get("timestamp", datetime.now(timezone.utc).isoformat())
                        .replace("Z", "+00:00")
                    ),
                    energy_wh=parsed.get("energy_wh"),
                    power_w=parsed.get("power_w"),
                    current_a=parsed.get("current_a"),
                    voltage_v=parsed.get("voltage_v"),
                    soc_percent=parsed.get("soc_percent"),
                    raw=mv,
                )
                db.add(record)
                await self.server.broadcast_meter_value(self.id, connector_id, parsed)

            await db.commit()

        return call_result.MeterValues()

    @on(Action.DiagnosticsStatusNotification)
    async def on_diagnostics_status(self, status: str, **kwargs):
        log.info("DiagnosticsStatus", id=self.id, status=status)
        await self.server.broadcast_event(self.id, "diagnostics_status", {"status": status})
        await self._log_event("DiagnosticsStatus", {"status": status})
        return call_result.DiagnosticsStatusNotification()

    @on(Action.FirmwareStatusNotification)
    async def on_firmware_status(self, status: str, **kwargs):
        log.info("FirmwareStatus", id=self.id, status=status)
        await self.server.broadcast_event(self.id, "firmware_status", {"status": status})
        await self._log_event("FirmwareStatus", {"status": status})
        return call_result.FirmwareStatusNotification()

    @on(Action.DataTransfer)
    async def on_data_transfer(self, vendor_id: str, message_id: str = "", data: str = "", **kwargs):
        log.info("DataTransfer", id=self.id, vendor=vendor_id, message_id=message_id)
        await self._log_event("DataTransfer", {
            "vendor_id": vendor_id, "message_id": message_id, "data": data,
        })
        return call_result.DataTransfer(status=DataTransferStatus.accepted)

    # ──────────────────────────────────────────────────────
    # Session synthétique (charge sans StartTransaction OCPP)
    # ──────────────────────────────────────────────────────

    async def _open_synthetic_session(self) -> None:
        """Crée une session synthétique quand la borne charge sans StartTransaction."""
        if self._synthetic_session_id is not None:
            return

        log.info("Création session synthétique (charge sans StartTransaction OCPP)", id=self.id)
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Connector).where(
                    Connector.charger_id   == self.id,
                    Connector.connector_id == 1,
                )
            )
            connector_db = result.scalar_one_or_none()

            # Générer un ID négatif unique : min(transaction_id existant pour cette borne) - 1
            # Filtré par charger_id : chaque borne a son propre espace de transaction_id
            min_result = await db.execute(
                select(sa_func.min(Session.transaction_id))
                .where(Session.charger_id == self.id)
            )
            min_tx = min_result.scalar_one_or_none()
            synthetic_tx_id = (min_tx - 1) if (min_tx is not None and min_tx < 0) else -1

            session = Session(
                transaction_id=synthetic_tx_id,
                charger_id=self.id,
                connector_id=connector_db.id if connector_db else 1,
                id_tag="LOCAL",
                meter_start=0.0,
                start_time=datetime.now(timezone.utc),
                status=SessionStatus.ACTIVE,
            )
            db.add(session)
            await db.commit()
            await db.refresh(session)
            self._synthetic_session_id = session.id

        await self.server.broadcast_transaction(self.id, "start", {
            "transaction_id": synthetic_tx_id,
            "connector_id":   1,
            "id_tag":         "LOCAL",
            "meter_start":    0,
            "synthetic":      True,
        })

    async def _close_synthetic_session(self) -> None:
        """Ferme la session synthétique."""
        if self._synthetic_session_id is None:
            return
        log.info("Fermeture session synthétique", id=self.id)
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Session).where(Session.id == self._synthetic_session_id)
            )
            session = result.scalar_one_or_none()
            if session:
                session.stop_time   = datetime.now(timezone.utc)
                session.stop_reason = "Local"
                session.status      = SessionStatus.COMPLETED
                await db.commit()
        self._synthetic_session_id = None
        await self.server.broadcast_transaction(self.id, "stop", {
            "transaction_id": None,
            "meter_stop":     0,
            "reason":         "Local",
        })

    # ──────────────────────────────────────────────────────
    # Polling MeterValues (quand Cst_MeterValuesInTxOnly=true)
    # ──────────────────────────────────────────────────────

    def _start_meter_polling(self) -> None:
        """Démarre le polling périodique des MeterValues via TriggerMessage."""
        if self._meter_poll_task and not self._meter_poll_task.done():
            return
        self._meter_poll_task = asyncio.create_task(self._meter_poll_loop())

    def _stop_meter_polling(self) -> None:
        """Arrête le polling."""
        if self._meter_poll_task and not self._meter_poll_task.done():
            self._meter_poll_task.cancel()
            self._meter_poll_task = None

    async def _meter_poll_loop(self) -> None:
        """Boucle de polling — TriggerMessage MeterValues toutes les 60s."""
        log.info("Démarrage polling MeterValues", id=self.id)
        # Attendre que la session soit stable avant de commencer
        await asyncio.sleep(5)

        # Ouvrir une session synthétique si pas de transaction OCPP
        if not self._active_transactions and self._synthetic_session_id is None:
            await self._open_synthetic_session()

        while True:
            try:
                await self.call(call.TriggerMessage(
                    requested_message=MessageTrigger.meter_values,
                    connector_id=1,
                ))
                log.debug("Polling MeterValues envoyé", id=self.id)
            except Exception as e:
                log.warning("Polling MeterValues échoué", id=self.id, error=str(e))
            await asyncio.sleep(60)

    # ──────────────────────────────────────────────────────
    # Séquence post-boot
    # ──────────────────────────────────────────────────────

    async def _post_boot_sequence(self) -> None:
        await asyncio.sleep(2)

        # ── Étape 1 : Profil de limite de courant ────────────────────────────
        if self._default_max_amps is not None:
            if self._is_technove:
                # TechnoVE quirk :
                # - Supporte ChargingScheduleAllowedChargingRateUnit=Current ("A") seulement
                # - SetChargingProfile pendant un état "Preparing" (véhicule branché au boot)
                #   ne reçoit jamais de réponse → timeout 30s → déconnexion en cascade.
                # - On diffère l'application du profil : il sera envoyé après que le statut
                #   soit stable, ou lors d'un RemoteStart (via charging_profile).
                # - Si le connecteur est Available (pas de véhicule), on peut tenter le profil.
                current_status = self._connector1_status
                if current_status in VEHICLE_PRESENT_STATUSES:
                    log.info("TechnoVE — SetChargingProfile différé (véhicule présent au boot)",
                             id=self.id, status=current_status)
                else:
                    profile = self._build_charging_profile(
                        self._default_max_amps,
                        profile_id=99,
                        purpose="TxDefaultProfile",
                    )
                    profile["stack_level"] = 0
                    try:
                        await self.call(call.SetChargingProfile(
                            connector_id=1,
                            cs_charging_profiles=profile,
                        ))
                        log.info("TechnoVE — TxDefaultProfile appliqué au boot",
                                 id=self.id, amps=self._default_max_amps)
                    except Exception as e:
                        log.warning("TechnoVE — SetChargingProfile boot échoué",
                                    id=self.id, error=str(e))
            else:
                # Comportement générique : TxDefaultProfile sur connector 1 avec stack_level élevé
                try:
                    profile = {
                        "charging_profile_id":      99,
                        "stack_level":              8,
                        "charging_profile_purpose": "TxDefaultProfile",
                        "charging_profile_kind":    "Absolute",
                        "charging_schedule": {
                            "charging_rate_unit": "A",
                            "charging_schedule_period": [
                                {"start_period": 0, "limit": self._default_max_amps}
                            ],
                        },
                    }
                    await self.call(call.SetChargingProfile(
                        connector_id=1,
                        cs_charging_profiles=profile,
                    ))
                    log.info("Profil max (TxDefault) appliqué AVANT Operative",
                             id=self.id, amps=self._default_max_amps)
                except Exception as e:
                    log.warning("SetChargingProfile boot (TxDefault) échoué", id=self.id, error=str(e))

        # ── Étape 2 : Remettre Operative ─────────────────────────────────────
        # TechnoVE quirk CRITIQUE : envoyer ChangeAvailability(Operative) quand le connecteur
        # est déjà en état "Preparing" (véhicule branché) cause un reboot interne immédiat.
        # On l'envoie seulement si le connecteur est disponible/inopératif — jamais en Preparing.
        _skip_avail = self._is_technove and self._connector1_status in VEHICLE_PRESENT_STATUSES
        if _skip_avail:
            log.info("TechnoVE — ChangeAvailability Operative skippé (connecteur déjà occupé)",
                     id=self.id, status=self._connector1_status)
        else:
            log.info("Remise Operative du connecteur au boot", id=self.id)
            try:
                await self.call(call.ChangeAvailability(
                    connector_id=1,
                    type=AvailabilityType.operative,
                ))
            except Exception as e:
                log.warning("ChangeAvailability Operative échoué", id=self.id, error=str(e))

        # ── Étape 3 : ChargePointMaxProfile — uniquement pour les bornes génériques ──
        # TechnoVE : skippé (inutile et parfois rejeté hors transaction)
        if self._default_max_amps is not None and not self._is_technove:
            try:
                await self.call(call.SetChargingProfile(
                    connector_id=0,
                    cs_charging_profiles=self._build_charging_profile(
                        self._default_max_amps,
                        profile_id=99,
                        purpose="ChargePointMaxProfile",
                    ),
                ))
                log.info("ChargePointMaxProfile appliqué au boot",
                         id=self.id, amps=self._default_max_amps)
            except Exception as e:
                log.warning("SetChargingProfile boot (ChargePointMax) échoué", id=self.id, error=str(e))

        # ── Étape 4 : Demander le vrai statut ────────────────────────────────
        await asyncio.sleep(1)
        try:
            await self.call(call.TriggerMessage(
                requested_message=MessageTrigger.status_notification,
                connector_id=1,
            ))
        except Exception:
            pass

        await asyncio.sleep(2)

        real_status = self._connector1_status
        vehicle_present = real_status in VEHICLE_PRESENT_STATUSES
        log.info("Statut réel connecteur après boot",
                 id=self.id, status=real_status, vehicle_present=vehicle_present)

        # ── Étape 5 : Configurer MeterValues ─────────────────────────────────
        await self._configure_meter_values()

        # ── Étape 6 : Peupler la liste locale OCPP (TechnoVE) ────────────────
        # AllowOfflineTxForUnknownId=false + LocalAuthorizeOffline=true sur TechnoVE :
        # l'idTag doit être connu localement pour éviter tout rejet lors de transactions locales.
        if self._is_technove:
            self._add_task(self._populate_local_list())

        # ── Étape 7 : Logique de verrouillage / polling ───────────────────────
        if vehicle_present:
            log.info("Véhicule détecté au boot — démarrage polling MeterValues", id=self.id)
            self._start_meter_polling()

            # TechnoVE : si véhicule présent et boot_lock=False, tenter un RemoteStart automatique.
            # La borne peut avoir rebooté après un RemoteStart précédent (sans StartTransaction).
            # On réessaie après une pause pour laisser la borne se stabiliser.
            if self._is_technove and not self._boot_lock:
                self._add_task(self._auto_remote_start_after_boot())

        elif self._boot_lock:
            try:
                await self.call(call.ChangeAvailability(
                    connector_id=1,
                    type=AvailabilityType.inoperative,
                ))
                log.info("Borne verrouillée au boot (pas de véhicule)", id=self.id)
            except Exception as e:
                log.warning("Verrouillage boot échoué", id=self.id, error=str(e))
        else:
            log.info("boot_lock=False — borne laissée disponible", id=self.id)

    async def _configure_meter_values(self) -> None:
        """Configure l'intervalle et les measurands, désactive MeterValuesInTxOnly."""

        DESIRED = [
            "Energy.Active.Import.Register",
            "Power.Active.Import",
            "Current.Import",
            "Voltage",
            "SoC",
            "Temperature",
            "Energy.Active.Export.Register",
            "Power.Active.Export",
        ]

        supported_measurands = DESIRED
        try:
            r = await self.call(call.GetConfiguration(
                key=["MeterValuesSampledData", "MeterValuesSampledDataMaxLength"]
            ))
            max_len = len(DESIRED)
            for item in r.configuration_key or []:
                if item["key"] == "MeterValuesSampledDataMaxLength":
                    try:
                        max_len = int(item.get("value", str(len(DESIRED))))
                    except ValueError:
                        pass
                if item["key"] == "MeterValuesSampledData":
                    current = [m.strip() for m in item.get("value", "").split(",") if m.strip()]
                    if current:
                        supported_measurands = current

            final = [m for m in DESIRED if m in supported_measurands][:max_len]
            if not final:
                final = ["Energy.Active.Import.Register", "Power.Active.Import"]
        except Exception:
            final = ["Energy.Active.Import.Register", "Power.Active.Import"]

        self._active_measurands = final
        log.info("Measurands retenus", id=self.id, final=final)

        configs = [
            ("MeterValueSampleInterval",          "60"),
            ("MeterValuesSampledData",            ",".join(final)),
            ("HeartbeatInterval",                 "30"),
            ("ConnectionTimeOut",                 "60"),
            ("StopTransactionOnEVSideDisconnect", "true"),
            # Désactiver pour recevoir MeterValues même sans transaction OCPP
            ("Cst_MeterValuesInTxOnly",           "false"),
        ]

        for key, value in configs:
            try:
                resp = await self.call(call.ChangeConfiguration(key=key, value=value))
                if resp.status == "Accepted":
                    self._config_cache[key] = value
                    log.info(f"Config {key}={value}", id=self.id)
                elif resp.status == "Rejected":
                    log.warning(f"Config {key} rejetée", id=self.id)
                elif resp.status == "NotSupported":
                    log.warning(f"Config {key} non supportée", id=self.id)
            except Exception as e:
                log.warning(f"Config {key} échouée", id=self.id, error=str(e))

    async def _fetch_configuration(self) -> None:
        await asyncio.sleep(10)
        try:
            response = await self.call(call.GetConfiguration(key=[]))
            for item in response.configuration_key or []:
                self._config_cache[item["key"]] = item.get("value", "")
            log.info("Configuration complète récupérée",
                     id=self.id, count=len(self._config_cache))
            await self.server.broadcast_config(self.id, self._config_cache)
        except Exception as e:
            log.warning("GetConfiguration échouée", id=self.id, error=str(e))

    async def _populate_local_list(self) -> None:
        """Peuple la liste locale OCPP avec l'idTag admin.

        TechnoVE : LocalAuthorizeOffline=true + AllowOfflineTxForUnknownId=false.
        L'idTag utilisé pour RemoteStart doit être connu localement pour éviter
        tout rejet lors d'une transaction initiée localement (RFID, bouton).
        Attendu après _fetch_configuration pour ne pas surcharger la borne au boot.
        """
        await asyncio.sleep(15)  # laisser _fetch_configuration se terminer d'abord
        try:
            response = await self.call(call.SendLocalList(
                list_version=1,
                update_type="Full",
                local_authorization_list=[
                    {
                        "id_tag": self._local_id_tag,
                        "id_tag_info": {"status": AuthorizationStatus.accepted},
                    }
                ],
            ))
            log.info("SendLocalList", id=self.id,
                     id_tag=self._local_id_tag, status=response.status)
        except Exception as e:
            log.warning("SendLocalList échoué", id=self.id, error=str(e))

    async def _auto_remote_start_after_boot(self) -> None:
        """Déclenche un RemoteStart automatique si le véhicule est présent au boot.

        TechnoVE : quand la borne reboot (suite à un crash après RemoteStart),
        elle reconnecte en état Preparing. Si boot_lock=False, on réessaie le
        RemoteStart automatiquement après que la borne soit stable.
        Attendre _populate_local_list (15s) + un peu plus pour que SendLocalList soit traité.
        """
        await asyncio.sleep(20)  # attendre SendLocalList + marge

        # Vérifier que le véhicule est toujours présent
        if self._connector1_status not in VEHICLE_PRESENT_STATUSES:
            log.info("Auto-RemoteStart annulé — véhicule plus présent", id=self.id)
            return

        # Vérifier qu'aucune transaction OCPP n'est déjà active
        if self._active_transactions:
            log.info("Auto-RemoteStart annulé — transaction déjà active", id=self.id)
            return

        log.info("TechnoVE — Auto-RemoteStart (véhicule présent au boot, pas de transaction)",
                 id=self.id)
        success = await self._remote_start_technove(1, self._default_max_amps)
        if not success:
            log.warning("TechnoVE — Auto-RemoteStart échoué", id=self.id)

    # ──────────────────────────────────────────────────────
    # Commandes Serveur → Borne
    # ──────────────────────────────────────────────────────

    async def remote_start_transaction(
        self, connector_id: int, id_tag: str,
        charging_profile: Optional[dict] = None,
        max_amps: Optional[float] = None,
    ) -> bool:
        try:
            amps = max_amps if max_amps is not None else self._default_max_amps

            if self._is_technove:
                return await self._remote_start_technove(connector_id, amps)
            else:
                return await self._remote_start_generic(connector_id, id_tag, amps, charging_profile)

        except Exception as e:
            log.error("RemoteStart échoué", id=self.id, error=str(e))
            return False

    async def _remote_start_technove(self, connector_id: int, amps: Optional[float]) -> bool:
        """Séquence RemoteStart spécifique TechnoVE.

        Différences vs. séquence générique :
        1. idTag = self._local_id_tag ("ADMIN") — doit correspondre à la liste locale OCPP.
           TechnoVE crashe (reboot) si idTag absent de la liste + StopTransactionOnInvalidId=true.
        2. PAS de TxProfile dans RemoteStartTransaction — TechnoVE reboot quand elle en reçoit un.
           La limite de courant est appliquée via TxDefaultProfile AVANT le RemoteStart.
        3. Délai plus long (self._remote_start_delay = 3s) après ChangeAvailability.
        """
        # Étape 1 : Appliquer TxDefaultProfile si une limite est configurée
        # Fait ici (pas au boot) car la borne est maintenant stable et répond correctement.
        if amps is not None:
            profile = self._build_charging_profile(amps, profile_id=99, purpose="TxDefaultProfile")
            profile["stack_level"] = 0
            try:
                resp = await self.call(call.SetChargingProfile(
                    connector_id=1,
                    cs_charging_profiles=profile,
                ))
                log.info("TechnoVE — TxDefaultProfile pré-RemoteStart",
                         id=self.id, amps=amps, status=resp.status if resp else "None")
            except Exception as e:
                log.warning("TechnoVE — TxDefaultProfile pré-RemoteStart échoué",
                            id=self.id, error=str(e))
            await asyncio.sleep(1)

        # Étape 2 : ChangeAvailability(Operative) — seulement si nécessaire
        # TechnoVE : si le connecteur est déjà en Preparing (véhicule branché et prêt),
        # il est déjà Operative. Envoyer ChangeAvailability dans cet état cause un reboot.
        # On l'envoie uniquement si le connecteur est Unavailable (boot_lock) ou Available.
        connector_status = self._connector1_status
        if connector_status not in VEHICLE_PRESENT_STATUSES:
            try:
                await self.call(call.ChangeAvailability(
                    connector_id=connector_id,
                    type=AvailabilityType.operative,
                ))
            except Exception as e:
                log.warning("TechnoVE — ChangeAvailability RemoteStart échoué",
                            id=self.id, error=str(e))
            await asyncio.sleep(self._remote_start_delay)
        else:
            # Véhicule déjà en Preparing → pas de ChangeAvailability, délai court suffisant
            log.debug("TechnoVE — ChangeAvailability skippé (connecteur déjà occupé)",
                      id=self.id, status=connector_status)
            await asyncio.sleep(1)

        # Étape 3 : RemoteStartTransaction avec UNIQUEMENT idTag + connectorId
        # - idTag = local_id_tag (enregistré dans la liste locale via SendLocalList)
        # - Pas de TxProfile : TechnoVE reboot si elle en reçoit un dans RemoteStart
        response = await self.call(call.RemoteStartTransaction(
            connector_id=connector_id,
            id_tag=self._local_id_tag,
        ))

        if response is None:
            log.error("TechnoVE — RemoteStart réponse None (connexion perdue)", id=self.id)
            return False

        success = response.status == RemoteStartStopStatus.accepted
        log.info("RemoteStart", id=self.id, connector=connector_id,
                 amps=amps, id_tag=self._local_id_tag, success=success)
        return success

    async def _remote_start_generic(
        self, connector_id: int, id_tag: str,
        amps: Optional[float], charging_profile: Optional[dict]
    ) -> bool:
        """Séquence RemoteStart pour les bornes génériques."""
        await self.call(call.ChangeAvailability(
            connector_id=connector_id,
            type=AvailabilityType.operative,
        ))
        await asyncio.sleep(self._remote_start_delay)

        if amps is not None and charging_profile is None:
            charging_profile = self._build_charging_profile(amps)
            log.info("Profil de charge appliqué", id=self.id, amps=amps)

        kwargs: dict = {"connector_id": connector_id, "id_tag": id_tag}
        if charging_profile:
            kwargs["charging_profile"] = charging_profile

        response = await self.call(call.RemoteStartTransaction(**kwargs))

        if response is None:
            log.error("RemoteStart — réponse None (connexion perdue)", id=self.id)
            return False

        success = response.status == RemoteStartStopStatus.accepted
        log.info("RemoteStart", id=self.id, connector=connector_id, amps=amps, success=success)
        return success

    async def remote_stop_transaction(
        self, transaction_id: int, lock: bool = True
    ) -> bool:
        try:
            response = await self.call(call.RemoteStopTransaction(
                transaction_id=transaction_id,
            ))
            success = response.status == RemoteStartStopStatus.accepted
            log.info("RemoteStop", id=self.id,
                     transaction_id=transaction_id, success=success)
            if success and lock:
                asyncio.create_task(self._lock_after_stop())
            return success
        except Exception as e:
            log.error("RemoteStop échoué", id=self.id, error=str(e))
            return False

    async def _lock_after_stop(self) -> None:
        self._stop_meter_polling()
        await asyncio.sleep(5)
        try:
            await self.call(call.ChangeAvailability(
                connector_id=1,
                type=AvailabilityType.inoperative,
            ))
            log.info("ChangeAvailability Inoperative après arrêt", id=self.id)
        except Exception as e:
            log.warning("Verrouillage après arrêt échoué", id=self.id, error=str(e))

    async def reset(self, reset_type: str = "Soft") -> bool:
        try:
            response = await self.call(call.Reset(type=ResetType(reset_type)))
            success = response.status == ResetStatus.accepted
            log.info("Reset", id=self.id, type=reset_type, success=success)
            return success
        except Exception as e:
            log.error("Reset échoué", id=self.id, error=str(e))
            return False

    async def clear_cache(self) -> bool:
        try:
            response = await self.call(call.ClearCache())
            success = response.status == ClearCacheStatus.accepted
            log.info("ClearCache", id=self.id, success=success)
            return success
        except Exception as e:
            log.error("ClearCache échoué", id=self.id, error=str(e))
            return False

    async def unlock_connector(self, connector_id: int = 1) -> str:
        try:
            response = await self.call(call.UnlockConnector(connector_id=connector_id))
            log.info("UnlockConnector", id=self.id,
                     connector=connector_id, status=response.status)
            return response.status
        except Exception as e:
            log.error("UnlockConnector échoué", id=self.id, error=str(e))
            return "Failed"

    async def set_available(self, connector_id: int = 1) -> bool:
        try:
            response = await self.call(call.ChangeAvailability(
                connector_id=connector_id,
                type=AvailabilityType.operative,
            ))
            success = response.status in [
                AvailabilityStatus.accepted, AvailabilityStatus.scheduled,
            ]
            log.info("SetAvailable", id=self.id, connector=connector_id, success=success)
            return success
        except Exception as e:
            log.error("SetAvailable échoué", id=self.id, error=str(e))
            return False

    async def set_unavailable(self, connector_id: int = 1) -> bool:
        try:
            response = await self.call(call.ChangeAvailability(
                connector_id=connector_id,
                type=AvailabilityType.inoperative,
            ))
            success = response.status in [
                AvailabilityStatus.accepted, AvailabilityStatus.scheduled,
            ]
            log.info("SetUnavailable", id=self.id, connector=connector_id, success=success)
            return success
        except Exception as e:
            log.error("SetUnavailable échoué", id=self.id, error=str(e))
            return False

    async def get_configuration(self, keys: list[str] = []) -> dict:
        try:
            response = await self.call(call.GetConfiguration(key=keys))
            result = {}
            for item in response.configuration_key or []:
                result[item["key"]] = {
                    "value":    item.get("value", ""),
                    "readonly": item.get("readonly", False),
                }
            self._config_cache.update({k: v["value"] for k, v in result.items()})
            await self.server.broadcast_config(self.id, self._config_cache)
            return result
        except Exception as e:
            log.error("GetConfiguration échoué", id=self.id, error=str(e))
            return {}

    async def change_configuration(self, key: str, value: str) -> str:
        try:
            response = await self.call(call.ChangeConfiguration(key=key, value=value))
            log.info("ChangeConfiguration", id=self.id,
                     key=key, value=value, status=response.status)
            if response.status == ConfigurationStatus.accepted:
                self._config_cache[key] = value
            return response.status
        except Exception as e:
            log.error("ChangeConfiguration échoué", id=self.id, error=str(e))
            return "Failed"

    async def trigger_message(
        self, requested_message: str, connector_id: Optional[int] = None
    ) -> str:
        try:
            kwargs: dict = {"requested_message": MessageTrigger(requested_message)}
            if connector_id is not None:
                kwargs["connector_id"] = connector_id
            response = await self.call(call.TriggerMessage(**kwargs))
            log.info("TriggerMessage", id=self.id,
                     message=requested_message, status=response.status)
            return response.status
        except Exception as e:
            log.error("TriggerMessage échoué", id=self.id, error=str(e))
            return "Failed"

    async def get_diagnostics(
        self, location: str, retries: int = 1, retry_interval: int = 30,
        start_time: Optional[str] = None, stop_time: Optional[str] = None,
    ) -> Optional[str]:
        try:
            kwargs: dict = {"location": location, "retries": retries,
                            "retry_interval": retry_interval}
            if start_time:
                kwargs["start_time"] = start_time
            if stop_time:
                kwargs["stop_time"] = stop_time
            response = await self.call(call.GetDiagnostics(**kwargs))
            log.info("GetDiagnostics", id=self.id, filename=response.file_name)
            return response.file_name
        except Exception as e:
            log.error("GetDiagnostics échoué", id=self.id, error=str(e))
            return None

    async def update_firmware(
        self, location: str, retrieve_date: str,
        retries: int = 1, retry_interval: int = 30
    ) -> bool:
        try:
            await self.call(call.UpdateFirmware(
                location=location, retrieve_date=retrieve_date,
                retries=retries, retry_interval=retry_interval,
            ))
            log.info("UpdateFirmware envoyé", id=self.id, location=location)
            return True
        except Exception as e:
            log.error("UpdateFirmware échoué", id=self.id, error=str(e))
            return False

    async def set_charging_profile(self, connector_id: int, charging_profile: dict) -> str:
        try:
            response = await self.call(call.SetChargingProfile(
                connector_id=connector_id,
                cs_charging_profiles=charging_profile,
            ))
            log.info("SetChargingProfile", id=self.id,
                     connector=connector_id, status=response.status)
            return response.status
        except Exception as e:
            log.error("SetChargingProfile échoué", id=self.id, error=str(e))
            return "Failed"

    async def clear_charging_profile(
        self, profile_id: Optional[int] = None, connector_id: Optional[int] = None,
        charging_profile_purpose: Optional[str] = None, stack_level: Optional[int] = None,
    ) -> str:
        try:
            kwargs: dict = {}
            if profile_id is not None:
                kwargs["id"] = profile_id
            if connector_id is not None:
                kwargs["connector_id"] = connector_id
            if charging_profile_purpose:
                kwargs["charging_profile_purpose"] = charging_profile_purpose
            if stack_level is not None:
                kwargs["stack_level"] = stack_level
            response = await self.call(call.ClearChargingProfile(**kwargs))
            log.info("ClearChargingProfile", id=self.id, status=response.status)
            return response.status
        except Exception as e:
            log.error("ClearChargingProfile échoué", id=self.id, error=str(e))
            return "Failed"

    async def get_composite_schedule(
        self, connector_id: int, duration: int, charging_rate_unit: str = "W"
    ) -> Optional[dict]:
        try:
            response = await self.call(call.GetCompositeSchedule(
                connector_id=connector_id, duration=duration,
                charging_rate_unit=charging_rate_unit,
            ))
            log.info("GetCompositeSchedule", id=self.id,
                     connector=connector_id, status=response.status)
            return {
                "status":            response.status,
                "connector_id":      response.connector_id,
                "schedule_start":    response.schedule_start,
                "charging_schedule": response.charging_schedule,
            }
        except Exception as e:
            log.error("GetCompositeSchedule échoué", id=self.id, error=str(e))
            return None

    async def reserve_now(
        self, connector_id: int, expiry_date: str, id_tag: str,
        reservation_id: int, parent_id_tag: Optional[str] = None
    ) -> str:
        try:
            kwargs: dict = {"connector_id": connector_id, "expiry_date": expiry_date,
                            "id_tag": id_tag, "reservation_id": reservation_id}
            if parent_id_tag:
                kwargs["parent_id_tag"] = parent_id_tag
            response = await self.call(call.ReserveNow(**kwargs))
            log.info("ReserveNow", id=self.id, connector=connector_id, status=response.status)
            return response.status
        except Exception as e:
            log.error("ReserveNow échoué", id=self.id, error=str(e))
            return "Failed"

    async def cancel_reservation(self, reservation_id: int) -> str:
        try:
            response = await self.call(call.CancelReservation(reservation_id=reservation_id))
            log.info("CancelReservation", id=self.id,
                     reservation_id=reservation_id, status=response.status)
            return response.status
        except Exception as e:
            log.error("CancelReservation échoué", id=self.id, error=str(e))
            return "Failed"

    async def send_local_list(
        self, list_version: int, update_type: str, local_authorization_list: list = []
    ) -> str:
        try:
            response = await self.call(call.SendLocalList(
                list_version=list_version, update_type=update_type,
                local_authorization_list=local_authorization_list,
            ))
            log.info("SendLocalList", id=self.id, version=list_version, status=response.status)
            return response.status
        except Exception as e:
            log.error("SendLocalList échoué", id=self.id, error=str(e))
            return "Failed"

    async def get_local_list_version(self) -> int:
        try:
            response = await self.call(call.GetLocalListVersion())
            log.info("GetLocalListVersion", id=self.id, version=response.list_version)
            return response.list_version
        except Exception as e:
            log.error("GetLocalListVersion échoué", id=self.id, error=str(e))
            return -1

    async def data_transfer(
        self, vendor_id: str, message_id: str = "", data: str = ""
    ) -> Optional[str]:
        try:
            response = await self.call(call.DataTransfer(
                vendor_id=vendor_id, message_id=message_id, data=data,
            ))
            log.info("DataTransfer", id=self.id, vendor=vendor_id, status=response.status)
            return response.data
        except Exception as e:
            log.error("DataTransfer échoué", id=self.id, error=str(e))
            return None

    # ──────────────────────────────────────────────────────
    # Déconnexion
    # ──────────────────────────────────────────────────────

    async def on_disconnect(self) -> None:
        log.info("Borne déconnectée", id=self.id)
        # Annuler toutes les tâches de boot (post_boot_sequence, fetch_configuration,
        # populate_local_list, etc.) pour éviter qu'elles tournent comme zombies
        # sur le WebSocket mort et génèrent des erreurs ou races lors du reconnect.
        self._cancel_boot_tasks()
        self._stop_meter_polling()
        async with AsyncSessionLocal() as db:
            await db.execute(
                update(Charger).where(Charger.id == self.id)
                .values(status=ChargerStatus.OFFLINE)
            )
            await db.commit()
        await self.server.broadcast_status(self.id, 0, ChargerStatus.OFFLINE)
