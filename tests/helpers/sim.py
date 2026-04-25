"""
Wrapper httpx pour la borne virtuelle (port 8001).

Endpoints exposés par virtual_charger.py (sprint 27) :
  GET  /api/state          → snapshot complet
  POST /api/plug           → {plugged, soc_start, soc_target, battery_kwh}
  POST /api/connect        → {charger_id, server_url}
  POST /api/speed          → {speed} (1..600)
  POST /api/fault          → {type, error_code?}
  POST /api/phase_mode     → {mode}
  POST /api/ambient        → {temp_c}
  DELETE /api/messages
"""
from __future__ import annotations

from typing import Any, Optional

import httpx


class VirtualChargerAPI:
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

    async def get_state(self) -> dict[str, Any]:
        r = await self.client.get("/api/state")
        r.raise_for_status()
        return r.json()

    async def plug(self, plugged: bool = True, soc_start: float = 20.0,
                   soc_target: float = 100.0, battery_kwh: float = 60.0) -> dict:
        r = await self.client.post("/api/plug", json={
            "plugged": plugged, "soc_start": soc_start,
            "soc_target": soc_target, "battery_kwh": battery_kwh,
        })
        r.raise_for_status()
        return r.json()

    async def unplug(self) -> dict:
        return await self.plug(False)

    async def set_speed(self, speed: int) -> dict:
        r = await self.client.post("/api/speed", json={"speed": speed})
        r.raise_for_status()
        return r.json()

    async def inject_fault(self, fault_type: str,
                           error_code: Optional[str] = None) -> dict:
        body: dict[str, Any] = {"type": fault_type}
        if error_code:
            body["error_code"] = error_code
        r = await self.client.post("/api/fault", json=body)
        r.raise_for_status()
        return r.json()

    async def set_phase_mode(self, mode: str) -> dict:
        """mode = 'residential_l2' | 'commercial_l2_3ph'"""
        r = await self.client.post("/api/phase_mode", json={"mode": mode})
        r.raise_for_status()
        return r.json()

    async def set_ambient(self, temp_c: float) -> dict:
        r = await self.client.post("/api/ambient", json={"temp_c": temp_c})
        r.raise_for_status()
        return r.json()
