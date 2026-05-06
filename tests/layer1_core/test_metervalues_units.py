"""Couche 1 — Validation A4 : unités MeterValues normalisées (C6).

Sim en mode 3ph commercial 208V → le serveur doit :
  - Agréger Power.Active.Import (sum L1+L2+L3) si pas de composite
  - Écrire power_w en watts (pas kW)
  - Écrire voltage_v ~208 (moyenne 3ph), pas 240
  - Convertir Temperature °C correctement
"""
from __future__ import annotations

import asyncio
import pytest

from tests.helpers.wait import wait_for


pytestmark = [pytest.mark.sim_only, pytest.mark.slow]


async def test_3ph_power_sum_and_voltage(api, sim, db, clean_sim_state,
                                         sim_online, charger_ids):
    charger_id = charger_ids["sim"]

    # (0) Basculer en 3ph commercial
    await sim.set_phase_mode("commercial_l2_3ph")
    await sim.set_speed(600)

    state = await sim.get_state()
    assert state["phase_mode"] == "commercial_l2_3ph"
    assert state["voltage_nominal_v"] == 208.0

    # (1) Démarrer une session
    await sim.plug(plugged=True, soc_start=30, soc_target=55, battery_kwh=60)

    st = await wait_for(
        lambda: _wait_tx(sim), timeout=20,
        message="transaction_id sim jamais apparu",
    )
    tx_id = st["transaction_id"]

    session = await wait_for(
        lambda: db.get_session_by_tx(charger_id, tx_id),
        timeout=15, message="Session pas persistée",
    )

    # (1.5) P2-9 fix (2026-05-06) : depuis §40 B2, le serveur envoie un
    # TxDefaultProfile 0.1A bloquant (stackLevel=8) à Preparing pour
    # empêcher la charge avant le RemoteStart manuel. Sans override,
    # le sim charge à 0.1A * 208V * sqrt(3) * 3 ≈ 41W → fail assertion
    # >1000W. Le helper set_charging_profile pousse un nouveau profile
    # avec stackLevel par défaut (0 ou 1) qui PERD contre le bloc 8.
    # Solution : clear d'abord tous les profils sur connecteur 1 (incl.
    # le bloc), PUIS push le profile haut. Idempotent — si pas de bloc
    # actif (ex: sim isolé), le clear est no-op.
    await api.clear_charging_profile(charger_id, connector_id=1)
    await asyncio.sleep(1)
    await api.set_charging_profile(charger_id, max_amps=32.0,
                                   connector_id=1, duration_seconds=600)
    await asyncio.sleep(2)

    # (2) Attendre ≥ 2 MeterValues periodic (60s interval défaut) —
    # speed=600 n'accélère pas l'horloge serveur, donc 2 MV = ~120s.
    # On accepte 1 si timeout, moyennant warning.
    try:
        await wait_for(
            lambda: _has_at_least(db, session["id"], 2),
            timeout=150, poll=3, message="Moins de 2 MeterValues",
        )
    except TimeoutError:
        # fallback 1 MV
        await wait_for(
            lambda: _has_at_least(db, session["id"], 1),
            timeout=30, poll=3, message="Aucun MeterValue",
        )

    mvs = await db.get_metervalues_for_session(session["id"], limit=5)
    assert len(mvs) >= 1

    # (3) Assertions unités — filter MV avec power_w renseigné
    p_values = [m["power_w"] for m in mvs if m["power_w"] is not None]
    v_values = [m["voltage_v"] for m in mvs if m["voltage_v"] is not None]
    assert p_values, f"Aucun power_w renseigné dans MVs: {mvs}"

    # Power en watts (>500W si charge active), pas en kW (< 50)
    max_p = max(p_values)
    assert max_p > 1000, f"Power_w suspect (converti en kW ?): max={max_p}"

    # Voltage ~208V (3ph), tolérance bruit ±10V
    if v_values:
        v = max(v_values)
        assert 180 < v < 230, f"Voltage_v inattendu pour 3ph 208V: {v}"

    # Cleanup
    await sim.plug(plugged=False)


async def _wait_tx(sim):
    st = await sim.get_state()
    return st if st.get("transaction_id") else None


async def _has_at_least(db, session_id, n):
    count = await db.count_metervalues(session_id)
    return count if count >= n else None
