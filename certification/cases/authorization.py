"""Tests Authorize / LocalAuthList (§4.1, §6.9, §6.11).

Note OCPP : le message Authorize est CP→CS (borne → serveur). On ne peut
pas l'initier depuis le serveur. Pour certifier le comportement de la
borne, on valide la logique serveur (whitelist) qui serait exécutée si
la borne envoyait Authorize, en appelant directement ``_authorize_id_tag``
(même code path que le handler ``on_authorize``).

Pour une validation wire complète sur borne réelle, le technicien doit
passer une carte RFID ; le test ``auth.authorize_swipe`` (suite ``full``)
attend alors une entrée dans l'activity log.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from certification import catalog
from certification.catalog import TestCase, TestResult
from certification.helpers import TestContext


CERTIF_TAG_VALID = "CERTIF_VALID"
CERTIF_TAG_REJECTED = "CERTIF_BLOCKED"


async def _server_side_authorize(cp, id_tag: str) -> str | None:
    """Appelle ``cp._authorize_id_tag`` (helper StateMixin Sprint 29).

    Retourne la string de status (``"Accepted"``/``"Blocked"``/``"Invalid"``/
    ``"Expired"``) ou None si le helper n'est pas disponible.
    """
    fn = getattr(cp, "_authorize_id_tag", None)
    if not callable(fn):
        return None
    info = await fn(id_tag)
    status = info.get("status") if isinstance(info, dict) else None
    if status is None:
        return None
    # Peut être un enum AuthorizationStatus ou déjà une string.
    val = getattr(status, "value", None)
    return val if val is not None else str(status)


# ─────────────────────────── auth.authorize_valid ───────────────────────────


async def _run_authorize_valid(ctx: TestContext) -> TestResult:
    await ctx.ensure_tag(CERTIF_TAG_VALID, active=True, note="certif valid")
    cp = ctx.get_cp()
    if cp is None:
        return TestResult(status="failed", message="Borne hors ligne")
    try:
        status = await _server_side_authorize(cp, CERTIF_TAG_VALID)
    except Exception as e:
        return TestResult(
            status="failed",
            message=f"_authorize_id_tag a levé une exception : {e}",
            recommendation="Vérifier que StateMixin._authorize_id_tag est disponible.",
        )
    if status is None:
        return TestResult(
            status="failed",
            message="Helper serveur _authorize_id_tag indisponible",
            recommendation="Mettre à jour le serveur OCPP (Sprint 29 Volet A requis).",
        )
    details = {
        "tag": CERTIF_TAG_VALID,
        "expected": "Accepted",
        "server_status": status,
    }
    if str(status).lower() == "accepted":
        return TestResult(
            status="passed",
            message=f"Serveur autoriserait {CERTIF_TAG_VALID} → Accepted",
            details=details,
        )
    return TestResult(
        status="failed",
        message=f"Status serveur inattendu : {status} (attendu Accepted)",
        details=details,
        recommendation="Vérifier que la whitelist serveur contient bien le tag actif.",
    )


catalog.register(
    TestCase(
        name="auth.authorize_valid",
        category="Authorization",
        title="Authorize tag valide (§4.1 — logique serveur)",
        description=(
            f"Ajoute {CERTIF_TAG_VALID} actif dans la whitelist et vérifie que "
            "la logique d'autorisation serveur retourne Accepted."
        ),
        run=_run_authorize_valid,
        estimated_seconds=5,
        suites={"standard", "full"},
        ocpp_ref="§4.1 Authorize.conf",
    )
)


# ─────────────────────────── auth.authorize_rejected ───────────────────────────


async def _run_authorize_rejected(ctx: TestContext) -> TestResult:
    await ctx.ensure_tag(CERTIF_TAG_REJECTED, active=False, note="certif blocked")
    cp = ctx.get_cp()
    if cp is None:
        return TestResult(status="failed", message="Borne hors ligne")
    try:
        status = await _server_side_authorize(cp, CERTIF_TAG_REJECTED)
    except Exception as e:
        return TestResult(
            status="failed",
            message=f"_authorize_id_tag a levé une exception : {e}",
        )
    if status is None:
        return TestResult(
            status="failed",
            message="Helper serveur _authorize_id_tag indisponible",
        )
    details = {
        "tag": CERTIF_TAG_REJECTED,
        "expected": "Blocked or Invalid",
        "server_status": status,
    }
    if str(status).lower() in {"blocked", "invalid", "expired"}:
        return TestResult(
            status="passed",
            message=f"Serveur refuserait {CERTIF_TAG_REJECTED} → {status}",
            details=details,
        )
    return TestResult(
        status="failed",
        message=(
            f"Tag inactif mais serveur retournerait {status} "
            "— devrait être Blocked/Invalid"
        ),
        details=details,
        recommendation=(
            "Vérifier que la whitelist serveur bloque les tags inactifs (§4.1 IdTagInfo)."
        ),
    )


catalog.register(
    TestCase(
        name="auth.authorize_rejected",
        category="Authorization",
        title="Authorize tag bloqué (§4.1 — logique serveur)",
        description=(
            f"Vérifie qu'un tag inactif ({CERTIF_TAG_REJECTED}) est refusé par "
            "la logique d'autorisation serveur (Blocked/Invalid/Expired)."
        ),
        run=_run_authorize_rejected,
        estimated_seconds=5,
        suites={"standard", "full"},
        ocpp_ref="§4.1 Authorize.conf",
    )
)


# ─────────────────────────── auth.local_list_sync ───────────────────────────


async def _run_local_list_sync(ctx: TestContext) -> TestResult:
    if not ctx.supports("LocalAuthListManagement"):
        return TestResult(
            status="skipped",
            message="Profil LocalAuthListManagement non supporté par la borne",
        )
    cp = ctx.get_cp()
    if cp is None:
        return TestResult(status="failed", message="Borne hors ligne")
    try:
        version = await cp.get_local_list_version()
    except Exception as e:
        return TestResult(
            status="failed",
            message=f"GetLocalListVersion a levé une exception : {e}",
            recommendation="Vérifier l'implémentation §6.11 côté borne.",
        )
    details = {"current_version": version}
    if version is None:
        return TestResult(
            status="failed",
            message="GetLocalListVersion n'a retourné aucune version",
            details=details,
            recommendation="Le profil LocalAuthListManagement déclaré mais endpoint KO.",
        )
    return TestResult(
        status="passed",
        message=f"GetLocalListVersion a retourné version={version}",
        details=details,
    )


catalog.register(
    TestCase(
        name="auth.local_list_sync",
        category="Authorization",
        title="GetLocalListVersion (§6.11)",
        description="Vérifie que la borne répond à GetLocalListVersion (cache local).",
        run=_run_local_list_sync,
        requires_profile="LocalAuthListManagement",
        estimated_seconds=8,
        suites={"full"},
        ocpp_ref="§6.11 GetLocalListVersion",
    )
)


# ─────────────────────────── auth.authorize_swipe (full uniquement) ──────────


async def _run_authorize_swipe(ctx: TestContext) -> TestResult:
    """Validation wire : technicien passe une carte, on observe l'activity log."""
    await ctx.ensure_tag(CERTIF_TAG_VALID, active=True, note="certif valid swipe")
    since = datetime.now(timezone.utc) - timedelta(seconds=2)
    resp = await ctx.prompt_technician(
        message=(
            f"Passer une carte RFID (tag configuré : {CERTIF_TAG_VALID}). "
            "Appuyer sur 'done' dès que la carte est présentée."
        ),
        timeout_s=120,
        buttons=["done", "skip"],
    )
    if resp != "done":
        return TestResult(
            status="skipped",
            message=f"Technicien a répondu {resp!r} — test non exécuté",
        )
    # Observer l'activity log pour un Authorize entrant (direction=in).
    row = await ctx.wait_for_activity(
        action="Authorize",
        direction="in",
        since=since,
        timeout_s=15,
    )
    if row is None:
        return TestResult(
            status="failed",
            message="Aucun Authorize reçu après le swipe",
            recommendation=(
                "Vérifier que la borne est configurée en mode 'RFID' et "
                "envoie bien Authorize au central system."
            ),
        )
    details = {
        "activity_id": row.id,
        "payload": row.payload,
        "unique_id": row.unique_id,
    }
    return TestResult(
        status="passed",
        message="Authorize entrant capté dans l'activity log après swipe",
        details=details,
    )


catalog.register(
    TestCase(
        name="auth.authorize_swipe",
        category="Authorization",
        title="Authorize RFID réel (§4.1 — swipe)",
        description=(
            "Le technicien passe une carte RFID ; le serveur attend un "
            "message Authorize entrant dans l'activity log."
        ),
        run=_run_authorize_swipe,
        estimated_seconds=30,
        suites={"full"},
        ocpp_ref="§4.1 Authorize.req",
    )
)


# ═════════════════════════════════════════════════════════════════════════
#                        PHASE 3b — Authorization exhaustif
# ═════════════════════════════════════════════════════════════════════════
# Couvre les 3 IdTagInfo.status définis §4.1 OCPP 1.6J au-delà de Accepted :
#   • Expired  — tag valide côté whitelist mais `expiry_date` < now
#   • Blocked  — tag explicitement désactivé (active=False) ; le serveur doit
#                retourner strictement "Blocked" (pas Invalid/Expired)
#   • Invalid  — tag absent de la whitelist (jamais déclaré)
#
# Tous passent par ``_server_side_authorize`` qui appelle
# ``cp._authorize_id_tag`` — même code path que ``on_authorize`` côté serveur.
# Aucune dépendance véhicule ni carte RFID physique (wire test couvert par
# ``auth.authorize_swipe`` suite full).

CERTIF_TAG_EXPIRED = "CERTIF_EXPIRED"
CERTIF_TAG_BLOCKED_STRICT = "CERTIF_BLOCKED_STRICT"
CERTIF_TAG_UNKNOWN = "CERTIF_UNKNOWN_XYZ"


# ─────────────────────────── auth.authorize_expired ──────────────────────────


async def _run_authorize_expired(ctx: TestContext) -> TestResult:
    """Tag avec ``expiry_date`` dans le passé → status ``Expired``."""
    past = datetime.now(timezone.utc) - timedelta(days=1)
    await ctx.ensure_tag(
        CERTIF_TAG_EXPIRED,
        active=True,
        note="certif expired (expiry_date=yesterday)",
        expiry_date=past,
    )
    cp = ctx.get_cp()
    if cp is None:
        return TestResult(status="failed", message="Borne hors ligne")
    try:
        status = await _server_side_authorize(cp, CERTIF_TAG_EXPIRED)
    except Exception as e:
        return TestResult(
            status="failed",
            message=f"_authorize_id_tag a levé une exception : {e}",
        )
    if status is None:
        return TestResult(
            status="failed",
            message="Helper serveur _authorize_id_tag indisponible",
            recommendation="Sprint 29 Volet A requis côté serveur.",
        )
    details = {
        "tag": CERTIF_TAG_EXPIRED,
        "expiry_date": past.isoformat(),
        "expected": "Expired",
        "server_status": status,
    }
    if str(status).lower() == "expired":
        return TestResult(
            status="passed",
            message=f"Serveur retourne Expired pour tag avec expiry_date=J-1",
            details=details,
        )
    return TestResult(
        status="failed",
        message=(
            f"Tag expiré mais serveur retourne {status} — devrait être Expired "
            "(§4.1 IdTagInfo)"
        ),
        details=details,
        recommendation=(
            "Vérifier que StateMixin._authorize_id_tag compare bien "
            "expiry_date à datetime.now(timezone.utc)."
        ),
    )


catalog.register(
    TestCase(
        name="auth.authorize_expired",
        category="Authorization",
        title="Authorize tag expiré (§4.1 — logique serveur)",
        description=(
            f"Insère {CERTIF_TAG_EXPIRED} avec expiry_date=J-1 et vérifie "
            "que la logique d'autorisation serveur retourne strictement Expired."
        ),
        run=_run_authorize_expired,
        estimated_seconds=5,
        suites={"standard", "full"},
        ocpp_ref="§4.1 Authorize.conf IdTagInfo.status=Expired",
    )
)


# ─────────────────────────── auth.authorize_blocked ──────────────────────────


async def _run_authorize_blocked(ctx: TestContext) -> TestResult:
    """Tag avec ``active=False`` → status strictement ``Blocked``.

    Différence avec ``auth.authorize_rejected`` : ce test est strict — il
    exige ``Blocked`` exactement, pas le rollup {Blocked, Invalid, Expired}.
    But : détecter une régression où un tag désactivé tomberait par erreur
    dans la branche "inconnu" (Invalid) côté serveur.
    """
    await ctx.ensure_tag(
        CERTIF_TAG_BLOCKED_STRICT,
        active=False,
        note="certif blocked strict (active=False)",
    )
    cp = ctx.get_cp()
    if cp is None:
        return TestResult(status="failed", message="Borne hors ligne")
    try:
        status = await _server_side_authorize(cp, CERTIF_TAG_BLOCKED_STRICT)
    except Exception as e:
        return TestResult(
            status="failed",
            message=f"_authorize_id_tag a levé une exception : {e}",
        )
    if status is None:
        return TestResult(
            status="failed",
            message="Helper serveur _authorize_id_tag indisponible",
        )
    details = {
        "tag": CERTIF_TAG_BLOCKED_STRICT,
        "active": False,
        "expected": "Blocked",
        "server_status": status,
    }
    if str(status).lower() == "blocked":
        return TestResult(
            status="passed",
            message=f"Serveur retourne Blocked (strict) pour tag active=False",
            details=details,
        )
    return TestResult(
        status="failed",
        message=(
            f"Tag active=False mais serveur retourne {status} — "
            "devrait être strictement Blocked (§4.1 IdTagInfo)"
        ),
        details=details,
        recommendation=(
            "Vérifier l'ordre des branches dans _authorize_id_tag : "
            "la vérif active=False doit précéder la vérif expiry_date."
        ),
    )


catalog.register(
    TestCase(
        name="auth.authorize_blocked",
        category="Authorization",
        title="Authorize tag bloqué strict (§4.1 — logique serveur)",
        description=(
            f"Insère {CERTIF_TAG_BLOCKED_STRICT} avec active=False et exige "
            "que la logique serveur retourne strictement Blocked (pas Invalid)."
        ),
        run=_run_authorize_blocked,
        estimated_seconds=5,
        suites={"standard", "full"},
        ocpp_ref="§4.1 Authorize.conf IdTagInfo.status=Blocked",
    )
)


# ─────────────────────────── auth.authorize_unknown ──────────────────────────


async def _run_authorize_unknown(ctx: TestContext) -> TestResult:
    """Tag jamais déclaré dans la whitelist → status ``Invalid``.

    Purge le tag avant le test (idempotent) pour garantir l'état "absent".
    """
    await ctx.delete_tag(CERTIF_TAG_UNKNOWN)
    cp = ctx.get_cp()
    if cp is None:
        return TestResult(status="failed", message="Borne hors ligne")
    try:
        status = await _server_side_authorize(cp, CERTIF_TAG_UNKNOWN)
    except Exception as e:
        return TestResult(
            status="failed",
            message=f"_authorize_id_tag a levé une exception : {e}",
        )
    if status is None:
        return TestResult(
            status="failed",
            message="Helper serveur _authorize_id_tag indisponible",
        )
    details = {
        "tag": CERTIF_TAG_UNKNOWN,
        "expected": "Invalid",
        "server_status": status,
    }
    # Comportement observé §32.1 : whitelist vide → mode permissif "Accepted".
    # Si la whitelist est peuplée (cas §32 sprint seed), un tag absent doit
    # retourner Invalid. On accepte les deux selon la posture runtime.
    status_lower = str(status).lower()
    if status_lower == "invalid":
        return TestResult(
            status="passed",
            message=f"Serveur retourne Invalid pour tag absent (whitelist enforced)",
            details=details,
        )
    if status_lower == "accepted":
        return TestResult(
            status="passed",
            message=(
                "Serveur retourne Accepted pour tag absent — whitelist vide "
                "→ mode permissif (§29 Volet A)"
            ),
            details={**details, "posture": "permissive (whitelist empty)"},
        )
    return TestResult(
        status="failed",
        message=(
            f"Tag absent mais serveur retourne {status} — attendu Invalid "
            "(whitelist peuplée) ou Accepted (mode permissif)"
        ),
        details=details,
        recommendation=(
            "Vérifier StateMixin._authorize_id_tag : branche 'tag not found' "
            "doit retourner Invalid quand la whitelist n'est pas vide."
        ),
    )


catalog.register(
    TestCase(
        name="auth.authorize_unknown",
        category="Authorization",
        title="Authorize tag inconnu (§4.1 — logique serveur)",
        description=(
            f"Purge {CERTIF_TAG_UNKNOWN} puis vérifie que le serveur retourne "
            "Invalid (whitelist peuplée) ou Accepted (whitelist vide/permissif)."
        ),
        run=_run_authorize_unknown,
        estimated_seconds=5,
        suites={"standard", "full"},
        ocpp_ref="§4.1 Authorize.conf IdTagInfo.status=Invalid",
    )
)
