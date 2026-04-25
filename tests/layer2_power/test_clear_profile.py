"""Couche 2 — ClearChargingProfile + bugfix Sprint 28 réapply (P4, P6)."""
from __future__ import annotations

import asyncio
import pytest

from tests.helpers.wait import wait_for


pytestmark = [pytest.mark.sim_only]


async def test_clear_profile_expires_snapshot(api, db, clean_sim_state,
                                              sim_online, charger_ids):
    """P4 — SetCP puis ClearCP → snapshot.expired_at peuplé (soft expire)."""
    charger_id = charger_ids["sim"]

    # 1. Apply profile 10A
    res = await api.set_charging_profile(charger_id, max_amps=10.0, connector_id=1)
    assert res.get("status") == "Accepted"

    snap = await wait_for(
        lambda: db.get_snapshot(charger_id, 1, "TxDefaultProfile"),
        timeout=5, message="Snapshot post-set absent",
    )
    assert snap["expired_at"] is None, "Snapshot neuf ne doit pas être expired"

    # 2. Clear (connector_id=0 = tous connecteurs)
    clear_res = await api.clear_charging_profile(charger_id, connector_id=0)
    assert clear_res.get("status") == "Accepted", f"Clear refusé: {clear_res}"

    # 3. Vérifier expired_at peuplé en DB
    async def _is_expired():
        s = await db.get_snapshot(charger_id, 1, "TxDefaultProfile")
        return s if (s and s["expired_at"] is not None) else None

    expired_snap = await wait_for(
        _is_expired, timeout=5,
        message="expired_at pas peuplé après Clear",
    )
    assert expired_snap["expired_at"] is not None

    # 4. API /snapshots par défaut masque les expirés
    resp = await api.get_snapshots(charger_id, include_expired=False)
    active = [s for s in resp["snapshots"] if s["purpose"] == "TxDefaultProfile"
              and s["connector_id"] == 1]
    assert len(active) == 0, "Snapshot expiré visible sans include_expired"

    # 5. include_expired=true → le revoir
    resp_all = await api.get_snapshots(charger_id, include_expired=True)
    all_txdef = [s for s in resp_all["snapshots"] if s["purpose"] == "TxDefaultProfile"
                 and s["connector_id"] == 1]
    assert len(all_txdef) == 1
    assert all_txdef[0]["expired_at"] is not None


async def test_reapply_after_clear_resets_expired(api, db, clean_sim_state,
                                                  sim_online, charger_ids):
    """P6 — Bugfix Sprint 28 : Set → Clear → Set même purpose → expired_at=NULL."""
    charger_id = charger_ids["sim"]

    # 1. Set initial 8A
    await api.set_charging_profile(charger_id, max_amps=8.0, connector_id=1)
    await asyncio.sleep(0.3)

    # 2. Clear
    await api.clear_charging_profile(charger_id, connector_id=0)

    async def _is_expired():
        s = await db.get_snapshot(charger_id, 1, "TxDefaultProfile")
        return s if (s and s["expired_at"] is not None) else None

    await wait_for(_is_expired, timeout=5, message="Clear n'a pas expiré")

    # 3. Ré-apply 12A sur même (charger, conn, purpose)
    res = await api.set_charging_profile(charger_id, max_amps=12.0, connector_id=1)
    assert res.get("status") == "Accepted"

    # 4. Bugfix : expired_at DOIT repasser à NULL
    async def _is_active_again():
        s = await db.get_snapshot(charger_id, 1, "TxDefaultProfile")
        return s if (s and s["expired_at"] is None) else None

    active_snap = await wait_for(
        _is_active_again, timeout=5,
        message="Bug Sprint 28 : expired_at pas reset sur ré-apply",
    )
    assert active_snap["expired_at"] is None

    # 5. Le limit doit refléter la dernière valeur (12A)
    import json
    pj = (json.loads(active_snap["profile_json"])
          if isinstance(active_snap["profile_json"], str)
          else active_snap["profile_json"])
    limit = pj["charging_schedule"]["charging_schedule_period"][0]["limit"]
    assert float(limit) == 12.0

    # 6. GET /snapshots (include_expired=false) doit le voir
    resp = await api.get_snapshots(charger_id, include_expired=False)
    active = [s for s in resp["snapshots"] if s["purpose"] == "TxDefaultProfile"
              and s["connector_id"] == 1]
    assert len(active) == 1, "Snapshot ré-appliqué invisible sans include_expired"
