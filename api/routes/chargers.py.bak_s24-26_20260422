# api/routes/chargers.py
import asyncio
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func as sa_func
from sqlalchemy.orm import selectinload
from pydantic import BaseModel
from typing import Optional
from datetime import datetime

from db.database import get_db
from db.models import Charger, Session as ChargingSession, SessionStatus, MeterValue
from core.logging import log

router = APIRouter()


# ─── Schémas Pydantic ────────────────────────────────────

class ConnectorOut(BaseModel):
    connector_id: int
    status:       str
    error_code:   Optional[str]
    updated_at:   Optional[datetime]

    class Config:
        from_attributes = True


class ChargerOut(BaseModel):
    id:                 str
    description:        Optional[str]
    manufacturer:       Optional[str]
    model:              Optional[str]
    firmware_version:   Optional[str]
    ocpp_protocol:      str
    status:             str
    last_heartbeat:     Optional[datetime]
    ip_address:         Optional[str]
    is_enabled:         bool
    boot_lock:          bool
    default_max_amps:   Optional[float]
    remote_start_delay: Optional[float]
    local_id_tag:       Optional[str]
    created_at:         datetime
    connectors:         list[ConnectorOut] = []

    class Config:
        from_attributes = True


class ChargerCreate(BaseModel):
    id:                 str
    description:        Optional[str]   = None
    manufacturer:       Optional[str]   = None
    model:              Optional[str]   = None
    auth_password:      Optional[str]   = None
    notes:              Optional[str]   = None
    boot_lock:          bool            = True
    default_max_amps:   Optional[float] = None
    remote_start_delay: Optional[float] = None  # None = détection auto par fabricant
    local_id_tag:       Optional[str]   = None  # None = "ADMIN"


class ChargerUpdate(BaseModel):
    description:        Optional[str]   = None
    is_enabled:         Optional[bool]  = None
    boot_lock:          Optional[bool]  = None
    default_max_amps:   Optional[float] = None
    remote_start_delay: Optional[float] = None
    local_id_tag:       Optional[str]   = None


# ─── Endpoints ───────────────────────────────────────────

@router.get("/", response_model=list[ChargerOut])
async def list_chargers(db: AsyncSession = Depends(get_db)):
    """Liste toutes les bornes enregistrées."""
    result = await db.execute(
        select(Charger)
        .options(selectinload(Charger.connectors))
        .order_by(Charger.created_at.desc())
    )
    return result.scalars().all()


@router.post("/", response_model=ChargerOut, status_code=201)
async def create_charger(data: ChargerCreate, db: AsyncSession = Depends(get_db)):
    """Pré-enregistrer une borne avant sa première connexion."""
    existing = await db.get(Charger, data.id)
    if existing:
        raise HTTPException(status_code=409, detail="Une borne avec cet ID existe déjà")

    charger = Charger(
        id=data.id,
        description=data.description,
        manufacturer=data.manufacturer,
        model=data.model,
        auth_password=data.auth_password,
        notes=data.notes,
        boot_lock=data.boot_lock,
        default_max_amps=data.default_max_amps,
        remote_start_delay=data.remote_start_delay,
        local_id_tag=data.local_id_tag,
        status="Offline",
    )
    db.add(charger)
    await db.commit()

    # Recharger avec les connectors (db.refresh ne charge pas les relations)
    result = await db.execute(
        select(Charger)
        .options(selectinload(Charger.connectors))
        .where(Charger.id == charger.id)
    )
    return result.scalar_one()


@router.get("/{charger_id}", response_model=ChargerOut)
async def get_charger(charger_id: str, db: AsyncSession = Depends(get_db)):
    """Détails d'une borne."""
    result = await db.execute(
        select(Charger)
        .options(selectinload(Charger.connectors))
        .where(Charger.id == charger_id)
    )
    charger = result.scalar_one_or_none()
    if not charger:
        raise HTTPException(status_code=404, detail="Borne introuvable")
    return charger


@router.patch("/{charger_id}", response_model=ChargerOut)
async def update_charger(
    charger_id: str,
    data: ChargerUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Mettre à jour les paramètres d'une borne."""
    charger = await db.get(Charger, charger_id)
    if not charger:
        raise HTTPException(status_code=404, detail="Borne introuvable")

    if data.description is not None:
        charger.description = data.description
    if data.is_enabled is not None:
        charger.is_enabled = data.is_enabled
    if data.boot_lock is not None:
        charger.boot_lock = data.boot_lock
    if data.default_max_amps is not None:
        charger.default_max_amps = data.default_max_amps
    if data.remote_start_delay is not None:
        charger.remote_start_delay = data.remote_start_delay
    if data.local_id_tag is not None:
        charger.local_id_tag = data.local_id_tag

    await db.commit()

    # ── Push live vers la borne si connectée ─────────────
    if data.default_max_amps is not None:
        cp = request.app.state.ocpp_server.get_charger(charger_id)
        if cp:
            cp._default_max_amps = data.default_max_amps

            if cp._is_technove:
                # TechnoVE : TxDefaultProfile sur connector 1, stack_level=0
                # Ne pas envoyer ChargePointMaxProfile sur connector 0 (rejeté hors transaction)
                profile = {
                    "charging_profile_id":      99,
                    "stack_level":              0,
                    "charging_profile_purpose": "TxDefaultProfile",
                    "charging_profile_kind":    "Absolute",
                    "charging_schedule": {
                        "charging_rate_unit": "A",
                        "charging_schedule_period": [
                            {"start_period": 0, "limit": data.default_max_amps}
                        ],
                    },
                }
                asyncio.create_task(cp.set_charging_profile(1, profile))
                log.info("TechnoVE — TxDefaultProfile mis à jour live",
                         id=charger_id, amps=data.default_max_amps)
            else:
                # Comportement générique : ChargePointMaxProfile sur connector 0
                profile = {
                    "charging_profile_id":      99,
                    "stack_level":              8,
                    "charging_profile_purpose": "ChargePointMaxProfile",
                    "charging_profile_kind":    "Absolute",
                    "charging_schedule": {
                        "charging_rate_unit": "A",
                        "charging_schedule_period": [
                            {"start_period": 0, "limit": data.default_max_amps}
                        ],
                    },
                }
                asyncio.create_task(cp.set_charging_profile(0, profile))

    # Mettre à jour les valeurs runtime si la borne est connectée
    if data.remote_start_delay is not None:
        cp = request.app.state.ocpp_server.get_charger(charger_id)
        if cp:
            cp._remote_start_delay = data.remote_start_delay

    if data.local_id_tag is not None:
        cp = request.app.state.ocpp_server.get_charger(charger_id)
        if cp:
            cp._local_id_tag = data.local_id_tag

    # Recharger avec les connectors (db.refresh ne charge pas les relations)
    result = await db.execute(
        select(Charger)
        .options(selectinload(Charger.connectors))
        .where(Charger.id == charger_id)
    )
    return result.scalar_one()


@router.delete("/{charger_id}")
async def delete_charger(charger_id: str, db: AsyncSession = Depends(get_db)):
    """Supprimer une borne de la DB."""
    charger = await db.get(Charger, charger_id)
    if not charger:
        raise HTTPException(status_code=404, detail="Borne introuvable")
    await db.delete(charger)
    await db.commit()
    return {"detail": f"Borne {charger_id} supprimée"}


@router.get("/{charger_id}/stats")
async def get_charger_stats(charger_id: str, db: AsyncSession = Depends(get_db)):
    """Statistiques en temps réel pour une borne."""
    result = await db.execute(
        select(ChargingSession)
        .where(ChargingSession.charger_id == charger_id)
        .where(ChargingSession.status == SessionStatus.ACTIVE)
        .order_by(ChargingSession.start_time.desc())
        .limit(1)
    )
    active = result.scalar_one_or_none()

    total_energy = await db.scalar(
        select(sa_func.sum(ChargingSession.energy_wh))
        .where(ChargingSession.charger_id == charger_id)
        .where(ChargingSession.status == SessionStatus.COMPLETED)
    ) or 0

    last_meter = None
    if active:
        result2 = await db.execute(
            select(MeterValue)
            .where(MeterValue.session_id == active.id)
            .order_by(MeterValue.timestamp.desc())
            .limit(1)
        )
        last_meter = result2.scalar_one_or_none()

    return {
        "charger_id":            charger_id,
        "active_transaction_id": active.transaction_id if active else None,
        "active_since":          active.start_time.isoformat() if active else None,
        "current_power_w":       last_meter.power_w if last_meter else None,
        "current_energy_wh":     last_meter.energy_wh if last_meter else None,
        "session_energy_wh": (
            (last_meter.energy_wh - active.meter_start * 1000)
            if last_meter and active and last_meter.energy_wh is not None else None
        ),
        "total_delivered_kwh":   round(total_energy / 1000, 2),
    }
