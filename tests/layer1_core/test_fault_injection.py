"""Couche 1 — Injection de défauts sim (F1 du plan)."""
from __future__ import annotations

import asyncio
import pytest

from tests.helpers.wait import wait_for


pytestmark = [pytest.mark.sim_only]


async def test_fault_faulted_then_clear(api, sim, db, rawlog_sim, clean_sim_state,
                                        sim_online, charger_ids):
    charger_id = charger_ids["sim"]

    # (1) Injection
    res = await sim.inject_fault("faulted", error_code="GroundFailure")
    assert res.get("ok")

    # (2) Sim doit avoir publié StatusNotification Faulted
    st = await wait_for(
        lambda: _sim_state_if(sim, conn1_status="Faulted"),
        timeout=8, message="conn1 pas en Faulted côté sim",
    )
    assert st["fault_injection"] == "faulted"

    # (3) Frame CALL StatusNotification reçue par le serveur
    #     (peut être déjà dans le buffer après le drain du sim)
    found = False
    rawlog_sim.drain()
    for f in rawlog_sim._buffer:  # pylint: disable=protected-access
        if f.action == "StatusNotification" and isinstance(f.payload, dict):
            if (f.payload.get("status") == "Faulted"
                    or f.payload.get("errorCode") == "GroundFailure"):
                found = True
                break
    assert found, "StatusNotification Faulted absent du raw log"

    # (4) Clear → retour Available
    await sim.inject_fault("clear")
    await wait_for(
        lambda: _sim_state_if(sim, fault_injection=None),
        timeout=8, message="Fault pas effacé côté sim",
    )


async def _sim_state_if(sim, **expected):
    st = await sim.get_state()
    for k, v in expected.items():
        if st.get(k) != v:
            return None
    return st
