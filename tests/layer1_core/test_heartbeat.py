"""Couche 1 — Heartbeat (C2, C3, C4 du plan)."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta

import pytest

from tests.helpers.wait import wait_for


pytestmark = [pytest.mark.sim_only]


async def test_trigger_heartbeat_produces_frame(api, rawlog_sim, sim_online, charger_ids):
    """TriggerMessage Heartbeat → CALL Heartbeat du borne visible dans raw log."""
    charger_id = charger_ids["sim"]
    result = await api.trigger(charger_id, "Heartbeat")
    assert result.get("status") == "Accepted", f"Trigger refusé: {result}"

    # Le sim répond par un Heartbeat CALL (2) dans les 5s
    frame = await rawlog_sim.wait_for(
        action="Heartbeat", direction="receive", msg_type=2, timeout=8,
    )
    assert frame.charger_id == charger_id
    assert frame.is_call


async def test_heartbeat_is_persisted(api, db, sim_online, charger_ids):
    """L'horloge last_heartbeat en DB est mise à jour < 90s."""
    charger_id = charger_ids["sim"]
    # Force un HB frais via trigger pour ne pas dépendre du cycle naturel
    await api.trigger(charger_id, "Heartbeat")

    async def recent():
        c = await db.get_charger(charger_id)
        if not c or not c["last_heartbeat"]:
            return None
        # SQLite retourne le timestamp en string sans suffixe fuseau
        ts = datetime.fromisoformat(c["last_heartbeat"]).replace(tzinfo=timezone.utc)
        age = datetime.now(timezone.utc) - ts
        return c if age < timedelta(minutes=2) else None

    await wait_for(recent, timeout=10, message="last_heartbeat pas rafraîchi")


async def test_heartbeat_interval_patch(api, rawlog_sim, sim_online, charger_ids):
    """PATCH chargers/{id} heartbeat_interval → serveur envoie ChangeConfiguration."""
    charger_id = charger_ids["sim"]
    # Patch live
    new_interval = 45
    try:
        updated = await api.patch_charger(charger_id, heartbeat_interval=new_interval)
        # Le PATCH déclenche un asyncio.create_task pour push ChangeConfiguration
        # On observe la trame SEND ChangeConfiguration dans les 5s
        frame = await rawlog_sim.wait_for(
            action="ChangeConfiguration", direction="send", msg_type=2, timeout=8,
        )
        assert isinstance(frame.payload, dict)
        assert frame.payload.get("key") == "HeartbeatInterval"
        assert str(frame.payload.get("value")) == str(new_interval)
    finally:
        # Restore (pas strict car default profile s'applique si None, mais
        # on remet à 30 pour que les tests suivants aient une cadence normale)
        await api.patch_charger(charger_id, heartbeat_interval=30)
        await asyncio.sleep(1)
