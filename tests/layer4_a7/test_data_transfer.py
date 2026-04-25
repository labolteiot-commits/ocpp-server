"""Couche 4 — DataTransfer §6.6 Server→CP + audit log.

Sprint 31 livre le POST /api/commands/data-transfer/send qui :
1. Appelle `cp.data_transfer(vendor_id, message_id, data)` — CALL OCPP.
2. Persiste la transaction dans `data_transfer_logs` avec direction="out"
   et le status/data renvoyés par la borne.

Le simulateur `virtual_charger.py::on_data_transfer` accepte tout et renvoie
`status=Accepted, data=None`.
"""
from __future__ import annotations

import uuid

import pytest

from tests.helpers.wait import wait_for


pytestmark = [pytest.mark.sim_only]


async def test_data_transfer_send_persists_out_log(api, rawlog_sim,
                                                   clean_sim_state, sim_online,
                                                   charger_ids):
    """D1 — Send DataTransfer → CALL OCPP + audit persisté direction=out."""
    charger_id = charger_ids["sim"]
    vendor = "com.test.layer4"
    msg_id = f"ping-{uuid.uuid4().hex[:8]}"
    payload = "hello-harness"

    r = await api.send_data_transfer(charger_id, vendor_id=vendor,
                                     message_id=msg_id, data=payload)
    assert r.status_code == 200, f"HTTP {r.status_code}: {r.text}"
    body = r.json()
    assert body["vendor_id"] == vendor
    assert body["message_id"] == msg_id

    # CALL DataTransfer (serveur → borne)
    frame = await rawlog_sim.wait_for(
        action="DataTransfer", direction="send", msg_type=2, timeout=5,
    )
    # OCPP 1.6 payload keys en camelCase
    assert frame.payload.get("vendorId") == vendor
    assert frame.payload.get("messageId") == msg_id

    # Audit log direction=out
    async def _find_in_logs():
        logs = await api.data_transfer_logs(
            charger_id=charger_id, direction="out", limit=50,
        )
        for entry in logs.get("logs", []):
            if entry.get("message_id") == msg_id:
                return entry
        return None

    entry = await wait_for(_find_in_logs, timeout=5,
                           message="Audit log direction=out manquant")
    assert entry["vendor_id"] == vendor
    assert entry["direction"] == "out"
    # Data peut être stockée telle quelle (string) ou sérialisée — tolérant
    assert payload in str(entry.get("data"))


async def test_data_transfer_filter_by_direction(api, clean_sim_state,
                                                 sim_online, charger_ids):
    """D2 — Filtrage direction=out ne renvoie que les entrées sortantes."""
    charger_id = charger_ids["sim"]
    vendor = "com.test.layer4"
    msg_id = f"filter-{uuid.uuid4().hex[:8]}"

    # Créer au moins une entrée out
    r = await api.send_data_transfer(charger_id, vendor_id=vendor,
                                     message_id=msg_id, data="filter-test")
    assert r.status_code == 200

    logs = await api.data_transfer_logs(charger_id=charger_id,
                                        direction="out", limit=50)
    assert logs["count"] >= 1
    for entry in logs["logs"]:
        assert entry["direction"] == "out", \
            f"Entrée direction!=out dans filtre out: {entry}"


async def test_data_transfer_limit_capped(api, clean_sim_state,
                                          sim_online, charger_ids):
    """D3 — Paramètre limit respecté (validation basique 1..500)."""
    charger_id = charger_ids["sim"]

    # S'assurer qu'il y a au moins 2 entrées
    for i in range(2):
        r = await api.send_data_transfer(
            charger_id, vendor_id="com.test.limit",
            message_id=f"seed-{i}-{uuid.uuid4().hex[:6]}",
            data=f"row{i}",
        )
        assert r.status_code == 200

    logs = await api.data_transfer_logs(charger_id=charger_id, limit=1)
    assert logs["count"] == 1, f"Limit=1 non respecté: count={logs['count']}"
