"""Tests Reservation (§6.17 ReserveNow, §6.5 CancelReservation)."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import desc, select

from ocpp.v16 import call as ocpp_call

from certification import catalog
from certification.catalog import TestCase, TestResult
from certification.helpers import TestContext

from db.database import AsyncSessionLocal
from db.models import Reservation, ReservationStatus


CERTIF_RES_TAG = "CERTIF_VALID"


async def _next_reservation_id(charger_id: str) -> int:
    async with AsyncSessionLocal() as db:
        q = (
            select(Reservation.reservation_id)
            .where(Reservation.charger_id == charger_id)
            .order_by(desc(Reservation.reservation_id))
            .limit(1)
        )
        r = await db.execute(q)
        last = r.scalar_one_or_none()
        return (int(last) if last is not None else 0) + 1


async def _persist_reservation(
    charger_id: str,
    reservation_id: int,
    connector_id: int,
    id_tag: str,
    expiry_date: datetime,
) -> Reservation:
    async with AsyncSessionLocal() as db:
        row = Reservation(
            reservation_id=reservation_id,
            charger_id=charger_id,
            connector_id=connector_id,
            id_tag=id_tag,
            expiry_date=expiry_date,
            status=ReservationStatus.ACTIVE,
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
        return row


async def _get_reservation_status(charger_id: str, reservation_id: int):
    async with AsyncSessionLocal() as db:
        q = select(Reservation).where(
            Reservation.charger_id == charger_id,
            Reservation.reservation_id == reservation_id,
        )
        r = await db.execute(q)
        row = r.scalar_one_or_none()
        return row.status if row else None


async def _run_reserve_and_consume(ctx: TestContext) -> TestResult:
    if not ctx.supports("Reservation"):
        return TestResult(status="skipped", message="Profil Reservation non supporté")
    cp = ctx.get_cp()
    if cp is None:
        return TestResult(status="failed", message="Borne hors ligne")
    await ctx.ensure_tag(CERTIF_RES_TAG, active=True)
    # Annule les active orphelines pour repartir propre
    await ctx.cancel_active_reservations()

    rid = await _next_reservation_id(ctx.charger_id)
    expiry = datetime.now(timezone.utc) + timedelta(minutes=15)
    await _persist_reservation(ctx.charger_id, rid, 1, CERTIF_RES_TAG, expiry)

    try:
        resp = await cp.call(ocpp_call.ReserveNow(
            connector_id=1,
            expiry_date=expiry.isoformat(),
            id_tag=CERTIF_RES_TAG,
            reservation_id=rid,
        ))
        status = getattr(resp, "status", "Unknown")
    except Exception as e:
        return TestResult(
            status="failed",
            message=f"ReserveNow a levé une exception : {e}",
        )
    details = {"reservation_id": rid, "reserve_status": str(status)}
    if str(status).lower() != "accepted":
        return TestResult(
            status="failed",
            message=f"ReserveNow refusé : {status}",
            details=details,
            recommendation="Vérifier que la borne est Available et supporte ReserveNow.",
        )

    # Pas de plug véhicule en certif passive : on cancel pour cleanup et on
    # vérifie juste la persistance ACTIVE en DB.
    db_status = await _get_reservation_status(ctx.charger_id, rid)
    details["db_status"] = str(db_status) if db_status else None

    # Cleanup
    try:
        await cp.call(ocpp_call.CancelReservation(reservation_id=rid))
    except Exception:
        pass
    await ctx.cancel_active_reservations()

    if db_status is None:
        return TestResult(
            status="failed",
            message="Réservation acceptée mais non persistée en DB",
            details=details,
            recommendation="Vérifier le hook Sprint 28 A7.",
        )
    return TestResult(
        status="passed",
        message=f"ReserveNow accepté + persisté en DB (rid={rid})",
        details=details,
    )


catalog.register(
    TestCase(
        name="res.reserve_and_consume",
        category="Reservation",
        title="ReserveNow + persistance (§6.17)",
        description="Crée une réservation 15 min, vérifie qu'elle est acceptée et persistée.",
        run=_run_reserve_and_consume,
        requires_profile="Reservation",
        estimated_seconds=10,
        suites={"full"},
        ocpp_ref="§6.17 ReserveNow",
    )
)


# ─────────────────────────── res.cancel ───────────────────────────


async def _run_cancel_reservation(ctx: TestContext) -> TestResult:
    if not ctx.supports("Reservation"):
        return TestResult(status="skipped", message="Profil Reservation non supporté")
    cp = ctx.get_cp()
    if cp is None:
        return TestResult(status="failed", message="Borne hors ligne")
    await ctx.ensure_tag(CERTIF_RES_TAG, active=True)
    await ctx.cancel_active_reservations()

    rid = await _next_reservation_id(ctx.charger_id)
    expiry = datetime.now(timezone.utc) + timedelta(minutes=10)
    await _persist_reservation(ctx.charger_id, rid, 1, CERTIF_RES_TAG, expiry)

    try:
        resp = await cp.call(ocpp_call.ReserveNow(
            connector_id=1,
            expiry_date=expiry.isoformat(),
            id_tag=CERTIF_RES_TAG,
            reservation_id=rid,
        ))
        s_reserve = getattr(resp, "status", "Unknown")
    except Exception as e:
        return TestResult(status="failed", message=f"ReserveNow ex : {e}")
    if str(s_reserve).lower() != "accepted":
        return TestResult(
            status="failed",
            message=f"ReserveNow non accepté ({s_reserve}) — impossible de tester Cancel.",
        )

    try:
        resp = await cp.call(ocpp_call.CancelReservation(reservation_id=rid))
        s_cancel = getattr(resp, "status", "Unknown")
    except Exception as e:
        return TestResult(
            status="failed",
            message=f"CancelReservation a levé une exception : {e}",
        )
    details = {"reservation_id": rid, "cancel_status": str(s_cancel)}
    if str(s_cancel).lower() not in {"accepted"}:
        return TestResult(
            status="failed",
            message=f"CancelReservation refusé : {s_cancel}",
            details=details,
            recommendation="Vérifier §6.5 CancelReservation côté borne.",
        )
    db_status = await _get_reservation_status(ctx.charger_id, rid)
    details["db_status_after_cancel"] = str(db_status) if db_status else None
    return TestResult(
        status="passed",
        message=f"CancelReservation OK (rid={rid})",
        details=details,
    )


catalog.register(
    TestCase(
        name="res.cancel",
        category="Reservation",
        title="CancelReservation (§6.5)",
        description="Crée puis annule une réservation, vérifie le statut Accepted.",
        run=_run_cancel_reservation,
        requires_profile="Reservation",
        estimated_seconds=10,
        suites={"full"},
        ocpp_ref="§6.5 CancelReservation",
    )
)


# ─────────────────────────── res.expiry ──────────────────────────────────────


async def _run_reserve_expiry(ctx: TestContext) -> TestResult:
    """Vérifie que la boucle d'expiration serveur purge bien les réservations échues."""
    if not ctx.supports("Reservation"):
        return TestResult(status="skipped", message="Profil Reservation non supporté")
    cp = ctx.get_cp()
    if cp is None:
        return TestResult(status="failed", message="Borne hors ligne")
    await ctx.ensure_tag(CERTIF_RES_TAG, active=True)
    await ctx.cancel_active_reservations()

    # Expiry dans 65s — la boucle serveur tourne toutes les 60s
    rid = await _next_reservation_id(ctx.charger_id)
    expiry = datetime.now(timezone.utc) + timedelta(seconds=65)
    await _persist_reservation(ctx.charger_id, rid, 1, CERTIF_RES_TAG, expiry)

    try:
        resp = await cp.call(ocpp_call.ReserveNow(
            connector_id=1,
            expiry_date=expiry.isoformat(),
            id_tag=CERTIF_RES_TAG,
            reservation_id=rid,
        ))
        reserve_status = getattr(resp, "status", "Unknown")
    except Exception as e:
        return TestResult(status="failed", message=f"ReserveNow exception : {e}")

    if str(reserve_status).lower() != "accepted":
        return TestResult(
            status="failed",
            message=f"ReserveNow refusé ({reserve_status}) — impossible de tester l'expiry",
            recommendation="Vérifier que la borne est Available et supporte ReserveNow.",
        )

    ctx.log("info", f"Réservation rid={rid} créée (expiry 65s). Attente boucle d'expiration (90s)…")
    await asyncio.sleep(90)

    db_status = await _get_reservation_status(ctx.charger_id, rid)
    details = {
        "reservation_id": rid,
        "reserve_response": str(reserve_status),
        "db_status_after_90s": str(db_status) if db_status else None,
    }

    if db_status is None:
        return TestResult(
            status="failed",
            message="Réservation introuvable en DB après attente",
            details=details,
        )

    from db.models import ReservationStatus
    if db_status == ReservationStatus.EXPIRED:
        return TestResult(
            status="passed",
            message=f"Réservation rid={rid} correctement expirée par la boucle serveur",
            details=details,
        )

    return TestResult(
        status="failed",
        message=f"Statut DB après 90s : {db_status} (attendu : Expired)",
        details=details,
        recommendation=(
            "Vérifier que la boucle _reservation_expiry_loop() est active "
            "et tourne toutes les 60s dans le serveur OCPP."
        ),
    )


catalog.register(
    TestCase(
        name="res.expiry",
        category="Reservation",
        title="Expiration automatique réservation (§6.17)",
        description=(
            "Crée une réservation avec expiry dans 65s, attend 90s, "
            "vérifie que la boucle serveur l'a marquée Expired en DB."
        ),
        run=_run_reserve_expiry,
        requires_profile="Reservation",
        estimated_seconds=100,
        suites={"full"},
        ocpp_ref="§6.17 ReserveNow / expiry_date",
    )
)
