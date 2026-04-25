# main.py
import asyncio
import uvicorn
import os
import sys
import signal
import time
from pathlib import Path

from core.logging import setup_logging, log
from config import settings
from db.database import init_db
from core.ocpp_server import OCPPServer
from api.main import create_app

PID_FILE = Path("/tmp/ocpp-server.pid")


def check_pid_file():
    """Vérifie qu'une seule instance tourne."""
    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text().strip())
        except ValueError:
            PID_FILE.unlink(missing_ok=True)
            pid = None

        if pid:
            try:
                os.kill(pid, 0)  # signal 0 = juste vérifier l'existence
                print(f"❌ Une instance tourne déjà (PID {pid}). Arrêt.")
                print(f"   → Pour forcer l'arrêt : python main.py --stop")
                sys.exit(1)
            except ProcessLookupError:
                # PID file stale — processus mort, on peut continuer
                PID_FILE.unlink(missing_ok=True)

    PID_FILE.write_text(str(os.getpid()))


def stop_existing():
    """Arrête l'instance en cours et attend sa fermeture complète."""
    if not PID_FILE.exists():
        print("Aucune instance en cours.")
        return

    try:
        pid = int(PID_FILE.read_text().strip())
    except (ValueError, FileNotFoundError):
        PID_FILE.unlink(missing_ok=True)
        print("PID file invalide, nettoyé.")
        return

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        PID_FILE.unlink(missing_ok=True)
        print(f"Le processus {pid} n'existe plus. PID file nettoyé.")
        return

    print(f"Arrêt du processus {pid}...")
    os.kill(pid, signal.SIGTERM)

    # Attendre jusqu'à 10s que le processus se termine
    for i in range(10):
        time.sleep(1)
        try:
            os.kill(pid, 0)
            print(f"  En attente... ({i+1}s)")
        except ProcessLookupError:
            PID_FILE.unlink(missing_ok=True)
            print(f"✅ Processus {pid} arrêté proprement.")
            return

    # Toujours vivant après 10s → SIGKILL
    print(f"⚠️  Toujours actif après 10s — SIGKILL...")
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    PID_FILE.unlink(missing_ok=True)
    print("✅ Arrêt forcé.")


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
    app = create_app(ocpp_server)

    config = uvicorn.Config(
        app=app,
        host=settings.api.host,
        port=settings.api.port,
        log_level="warning",  # on utilise structlog
    )
    api_server = uvicorn.Server(config)

    try:
        await asyncio.gather(
            ocpp_server.start(),
            api_server.serve(),
        )
    finally:
        # Appelé automatiquement sur Ctrl+C, kill, ou crash Python
        remove_pid_file()


if __name__ == "__main__":
    if "--stop" in sys.argv:
        stop_existing()
        sys.exit(0)

    if "--restart" in sys.argv:
        stop_existing()
        time.sleep(1)
        # Continuer vers le démarrage normal

    check_pid_file()
    asyncio.run(main())
