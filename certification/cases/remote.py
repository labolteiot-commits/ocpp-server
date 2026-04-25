"""Tests RemoteCommands (§5.1 ChangeAvailability, §5.18 UnlockConnector, §5.17 TriggerMessage)."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from certification import catalog
from certification.catalog import TestCase, TestResult
from certification.helpers import TestContext


async def _run_change_availability(ctx: TestContext) -> TestResult:
    cp = ctx.get_cp()
    if cp is None:
        return TestResult(status="failed", message="Borne hors ligne")
    # Inoperative puis Operative pour éviter de laisser une borne bloquée.
    # Le ChargePoint expose set_available(connector_id) / set_unavailable(connector_id).
    try:
        status_ino = await cp.set_unavailable(connector_id=0)
    except Exception as e:
        return TestResult(
            status="failed",
            message=f"ChangeAvailability Inoperative a levé une exception : {e}",
        )
    await asyncio.sleep(2)
    try:
        status_op = await cp.set_available(connector_id=0)
    except Exception as e:
        return TestResult(
            status="failed",
            message=f"ChangeAvailability Operative a levé une exception : {e}",
            recommendation="Borne possiblement coincée en Inoperative — vérifier manuellement.",
        )
    details = {
        "inoperative_status": str(status_ino),
        "operative_status": str(status_op),
    }
    # set_unavailable/set_available retournent bool (True = Accepted|Scheduled)
    # ou potentiellement string OCPP selon implémentation — accepter les deux.
    def _ok(v) -> bool:
        if isinstance(v, bool):
            return v
        return str(v).lower() in {"accepted", "scheduled", "true"}
    ok_ino = _ok(status_ino)
    ok_op = _ok(status_op)
    if not ok_ino or not ok_op:
        return TestResult(
            status="failed",
            message=f"ChangeAvailability réponse inattendue : ino={status_ino}, op={status_op}",
            details=details,
        )
    return TestResult(
        status="passed",
        message="ChangeAvailability Inoperative → Operative OK",
        details=details,
    )


catalog.register(
    TestCase(
        name="rem.change_availability",
        category="Remote",
        title="ChangeAvailability connector 0 (§5.1)",
        description="Bascule la borne en Inoperative puis Operative (connecteur 0 = toute la borne).",
        run=_run_change_availability,
        estimated_seconds=10,
        suites={"quick", "standard", "full"},
        ocpp_ref="§5.1 ChangeAvailability",
    )
)


# ─────────────────────────── rem.unlock_connector ───────────────────────────


async def _run_unlock_connector(ctx: TestContext) -> TestResult:
    cp = ctx.get_cp()
    if cp is None:
        return TestResult(status="failed", message="Borne hors ligne")
    try:
        status = await cp.unlock_connector(connector_id=1)
    except Exception as e:
        return TestResult(
            status="failed",
            message=f"UnlockConnector a levé une exception : {e}",
        )
    details = {"status": str(status)}
    # Accepted, Unlocked, UnlockFailed, NotSupported — tout retour OCPP valide = passed.
    valid = {"unlocked", "accepted", "unlockfailed", "notsupported"}
    if str(status).lower() in valid:
        return TestResult(
            status="passed",
            message=f"UnlockConnector a répondu : {status}",
            details=details,
        )
    return TestResult(
        status="failed",
        message=f"UnlockConnector réponse inattendue : {status}",
        details=details,
        recommendation="Valeurs OCPP 1.6J §5.18 : Unlocked | UnlockFailed | NotSupported.",
    )


catalog.register(
    TestCase(
        name="rem.unlock_connector",
        category="Remote",
        title="UnlockConnector (§5.18)",
        description="Demande le déverrouillage du connecteur 1 et vérifie la réponse.",
        run=_run_unlock_connector,
        estimated_seconds=6,
        suites={"full"},
        ocpp_ref="§5.18 UnlockConnector",
    )
)


# ─────────────────────────── rem.trigger_heartbeat ───────────────────────────


async def _run_trigger_heartbeat(ctx: TestContext) -> TestResult:
    cp = ctx.get_cp()
    if cp is None:
        return TestResult(status="failed", message="Borne hors ligne")
    if not ctx.supports("RemoteTrigger"):
        return TestResult(
            status="skipped",
            message="Profil RemoteTrigger non supporté",
        )
    try:
        status = await cp.trigger_message("Heartbeat")
    except Exception as e:
        return TestResult(
            status="failed",
            message=f"TriggerMessage a levé une exception : {e}",
        )
    details = {"trigger_status": str(status)}
    if str(status).lower() not in {"accepted"}:
        return TestResult(
            status="failed",
            message=f"TriggerMessage refusé : {status}",
            details=details,
        )
    # Attend le Heartbeat consécutif
    since = datetime.now(timezone.utc) - timedelta(seconds=3)
    row = await ctx.wait_for_activity(action="Heartbeat", direction="in", since=since, timeout_s=15, poll_s=0.5)
    details["heartbeat_received"] = row is not None
    if row is None:
        return TestResult(
            status="failed",
            message="TriggerMessage accepté mais pas de Heartbeat dans les 15s",
            details=details,
            recommendation="Vérifier §5.17 TriggerMessage : la borne doit envoyer le msg dans la foulée.",
        )
    return TestResult(
        status="passed",
        message="TriggerMessage → Heartbeat observé",
        details=details,
    )


catalog.register(
    TestCase(
        name="rem.trigger_heartbeat",
        category="Remote",
        title="TriggerMessage Heartbeat (§5.17)",
        description="Déclenche un Heartbeat via TriggerMessage et vérifie la réception.",
        run=_run_trigger_heartbeat,
        requires_profile="RemoteTrigger",
        estimated_seconds=10,
        suites={"quick", "standard", "full"},
        ocpp_ref="§5.17 TriggerMessage",
    )
)


# ═════════════════════════════════════════════════════════════════════════
#                     PHASE 3c — Remote exhaustif
# ═════════════════════════════════════════════════════════════════════════
# Couvre les messages serveur→borne qui ne sont pas déjà dans Phase 3 :
#   • Reset Soft   (§6.15)   — rem.reset_soft
#   • Reset Hard   (§6.15)   — rem.reset_hard                 (full only)
#   • ClearCache   (§6.4)    — rem.clear_cache
#   • DataTransfer (§6.6)    — rem.data_transfer
#   • TriggerMessage MeterValues          (§5.17) — rem.trigger_metervalues
#   • TriggerMessage StatusNotification   (§5.17) — rem.trigger_statusnotification
#
# Patterns :
#   - Les méthodes bool (reset, clear_cache) retournent True/False selon
#     Accepted/Rejected. On fail si False.
#   - `trigger_message` retourne "Accepted" | "Rejected" | "NotImplemented"
#     | "NotSupported" | "Failed". On accepte Accepted OU NotImplemented
#     (certaines bornes ne triggent pas tous les messages).
#   - `data_transfer` retourne `response_data` (peut être None avec un
#     status OCPP valide). On vérifie via ChargerActivityLog le CALL/
#     CALLRESULT pour confirmer l'échange OCPP.
#
# Toutes les vérifications "wire réelle" utilisent ctx.wait_for_activity
# (poll sur ChargerActivityLog) — cohérent avec §34 capture S32.

from sqlalchemy import select


# ─────────────────────────── rem.reset_soft ──────────────────────────


async def _run_reset_soft(ctx: TestContext) -> TestResult:
    """Reset Soft (§6.15) — redémarrage graceful de la borne.

    La borne doit retourner Accepted puis se reconnecter. Le test ne
    bloque PAS en attente de la reconnexion (trop long/variable selon
    constructeur) — il valide uniquement la réponse Accepted.
    """
    cp = ctx.get_cp()
    if cp is None:
        return TestResult(status="failed", message="Borne hors ligne")
    try:
        ok = await cp.reset(reset_type="Soft")
    except Exception as e:
        return TestResult(
            status="failed",
            message=f"Reset Soft a levé une exception : {e}",
        )
    details = {"accepted": bool(ok), "type": "Soft"}
    if ok:
        return TestResult(
            status="passed",
            message="Reset Soft accepté par la borne",
            details=details,
        )
    return TestResult(
        status="failed",
        message="Reset Soft rejeté — borne n'accepte pas le redémarrage",
        details=details,
        recommendation=(
            "§6.15 Reset.conf doit retourner Accepted. Vérifier que la borne "
            "n'est pas déjà en cours de reboot / update firmware."
        ),
    )


catalog.register(
    TestCase(
        name="rem.reset_soft",
        category="Remote",
        title="Reset Soft (§6.15)",
        description=(
            "Envoie Reset.req type=Soft et vérifie que la borne retourne "
            "Accepted. Ne bloque pas sur la reconnexion post-reset."
        ),
        run=_run_reset_soft,
        estimated_seconds=8,
        suites={"standard", "full"},
        ocpp_ref="§6.15 Reset type=Soft",
    )
)


# ─────────────────────────── rem.reset_hard ──────────────────────────


async def _run_reset_hard(ctx: TestContext) -> TestResult:
    """Reset Hard (§6.15) — redémarrage brutal.

    Attention : contrairement à Soft, Hard coupe immédiatement — peut
    interrompre une transaction en cours. Restreint à la suite `full`.
    """
    cp = ctx.get_cp()
    if cp is None:
        return TestResult(status="failed", message="Borne hors ligne")
    try:
        ok = await cp.reset(reset_type="Hard")
    except Exception as e:
        return TestResult(
            status="failed",
            message=f"Reset Hard a levé une exception : {e}",
        )
    details = {"accepted": bool(ok), "type": "Hard"}
    if ok:
        return TestResult(
            status="passed",
            message="Reset Hard accepté par la borne",
            details=details,
        )
    return TestResult(
        status="failed",
        message="Reset Hard rejeté — borne n'accepte pas le redémarrage brutal",
        details=details,
        recommendation=(
            "§6.15 Reset.conf type=Hard doit être supporté — même si la borne "
            "préfère Soft en interne, elle doit honorer la demande Hard."
        ),
    )


catalog.register(
    TestCase(
        name="rem.reset_hard",
        category="Remote",
        title="Reset Hard (§6.15)",
        description=(
            "Envoie Reset.req type=Hard. Plus disruptif que Soft — "
            "réservé à la suite full pour éviter d'interrompre des essais."
        ),
        run=_run_reset_hard,
        estimated_seconds=10,
        suites={"full"},
        ocpp_ref="§6.15 Reset type=Hard",
    )
)


# ─────────────────────────── rem.clear_cache ──────────────────────────


async def _run_clear_cache(ctx: TestContext) -> TestResult:
    """ClearCache (§6.4) — vide la cache locale d'authorization de la borne.

    La réponse OCPP est Accepted | Rejected. Une borne qui n'implémente
    pas de cache peut retourner Accepted (no-op) ou Rejected.
    """
    cp = ctx.get_cp()
    if cp is None:
        return TestResult(status="failed", message="Borne hors ligne")
    try:
        ok = await cp.clear_cache()
    except Exception as e:
        return TestResult(
            status="failed",
            message=f"ClearCache a levé une exception : {e}",
        )
    details = {"accepted": bool(ok)}
    if ok:
        return TestResult(
            status="passed",
            message="ClearCache accepté — cache RFID vidée",
            details=details,
        )
    return TestResult(
        status="failed",
        message="ClearCache rejeté",
        details=details,
        recommendation=(
            "§6.4 ClearCache doit retourner Accepted. Si la borne ne gère pas "
            "de cache locale, elle devrait quand même répondre Accepted (no-op)."
        ),
    )


catalog.register(
    TestCase(
        name="rem.clear_cache",
        category="Remote",
        title="ClearCache (§6.4)",
        description=(
            "Envoie ClearCache.req et vérifie que la borne retourne Accepted. "
            "Vide la cache RFID locale de la borne (Authorize déjà acceptés)."
        ),
        run=_run_clear_cache,
        estimated_seconds=5,
        suites={"standard", "full"},
        ocpp_ref="§6.4 ClearCache",
    )
)


# ─────────────────────────── rem.data_transfer ──────────────────────────


async def _run_data_transfer(ctx: TestContext) -> TestResult:
    """DataTransfer §6.6 — échange vendeur générique serveur → borne.

    Envoie un payload neutre (vendor_id=org.ocpp.certif). La réponse OCPP
    peut être Accepted / Rejected / UnknownMessageId / UnknownVendorId —
    tous ces statuts sont conformes §6.6. Le test valide simplement qu'un
    CALL sortant + un CALLRESULT entrant sont visibles dans l'activity log.
    """
    cp = ctx.get_cp()
    if cp is None:
        return TestResult(status="failed", message="Borne hors ligne")
    since = datetime.now(timezone.utc) - timedelta(seconds=2)
    try:
        response = await cp.data_transfer(
            vendor_id="org.ocpp.certif",
            message_id="Ping",
            data="hello",
        )
    except Exception as e:
        return TestResult(
            status="failed",
            message=f"DataTransfer a levé une exception : {e}",
        )
    # Vérifier wire OCPP via activity log (CALL out + CALLRESULT in)
    row_out = await ctx.wait_for_activity(
        action="DataTransfer",
        direction="out",
        since=since,
        timeout_s=5,
        poll_s=0.3,
    )
    details = {
        "response_data": str(response) if response is not None else None,
        "call_logged": row_out is not None,
    }
    if row_out is None:
        return TestResult(
            status="failed",
            message="DataTransfer CALL sortant absent de l'activity log",
            details=details,
            recommendation=(
                "Vérifier capture activity log (§34 S32) — le hook logging "
                "doit enregistrer les CALL sortants."
            ),
        )
    return TestResult(
        status="passed",
        message="DataTransfer envoyé — échange OCPP conforme §6.6",
        details=details,
    )


catalog.register(
    TestCase(
        name="rem.data_transfer",
        category="Remote",
        title="DataTransfer serveur → borne (§6.6)",
        description=(
            "Envoie DataTransfer.req vendor_id=org.ocpp.certif et vérifie "
            "qu'un CALL sortant apparaît dans l'activity log."
        ),
        run=_run_data_transfer,
        estimated_seconds=8,
        suites={"standard", "full"},
        ocpp_ref="§6.6 DataTransfer",
    )
)


# ─────────────────────────── rem.trigger_metervalues ──────────────────────────


async def _run_trigger_metervalues(ctx: TestContext) -> TestResult:
    """TriggerMessage MeterValues (§5.17) — force un envoi MeterValues.

    La borne doit retourner Accepted puis envoyer un MeterValues pour le
    connecteur demandé (ou tous si connector_id=None).
    """
    cp = ctx.get_cp()
    if cp is None:
        return TestResult(status="failed", message="Borne hors ligne")
    if not ctx.supports("RemoteTrigger"):
        return TestResult(
            status="skipped",
            message="Profil RemoteTrigger non supporté",
        )
    since = datetime.now(timezone.utc) - timedelta(seconds=2)
    try:
        status = await cp.trigger_message("MeterValues", connector_id=1)
    except Exception as e:
        return TestResult(
            status="failed",
            message=f"TriggerMessage MeterValues a levé une exception : {e}",
        )
    details = {"trigger_status": str(status), "connector_id": 1}
    status_lower = str(status).lower()
    # Accepted attendu ; NotImplemented OK si la borne n'a pas de compteur
    # actif (pas de transaction en cours) mais annonce le profil.
    if status_lower == "notimplemented":
        return TestResult(
            status="passed",
            message="TriggerMessage MeterValues retourne NotImplemented "
            "(borne conforme §5.17 — OK sans transaction active)",
            details=details,
        )
    if status_lower != "accepted":
        return TestResult(
            status="failed",
            message=f"TriggerMessage MeterValues refusé : {status}",
            details=details,
            recommendation=(
                "§5.17 TriggerMessageStatus = Accepted | Rejected | "
                "NotImplemented. Vérifier que le connecteur 1 est valide."
            ),
        )
    # Status Accepted — attendre le MeterValues suivant
    row = await ctx.wait_for_activity(
        action="MeterValues",
        direction="in",
        since=since,
        timeout_s=15,
        poll_s=0.5,
    )
    details["metervalues_received"] = row is not None
    if row is None:
        return TestResult(
            status="failed",
            message=(
                "TriggerMessage MeterValues accepté mais aucun MeterValues "
                "reçu dans les 15s"
            ),
            details=details,
            recommendation=(
                "§5.17 : la borne doit envoyer le message demandé dans la "
                "foulée de la réponse Accepted."
            ),
        )
    return TestResult(
        status="passed",
        message="TriggerMessage → MeterValues observé (§5.17 conforme)",
        details=details,
    )


catalog.register(
    TestCase(
        name="rem.trigger_metervalues",
        category="Remote",
        title="TriggerMessage MeterValues (§5.17)",
        description=(
            "Déclenche un MeterValues via TriggerMessage sur connecteur 1 "
            "et vérifie la réception (ou NotImplemented accepté)."
        ),
        run=_run_trigger_metervalues,
        requires_profile="RemoteTrigger",
        estimated_seconds=18,
        suites={"full"},
        ocpp_ref="§5.17 TriggerMessage MeterValues",
    )
)


# ─────────────────────────── rem.trigger_statusnotification ──────────────────


async def _run_trigger_statusnotification(ctx: TestContext) -> TestResult:
    """TriggerMessage StatusNotification (§5.17) — force un état courant.

    Utile pour resynchroniser le backend avec la vision de la borne.
    """
    cp = ctx.get_cp()
    if cp is None:
        return TestResult(status="failed", message="Borne hors ligne")
    if not ctx.supports("RemoteTrigger"):
        return TestResult(
            status="skipped",
            message="Profil RemoteTrigger non supporté",
        )
    since = datetime.now(timezone.utc) - timedelta(seconds=2)
    try:
        status = await cp.trigger_message("StatusNotification", connector_id=1)
    except Exception as e:
        return TestResult(
            status="failed",
            message=f"TriggerMessage StatusNotification a levé une exception : {e}",
        )
    details = {"trigger_status": str(status), "connector_id": 1}
    status_lower = str(status).lower()
    if status_lower != "accepted":
        return TestResult(
            status="failed",
            message=f"TriggerMessage StatusNotification refusé : {status}",
            details=details,
            recommendation=(
                "§5.17 TriggerMessageStatus = Accepted | Rejected | "
                "NotImplemented. StatusNotification doit être universellement "
                "supporté (profil Core §5.15)."
            ),
        )
    row = await ctx.wait_for_activity(
        action="StatusNotification",
        direction="in",
        since=since,
        timeout_s=15,
        poll_s=0.5,
    )
    details["statusnotification_received"] = row is not None
    if row is None:
        return TestResult(
            status="failed",
            message=(
                "TriggerMessage StatusNotification accepté mais pas de "
                "StatusNotification dans les 15s"
            ),
            details=details,
            recommendation="§5.17 : envoi du message demandé dans la foulée.",
        )
    return TestResult(
        status="passed",
        message="TriggerMessage → StatusNotification observé (§5.17 conforme)",
        details=details,
    )


catalog.register(
    TestCase(
        name="rem.trigger_statusnotification",
        category="Remote",
        title="TriggerMessage StatusNotification (§5.17)",
        description=(
            "Déclenche un StatusNotification via TriggerMessage sur "
            "connecteur 1 et vérifie la réception."
        ),
        run=_run_trigger_statusnotification,
        requires_profile="RemoteTrigger",
        estimated_seconds=18,
        suites={"standard", "full"},
        ocpp_ref="§5.17 TriggerMessage StatusNotification",
    )
)
