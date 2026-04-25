"""Couche 3 — TriggerMessage (T1, T2) : Heartbeat et StatusNotification."""
from __future__ import annotations

import pytest


pytestmark = [pytest.mark.sim_only]


async def test_trigger_heartbeat(api, rawlog_sim, sim_online, charger_ids):
    """T1 — TriggerMessage(Heartbeat) → CALL + Heartbeat reçu en raw log."""
    charger_id = charger_ids["sim"]

    result = await api.trigger(charger_id, message="Heartbeat")
    assert result.get("status") == "Accepted", f"Trigger refusé: {result}"

    # CALL TriggerMessage (serveur → borne)
    trig_frame = await rawlog_sim.wait_for(
        action="TriggerMessage", direction="send", msg_type=2, timeout=5,
    )
    assert trig_frame.payload.get("requestedMessage") == "Heartbeat"

    # Heartbeat reçu (borne → serveur)
    # parse_line normalise "receive message" → direction="receive"
    hb_frame = await rawlog_sim.wait_for(
        action="Heartbeat", direction="receive", msg_type=2, timeout=10,
    )
    assert isinstance(hb_frame.payload, dict)


async def test_trigger_status_notification(api, rawlog_sim, sim_online, charger_ids):
    """T2 — TriggerMessage(StatusNotification) → StatusNotification reçu."""
    charger_id = charger_ids["sim"]

    result = await api.trigger(charger_id, message="StatusNotification", connector_id=1)
    assert result.get("status") == "Accepted", f"Trigger refusé: {result}"

    await rawlog_sim.wait_for(
        action="TriggerMessage", direction="send", msg_type=2, timeout=5,
    )

    # StatusNotification (borne → serveur)
    sn_frame = await rawlog_sim.wait_for(
        action="StatusNotification", direction="receive", msg_type=2, timeout=10,
    )
    payload = sn_frame.payload
    assert isinstance(payload, dict), f"Payload StatusNotification invalide: {payload}"
    assert "status" in payload
