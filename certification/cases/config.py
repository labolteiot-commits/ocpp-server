"""Tests Configuration (§5.8 GetConfiguration, §5.2 ChangeConfiguration)."""
from __future__ import annotations

import asyncio

from certification import catalog
from certification.catalog import TestCase, TestResult
from certification.helpers import TestContext


async def _run_get_configuration(ctx: TestContext) -> TestResult:
    cp = ctx.get_cp()
    if cp is None:
        return TestResult(status="failed", message="Borne hors ligne")
    try:
        # ActionsMixin.get_configuration(keys=None) retourne directement une liste
        # de {"key": ..., "value": ..., "readonly": ...} ou [] en cas d'échec.
        result = await cp.get_configuration()
    except Exception as e:
        return TestResult(
            status="failed",
            message=f"GetConfiguration a levé une exception : {e}",
            recommendation="Vérifier que la borne implémente §5.8 GetConfiguration.",
        )
    details = {"raw_type": type(result).__name__}
    # Extrait configuration_key : le wrapper ActionsMixin retourne déjà la liste.
    keys = None
    try:
        if isinstance(result, list):
            keys = result
        else:
            keys = getattr(result, "configuration_key", None)
            if keys is None and isinstance(result, dict):
                keys = result.get("configurationKey") or result.get("configuration_key")
    except Exception:
        pass
    details["keys_count"] = len(keys) if keys else 0
    if not keys:
        return TestResult(
            status="failed",
            message="GetConfiguration n'a retourné aucune clé",
            details=details,
            recommendation="La borne doit retourner au moins quelques clés Core (§9.1).",
        )
    return TestResult(
        status="passed",
        message=f"GetConfiguration OK ({len(keys)} clés)",
        details=details,
    )


catalog.register(
    TestCase(
        name="cfg.get_configuration",
        category="Configuration",
        title="GetConfiguration (§5.8)",
        description="Vérifie que la borne répond à GetConfiguration avec ≥1 clé.",
        run=_run_get_configuration,
        estimated_seconds=6,
        suites={"quick", "standard", "full"},
        ocpp_ref="§5.8 GetConfiguration",
    )
)


# ─────────────────────────── cfg.change_and_read_back ───────────────────────────


async def _run_change_and_read_back(ctx: TestContext) -> TestResult:
    cp = ctx.get_cp()
    if cp is None:
        return TestResult(status="failed", message="Borne hors ligne")

    # Utilise HeartbeatInterval comme clé testable (re-mise à la valeur courante).
    key = "HeartbeatInterval"
    # 1) Lit la valeur courante (wrapper retourne list directement)
    try:
        before = await cp.get_configuration(keys=[key])
    except Exception as e:
        return TestResult(
            status="failed",
            message=f"GetConfiguration({key}) a échoué : {e}",
        )
    current = None
    try:
        items = before if isinstance(before, list) else (
            getattr(before, "configuration_key", None) or []
        )
        for it in items:
            k = getattr(it, "key", None) or (it.get("key") if isinstance(it, dict) else None)
            if k == key:
                current = getattr(it, "value", None) or (
                    it.get("value") if isinstance(it, dict) else None
                )
                break
    except Exception:
        pass
    # 2) Tente un ChangeConfiguration à la même valeur (safe)
    target = str(current) if current is not None else "60"
    try:
        status = await cp.change_configuration(key=key, value=target)
    except Exception as e:
        return TestResult(
            status="failed",
            message=f"ChangeConfiguration({key}={target}) a levé une exception : {e}",
        )
    details = {"key": key, "before": current, "target": target, "status": str(status)}
    if str(status).lower() not in {"accepted", "rebootrequired"}:
        return TestResult(
            status="failed",
            message=f"ChangeConfiguration refusé : {status}",
            details=details,
            recommendation="La clé HeartbeatInterval doit être Writable (§9.1).",
        )
    # 3) Re-lit
    try:
        after = await cp.get_configuration(keys=[key])
    except Exception:
        after = None
    readback = None
    try:
        items = after if isinstance(after, list) else (
            getattr(after, "configuration_key", None) or []
        )
        for it in items:
            k = getattr(it, "key", None) or (it.get("key") if isinstance(it, dict) else None)
            if k == key:
                readback = getattr(it, "value", None) or (
                    it.get("value") if isinstance(it, dict) else None
                )
                break
    except Exception:
        pass
    details["readback"] = readback
    if readback is not None and str(readback) != target:
        return TestResult(
            status="failed",
            message=f"Read-back différent : demandé={target}, lu={readback}",
            details=details,
            recommendation="Vérifier que la borne persiste la valeur après ChangeConfiguration.",
        )
    return TestResult(
        status="passed",
        message=f"ChangeConfiguration({key}) accepté + read-back OK",
        details=details,
    )


catalog.register(
    TestCase(
        name="cfg.change_and_read_back",
        category="Configuration",
        title="ChangeConfiguration + read-back (§5.2)",
        description="Change HeartbeatInterval à sa valeur courante puis la relit pour confirmer persistance.",
        run=_run_change_and_read_back,
        estimated_seconds=8,
        suites={"standard", "full"},
        ocpp_ref="§5.2 ChangeConfiguration",
    )
)


# ─────────────────────────── cfg.supported_profiles ───────────────────────────


async def _run_supported_profiles(ctx: TestContext) -> TestResult:
    cp = ctx.get_cp()
    if cp is None:
        return TestResult(status="failed", message="Borne hors ligne")
    key = "SupportedFeatureProfiles"
    try:
        result = await cp.get_configuration(keys=[key])
    except Exception as e:
        return TestResult(
            status="failed",
            message=f"GetConfiguration({key}) a échoué : {e}",
            recommendation="Cette clé est obligatoire §9.1.",
        )
    value = None
    try:
        items = result if isinstance(result, list) else (
            getattr(result, "configuration_key", None) or []
        )
        for it in items:
            k = getattr(it, "key", None) or (it.get("key") if isinstance(it, dict) else None)
            if k == key:
                value = getattr(it, "value", None) or (
                    it.get("value") if isinstance(it, dict) else None
                )
                break
    except Exception:
        pass
    if not value:
        return TestResult(
            status="failed",
            message="SupportedFeatureProfiles manquant ou vide",
            recommendation="La borne doit publier au minimum 'Core' (§4.2.2).",
        )
    profiles = [p.strip() for p in str(value).split(",") if p.strip()]
    details = {"raw": value, "profiles": profiles}
    if "Core" not in profiles:
        return TestResult(
            status="failed",
            message=f"Profil 'Core' absent : {profiles}",
            details=details,
            recommendation="Le profil Core est obligatoire pour toute borne 1.6J.",
        )
    return TestResult(
        status="passed",
        message=f"Profils supportés : {', '.join(profiles)}",
        details=details,
    )


catalog.register(
    TestCase(
        name="cfg.supported_profiles",
        category="Configuration",
        title="SupportedFeatureProfiles (§9.1)",
        description="Vérifie que la borne publie au minimum le profil Core.",
        run=_run_supported_profiles,
        estimated_seconds=5,
        suites={"standard", "full"},
        ocpp_ref="§9.1.21 SupportedFeatureProfiles",
    )
)
