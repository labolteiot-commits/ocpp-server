# api/routes/dashboard.py
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from pathlib import Path
from datetime import datetime, timezone
import zoneinfo

from db.database import get_db
from db.models import Charger, Session as ChargingSession, SessionStatus
from config import settings

router = APIRouter()
templates = Jinja2Templates(
    directory=Path(__file__).parent.parent.parent / "dashboard" / "templates"
)

def to_local(dt: datetime | None) -> datetime | None:
    """Convertit un datetime UTC en heure locale."""
    if dt is None:
        return None
    tz = zoneinfo.ZoneInfo(settings.timezone)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(tz)


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, db: AsyncSession = Depends(get_db)):

    result = await db.execute(
        select(Charger).options(selectinload(Charger.connectors))
    )
    chargers = result.scalars().all()

    # Convertir les timestamps en heure locale
    for c in chargers:
        c.last_heartbeat = to_local(c.last_heartbeat)

    total_sessions  = await db.scalar(select(func.count(ChargingSession.id))) or 0
    active_sessions = await db.scalar(
        select(func.count(ChargingSession.id))
        .where(ChargingSession.status == SessionStatus.ACTIVE)
    ) or 0
    total_energy = await db.scalar(
        select(func.sum(ChargingSession.energy_wh))
        .where(ChargingSession.status == SessionStatus.COMPLETED)
    ) or 0

    return templates.TemplateResponse("dashboard.html", {
        "request":            request,
        "chargers":           chargers,
        "stats": {
            "total_chargers":   len(chargers),
            "total_sessions":   total_sessions,
            "active_sessions":  active_sessions,
            "total_energy_kwh": round(total_energy / 1000, 2),
        },
        "connected_chargers": request.app.state.ocpp_server.connected_chargers,
    })
