"""
Harness OCPP — fixtures racine.

Lance pytest depuis /home/lteiot/ocpp-server :
    source .venv/bin/activate
    pytest tests/ -v -m "sim_only"          # safe only
    pytest tests/ -v -m "not requires_vehicle"  # sim + live sans EV
    pytest tests/ -v -m "requires_vehicle"  # avec véhicule branché

Tous les tests couche 1 sont marqués sim_only.
"""
from __future__ import annotations

import asyncio
import os
import httpx
import pytest
import pytest_asyncio

from tests.helpers.api import OCPPServerAPI
from tests.helpers.sim import VirtualChargerAPI
from tests.helpers.db import OCPPDb
from tests.helpers.rawlog import RawLogTail


# ── Configuration (surchargeable via env) ──────────────────────────────────
OCPP_API_URL = os.environ.get("OCPP_API_URL", "http://localhost:8000")
SIM_API_URL = os.environ.get("SIM_API_URL", "http://localhost:8001")
OCPP_DB_PATH = os.environ.get("OCPP_DB_PATH", "/home/lteiot/ocpp-server/data/ocpp.db")
RAW_LOG_DIR = os.environ.get("OCPP_RAW_LOG_DIR", "/home/lteiot/ocpp-server/logs/ocpp_raw")

SIM_CHARGER_ID = "VIRTUAL-001"
TECHNOVE_CHARGER_ID = "BORNE-CABANON4"
GRIZZLE_CHARGER_ID = "GRIZZLE-001"


# ── Fixtures function-scoped (évite problème loop session avec pytest-asyncio 1.x) ──
@pytest_asyncio.fixture
async def api():
    async with OCPPServerAPI(OCPP_API_URL) as client:
        yield client


@pytest_asyncio.fixture
async def sim():
    async with VirtualChargerAPI(SIM_API_URL) as client:
        yield client


@pytest_asyncio.fixture
async def db():
    engine = OCPPDb(OCPP_DB_PATH)
    yield engine
    await engine.close()


# ── Fixtures fonction (rafraîchies) ───────────────────────────────────────
@pytest_asyncio.fixture
async def rawlog_sim():
    """Tailer positionné en fin de fichier → ne voit que les frames post-open."""
    path = f"{RAW_LOG_DIR}/{SIM_CHARGER_ID}.log"
    tail = RawLogTail(path)
    tail.open()
    yield tail
    tail.close()


@pytest_asyncio.fixture
async def clean_sim_state(api: OCPPServerAPI, sim: VirtualChargerAPI):
    """Garantit sim sans véhicule branché, sans fault, profil clear, mode résidentiel."""

    async def _reset():
        state = await sim.get_state()
        if state.get("vehicle_plugged") or state.get("transaction_id"):
            await sim.plug(False)
            await asyncio.sleep(2)
        if state.get("fault_injection"):
            await sim.inject_fault("clear")
        # Effacer tout profil residuel (ex: SetCP(limit=0) d'un stop_charging)
        # pour que le sim reparte à max_current par défaut (80A)
        try:
            await api.clear_charging_profile(SIM_CHARGER_ID, connector_id=0)
            await asyncio.sleep(0.3)
        except Exception:
            pass
        # Restore phase mode residential (en cas de test 3ph)
        try:
            await sim.set_phase_mode("residential_l2")
        except Exception:
            pass
        # Restore availability=Operative si un test precedent a fait ChangeAvailability Inoperative
        try:
            if (state.get("availability") or "").lower() == "inoperative":
                await api.change_availability(SIM_CHARGER_ID, connector_id=0, operative=True)
                await asyncio.sleep(0.3)
        except Exception:
            pass
        # Annuler toutes les reservations ACTIVE orphelines (tests couche 4)
        # pour éviter que _consume_reservation match la mauvaise ligne au StartTx
        try:
            rs = await api.list_reservations(SIM_CHARGER_ID)
            for r in (rs.get("reservations") if isinstance(rs, dict) else rs) or []:
                if (r.get("status") or "").upper() == "ACTIVE":
                    try:
                        await api.cancel_reserve(SIM_CHARGER_ID,
                                                 r["reservation_id"],
                                                 check=False)
                    except Exception:
                        pass
        except Exception:
            pass

    await _reset()
    yield sim
    await _reset()


@pytest_asyncio.fixture
async def sim_online(api: OCPPServerAPI):
    """Skip le test si VIRTUAL-001 pas connecté côté serveur OCPP."""
    charger = await api.get_charger(SIM_CHARGER_ID)
    # status "AVAILABLE" ou autre sauf "OFFLINE"
    if (charger.get("status") or "").upper() == "OFFLINE":
        pytest.skip(f"{SIM_CHARGER_ID} est OFFLINE")
    return charger


# ── Constants exportées pour les tests ────────────────────────────────────
@pytest.fixture(scope="session")
def charger_ids():
    return {
        "sim": SIM_CHARGER_ID,
        "technove": TECHNOVE_CHARGER_ID,
        "grizzle": GRIZZLE_CHARGER_ID,
    }


# ── Seed whitelist (Sprint 29 A) ──────────────────────────────────────────
# Une fois la table `ocpp_tags` non-vide (seeding auto-admin au boot
# serveur, ou CRUD manuel), tout id_tag absent → Authorize=Invalid. Les
# tests utilisent majoritairement id_tag="LOCAL" → on le garantit présent
# et actif avant toute collection de tests. Sync via httpx.Client
# (session-scoped autouse, pas d'event loop requis).
@pytest.fixture(scope="session", autouse=True)
def _seed_test_tags():
    """Garantit que les id_tags utilisés par le harness sont whitelistés."""
    test_tags = ["LOCAL"]
    try:
        with httpx.Client(base_url=OCPP_API_URL, timeout=5.0) as c:
            for tag in test_tags:
                body = {
                    "id_tag": tag,
                    "active": True,
                    "note": f"harness test-tag — seeded by conftest",
                }
                r = c.post("/api/tags", json=body)
                if r.status_code == 409:
                    # Existe déjà — force active=True
                    c.patch(f"/api/tags/{tag}", json={"active": True})
    except Exception:
        # Best-effort : si le serveur est down, les tests eux-mêmes échoueront clairement.
        pass
    yield
