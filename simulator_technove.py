# simulator_technove.py — Simule une TechnoVE S48
import asyncio
import websockets
from ocpp.v16 import ChargePoint as CP, call
from ocpp.v16.enums import ChargePointStatus, ChargePointErrorCode

class TechnoVES48(CP):
    """Simule le comportement d'une TechnoVE S48 OCPP 1.6"""

    async def run(self):
        # 1. Boot
        await self.call(call.BootNotification(
            charge_point_vendor="TechnoVE",
            charge_point_model="S48",
            charge_point_serial_number="TVE-2024-00123",
            firmware_version="2.1.4",
        ))

        # 2. Status initial
        await self.call(call.StatusNotification(
            connector_id=0,
            error_code=ChargePointErrorCode.no_error,
            status=ChargePointStatus.available,
        ))
        await self.call(call.StatusNotification(
            connector_id=1,
            error_code=ChargePointErrorCode.no_error,
            status=ChargePointStatus.available,
        ))

        print("✅ TechnoVE S48 simulée connectée et disponible")

        # 3. Heartbeat en boucle
        while True:
            await asyncio.sleep(30)
            await self.call(call.Heartbeat())
            print("💓 Heartbeat envoyé")

async def main():
    ip = input("IP du Pi (défaut: localhost): ").strip() or "localhost"
    charger_id = input("ID borne (défaut: TECHNOVE-S48-001): ").strip() or "TECHNOVE-S48-001"

    url = f"ws://{ip}:9000/ocpp/{charger_id}"
    print(f"Connexion à {url}...")

    async with websockets.connect(url, subprotocols=["ocpp1.6"]) as ws:
        cp = TechnoVES48(charger_id, ws)
        await asyncio.gather(cp.start(), cp.run())

asyncio.run(main())
