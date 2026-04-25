# core/charge_point/handlers.py
# HandlersMixin — tous les @on(Action.X) OCPP 1.6J handlers.
# Extrait de core/charge_point.py dans le refactor Phase 1 (2026-04-23).
from __future__ import annotations
import asyncio
from datetime import datetime, timezone

from ocpp.routing import on
from ocpp.v16 import call_result
from ocpp.v16.enums import (
    Action, RegistrationStatus, AuthorizationStatus,
    DataTransferStatus,
)
from sqlalchemy import select, update

from db.database import AsyncSessionLocal
from db.models import (
    Charger, Connector, Session, MeterValue,
    ChargerStatus, ConnectorStatus, SessionStatus,
    DataTransferLog,
    FirmwareUpdate,
    DiagnosticsRequest,
    StatusNotificationLog,
)
from core.charger_profiles import detect_profile, profile_summary
from core.logging import log

VEHICLE_PRESENT_STATUSES = {"Charging","Preparing","Finishing","SuspendedEV","SuspendedEVSE"}


class HandlersMixin:
    # ── Handlers ─────────────────────────────────────────────────────────────
    @on(Action.BootNotification)
    async def on_boot_notification(self, charge_point_vendor, charge_point_model, **kwargs):
        log.info("BootNotification", id=self.id, vendor=charge_point_vendor, model=charge_point_model)
        self._cancel_boot_tasks()
        self._status_received.clear()
        self._synthetic_session_id = None
        self._auto_start_pending = False
        firmware = kwargs.get("firmware_version","")
        self.profile = detect_profile(charge_point_vendor, charge_point_model, firmware)
        log.info("Profil détecté", id=self.id, **profile_summary(self.profile))
        try:
            async with AsyncSessionLocal() as db:
                charger = await db.get(Charger, self.id)
                if charger is None:
                    charger = Charger(id=self.id, manufacturer=charge_point_vendor,
                                     model=charge_point_model, firmware_version=firmware,
                                     ip_address=self.ip_address, status=ChargerStatus.AVAILABLE,
                                     boot_lock=True)
                    db.add(charger)
                else:
                    charger.manufacturer=charge_point_vendor; charger.model=charge_point_model
                    charger.firmware_version=firmware or charger.firmware_version
                    charger.ip_address=self.ip_address; charger.status=ChargerStatus.AVAILABLE
                self._boot_lock = charger.boot_lock
                self._default_max_amps = charger.default_max_amps
                await db.commit()
            self._remote_start_delay = charger.remote_start_delay if charger.remote_start_delay is not None else self.profile.remote_start_delay
            self._local_id_tag = charger.local_id_tag or self.profile.local_auth_id_tag
            # A2 : override heartbeat_interval par borne (sinon fallback profile)
            self._heartbeat_interval = (
                charger.heartbeat_interval
                if getattr(charger, "heartbeat_interval", None)
                else self.profile.heartbeat_interval
            )
        except Exception as e:
            log.error("BootNotification DB error", id=self.id, error=str(e))
            self._heartbeat_interval = self.profile.heartbeat_interval
        self._add_task(self._log_event("BootNotification",{"vendor":charge_point_vendor,"model":charge_point_model,"firmware":firmware,"profile":self.profile.name,"heartbeat_interval":self._heartbeat_interval}))
        # Sprint 30 Volet A : si une mise à jour firmware récente est en vol,
        # capturer la nouvelle version annoncée au BootNotification.
        self._add_task(self._complete_firmware_update_on_boot(firmware))
        # Sprint 31 Volet B : détecter SupportedFeatureProfiles si pas encore
        # connus pour cette borne (première fois ou upgrade firmware).
        self._add_task(self._detect_supported_profiles())
        self._add_task(self._post_boot_sequence())
        self._add_task(self._fetch_configuration())
        self._add_task(self._init_transaction_id())
        return call_result.BootNotification(
            current_time=datetime.now(timezone.utc).isoformat(),
            interval=self._heartbeat_interval,
            status=RegistrationStatus.accepted)
    @on(Action.Heartbeat)
    async def on_heartbeat(self, **kwargs):
        now = datetime.now(timezone.utc)
        async with AsyncSessionLocal() as db:
            # Sprint 31 Volet D — correctif last_seen stale :
            # au boot du serveur, toutes les bornes sont marquées OFFLINE
            # (ocpp_server.py:353). Certaines TechnoVE / Grizzl-E reconnectent
            # avec Heartbeat seul (sans BootNotification) — on repasse alors
            # à AVAILABLE ici pour que /fleet voit la borne comme vivante.
            r = await db.execute(select(Charger).where(Charger.id == self.id))
            c = r.scalar_one_or_none()
            if c is not None:
                c.last_heartbeat = now
                if c.status == ChargerStatus.OFFLINE:
                    c.status = ChargerStatus.AVAILABLE
                    log.info("Heartbeat — borne repassée AVAILABLE (Volet D)", id=self.id)
                    # Reconnect sans BootNotification → initialiser le compteur tx_id
                    # pour éviter de réutiliser un transactionId déjà en DB.
                    self._add_task(self._init_transaction_id())
            await db.commit()
        await self.server.broadcast_heartbeat(self.id, now.isoformat())
        return call_result.Heartbeat(current_time=now.isoformat())
    @on(Action.StatusNotification)
    async def on_status_notification(self, connector_id, error_code, status, **kwargs):
        normalized = self._normalize_status(status)
        prev = self._connector1_status
        log.info("StatusNotification", id=self.id, connector=connector_id, status=normalized)
        if connector_id == 1:
            self._connector1_status = normalized
            self._status_received.set()
        # Sprint 31 Volet C — payload OCPP 1.6J §4.8 complet pour historisation
        info = kwargs.get("info")
        vendor_id_raw = kwargs.get("vendor_id")
        vendor_err_raw = kwargs.get("vendor_error_code")
        ts_raw = kwargs.get("timestamp")
        ts_parsed = None
        if ts_raw:
            try:
                ts_parsed = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
            except Exception:
                ts_parsed = None
        async with AsyncSessionLocal() as db:
            r = await db.execute(select(Connector).where(Connector.charger_id==self.id, Connector.connector_id==connector_id))
            conn = r.scalar_one_or_none()
            if conn is None:
                conn = Connector(charger_id=self.id, connector_id=connector_id); db.add(conn)
            try: conn.status = ConnectorStatus(normalized)
            except: conn.status = ConnectorStatus.UNAVAILABLE
            conn.error_code = error_code if error_code != "NoError" else None
            if connector_id == 0:
                try: await db.execute(update(Charger).where(Charger.id==self.id).values(status=ChargerStatus(normalized)))
                except: pass
            # Sprint 31 Volet C — log complet de chaque StatusNotification
            try:
                db.add(StatusNotificationLog(
                    charger_id=self.id,
                    connector_id=connector_id,
                    status=normalized,
                    error_code=error_code if error_code != "NoError" else None,
                    info=str(info)[:200] if info else None,
                    vendor_id=str(vendor_id_raw)[:100] if vendor_id_raw else None,
                    vendor_error_code=str(vendor_err_raw)[:100] if vendor_err_raw else None,
                    timestamp=ts_parsed,
                    received_at=datetime.now(timezone.utc),
                ))
            except Exception as e:
                log.warning("StatusNotificationLog insert failed", id=self.id, error=str(e))
            await db.commit()
        await self.server.broadcast_status(self.id, connector_id, normalized)
        if connector_id == 1: await self._handle_status_transition(prev, normalized)
        # S2f-auto — bloquer immédiatement + notifier telemetry au branchement
        if connector_id > 0 and normalized == "Preparing":
            self._add_task(self._send_block_profile())   # Bug 2 : blocage avant la course
            self._add_task(self._fire_auto_schedule())
        return call_result.StatusNotification()

    async def _send_block_profile(self):
        """Bug 2 — TxDefaultProfile 0.1A dès Preparing, avant que la tx démarre."""
        profile = {
            "chargingProfileId": 98,
            "stackLevel": 10,
            "chargingProfilePurpose": "TxDefaultProfile",
            "chargingProfileKind": "Relative",
            "chargingSchedule": {
                "chargingRateUnit": "A",
                "chargingSchedulePeriod": [{"startPeriod": 0, "limit": 0.1}],
            },
        }
        status = await self.set_charging_profile(connector_id=1, profile=profile)
        log.info("block profile sent", id=self.id, status=status)

    async def _fire_auto_schedule(self):
        """POST non-bloquant vers telemetry quand la borne passe en Preparing."""
        import json as _j
        import asyncio
        import urllib.request

        def _post():
            data = _j.dumps({"charger_id": self.id}).encode()
            req = urllib.request.Request(
                "http://127.0.0.1:5001/api/v1/auto-schedule",
                data=data,
                headers={"Content-Type": "application/json"},
            )
            resp = urllib.request.urlopen(req, timeout=10)
            return _j.loads(resp.read().decode())

        try:
            result = await asyncio.get_running_loop().run_in_executor(None, _post)
            log.info("auto-schedule notified", id=self.id, tier=result.get("tier"))
            await self._apply_schedule_profile(
                result.get("window_start"), result.get("max_kw", 3.6)
            )
        except Exception as exc:
            log.warning("auto-schedule notify failed", id=self.id, error=str(exc))

    async def _apply_schedule_profile(self, window_start_iso, max_kw: float):
        """Bug 3 — si tx déjà active, envoie TxProfile deux-périodes pour retarder la recharge."""
        if not window_start_iso:
            return
        from datetime import datetime, timezone as _tz
        try:
            ws = datetime.fromisoformat(str(window_start_iso).replace("Z", "+00:00"))
        except Exception:
            log.warning("_apply_schedule_profile bad window_start", id=self.id, v=window_start_iso)
            return

        active_tx_ids = [tid for tid in self._active_transactions if tid > 0]
        if not active_tx_ids:
            log.info("_apply_schedule_profile no active tx — TxDefault already blocking", id=self.id)
            return

        tx_id = active_tx_ids[-1]
        now = datetime.now(_tz.utc)
        max_amps = round(max_kw * 1000.0 / 240.0, 1)
        delay_s = max(0, int((ws - now).total_seconds()))

        if delay_s > 60:
            periods = [
                {"startPeriod": 0, "limit": 0.1},
                {"startPeriod": delay_s, "limit": max_amps},
            ]
        else:
            periods = [{"startPeriod": 0, "limit": max_amps}]

        profile = {
            "chargingProfileId": 99,
            "stackLevel": 10,
            "chargingProfilePurpose": "TxProfile",
            "chargingProfileKind": "Relative",
            "transactionId": tx_id,
            "chargingSchedule": {
                "chargingRateUnit": "A",
                "chargingSchedulePeriod": periods,
            },
        }
        status = await self.set_charging_profile(connector_id=1, profile=profile)
        log.info("TxProfile applied", id=self.id, tx_id=tx_id, delay_s=delay_s,
                 max_amps=max_amps, status=status)
    @on(Action.Authorize)
    async def on_authorize(self, id_tag, **kwargs):
        info = await self._authorize_id_tag(id_tag)
        log.info("Authorize", id=self.id, id_tag=id_tag,
                 status=str(info.get("status")))
        return call_result.Authorize(id_tag_info=info)
    @on(Action.StartTransaction)
    async def on_start_transaction(self, connector_id, id_tag, meter_start, timestamp, **kwargs):
        # Sprint 29 Volet A — bloque l'ouverture de session si tag non whitelisté.
        # Retrocompat : table vide → Accepted par _authorize_id_tag.
        auth_info = await self._authorize_id_tag(id_tag)
        if auth_info.get("status") != AuthorizationStatus.accepted:
            log.warning("StartTransaction rejeté — tag non autorisé",
                        id=self.id, id_tag=id_tag,
                        status=str(auth_info.get("status")))
            return call_result.StartTransaction(
                transaction_id=0,
                id_tag_info=auth_info,
            )
        tx_id = self._next_transaction_id; self._next_transaction_id += 1
        log.info("StartTransaction", id=self.id, tx_id=tx_id)
        if self._synthetic_session_id: await self._close_synthetic_session()
        async with AsyncSessionLocal() as db:
            ex = await db.execute(select(Session).where(Session.charger_id==self.id, Session.transaction_id==tx_id))
            session = ex.scalar_one_or_none()
            if session:
                self._active_transactions[tx_id] = session.id
            else:
                r = await db.execute(select(Connector).where(Connector.charger_id==self.id, Connector.connector_id==connector_id))
                conn = r.scalar_one_or_none()
                try: st = datetime.fromisoformat(timestamp.replace("Z","+00:00"))
                except: st = datetime.now(timezone.utc)
                session = Session(transaction_id=tx_id, charger_id=self.id,
                                  connector_id=conn.id if conn else connector_id,
                                  id_tag=id_tag, meter_start=meter_start/1000.0,
                                  start_time=st, status=SessionStatus.ACTIVE)
                db.add(session)
                try:
                    await db.commit(); await db.refresh(session)
                    self._active_transactions[tx_id] = session.id
                except Exception as e:
                    await db.rollback()
                    log.error("StartTransaction insert failed", id=self.id, error=str(e))
        # Sprint 28 A7 — consommer une éventuelle réservation active
        await self._consume_reservation(connector_id, id_tag)
        await self.server.broadcast_transaction(self.id,"start",{"transaction_id":tx_id,"connector_id":connector_id,"id_tag":id_tag,"meter_start":meter_start})
        return call_result.StartTransaction(transaction_id=tx_id, id_tag_info=auth_info)
    @on(Action.StopTransaction)
    async def on_stop_transaction(self, meter_stop, timestamp, transaction_id, reason="Local", **kwargs):
        log.info("StopTransaction", id=self.id, tx_id=transaction_id, reason=reason)
        async with AsyncSessionLocal() as db:
            r = await db.execute(select(Session).where(Session.transaction_id==transaction_id, Session.charger_id==self.id))
            session = r.scalar_one_or_none()
            if session:
                kwh = meter_stop/1000.0
                session.meter_stop=kwh; session.energy_wh=(kwh-(session.meter_start or 0))*1000
                try: session.stop_time=datetime.fromisoformat(timestamp.replace("Z","+00:00"))
                except: session.stop_time=datetime.now(timezone.utc)
                session.stop_reason=reason; session.status=SessionStatus.COMPLETED
                await db.commit()
            self._active_transactions.pop(transaction_id,None)
        await self.server.broadcast_transaction(self.id,"stop",{"transaction_id":transaction_id,"meter_stop":meter_stop,"reason":reason})
        return call_result.StopTransaction(id_tag_info={"status":AuthorizationStatus.accepted})
    @on(Action.MeterValues)
    async def on_meter_values(self, connector_id, meter_value, **kwargs):
        tx_id = kwargs.get("transaction_id")
        async with AsyncSessionLocal() as db:
            session_db = None
            if tx_id:
                r = await db.execute(select(Session).where(Session.transaction_id==tx_id, Session.charger_id==self.id))
                session_db = r.scalar_one_or_none()
            if session_db is None and self._synthetic_session_id:
                r = await db.execute(select(Session).where(Session.id==self._synthetic_session_id))
                session_db = r.scalar_one_or_none()
            for mv in meter_value:
                parsed = self._parse_meter_value(mv)
                if parsed.get("power_w") is None and parsed.get("energy_wh") is not None:
                    now=datetime.now(timezone.utc); last=self._last_energy.get(connector_id)
                    if last:
                        dh=(now-last["time"]).total_seconds()/3600; dwh=parsed["energy_wh"]-last["energy_wh"]
                        if dh>0 and dwh>=0: parsed["power_w"]=round(dwh/dh,1)
                    self._last_energy[connector_id]={"energy_wh":parsed["energy_wh"],"time":now}
                raw_ts=mv.get("timestamp")
                try: ts=datetime.fromisoformat(raw_ts.replace("Z","+00:00")) if raw_ts else datetime.now(timezone.utc)
                except: ts=datetime.now(timezone.utc)
                db.add(MeterValue(session_id=session_db.id if session_db else None, charger_id=self.id, timestamp=ts,
                                  energy_wh=parsed.get("energy_wh"), power_w=parsed.get("power_w"),
                                  current_a=parsed.get("current_a"), voltage_v=parsed.get("voltage_v"),
                                  soc_percent=parsed.get("soc_percent"), raw=mv))
                await self.server.broadcast_meter_value(self.id, connector_id, parsed)
            await db.commit()

        # Sprint 30 Volet C : drift detection Current.Import vs profil attendu
        # (après commit DB pour ne pas bloquer le handler OCPP)
        for mv in meter_value:
            parsed = self._parse_meter_value(mv)
            cur = parsed.get("current_a")
            if cur is not None:
                try:
                    await self._check_current_drift(connector_id, float(cur))
                except Exception as e:
                    log.warning("_check_current_drift failed",
                                id=self.id, error=str(e))
                break  # 1 seule vérif par MeterValues (sample le plus frais)

        return call_result.MeterValues()
    @on(Action.DataTransfer)
    async def on_data_transfer(self, vendor_id, message_id="", data="", **kwargs):
        """Sprint 28 A7 — persiste les DataTransfer entrants (audit vendor-specific).

        On loggue tout sans interpréter (Grizzl-E diag, TechnoVE télémétrie
        custom, etc.). Réponse générique Accepted — ajouter un router
        vendor-specific plus tard si nécessaire.
        """
        # Data peut être dict/list/str selon la borne — normaliser en str pour Text
        try:
            import json as _json
            data_str = data if isinstance(data, str) else _json.dumps(data)
        except Exception:
            data_str = str(data)
        status = "Accepted"
        try:
            async with AsyncSessionLocal() as db:
                db.add(DataTransferLog(
                    charger_id=self.id, direction="in",
                    vendor_id=vendor_id, message_id=message_id or None,
                    data=data_str, response=status,
                    timestamp=datetime.now(timezone.utc),
                ))
                await db.commit()
        except Exception as e:
            log.warning("on_data_transfer persist failed",
                        id=self.id, vendor=vendor_id, error=str(e))
        log.info("DataTransfer reçu", id=self.id,
                 vendor_id=vendor_id, message_id=message_id,
                 data_len=len(data_str) if data_str else 0)
        return call_result.DataTransfer(status=DataTransferStatus.accepted)
    @on(Action.DiagnosticsStatusNotification)
    async def on_diagnostics_status_notification(self, status, **kwargs):
        """Met à jour la dernière DiagnosticsRequest de cette borne avec le
        nouveau statut (Idle / Uploading / Uploaded / UploadFailed).
        """
        try:
            async with AsyncSessionLocal() as db:
                r = await db.execute(
                    select(DiagnosticsRequest)
                    .where(DiagnosticsRequest.charger_id == self.id)
                    .order_by(DiagnosticsRequest.requested_at.desc())
                    .limit(1)
                )
                req = r.scalar_one_or_none()
                if req is not None:
                    req.status = str(status)
                    req.status_updated_at = datetime.now(timezone.utc)
                    await db.commit()
        except Exception as e:
            log.warning("on_diagnostics_status_notification persist failed",
                        id=self.id, error=str(e))
        log.info("DiagnosticsStatusNotification", id=self.id, status=str(status))
        return call_result.DiagnosticsStatusNotification()
    @on(Action.FirmwareStatusNotification)
    async def on_firmware_status_notification(self, status, **kwargs):
        """Sprint 30 Volet A — met à jour le dernier FirmwareUpdate non
        terminal avec le nouveau statut OCPP (Downloading/Downloaded/
        Installing/Installed/DownloadFailed/InstallationFailed/Idle).
        """
        status_str = str(status)
        try:
            async with AsyncSessionLocal() as db:
                terminal = {"Installed", "DownloadFailed", "InstallationFailed"}
                r = await db.execute(
                    select(FirmwareUpdate)
                    .where(
                        FirmwareUpdate.charger_id == self.id,
                        FirmwareUpdate.status.notin_(list(terminal)),
                    )
                    .order_by(FirmwareUpdate.requested_at.desc())
                    .limit(1)
                )
                fw = r.scalar_one_or_none()
                if fw is not None:
                    fw.status = status_str
                    fw.status_updated_at = datetime.now(timezone.utc)
                    await db.commit()
        except Exception as e:
            log.warning("on_firmware_status_notification persist failed",
                        id=self.id, error=str(e))
        log.info("FirmwareStatusNotification", id=self.id, status=status_str)
        return call_result.FirmwareStatusNotification()
