# core/ocpp_server.py
import asyncio
import json
import base64
from typing import Optional
import websockets
from websockets.server import WebSocketServerProtocol

from config import settings
from core.charge_point import ChargePoint
from core.logging import log
from db.database import AsyncSessionLocal
from db.models import Charger


class OCPPServer:

    def __init__(self):
        self._charge_points: dict[str, ChargePoint] = {}
        self._dashboard_clients: set = set()

    @property
    def connected_chargers(self) -> list[str]:
        return list(self._charge_points.keys())

    def get_charger(self, charge_point_id: str) -> Optional[ChargePoint]:
        return self._charge_points.get(charge_point_id)

    # ── Basic Auth ────────────────────────────────────────

    def _parse_basic_auth(self, websocket) -> tuple[Optional[str], Optional[str]]:
        headers = dict(websocket.request_headers)
        auth = headers.get("Authorization") or headers.get("authorization")
        if not auth or not auth.startswith("Basic "):
            return None, None
        try:
            decoded = base64.b64decode(auth[6:]).decode("utf-8")
            user, password = decoded.split(":", 1)
            return user, password
        except Exception:
            return None, None

    async def _authenticate(self, charge_point_id: str, password: Optional[str]) -> bool:
        async with AsyncSessionLocal() as db:
            charger = await db.get(Charger, charge_point_id)
            if charger is None:
                return True
            if not charger.is_enabled:
                log.warning("Borne désactivée", id=charge_point_id)
                return False
            if charger.auth_password:
                if password != charger.auth_password:
                    log.warning("Mauvais mot de passe", id=charge_point_id)
                    return False
            return True

    # ── Connexions OCPP ───────────────────────────────────

    async def on_connect(self, websocket: WebSocketServerProtocol, path: str) -> None:
        charge_point_id = path.strip("/").split("/")[-1]

        if not charge_point_id:
            await websocket.close(1008, "charge_point_id manquant")
            return

        if websocket.subprotocol not in settings.ocpp.supported_protocols:
            log.warning("Protocole non supporté", id=charge_point_id,
                        requested=websocket.subprotocol)
            await websocket.close(1002, "Protocole non supporté")
            return

        _, password = self._parse_basic_auth(websocket)
        if not await self._authenticate(charge_point_id, password):
            await websocket.close(4001, "Non autorisé")
            return

        if charge_point_id in self._charge_points:
            log.info("Reconnexion", id=charge_point_id)
            await self._charge_points[charge_point_id].on_disconnect()

        cp = ChargePoint(charge_point_id, websocket, server=self)
        self._charge_points[charge_point_id] = cp

        log.info("Borne connectée", id=charge_point_id,
                 ip=websocket.remote_address, protocol=websocket.subprotocol)

        try:
            await cp.start()
        except websockets.exceptions.ConnectionClosed as e:
            log.info("Connexion fermée", id=charge_point_id, code=e.code)
        except Exception as e:
            log.error("Erreur", id=charge_point_id, error=str(e))
        finally:
            await cp.on_disconnect()
            self._charge_points.pop(charge_point_id, None)
            log.info("Borne retirée du registre", id=charge_point_id)

    # ── Broadcast dashboard ───────────────────────────────

    async def broadcast_status(self, charger_id: str, connector_id: int, status) -> None:
        await self._broadcast({
            "type":         "status_update",
            "charger_id":   charger_id,
            "connector_id": connector_id,
            "status":       str(status.value) if hasattr(status, "value") else str(status),
        })

    async def broadcast_meter_value(self, charger_id: str, connector_id: int, values: dict) -> None:
        await self._broadcast({
            "type":           "meter_value",
            "charger_id":     charger_id,
            "connector_id":   connector_id,
            "power_w":        values.get("power_w"),
            "energy_wh":      values.get("energy_wh"),
            "session_energy_wh": values.get("session_energy_wh"),
            "current_a":      values.get("current_a"),
            "voltage_v":      values.get("voltage_v"),
            "soc_percent":    values.get("soc_percent"),
            "timestamp":      __import__("datetime").datetime.utcnow().isoformat(),
        })

    async def broadcast_heartbeat(self, charger_id: str, timestamp: str) -> None:
        await self._broadcast({
            "type":       "heartbeat",
            "charger_id": charger_id,
            "timestamp":  timestamp,
        })

    async def broadcast_transaction(self, charger_id: str, action: str, data: dict) -> None:
        await self._broadcast({
            "type":       "transaction",
            "charger_id": charger_id,
            "action":     action,
            **data,
        })

    async def broadcast_event(self, charger_id: str, event_type: str, data: dict) -> None:
        await self._broadcast({
            "type":       "event",
            "charger_id": charger_id,
            "event_type": event_type,
            **data,
        })

    async def broadcast_config(self, charger_id: str, config: dict) -> None:
        await self._broadcast({
            "type":       "config",
            "charger_id": charger_id,
            "config":     config,
        })

    async def _broadcast(self, data: dict) -> None:
        if not self._dashboard_clients:
            return
        message = json.dumps(data)
        dead = set()
        for client in self._dashboard_clients:
            try:
                # FastAPI WebSocket utilise send_text(), pas send()
                await client.send_text(message)
            except Exception:
                dead.add(client)
        self._dashboard_clients -= dead

    def register_dashboard_client(self, ws) -> None:
        self._dashboard_clients.add(ws)

    def unregister_dashboard_client(self, ws) -> None:
        self._dashboard_clients.discard(ws)

    # ── Démarrage ─────────────────────────────────────────

    async def start(self) -> None:
        log.info("Démarrage OCPP WebSocket",
                 host=settings.ocpp.host, port=settings.ocpp.port)

        async with websockets.serve(
            self.on_connect,
            settings.ocpp.host,
            settings.ocpp.port,
            subprotocols=settings.ocpp.supported_protocols,
            ping_interval=settings.ocpp.ping_interval,
            ping_timeout=settings.ocpp.ping_timeout,
        ):
            log.info("Serveur OCPP prêt",
                     url=f"ws://{settings.ocpp.host}:{settings.ocpp.port}/ocpp/[ID_BORNE]")
            await asyncio.Future()
