"""
Wrapper httpx pour le serveur OCPP REST (port 8000).

Tous les endpoints /api/commands/* et /api/chargers/* utilisés par les tests.
Basic auth non supporté ici (mode ouvert d'après .env Pi) — si activé plus tard,
lire OCPP_DASH_USER/OCPP_DASH_PASS et ajouter auth=(u,p) au client.
"""
from __future__ import annotations

from typing import Any, Optional

import httpx


class OCPPServerAPI:
    def __init__(self, base_url: str, timeout: float = 10.0):
        self._base_url = base_url.rstrip("/")
        self._client: Optional[httpx.AsyncClient] = None
        self._timeout = timeout

    async def __aenter__(self):
        self._client = httpx.AsyncClient(base_url=self._base_url, timeout=self._timeout)
        return self

    async def __aexit__(self, *exc):
        if self._client:
            await self._client.aclose()

    @property
    def client(self) -> httpx.AsyncClient:
        assert self._client is not None
        return self._client

    # ── Chargers ─────────────────────────────────────────────────────────
    async def list_chargers(self) -> list[dict]:
        r = await self.client.get("/api/chargers/")
        r.raise_for_status()
        return r.json()

    async def get_charger(self, charger_id: str) -> dict:
        r = await self.client.get(f"/api/chargers/{charger_id}")
        r.raise_for_status()
        return r.json()

    async def patch_charger(self, charger_id: str, **fields) -> dict:
        r = await self.client.patch(f"/api/chargers/{charger_id}", json=fields)
        r.raise_for_status()
        return r.json()

    # ── Commandes remote ─────────────────────────────────────────────────
    async def trigger(self, charger_id: str, message: str,
                      connector_id: Optional[int] = None) -> dict:
        body: dict[str, Any] = {"charger_id": charger_id, "message": message}
        if connector_id is not None:
            body["connector_id"] = connector_id
        r = await self.client.post("/api/commands/trigger", json=body)
        r.raise_for_status()
        return r.json()

    async def remote_start(self, charger_id: str, id_tag: str = "ADMIN",
                           connector_id: int = 1, max_amps: Optional[float] = None) -> dict:
        body: dict[str, Any] = {
            "charger_id": charger_id, "id_tag": id_tag,
            "connector_id": connector_id,
        }
        if max_amps is not None:
            body["max_amps"] = max_amps
        r = await self.client.post("/api/commands/remote-start", json=body)
        r.raise_for_status()
        return r.json()

    async def remote_stop(self, charger_id: str, transaction_id: int,
                          lock: bool = True) -> dict:
        r = await self.client.post(
            "/api/commands/remote-stop",
            json={"charger_id": charger_id, "transaction_id": transaction_id, "lock": lock},
        )
        r.raise_for_status()
        return r.json()

    async def set_power_limit(self, charger_id: str, max_amps: float) -> dict:
        r = await self.client.post(
            "/api/commands/set-power-limit",
            json={"charger_id": charger_id, "max_amps": max_amps},
        )
        r.raise_for_status()
        return r.json()

    async def set_charging_profile(self, charger_id: str, max_amps: float,
                                   connector_id: int = 1,
                                   duration_seconds: Optional[int] = None) -> dict:
        body: dict[str, Any] = {
            "charger_id": charger_id, "connector_id": connector_id, "max_amps": max_amps,
        }
        if duration_seconds is not None:
            body["duration_seconds"] = duration_seconds
        r = await self.client.post("/api/commands/charging-profile/set", json=body)
        r.raise_for_status()
        return r.json()

    async def clear_charging_profile(self, charger_id: str, connector_id: int = 0,
                                     profile_id: Optional[int] = None) -> dict:
        body: dict[str, Any] = {"charger_id": charger_id, "connector_id": connector_id}
        if profile_id is not None:
            body["profile_id"] = profile_id
        r = await self.client.post("/api/commands/charging-profile/clear", json=body)
        r.raise_for_status()
        return r.json()

    async def get_snapshots(self, charger_id: str, include_expired: bool = False) -> dict:
        params = {"include_expired": "true"} if include_expired else {}
        r = await self.client.get(
            f"/api/commands/charging-profile/snapshots/{charger_id}",
            params=params,
        )
        r.raise_for_status()
        return r.json()

    async def change_availability(self, charger_id: str, connector_id: int = 0,
                                  operative: bool = True) -> dict:
        r = await self.client.post(
            "/api/commands/change-availability",
            json={"charger_id": charger_id, "connector_id": connector_id, "operative": operative},
        )
        r.raise_for_status()
        return r.json()

    # ── Réservations ─────────────────────────────────────────────────────
    async def reserve(self, charger_id: str, id_tag: str, connector_id: int = 1,
                      expiry_date: Optional[str] = None,
                      check: bool = True) -> httpx.Response:
        """ReserveNow. check=True → raise_for_status; False → renvoie la Response brute."""
        body: dict[str, Any] = {
            "charger_id": charger_id, "id_tag": id_tag, "connector_id": connector_id,
        }
        if expiry_date is not None:
            body["expiry_date"] = expiry_date
        r = await self.client.post("/api/commands/reserve", json=body)
        if check:
            r.raise_for_status()
        return r

    async def cancel_reserve(self, charger_id: str, reservation_id: int,
                             check: bool = True) -> httpx.Response:
        """CancelReservation. check=False pour valider les 404/400 des tests."""
        r = await self.client.post(
            "/api/commands/cancel-reserve",
            json={"charger_id": charger_id, "reservation_id": reservation_id},
        )
        if check:
            r.raise_for_status()
        return r

    async def list_reservations(self, charger_id: str, include_inactive: bool = False) -> dict:
        params = {"include_inactive": "true"} if include_inactive else {}
        r = await self.client.get(
            f"/api/commands/reservations/{charger_id}", params=params,
        )
        r.raise_for_status()
        return r.json()

    # ── DataTransfer ─────────────────────────────────────────────────────
    async def send_data_transfer(self, charger_id: str, vendor_id: str,
                                 message_id: Optional[str] = None,
                                 data: Optional[Any] = None,
                                 check: bool = True) -> httpx.Response:
        """DataTransfer Server→CP (§6.6, Sprint 31)."""
        body: dict[str, Any] = {"charger_id": charger_id, "vendor_id": vendor_id}
        if message_id is not None:
            body["message_id"] = message_id
        if data is not None:
            body["data"] = data
        r = await self.client.post("/api/commands/data-transfer/send", json=body)
        if check:
            r.raise_for_status()
        return r

    async def data_transfer_logs(self, charger_id: Optional[str] = None,
                                 direction: Optional[str] = None,
                                 limit: int = 100) -> dict:
        params: dict[str, Any] = {"limit": limit}
        if charger_id:
            params["charger_id"] = charger_id
        if direction:
            params["direction"] = direction
        r = await self.client.get("/api/commands/data-transfer/logs", params=params)
        r.raise_for_status()
        return r.json()

    async def unlock_connector(self, charger_id: str, connector_id: int = 1) -> dict:
        r = await self.client.post(
            "/api/commands/unlock",
            json={"charger_id": charger_id, "connector_id": connector_id},
        )
        r.raise_for_status()
        return r.json()

    async def active_transaction(self, charger_id: str) -> dict:
        r = await self.client.get(f"/api/commands/active-transaction/{charger_id}")
        r.raise_for_status()
        return r.json()

    # ── Whitelist (Sprint 29 A) ──────────────────────────────────────────
    async def list_tags(self) -> dict:
        r = await self.client.get("/api/tags")
        r.raise_for_status()
        return r.json()

    async def ensure_tag(self, id_tag: str, *, active: bool = True,
                         note: Optional[str] = None) -> dict:
        """Idempotent : crée le tag s'il n'existe pas, sinon garantit active=True.

        Nécessaire dès que la table `ocpp_tags` est non-vide (Sprint 29
        whitelist enforcement activée : tout id_tag absent → Invalid à
        l'Authorize). Les tests utilisent "LOCAL" par défaut → on le seed
        ici pour éviter les faux négatifs.
        """
        body: dict[str, Any] = {"id_tag": id_tag, "active": active}
        if note is not None:
            body["note"] = note
        r = await self.client.post("/api/tags", json=body)
        if r.status_code == 409:
            # Existe déjà — s'assurer qu'il est actif
            patch_body: dict[str, Any] = {"active": active}
            if note is not None:
                patch_body["note"] = note
            rp = await self.client.patch(f"/api/tags/{id_tag}", json=patch_body)
            rp.raise_for_status()
            return rp.json()
        r.raise_for_status()
        return r.json()
