"""Couche 1 — Session complète plug → StartTransaction → MeterValues → Stop (C5, C7)."""
from __future__ import annotations

import asyncio
import pytest

from tests.helpers.wait import wait_for


pytestmark = [pytest.mark.sim_only, pytest.mark.slow]


@pytest.fixture
def session_params():
    return {
        "soc_start": 30.0,
        "soc_target": 50.0,  # petit écart, évite de traîner
        "battery_kwh": 60.0,
    }


async def test_full_cycle(api, sim, db, rawlog_sim, clean_sim_state,
                         sim_online, charger_ids, session_params):
    """Cycle complet sur VIRTUAL-001 à speed=600× (≈10-20s simulées)."""
    charger_id = charger_ids["sim"]

    # Accélération de la simulation pour que les MeterValues avancent vite
    await sim.set_speed(600)

    # (1) Plug — déclenche Preparing puis auto-start après 2s (sim)
    await sim.plug(plugged=True, **session_params)

    # (2) Transaction_id apparaît côté sim
    state = await wait_for(
        lambda: _wait_tx(sim),
        timeout=20, message="transaction_id sim jamais apparu",
    )
    tx_id = state["transaction_id"]
    assert tx_id is not None

    # (3) StartTransaction CALL visible dans raw log
    # On l'a peut-être déjà reçu pendant le wait_for ci-dessus → drain + buffer
    rawlog_sim.drain()
    found_start = any(
        f.action == "StartTransaction" and f.is_call
        for f in rawlog_sim._buffer  # pylint: disable=protected-access
    )
    assert found_start, "StartTransaction absent du raw log"

    # (4) Session persistée en DB avec start_time renseigné
    session = await wait_for(
        lambda: _find_session(db, charger_id, tx_id),
        timeout=10, message=f"Session tx={tx_id} pas persistée",
    )
    assert session["start_time"] is not None
    assert session["stop_time"] is None
    assert session["meter_start"] is not None

    # (5) Attendre ≥ 1 MeterValue
    mv = await wait_for(
        lambda: _has_metervalues(db, session["id"]),
        timeout=90, poll=2, message="Aucun MeterValue pour la session",
    )
    assert mv >= 1

    # (6) Unplug → StopTransaction avec reason=EVDisconnected
    await sim.plug(plugged=False)

    # (7) Session fermée
    stopped = await wait_for(
        lambda: _find_stopped(db, charger_id, tx_id),
        timeout=30, message="StopTransaction pas persisté",
    )
    assert stopped["stop_time"] is not None
    assert stopped["meter_stop"] is not None
    # Au moins 1 Wh consommé à speed=600× même sur 5s
    assert stopped["energy_wh"] is not None
    assert stopped["energy_wh"] >= 1, f"energy_wh trop bas: {stopped['energy_wh']}"


# ── helpers asyncio ────────────────────────────────────────────────────────
async def _wait_tx(sim):
    st = await sim.get_state()
    return st if st.get("transaction_id") else None


async def _find_session(db, charger_id, tx_id):
    return await db.get_session_by_tx(charger_id, tx_id)


async def _has_metervalues(db, session_id):
    return await db.count_metervalues(session_id)


async def _find_stopped(db, charger_id, tx_id):
    s = await db.get_session_by_tx(charger_id, tx_id)
    if s and s["stop_time"]:
        return s
    return None
