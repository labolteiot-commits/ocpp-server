"""Tests Firmware (§6.9 GetDiagnostics, §6.19 UpdateFirmware).

Note : UpdateFirmware est testé avec URL 404 fail-safe pour ne jamais
brick la borne (cf. sprint S32 Volet B).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import desc, select

from certification import catalog
from certification.catalog import TestCase, TestResult
from certification.helpers import TestContext

from db.database import AsyncSessionLocal
from db.models import DiagnosticsRequest, FirmwareUpdate


async def _run_get_diagnostics(ctx: TestContext) -> TestResult:
    if not ctx.supports("FirmwareManagement"):
        return TestResult(
            status="skipped",
            message="Profil FirmwareManagement non supporté",
        )
    cp = ctx.get_cp()
    if cp is None:
        return TestResult(status="failed", message="Borne hors ligne")

    # Location factice (HTTP PUT unreachable) → la borne va tenter puis échouer.
    # Le test mesure la conformité OCPP : CALL accepté + persistance DB.
    location = "http://127.0.0.1:9/diag-certif-dump"
    try:
        result = await cp.get_diagnostics(
            location=location,
            retries=0,
            retry_interval=30,
            start_time=None,
            stop_time=None,
        )
    except Exception as e:
        return TestResult(
            status="failed",
            message=f"GetDiagnostics a levé une exception : {e}",
            recommendation="Vérifier §6.9 GetDiagnostics côté borne.",
        )
    # get_diagnostics retourne un dict {"request_id": ..., "filename": ...}
    if isinstance(result, dict):
        filename = result.get("filename")
    else:
        filename = result
    details = {"filename": filename, "raw": str(result)}
    # Persistance DB — dernière ligne diagnostics_requests
    async with AsyncSessionLocal() as db:
        q = (
            select(DiagnosticsRequest)
            .where(DiagnosticsRequest.charger_id == ctx.charger_id)
            .order_by(desc(DiagnosticsRequest.requested_at))
            .limit(1)
        )
        r = await db.execute(q)
        row = r.scalar_one_or_none()
    details["db_row_present"] = row is not None
    if row is not None:
        details["db_status"] = row.status
    # Un filename = None est acceptable (upload location injoignable). Ce qui
    # compte : CALL parti + row en DB.
    if row is None:
        return TestResult(
            status="failed",
            message="GetDiagnostics n'a créé aucune ligne en DB",
            details=details,
            recommendation="Vérifier le hook de persistance Sprint 29 Volet B.",
        )
    return TestResult(
        status="passed",
        message=f"GetDiagnostics CALL envoyé, ligne persistée (filename={filename})",
        details=details,
    )


catalog.register(
    TestCase(
        name="fw.get_diagnostics",
        category="Firmware",
        title="GetDiagnostics (§6.9)",
        description="Demande un upload diagnostics vers une URL factice et vérifie la persistance DB.",
        run=_run_get_diagnostics,
        requires_profile="FirmwareManagement",
        estimated_seconds=15,
        suites={"full"},
        ocpp_ref="§6.9 GetDiagnostics",
    )
)


# ─────────────────────────── fw.update_firmware_fail_safe ───────────────────────────


async def _run_update_firmware_fail_safe(ctx: TestContext) -> TestResult:
    if not ctx.supports("FirmwareManagement"):
        return TestResult(
            status="skipped",
            message="Profil FirmwareManagement non supporté",
        )
    cp = ctx.get_cp()
    if cp is None:
        return TestResult(status="failed", message="Borne hors ligne")

    # URL volontairement 404 → la borne tente download et échoue = safe.
    location = "http://127.0.0.1:9/firmware-certif-404.bin"
    retrieve_date = (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat()
    try:
        status = await cp.update_firmware(
            location=location,
            retrieve_date=retrieve_date,
            retries=1,
            retry_interval=60,
        )
    except Exception as e:
        return TestResult(
            status="failed",
            message=f"UpdateFirmware a levé une exception : {e}",
        )
    details = {"status": str(status), "location": location}
    # Persistance DB
    async with AsyncSessionLocal() as db:
        q = (
            select(FirmwareUpdate)
            .where(FirmwareUpdate.charger_id == ctx.charger_id)
            .order_by(desc(FirmwareUpdate.requested_at))
            .limit(1)
        )
        r = await db.execute(q)
        row = r.scalar_one_or_none()
    details["db_row_present"] = row is not None
    if row is not None:
        details["db_status"] = row.status
        details["firmware_before"] = row.firmware_version_before

    if str(status).lower() not in {"accepted"}:
        return TestResult(
            status="failed",
            message=f"UpdateFirmware CALL non accepté : {status}",
            details=details,
        )
    if row is None:
        return TestResult(
            status="failed",
            message="UpdateFirmware CALL accepté mais aucune ligne en DB",
            details=details,
            recommendation="Vérifier le hook de persistance Sprint 30 Volet A.",
        )
    return TestResult(
        status="passed",
        message="UpdateFirmware CALL accepté + ligne persistée (fail-safe URL)",
        details=details,
    )


catalog.register(
    TestCase(
        name="fw.update_firmware_fail_safe",
        category="Firmware",
        title="UpdateFirmware fail-safe (§6.19)",
        description="Envoie UpdateFirmware vers URL 404 : vérifie CALL accepté + persistance, pas de brick.",
        run=_run_update_firmware_fail_safe,
        requires_profile="FirmwareManagement",
        estimated_seconds=20,
        suites={"full"},
        ocpp_ref="§6.19 UpdateFirmware",
    )
)
