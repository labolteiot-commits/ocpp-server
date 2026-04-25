# api/main.py
"""
Factory de l'application FastAPI.
Enregistre tous les routers et configure l'état global (ocpp_server, scheduler).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import settings
from core.logging import log

# Routes
from api.routes.chargers  import router as chargers_router
from api.routes.commands  import router as commands_router
from api.routes.sessions  import router as sessions_router
from api.routes.dashboard import router as dashboard_router
from api.websocket        import router as websocket_router   # api/websocket.py, pas api/routes/
from api.routes.obd2      import router as obd2_router

if TYPE_CHECKING:
    from core.ocpp_server import OCPPServer
    from core.scheduler   import ChargingScheduler


def create_app(
    ocpp_server: "OCPPServer",
    scheduler:   "ChargingScheduler | None" = None,
) -> FastAPI:

    app = FastAPI(
        title       = settings.app_name,
        version     = "1.0.0",
        description = "Serveur OCPP résidentiel avec prédictif OBD2",
    )

    # ── CORS ────────────────────────────────────────────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins     = settings.api.allowed_origins,
        allow_credentials = True,
        allow_methods     = ["*"],
        allow_headers     = ["*"],
    )

    # ── État global ─────────────────────────────────────────
    app.state.ocpp_server = ocpp_server
    app.state.scheduler   = scheduler

    # ── Routers ─────────────────────────────────────────────
    app.include_router(dashboard_router,  prefix="")
    app.include_router(websocket_router,  prefix="")
    app.include_router(chargers_router,   prefix="/api/chargers",  tags=["Bornes"])
    app.include_router(commands_router,   prefix="/api/commands",  tags=["Commandes OCPP"])
    app.include_router(sessions_router,   prefix="/api/sessions",  tags=["Sessions"])
    app.include_router(obd2_router,       prefix="/api/obd2",      tags=["OBD2 & Prédictif"])

    @app.on_event("startup")
    async def on_startup():
        log.info("API démarrée", port=settings.api.port)

    @app.on_event("shutdown")
    async def on_shutdown():
        log.info("API arrêtée")

    return app
