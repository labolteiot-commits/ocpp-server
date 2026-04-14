# api/routes/dashboard.py
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from pathlib import Path

from db.database import get_db
from db.models import Charger, Session as ChargingSession, SessionStatus

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent.parent.parent / "dashboard" / "templates")


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    # Bornes — selectinload OBLIGATOIRE en async pour éviter le lazy-load
    # synchrone sur charger.connectors dans Jinja2 (MissingGreenlet / DetachedInstanceError)
    result = await db.execute(
        select(Charger)
        .options(selectinload(Charger.connectors))
        .order_by(Charger.created_at.desc())
    )
    chargers = result.scalars().all()

    # Stats globales
    total_sessions = await db.scalar(select(func.count(ChargingSession.id)))
    active_sessions = await db.scalar(
        select(func.count(ChargingSession.id))
        .where(ChargingSession.status == SessionStatus.ACTIVE)
    )
    total_energy = await db.scalar(
        select(func.sum(ChargingSession.energy_wh))
        .where(ChargingSession.status == SessionStatus.COMPLETED)
    ) or 0

    return templates.TemplateResponse("dashboard.html", {
        "request":            request,
        "chargers":           chargers,
        "stats": {
            "total_chargers":   len(chargers),
            "total_sessions":   total_sessions or 0,
            "active_sessions":  active_sessions or 0,
            "total_energy_kwh": round((total_energy or 0) / 1000, 2),
        },
        "connected_chargers": request.app.state.ocpp_server.connected_chargers,
    })
