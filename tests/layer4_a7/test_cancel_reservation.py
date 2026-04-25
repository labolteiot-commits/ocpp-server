"""Couche 4 — CancelReservation (§6.5) + edge cases 404 / 400.

Sprint 28 A7 : CancelReservation refuse côté API REST si :
- la réservation n'existe pas (404)
- la réservation n'est pas ACTIVE (400) — déjà Cancelled/Used/Expired

Quand la borne répond Rejected à l'OCPP CancelReservation (cas typique où
la sim n'a plus conn1_status=Reserved au moment de l'annulation), le
serveur marque quand même CANCELLED en DB — c'est lui la source de vérité
côté opérateur.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tests.helpers.wait import wait_for


pytestmark = [pytest.mark.sim_only]


async def _reserve(api, charger_id, id_tag="LOCAL"):
    expiry = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    r = await api.reserve(charger_id, id_tag=id_tag, connector_id=1,
                          expiry_date=expiry)
    assert r.status_code == 200
    data = r.json()
    assert data["db_status"] == "Active", f"Reserve pas Active: {data}"
    return data["reservation_id"]


async def test_cancel_accepted_flips_to_cancelled(api, db, clean_sim_state,
                                                   rawlog_sim, sim_online,
                                                   charger_ids):
    """C1 — Reserve → Cancel → DB status=CANCELLED + CALL OCPP envoyé."""
    charger_id = charger_ids["sim"]
    rid = await _reserve(api, charger_id)

    # Consommer le frame ReserveNow d'abord
    await rawlog_sim.wait_for(action="ReserveNow", direction="send",
                              msg_type=2, timeout=5)

    r = await api.cancel_reserve(charger_id, rid)
    assert r.status_code == 200, f"Cancel HTTP: {r.status_code} {r.text}"
    data = r.json()
    assert data["db_status"] == "Cancelled"
    assert data["reservation_id"] == rid

    # CALL CancelReservation envoyé à la borne
    frame = await rawlog_sim.wait_for(
        action="CancelReservation", direction="send", msg_type=2, timeout=5,
    )
    assert frame.payload.get("reservationId") == rid

    # DB : plus dans ACTIVE, présent dans CANCELLED (SQLAlchemy stocke le NOM de l'enum)
    async def _is_cancelled():
        rows = await db.get_reservations(charger_id, status="CANCELLED")
        return next((r for r in rows if r["reservation_id"] == rid), None)
    row = await wait_for(_is_cancelled, timeout=5,
                         message="Reservation pas CANCELLED")
    # ocpp_status du cancel ajouté en suffixe (ex: "Accepted | cancel=Accepted")
    assert "cancel=" in (row["ocpp_status"] or "")


async def test_cancel_unknown_reservation_returns_404(api, clean_sim_state,
                                                     sim_online, charger_ids):
    """C2 — Cancel d'un reservation_id inexistant → HTTP 404."""
    charger_id = charger_ids["sim"]

    r = await api.cancel_reserve(charger_id, reservation_id=999_999, check=False)
    assert r.status_code == 404, f"Attendu 404, reçu {r.status_code}: {r.text}"
    assert "introuvable" in r.json().get("detail", "").lower()


async def test_cancel_already_cancelled_returns_400(api, clean_sim_state,
                                                    sim_online, charger_ids):
    """C3 — Cancel d'une réservation déjà CANCELLED → HTTP 400.

    Le serveur vérifie que le status DB est ACTIVE avant d'envoyer le CALL
    OCPP (sinon on double-annule et on pollue le ocpp_status).
    """
    charger_id = charger_ids["sim"]
    rid = await _reserve(api, charger_id)

    # Première annulation (doit réussir)
    r1 = await api.cancel_reserve(charger_id, rid)
    assert r1.status_code == 200

    # Deuxième annulation → refusée
    r2 = await api.cancel_reserve(charger_id, rid, check=False)
    assert r2.status_code == 400, f"Attendu 400, reçu {r2.status_code}: {r2.text}"
    detail = r2.json().get("detail", "").lower()
    assert "non annulable" in detail or "cancelled" in detail
