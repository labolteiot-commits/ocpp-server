# core/predictor.py
"""
Moteur prédictif de charge.

Logique :
1. Analyser les lectures OBD2 historiques pour détecter les patterns de trajet
   (heure de départ typique par jour de semaine, énergie consommée par trajet).
2. Calculer le plan de charge optimal :
   - SOC cible = vehicle.target_soc_pct
   - Énergie requise = (target_soc - current_soc) * capacity / efficiency
   - Temps disponible = prochain départ prévu - maintenant
   - Courant requis = énergie / (temps * tension * phases)
3. Retourner un ChargingPlan que le scheduler appliquera via OCPP.

Pas de ML complexe : médiane glissante par jour de semaine sur les 30 derniers jours.
Simple, robuste, explicable.
"""
from __future__ import annotations

import statistics
from datetime import datetime, timedelta, timezone, time as dtime
from typing import Optional, TYPE_CHECKING

from sqlalchemy import select, func as sa_func
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import OBD2Reading, Vehicle, ChargingPlan
from core.logging import log

if TYPE_CHECKING:
    pass

# Tension nominale réseau (V) et nombre de phases pour calcul puissance
VOLTAGE_V      = 240.0
PHASES         = 1        # borne résidentielle monophasée
MIN_AMPS       = 6.0      # minimum OCPP / NEC
HISTORY_DAYS   = 30       # fenêtre d'analyse historique
MIN_SAMPLES    = 3        # minimum d'observations pour prédire


async def detect_departures(db: AsyncSession, vehicle_id: str) -> list[datetime]:
    """Détecte les événements de départ dans l'historique OBD2.

    Un départ = transition de is_moving=False vers is_moving=True
    après une période d'immobilité >= 30 min.
    """
    since = datetime.now(timezone.utc) - timedelta(days=HISTORY_DAYS)
    result = await db.execute(
        select(OBD2Reading)
        .where(
            OBD2Reading.vehicle_id == vehicle_id,
            OBD2Reading.timestamp  >= since,
        )
        .order_by(OBD2Reading.timestamp)
    )
    readings = result.scalars().all()

    departures: list[datetime] = []
    prev_moving   = False
    prev_stop_ts  = None

    for r in readings:
        moving = r.is_moving or False
        ts = r.timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)

        if not moving and not prev_moving:
            prev_stop_ts = ts

        # Départ détecté : était immobile >= 30 min, maintenant en mouvement
        if moving and not prev_moving:
            if prev_stop_ts and (ts - prev_stop_ts) >= timedelta(minutes=30):
                departures.append(ts)

        prev_moving = moving

    return departures


async def predict_next_departure(
    db: AsyncSession, vehicle_id: str, now: Optional[datetime] = None
) -> Optional[datetime]:
    """Prédit la prochaine heure de départ en minutes depuis minuit.

    Stratégie : médiane des heures de départ historiques pour le jour de semaine
    actuel (et le lendemain si on est passé l'heure médiane d'aujourd'hui).
    """
    if now is None:
        now = datetime.now(timezone.utc)

    departures = await detect_departures(db, vehicle_id)
    if len(departures) < MIN_SAMPLES:
        log.debug("Prédiction départ — pas assez de données",
                  vehicle_id=vehicle_id, samples=len(departures))
        return None

    # Grouper par jour de semaine (0=lundi, 6=dimanche)
    by_weekday: dict[int, list[float]] = {i: [] for i in range(7)}
    for dep in departures:
        local = dep.astimezone()
        minutes = local.hour * 60 + local.minute
        by_weekday[local.weekday()].append(minutes)

    def _median_minutes(weekday: int) -> Optional[float]:
        data = by_weekday.get(weekday, [])
        return statistics.median(data) if len(data) >= MIN_SAMPLES else None

    now_local   = now.astimezone()
    now_minutes = now_local.hour * 60 + now_local.minute

    # Essayer aujourd'hui, puis demain, puis après-demain…
    for offset in range(7):
        target_date    = (now_local + timedelta(days=offset)).date()
        target_weekday = target_date.weekday()
        median_min     = _median_minutes(target_weekday)

        if median_min is None:
            continue

        h, m    = divmod(int(median_min), 60)
        dep_dt  = datetime.combine(
            target_date,
            dtime(h, m, 0),
            tzinfo=now_local.tzinfo,
        ).astimezone(timezone.utc)

        # Départ dans le futur avec au moins 30 min de marge
        if dep_dt > now + timedelta(minutes=30):
            log.debug("Départ prédit",
                      vehicle_id=vehicle_id, departure=dep_dt.isoformat(),
                      day=target_date.strftime("%A"), median_min=int(median_min))
            return dep_dt

    return None


def calculate_required_amps(
    current_soc_pct:  float,
    target_soc_pct:   float,
    battery_kwh:      float,
    available_hours:  float,
    max_amps:         float,
    efficiency:       float = 0.92,
) -> float:
    """Calcule le courant en Ampères requis pour atteindre target_soc avant available_hours.

    Formule : I = (ΔE / η) / (V × phases × t)
    """
    delta_soc    = max(0.0, target_soc_pct - current_soc_pct) / 100.0
    energy_kwh   = delta_soc * battery_kwh / efficiency       # énergie à fournir au réseau
    power_kw     = energy_kwh / max(available_hours, 0.1)      # puissance nécessaire
    amps         = (power_kw * 1000.0) / (VOLTAGE_V * PHASES)  # courant (A)

    # Borner au min/max
    if amps < 1.0:
        return 0.0   # déjà chargé ou presque → ne pas démarrer
    return min(max(amps, MIN_AMPS), max_amps)


async def build_charging_plan(
    db: AsyncSession,
    vehicle: Vehicle,
    current_soc_pct: float,
    now: Optional[datetime] = None,
) -> Optional[ChargingPlan]:
    """Construit un plan de charge pour ce véhicule.

    Retourne None si aucune charge n'est nécessaire ou si impossible à calculer.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    target_soc = vehicle.target_soc_pct or 90.0

    # Pas besoin de charger
    if current_soc_pct >= target_soc - 1.0:
        log.info("Plan charge — SOC cible atteint, pas de charge nécessaire",
                 vehicle_id=vehicle.id, soc=current_soc_pct, target=target_soc)
        return None

    departure = await predict_next_departure(db, vehicle.id, now)

    if departure is None:
        # Pas assez de données : charger à puissance maximale immédiatement
        log.info("Plan charge — pas de prédiction, charge max immédiate", vehicle_id=vehicle.id)
        amps   = min(vehicle.max_charge_amps or 32.0, 32.0)
        stop   = now + timedelta(hours=12)  # horizon large
        return ChargingPlan(
            charger_id      = vehicle.charger_id,
            vehicle_id      = vehicle.id,
            planned_start   = now,
            planned_stop    = stop,
            departure_time  = None,
            target_soc_pct  = target_soc,
            current_soc_pct = current_soc_pct,
            required_amps   = amps,
            status          = "pending",
            notes           = "Charge max (pas assez de données historiques)",
        )

    available_hours = (departure - now).total_seconds() / 3600.0
    amps = calculate_required_amps(
        current_soc_pct  = current_soc_pct,
        target_soc_pct   = target_soc,
        battery_kwh      = vehicle.battery_kwh or 60.0,
        available_hours  = available_hours,
        max_amps         = vehicle.max_charge_amps or 32.0,
        efficiency       = vehicle.charge_efficiency or 0.92,
    )

    if amps == 0.0:
        log.info("Plan charge — SOC presque atteint", vehicle_id=vehicle.id, soc=current_soc_pct)
        return None

    # Heure de fin = départ (on veut être prêt pour le départ)
    charge_hours = (
        (target_soc - current_soc_pct) / 100.0
        * (vehicle.battery_kwh or 60.0)
        / (vehicle.charge_efficiency or 0.92)
        / ((amps * VOLTAGE_V * PHASES) / 1000.0)
    )
    planned_stop = min(now + timedelta(hours=charge_hours + 0.5), departure)

    plan = ChargingPlan(
        charger_id      = vehicle.charger_id,
        vehicle_id      = vehicle.id,
        planned_start   = now,
        planned_stop    = planned_stop,
        departure_time  = departure,
        target_soc_pct  = target_soc,
        current_soc_pct = current_soc_pct,
        required_amps   = round(amps, 1),
        status          = "pending",
        notes           = (
            f"Départ prévu {departure.strftime('%H:%M')} — "
            f"{available_hours:.1f}h dispo — "
            f"besoin {charge_hours:.1f}h à {amps:.1f}A"
        ),
    )

    log.info("Plan de charge calculé",
             vehicle_id=vehicle.id,
             charger_id=vehicle.charger_id,
             current_soc=current_soc_pct,
             target_soc=target_soc,
             amps=round(amps, 1),
             departure=departure.isoformat(),
             available_h=round(available_hours, 1))

    return plan
