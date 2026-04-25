#!/usr/bin/env python3
"""
virtual_charger.py — Borne OCPP 1.6 virtuelle avec interface web

Usage:
  python virtual_charger.py
  python virtual_charger.py --id VIRTUAL-002 --server ws://192.168.1.10:9000/ocpp/ --port 8002

Ouvre http://localhost:8001 dans ton navigateur.
"""
import argparse
import asyncio
import json
import random
import sys
from datetime import datetime, timezone
from typing import Optional, List, Dict

import websockets
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import uvicorn
from ocpp.v16 import ChargePoint as CP, call, call_result
from ocpp.v16.enums import (
    RegistrationStatus, AuthorizationStatus,
    RemoteStartStopStatus, ResetStatus,
    AvailabilityType, AvailabilityStatus,
    ConfigurationStatus, ClearCacheStatus,
    MessageTrigger, Action,
)
from ocpp.routing import on


# ═══════════════════════════════════════════════════════
# État partagé du simulateur
# ═══════════════════════════════════════════════════════
class SimState:
    def __init__(self):
        # Connexion
        self.charger_id   = "VIRTUAL-001"
        self.server_url   = "ws://localhost:9000/ocpp/"
        self.ws_connected = False

        # Statuts OCPP connecteurs
        self.conn0_status = "Unavailable"
        self.conn1_status = "Unavailable"

        # Véhicule
        self.vehicle_plugged = False
        self.soc_pct         = 20.0    # SOC actuel %
        self.soc_start       = 20.0    # SOC au branchement
        self.soc_target      = 100.0   # SOC cible %
        self.battery_kwh     = 60.0    # Capacité batterie kWh

        # Charge
        self.charging        = False
        self.availability    = "Operative"
        self.max_current_a   = 32.0    # peut être réduit par SetChargingProfile
        self.current_a       = 0.0
        self.power_w         = 0.0
        self.session_energy_wh = 0.0
        self.meter_wh        = round(random.uniform(5000, 30000), 1)  # compteur absolu
        self.meter_start_wh  = 0.0
        self.transaction_id: Optional[int] = None
        self.session_start: Optional[datetime] = None

        # Simulation
        self.speed           = 60      # 1 seconde réelle = speed secondes simulées

        # Messages OCPP
        self.messages: List[Dict] = []

        # Tâches asyncio
        self._cp = None
        self._charge_task: Optional[asyncio.Task] = None
        self._meter_task:  Optional[asyncio.Task] = None
        self._hb_task:     Optional[asyncio.Task] = None
        self._ocpp_task:   Optional[asyncio.Task] = None
        self._next_tx      = 1

        # Clients WebSocket du navigateur
        self._browser_clients: set = set()

    # ── Logging ──────────────────────────────────────────
    def log(self, direction: str, action: str, payload: dict = {}):
        msg = {
            "t":       datetime.now().strftime("%H:%M:%S.%f")[:12],
            "dir":     direction,
            "action":  action,
            "payload": payload,
        }
        self.messages.append(msg)
        if len(self.messages) > 400:
            self.messages = self.messages[-400:]
        asyncio.create_task(self._push({"type": "msg", "msg": msg}))

    # ── Snapshot état complet ─────────────────────────────
    def snapshot(self) -> dict:
        dur = None
        if self.session_start:
            d = datetime.now(timezone.utc) - self.session_start
            h, r = divmod(int(d.total_seconds()), 3600)
            m, s = divmod(r, 60)
            dur = f"{h:02d}:{m:02d}:{s:02d}"
        return {
            "type":              "state",
            "charger_id":        self.charger_id,
            "server_url":        self.server_url,
            "ws_connected":      self.ws_connected,
            "conn0_status":      self.conn0_status,
            "conn1_status":      self.conn1_status,
            "vehicle_plugged":   self.vehicle_plugged,
            "soc_pct":           round(self.soc_pct, 1),
            "soc_start":         self.soc_start,
            "soc_target":        self.soc_target,
            "battery_kwh":       self.battery_kwh,
            "charging":          self.charging,
            "availability":      self.availability,
            "max_current_a":     round(self.max_current_a, 1),
            "current_a":         round(self.current_a, 1),
            "power_w":           round(self.power_w),
            "session_energy_wh": round(self.session_energy_wh, 2),
            "meter_wh":          round(self.meter_wh, 1),
            "transaction_id":    self.transaction_id,
            "session_duration":  dur,
            "speed":             self.speed,
        }

    async def broadcast(self):
        await self._push(self.snapshot())

    async def _push(self, data: dict):
        dead = set()
        for ws in self._browser_clients:
            try:
                await ws.send_json(data)
            except Exception:
                dead.add(ws)
        self._browser_clients -= dead


sim = SimState()


# ═══════════════════════════════════════════════════════
# Charge Point OCPP 1.6
# ═══════════════════════════════════════════════════════
class VirtualCP(CP):

    @on(Action.RemoteStartTransaction)
    async def on_remote_start(self, connector_id: int = 1,
                               id_tag: str = "REMOTE",
                               charging_profile=None, **kwargs):
        sim.log("← recv", "RemoteStartTransaction",
                {"connector_id": connector_id, "id_tag": id_tag,
                 "has_profile": charging_profile is not None})

        if sim.availability == "Inoperative" and not sim.vehicle_plugged:
            sim.log("→ sent", "RemoteStartTransaction.response", {"status": "Rejected"})
            return call_result.RemoteStartTransaction(
                status=RemoteStartStopStatus.rejected)

        # Appliquer la limite de courant du profil si présente
        if charging_profile:
            sched   = charging_profile.get("charging_schedule", {})
            periods = sched.get("charging_schedule_period", [])
            if periods:
                sim.max_current_a = float(periods[0].get("limit", sim.max_current_a))

        asyncio.create_task(_do_remote_start(id_tag, connector_id))
        sim.log("→ sent", "RemoteStartTransaction.response", {"status": "Accepted"})
        return call_result.RemoteStartTransaction(
            status=RemoteStartStopStatus.accepted)

    @on(Action.RemoteStopTransaction)
    async def on_remote_stop(self, transaction_id: int, **kwargs):
        sim.log("← recv", "RemoteStopTransaction", {"transaction_id": transaction_id})
        if sim.transaction_id == transaction_id and sim.charging:
            asyncio.create_task(_do_stop("Remote"))
            sim.log("→ sent", "RemoteStopTransaction.response", {"status": "Accepted"})
            return call_result.RemoteStopTransaction(
                status=RemoteStartStopStatus.accepted)
        sim.log("→ sent", "RemoteStopTransaction.response", {"status": "Rejected"})
        return call_result.RemoteStopTransaction(
            status=RemoteStartStopStatus.rejected)

    @on(Action.SetChargingProfile)
    async def on_set_profile(self, connector_id: int,
                              cs_charging_profiles: dict, **kwargs):
        sched   = cs_charging_profiles.get("charging_schedule", {})
        periods = sched.get("charging_schedule_period", [])
        purpose = cs_charging_profiles.get("charging_profile_purpose", "")
        limit   = float(periods[0].get("limit", sim.max_current_a)) if periods else None

        sim.log("← recv", "SetChargingProfile", {
            "connector_id": connector_id,
            "purpose": purpose,
            "limit_a": limit,
        })

        if limit is not None:
            sim.max_current_a = limit
            if limit == 0 and sim.charging:
                # Coupure immédiate
                sim.charging  = False
                sim.current_a = 0.0
                sim.power_w   = 0.0
                await _set_status("SuspendedEVSE")
            elif limit > 0 and sim.conn1_status == "SuspendedEVSE" and sim.vehicle_plugged:
                # Reprise de la charge
                sim.charging = True
                await _set_status("Charging")
            await sim.broadcast()

        sim.log("→ sent", "SetChargingProfile.response", {"status": "Accepted"})
        return call_result.SetChargingProfile(status="Accepted")

    @on(Action.ChangeAvailability)
    async def on_change_avail(self, connector_id: int, type: str, **kwargs):
        sim.log("← recv", "ChangeAvailability",
                {"connector_id": connector_id, "type": type})

        if type == AvailabilityType.operative:
            sim.availability = "Operative"
            if not sim.vehicle_plugged:
                await _set_status("Available")
            status = AvailabilityStatus.accepted
        else:
            sim.availability = "Inoperative"
            if sim.charging:
                status = AvailabilityStatus.scheduled
                # Deviendra Inoperative après la session
            else:
                await _set_status("Unavailable")
                status = AvailabilityStatus.accepted

        sim.log("→ sent", "ChangeAvailability.response", {"status": str(status)})
        return call_result.ChangeAvailability(status=status)

    @on(Action.GetConfiguration)
    async def on_get_config(self, key: list = [], **kwargs):
        sim.log("← recv", "GetConfiguration", {"keys": key})
        cfg = {
            "MeterValueSampleInterval":         "60",
            "HeartbeatInterval":                "30",
            "NumberOfConnectors":               "1",
            "SupportedFeatureProfiles":         "Core,SmartCharging,RemoteTrigger",
            "MeterValuesSampledData":           "Energy.Active.Import.Register,Power.Active.Import,Current.Import,Voltage,SoC",
            "ChargeProfileMaxStackLevel":       "10",
            "ChargingScheduleMaxPeriods":       "24",
            "MaxChargingProfilesInstalled":     "5",
            "StopTransactionOnEVSideDisconnect":"true",
            "ConnectorSwitch3to1PhaseSupported":"false",
        }
        keys_to_return = key if key else list(cfg.keys())
        result = [
            {"key": k, "value": cfg[k], "readonly": False}
            for k in keys_to_return if k in cfg
        ]
        sim.log("→ sent", "GetConfiguration.response", {"count": len(result)})
        return call_result.GetConfiguration(
            configuration_key=result, unknown_key=[])

    @on(Action.ChangeConfiguration)
    async def on_change_config(self, key: str, value: str, **kwargs):
        sim.log("← recv", "ChangeConfiguration", {"key": key, "value": value})
        sim.log("→ sent", "ChangeConfiguration.response", {"status": "Accepted"})
        return call_result.ChangeConfiguration(
            status=ConfigurationStatus.accepted)

    @on(Action.TriggerMessage)
    async def on_trigger(self, requested_message: str,
                          connector_id: int = None, **kwargs):
        sim.log("← recv", "TriggerMessage",
                {"message": requested_message, "connector_id": connector_id})
        asyncio.create_task(_send_triggered(requested_message, connector_id))
        sim.log("→ sent", "TriggerMessage.response", {"status": "Accepted"})
        return call_result.TriggerMessage(status="Accepted")

    @on(Action.Reset)
    async def on_reset(self, type: str, **kwargs):
        sim.log("← recv", "Reset", {"type": type})
        asyncio.create_task(_do_reset(type))
        sim.log("→ sent", "Reset.response", {"status": "Accepted"})
        return call_result.Reset(status=ResetStatus.accepted)

    @on(Action.ClearCache)
    async def on_clear_cache(self, **kwargs):
        sim.log("← recv", "ClearCache", {})
        sim.log("→ sent", "ClearCache.response", {"status": "Accepted"})
        return call_result.ClearCache(status=ClearCacheStatus.accepted)

    @on(Action.UnlockConnector)
    async def on_unlock(self, connector_id: int, **kwargs):
        sim.log("← recv", "UnlockConnector", {"connector_id": connector_id})
        sim.log("→ sent", "UnlockConnector.response", {"status": "Unlocked"})
        return call_result.UnlockConnector(status="Unlocked")

    @on(Action.GetCompositeSchedule)
    async def on_get_schedule(self, connector_id: int, duration: int, **kwargs):
        sim.log("← recv", "GetCompositeSchedule",
                {"connector_id": connector_id, "duration": duration})
        sim.log("→ sent", "GetCompositeSchedule.response", {"status": "Accepted"})
        return call_result.GetCompositeSchedule(
            status="Accepted",
            connector_id=connector_id,
            schedule_start=datetime.now(timezone.utc).isoformat(),
            charging_schedule={
                "charging_rate_unit": "A",
                "charging_schedule_period": [
                    {"start_period": 0, "limit": sim.max_current_a}
                ],
            },
        )

    @on(Action.ClearChargingProfile)
    async def on_clear_profile(self, **kwargs):
        sim.log("← recv", "ClearChargingProfile", {})
        sim.max_current_a = 32.0
        sim.log("→ sent", "ClearChargingProfile.response", {"status": "Accepted"})
        return call_result.ClearChargingProfile(status="Accepted")

    @on(Action.DataTransfer)
    async def on_data_transfer(self, vendor_id: str, **kwargs):
        sim.log("← recv", "DataTransfer", {"vendor_id": vendor_id})
        sim.log("→ sent", "DataTransfer.response", {"status": "Accepted"})
        return call_result.DataTransfer(status="Accepted")


# ═══════════════════════════════════════════════════════
# Helpers de simulation
# ═══════════════════════════════════════════════════════
VOLTAGE_V    = 240.0
POWER_FACTOR = 0.98


async def _set_status(status: str, connector_id: int = 1):
    """Met à jour le statut local et envoie StatusNotification."""
    if connector_id == 0:
        sim.conn0_status = status
    else:
        sim.conn1_status = status

    if sim._cp:
        payload = {"connector_id": connector_id,
                   "error_code": "NoError", "status": status}
        sim.log("→ sent", "StatusNotification", payload)
        try:
            # create_task évite le deadlock quand appelé depuis un @on() handler.
            # cp.call() nécessite que start() soit libre pour recevoir la réponse,
            # mais start() attend que le handler retourne → deadlock sans create_task.
            asyncio.create_task(sim._cp.call(call.StatusNotification(
                connector_id=connector_id,
                error_code="NoError",
                status=status,
            )))
        except Exception:
            pass
    await sim.broadcast()


async def _do_remote_start(id_tag: str, connector_id: int = 1):
    """Séquence déclenchée par RemoteStartTransaction."""
    await asyncio.sleep(0.5)
    if not sim.vehicle_plugged:
        sim.vehicle_plugged = True
        await _set_status("Preparing")
        await asyncio.sleep(1)
    await _start_transaction(id_tag)


async def _start_transaction(id_tag: str = "LOCAL"):
    """Envoie StartTransaction et démarre la boucle de charge."""
    if not sim._cp:
        return

    sim.meter_start_wh    = sim.meter_wh
    sim.session_energy_wh = 0.0
    sim.session_start     = datetime.now(timezone.utc)

    payload = {
        "connector_id": 1, "id_tag": id_tag,
        "meter_start": int(sim.meter_wh),
        "timestamp": sim.session_start.isoformat(),
    }
    sim.log("→ sent", "StartTransaction", payload)
    try:
        resp = await sim._cp.call(call.StartTransaction(
            connector_id=1,
            id_tag=id_tag,
            meter_start=int(sim.meter_wh),
            timestamp=sim.session_start.isoformat(),
        ))
        # ocpp==1.0.0 retourne None sur CALLERROR au lieu de lever une exception
        if resp is None:
            sim.log("✗ error", "StartTransaction", {
                "error": "Pas de réponse (CALLERROR) — vérifier les logs serveur"
            })
            return
        sim.transaction_id = resp.transaction_id
        sim.log("← recv", "StartTransaction.response", {
            "transaction_id": resp.transaction_id,
            "status": resp.id_tag_info.get("status", "?") if resp.id_tag_info else "?",
        })
        sim.charging = True
        await _set_status("Charging")

        _cancel_tasks()
        sim._charge_task = asyncio.create_task(_charge_loop())
        sim._meter_task  = asyncio.create_task(_meter_loop())
    except Exception as e:
        sim.log("✗ error", "StartTransaction", {"error": str(e)})


async def _do_stop(reason: str = "Remote"):
    """Arrête la charge et envoie StopTransaction."""
    _cancel_tasks()
    sim.charging  = False
    sim.current_a = 0.0
    sim.power_w   = 0.0

    await _set_status("Finishing")
    await asyncio.sleep(1)

    if sim._cp and sim.transaction_id is not None:
        now = datetime.now(timezone.utc).isoformat()
        sim.log("→ sent", "StopTransaction", {
            "transaction_id": sim.transaction_id,
            "meter_stop": int(sim.meter_wh),
            "reason": reason,
        })
        try:
            await sim._cp.call(call.StopTransaction(
                meter_stop=int(sim.meter_wh),
                timestamp=now,
                transaction_id=sim.transaction_id,
                reason=reason,
            ))
            sim.log("← recv", "StopTransaction.response", {"status": "Accepted"})
        except Exception as e:
            sim.log("✗ error", "StopTransaction", {"error": str(e)})

    sim.transaction_id = None
    sim.session_start  = None

    if sim.vehicle_plugged and reason not in ("EVDisconnected",):
        # Véhicule encore branché — rester en Finishing
        await _set_status("Finishing")
    else:
        sim.vehicle_plugged = False
        await _set_status("Available")

    # Si Inoperative schedulée, l'appliquer maintenant
    if sim.availability == "Inoperative":
        await _set_status("Unavailable")

    await sim.broadcast()


async def _charge_loop():
    """Boucle principale de simulation de charge (tourne à 1 Hz)."""
    while True:
        if not sim.charging or not sim.vehicle_plugged:
            break

        eff_current = max(0.0, min(sim.max_current_a, 32.0))
        if eff_current <= 0:
            sim.current_a = 0.0
            sim.power_w   = 0.0
            if sim.conn1_status == "Charging":
                await _set_status("SuspendedEVSE")
            await sim.broadcast()
            await asyncio.sleep(1)
            continue

        sim.current_a = eff_current
        sim.power_w   = VOLTAGE_V * eff_current * POWER_FACTOR

        # Énergie livrée (multipliée par speed pour la simulation rapide)
        delta_wh = (sim.power_w / 3600.0) * sim.speed
        sim.session_energy_wh += delta_wh
        sim.meter_wh          += delta_wh

        # SOC : delta_wh / capacité_totale_wh × 100
        sim.soc_pct = min(sim.soc_target,
            sim.soc_pct + delta_wh / (sim.battery_kwh * 10.0)
        )

        if sim.soc_pct >= sim.soc_target:
            # Cible atteinte : véhicule suspend la charge
            sim.soc_pct   = sim.soc_target
            sim.charging  = False
            sim.current_a = 0.0
            sim.power_w   = 0.0
            if sim.soc_target >= 100.0:
                await _set_status("SuspendedEV")
            else:
                # Cible personnalisée atteinte
                await _set_status("SuspendedEV")
            await sim.broadcast()
            break

        await sim.broadcast()
        await asyncio.sleep(1)


async def _meter_loop():
    """Envoie MeterValues toutes les 60 secondes (intervalle simulé)."""
    interval = max(5, 60 // sim.speed)   # accéléré avec speed
    while True:
        await asyncio.sleep(interval)
        if not sim._cp or sim.transaction_id is None:
            continue
        await _send_meter_values()


async def _send_meter_values():
    """Construit et envoie un message MeterValues."""
    now = datetime.now(timezone.utc).isoformat()
    meter_value = [{
        "timestamp": now,
        "sampled_value": [
            {"measurand": "Energy.Active.Import.Register",
             "value": str(round(sim.meter_wh, 1)), "unit": "Wh"},
            {"measurand": "Power.Active.Import",
             "value": str(round(sim.power_w, 0)), "unit": "W"},
            {"measurand": "Current.Import",
             "value": str(round(sim.current_a, 1)), "unit": "A"},
            {"measurand": "Voltage",
             "value": str(round(VOLTAGE_V, 0)), "unit": "V"},
            {"measurand": "SoC",
             "value": str(round(sim.soc_pct, 1)), "unit": "Percent"},
        ],
    }]
    sim.log("→ sent", "MeterValues", {
        "energy_wh": round(sim.meter_wh, 1),
        "power_w":   round(sim.power_w, 0),
        "current_a": round(sim.current_a, 1),
        "soc_pct":   round(sim.soc_pct, 1),
    })
    try:
        # transaction_id est optionnel — ne pas l'inclure si None
        mv_kwargs: dict = {"connector_id": 1, "meter_value": meter_value}
        if sim.transaction_id is not None:
            mv_kwargs["transaction_id"] = sim.transaction_id
        await sim._cp.call(call.MeterValues(**mv_kwargs))
    except Exception as e:
        sim.log("✗ error", "MeterValues", {"error": str(e)})


async def _heartbeat_loop():
    """Heartbeat toutes les 30 secondes."""
    while sim.ws_connected and sim._cp:
        await asyncio.sleep(30)
        if not sim._cp:
            break
        try:
            sim.log("→ sent", "Heartbeat", {})
            await sim._cp.call(call.Heartbeat())
        except Exception:
            break


async def _send_triggered(message: str, connector_id: Optional[int]):
    """Répond à un TriggerMessage du serveur."""
    await asyncio.sleep(0.3)
    if not sim._cp:
        return
    try:
        if message == "MeterValues":
            # Envoyer même sans transaction active (ex: polling synthétique)
            await _send_meter_values()
        elif message == "StatusNotification":
            cid = connector_id or 1
            st  = sim.conn1_status if cid == 1 else sim.conn0_status
            sim.log("→ sent", "StatusNotification (triggered)",
                    {"connector_id": cid, "status": st})
            await sim._cp.call(call.StatusNotification(
                connector_id=cid, error_code="NoError", status=st,
            ))
        elif message == "Heartbeat":
            sim.log("→ sent", "Heartbeat (triggered)", {})
            await sim._cp.call(call.Heartbeat())
        elif message == "BootNotification":
            await _send_boot()
    except Exception as e:
        sim.log("✗ error", f"Triggered {message}", {"error": str(e)})


async def _send_boot():
    if not sim._cp:
        return
    sim.log("→ sent", "BootNotification",
            {"vendor": "Virtual", "model": "SimV1"})
    resp = await sim._cp.call(call.BootNotification(
        charge_point_vendor="Virtual",
        charge_point_model="SimV1",
        charge_point_serial_number=f"SIM-{sim.charger_id}",
        firmware_version="1.0.0",
    ))
    sim.log("← recv", "BootNotification.response", {
        "status": str(resp.status), "interval": resp.interval,
    })


async def _do_reset(reset_type: str):
    await asyncio.sleep(1)
    if sim.charging:
        await _do_stop("SoftReset" if reset_type == "Soft" else "HardReset")
    sim.log("⟳ info", f"Reset {reset_type} — reconnexion dans 3s", {})
    await asyncio.sleep(3)
    # La boucle de reconnexion dans connect_ocpp() va reprendre


def _cancel_tasks():
    for t in (sim._charge_task, sim._meter_task):
        if t and not t.done():
            t.cancel()
    sim._charge_task = None
    sim._meter_task  = None


# ═══════════════════════════════════════════════════════
# Boucle de connexion OCPP (reconnexion automatique)
# ═══════════════════════════════════════════════════════
async def connect_ocpp():
    """Se connecte au serveur OCPP et reconnecte automatiquement."""
    while True:
        url = f"{sim.server_url}{sim.charger_id}"
        sim.log("⟳ info", "Connexion OCPP", {"url": url})
        try:
            async with websockets.connect(
                url,
                subprotocols=["ocpp1.6"],
                ping_interval=30,
                ping_timeout=10,
            ) as ws:
                cp = VirtualCP(sim.charger_id, ws)
                sim._cp        = cp
                sim.ws_connected = True
                await sim.broadcast()

                # Boot sequence
                try:
                    await _send_boot()
                    await asyncio.sleep(1)
                    await _set_status("Available", 0)
                    await _set_status("Available", 1)
                    sim._hb_task = asyncio.create_task(_heartbeat_loop())
                except Exception as e:
                    sim.log("✗ error", "Boot sequence", {"error": str(e)})

                await cp.start()   # bloque jusqu'à déconnexion

        except Exception as e:
            sim.log("✗ error", "Connexion perdue", {"error": str(e)})
        finally:
            _cancel_tasks()
            if sim._hb_task:
                sim._hb_task.cancel()
            sim._cp          = None
            sim.ws_connected = False
            sim.conn0_status = "Offline"
            sim.conn1_status = "Offline"
            if sim.transaction_id:
                sim.transaction_id = None
                sim.session_start  = None
                sim.charging       = False
            await sim.broadcast()

        sim.log("⟳ info", "Reconnexion dans 5s", {})
        await asyncio.sleep(5)


# ═══════════════════════════════════════════════════════
# API FastAPI
# ═══════════════════════════════════════════════════════
app = FastAPI(title="Virtual Charger UI", docs_url=None)


class PlugRequest(BaseModel):
    plugged:     bool
    soc_start:   float = 20.0
    soc_target:  float = 100.0
    battery_kwh: float = 60.0


class ConnectRequest(BaseModel):
    charger_id: str
    server_url: str


class SpeedRequest(BaseModel):
    speed: int


@app.get("/", response_class=HTMLResponse)
async def ui():
    return HTMLResponse(content=HTML_PAGE)


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await websocket.accept()
    sim._browser_clients.add(websocket)
    # Envoyer état initial + historique messages
    await websocket.send_json(sim.snapshot())
    for msg in sim.messages[-100:]:
        await websocket.send_json({"type": "msg", "msg": msg})
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        sim._browser_clients.discard(websocket)


@app.post("/api/plug")
async def plug(req: PlugRequest):
    if req.plugged == sim.vehicle_plugged:
        return {"ok": False, "detail": "Pas de changement"}

    if req.plugged:
        # Brancher le véhicule
        sim.soc_start    = req.soc_start
        sim.soc_pct      = req.soc_start
        sim.soc_target   = req.soc_target
        sim.battery_kwh  = req.battery_kwh
        sim.vehicle_plugged = True

        if sim.availability == "Operative":
            await _set_status("Preparing")
            # Auto-démarrage après 2 secondes
            asyncio.create_task(_auto_start())
        else:
            await _set_status("Preparing")  # Inoperative : attendre RemoteStart
    else:
        # Débrancher
        sim.vehicle_plugged = False
        if sim.charging or sim.transaction_id:
            await _do_stop("EVDisconnected")
        else:
            await _set_status("Available")

    return {"ok": True}


async def _auto_start():
    await asyncio.sleep(2)
    if sim.vehicle_plugged and not sim.charging and not sim.transaction_id:
        await _start_transaction("LOCAL")


@app.post("/api/connect")
async def reconnect(req: ConnectRequest):
    sim.charger_id = req.charger_id
    sim.server_url = req.server_url
    # Tuer la connexion actuelle → la boucle reconnectera
    if sim._cp:
        try:
            await sim._cp._connection.close()
        except Exception:
            pass
    if sim._ocpp_task:
        sim._ocpp_task.cancel()
    sim._ocpp_task = asyncio.create_task(connect_ocpp())
    return {"ok": True}


@app.post("/api/speed")
async def set_speed(req: SpeedRequest):
    sim.speed = max(1, min(600, req.speed))
    return {"ok": True, "speed": sim.speed}


@app.delete("/api/messages")
async def clear_messages():
    sim.messages.clear()
    await sim._push({"type": "clear_log"})
    return {"ok": True}


@app.get("/api/state")
async def get_state():
    return sim.snapshot()


# ═══════════════════════════════════════════════════════
# Interface HTML
# ═══════════════════════════════════════════════════════
HTML_PAGE = r"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Borne Virtuelle OCPP</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,sans-serif;background:#0f172a;color:#e2e8f0;min-height:100vh}
header{background:#1e293b;padding:.75rem 1.25rem;display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid #334155;position:sticky;top:0;z-index:50;flex-wrap:wrap;gap:.5rem}
header h1{font-size:1rem;font-weight:700;color:#38bdf8}
.ws-badge{font-size:.72rem;font-weight:600;padding:3px 10px;border-radius:999px}
.ws-on{background:#166534;color:#86efac}.ws-off{background:#374151;color:#9ca3af}
main{padding:1rem;max-width:1400px;margin:0 auto;display:grid;grid-template-columns:340px 1fr;grid-template-rows:auto auto;gap:1rem}
@media(max-width:780px){main{grid-template-columns:1fr}}
.card{background:#1e293b;border:1px solid #334155;border-radius:12px;padding:1.1rem}
.card h2{font-size:.8rem;font-weight:600;color:#64748b;text-transform:uppercase;letter-spacing:.06em;margin-bottom:.875rem}
/* Vehicle panel */
.col-left{display:flex;flex-direction:column;gap:1rem}
.btn-plug{width:100%;padding:.875rem;border-radius:10px;border:none;font-size:1rem;font-weight:700;cursor:pointer;transition:all .2s}
.btn-plug.off{background:#0284c7;color:#fff}
.btn-plug.on{background:#dc2626;color:#fff}
.slider-row{display:flex;align-items:center;gap:.625rem;margin-bottom:.5rem}
.slider-row label{font-size:.72rem;color:#94a3b8;min-width:90px}
.slider-row input[type=range]{flex:1;accent-color:#38bdf8}
.slider-row .val{font-size:.72rem;color:#e2e8f0;min-width:38px;text-align:right}
.num-row{display:flex;align-items:center;gap:.625rem;margin-bottom:.5rem}
.num-row label{font-size:.72rem;color:#94a3b8;min-width:90px}
.num-row input{width:80px;background:#0f172a;border:1px solid #334155;color:#e2e8f0;padding:4px 8px;border-radius:6px;font-size:.8rem}
.progress-wrap{margin:.875rem 0 .5rem;background:#0f172a;border-radius:8px;height:22px;overflow:hidden;position:relative}
.progress-bar{height:100%;background:linear-gradient(90deg,#0284c7,#38bdf8);transition:width .5s;border-radius:8px}
.progress-label{position:absolute;top:0;left:0;right:0;text-align:center;line-height:22px;font-size:.75rem;font-weight:600}
.session-info{display:grid;grid-template-columns:1fr 1fr;gap:.375rem;margin-top:.625rem}
.si-box{background:#0f172a;border-radius:7px;padding:.5rem;text-align:center}
.si-lbl{font-size:.62rem;color:#64748b;margin-bottom:2px}
.si-val{font-size:.88rem;font-weight:700;color:#38bdf8}
/* Metrics */
.col-right{display:flex;flex-direction:column;gap:1rem}
.power-big{font-size:2.5rem;font-weight:700;color:#38bdf8;text-align:center;margin:.5rem 0;line-height:1}
.power-sub{font-size:.72rem;color:#64748b;text-align:center;margin-bottom:.875rem}
.metrics-grid{display:grid;grid-template-columns:1fr 1fr 1fr;gap:.5rem}
.m-box{background:#0f172a;border-radius:8px;padding:.625rem;text-align:center}
.m-lbl{font-size:.62rem;color:#64748b;text-transform:uppercase;letter-spacing:.04em;margin-bottom:3px}
.m-val{font-size:1.05rem;font-weight:700}
.status-row{display:flex;gap:.5rem;flex-wrap:wrap;align-items:center}
.st-badge{font-size:.72rem;font-weight:600;padding:3px 10px;border-radius:999px}
.b-available{background:#166534;color:#86efac}
.b-charging{background:#1e3a8a;color:#93c5fd}
.b-preparing{background:#78350f;color:#fcd34d}
.b-finishing{background:#4c1d95;color:#c4b5fd}
.b-faulted{background:#7f1d1d;color:#fca5a5}
.b-unavailable,.b-offline{background:#374151;color:#9ca3af}
.b-suspendedev,.b-suspendedevse{background:#1e293b;color:#94a3b8;border:1px solid #334155}
/* Settings collapsible */
.settings-toggle{font-size:.72rem;background:none;border:1px solid #334155;color:#94a3b8;padding:3px 10px;border-radius:6px;cursor:pointer}
.settings-panel{display:none;margin-top:.75rem;padding:.75rem;background:#0f172a;border-radius:8px;border:1px solid #334155}
.settings-panel.open{display:block}
.sf{display:flex;align-items:center;gap:.5rem;margin-bottom:.5rem}
.sf label{font-size:.72rem;color:#94a3b8;min-width:80px}
.sf input{flex:1;background:#1e293b;border:1px solid #334155;color:#e2e8f0;padding:5px 8px;border-radius:6px;font-size:.8rem}
/* Speed */
.speed-row{display:flex;align-items:center;gap:.5rem;margin-top:.25rem}
.speed-btn{padding:2px 9px;border-radius:5px;border:1px solid #334155;background:#0f172a;color:#94a3b8;font-size:.72rem;cursor:pointer}
.speed-btn.active{background:#1e3a8a;color:#93c5fd;border-color:#1e3a8a}
/* Log */
.col-full{grid-column:1/-1}
.log-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:.75rem}
.log-header h2{margin:0}
.log-controls{display:flex;gap:.5rem;align-items:center}
.btn-sm{padding:3px 9px;border-radius:6px;border:none;font-size:.72rem;cursor:pointer;background:#334155;color:#e2e8f0}
#log{height:280px;overflow-y:auto;font-family:monospace;font-size:.72rem}
.msg{display:flex;gap:.5rem;padding:3px 4px;border-radius:4px;margin-bottom:1px;align-items:flex-start;cursor:pointer}
.msg:hover{background:#1e293b}
.msg-t{color:#475569;min-width:95px;flex-shrink:0}
.msg-dir{min-width:60px;flex-shrink:0;font-weight:600}
.msg-action{color:#e2e8f0;flex:1}
.msg-payload{color:#475569;white-space:pre-wrap;word-break:break-all;display:none;padding:4px 8px;background:#0f172a;border-radius:4px;margin-top:2px;font-size:.68rem}
.msg-payload.open{display:block}
.dir-sent .msg-dir{color:#38bdf8}
.dir-recv .msg-dir{color:#34d399}
.dir-error .msg-dir{color:#f87171}
.dir-info .msg-dir{color:#94a3b8}
.autoscroll-lbl{font-size:.7rem;color:#64748b;display:flex;align-items:center;gap:4px;cursor:pointer}
</style>
</head>
<body>
<header>
  <h1>&#9889; Borne Virtuelle OCPP 1.6</h1>
  <div style="display:flex;align-items:center;gap:.75rem;flex-wrap:wrap">
    <span id="hdr-id" style="font-size:.72rem;color:#64748b"></span>
    <span id="ws-badge" class="ws-badge ws-off">&#9679; Déconnecté</span>
    <button class="settings-toggle" onclick="toggleSettings()">&#9881; Paramètres</button>
  </div>
</header>

<div id="settings-panel" class="settings-panel" style="max-width:1400px;margin:.5rem auto 0;padding:0 1rem">
  <div class="card" style="margin-bottom:0">
    <div class="sf"><label>ID Borne</label><input id="cfg-id" value="VIRTUAL-001"></div>
    <div class="sf"><label>Serveur OCPP</label><input id="cfg-url" value="ws://localhost:9000/ocpp/"></div>
    <button class="btn-sm" onclick="reconnect()" style="background:#0284c7;color:#fff">Reconnecter</button>
  </div>
</div>

<main>
  <!-- Colonne gauche : véhicule -->
  <div class="col-left">
    <div class="card">
      <h2>Véhicule</h2>
      <button id="btn-plug" class="btn-plug off" onclick="togglePlug()">&#128268; Brancher le véhicule</button>

      <div style="margin-top:1rem">
        <div class="slider-row">
          <label>SOC départ</label>
          <input type="range" id="sl-soc-start" min="0" max="99" value="20"
                 oninput="document.getElementById('v-soc-start').textContent=this.value+'%'">
          <span class="val" id="v-soc-start">20%</span>
        </div>
        <div class="slider-row">
          <label>SOC cible</label>
          <input type="range" id="sl-soc-target" min="1" max="100" value="100"
                 oninput="document.getElementById('v-soc-target').textContent=this.value+'%'">
          <span class="val" id="v-soc-target">100%</span>
        </div>
        <div class="num-row">
          <label>Batterie (kWh)</label>
          <input type="number" id="inp-bat" value="60" min="10" max="200" step="5">
        </div>
      </div>

      <div class="progress-wrap">
        <div class="progress-bar" id="soc-bar" style="width:20%"></div>
        <div class="progress-label" id="soc-lbl">SOC : 20%</div>
      </div>

      <div class="session-info">
        <div class="si-box"><div class="si-lbl">Durée session</div><div class="si-val" id="si-dur">—</div></div>
        <div class="si-box"><div class="si-lbl">Énergie session</div><div class="si-val" id="si-nrj">— kWh</div></div>
        <div class="si-box"><div class="si-lbl">Transaction</div><div class="si-val" id="si-tx">—</div></div>
        <div class="si-box"><div class="si-lbl">Compteur borne</div><div class="si-val" id="si-meter">— kWh</div></div>
      </div>
    </div>

    <div class="card">
      <h2>Vitesse simulation</h2>
      <div class="speed-row">
        <span style="font-size:.72rem;color:#94a3b8">1 s réelle =</span>
        <button class="speed-btn" onclick="setSpeed(1)" data-s="1">1s</button>
        <button class="speed-btn" onclick="setSpeed(10)" data-s="10">10s</button>
        <button class="speed-btn active" onclick="setSpeed(60)" data-s="60">1 min</button>
        <button class="speed-btn" onclick="setSpeed(300)" data-s="300">5 min</button>
        <button class="speed-btn" onclick="setSpeed(600)" data-s="600">10 min</button>
      </div>
      <div style="font-size:.68rem;color:#475569;margin-top:.5rem" id="speed-hint">
        Temps pour 60 kWh @ 32A : <span id="charge-time">~8h</span>
      </div>
    </div>
  </div>

  <!-- Colonne droite : métriques + état -->
  <div class="col-right">
    <div class="card">
      <h2>Puissance instantanée</h2>
      <div class="power-big" id="m-power">— W</div>
      <div class="power-sub" id="m-power-sub">En attente de charge</div>
      <div class="metrics-grid">
        <div class="m-box"><div class="m-lbl">Courant</div><div class="m-val" id="m-cur" style="color:#34d399">— A</div></div>
        <div class="m-box"><div class="m-lbl">Tension</div><div class="m-val" id="m-volt" style="color:#f59e0b">240 V</div></div>
        <div class="m-box"><div class="m-lbl">SOC actuel</div><div class="m-val" id="m-soc" style="color:#a78bfa">—%</div></div>
        <div class="m-box"><div class="m-lbl">Courant max imposé</div><div class="m-val" id="m-maxcur" style="color:#64748b">32 A</div></div>
        <div class="m-box"><div class="m-lbl">Disponibilité</div><div class="m-val" id="m-avail" style="font-size:.8rem;color:#94a3b8">Operative</div></div>
        <div class="m-box"><div class="m-lbl">Vitesse sim.</div><div class="m-val" id="m-speed" style="color:#64748b;font-size:.8rem">×60</div></div>
      </div>
    </div>

    <div class="card">
      <h2>État OCPP</h2>
      <div class="status-row">
        <span style="font-size:.72rem;color:#64748b">Conn. 0 :</span>
        <span class="st-badge b-unavailable" id="st-c0">Unavailable</span>
        <span style="font-size:.72rem;color:#64748b;margin-left:.5rem">Conn. 1 :</span>
        <span class="st-badge b-unavailable" id="st-c1">Unavailable</span>
      </div>
      <div style="margin-top:.625rem;font-size:.7rem;color:#475569" id="st-hint">
        Aucune session active
      </div>
    </div>
  </div>

  <!-- Log messages OCPP (pleine largeur) -->
  <div class="col-full">
    <div class="card">
      <div class="log-header">
        <h2>Messages OCPP</h2>
        <div class="log-controls">
          <label class="autoscroll-lbl">
            <input type="checkbox" id="autoscroll" checked> Auto-scroll
          </label>
          <button class="btn-sm" onclick="clearLog()">Vider</button>
        </div>
      </div>
      <div id="log"></div>
    </div>
  </div>
</main>

<script>
let plugged = false;
let currentSpeed = 60;

// ── WebSocket ───────────────────────────────────────────
const proto = location.protocol === 'https:' ? 'wss' : 'ws';
let ws;
function connectWS() {
  ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onopen  = () => {};
  ws.onclose = () => setTimeout(connectWS, 3000);
  ws.onerror = () => {};
  ws.onmessage = e => {
    const d = JSON.parse(e.data);
    if      (d.type === 'state')     applyState(d);
    else if (d.type === 'msg')       appendMsg(d.msg);
    else if (d.type === 'clear_log') document.getElementById('log').innerHTML = '';
  };
}
connectWS();

// ── State ───────────────────────────────────────────────
function applyState(s) {
  // WS badge
  const wb = document.getElementById('ws-badge');
  wb.textContent = s.ws_connected ? '● Connecté' : '● Déconnecté';
  wb.className   = `ws-badge ${s.ws_connected ? 'ws-on' : 'ws-off'}`;
  document.getElementById('hdr-id').textContent = s.charger_id;

  // SOC progress
  const soc = s.soc_pct;
  document.getElementById('soc-bar').style.width = soc + '%';
  document.getElementById('soc-lbl').textContent = `SOC : ${soc}%  (cible ${s.soc_target}%)`;
  document.getElementById('m-soc').textContent   = soc + '%';

  // Plug button
  plugged = s.vehicle_plugged;
  const btn = document.getElementById('btn-plug');
  btn.textContent = plugged ? '🔌 Débrancher le véhicule' : '🔌 Brancher le véhicule';
  btn.className   = `btn-plug ${plugged ? 'on' : 'off'}`;

  // Métriques
  const pw = s.power_w;
  document.getElementById('m-power').textContent   = pw > 0 ? fmtPower(pw) : '— W';
  document.getElementById('m-power-sub').textContent =
    pw > 0 ? `${(pw/1000).toFixed(2)} kW — ${s.current_a.toFixed(1)} A × 240 V` : 'En attente de charge';
  document.getElementById('m-cur').textContent    = s.current_a > 0 ? s.current_a.toFixed(1) + ' A' : '— A';
  document.getElementById('m-volt').textContent   = '240 V';
  document.getElementById('m-maxcur').textContent = s.max_current_a + ' A';
  document.getElementById('m-avail').textContent  = s.availability;
  document.getElementById('m-speed').textContent  = '×' + s.speed;

  // Session
  document.getElementById('si-dur').textContent   = s.session_duration || '—';
  document.getElementById('si-nrj').textContent   = s.session_energy_wh > 0
    ? (s.session_energy_wh/1000).toFixed(3) + ' kWh' : '—';
  document.getElementById('si-tx').textContent    = s.transaction_id ? '#' + s.transaction_id : '—';
  document.getElementById('si-meter').textContent = (s.meter_wh/1000).toFixed(2) + ' kWh';

  // État OCPP
  setStatusBadge('st-c0', s.conn0_status);
  setStatusBadge('st-c1', s.conn1_status);
  const hint = document.getElementById('st-hint');
  if (s.transaction_id) {
    hint.textContent = `Session active — Transaction #${s.transaction_id}`;
    hint.style.color = '#93c5fd';
  } else {
    hint.textContent = 'Aucune session active';
    hint.style.color = '#475569';
  }

  // Vitesse — mettre à jour le hint
  currentSpeed = s.speed;
  updateSpeedButtons(s.speed);
  updateChargeTimeHint(s.battery_kwh, s.speed);
}

function setStatusBadge(id, status) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = status;
  el.className   = `st-badge b-${status.toLowerCase()}`;
}

function fmtPower(w) {
  return w >= 1000 ? (w/1000).toFixed(2) + ' kW' : Math.round(w) + ' W';
}

// ── Log messages ────────────────────────────────────────
const DIR_CLASS = {
  '→ sent': 'dir-sent', '← recv': 'dir-recv',
  '✗ error': 'dir-error', '⟳ info': 'dir-info',
};

function appendMsg(msg) {
  const log = document.getElementById('log');
  const row = document.createElement('div');
  row.className = `msg ${DIR_CLASS[msg.dir] || ''}`;

  const pay = JSON.stringify(msg.payload, null, 2);
  row.innerHTML = `
    <span class="msg-t">${msg.t}</span>
    <span class="msg-dir">${msg.dir}</span>
    <span class="msg-action">${msg.action}</span>
  `;
  if (pay && pay !== '{}') {
    const payEl = document.createElement('div');
    payEl.className = 'msg-payload';
    payEl.textContent = pay;
    const wrapper = document.createElement('div');
    wrapper.style.width = '100%';
    wrapper.appendChild(row);
    wrapper.appendChild(payEl);
    row.onclick = () => payEl.classList.toggle('open');
    log.appendChild(wrapper);
  } else {
    log.appendChild(row);
  }

  const as = document.getElementById('autoscroll');
  if (as && as.checked) log.scrollTop = log.scrollHeight;
}

// ── Contrôles ───────────────────────────────────────────
async function togglePlug() {
  const soc_start  = +document.getElementById('sl-soc-start').value;
  const soc_target = +document.getElementById('sl-soc-target').value;
  const bat_kwh    = +document.getElementById('inp-bat').value;

  await fetch('/api/plug', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      plugged: !plugged,
      soc_start, soc_target, battery_kwh: bat_kwh,
    }),
  });
}

async function setSpeed(s) {
  currentSpeed = s;
  updateSpeedButtons(s);
  await fetch('/api/speed', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({speed: s}),
  });
}

function updateSpeedButtons(s) {
  document.querySelectorAll('.speed-btn').forEach(b => {
    b.classList.toggle('active', +b.dataset.s === s);
  });
}

function updateChargeTimeHint(bat_kwh, speed) {
  const bat = bat_kwh || 60;
  const totalSec = (bat * 1000 / (240 * 32 * 0.98)) * 3600 / speed;
  const h = Math.floor(totalSec / 3600);
  const m = Math.floor((totalSec % 3600) / 60);
  const el = document.getElementById('charge-time');
  if (el) el.textContent = h > 0 ? `~${h}h${m > 0 ? m + 'min' : ''}` : `~${m}min`;
}

async function reconnect() {
  const cid = document.getElementById('cfg-id').value.trim();
  const url = document.getElementById('cfg-url').value.trim();
  await fetch('/api/connect', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({charger_id: cid, server_url: url}),
  });
  toggleSettings();
}

async function clearLog() {
  await fetch('/api/messages', {method: 'DELETE'});
}

function toggleSettings() {
  const p = document.getElementById('settings-panel');
  p.querySelector('.card').classList.toggle('open', !p.querySelector('.card').classList.contains('open'));
  p.style.display = p.style.display === 'none' ? 'block' : 'none';
}
document.getElementById('settings-panel').style.display = 'none';
</script>
</body>
</html>"""


# ═══════════════════════════════════════════════════════
# Démarrage
# ═══════════════════════════════════════════════════════
async def main():
    parser = argparse.ArgumentParser(description="Borne OCPP 1.6 virtuelle")
    parser.add_argument("--id",     default="VIRTUAL-001",
                        help="ID de la borne (défaut: VIRTUAL-001)")
    parser.add_argument("--server", default="ws://localhost:9000/ocpp/",
                        help="URL WebSocket du serveur OCPP")
    parser.add_argument("--port",   type=int, default=8001,
                        help="Port de l'interface web (défaut: 8001)")
    args = parser.parse_args()

    sim.charger_id = args.id
    sim.server_url = args.server

    print(f"⚡ Borne virtuelle  : {args.id}")
    print(f"⚡ Serveur OCPP     : {args.server}{args.id}")
    print(f"⚡ Interface web    : http://localhost:{args.port}")
    print()

    # Lancer connexion OCPP + serveur web en parallèle
    sim._ocpp_task = asyncio.create_task(connect_ocpp())

    config = uvicorn.Config(
        app, host="0.0.0.0", port=args.port,
        log_level="warning",
    )
    server = uvicorn.Server(config)
    await server.serve()


if __name__ == "__main__":
    asyncio.run(main())
