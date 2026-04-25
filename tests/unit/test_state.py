"""Sprint 31 Volet E — Tests unitaires StateMixin.

Couvre 4 catégories :
  1. `_extract_limit_amps` — extraction courant d'un profil OCPP (statique)
  2. `_normalize_sample` — conversion d'unités MeterValue (classmethod)
  3. `_authorize_id_tag` — whitelist OcppTag (async, DB in-memory)
  4. `ANTI_OVERRIDE_WATCHED_KEYS` — constante anti-override

Exécution isolée (pas de broker WebSocket, pas de borne live). Utilise
`aiosqlite` en mémoire pour la catégorie 3 via une session SQLAlchemy
éphémère.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from core.charge_point.state import StateMixin, ANTI_OVERRIDE_WATCHED_KEYS


# ═══════════════════════════════════════════════════════════════════════
# 1. _extract_limit_amps — statique
# ═══════════════════════════════════════════════════════════════════════
class TestExtractLimitAmps:
    def test_valid_profile(self):
        prof = {
            "charging_profile_id": 99,
            "charging_profile_purpose": "TxDefaultProfile",
            "charging_schedule": {
                "charging_rate_unit": "A",
                "charging_schedule_period": [{"start_period": 0, "limit": 24}],
            },
        }
        assert StateMixin._extract_limit_amps(prof) == 24.0

    def test_float_limit(self):
        prof = {
            "charging_schedule": {
                "charging_schedule_period": [{"start_period": 0, "limit": 16.5}],
            }
        }
        assert StateMixin._extract_limit_amps(prof) == 16.5

    def test_string_limit_convertible(self):
        prof = {
            "charging_schedule": {
                "charging_schedule_period": [{"start_period": 0, "limit": "32"}],
            }
        }
        assert StateMixin._extract_limit_amps(prof) == 32.0

    def test_missing_schedule(self):
        assert StateMixin._extract_limit_amps({}) is None

    def test_empty_periods(self):
        prof = {
            "charging_schedule": {
                "charging_schedule_period": [],
            }
        }
        assert StateMixin._extract_limit_amps(prof) is None

    def test_missing_periods_key(self):
        prof = {"charging_schedule": {"charging_rate_unit": "A"}}
        assert StateMixin._extract_limit_amps(prof) is None

    def test_non_numeric_limit(self):
        prof = {
            "charging_schedule": {
                "charging_schedule_period": [{"start_period": 0, "limit": "abc"}],
            }
        }
        assert StateMixin._extract_limit_amps(prof) is None

    def test_none_profile(self):
        # robustesse : un appelant qui passerait None ne doit pas crasher
        assert StateMixin._extract_limit_amps(None) is None  # type: ignore

    def test_first_period_used(self):
        """Seulement la 1ère période est utilisée (convention S23 A9)."""
        prof = {
            "charging_schedule": {
                "charging_schedule_period": [
                    {"start_period": 0, "limit": 10},
                    {"start_period": 3600, "limit": 20},
                ],
            }
        }
        assert StateMixin._extract_limit_amps(prof) == 10.0


# ═══════════════════════════════════════════════════════════════════════
# 2. _normalize_sample — classmethod (conversion unités)
# ═══════════════════════════════════════════════════════════════════════
class TestNormalizeSample:
    def test_energy_wh_default(self):
        """Wh par défaut (unité de base OCPP §7.22.5)."""
        sv = {"measurand": "Energy.Active.Import.Register",
              "value": "1500", "context": "Sample.Periodic"}
        out = StateMixin._normalize_sample(sv)
        assert out is not None
        field, value, phase = out
        assert field == "energy_wh"
        assert value == 1500.0
        assert phase is None

    def test_energy_kwh_conversion(self):
        """kWh → Wh doit appliquer ×1000."""
        sv = {"measurand": "Energy.Active.Import.Register",
              "value": "1.5", "unit": "kWh"}
        field, value, _ = StateMixin._normalize_sample(sv)
        assert field == "energy_wh"
        assert value == 1500.0

    def test_power_kw_conversion(self):
        """kW → W doit appliquer ×1000."""
        sv = {"measurand": "Power.Active.Import", "value": "7.2", "unit": "kW"}
        field, value, _ = StateMixin._normalize_sample(sv)
        assert field == "power_w"
        assert value == 7200.0

    def test_power_w_as_is(self):
        sv = {"measurand": "Power.Active.Import", "value": "5650", "unit": "W"}
        field, value, _ = StateMixin._normalize_sample(sv)
        assert field == "power_w"
        assert value == 5650.0

    def test_temperature_celsius(self):
        sv = {"measurand": "Temperature", "value": "29", "unit": "Celsius"}
        field, value, _ = StateMixin._normalize_sample(sv)
        assert field == "temperature_c"
        assert value == 29.0

    def test_temperature_kelvin_to_celsius(self):
        sv = {"measurand": "Temperature", "value": "300", "unit": "K"}
        field, value, _ = StateMixin._normalize_sample(sv)
        assert field == "temperature_c"
        assert abs(value - 26.85) < 0.01

    def test_temperature_fahrenheit_to_celsius(self):
        sv = {"measurand": "Temperature", "value": "86", "unit": "F"}
        field, value, _ = StateMixin._normalize_sample(sv)
        assert field == "temperature_c"
        assert abs(value - 30.0) < 0.01

    def test_soc_percent(self):
        sv = {"measurand": "SoC", "value": "72"}
        field, value, _ = StateMixin._normalize_sample(sv)
        assert field == "soc_percent"
        assert value == 72.0

    def test_voltage_v(self):
        sv = {"measurand": "Voltage", "value": "242"}
        field, value, _ = StateMixin._normalize_sample(sv)
        assert field == "voltage_v"
        assert value == 242.0

    def test_phase_l1_returned(self):
        sv = {"measurand": "Current.Import", "value": "16", "phase": "L1"}
        field, value, phase = StateMixin._normalize_sample(sv)
        assert field == "current_a"
        assert value == 16.0
        assert phase == "L1"

    def test_phase_l2_returned(self):
        sv = {"measurand": "Voltage", "value": "240", "phase": "L2"}
        _, _, phase = StateMixin._normalize_sample(sv)
        assert phase == "L2"

    def test_phase_l3_returned(self):
        sv = {"measurand": "Power.Active.Import", "value": "2400", "phase": "L3"}
        _, _, phase = StateMixin._normalize_sample(sv)
        assert phase == "L3"

    def test_unknown_measurand_returns_none(self):
        sv = {"measurand": "ExoticMeasurand", "value": "42"}
        assert StateMixin._normalize_sample(sv) is None

    def test_non_numeric_value_returns_none(self):
        sv = {"measurand": "Power.Active.Import", "value": "not-a-number"}
        assert StateMixin._normalize_sample(sv) is None

    def test_missing_value_returns_none(self):
        sv = {"measurand": "Power.Active.Import"}
        assert StateMixin._normalize_sample(sv) is None

    def test_frequency_hz(self):
        sv = {"measurand": "Frequency", "value": "60.02"}
        field, value, _ = StateMixin._normalize_sample(sv)
        assert field == "frequency_hz"
        assert abs(value - 60.02) < 0.001


# ═══════════════════════════════════════════════════════════════════════
# 3. _authorize_id_tag — async, DB SQLite in-memory
# ═══════════════════════════════════════════════════════════════════════
@pytest.fixture
async def inmem_db(monkeypatch):
    """SQLite aiosqlite en mémoire, remplace AsyncSessionLocal dans state.py."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

    from db.models import Base
    from core.charge_point import state as state_mod

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)

    # Monkey-patch le AsyncSessionLocal importé dans state.py.
    monkeypatch.setattr(state_mod, "AsyncSessionLocal", SessionLocal)

    yield SessionLocal
    await engine.dispose()


class _FakeChargePoint(StateMixin):
    """Stub minimal pour appeler _authorize_id_tag sans instancier tout ChargePoint."""
    def __init__(self, charger_id: str = "TEST-001"):
        self.id = charger_id


class TestAuthorizeIdTag:
    async def test_empty_whitelist_accepts_any(self, inmem_db):
        """Fallback permissif : table vide → Accepted (retrocompat)."""
        cp = _FakeChargePoint()
        info = await cp._authorize_id_tag("ANY-TAG-123")
        assert str(info["status"]) == "Accepted"

    async def test_active_tag_accepted(self, inmem_db):
        """Tag actif sans expiry → Accepted."""
        from db.models import OcppTag
        async with inmem_db() as db:
            db.add(OcppTag(id_tag="ADMIN", active=True))
            await db.commit()

        cp = _FakeChargePoint()
        info = await cp._authorize_id_tag("ADMIN")
        assert str(info["status"]) == "Accepted"

    async def test_unknown_tag_invalid(self, inmem_db):
        """Table non-vide + tag absent → Invalid."""
        from db.models import OcppTag
        async with inmem_db() as db:
            db.add(OcppTag(id_tag="KNOWN", active=True))
            await db.commit()

        cp = _FakeChargePoint()
        info = await cp._authorize_id_tag("UNKNOWN-XYZ")
        assert str(info["status"]) == "Invalid"

    async def test_inactive_tag_blocked(self, inmem_db):
        """Tag active=False → Blocked."""
        from db.models import OcppTag
        async with inmem_db() as db:
            db.add(OcppTag(id_tag="BLOCKED-01", active=False))
            await db.commit()

        cp = _FakeChargePoint()
        info = await cp._authorize_id_tag("BLOCKED-01")
        assert str(info["status"]) == "Blocked"

    async def test_expired_tag_expired(self, inmem_db):
        """Tag avec expiry_date < now → Expired."""
        from db.models import OcppTag
        past = datetime(2020, 1, 1, tzinfo=timezone.utc)
        async with inmem_db() as db:
            db.add(OcppTag(id_tag="EXPIRED-01", active=True, expiry_date=past))
            await db.commit()

        cp = _FakeChargePoint()
        info = await cp._authorize_id_tag("EXPIRED-01")
        assert str(info["status"]) == "Expired"

    async def test_future_expiry_accepted(self, inmem_db):
        """expiry_date > now → Accepted + expiry_date renvoyée."""
        from db.models import OcppTag
        future = datetime.now(timezone.utc) + timedelta(days=30)
        async with inmem_db() as db:
            db.add(OcppTag(id_tag="GUEST", active=True, expiry_date=future))
            await db.commit()

        cp = _FakeChargePoint()
        info = await cp._authorize_id_tag("GUEST")
        assert str(info["status"]) == "Accepted"
        assert "expiry_date" in info

    async def test_parent_id_tag_preserved(self, inmem_db):
        from db.models import OcppTag
        async with inmem_db() as db:
            db.add(OcppTag(id_tag="CHILD", active=True, parent_id_tag="PARENT"))
            await db.commit()

        cp = _FakeChargePoint()
        info = await cp._authorize_id_tag("CHILD")
        assert str(info["status"]) == "Accepted"
        assert info.get("parent_id_tag") == "PARENT"


# ═══════════════════════════════════════════════════════════════════════
# 4. ANTI_OVERRIDE_WATCHED_KEYS — constante
# ═══════════════════════════════════════════════════════════════════════
class TestAntiOverrideWatchedKeys:
    def test_not_empty(self):
        assert len(ANTI_OVERRIDE_WATCHED_KEYS) > 0

    def test_contains_heartbeat_interval(self):
        assert "HeartbeatInterval" in ANTI_OVERRIDE_WATCHED_KEYS

    def test_contains_metervalue_sample_interval(self):
        assert "MeterValueSampleInterval" in ANTI_OVERRIDE_WATCHED_KEYS

    def test_all_entries_are_strings(self):
        assert all(isinstance(k, str) for k in ANTI_OVERRIDE_WATCHED_KEYS)

    def test_no_duplicates(self):
        assert len(ANTI_OVERRIDE_WATCHED_KEYS) == len(set(ANTI_OVERRIDE_WATCHED_KEYS))

    def test_critical_auth_keys_present(self):
        """Les clés d'auth critiques surveillées (anti-cloud manufacturier)."""
        critical = {
            "AuthorizeRemoteTxRequests",
            "LocalAuthorizeOffline",
            "LocalPreAuthorize",
        }
        assert critical.issubset(set(ANTI_OVERRIDE_WATCHED_KEYS))
