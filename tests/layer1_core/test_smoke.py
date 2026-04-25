"""Smoke — l'infra répond, les 3 bornes sont connues."""
import pytest

pytestmark = pytest.mark.sim_only


async def test_api_reachable(api):
    chargers = await api.list_chargers()
    assert isinstance(chargers, list)
    ids = {c["id"] for c in chargers}
    assert {"VIRTUAL-001", "BORNE-CABANON4", "GRIZZLE-001"} <= ids, \
        f"Bornes attendues absentes, trouvé {ids}"


async def test_sim_reachable(sim):
    state = await sim.get_state()
    assert state["charger_id"] == "VIRTUAL-001"
    assert "ws_connected" in state
    assert state["ws_connected"] is True, "Sim n'est pas connecté au serveur OCPP"


async def test_db_readable(db, charger_ids):
    c = await db.get_charger(charger_ids["sim"])
    assert c is not None
    assert c["id"] == charger_ids["sim"]


async def test_sim_online_per_server(sim_online, charger_ids):
    """sim_online fixture skip si offline ; sinon charger status != OFFLINE."""
    assert sim_online["id"] == charger_ids["sim"]
