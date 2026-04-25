# core/charge_point/base.py
# Composé : handlers + actions + state mixins au-dessus de ocpp.v16.ChargePoint.
# Voir core/charger_profiles.py pour les flags par fabricant.
# AUCUN if is_technove / elif is_grizzle ici — tout passe par self.profile.<flag>
from __future__ import annotations
import asyncio
import json as _json
from ocpp.v16 import ChargePoint as OcppChargePoint

from core.charger_profiles import PROFILE_GENERIC, ChargerProfile
from core.logging import log

from .handlers import HandlersMixin
from .actions import ActionsMixin
from .state import StateMixin


class ChargePoint(HandlersMixin, ActionsMixin, StateMixin, OcppChargePoint):
    def __init__(self, charge_point_id, websocket, server, ip_address="unknown"):
        super().__init__(charge_point_id, websocket)
        self.server = server
        self.ip_address = ip_address
        self.profile: ChargerProfile = PROFILE_GENERIC
        self._active_transactions: dict = {}
        self._transactions_lock = asyncio.Lock()
        self._next_transaction_id: int = 1
        self._last_energy: dict = {}
        self._config_cache: dict = {}
        self._active_measurands: list = []
        self._boot_lock: bool = True
        self._default_max_amps = None
        self._connector1_status: str = "Unknown"
        self._meter_poll_task = None
        self._synthetic_session_id = None
        self._remote_start_delay: float = 1.0
        self._local_id_tag: str = "ADMIN"
        self._boot_tasks: set = set()
        self._status_received = asyncio.Event()
        self._remote_start_lock = asyncio.Lock()
        self._auto_start_pending: bool = False
        self._server_lock_active: bool = False   # BUG-2 FIX : évite AttributeError
        self._scheduler_applied_amps: float | None = None  # tracker pour le scheduler
        # Sprint 30 Volet C : compteur de samples Current.Import consécutifs en drift
        # dict[connector_id] = int (nb samples au-dessus de expected + 2A)
        self._current_drift_samples: dict = {}

    async def route_message(self, raw_message) -> None:
        """MED-02 — Détection doublon messageId avant dispatch au handler OCPP.
        Ferme le WebSocket sur doublon (STEVE-2 : SteVe ferme la session).
        """
        try:
            text = raw_message.decode() if isinstance(raw_message, (bytes, bytearray)) else raw_message
            msg = _json.loads(text)
            if isinstance(msg, list) and len(msg) >= 2 and msg[0] == 2:  # CALL
                message_id = str(msg[1])
                if not self.server.check_duplicate_message_id(self._connection, message_id):
                    error_frame = _json.dumps([4, message_id, "GenericError", "Duplicate messageId", {}])
                    try:
                        await self._connection.send(error_frame)
                    except Exception:
                        pass
                    await self._connection.close(3002, "Duplicate messageId")
                    return
        except Exception as exc:
            log.warning("route_message duplicate check failed", id=self.id, error=str(exc))
        await super().route_message(raw_message)
