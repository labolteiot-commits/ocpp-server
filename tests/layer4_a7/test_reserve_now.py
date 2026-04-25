"""Couche 4 — ReserveNow (§6.18) + persistance + consommation StartTransaction.

Sprint 28 A7 : le serveur génère reservation_id = max+1 par borne, persiste
la ligne AVANT l'appel OCPP (audit garanti), et passe db_status=ACTIVE
si la borne répond Accepted, REJECTED sinon. La ligne reste en DB dans les
deux cas (HTTP 200 garanti — pas 400).

Sur StartTransaction avec le même id_tag (+ connector_id ou 0), le hook
`_consume_reservation` passe la ligne à USED.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tests.helpers.wait import wait_for


pytestmark = [pytest.mark.sim_only]


async def _cleanup_reservation(api, charger_id: str, reservation_id: int):
    """Best-effort cancel pour libérer le conn1_status=Reserved du sim."""
    try:
        await api.cancel_reserve(charger_id, reservation_id, check=False)
    except Exception:
        pass


async def test_reserve_accepted_persists_active(api, db, clean_sim_state,
                                                rawlog_sim, sim_online,
                                                charger_ids):
    """A1 — Reserve accepté → DB status=Active + CALL OCPP + ocpp_status=Accepted."""
    charger_id = charger_ids["sim"]
    expiry = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()

    r = await api.reserve(charger_id, id_tag="LOCAL", connector_id=1,
                          expiry_date=expiry)
    assert r.status_code == 200, f"Reserve HTTP: {r.status_code} {r.text}"
    data = r.json()
    assert data["status"] == "Accepted", f"OCPP status non-Accepted: {data}"
    assert data["db_status"] == "Active"
    rid = data["reservation_id"]
    assert rid >= 1

    # CALL ReserveNow envoyé
    frame = await rawlog_sim.wait_for(
        action="ReserveNow", direction="send", msg_type=2, timeout=5,
    )
    assert frame.payload.get("reservationId") == rid
    assert frame.payload.get("idTag") == "LOCAL"

    # DB persiste la ligne ACTIVE (SQLAlchemy stocke le NOM de l'enum)
    async def _find():
        rows = await db.get_reservations(charger_id, status="ACTIVE")
        return next((r for r in rows if r["reservation_id"] == rid), None)

    row = await wait_for(_find, timeout=5, message="Reservation pas en DB")
    assert row["id_tag"] == "LOCAL"
    assert row["connector_id"] == 1
    assert row["ocpp_status"] == "Accepted"

    await _cleanup_reservation(api, charger_id, rid)


async def test_reserve_rejected_when_faulted(api, sim, db, clean_sim_state,
                                             rawlog_sim, sim_online,
                                             charger_ids):
    """A2 — Borne en Faulted → ReserveNow renvoie Faulted → DB status=REJECTED.

    La ligne est PERSISTÉE même si la borne refuse (audit). HTTP 200, pas 400.
    """
    charger_id = charger_ids["sim"]

    await sim.inject_fault("faulted", error_code="GroundFailure")
    # Laisse le StatusNotification atteindre le serveur
    async def _is_faulted():
        st = await sim.get_state()
        return (st.get("conn1_status") or "").lower() == "faulted"
    await wait_for(_is_faulted, timeout=5, message="sim pas Faulted")

    expiry = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    r = await api.reserve(charger_id, id_tag="LOCAL", connector_id=1,
                          expiry_date=expiry)
    assert r.status_code == 200, f"Doit rester 200 même si Rejected: {r.status_code}"
    data = r.json()
    # La réponse OCPP du sim est "Faulted" (status de refus OCPP §6.18)
    assert data["status"] in ("Faulted", "Occupied", "Unavailable", "Rejected"), \
        f"Status inattendu: {data}"
    assert data["db_status"] == "Rejected"
    rid = data["reservation_id"]

    # Audit row en DB malgré le refus (SQLAlchemy stocke le NOM de l'enum)
    rows = await db.get_reservations(charger_id, status="REJECTED")
    assert any(x["reservation_id"] == rid for x in rows), \
        f"Ligne REJECTED absente: {rows}"


async def test_reserve_consumed_on_start_transaction(api, sim, db, clean_sim_state,
                                                    rawlog_sim, sim_online,
                                                    charger_ids):
    """A3 — Reserve + plug (StartTransaction id_tag=LOCAL) → DB status=USED.

    Le hook _consume_reservation match sur (charger_id, id_tag, connector in
    [X, 0], status=ACTIVE, expiry > now) et passe à USED.
    """
    charger_id = charger_ids["sim"]
    expiry = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()

    r = await api.reserve(charger_id, id_tag="LOCAL", connector_id=1,
                          expiry_date=expiry)
    data = r.json()
    assert data["db_status"] == "Active"
    rid = data["reservation_id"]

    # Plug → _auto_start() avec id_tag="LOCAL" → StartTransaction
    await sim.plug(True, soc_start=30.0, soc_target=80.0, battery_kwh=60.0)

    async def _has_tx():
        st = await sim.get_state()
        return st.get("transaction_id")
    tx_id = await wait_for(_has_tx, timeout=15,
                           message="StartTransaction pas reçu")

    # Consommation : la ligne doit passer à USED (commit dans le hook async)
    async def _is_used():
        rows = await db.get_reservations(charger_id, status="USED")
        return next((r for r in rows if r["reservation_id"] == rid), None)
    used = await wait_for(_is_used, timeout=8,
                          message="Reservation pas passée à USED après StartTx")
    assert used["id_tag"] == "LOCAL"

    # Nettoyer la transaction
    await sim.plug(False)
