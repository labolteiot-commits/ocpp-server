# api/routes/sessions.py
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from pydantic import BaseModel
from typing import Optional
from datetime import datetime

from db.database import get_db
from db.models import Session as ChargingSession, MeterValue

router = APIRouter()


class MeterValueOut(BaseModel):
    timestamp: datetime
    energy_wh: Optional[float]
    power_w: Optional[float]
    current_a: Optional[float]
    voltage_v: Optional[float]
    soc_percent: Optional[float]

    class Config:
        from_attributes = True


class SessionOut(BaseModel):
    id: int
    transaction_id: int
    charger_id: str
    id_tag: str
    status: str
    start_time: datetime
    stop_time: Optional[datetime]
    energy_wh: float
    meter_start: float
    meter_stop: Optional[float]
    stop_reason: Optional[str]

    class Config:
        from_attributes = True


@router.get("/", response_model=list[SessionOut])
async def list_sessions(
    charger_id: Optional[str] = Query(None),
    limit: int = Query(50, le=200),
    db: AsyncSession = Depends(get_db),
):
    query = select(ChargingSession).order_by(desc(ChargingSession.start_time)).limit(limit)
    if charger_id:
        query = query.where(ChargingSession.charger_id == charger_id)

    result = await db.execute(query)
    return result.scalars().all()


@router.get("/{session_id}/meter-values", response_model=list[MeterValueOut])
async def get_meter_values(session_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(MeterValue)
        .where(MeterValue.session_id == session_id)
        .order_by(MeterValue.timestamp)
    )
    return result.scalars().all()
