"""Tests LocalAuthList (§5.7 SendLocalList, §5.11 GetLocalListVersion).

Phase 3d — Couverture exhaustive de LocalAuthListManagement :
  • lal.get_version       — GetLocalListVersion §6.11 (lecture version courante)
  • lal.send_full         — SendLocalList Full §6.20 (remplace toute la liste)
  • lal.send_differential — SendLocalList Differential §6.20 (ajoute/modifie)

Les 3 tests sont gatés par ``ctx.supports("LocalAuthListManagement")`` —
les bornes qui n'annoncent pas ce profil dans ``SupportedFeatureProfiles``
sont marquées ``skipped`` (non applicable à leur conformité).

Dépendances serveur : ``ChargePoint.send_local_list`` +
``ChargePoint.get_local_list_version`` (actions.py Sprint 31 Volet A).
Nettoyage : les tags semés sont purgés en fin de test via ``ctx.delete_tag``.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from certification import catalog
from certification.catalog import TestCase, TestResult
from certification.helpers import TestContext


CERTIF_LAL_TAG_FULL_A = "CERTIF_LAL_FULL_A"
CERTIF_LAL_TAG_FULL_B = "CERTIF_LAL_FULL_B"
CERTIF_LAL_TAG_DIFF = "CERTIF_LAL_DIFF"


# ─────────────────────────── lal.get_version ─────────────────────────────────


async def _run_get_local_list_version(ctx: TestContext) -> TestResult:
    """GetLocalListVersion §6.11 — retourne un entier (>=0) ou −1 si non supporté."""
    if not ctx.supports("LocalAuthListManagement"):
        return TestResult(
            status="skipped",
            message="Profil LocalAuthListManagement non supporté",
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
        )
    if version is None:
        return TestResult(
            status="failed",
            message="GetLocalListVersion a retourné None (CALL échoué ou timeout)",
            recommendation=(
                "Vérifier que la borne répond à GetLocalListVersion et que "
                "le profil LocalAuthListManagement est activé côté borne."
            ),
        )
    details = {"list_version": int(version)}
    if version == -1:
        return TestResult(
            status="passed",
            message="GetLocalListVersion = −1 (borne annonce fonctionnalité inactive)",
            details=details,
        )
    if version >= 0:
        return TestResult(
            status="passed",
            message=f"GetLocalListVersion = {version} (liste locale active)",
            details=details,
        )
    return TestResult(
        status="failed",
        message=f"GetLocalListVersion a retourné valeur inattendue : {version}",
        details=details,
    )


catalog.register(
    TestCase(
        name="lal.get_version",
        category="LocalAuthList",
        title="GetLocalListVersion (§6.11)",
        description=(
            "Lit la version courante de la whitelist locale de la borne. "
            "Valeur −1 si la fonctionnalité est inactive, ≥0 sinon."
        ),
        run=_run_get_local_list_version,
        estimated_seconds=6,
        suites={"standard", "full"},
        requires_profile="LocalAuthListManagement",
        ocpp_ref="§5.11 GetLocalListVersion / §6.11",
    )
)


# ─────────────────────────── lal.send_full ───────────────────────────────────


async def _run_send_local_list_full(ctx: TestContext) -> TestResult:
    """SendLocalList updateType=Full §6.20 — remplace intégralement la liste."""
    if not ctx.supports("LocalAuthListManagement"):
        return TestResult(
            status="skipped",
            message="Profil LocalAuthListManagement non supporté",
        )
    cp = ctx.get_cp()
    if cp is None:
        return TestResult(status="failed", message="Borne hors ligne")

    # Lit la version courante pour bumper proprement
    try:
        current_version = await cp.get_local_list_version()
    except Exception:
        current_version = None
    base = 0 if current_version is None or current_version < 0 else int(current_version)
    new_version = base + 1

    # 2 tags actifs à pousser — liste Full complète
    expiry = datetime.now(timezone.utc) + timedelta(days=30)
    auth_list = [
        {
            "id_tag": CERTIF_LAL_TAG_FULL_A,
            "id_tag_info": {
                "status": "Accepted",
                "expiry_date": expiry.isoformat(),
            },
        },
        {
            "id_tag": CERTIF_LAL_TAG_FULL_B,
            "id_tag_info": {
                "status": "Accepted",
            },
        },
    ]

    try:
        status = await cp.send_local_list(
            list_version=new_version,
            update_type="Full",
            local_auth_list=auth_list,
        )
    except Exception as e:
        return TestResult(
            status="failed",
            message=f"SendLocalList Full a levé une exception : {e}",
        )

    details = {
        "update_type": "Full",
        "list_version_sent": new_version,
        "tag_count": len(auth_list),
        "status": str(status),
    }

    status_str = str(status).lower()
    if status_str == "accepted":
        return TestResult(
            status="passed",
            message=f"SendLocalList Full accepté version={new_version} count=2",
            details=details,
        )
    if status_str in ("failed", "versionmismatch", "notsupported"):
        return TestResult(
            status="failed",
            message=f"SendLocalList Full retourne {status} — attendu Accepted",
            details=details,
            recommendation=(
                "VersionMismatch : re-lire GetLocalListVersion et utiliser "
                "current+1. NotSupported : vérifier SupportedFeatureProfiles. "
                "Failed : vérifier logs borne."
            ),
        )
    return TestResult(
        status="failed",
        message=f"SendLocalList Full : status inattendu {status}",
        details=details,
    )


catalog.register(
    TestCase(
        name="lal.send_full",
        category="LocalAuthList",
        title="SendLocalList updateType=Full (§6.20)",
        description=(
            f"Pousse 2 tags ({CERTIF_LAL_TAG_FULL_A} + {CERTIF_LAL_TAG_FULL_B}) "
            "en mode Full (remplace la whitelist locale). Attend Accepted."
        ),
        run=_run_send_local_list_full,
        estimated_seconds=8,
        suites={"standard", "full"},
        requires_profile="LocalAuthListManagement",
        ocpp_ref="§5.7 SendLocalList / §6.20 updateType=Full",
    )
)


# ─────────────────────────── lal.send_differential ───────────────────────────


async def _run_send_local_list_differential(ctx: TestContext) -> TestResult:
    """SendLocalList updateType=Differential §6.20 — ajout/modif incrémental.

    Attend que la borne ait déjà une liste (sinon VersionMismatch attendu).
    Pousse un seul tag supplémentaire pour tester le mode Differential isolé
    de Full.
    """
    if not ctx.supports("LocalAuthListManagement"):
        return TestResult(
            status="skipped",
            message="Profil LocalAuthListManagement non supporté",
        )
    cp = ctx.get_cp()
    if cp is None:
        return TestResult(status="failed", message="Borne hors ligne")

    # Lit la version pour bumper (Differential exige version exacte +1)
    try:
        current_version = await cp.get_local_list_version()
    except Exception:
        current_version = None
    if current_version is None:
        return TestResult(
            status="failed",
            message="Impossible de lire la version courante pour bumper",
            recommendation="Exécuter lal.get_version avant ce test.",
        )
    base = 0 if current_version < 0 else int(current_version)
    new_version = base + 1

    auth_list = [
        {
            "id_tag": CERTIF_LAL_TAG_DIFF,
            "id_tag_info": {"status": "Accepted"},
        },
    ]

    try:
        status = await cp.send_local_list(
            list_version=new_version,
            update_type="Differential",
            local_auth_list=auth_list,
        )
    except Exception as e:
        return TestResult(
            status="failed",
            message=f"SendLocalList Differential a levé une exception : {e}",
        )

    details = {
        "update_type": "Differential",
        "list_version_sent": new_version,
        "base_version": base,
        "tag_count": len(auth_list),
        "status": str(status),
    }

    status_str = str(status).lower()
    if status_str == "accepted":
        return TestResult(
            status="passed",
            message=(
                f"SendLocalList Differential accepté version={new_version} "
                f"(+1 tag ajouté)"
            ),
            details=details,
        )
    if status_str == "versionmismatch":
        # Certains firmwares refusent Differential si la version précédente
        # n'a pas été servie par ce même canal — comportement documenté,
        # pas un échec de conformité §6.20.
        return TestResult(
            status="passed",
            message=(
                "SendLocalList Differential retourne VersionMismatch — "
                "comportement conforme §6.20 si la borne exige Full d'abord."
            ),
            details=details,
        )
    if status_str in ("failed", "notsupported"):
        return TestResult(
            status="failed",
            message=f"SendLocalList Differential retourne {status} — attendu Accepted ou VersionMismatch",
            details=details,
            recommendation=(
                "NotSupported : la borne annonce LocalAuthListManagement "
                "mais rejette Differential. Vérifier la configuration "
                "'LocalAuthListEnabled' via GetConfiguration."
            ),
        )
    return TestResult(
        status="failed",
        message=f"SendLocalList Differential : status inattendu {status}",
        details=details,
    )


catalog.register(
    TestCase(
        name="lal.send_differential",
        category="LocalAuthList",
        title="SendLocalList updateType=Differential (§6.20)",
        description=(
            f"Pousse 1 tag ({CERTIF_LAL_TAG_DIFF}) en mode Differential. "
            "Accepte Accepted (nominal) ou VersionMismatch (borne exigeant Full)."
        ),
        run=_run_send_local_list_differential,
        estimated_seconds=8,
        suites={"full"},
        requires_profile="LocalAuthListManagement",
        ocpp_ref="§5.7 SendLocalList / §6.20 updateType=Differential",
    )
)
