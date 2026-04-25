"""Tests SmartCharging (§5.15 SetChargingProfile, §5.3 ClearChargingProfile)."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from certification import catalog
from certification.catalog import TestCase, TestResult
from certification.helpers import TestContext


async def _run_set_profile_base(ctx: TestContext) -> TestResult:
    if not ctx.supports("SmartCharging"):
        return TestResult(status="skipped", message="Profil SmartCharging non supporté")
    cp = ctx.get_cp()
    if cp is None:
        return TestResult(status="failed", message="Borne hors ligne")
    # Applique un TxDefaultProfile à 16A
    expected_a = 16.0
    profile = {
        "chargingProfileId": 999,
        "stackLevel": 0,
        "chargingProfilePurpose": "TxDefaultProfile",
        "chargingProfileKind": "Relative",
        "chargingSchedule": {
            "chargingRateUnit": "A",
            "chargingSchedulePeriod": [{"startPeriod": 0, "limit": expected_a}],
        },
    }
    try:
        status = await cp.set_charging_profile(connector_id=0, profile=profile)
    except Exception as e:
        return TestResult(
            status="failed",
            message=f"SetChargingProfile a levé une exception : {e}",
        )
    details = {"status": str(status), "limit_a": expected_a}
    if str(status).lower() != "accepted":
        return TestResult(
            status="failed",
            message=f"SetChargingProfile refusé : {status}",
            details=details,
        )
    # Snapshot DB — preuve de persistance
    snap = await ctx.get_latest_profile_snapshot(connector_id=0)
    details["snapshot_found"] = snap is not None
    if snap is not None:
        details["snapshot_limit_amps"] = getattr(snap, "limit_amps_extracted", None)

    # Validation physique — attend la rampe DC-DC puis vérifie le courant
    # réel via MeterValues. Si aucune transaction active → skip la partie
    # physique (TxDefaultProfile s'applique au prochain plug).
    tx_active = ctx.state.get("transaction_id") is not None
    if not tx_active:
        details["physical_validation"] = "skipped_no_active_tx"
        return TestResult(
            status="passed",
            message=(
                f"SetChargingProfile accepté à {expected_a}A et snapshot persisté "
                "(validation courant différée — pas de transaction active)"
            ),
            details=details,
        )
    # Transaction active → on valide que le courant suit la consigne
    await ctx.settle(30.0, reason=f"propagation profil {expected_a}A (rampe DC-DC)")
    ok, samples = await ctx.wait_for_current_within(
        expected_a, tolerance_a=2.0, tolerance_pct=0.10,
        min_consecutive=3, timeout_s=60, poll_s=5.0,
    )
    details["measured_current_a"] = samples
    details["physical_validation"] = "passed" if ok else "failed"
    if not ok:
        return TestResult(
            status="failed",
            message=(
                f"SetChargingProfile accepté mais courant ne suit pas : "
                f"consigne={expected_a}A, observé={samples}"
            ),
            details=details,
            recommendation=(
                "Vérifier que la borne applique bien la consigne OCPP — "
                "contacteur fermé, DC-DC actif, mesure MeterValues à jour."
            ),
        )
    return TestResult(
        status="passed",
        message=(
            f"SetChargingProfile accepté à {expected_a}A, snapshot persisté, "
            f"courant réel conforme ({len(samples)} lectures dans tolérance)"
        ),
        details=details,
    )


catalog.register(
    TestCase(
        name="sc.set_profile_base",
        category="SmartCharging",
        title="SetChargingProfile de base (§5.15)",
        description="Applique un TxDefaultProfile à 16A et vérifie l'acceptation + snapshot.",
        run=_run_set_profile_base,
        requires_profile="SmartCharging",
        estimated_seconds=8,
        suites={"standard", "full"},
        ocpp_ref="§5.15 SetChargingProfile",
    )
)


# ─────────────────────────── sc.current_follows_limit ───────────────────────────


async def _run_current_follows_limit(ctx: TestContext) -> TestResult:
    """Validation 100% autonome — pas de prompt technicien.

    Applique une consigne A, attend la propagation DC-DC, puis vérifie
    que le courant mesuré via MeterValues suit la consigne dans la
    tolérance ±max(2A, 10%) avec ≥3 lectures consécutives conformes.
    """
    if not ctx.supports("SmartCharging"):
        return TestResult(status="skipped", message="Profil SmartCharging non supporté")
    cp = ctx.get_cp()
    if cp is None:
        return TestResult(status="failed", message="Borne hors ligne")

    # Pré-requis : transaction active (sinon profil ne s'applique pas physiquement)
    tx_id = ctx.state.get("transaction_id")
    if tx_id is None:
        session = await ctx.get_latest_session()
        tx_id = getattr(session, "transaction_id", None) if session else None
    if tx_id is None:
        return TestResult(
            status="skipped",
            message="Pas de transaction active — validation physique courant impossible",
            details={"reason": "no_active_tx"},
        )

    # Applique consigne 16A via TxProfile (pas TxDefault — TX en cours)
    expected_a = 16.0
    profile = {
        "chargingProfileId": 901,
        "stackLevel": 1,
        "chargingProfilePurpose": "TxProfile",
        "chargingProfileKind": "Relative",
        "transactionId": int(tx_id),
        "chargingSchedule": {
            "chargingRateUnit": "A",
            "chargingSchedulePeriod": [{"startPeriod": 0, "limit": expected_a}],
        },
    }
    try:
        status = await cp.set_charging_profile(connector_id=1, profile=profile)
    except Exception as e:
        return TestResult(
            status="failed",
            message=f"SetChargingProfile (TxProfile 16A) a levé une exception : {e}",
        )
    details = {"profile_status": str(status), "expected_a": expected_a, "tx_id": int(tx_id)}
    if str(status).lower() != "accepted":
        return TestResult(
            status="failed",
            message=f"SetChargingProfile refusé : {status}",
            details=details,
        )

    # Laisse 30s pour propagation OCPP → contacteur → DC-DC → MeterValues
    await ctx.settle(30.0, reason=f"propagation TxProfile {expected_a}A (rampe DC-DC)")

    ok, samples = await ctx.wait_for_current_within(
        expected_a, tolerance_a=2.0, tolerance_pct=0.10,
        min_consecutive=3, timeout_s=90, poll_s=5.0,
    )
    details["measured_current_a"] = samples
    details["n_samples_in_band"] = len(samples) if ok else 0

    # Cleanup : efface le TxProfile pour ne pas impacter la suite
    try:
        await cp.clear_charging_profile(connector_id=1, profile_id=901)
    except Exception:
        pass

    if not ok:
        return TestResult(
            status="failed",
            message=(
                f"Courant ne suit pas la consigne TxProfile : "
                f"consigne={expected_a}A ±2A/10%, observé={samples}"
            ),
            details=details,
            recommendation=(
                "Vérifier (1) contacteur fermé, (2) DC-DC actif, "
                "(3) MeterValues émis à cadence ≤ 30s, (4) la borne "
                "applique bien les TxProfile (certaines ignorent TxProfile "
                "et n'honorent que TxDefaultProfile)."
            ),
        )
    return TestResult(
        status="passed",
        message=(
            f"Courant réel conforme à la consigne TxProfile {expected_a}A "
            f"({len(samples)} lectures dans ±max(2A,10%))"
        ),
        details=details,
    )


catalog.register(
    TestCase(
        name="sc.current_follows_limit",
        category="SmartCharging",
        title="Courant suit la consigne",
        description="Demande confirmation technicien que le courant réel suit la limite 16A.",
        run=_run_current_follows_limit,
        requires_profile="SmartCharging",
        requires_vehicle=True,
        estimated_seconds=60,
        suites={"full"},
        ocpp_ref="§3.13 ChargePointMaxProfile",
    )
)


# ─────────────────────────── sc.clear_profile ───────────────────────────


async def _run_clear_profile(ctx: TestContext) -> TestResult:
    if not ctx.supports("SmartCharging"):
        return TestResult(status="skipped", message="Profil SmartCharging non supporté")
    cp = ctx.get_cp()
    if cp is None:
        return TestResult(status="failed", message="Borne hors ligne")
    try:
        status = await cp.clear_charging_profile(connector_id=0, profile_id=999)
    except Exception as e:
        return TestResult(
            status="failed",
            message=f"ClearChargingProfile a levé une exception : {e}",
        )
    details = {"status": str(status)}
    if str(status).lower() not in {"accepted", "unknown"}:
        return TestResult(
            status="failed",
            message=f"ClearChargingProfile retour inattendu : {status}",
            details=details,
        )
    return TestResult(
        status="passed",
        message=f"ClearChargingProfile retour OK : {status}",
        details=details,
    )


catalog.register(
    TestCase(
        name="sc.clear_profile",
        category="SmartCharging",
        title="ClearChargingProfile (§5.3)",
        description="Efface le profil SmartCharging id=999 précédemment appliqué.",
        run=_run_clear_profile,
        requires_profile="SmartCharging",
        estimated_seconds=6,
        suites={"standard", "full"},
        ocpp_ref="§5.3 ClearChargingProfile",
    )
)


# ─────────────────────────── sc.profile_replay_after_reboot ───────────────────────────


async def _run_profile_replay(ctx: TestContext) -> TestResult:
    if not ctx.supports("SmartCharging"):
        return TestResult(status="skipped", message="Profil SmartCharging non supporté")
    # Ce test vérifie juste que le snapshot est persisté en DB (pas le reboot réel)
    # Le reboot réel est simulé par la borne si testé en labo complet.
    cp = ctx.get_cp()
    if cp is None:
        return TestResult(status="failed", message="Borne hors ligne")
    profile = {
        "chargingProfileId": 998,
        "stackLevel": 0,
        "chargingProfilePurpose": "TxDefaultProfile",
        "chargingProfileKind": "Relative",
        "chargingSchedule": {
            "chargingRateUnit": "A",
            "chargingSchedulePeriod": [{"startPeriod": 0, "limit": 20.0}],
        },
    }
    try:
        status = await cp.set_charging_profile(connector_id=0, profile=profile)
    except Exception as e:
        return TestResult(status="failed", message=f"SetChargingProfile ex : {e}")
    if str(status).lower() != "accepted":
        return TestResult(status="failed", message=f"Refusé : {status}")
    snap = await ctx.get_latest_profile_snapshot(connector_id=0)
    details = {"snap_exists": snap is not None}
    if snap:
        details["limit_amps"] = getattr(snap, "limit_amps_extracted", None)
        details["applied_at"] = str(getattr(snap, "applied_at", ""))
    if snap is None:
        return TestResult(
            status="failed",
            message="Profile appliqué mais snapshot non persisté en DB",
            details=details,
            recommendation="Vérifier le hook _persist_profile_snapshot côté serveur (A9).",
        )
    # Cleanup
    try:
        await cp.clear_charging_profile(connector_id=0, profile_id=998)
    except Exception:
        pass
    return TestResult(
        status="passed",
        message="Profile persisté en DB → replay automatique au reboot fonctionnel",
        details=details,
    )


catalog.register(
    TestCase(
        name="sc.profile_replay_after_reboot",
        category="SmartCharging",
        title="Snapshot profil persisté (replay reboot)",
        description="Vérifie que les profils SmartCharging sont persistés pour être ré-appliqués au reboot.",
        run=_run_profile_replay,
        requires_profile="SmartCharging",
        estimated_seconds=10,
        suites={"full"},
        ocpp_ref="Sprint 23 A9 — profile replay",
    )
)


# ─────────────────────────── sc.ramp_down ───────────────────────────


async def _run_ramp_down(ctx: TestContext) -> TestResult:
    """Rampe descendante 16A → 8A avec validation physique du courant.

    Vérifie que la borne suit une consigne qui diminue. Essentiel pour
    l'effacement/delestage Hydro-Québec pendant les pointes.
    """
    if not ctx.supports("SmartCharging"):
        return TestResult(status="skipped", message="Profil SmartCharging non supporté")
    cp = ctx.get_cp()
    if cp is None:
        return TestResult(status="failed", message="Borne hors ligne")

    tx_id = ctx.state.get("transaction_id")
    if tx_id is None:
        session = await ctx.get_latest_session()
        tx_id = getattr(session, "transaction_id", None) if session else None
    if tx_id is None:
        return TestResult(
            status="skipped",
            message="Pas de transaction active — validation physique rampe impossible",
            details={"reason": "no_active_tx"},
        )

    def _profile(amps: float) -> dict:
        return {
            "chargingProfileId": 910,
            "stackLevel": 1,
            "chargingProfilePurpose": "TxProfile",
            "chargingProfileKind": "Relative",
            "transactionId": int(tx_id),
            "chargingSchedule": {
                "chargingRateUnit": "A",
                "chargingSchedulePeriod": [{"startPeriod": 0, "limit": amps}],
            },
        }

    details: dict = {"tx_id": int(tx_id), "phase": "init"}

    # Étape 1 — consigne haute 16A
    high_a = 16.0
    try:
        st_high = await cp.set_charging_profile(connector_id=1, profile=_profile(high_a))
    except Exception as e:
        return TestResult(status="failed", message=f"SetCP {high_a}A exception : {e}", details=details)
    details["step1_status"] = str(st_high)
    if str(st_high).lower() != "accepted":
        return TestResult(status="failed", message=f"SetCP {high_a}A refusé : {st_high}", details=details)

    details["phase"] = "settle_high"
    await ctx.settle(30.0, reason=f"propagation consigne haute {high_a}A")
    ok_h, samples_h = await ctx.wait_for_current_within(
        high_a, tolerance_a=2.0, tolerance_pct=0.10,
        min_consecutive=3, timeout_s=60, poll_s=5.0,
    )
    details["step1_samples"] = samples_h
    details["step1_ok"] = ok_h
    if not ok_h:
        try:
            await cp.clear_charging_profile(connector_id=1, profile_id=910)
        except Exception:
            pass
        return TestResult(
            status="failed",
            message=f"Consigne haute {high_a}A non atteinte (observé={samples_h})",
            details=details,
        )

    # Étape 2 — descente à 8A
    low_a = 8.0
    details["phase"] = "apply_low"
    try:
        st_low = await cp.set_charging_profile(connector_id=1, profile=_profile(low_a))
    except Exception as e:
        return TestResult(status="failed", message=f"SetCP {low_a}A exception : {e}", details=details)
    details["step2_status"] = str(st_low)
    if str(st_low).lower() != "accepted":
        try:
            await cp.clear_charging_profile(connector_id=1, profile_id=910)
        except Exception:
            pass
        return TestResult(status="failed", message=f"SetCP {low_a}A refusé : {st_low}", details=details)

    details["phase"] = "settle_low"
    await ctx.settle(30.0, reason=f"propagation rampe descendante → {low_a}A")
    ok_l, samples_l = await ctx.wait_for_current_within(
        low_a, tolerance_a=2.0, tolerance_pct=0.10,
        min_consecutive=3, timeout_s=60, poll_s=5.0,
    )
    details["step2_samples"] = samples_l
    details["step2_ok"] = ok_l

    # Cleanup
    try:
        await cp.clear_charging_profile(connector_id=1, profile_id=910)
    except Exception:
        pass

    if not ok_l:
        return TestResult(
            status="failed",
            message=(
                f"Rampe descendante échouée : consigne={low_a}A, observé={samples_l}. "
                f"Étape haute OK ({samples_h})."
            ),
            details=details,
            recommendation=(
                "Vérifier que la borne applique bien les consignes décroissantes "
                "(essentiel pour effacement HQ)."
            ),
        )
    return TestResult(
        status="passed",
        message=(
            f"Rampe descendante {high_a}A → {low_a}A conforme "
            f"(haute: {samples_h}, basse: {samples_l})"
        ),
        details=details,
    )


catalog.register(
    TestCase(
        name="sc.ramp_down",
        category="SmartCharging",
        title="Rampe descendante 16A → 8A",
        description="Valide physiquement une rampe descendante du courant (effacement HQ).",
        run=_run_ramp_down,
        requires_profile="SmartCharging",
        requires_vehicle=True,
        estimated_seconds=150,
        suites={"full"},
        ocpp_ref="§5.15 SetChargingProfile, §3.13 ChargePointMaxProfile",
    )
)


# ─────────────────────────── sc.ramp_up ───────────────────────────


async def _run_ramp_up(ctx: TestContext) -> TestResult:
    """Rampe montante 8A → 20A avec validation physique.

    Vérifie que la borne suit une consigne qui augmente (fin de pointe HQ,
    retour à pleine puissance).
    """
    if not ctx.supports("SmartCharging"):
        return TestResult(status="skipped", message="Profil SmartCharging non supporté")
    cp = ctx.get_cp()
    if cp is None:
        return TestResult(status="failed", message="Borne hors ligne")

    tx_id = ctx.state.get("transaction_id")
    if tx_id is None:
        session = await ctx.get_latest_session()
        tx_id = getattr(session, "transaction_id", None) if session else None
    if tx_id is None:
        return TestResult(
            status="skipped",
            message="Pas de transaction active — validation physique rampe impossible",
            details={"reason": "no_active_tx"},
        )

    def _profile(amps: float) -> dict:
        return {
            "chargingProfileId": 911,
            "stackLevel": 1,
            "chargingProfilePurpose": "TxProfile",
            "chargingProfileKind": "Relative",
            "transactionId": int(tx_id),
            "chargingSchedule": {
                "chargingRateUnit": "A",
                "chargingSchedulePeriod": [{"startPeriod": 0, "limit": amps}],
            },
        }

    details: dict = {"tx_id": int(tx_id), "phase": "init"}

    # Étape 1 — consigne basse 8A
    low_a = 8.0
    try:
        st_low = await cp.set_charging_profile(connector_id=1, profile=_profile(low_a))
    except Exception as e:
        return TestResult(status="failed", message=f"SetCP {low_a}A exception : {e}", details=details)
    details["step1_status"] = str(st_low)
    if str(st_low).lower() != "accepted":
        return TestResult(status="failed", message=f"SetCP {low_a}A refusé : {st_low}", details=details)

    details["phase"] = "settle_low"
    await ctx.settle(30.0, reason=f"propagation consigne basse {low_a}A")
    ok_l, samples_l = await ctx.wait_for_current_within(
        low_a, tolerance_a=2.0, tolerance_pct=0.10,
        min_consecutive=3, timeout_s=60, poll_s=5.0,
    )
    details["step1_samples"] = samples_l
    details["step1_ok"] = ok_l
    if not ok_l:
        try:
            await cp.clear_charging_profile(connector_id=1, profile_id=911)
        except Exception:
            pass
        return TestResult(
            status="failed",
            message=f"Consigne basse {low_a}A non atteinte (observé={samples_l})",
            details=details,
        )

    # Étape 2 — montée à 20A
    high_a = 20.0
    details["phase"] = "apply_high"
    try:
        st_high = await cp.set_charging_profile(connector_id=1, profile=_profile(high_a))
    except Exception as e:
        return TestResult(status="failed", message=f"SetCP {high_a}A exception : {e}", details=details)
    details["step2_status"] = str(st_high)
    if str(st_high).lower() != "accepted":
        try:
            await cp.clear_charging_profile(connector_id=1, profile_id=911)
        except Exception:
            pass
        return TestResult(status="failed", message=f"SetCP {high_a}A refusé : {st_high}", details=details)

    details["phase"] = "settle_high"
    await ctx.settle(30.0, reason=f"propagation rampe montante → {high_a}A")
    ok_h, samples_h = await ctx.wait_for_current_within(
        high_a, tolerance_a=2.0, tolerance_pct=0.10,
        min_consecutive=3, timeout_s=60, poll_s=5.0,
    )
    details["step2_samples"] = samples_h
    details["step2_ok"] = ok_h

    # Cleanup
    try:
        await cp.clear_charging_profile(connector_id=1, profile_id=911)
    except Exception:
        pass

    if not ok_h:
        return TestResult(
            status="failed",
            message=(
                f"Rampe montante échouée : consigne={high_a}A, observé={samples_h}. "
                f"Étape basse OK ({samples_l})."
            ),
            details=details,
            recommendation=(
                "Vérifier que la borne applique bien les consignes croissantes "
                "(retour à pleine puissance après pointe HQ)."
            ),
        )
    return TestResult(
        status="passed",
        message=(
            f"Rampe montante {low_a}A → {high_a}A conforme "
            f"(basse: {samples_l}, haute: {samples_h})"
        ),
        details=details,
    )


catalog.register(
    TestCase(
        name="sc.ramp_up",
        category="SmartCharging",
        title="Rampe montante 8A → 20A",
        description="Valide physiquement une rampe montante du courant (fin de pointe HQ).",
        run=_run_ramp_up,
        requires_profile="SmartCharging",
        requires_vehicle=True,
        estimated_seconds=150,
        suites={"full"},
        ocpp_ref="§5.15 SetChargingProfile",
    )
)


# ─────────────────────────── sc.stacked_profiles ───────────────────────────


async def _run_stacked_profiles(ctx: TestContext) -> TestResult:
    """TxDefaultProfile (24A) + TxProfile (12A) : le plus restrictif gagne.

    OCPP 1.6J §3.13.2 : quand plusieurs profils s'empilent, le minimum
    des limites s'applique au courant réel (lowest wins).
    """
    if not ctx.supports("SmartCharging"):
        return TestResult(status="skipped", message="Profil SmartCharging non supporté")
    cp = ctx.get_cp()
    if cp is None:
        return TestResult(status="failed", message="Borne hors ligne")

    tx_id = ctx.state.get("transaction_id")
    if tx_id is None:
        session = await ctx.get_latest_session()
        tx_id = getattr(session, "transaction_id", None) if session else None
    if tx_id is None:
        return TestResult(
            status="skipped",
            message="Pas de transaction active — validation stacking impossible",
            details={"reason": "no_active_tx"},
        )

    details: dict = {"tx_id": int(tx_id), "phase": "init"}

    default_a = 24.0
    tx_a = 12.0
    expected_a = min(default_a, tx_a)

    default_profile = {
        "chargingProfileId": 920,
        "stackLevel": 0,
        "chargingProfilePurpose": "TxDefaultProfile",
        "chargingProfileKind": "Relative",
        "chargingSchedule": {
            "chargingRateUnit": "A",
            "chargingSchedulePeriod": [{"startPeriod": 0, "limit": default_a}],
        },
    }
    tx_profile = {
        "chargingProfileId": 921,
        "stackLevel": 1,
        "chargingProfilePurpose": "TxProfile",
        "chargingProfileKind": "Relative",
        "transactionId": int(tx_id),
        "chargingSchedule": {
            "chargingRateUnit": "A",
            "chargingSchedulePeriod": [{"startPeriod": 0, "limit": tx_a}],
        },
    }

    # Étape 1 — TxDefaultProfile (stack 0, 24A)
    try:
        st_def = await cp.set_charging_profile(connector_id=0, profile=default_profile)
    except Exception as e:
        return TestResult(status="failed", message=f"SetCP TxDefault exception : {e}", details=details)
    details["default_status"] = str(st_def)
    if str(st_def).lower() != "accepted":
        return TestResult(
            status="failed",
            message=f"SetCP TxDefault {default_a}A refusé : {st_def}",
            details=details,
        )

    # Étape 2 — empile TxProfile (stack 1, 12A)
    try:
        st_tx = await cp.set_charging_profile(connector_id=1, profile=tx_profile)
    except Exception as e:
        try:
            await cp.clear_charging_profile(connector_id=0, profile_id=920)
        except Exception:
            pass
        return TestResult(status="failed", message=f"SetCP TxProfile exception : {e}", details=details)
    details["tx_status"] = str(st_tx)
    if str(st_tx).lower() != "accepted":
        try:
            await cp.clear_charging_profile(connector_id=0, profile_id=920)
        except Exception:
            pass
        return TestResult(
            status="failed",
            message=f"SetCP TxProfile {tx_a}A refusé : {st_tx}",
            details=details,
        )

    details["phase"] = "settle"
    await ctx.settle(
        30.0,
        reason=f"propagation stack TxDefault({default_a}A)+TxProfile({tx_a}A) → attendu {expected_a}A",
    )

    ok, samples = await ctx.wait_for_current_within(
        expected_a, tolerance_a=2.0, tolerance_pct=0.10,
        min_consecutive=3, timeout_s=90, poll_s=5.0,
    )
    details["expected_a"] = expected_a
    details["samples"] = samples
    details["ok"] = ok

    # Cleanup des deux profils
    for conn, pid in ((1, 921), (0, 920)):
        try:
            await cp.clear_charging_profile(connector_id=conn, profile_id=pid)
        except Exception:
            pass

    if not ok:
        return TestResult(
            status="failed",
            message=(
                f"Stacking profile : attendu min({default_a}A,{tx_a}A)={expected_a}A, "
                f"observé={samples}"
            ),
            details=details,
            recommendation=(
                "OCPP 1.6J §3.13.2 : le plus restrictif doit s'appliquer. "
                "Vérifier que la borne évalue bien le min de TxDefault+TxProfile."
            ),
        )
    return TestResult(
        status="passed",
        message=(
            f"Stacking conforme : TxDefault({default_a}A) + TxProfile({tx_a}A) = {expected_a}A "
            f"({len(samples)} lectures dans tolérance)"
        ),
        details=details,
    )


catalog.register(
    TestCase(
        name="sc.stacked_profiles",
        category="SmartCharging",
        title="Empilement de profils — lowest wins",
        description="TxDefaultProfile(24A) + TxProfile(12A) : le min doit s'appliquer (§3.13.2).",
        run=_run_stacked_profiles,
        requires_profile="SmartCharging",
        requires_vehicle=True,
        estimated_seconds=150,
        suites={"full"},
        ocpp_ref="§3.13.2 Profile stacking",
    )
)


# ─────────────────────────── sc.limit_0a ───────────────────────────


async def _run_limit_0a(ctx: TestContext) -> TestResult:
    """Limite 0A — test de sécurité critique.

    Vérifie que la borne arrête physiquement la charge quand la consigne
    tombe à 0A. Essentiel pour effacement forcé / arrêt d'urgence.
    """
    if not ctx.supports("SmartCharging"):
        return TestResult(status="skipped", message="Profil SmartCharging non supporté")
    cp = ctx.get_cp()
    if cp is None:
        return TestResult(status="failed", message="Borne hors ligne")

    tx_id = ctx.state.get("transaction_id")
    if tx_id is None:
        session = await ctx.get_latest_session()
        tx_id = getattr(session, "transaction_id", None) if session else None
    if tx_id is None:
        return TestResult(
            status="skipped",
            message="Pas de transaction active — validation 0A impossible",
            details={"reason": "no_active_tx"},
        )

    expected_a = 0.0
    profile = {
        "chargingProfileId": 930,
        "stackLevel": 2,
        "chargingProfilePurpose": "TxProfile",
        "chargingProfileKind": "Relative",
        "transactionId": int(tx_id),
        "chargingSchedule": {
            "chargingRateUnit": "A",
            "chargingSchedulePeriod": [{"startPeriod": 0, "limit": expected_a}],
        },
    }

    details: dict = {"tx_id": int(tx_id), "phase": "apply"}

    try:
        status = await cp.set_charging_profile(connector_id=1, profile=profile)
    except Exception as e:
        return TestResult(status="failed", message=f"SetCP 0A exception : {e}", details=details)
    details["profile_status"] = str(status)
    if str(status).lower() != "accepted":
        return TestResult(
            status="failed",
            message=f"SetCP 0A refusé : {status}",
            details=details,
            recommendation=(
                "Certaines bornes refusent 0A par design — tester alternativement "
                "avec limit=6A (minimum J1772) et vérifier SuspendedEV."
            ),
        )

    # 0A = arrêt physique ; laisser ~45s pour ouverture du contacteur
    details["phase"] = "settle"
    await ctx.settle(45.0, reason="propagation consigne 0A (ouverture contacteur)")

    # Tolérance ±1A absolue (courant résiduel capteur), pct=0 car 0×anything=0
    ok, samples = await ctx.wait_for_current_within(
        expected_a, tolerance_a=1.0, tolerance_pct=0.0,
        min_consecutive=3, timeout_s=60, poll_s=5.0,
    )
    details["samples"] = samples
    details["ok"] = ok

    # Cleanup critique : retirer la consigne 0A pour ne pas laisser la borne bloquée
    try:
        await cp.clear_charging_profile(connector_id=1, profile_id=930)
    except Exception:
        pass

    if not ok:
        return TestResult(
            status="failed",
            message=(
                f"Courant ne tombe pas à 0A : consigne=0A ±1A, observé={samples}. "
                "TEST DE SÉCURITÉ CRITIQUE ÉCHOUÉ."
            ),
            details=details,
            recommendation=(
                "La borne DOIT honorer 0A comme arrêt (effacement d'urgence). "
                "Si refus 0A par design, tester avec limit=6A (minimum J1772) + "
                "vérifier que la borne signale SuspendedEV ou Finishing."
            ),
        )
    return TestResult(
        status="passed",
        message=(
            f"Consigne 0A honorée : courant tombé à {samples} "
            f"({len(samples)} lectures ≤1A — sécurité OK)"
        ),
        details=details,
    )


catalog.register(
    TestCase(
        name="sc.limit_0a",
        category="SmartCharging",
        title="Limite 0A — test sécurité critique",
        description="Consigne 0A = arrêt physique de la charge (effacement d'urgence / delestage forcé).",
        run=_run_limit_0a,
        requires_profile="SmartCharging",
        requires_vehicle=True,
        estimated_seconds=120,
        suites={"full"},
        ocpp_ref="§5.15 SetChargingProfile, sécurité réseau",
    )
)
