"""CertificationRun : state machine async qui exécute une suite.

Workflow :
  1. Crée un row ``CertificationRun`` en DB (status=running).
  2. Filtre les tests selon profils supportés.
  3. Pour chaque test : publie test_started, await run(ctx), publie
     résultat, persiste ``CertificationTestResult``.
  4. À la fin : render rapports HTML+JSON, stocke en DB, publie
     run_finished.

Interruption :
  * Un ``asyncio.Task`` stocké dans _RUNS permet ``cancel_run(run_id)``.
  * Chaque test a un timeout dur (``max_test_seconds=240`` par défaut).
"""
from __future__ import annotations

import asyncio
import json
import time
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select

from db.database import AsyncSessionLocal
from db.models import CertificationRun as CertRunRow
from db.models import CertificationTestResult as CertResultRow

from certification import catalog
from certification.catalog import TestCase, TestResult
from certification.events import EventBus, get_bus, drop_bus
from certification.helpers import (
    TestContext,
    charger_exists,
    get_charger_firmware,
    get_supported_profiles,
)


# ──────────── Registry des runs actifs ────────────
_RUNS: Dict[str, "CertificationRun"] = {}


def get_run(run_id: str) -> Optional["CertificationRun"]:
    return _RUNS.get(run_id)


async def cancel_run(run_id: str) -> bool:
    run = _RUNS.get(run_id)
    if run is None:
        return False
    return await run.cancel()


# ──────────── CertificationRun ────────────


@dataclass
class CertificationRun:
    run_id: str
    charger_id: str
    suite: str
    tests: List[TestCase]
    bus: EventBus
    ocpp_server: Any
    technician_name: Optional[str] = None
    notes: Optional[str] = None
    options: Dict[str, Any] = field(default_factory=dict)
    status: str = "pending"  # running | completed | cancelled | error
    _task: Optional[asyncio.Task] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    results: List[dict] = field(default_factory=list)
    supported_profiles: List[str] = field(default_factory=list)
    firmware_before: Optional[str] = None
    report_html: Optional[str] = None
    report_json: Optional[str] = None

    # ───────────────── lifecycle ─────────────────

    async def start(self) -> None:
        self._task = asyncio.create_task(self._execute())

    async def cancel(self) -> bool:
        if self._task is None or self._task.done():
            return False
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        return True

    # ───────────────── main loop ─────────────────

    async def _execute(self) -> None:
        self.status = "running"
        self.started_at = datetime.now(timezone.utc)
        self.supported_profiles = await get_supported_profiles(self.charger_id)
        self.firmware_before = await get_charger_firmware(self.charger_id)
        filtered = self._filter_tests(self.tests)
        await self._persist_initial()
        await self.bus.publish({
            "event": "run_started",
            "run_id": self.run_id,
            "charger_id": self.charger_id,
            "suite": self.suite,
            "total_tests": len(filtered),
            "supported_profiles": self.supported_profiles,
            "firmware_before": self.firmware_before,
            "technician_name": self.technician_name,
        })

        passed = 0
        failed = 0
        skipped = 0

        try:
            for idx, tc in enumerate(filtered, start=1):
                # Skip si le profil OCPP requis est absent
                if tc.requires_profile and self.supported_profiles and \
                   tc.requires_profile not in self.supported_profiles:
                    res = TestResult(
                        status="skipped",
                        message=f"Profil {tc.requires_profile} non supporté par la borne",
                    )
                    await self._emit_result(idx, tc, res, duration_s=0.0)
                    skipped += 1
                    continue

                await self.bus.publish({
                    "event": "test_started",
                    "index": idx,
                    "total": len(filtered),
                    "name": tc.name,
                    "category": tc.category,
                    "title": tc.title,
                    "description": tc.description,
                    "estimated_seconds": tc.estimated_seconds,
                    "requires_vehicle": tc.requires_vehicle,
                    "requires_power_cycle": tc.requires_power_cycle,
                    "ocpp_ref": tc.ocpp_ref,
                })

                ctx = TestContext(
                    charger_id=self.charger_id,
                    supported_profiles=self.supported_profiles,
                    bus=self.bus,
                    ocpp_server=self.ocpp_server,
                    options=self.options,
                )

                t_start = time.monotonic()
                try:
                    res = await asyncio.wait_for(
                        tc.run(ctx), timeout=self.options.get("max_test_seconds", 240)
                    )
                    if not isinstance(res, TestResult):
                        res = TestResult(
                            status="failed",
                            message=f"Retour inattendu du test : {type(res).__name__}",
                        )
                    # Pousse les details du contexte dans le résultat si vide
                    if not res.details and ctx.details:
                        res.details = ctx.details
                except asyncio.TimeoutError:
                    res = TestResult(
                        status="failed",
                        message=f"Timeout après {self.options.get('max_test_seconds', 240)}s",
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    tb = traceback.format_exc()
                    res = TestResult(
                        status="failed",
                        message=f"Exception : {e}",
                        details={"traceback": tb},
                    )

                duration = time.monotonic() - t_start
                await self._emit_result(idx, tc, res, duration_s=duration)
                if res.status == "passed":
                    passed += 1
                elif res.status == "skipped":
                    skipped += 1
                else:
                    failed += 1

                await self.bus.publish({
                    "event": "progress",
                    "completed": idx,
                    "total": len(filtered),
                    "passed": passed,
                    "failed": failed,
                    "skipped": skipped,
                })

            self.status = "completed"
        except asyncio.CancelledError:
            self.status = "cancelled"
            await self.bus.publish({"event": "log", "level": "warn", "message": "Run annulé par l'utilisateur"})
        except Exception as e:
            self.status = "error"
            await self.bus.publish({"event": "log", "level": "error", "message": f"Erreur runner : {e}"})
        finally:
            self.finished_at = datetime.now(timezone.utc)
            # Génère les rapports
            try:
                from certification.report import render_html, render_json
                ctx_report = {
                    "run_id": self.run_id,
                    "charger_id": self.charger_id,
                    "suite": self.suite,
                    "suite_label": catalog.SUITES.get(self.suite, {}).get("label", self.suite),
                    "technician_name": self.technician_name,
                    "notes": self.notes,
                    "status": self.status,
                    "started_at": self.started_at.isoformat() if self.started_at else None,
                    "finished_at": self.finished_at.isoformat() if self.finished_at else None,
                    "duration_s": (self.finished_at - self.started_at).total_seconds() if self.started_at and self.finished_at else 0,
                    "supported_profiles": self.supported_profiles,
                    "firmware_before": self.firmware_before,
                    "passed": passed,
                    "failed": failed,
                    "skipped": skipped,
                    "total": len(filtered),
                    "results": self.results,
                }
                self.report_json = json.dumps(ctx_report, indent=2, default=str)
                self.report_html = render_html(ctx_report)
            except Exception as e:
                await self.bus.publish({"event": "log", "level": "error", "message": f"Génération rapport échouée : {e}"})

            await self._persist_final(passed, failed, skipped, len(filtered))
            await self.bus.publish({
                "event": "run_finished",
                "status": self.status,
                "passed": passed,
                "failed": failed,
                "skipped": skipped,
                "total": len(filtered),
                "report_url": f"/api/certification/runs/{self.run_id}/report",
                "report_json_url": f"/api/certification/runs/{self.run_id}/report.json",
            })

    # ───────────────── emit / persist ─────────────────

    async def _emit_result(self, idx: int, tc: TestCase, res: TestResult, duration_s: float) -> None:
        event_name = {
            "passed": "test_passed",
            "failed": "test_failed",
            "skipped": "test_skipped",
        }.get(res.status, "test_failed")
        payload = {
            "event": event_name,
            "index": idx,
            "name": tc.name,
            "category": tc.category,
            "title": tc.title,
            "status": res.status,
            "duration_s": round(duration_s, 2),
            "message": res.message,
            "details": res.details,
            "recommendation": res.recommendation,
        }
        if res.status == "skipped":
            payload["reason"] = res.message
        await self.bus.publish(payload)

        self.results.append({
            "index": idx,
            "name": tc.name,
            "category": tc.category,
            "title": tc.title,
            "description": tc.description,
            "ocpp_ref": tc.ocpp_ref,
            "status": res.status,
            "duration_s": round(duration_s, 2),
            "message": res.message,
            "details": res.details,
            "recommendation": res.recommendation,
        })

        # Persiste le résultat individuel
        try:
            async with AsyncSessionLocal() as db:
                row = CertResultRow(
                    run_id=self.run_id,
                    test_name=tc.name,
                    category=tc.category,
                    title=tc.title,
                    status=res.status,
                    started_at=datetime.now(timezone.utc),
                    finished_at=datetime.now(timezone.utc),
                    duration_s=duration_s,
                    message=res.message or "",
                    details_json=json.dumps(res.details, default=str) if res.details else None,
                    recommendation=res.recommendation or "",
                )
                db.add(row)
                await db.commit()
        except Exception:
            pass  # persistance best-effort

    async def _persist_initial(self) -> None:
        try:
            async with AsyncSessionLocal() as db:
                row = CertRunRow(
                    run_id=self.run_id,
                    charger_id=self.charger_id,
                    suite=self.suite,
                    status="running",
                    started_at=self.started_at,
                    technician_name=self.technician_name,
                    notes=self.notes,
                    total=len(self.tests),
                    firmware_before=self.firmware_before,
                    supported_profiles=",".join(self.supported_profiles) if self.supported_profiles else None,
                )
                db.add(row)
                await db.commit()
        except Exception as e:
            await self.bus.publish({"event": "log", "level": "warn", "message": f"DB persist (init) : {e}"})

    async def _persist_final(self, passed: int, failed: int, skipped: int, total: int) -> None:
        try:
            async with AsyncSessionLocal() as db:
                r = await db.execute(select(CertRunRow).where(CertRunRow.run_id == self.run_id))
                row = r.scalar_one_or_none()
                if row is None:
                    return
                row.status = self.status
                row.finished_at = self.finished_at
                row.passed = passed
                row.failed = failed
                row.skipped = skipped
                row.total = total
                row.report_html = self.report_html
                row.report_json = self.report_json
                await db.commit()
        except Exception as e:
            await self.bus.publish({"event": "log", "level": "warn", "message": f"DB persist (final) : {e}"})

    # ───────────────── filtering ─────────────────

    def _filter_tests(self, tests: List[TestCase]) -> List[TestCase]:
        """Respect des options ``no_prompts`` (skip tests qui requièrent prompt)."""
        if not self.options.get("no_prompts"):
            return tests
        out = []
        for t in tests:
            if t.requires_vehicle or t.requires_power_cycle:
                continue
            out.append(t)
        return out


# ────────────────────── public factory ──────────────────────


async def start_run(
    *,
    charger_id: str,
    suite: str,
    ocpp_server: Any,
    tests: Optional[List[str]] = None,
    technician_name: Optional[str] = None,
    notes: Optional[str] = None,
    options: Optional[Dict[str, Any]] = None,
) -> CertificationRun:
    if not await charger_exists(charger_id):
        raise ValueError(f"Borne inconnue : {charger_id}")
    tcs = catalog.get_suite_tests(suite, custom_names=tests)
    if not tcs:
        raise ValueError("Aucun test résolu pour cette suite")

    options = options or {}
    # Quick suite = pas de prompts
    if suite == "quick":
        options.setdefault("no_prompts", True)

    run_id = uuid.uuid4().hex
    bus = get_bus(run_id, create=True)
    run = CertificationRun(
        run_id=run_id,
        charger_id=charger_id,
        suite=suite,
        tests=tcs,
        bus=bus,
        ocpp_server=ocpp_server,
        technician_name=technician_name,
        notes=notes,
        options=options,
    )
    _RUNS[run_id] = run
    await run.start()
    return run
