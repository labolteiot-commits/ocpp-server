# api/routes/obd2.py
"""
Routes OBD2 et prédictif.

Endpoints :
  POST /api/obd2/reading          — reçoit une lecture de dongle OBD2
  GET  /api/obd2/vehicles         — liste les véhicules configurés
  POST /api/obd2/vehicles         — crée/met à jour un véhicule
  GET  /api/obd2/vehicles/{id}    — détails + dernière lecture + plan actif
  DELETE /api/obd2/vehicles/{id}  — supprime un véhicule
  GET  /api/obd2/plan/{charger_id}— plan de charge actif pour une borne
  POST /api/obd2/plan/{charger_id}/recalculate — force recalcul
  GET  /api/obd2/stats/{vehicle_id} — statistiques et patterns détectés
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func as sa_func
from pydantic import BaseModel

from db.database import get_db
from db.models import Vehicle, OBD2Reading, ChargingPlan
from core.logging import log

router = APIRouter()


# ── Schémas Pydantic ──────────────────────────────────────

class OBD2ReadingIn(BaseModel):
    """Payload envoyé par le dongle OBD2 / l'app de lecture."""
    vehicle_id:   str
    soc_pct:      Optional[float] = None
    speed_kmh:    Optional[float] = None
    odometer_km:  Optional[float] = None
    latitude:     Optional[float] = None
    longitude:    Optional[float] = None
    timestamp:    Optional[datetime] = None
    # Champs optionnels — enrichissement futur
    charger_id:   Optional[str]   = None  # si l'app connaît la borne
    raw:          Optional[dict]  = None  # payload complet si besoin


class OBD2ReadingOut(BaseModel):
    id:          int
    vehicle_id:  str
    timestamp:   datetime
    soc_pct:     Optional[float]
    speed_kmh:   Optional[float]
    odometer_km: Optional[float]
    is_moving:   bool

    class Config:
        from_attributes = True


class VehicleIn(BaseModel):
    id:               str
    label:            Optional[str]   = None
    charger_id:       Optional[str]   = None
    battery_kwh:      float           = 60.0
    max_charge_amps:  float           = 32.0
    target_soc_pct:   float           = 90.0
    charge_efficiency: float          = 0.92
    notes:            Optional[str]   = None


class VehicleOut(BaseModel):
    id:               str
    label:            Optional[str]
    charger_id:       Optional[str]
    battery_kwh:      float
    max_charge_amps:  float
    target_soc_pct:   float
    charge_efficiency: float
    notes:            Optional[str]
    created_at:       datetime

    class Config:
        from_attributes = True


class ChargingPlanOut(BaseModel):
    id:              int
    charger_id:      str
    vehicle_id:      Optional[str]
    created_at:      datetime
    planned_start:   datetime
    planned_stop:    datetime
    departure_time:  Optional[datetime]
    target_soc_pct:  float
    current_soc_pct: Optional[float]
    required_amps:   float
    status:          str
    applied_at:      Optional[datetime]
    notes:           Optional[str]

    class Config:
        from_attributes = True


# ── Helpers ───────────────────────────────────────────────

def _get_scheduler(request: Request):
    sched = getattr(request.app.state, "scheduler", None)
    if sched is None:
        raise HTTPException(status_code=503, detail="Scheduler non disponible")
    return sched


# ── Lectures OBD2 ─────────────────────────────────────────

@router.post("/reading", status_code=201)
async def receive_reading(
    body: OBD2ReadingIn,
    db:   AsyncSession = Depends(get_db),
):
    """Reçoit une lecture de dongle OBD2 et la persiste.

    Le dongle (ou l'app intermédiaire) envoie régulièrement les données du véhicule.
    Si le véhicule n'existe pas encore, on le crée automatiquement avec des valeurs par défaut.
    """
    # Vérifier / créer le véhicule à la volée
    vehicle = await db.get(Vehicle, body.vehicle_id)
    if vehicle is None:
        vehicle = Vehicle(
            id         = body.vehicle_id,
            label      = f"Véhicule {body.vehicle_id[:8]}",
            charger_id = body.charger_id,
        )
        db.add(vehicle)
        log.info("OBD2 — nouveau véhicule auto-créé", vehicle_id=body.vehicle_id)

    # Mettre à jour l'association borne si fournie
    if body.charger_id and vehicle.charger_id != body.charger_id:
        vehicle.charger_id = body.charger_id

    ts      = body.timestamp or datetime.now(timezone.utc)
    moving  = (body.speed_kmh or 0) > 3.0

    reading = OBD2Reading(
        vehicle_id  = body.vehicle_id,
        timestamp   = ts,
        soc_pct     = body.soc_pct,
        speed_kmh   = body.speed_kmh,
        odometer_km = body.odometer_km,
        latitude    = body.latitude,
        longitude   = body.longitude,
        is_moving   = moving,
        raw         = body.raw,
    )
    db.add(reading)
    await db.commit()
    await db.refresh(reading)

    log.debug("OBD2 lecture reçue",
              vehicle_id=body.vehicle_id,
              soc=body.soc_pct,
              speed=body.speed_kmh)

    return {
        "reading_id": reading.id,
        "vehicle_id": body.vehicle_id,
        "soc_pct":    body.soc_pct,
        "is_moving":  moving,
        "timestamp":  ts.isoformat(),
    }


@router.get("/readings/{vehicle_id}", response_model=list[OBD2ReadingOut])
async def get_readings(
    vehicle_id: str,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(OBD2Reading)
        .where(OBD2Reading.vehicle_id == vehicle_id)
        .order_by(OBD2Reading.timestamp.desc())
        .limit(limit)
    )
    return result.scalars().all()


# ── Véhicules ─────────────────────────────────────────────

@router.get("/vehicles", response_model=list[VehicleOut])
async def list_vehicles(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Vehicle).order_by(Vehicle.created_at.desc()))
    return result.scalars().all()


@router.post("/vehicles", response_model=VehicleOut, status_code=201)
async def create_or_update_vehicle(body: VehicleIn, db: AsyncSession = Depends(get_db)):
    """Crée ou met à jour un véhicule."""
    vehicle = await db.get(Vehicle, body.id)
    if vehicle is None:
        vehicle = Vehicle(id=body.id)
        db.add(vehicle)

    vehicle.label             = body.label
    vehicle.charger_id        = body.charger_id
    vehicle.battery_kwh       = body.battery_kwh
    vehicle.max_charge_amps   = body.max_charge_amps
    vehicle.target_soc_pct    = body.target_soc_pct
    vehicle.charge_efficiency = body.charge_efficiency
    vehicle.notes             = body.notes

    await db.commit()
    await db.refresh(vehicle)
    return vehicle


@router.get("/vehicles/{vehicle_id}")
async def get_vehicle(vehicle_id: str, db: AsyncSession = Depends(get_db)):
    """Retourne le véhicule avec sa dernière lecture OBD2 et son plan de charge actif."""
    vehicle = await db.get(Vehicle, vehicle_id)
    if not vehicle:
        raise HTTPException(status_code=404, detail="Véhicule introuvable")

    # Dernière lecture
    r = await db.execute(
        select(OBD2Reading)
        .where(OBD2Reading.vehicle_id == vehicle_id)
        .order_by(OBD2Reading.timestamp.desc())
        .limit(1)
    )
    reading = r.scalar_one_or_none()

    # Plan actif
    p = await db.execute(
        select(ChargingPlan)
        .where(
            ChargingPlan.vehicle_id == vehicle_id,
            ChargingPlan.status.in_(["pending", "active"]),
        )
        .order_by(ChargingPlan.created_at.desc())
        .limit(1)
    )
    plan = p.scalar_one_or_none()

    # Statistiques rapides
    count_r = await db.scalar(
        select(sa_func.count()).where(OBD2Reading.vehicle_id == vehicle_id)
    )
    since = datetime.now(timezone.utc) - timedelta(days=30)
    count_recent = await db.scalar(
        select(sa_func.count()).where(
            OBD2Reading.vehicle_id == vehicle_id,
            OBD2Reading.timestamp  >= since,
        )
    )

    return {
        "vehicle": {
            "id":               vehicle.id,
            "label":            vehicle.label,
            "charger_id":       vehicle.charger_id,
            "battery_kwh":      vehicle.battery_kwh,
            "max_charge_amps":  vehicle.max_charge_amps,
            "target_soc_pct":   vehicle.target_soc_pct,
            "charge_efficiency": vehicle.charge_efficiency,
        },
        "last_reading": {
            "soc_pct":     reading.soc_pct     if reading else None,
            "speed_kmh":   reading.speed_kmh   if reading else None,
            "odometer_km": reading.odometer_km if reading else None,
            "is_moving":   reading.is_moving   if reading else None,
            "timestamp":   reading.timestamp.isoformat() if reading else None,
            "age_seconds": (
                (datetime.now(timezone.utc) - reading.timestamp.replace(tzinfo=timezone.utc)).seconds
                if reading else None
            ),
        },
        "active_plan": {
            "id":            plan.id            if plan else None,
            "required_amps": plan.required_amps if plan else None,
            "target_soc":    plan.target_soc_pct if plan else None,
            "departure":     plan.departure_time.isoformat() if plan and plan.departure_time else None,
            "planned_stop":  plan.planned_stop.isoformat() if plan else None,
            "status":        plan.status        if plan else None,
            "notes":         plan.notes         if plan else None,
        },
        "stats": {
            "total_readings":  count_r,
            "readings_30d":    count_recent,
            "data_sufficient": (count_recent or 0) >= 3,
        },
    }


@router.delete("/vehicles/{vehicle_id}")
async def delete_vehicle(vehicle_id: str, db: AsyncSession = Depends(get_db)):
    vehicle = await db.get(Vehicle, vehicle_id)
    if not vehicle:
        raise HTTPException(status_code=404, detail="Véhicule introuvable")
    await db.delete(vehicle)
    await db.commit()
    return {"detail": f"Véhicule {vehicle_id} supprimé"}


# ── Plans de charge ───────────────────────────────────────

@router.get("/plan/{charger_id}", response_model=list[ChargingPlanOut])
async def get_plans(
    charger_id: str,
    limit: int = 10,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ChargingPlan)
        .where(ChargingPlan.charger_id == charger_id)
        .order_by(ChargingPlan.created_at.desc())
        .limit(limit)
    )
    return result.scalars().all()


@router.post("/plan/{charger_id}/recalculate")
async def recalculate_plan(charger_id: str, request: Request):
    """Force un recalcul immédiat du plan de charge pour cette borne."""
    scheduler = _get_scheduler(request)
    plan = await scheduler.force_recalculate(charger_id)
    if plan is None:
        return {"detail": "Aucun plan nécessaire (SOC cible atteint ou pas de véhicule associé)"}
    return {
        "detail": "Plan recalculé",
        "plan": {
            "id":            plan.id,
            "required_amps": plan.required_amps,
            "target_soc":    plan.target_soc_pct,
            "departure":     plan.departure_time.isoformat() if plan.departure_time else None,
            "notes":         plan.notes,
        },
    }


# ── Statistiques de patterns ──────────────────────────────

@router.get("/stats/{vehicle_id}")
async def get_vehicle_stats(vehicle_id: str, db: AsyncSession = Depends(get_db)):
    """Retourne les patterns de départ détectés et les statistiques OBD2."""
    vehicle = await db.get(Vehicle, vehicle_id)
    if not vehicle:
        raise HTTPException(status_code=404, detail="Véhicule introuvable")

    from core.predictor import detect_departures, predict_next_departure
    departures = await detect_departures(db, vehicle_id)

    # Grouper par jour de semaine
    day_names = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
    by_day: dict[str, list[str]] = {d: [] for d in day_names}
    for dep in departures:
        local = dep.astimezone()
        by_day[day_names[local.weekday()]].append(local.strftime("%H:%M"))

    next_departure = await predict_next_departure(db, vehicle_id)

    # SOC moyen au retour (10 derniers retours = is_moving False après mouvement)
    soc_readings = await db.execute(
        select(OBD2Reading.soc_pct)
        .where(
            OBD2Reading.vehicle_id == vehicle_id,
            OBD2Reading.is_moving  == False,
            OBD2Reading.soc_pct.is_not(None),
        )
        .order_by(OBD2Reading.timestamp.desc())
        .limit(20)
    )
    socs = [r[0] for r in soc_readings if r[0] is not None]
    avg_return_soc = round(sum(socs) / len(socs), 1) if socs else None

    return {
        "vehicle_id":      vehicle_id,
        "departures_total": len(departures),
        "departures_by_day": {
            day: {
                "count": len(times),
                "times": sorted(set(times)),
            }
            for day, times in by_day.items()
        },
        "next_predicted_departure": next_departure.isoformat() if next_departure else None,
        "avg_return_soc_pct": avg_return_soc,
        "data_sufficient": len(departures) >= 3,
    }
