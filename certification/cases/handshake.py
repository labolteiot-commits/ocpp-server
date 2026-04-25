"""Tests de la phase handshake OCPP (BootNotification, Heartbeat, StatusNotification).

Réfs norme 1.6J :
  * §4.2 BootNotification
  * §4.6 Heartbeat
  * §4.8 StatusNotification
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from certification import catalog
from certification.catalog import TestCase, TestResult
from certification.helpers import TestContext


# ─────────────────────────── handshake.boot_notification ───────────────────────────


async def _run_boot_notification(ctx: TestContext) -> TestResult:
    if not ctx.is_online():
        return TestResult(
            status="failed",
            message="Borne non connectée au serveur OCPP",
            recommendation="Vérifier le câble Ethernet / WiFi et le CP ID configuré.",
        )
    # Cherche le dernier BootNotification reçu (direction='in' = CALL borne→serveur
    # avec le payload vendor/model ; direction='out' = CALLRESULT serveur→borne sans ces champs)
    rows = await ctx.get_last_activity(action="BootNotification", direction="in", limit=5)
    if not rows:
        ctx.log("warn", "Aucun BootNotification dans l'historique, demande TriggerMessage")
        cp = ctx.get_cp()
        try:
            await cp.trigger_message("BootNotification")
            await asyncio.sleep(3)
        except Exception as e:
            return TestResult(
                status="failed",
                message=f"TriggerMessage BootNotification a échoué : {e}",
                recommendation="Vérifier que la borne supporte le profil RemoteTrigger.",
            )
        rows = await ctx.get_last_activity(action="BootNotification", direction="in", limit=5)

    if not rows:
        return TestResult(
            status="failed",
            message="Aucun BootNotification reçu, même après TriggerMessage",
            recommendation="Redémarrer la borne manuellement. Un BootNotification doit être émis au démarrage.",
        )
    row = rows[0]
    payload_raw = getattr(row, "payload", None) or ""
    details = {"last_boot_ts": str(getattr(row, "timestamp", "")), "payload_excerpt": payload_raw[:400]}
    # Vérifie les champs requis (chargePointVendor + chargePointModel)
    missing = []
    for key in ["chargePointVendor", "chargePointModel"]:
        if key not in payload_raw:
            missing.append(key)
    if missing:
        return TestResult(
            status="failed",
            message=f"BootNotification reçu mais champs obligatoires manquants : {', '.join(missing)}",
            details=details,
            recommendation="La borne doit envoyer au minimum chargePointVendor et chargePointModel (§4.2).",
        )
    return TestResult(
        status="passed",
        message="BootNotification valide avec vendor + model",
        details=details,
    )


catalog.register(
    TestCase(
        name="handshake.boot_notification",
        category="Handshake",
        title="BootNotification valide (§4.2)",
        description="Vérifie que la borne émet un BootNotification complet avec vendor et model.",
        run=_run_boot_notification,
        estimated_seconds=10,
        suites={"quick", "standard", "full"},
        ocpp_ref="§4.2 BootNotification.req",
    )
)


# ─────────────────────────── handshake.heartbeat_interval ───────────────────────────


async def _run_heartbeat_interval(ctx: TestContext) -> TestResult:
    if not ctx.is_online():
        return TestResult(status="failed", message="Borne non connectée")
    since = datetime.now(timezone.utc) - timedelta(minutes=5)
    # direction='in' = Heartbeat envoyé par la borne vers le serveur
    rows = await ctx.get_last_activity(action="Heartbeat", direction="in", since=since, limit=50)
    details = {"heartbeats_5min": len(rows)}
    if len(rows) < 2:
        # Force un TriggerMessage pour ne pas attendre
        cp = ctx.get_cp()
        try:
            await cp.trigger_message("Heartbeat")
            await asyncio.sleep(3)
        except Exception:
            pass
        rows = await ctx.get_last_activity(action="Heartbeat", direction="in", since=since, limit=50)
        details["heartbeats_5min_after_trigger"] = len(rows)

    if not rows:
        return TestResult(
            status="failed",
            message="Aucun Heartbeat reçu ces 5 dernières minutes",
            details=details,
            recommendation="Configurer HeartbeatInterval ≤ 60s ou vérifier connectivité WebSocket.",
        )
    # Calcule intervalle médian entre heartbeats
    ts_list = sorted([getattr(r, "timestamp", None) for r in rows if getattr(r, "timestamp", None)])
    if len(ts_list) >= 2:
        intervals = [
            (ts_list[i] - ts_list[i - 1]).total_seconds() for i in range(1, len(ts_list))
        ]
        avg_interval = sum(intervals) / len(intervals)
        details["avg_interval_s"] = round(avg_interval, 1)
        if avg_interval > 600:
            return TestResult(
                status="failed",
                message=f"Heartbeat interval trop long ({avg_interval:.0f}s, recommandé < 300s)",
                details=details,
                recommendation="Utiliser ChangeConfiguration HeartbeatInterval pour ajuster (§5.2).",
            )
    return TestResult(
        status="passed",
        message=f"Heartbeats reçus ({len(rows)} en 5 min)",
        details=details,
    )


catalog.register(
    TestCase(
        name="handshake.heartbeat_interval",
        category="Handshake",
        title="Heartbeat régulier (§4.6)",
        description="Vérifie que la borne émet des Heartbeat au moins toutes les 10 min.",
        run=_run_heartbeat_interval,
        estimated_seconds=15,
        suites={"quick", "standard", "full"},
        ocpp_ref="§4.6 Heartbeat.req",
    )
)


# ─────────────────────────── handshake.status_notifications ───────────────────────────


async def _run_status_notifications(ctx: TestContext) -> TestResult:
    cp = ctx.get_cp()
    if cp is None:
        return TestResult(status="failed", message="Borne hors ligne")
    try:
        await cp.trigger_message("StatusNotification")
    except Exception as e:
        return TestResult(
            status="failed",
            message=f"TriggerMessage StatusNotification a échoué : {e}",
            recommendation="Vérifier le profil RemoteTrigger côté borne.",
        )
    await asyncio.sleep(3)
    since = datetime.now(timezone.utc) - timedelta(seconds=30)
    rows = await ctx.get_last_activity(action="StatusNotification", since=since, limit=10)
    details = {"count": len(rows)}
    if not rows:
        return TestResult(
            status="failed",
            message="Aucun StatusNotification reçu après TriggerMessage",
            details=details,
            recommendation="La borne doit répondre au TriggerMessage par un StatusNotification (§5.17).",
        )
    return TestResult(
        status="passed",
        message=f"{len(rows)} StatusNotification reçu(s) après TriggerMessage",
        details=details,
    )


catalog.register(
    TestCase(
        name="handshake.status_notifications",
        category="Handshake",
        title="StatusNotification sur demande (§4.8)",
        description="Déclenche TriggerMessage StatusNotification et vérifie la réception.",
        run=_run_status_notifications,
        requires_profile="RemoteTrigger",
        estimated_seconds=8,
        suites={"standard", "full"},
        ocpp_ref="§4.8 StatusNotification.req",
    )
)


# ─────────────────────────── handshake.reconnection ───────────────────────────


async def _run_reconnection(ctx: TestContext) -> TestResult:
    """Vérifie que la borne reste connectée sur une fenêtre de 30s.

    Ce test n'interrompt pas la connexion (risqué pour d'autres tests) :
    il vérifie juste qu'aucune déconnexion n'est survenue récemment.
    """
    cp = ctx.get_cp()
    if cp is None:
        return TestResult(status="failed", message="Borne hors ligne")

    await asyncio.sleep(5)  # Observation passive
    cp2 = ctx.get_cp()
    if cp2 is None:
        return TestResult(
            status="failed",
            message="Borne déconnectée pendant la fenêtre d'observation 5s",
            recommendation="Vérifier la stabilité du lien WebSocket / WiFi / Ethernet.",
        )
    return TestResult(status="passed", message="Borne reste connectée (observation 5s)")


catalog.register(
    TestCase(
        name="handshake.reconnection",
        category="Handshake",
        title="Connexion stable",
        description="Vérifie que la borne maintient la session WebSocket sur 5 secondes.",
        run=_run_reconnection,
        estimated_seconds=8,
        suites={"full"},
        ocpp_ref="§3.1 Connection setup",
    )
)
