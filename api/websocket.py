# api/websocket.py
import base64
import os
import secrets

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from core.logging import log

router = APIRouter()

_USER = os.getenv("OCPP_DASH_USER", "")
_PASS = os.getenv("OCPP_DASH_PASS", "")


def _ws_auth_ok(websocket: WebSocket) -> bool:
    """Valide HTTP Basic auth sur le WebSocket dashboard.
    Mode ouvert si OCPP_DASH_USER/PASS non configurés (rétrocompat).
    """
    if not _USER or not _PASS:
        return True
    auth = websocket.headers.get("authorization", "")
    if not auth.lower().startswith("basic "):
        return False
    try:
        decoded = base64.b64decode(auth[6:]).decode()
        user, _, pwd = decoded.partition(":")
    except Exception:
        return False
    return secrets.compare_digest(user, _USER) and secrets.compare_digest(pwd, _PASS)


@router.websocket("/ws/dashboard")
async def dashboard_ws(websocket: WebSocket):
    ocpp_server = websocket.app.state.ocpp_server
    await websocket.accept()

    if not _ws_auth_ok(websocket):
        log.warning("dashboard WS — authentification échouée, connexion refusée")
        await websocket.close(code=4401, reason="Unauthorized")
        return

    ocpp_server.register_dashboard_client(websocket)
    try:
        await websocket.send_json({
            "type":     "connected_chargers",
            "chargers": ocpp_server.connected_chargers,
        })
        while True:
            try:
                await websocket.receive_text()
            except WebSocketDisconnect:
                break
            except Exception as exc:
                log.debug("dashboard WS receive error", error=str(exc))
                break
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        log.warning("dashboard WS error", error=str(exc))
    finally:
        ocpp_server.unregister_dashboard_client(websocket)
