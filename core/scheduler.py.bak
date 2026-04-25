# core/scheduler.py
"""
Scheduler de charge prédictif.

Tourne en boucle toutes les LOOP_INTERVAL secondes.
Pour chaque véhicule associé à une borne :
  1. Récupère la dernière lecture OBD2 (SOC actuel)
  2. Récupère le plan actif ou en calcule un nouveau via predictor
  3. Si la borne est en charge ou en Preparing → applique le profil via SetChargingProfile
  4. Si la charge est terminée (SOC atteint) → réduit à MIN_AMPS ou arrête

Le scheduler ne démarre PAS les transactions — c'est auto_remote_start ou le dashboard.
Il contrôle uniquement le courant via SetChargingProfile sur une session déjà active.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Optional

from sqlalchemy import select, update

from db.database import AsyncSessionLocal
from db.models import Vehicle, OBD2Reading, ChargingPlan, Session as ChargingSession, SessionStatus
from core.predictor import build_charging_plan, MIN_AMPS
from core.logging import log

if TYPE_CHECKING:
    from core.ocpp_server import OCPPServer

LOOP_INTERVAL    = 60    # secondes entre chaque cycle du scheduler
SOC_TOLERANCE    = 1.5   # % de tolérance sur le SOC cible
STALE_READING_S  = 300   # lecture OBD2 considérée périmée après 5 min


class ChargingScheduler:

    def __init__(self, ocpp_server: "OCPPServer"):
        self._server  = ocpp_server
        self._running = False
        self._task: Optional[asyncio.Task] = None

    def start(self) -> None:
        if not self._running:
            self._running = True
            self._task    = asyncio.create_task(self._loop())
            log.info("Scheduler de charge démarré", interval_s=LOOP_INTERVAL)

    def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        log.info("Scheduler de charge arrêté")

    async def _loop(self) -> None:
        while self._running:
            try:
                await self._cycle()
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error("Scheduler — erreur cycle", error=str(e))
            await asyncio.sleep(LOOP_INTERVAL)

    async def _cycle(self) -> None:
        """Un cycle complet : évalue chaque véhicule et ajuste la charge."""
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Vehicle).where(Vehicle.charger_id.is_not(None))
            )
            vehicles = result.scalars().all()

        for vehicle in vehicles:
            try:
                await self._process_vehicle(vehicle)
            except Exception as e:
                log.warning("Scheduler — erreur véhicule",
                            vehicle_id=vehicle.id, error=str(e))

    async def _process_vehicle(self, vehicle: Vehicle) -> None:
        """Évalue et ajuste la charge pour un véhicule."""
        cp = self._server.get_charger(vehicle.charger_id)
        if cp is None:
            return  # borne hors ligne

        # Vérifier si la borne est en état actif (charge ou prêt à charger)
        connector_status = cp._connector1_status
        is_active = connector_status in {
            "Charging", "Preparing", "SuspendedEV", "SuspendedEVSE"
        }
        if not is_active:
            return  # pas de véhicule branché

        # Récupérer la dernière lecture OBD2
        reading = await self._latest_reading(vehicle.id)
        if reading is None:
            log.debug("Scheduler — pas de lecture OBD2", vehicle_id=vehicle.id)
            return

        # Vérifier que la lecture n'est pas périmée
        now = datetime.now(timezone.utc)
        reading_ts = reading.timestamp
        if reading_ts.tzinfo is None:
            reading_ts = reading_ts.replace(tzinfo=timezone.utc)

        age_s = (now - reading_ts).total_seconds()
        if age_s > STALE_READING_S:
            log.debug("Scheduler — lecture OBD2 périmée",
                      vehicle_id=vehicle.id, age_s=int(age_s))
            return

        current_soc = reading.soc_pct
        if current_soc is None:
            return

        target_soc = vehicle.target_soc_pct or 90.0

        # SOC cible atteint → réduire à minimum
        if current_soc >= target_soc - SOC_TOLERANCE:
            log.info("Scheduler — SOC cible atteint, réduction courant",
                     vehicle_id=vehicle.id, soc=current_soc, target=target_soc)
            await self._apply_amps(cp, MIN_AMPS, reason="SOC cible atteint")
            await self._complete_active_plan(vehicle.charger_id)
            return

        # Calculer ou récupérer le plan actif
        plan = await self._active_plan(vehicle.charger_id)
        if plan is None:
            async with AsyncSessionLocal() as db:
                plan = await build_charging_plan(db, vehicle, current_soc, now)
                if plan is not None:
                    db.add(plan)
                    await db.commit()
                    await db.refresh(plan)

        if plan is None:
            return

        # Appliquer le courant du plan si différent de la limite actuelle
        target_amps = plan.required_amps
        current_limit = cp._default_max_amps  # limite actuelle configurée

        # Recalculer si les conditions ont changé (SOC a progressé)
        if plan.departure_time:
            dep_ts = plan.departure_time
            if dep_ts.tzinfo is None:
                dep_ts = dep_ts.replace(tzinfo=timezone.utc)
            remaining_h = (dep_ts - now).total_seconds() / 3600.0
            if remaining_h > 0:
                from core.predictor import calculate_required_amps
                recalc = calculate_required_amps(
                    current_soc_pct  = current_soc,
                    target_soc_pct   = target_soc,
                    battery_kwh      = vehicle.battery_kwh or 60.0,
                    available_hours  = remaining_h,
                    max_amps         = vehicle.max_charge_amps or 32.0,
                    efficiency       = vehicle.charge_efficiency or 0.92,
                )
                if abs(recalc - target_amps) > 1.0:
                    target_amps = recalc
                    log.info("Scheduler — recalcul courant",
                             vehicle_id=vehicle.id,
                             soc=current_soc, remaining_h=round(remaining_h, 1),
                             new_amps=round(target_amps, 1))

        # Appliquer seulement si la différence est significative (> 1A)
        if current_limit is None or abs((current_limit or 0) - target_amps) > 1.0:
            await self._apply_amps(cp, target_amps, reason=f"plan prédictif SOC {current_soc:.0f}%")

            # Marquer le plan comme actif et noter l'application
            async with AsyncSessionLocal() as db:
                await db.execute(
                    update(ChargingPlan)
                    .where(ChargingPlan.id == plan.id)
                    .values(status="active", applied_at=now)
                )
                await db.commit()

    async def _apply_amps(self, cp, amps: float, reason: str = "") -> None:
        """Applique un courant via SetChargingProfile sur la borne."""
        if cp._is_technove:
            profile = {
                "charging_profile_id":      99,
                "stack_level":              0,
                "charging_profile_purpose": "TxDefaultProfile",
                "charging_profile_kind":    "Absolute",
                "charging_schedule": {
                    "charging_rate_unit": "A",
                    "charging_schedule_period": [
                        {"start_period": 0, "limit": amps}
                    ],
                },
            }
            connector_id = 1
        else:
            profile = {
                "charging_profile_id":      99,
                "stack_level":              8,
                "charging_profile_purpose": "ChargePointMaxProfile",
                "charging_profile_kind":    "Absolute",
                "charging_schedule": {
                    "charging_rate_unit": "A",
                    "charging_schedule_period": [
                        {"start_period": 0, "limit": amps}
                    ],
                },
            }
            connector_id = 0

        try:
            status = await cp.set_charging_profile(connector_id, profile)
            log.info("Scheduler — SetChargingProfile",
                     charger_id=cp.id, amps=amps, status=status, reason=reason)
        except Exception as e:
            log.warning("Scheduler — SetChargingProfile échoué",
                        charger_id=cp.id, error=str(e))

    async def _latest_reading(self, vehicle_id: str) -> Optional[OBD2Reading]:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(OBD2Reading)
                .where(OBD2Reading.vehicle_id == vehicle_id)
                .order_by(OBD2Reading.timestamp.desc())
                .limit(1)
            )
            return result.scalar_one_or_none()

    async def _active_plan(self, charger_id: str) -> Optional[ChargingPlan]:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(ChargingPlan)
                .where(
                    ChargingPlan.charger_id == charger_id,
                    ChargingPlan.status.in_(["pending", "active"]),
                )
                .order_by(ChargingPlan.created_at.desc())
                .limit(1)
            )
            return result.scalar_one_or_none()

    async def _complete_active_plan(self, charger_id: str) -> None:
        async with AsyncSessionLocal() as db:
            await db.execute(
                update(ChargingPlan)
                .where(
                    ChargingPlan.charger_id == charger_id,
                    ChargingPlan.status     == "active",
                )
                .values(status="completed")
            )
            await db.commit()

    # ── API publique — appelée depuis les routes ──────────────────────────────

    async def force_recalculate(self, charger_id: str) -> Optional[ChargingPlan]:
        """Force un recalcul immédiat du plan de charge (appelé depuis l'API)."""
        async with AsyncSessionLocal() as db:
            # Annuler les plans pending
            await db.execute(
                update(ChargingPlan)
                .where(
                    ChargingPlan.charger_id == charger_id,
                    ChargingPlan.status.in_(["pending", "active"]),
                )
                .values(status="cancelled")
            )
            # Récupérer le véhicule associé
            result = await db.execute(
                select(Vehicle).where(Vehicle.charger_id == charger_id).limit(1)
            )
            vehicle = result.scalar_one_or_none()
            if vehicle is None:
                return None

            # Dernière lecture OBD2
            r = await db.execute(
                select(OBD2Reading)
                .where(OBD2Reading.vehicle_id == vehicle.id)
                .order_by(OBD2Reading.timestamp.desc())
                .limit(1)
            )
            reading = r.scalar_one_or_none()
            current_soc = reading.soc_pct if reading else 50.0  # défaut si pas de lecture

            plan = await build_charging_plan(db, vehicle, current_soc)
            if plan:
                db.add(plan)
                await db.commit()
                await db.refresh(plan)

            return plan
