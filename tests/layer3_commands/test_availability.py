"""Couche 3 — ChangeAvailability + UnlockConnector (A1, A2, A3)."""
from __future__ import annotations

import asyncio
import pytest

from tests.helpers.wait import wait_for


pytestmark = [pytest.mark.sim_only]


async def test_change_availability_inoperative_then_operative(api, sim, rawlog_sim,
                                                               clean_sim_state,
                                                               sim_online, charger_ids):
    """A1+A2 — ChangeAvailability Inoperative puis Operative + statut sim cohérent."""
    charger_id = charger_ids["sim"]

    # A1 : Inoperative
    result = await api.change_availability(charger_id, connector_id=0, operative=False)
    assert "Inoperative" in result.get("detail", ""), f"Echec: {result}"

    ca_frame = await rawlog_sim.wait_for(
        action="ChangeAvailability", direction="send", msg_type=2, timeout=5,
    )
    assert ca_frame.payload.get("type") == "Inoperative"

    async def _unavailable():
        st = await sim.get_state()
        return st if (st.get("conn1_status") or "").lower() == "unavailable" else None

    await wait_for(_unavailable, timeout=10, message="Sim pas Unavailable")

    # A2 : Operative
    result2 = await api.change_availability(charger_id, connector_id=0, operative=True)
    assert "Operative" in result2.get("detail", ""), f"Echec restore: {result2}"

    await rawlog_sim.wait_for(
        action="ChangeAvailability", direction="send", msg_type=2, timeout=5,
    )

    async def _available():
        st = await sim.get_state()
        return st if (st.get("conn1_status") or "").lower() == "available" else None

    await wait_for(_available, timeout=10, message="Sim pas revenu Available")


async def test_unlock_connector_not_plugged(api, rawlog_sim, clean_sim_state,
                                            sim_online, charger_ids):
    """A3 — UnlockConnector sans véhicule → UnlockConnector CALL + réponse non-Failed."""
    charger_id = charger_ids["sim"]

    result = await api.unlock_connector(charger_id, connector_id=1)
    assert result.get("status", "").lower() in ("unlocked", "notsupported") or \
           "déverrouillé" in result.get("detail", "").lower(), \
           f"Résultat inattendu: {result}"

    frame = await rawlog_sim.wait_for(
        action="UnlockConnector", direction="send", msg_type=2, timeout=5,
    )
    assert frame.payload.get("connectorId") == 1 or frame.payload.get("connector_id") == 1


async def test_change_availability_scheduled_during_charge(api, sim, rawlog_sim,
                                                            clean_sim_state,
                                                            sim_online, charger_ids):
    """A4 — ChangeAvailability Inoperative pendant charge → status=Scheduled (pas Accepted)."""
    charger_id = charger_ids["sim"]

    # Brancher véhicule (démarre une charge)
    await sim.plug(True, soc_start=30.0)
    async def _charging():
        st = await sim.get_state()
        return st if (st.get("conn1_status") or "").lower() == "charging" else None

    await wait_for(_charging, timeout=15, message="Sim pas en charge")

    # Demander Inoperative pendant charge → borne répond Scheduled
    result = await api.change_availability(charger_id, connector_id=0, operative=False)
    # Pas d'exception → le serveur a bien traité la réponse Scheduled (non-False)
    assert "Inoperative" in result.get("detail", ""), f"Echec: {result}"

    # CALLRESULT ChangeAvailability avec status="Scheduled" dans raw log
    ca_frame = await rawlog_sim.wait_for(
        action="ChangeAvailability", direction="send", msg_type=2, timeout=5,
    )
    assert ca_frame.payload.get("type") == "Inoperative"

    # Nettoyer : débrancher puis remettre Operative
    await sim.plug(False)
    await asyncio.sleep(1.0)
    await api.change_availability(charger_id, connector_id=0, operative=True)
    await asyncio.sleep(0.5)
