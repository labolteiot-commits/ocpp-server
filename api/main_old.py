# api/main.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pathlib import Path

from config import settings
from api.routes import chargers, sessions, commands
from api import websocket as ws_router

BASE_DIR = Path(__file__).parent.parent


def create_app(ocpp_server) -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version="1.0.0",
        docs_url="/api/docs",
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.api.allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Stocker la référence au serveur OCPP dans l'app
    app.state.ocpp_server = ocpp_server

    # Routes API
    app.include_router(chargers.router, prefix="/api/chargers", tags=["Bornes"])
    app.include_router(sessions.router, prefix="/api/sessions", tags=["Sessions"])
    app.include_router(commands.router, prefix="/api/commands", tags=["Commandes"])

    # WebSocket dashboard
    app.include_router(ws_router.router)

    # Fichiers statiques & templates
    app.mount(
        "/static",
        StaticFiles(directory=BASE_DIR / "dashboard" / "static"),
        name="static",
    )

    # Dashboard (routes HTML)
    from api.routes import dashboard as dash_router
    app.include_router(dash_router.router)

    return app
