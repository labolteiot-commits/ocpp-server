"""Couche 3 — RemoteStart + RemoteStop (R1, R2, R3)."""
from __future__ import annotations

import asyncio
import pytest

from tests.helpers.wait import wait_for


pytestmark = [pytest.mark.sim_only]


async def test_remote_start_creates_transaction(api, db, rawlog_sim,
                                                clean_sim_state, sim_online,
                                                charger_ids):
    """R1 — RemoteStart → CALL OCPP + StartTransaction borne + session DB active."""
    charger_id = charger_ids["sim"]

    active = await api.active_transaction(charger_id)
    assert active["transaction_id"] is None, "TX déjà active avant le test"

    result = await api.remote_start(charger_id, id_tag="ADMIN", connector_id=1)
    assert "Démarrage" in result.get("detail", ""), f"Refusé: {result}"

    # CALL RemoteStartTransaction (serveur → borne)
    frame = await rawlog_sim.wait_for(
        action="RemoteStartTransaction", direction="send", msg_type=2, timeout=5,
    )
    assert frame.payload.get("connectorId") == 1

    # StartTransaction envoyé par la borne (borne → serveur)
    # direction="receive" car parse_line normalise "receive message" → "receive"
    await rawlog_sim.wait_for(
        action="StartTransaction", direction="receive", msg_type=2, timeout=15,
    )

    # Session active en DB
    async def _has_active_session():
        s = await db.get_latest_session(charger_id)
        return s if (s and s.get("stop_time") is None) else None

    session = await wait_for(
        _has_active_session, timeout=10,
        message="Aucune session active en DB après RemoteStart",
    )
    assert session["id_tag"] == "ADMIN"
    assert session["transaction_id"] is not None


async def test_remote_stop_closes_transaction(api, sim, db, rawlog_sim,
                                              clean_sim_state, sim_online,
                                              charger_ids):
    """R2 — sim branché → RemoteStop → commandes envoyées + session fermée en DB.

    stop_charging() envoie SetChargingProfile(limit=0) puis RemoteStopTransaction.
    Le sim rejette RemoteStopTransaction (charging=False après SetCP(0)), donc
    StopTransaction vient par débranchement sim (EVDisconnected).
    """
    charger_id = charger_ids["sim"]

    await sim.plug(True, soc_start=30.0, soc_target=80.0, battery_kwh=60.0)

    async def _has_tx():
        st = await sim.get_state()
        return st.get("transaction_id")

    tx_id = await wait_for(_has_tx, timeout=15, message="Transaction sim absente")

    result = await api.remote_stop(charger_id, transaction_id=tx_id, lock=False)
    assert result.get("detail") in ("Arrêt effectué", "Arrêt partiel"), f"Stop: {result}"

    # Étape 1 : SetChargingProfile(limit=0) coupe le courant physiquement
    await rawlog_sim.wait_for(
        action="SetChargingProfile", direction="send", msg_type=2, timeout=8,
    )

    # Étape 2 : RemoteStopTransaction
    await rawlog_sim.wait_for(
        action="RemoteStopTransaction", direction="send", msg_type=2, timeout=10,
    )

    # Débrancher pour déclencher le StopTransaction (EVDisconnected)
    await sim.plug(False)

    # Session fermée en DB une fois StopTransaction reçu
    async def _session_closed():
        s = await db.get_latest_session(charger_id)
        return s if (s and s.get("stop_time") is not None) else None

    closed = await wait_for(
        _session_closed, timeout=15,
        message="Session pas fermée en DB après débranchement",
    )
    assert closed["transaction_id"] == tx_id
    assert closed["energy_wh"] is not None and closed["energy_wh"] >= 0


async def test_remote_stop_with_lock(api, sim, rawlog_sim,
                                     clean_sim_state, sim_online, charger_ids):
    """R3 — RemoteStop lock=True → borne passe Unavailable."""
    charger_id = charger_ids["sim"]

    await sim.plug(True, soc_start=20.0)

    async def _has_tx():
        st = await sim.get_state()
        return st.get("transaction_id")

    tx_id = await wait_for(_has_tx, timeout=15, message="Transaction sim absente")

    # Arrêt avec lock ; transaction_id obligatoire (int)
    result = await api.remote_stop(charger_id, transaction_id=tx_id, lock=True)
    assert result.get("locked") is True, f"Lock pas confirmé: {result}"

    async def _is_unavailable():
        st = await sim.get_state()
        status = (st.get("conn1_status") or "").lower()
        return st if status in ("unavailable", "inoperative") else None

    await wait_for(
        _is_unavailable, timeout=20, poll=0.5,
        message="Sim pas passé Unavailable après lock",
    )

    # Restaurer
    await api.change_availability(charger_id, connector_id=0, operative=True)
    await asyncio.sleep(1.0)
