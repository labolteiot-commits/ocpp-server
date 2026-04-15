# main.py
import asyncio
import uvicorn
import os
import sys
from pathlib import Path

from core.logging import setup_logging, log
from config import settings
from db.database import init_db
from core.ocpp_server import OCPPServer
from core.scheduler import ChargingScheduler
from api.main import create_app

PID_FILE = Path("/tmp/ocpp-server.pid")


def check_pid_file():
    """Vérifie qu'une seule instance tourne."""
    if PID_FILE.exists():
        pid = int(PID_FILE.read_text().strip())
        try:
            os.kill(pid, 0)  # signal 0 = juste vérifier l'existence
            print(f"❌ Une instance tourne déjà (PID {pid}). Arrêt.")
            sys.exit(1)
        except ProcessLookupError:
            # PID file stale — processus mort, on peut continuer
            PID_FILE.unlink()

    PID_FILE.write_text(str(os.getpid()))


def remove_pid_file():
    PID_FILE.unlink(missing_ok=True)


async def main():
    setup_logging()
    log.info(
        "Démarrage",
        app=settings.app_name,
        env=settings.environment,
        ocpp_port=settings.ocpp.port,
        api_port=settings.api.port,
    )

    await init_db()

    ocpp_server = OCPPServer()
    scheduler   = ChargingScheduler(ocpp_server)
    app = create_app(ocpp_server, scheduler)

    config = uvicorn.Config(
        app=app,
        host=settings.api.host,
        port=settings.api.port,
        log_level="warning",  # on utilise structlog
    )
    api_server = uvicorn.Server(config)

    try:
        scheduler.start()
        await asyncio.gather(
            ocpp_server.start(),
            api_server.serve(),
        )
    finally:
        scheduler.stop()
        remove_pid_file()


if __name__ == "__main__":
    check_pid_file()  # Vérifier avant de démarrer quoi que ce soit
    asyncio.run(main())
