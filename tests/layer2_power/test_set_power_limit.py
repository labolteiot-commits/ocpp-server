"""Couche 2 — POST /set-power-limit : clamp sim en charge + rejet sur-max (P3, P8)."""
from __future__ import annotations

import asyncio
import httpx
import pytest

from tests.helpers.wait import wait_for


pytestmark = [pytest.mark.sim_only]


async def test_set_power_limit_over_max_rejected(api, clean_sim_state,
                                                 sim_online, charger_ids):
    """P8 — max_amps > default_max_amps (80A sim) → HTTP 422."""
    charger_id = charger_ids["sim"]

    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        await api.set_power_limit(charger_id, max_amps=200.0)
    assert exc_info.value.response.status_code == 422, (
        f"Attendu 422, reçu {exc_info.value.response.status_code}"
    )


async def test_set_power_limit_applied_to_sim(api, sim, db, clean_sim_state,
                                              sim_online, charger_ids):
    """P3 — sim branché + transaction active → set-power-limit 10A clampe sim.current_a."""
    charger_id = charger_ids["sim"]

    # Clear profile pré-existant pour partir propre
    try:
        await api.clear_charging_profile(charger_id, connector_id=0)
    except Exception:
        pass
    await asyncio.sleep(0.3)

    # Brancher sim (enclenche StartTransaction côté borne → serveur)
    await sim.plug(True, soc_start=30.0, soc_target=80.0, battery_kwh=60.0)

    # Attendre transaction active (sim expose transaction_id dans /api/state)
    async def _has_tx():
        st = await sim.get_state()
        tx = st.get("transaction_id")
        return st if tx else None

    state = await wait_for(_has_tx, timeout=15, message="Transaction pas démarrée côté sim")
    tx_id = state["transaction_id"]
    assert tx_id

    # Set limit 10A (sous 80A par défaut du sim)
    res = await api.set_power_limit(charger_id, max_amps=10.0)
    assert res.get("status") == "Accepted", f"Limit refusée: {res}"

    # Attendre que le sim clampe conn1.current_a ≤ 10.5 (tolérance jitter ±0.3%)
    async def _current_clamped():
        st = await sim.get_state()
        # Snapshot sim expose conn1_current_a OU current_a selon version —
        # cherche une des deux clés.
        i = st.get("conn1_current_a") or st.get("current_a")
        if i is None:
            # Fallback : chercher dans un sous-dict connectors
            conns = st.get("connectors") or {}
            c1 = conns.get("1") or conns.get(1) or {}
            i = c1.get("current_a")
        return st if (i is not None and i <= 10.5) else None

    clamped = await wait_for(
        _current_clamped, timeout=15, poll=0.5,
        message="Sim n'a pas clampé le courant à ≤10.5A",
    )
    assert clamped is not None

    # Un snapshot actif limit=10 doit exister (TxProfile/TxDefault conn1 OU
    # ChargePointMaxProfile conn0 selon la branche choisie par set_current_limit).
    async def _snap10():
        resp = await api.get_snapshots(charger_id, include_expired=False)
        import json
        for s in resp.get("snapshots", []):
            prof = s.get("profile")
            if isinstance(prof, str):
                prof = json.loads(prof)
            try:
                lim = prof["charging_schedule"]["charging_schedule_period"][0]["limit"]
            except (KeyError, IndexError, TypeError):
                continue
            if abs(float(lim) - 10.0) < 0.01:
                return s
        return None

    snap = await wait_for(_snap10, timeout=10, message="Aucun snapshot limit=10 persisté")
    assert snap is not None, "Aucun snapshot actif avec limit=10 après set-power-limit"

    # Stop côté sim (débranche)
    await sim.plug(False)
    await asyncio.sleep(1.0)
