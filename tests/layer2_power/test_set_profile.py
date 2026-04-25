"""Couche 2 — SetChargingProfile : trame, persistance snapshot (P1, P2)."""
from __future__ import annotations

import asyncio
import pytest

from tests.helpers.wait import wait_for


pytestmark = [pytest.mark.sim_only]


async def test_set_profile_persists_snapshot(api, db, rawlog_sim, clean_sim_state,
                                             sim_online, charger_ids):
    """P1 — POST /charging-profile/set 6A → raw log CALL + DB snapshot."""
    charger_id = charger_ids["sim"]
    amps = 6.0

    # Clear d'abord pour partir d'un état propre (tolère absence)
    try:
        await api.clear_charging_profile(charger_id, connector_id=0)
    except Exception:
        pass
    await asyncio.sleep(0.5)

    result = await api.set_charging_profile(charger_id, max_amps=amps, connector_id=1)
    assert result.get("status") == "Accepted", f"Refusé: {result}"

    # Frame CALL SetChargingProfile visible dans raw log (envoi serveur → borne)
    frame = await rawlog_sim.wait_for(
        action="SetChargingProfile", direction="send", msg_type=2, timeout=5,
    )
    assert isinstance(frame.payload, dict)
    # Le payload OCPP a la forme {connectorId, csChargingProfiles: {...}}
    csp = frame.payload.get("csChargingProfiles") or frame.payload.get("cs_charging_profiles")
    assert csp is not None, f"Profil absent du payload: {frame.payload}"

    # Snapshot persisté
    snap = await wait_for(
        lambda: db.get_snapshot(charger_id, 1, "TxDefaultProfile"),
        timeout=5, message="Snapshot pas persisté",
    )
    assert snap["expired_at"] is None
    # profile_json est sérialisé en JSON dans aiosqlite
    import json
    pj = json.loads(snap["profile_json"]) if isinstance(snap["profile_json"], str) else snap["profile_json"]
    limit = pj["charging_schedule"]["charging_schedule_period"][0]["limit"]
    assert float(limit) == amps


async def test_profile_ramp_upserts(api, db, clean_sim_state, sim_online, charger_ids):
    """P2 — Rampe 6→10→16→24A : un seul snapshot (UPSERT), limit final=24."""
    charger_id = charger_ids["sim"]

    for target_a in (6.0, 10.0, 16.0, 24.0):
        res = await api.set_charging_profile(charger_id, max_amps=target_a, connector_id=1)
        assert res.get("status") == "Accepted"
        await asyncio.sleep(0.5)

    # /api/commands/charging-profile/snapshots/{id} retourne count=1 car UPSERT
    resp = await api.get_snapshots(charger_id)
    active = [s for s in resp["snapshots"] if s["purpose"] == "TxDefaultProfile"
              and s["connector_id"] == 1]
    assert len(active) == 1, f"Attendu 1 snapshot TxDefaultProfile/conn1, trouvé {len(active)}"

    # Le dernier limit doit être 24
    import json
    prof = active[0]["profile"]
    if isinstance(prof, str):
        prof = json.loads(prof)
    limit = prof["charging_schedule"]["charging_schedule_period"][0]["limit"]
    assert float(limit) == 24.0
