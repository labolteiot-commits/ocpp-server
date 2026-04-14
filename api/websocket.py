# api/websocket.py
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()


@router.websocket("/ws/dashboard")
async def dashboard_ws(websocket: WebSocket):
    ocpp_server = websocket.app.state.ocpp_server
    await websocket.accept()
    ocpp_server.register_dashboard_client(websocket)

    try:
        # Envoyer l'état initial
        await websocket.send_json({
            "type":     "connected_chargers",
            "chargers": ocpp_server.connected_chargers,
        })
        # Garder la connexion vivante
        while True:
            try:
                await websocket.receive_text()
            except Exception:
                break
    except WebSocketDisconnect:
        pass
    except Exception as e:
        pass
    finally:
        ocpp_server.unregister_dashboard_client(websocket)
