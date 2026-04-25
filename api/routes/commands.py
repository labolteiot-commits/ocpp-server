# api/routes/commands.py
"""
Commandes OCPP 1.6J — endpoints REST organisés par profil OCPP.

CORRECTIONS CONSERVÉES (héritées de la version Sprint 30) :
  BUG-A  set_power_limit utilise set_current_limit() (profile-aware)
  BUG-B  Validation max_amps AVANT envoi profil
  BUG-C  remote_stop = stop_charging() multi-étapes (TechnoVE-friendly)
  BUG-D  set_available reset le verrou serveur + boot_lock DB
  BUG-E  stop_charging persiste boot_lock pour survivre reboots TechnoVE

SPRINT 31 :
  - Refonte OpenAPI : tag par profil OCPP, summaries, exemples Pydantic
  - +3 endpoints (LocalAuthListManagement profile + DataTransfer Server→CP)
"""
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func as sa_func, desc, update as sa_update
from sqlalchemy.orm import selectinload
from pydantic import BaseModel, Field, field_validator, model_validator

from db.database import get_db
from db.models import (
    Session as ChargingSession, SessionStatus, Charger,
    Reservation, ReservationStatus, DataTransferLog,
    DiagnosticsRequest, ConfigSnapshot,
    FirmwareUpdate, OverrideIncident,
    LocalListVersion, StatusNotificationLog,
)
from core.logging import log

router = APIRouter()


# ════════════════════════════════════════════════════════════════════════════
#                            Tags par profil OCPP
# ════════════════════════════════════════════════════════════════════════════
# Les libellés correspondent à ceux déclarés dans api/main.py::tags_metadata
# pour que la barre latérale /docs regroupe par profil norme OCPP 1.6J.

TAG_CORE       = "🔌 Core Profile"
TAG_SMART      = "⚡ Smart Charging Profile"
TAG_RESERVE    = "🛡️ Reservation Profile"
TAG_FIRMWARE   = "🔧 Firmware Management Profile"
TAG_TRIGGER    = "🎯 Remote Trigger Profile"
TAG_LOCALLIST  = "📋 Local Auth List Management Profile"
TAG_DATA       = "🔁 Data Transfer Profile"
TAG_OVERRIDE   = "🔐 Anti-Override Drift (extension)"
TAG_DIAGSRV    = "🛠️ Diagnostics serveur (extension)"


# ════════════════════════════════════════════════════════════════════════════
#                            Schémas Pydantic — Requests
# ════════════════════════════════════════════════════════════════════════════
# Chaque modèle expose des `examples` pour que la page /docs affiche un
# payload prérempli prêt à exécuter ("Try it out").

class RemoteStartRequest(BaseModel):
    charger_id:   str   = Field(..., examples=["BORNE-CABANON4"], description="ID OCPP de la borne (regex `[A-Za-z0-9_-]{1,20}`).")
    connector_id: int   = Field(1, ge=0, le=10, examples=[1], description="N° de connecteur. 0 = borne entière (rare).")
    id_tag:       str   = Field("ADMIN", examples=["ADMIN"], description="idTag à autoriser. Doit exister dans la whitelist si activée.")
    max_amps:     Optional[float] = Field(None, ge=0, le=80, examples=[16.0], description="Limite immédiate (A) optionnelle. Si omise, courant nominal de la borne.")

class RemoteStopRequest(BaseModel):
    charger_id:     str  = Field(..., examples=["BORNE-CABANON4"])
    transaction_id: int  = Field(..., examples=[42], description="ID OCPP de la transaction. <0 = session synthétique (sans StartTx OCPP).")
    lock:           bool = Field(True, description="Si True, verrouille la borne (boot_lock DB) — survit aux reboots TechnoVE.")

class PowerLimitRequest(BaseModel):
    charger_id: str   = Field(..., examples=["BORNE-CABANON4"])
    max_amps:   float = Field(..., ge=0, le=80, examples=[20.0], description="Courant max (A). 0 = arrêt complet (utilise stop_charging avec lock=True).")

class AvailabilityRequest(BaseModel):
    charger_id:   str = Field(..., examples=["BORNE-CABANON4"])
    connector_id: int = Field(1, ge=0, le=10, examples=[1])

class UnlockRequest(BaseModel):
    charger_id:   str = Field(..., examples=["BORNE-CABANON4"])
    connector_id: int = Field(1, ge=1, le=10, examples=[1], description="Connector_id ≥ 1 (déverrouillage matériel borne, pas borne entière).")

class ResetRequest(BaseModel):
    charger_id: str = Field(..., examples=["BORNE-CABANON4"])
    reset_type: str = Field("Soft", pattern="^(Soft|Hard)$", examples=["Soft"], description="Soft = redémarrage logiciel ; Hard = power-cycle complet.")

class ClearCacheRequest(BaseModel):
    charger_id: str = Field(..., examples=["BORNE-CABANON4"])

class TriggerRequest(BaseModel):
    charger_id:   str = Field(..., examples=["BORNE-CABANON4"])
    message:      str = Field(..., examples=["StatusNotification"],
                              description="BootNotification | DiagnosticsStatusNotification | FirmwareStatusNotification | Heartbeat | MeterValues | StatusNotification.")
    connector_id: Optional[int] = Field(None, ge=0, le=10, examples=[1])

class ChargingProfileRequest(BaseModel):
    charger_id:       str   = Field(..., examples=["BORNE-CABANON4"])
    connector_id:     int   = Field(1, ge=0, le=10, examples=[1],
        description="Pour ChargePointMaxProfile, doit être 0 (§3.13.1). Pour TxDefault/TxProfile : 0 ou >0.")
    max_amps:         float = Field(..., gt=0, le=80, examples=[16.0])
    duration_seconds: Optional[int] = Field(None, ge=60, le=86400, examples=[3600],
        description="Durée du plan (s). Sans, plan permanent.")
    purpose:          str   = Field("TxDefaultProfile",
        pattern="^(ChargePointMaxProfile|TxDefaultProfile|TxProfile)$",
        examples=["ChargePointMaxProfile"],
        description=(
            "OCPP 1.6 §3.13.1 — ChargePointMaxProfile : s'applique à la borne entière, "
            "TOUT LE TEMPS (y compris hors transaction, état Preparing). Connector_id DOIT = 0. "
            "TxDefaultProfile : défaut pour toute nouvelle transaction (ignoré hors transaction). "
            "TxProfile : profil pour une transaction spécifique (transaction_id requis)."
        ))
    transaction_id:   Optional[int] = Field(None, ge=0, examples=[42],
        description="Requis SI purpose=TxProfile. Sinon ne PAS fournir.")
    stack_level:      int   = Field(0, ge=0, le=10, examples=[0],
        description="Stack level (§3.13.1). Plus haut = priorité plus forte si plusieurs profils actifs.")
    profile_id:       int   = Field(1, ge=0, examples=[1],
        description="ID unique du profil. Réutiliser le même ID écrase le précédent côté borne.")
    kind:             str   = Field("Relative",
        pattern="^(Absolute|Relative|Recurring)$",
        examples=["Relative"],
        description=(
            "Relative (recommandé pour push immédiat) : schedule démarre quand le profil devient actif. "
            "Absolute : schedule lié à start_schedule (UTC ISO). Si Absolute sans valid_from/start_schedule, "
            "comportement implementation-defined (TechnoVE l'ignore)."
        ))
    valid_from:       Optional[str] = Field(None, examples=["2026-04-25T05:00:00Z"],
        description="ISO 8601 UTC. Pour kind=Absolute, fortement recommandé pour lever toute ambiguïté.")
    valid_to:         Optional[str] = Field(None, examples=["2026-04-25T10:00:00Z"],
        description="ISO 8601 UTC. Si fourni, le profil expire à cette date.")

class ClearProfileRequest(BaseModel):
    charger_id:   str           = Field(..., examples=["BORNE-CABANON4"])
    connector_id: int           = Field(0, ge=0, le=10, examples=[0], description="0 = tous connecteurs (§6.7).")
    profile_id:   Optional[int] = Field(None, ge=0, examples=[1], description="Si fourni, n'efface QUE ce profil. Sinon, tous.")

class ConfigGetRequest(BaseModel):
    charger_id: str       = Field(..., examples=["BORNE-CABANON4"])
    keys:       list[str] = Field(default_factory=list, examples=[["HeartbeatInterval", "MeterValueSampleInterval"]],
                                  description="Liste vide = toutes les clés exposées par la borne.")

class ConfigSetRequest(BaseModel):
    charger_id: str = Field(..., examples=["BORNE-CABANON4"])
    key:        str = Field(..., examples=["MeterValueSampleInterval"])
    value:      str = Field(..., examples=["60"], description="Toujours en string OCPP — la borne valide selon le type interne.")

class ChangeAvailabilityRequest(BaseModel):
    charger_id:   str  = Field(..., examples=["BORNE-CABANON4"])
    connector_id: int  = Field(0, ge=0, le=10, examples=[0], description="0 = toute la borne (§6.1). Certaines TechnoVE buguées traitent 0 comme 1.")
    operative:    bool = Field(True, description="True → Operative (dispo) ; False → Inoperative (charge en cours = Scheduled).")

class ReserveRequest(BaseModel):
    charger_id:   str = Field(..., examples=["BORNE-CABANON4"])
    connector_id: int = Field(1, ge=0, le=10, examples=[1])
    id_tag:       str = Field(..., examples=["KIA-EV9"])
    expiry_date:  Optional[str] = Field(None, examples=["2026-04-23T18:00:00Z"],
                                        description="ISO 8601 UTC. Défaut = +1 h. Doit être strictement futur.")

class CancelReserveRequest(BaseModel):
    charger_id:     str = Field(..., examples=["BORNE-CABANON4"])
    reservation_id: int = Field(..., ge=1, examples=[1])

class GetDiagnosticsRequestBody(BaseModel):
    charger_id:     str = Field(..., examples=["BORNE-CABANON4"])
    location:       str = Field(..., examples=["ftp://upload.example.com/diag/"],
                                description="URL FTP/HTTP/HTTPS où la borne uploadera le fichier de diagnostic.")
    retries:        Optional[int] = Field(None, ge=0, le=10, examples=[3])
    retry_interval: Optional[int] = Field(None, ge=10, le=3600, examples=[60])
    start_time:     Optional[str] = Field(None, examples=["2026-04-22T00:00:00Z"],
                                          description="ISO 8601 dateTime, suffixe Z ou ±hh:mm requis (OCPP 1.6 §7.4).")
    stop_time:      Optional[str] = Field(None, examples=["2026-04-23T00:00:00Z"])

    @field_validator("start_time", "stop_time")
    @classmethod
    def _validate_iso8601(cls, v):
        if v is None or v == "":
            return v
        try:
            # Accepte 'Z' (Zulu) et offset ±hh:mm ; convertit en aware datetime puis re-sérialise en Z.
            dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
        except Exception:
            raise ValueError(f"start_time/stop_time doit être ISO 8601 OCPP §7.4 (ex. '2026-04-22T00:00:00Z'), reçu: {v!r}")
        if dt.tzinfo is None:
            raise ValueError(f"start_time/stop_time doit inclure un fuseau ('Z' ou ±hh:mm), reçu naïf: {v!r}")
        return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    @model_validator(mode="after")
    def _validate_time_order(self):
        if self.start_time and self.stop_time:
            try:
                a = datetime.fromisoformat(self.start_time.replace("Z", "+00:00"))
                b = datetime.fromisoformat(self.stop_time.replace("Z", "+00:00"))
                if a >= b:
                    raise ValueError("stop_time doit être strictement > start_time.")
            except ValueError:
                raise
        return self

class ExpectedConfigBody(BaseModel):
    charger_id: str = Field(..., examples=["BORNE-CABANON4"])
    key:        str = Field(..., examples=["MeterValueSampleInterval"])
    value:      str = Field(..., examples=["60"])

class UpdateFirmwareRequestBody(BaseModel):
    charger_id:     str = Field(..., examples=["BORNE-CABANON4"])
    location:       str = Field(..., examples=["http://192.168.1.210:5001/ota/firmware.bin"],
                                description="URL HTTP/HTTPS/FTP où la borne va télécharger le firmware.")
    retrieve_date:  Optional[str] = Field(None, examples=["2026-04-23T08:00:00Z"],
                                          description="ISO 8601 UTC. Défaut = maintenant + 1 minute.")
    retries:        Optional[int] = Field(None, ge=0, le=10, examples=[3])
    retry_interval: Optional[int] = Field(None, ge=10, le=3600, examples=[120])

# ── Sprint 31 — LocalAuthListManagement profile ─────────────────────────────

class LocalListEntry(BaseModel):
    id_tag:      str = Field(..., examples=["KIA-EV9"])
    id_tag_info: dict = Field(..., examples=[{"status": "Accepted"}],
                              description="Bloc IdTagInfo §7.18 : {status, expiryDate?, parentIdTag?}.")

class SendLocalListRequest(BaseModel):
    charger_id:    str  = Field(..., examples=["BORNE-CABANON4"])
    list_version:  int  = Field(..., ge=1, examples=[2], description="Version monotone croissante. Doit être > version actuelle borne.")
    update_type:   str  = Field("Full", pattern="^(Full|Differential)$", examples=["Full"],
                                description="Full = remplace tout ; Differential = patche les entrées listées.")
    local_authorization_list: list[LocalListEntry] = Field(
        default_factory=list,
        examples=[[
            {"id_tag": "KIA-EV9",  "id_tag_info": {"status": "Accepted"}},
            {"id_tag": "FORD-LIGHTNING", "id_tag_info": {"status": "Accepted"}},
            {"id_tag": "BLOCKED-01", "id_tag_info": {"status": "Blocked"}},
        ]],
        description="Liste vide autorisée si Full = vidange complète."
    )

# ── Sprint 31 — DataTransfer Server→CP ──────────────────────────────────────

class DataTransferRequest(BaseModel):
    charger_id: str = Field(..., examples=["BORNE-CABANON4"])
    vendor_id:  str = Field(..., examples=["com.example.diagcustom"],
                            description="Identifiant vendor (reverse-DNS recommandé).")
    message_id: Optional[str] = Field(None, examples=["GetCustomData"],
                                      description="Type de message custom (interne au vendor).")
    data:       Any = Field(None, examples=[{"key": "value"}],
                            description="Charge utile arbitraire — JSON sérialisé en string par le wrapper.")


# ════════════════════════════════════════════════════════════════════════════
#                            Helper interne
# ════════════════════════════════════════════════════════════════════════════

def _get_cp(request: Request, charger_id: str):
    cp = request.app.state.ocpp_server.get_charger(charger_id)
    if not cp:
        raise HTTPException(
            status_code=400,
            detail=f"Borne '{charger_id}' non connectée"
        )
    return cp


# ════════════════════════════════════════════════════════════════════════════
#                            🔌 Core Profile
# ════════════════════════════════════════════════════════════════════════════
# Authorize, BootNotification, Heartbeat, MeterValues, StartTransaction,
# StatusNotification, StopTransaction sont initiés par la borne (callbacks
# OCPP). Les endpoints REST ci-dessous concernent uniquement les opérations
# server-initiated du Core Profile.

@router.post(
    "/remote-start",
    tags=[TAG_CORE],
    summary="RemoteStartTransaction (§6.16)",
    description=(
        "Démarre une session de charge à distance.\n\n"
        "**Effet** : envoie `RemoteStartTransaction` (avec un éventuel "
        "`ChargingProfile` TxProfile si `max_amps` est fourni). La borne "
        "répond `Accepted` ou `Rejected`.\n\n"
        "**Verrou serveur** : levé automatiquement (un démarrage manuel "
        "annule un verrou de sécurité préalable).\n\n"
        "**Profile-aware** : si la borne est TechnoVE et qu'il y a déjà "
        "une transaction OCPP, le profil est appliqué via `TxProfile + "
        "transaction_id` ; sinon `TxDefaultProfile + cycle Inop/Op`."
    ),
)
async def remote_start(body: RemoteStartRequest, request: Request):
    cp = _get_cp(request, body.charger_id)
    cp._server_lock_active = False
    success = await cp.remote_start_transaction(
        connector_id=body.connector_id,
        id_tag=body.id_tag,
        max_amps=body.max_amps,
    )
    if not success:
        raise HTTPException(status_code=400, detail="La borne a refusé le démarrage")
    return {"detail": "Démarrage accepté"}


@router.post(
    "/remote-stop",
    tags=[TAG_CORE],
    summary="RemoteStopTransaction (§6.17) — séquence renforcée",
    description=(
        "Arrête une charge de façon **fiable** sur toutes les bornes "
        "(TechnoVE, Grizzl-E, génériques).\n\n"
        "**Séquence multi-étapes** (`stop_charging()`) :\n\n"
        "1. `SetChargingProfile(limit=0)` → coupe le courant physiquement.\n"
        "2. `RemoteStopTransaction` si transaction OCPP active.\n"
        "3. `ChangeAvailability(Inoperative)` si `lock=True`.\n"
        "4. `boot_lock=True` en DB si `lock=True` → survit aux reboots.\n\n"
        "Pour les sessions synthétiques (`transaction_id < 0`) : même "
        "séquence, sans étape 2."
    ),
)
async def remote_stop(
    body: RemoteStopRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    cp = _get_cp(request, body.charger_id)
    result = await cp.stop_charging(lock=body.lock, force_tx_id=body.transaction_id)
    log.info("remote_stop — séquence complète",
             charger_id=body.charger_id,
             locked=body.lock,
             method=result.get("method", ""))
    return {
        "detail": "Arrêt effectué" if result["stopped"] else "Arrêt partiel",
        "locked": result["locked"],
        "method": result["method"],
    }


@router.get(
    "/active-transaction/{charger_id}",
    tags=[TAG_CORE],
    summary="Transaction active (snapshot DB)",
    description="Retourne la transaction OCPP active (ou synthétique si `transaction_id < 0`).",
)
async def get_active_transaction(
    charger_id: str,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ChargingSession)
        .where(ChargingSession.charger_id == charger_id)
        .where(ChargingSession.status     == SessionStatus.ACTIVE)
        .order_by(desc(ChargingSession.start_time))
        .limit(1)
    )
    session = result.scalar_one_or_none()
    if not session:
        return {"transaction_id": None, "id_tag": None, "start_time": None}
    return {
        "transaction_id": session.transaction_id,
        "id_tag":         session.id_tag,
        "start_time":     session.start_time.isoformat(),
        "meter_start":    session.meter_start,
        "is_synthetic":   session.transaction_id is not None and session.transaction_id < 0,
    }


@router.post(
    "/set-available",
    tags=[TAG_CORE],
    summary="ChangeAvailability(Operative) — alias confort",
    description=(
        "Remet un connecteur en **Operative** ET lève le verrou serveur "
        "(`_server_lock_active`, `_boot_lock`, `chargers.boot_lock` DB).\n\n"
        "Sans cette levée, après un arrêt manuel, la borne resterait "
        "bloquée même si l'opérateur veut relancer une charge."
    ),
)
async def set_available(body: AvailabilityRequest, request: Request):
    cp = _get_cp(request, body.charger_id)
    cp._server_lock_active = False
    cp._boot_lock = False
    try:
        from db.database import AsyncSessionLocal
        async with AsyncSessionLocal() as db:
            await db.execute(
                sa_update(Charger).where(Charger.id == body.charger_id)
                .values(boot_lock=False)
            )
            await db.commit()
    except Exception as e:
        log.warning("set_available — mise à jour boot_lock échouée",
                    id=body.charger_id, error=str(e))

    success = await cp.set_available(body.connector_id)
    if not success:
        raise HTTPException(status_code=400, detail="La borne a refusé")
    return {"detail": f"Connecteur {body.connector_id} remis en disponible, verrou levé"}


@router.post(
    "/set-unavailable",
    tags=[TAG_CORE],
    summary="ChangeAvailability(Inoperative) — alias confort",
    description=(
        "Met un connecteur en **Inoperative**.\n\n"
        "**Note** : si une session est en cours, la borne répond "
        "`Scheduled` et passe Inoperative seulement après la fin. "
        "Pour couper immédiatement, utiliser `/set-power-limit` avec "
        "`max_amps=0` puis `/set-unavailable`."
    ),
)
async def set_unavailable(body: AvailabilityRequest, request: Request):
    cp = _get_cp(request, body.charger_id)
    success = await cp.set_unavailable(body.connector_id)
    if not success:
        raise HTTPException(status_code=400, detail="La borne a refusé")
    return {"detail": f"Connecteur {body.connector_id} mis hors service"}


@router.post(
    "/change-availability",
    tags=[TAG_CORE],
    summary="ChangeAvailability (§6.1) — explicite",
    description=(
        "Conforme OCPP 1.6 §6.1.\n\n"
        "* `connector_id=0` → toute la borne (tous connecteurs).\n"
        "* `connector_id≥1` → un connecteur précis.\n\n"
        "**Attention** : certaines TechnoVE anciennes traitent `0` comme "
        "`1`. Vérifier `logs/ocpp_raw/<id>.log` pour la réponse exacte "
        "(`Accepted` / `Rejected` / `Scheduled`)."
    ),
)
async def change_availability(body: ChangeAvailabilityRequest, request: Request):
    cp = _get_cp(request, body.charger_id)
    if body.operative:
        success = await cp.set_available(body.connector_id)
    else:
        success = await cp.set_unavailable(body.connector_id)
    if not success:
        raise HTTPException(status_code=400, detail="La borne a refusé")
    state = "Operative" if body.operative else "Inoperative"
    scope = "toute la borne" if body.connector_id == 0 else f"connecteur {body.connector_id}"
    return {"detail": f"ChangeAvailability {state} — {scope}"}


@router.post(
    "/unlock",
    tags=[TAG_CORE],
    summary="UnlockConnector (§6.30)",
    description=(
        "Déverrouille **mécaniquement** un connecteur. Utile quand le "
        "verrou reste coincé après un `StopTransaction`.\n\n"
        "**Réponse borne** : `Unlocked` / `UnlockFailed` / `NotSupported`. "
        "Le câble est considéré débranché par l'EV-side seulement si le "
        "verrou cède physiquement — pas une garantie logicielle."
    ),
)
async def unlock_connector(body: UnlockRequest, request: Request):
    cp = _get_cp(request, body.charger_id)
    status = await cp.unlock_connector(body.connector_id)
    if status == "Failed":
        raise HTTPException(status_code=400, detail="Déverrouillage échoué")
    return {"detail": f"Connecteur {body.connector_id} déverrouillé", "status": status}


@router.post(
    "/reset",
    tags=[TAG_CORE],
    summary="Reset (§6.15)",
    description=(
        "Redémarre la borne.\n\n"
        "* `Soft` = redémarrage logiciel (préserve la session OCPP en "
        "cours si possible).\n"
        "* `Hard` = power-cycle complet (perd la session OCPP, remet à "
        "BootNotification)."
    ),
)
async def reset_charger(body: ResetRequest, request: Request):
    cp = _get_cp(request, body.charger_id)
    success = await cp.reset(body.reset_type)
    if not success:
        raise HTTPException(status_code=400, detail="La borne a refusé le reset")
    return {"detail": f"Reset {body.reset_type} accepté"}


@router.post(
    "/clear-cache",
    tags=[TAG_CORE],
    summary="ClearCache (§6.4)",
    description="Vide le cache d'authentification local de la borne (idTag récents).",
)
async def clear_cache(body: ClearCacheRequest, request: Request):
    cp = _get_cp(request, body.charger_id)
    success = await cp.clear_cache()
    if not success:
        raise HTTPException(status_code=400, detail="ClearCache refusé")
    return {"detail": "Cache vidé"}


@router.post(
    "/config/get",
    tags=[TAG_CORE],
    summary="GetConfiguration (§6.10)",
    description=(
        "Liste les clés de configuration OCPP exposées par la borne.\n\n"
        "Liste vide = toutes les clés. Le résultat alimente aussi le "
        "cache `_config_cache` interne (utilisé par l'anti-override "
        "drift detection)."
    ),
)
async def get_configuration(body: ConfigGetRequest, request: Request):
    cp = _get_cp(request, body.charger_id)
    config = await cp.get_configuration(body.keys)
    return {"charger_id": body.charger_id, "configuration": config}


@router.post(
    "/config/set",
    tags=[TAG_CORE],
    summary="ChangeConfiguration (§6.2)",
    description=(
        "Modifie une clé de configuration sur la borne.\n\n"
        "**Statuts possibles** : `Accepted`, `Rejected` (read-only), "
        "`RebootRequired`, `NotSupported` (clé inconnue).\n\n"
        "**Astuce anti-override** : pour qu'une clé soit re-pushée "
        "automatiquement si le cloud fabricant la modifie en douce, "
        "utiliser `/config-expected` (qui appelle ce endpoint + "
        "persiste l'expected)."
    ),
)
async def set_configuration(body: ConfigSetRequest, request: Request):
    cp = _get_cp(request, body.charger_id)
    status = await cp.change_configuration(body.key, body.value)
    if status == "Failed":
        raise HTTPException(status_code=400, detail="ChangeConfiguration échoué")
    if status == "NotSupported":
        raise HTTPException(status_code=400, detail=f"Clé '{body.key}' non supportée")
    if status == "Rejected":
        raise HTTPException(status_code=400, detail=f"Clé '{body.key}' rejetée (read-only?)")
    return {"detail": f"{body.key} = {body.value}", "status": status}


@router.get(
    "/config/cached/{charger_id}",
    tags=[TAG_CORE],
    summary="Cache local de configuration (sans appel OCPP)",
    description=(
        "Retourne le snapshot le plus récent récupéré par "
        "`GetConfiguration`. Pas d'appel réseau vers la borne."
    ),
)
async def get_cached_config(charger_id: str, request: Request):
    cp = _get_cp(request, charger_id)
    return {"charger_id": charger_id, "configuration": cp._config_cache}


# ════════════════════════════════════════════════════════════════════════════
#                            🎯 Remote Trigger Profile
# ════════════════════════════════════════════════════════════════════════════

@router.post(
    "/trigger",
    tags=[TAG_TRIGGER],
    summary="TriggerMessage (§6.28)",
    description=(
        "Force la borne à émettre un message **immédiatement**.\n\n"
        "**Messages valides** :\n\n"
        "* `BootNotification`\n"
        "* `DiagnosticsStatusNotification`\n"
        "* `FirmwareStatusNotification`\n"
        "* `Heartbeat`\n"
        "* `MeterValues` *(connector_id optionnel)*\n"
        "* `StatusNotification` *(connector_id optionnel)*\n\n"
        "**Réponses borne** : `Accepted` / `Rejected` / `NotImplemented`."
    ),
)
async def trigger_message(body: TriggerRequest, request: Request):
    cp = _get_cp(request, body.charger_id)
    status = await cp.trigger_message(body.message, body.connector_id)
    if status == "Failed":
        raise HTTPException(status_code=400, detail="TriggerMessage échoué")
    return {"detail": f"TriggerMessage {body.message} envoyé", "status": status}


# ════════════════════════════════════════════════════════════════════════════
#                            ⚡ Smart Charging Profile
# ════════════════════════════════════════════════════════════════════════════

@router.post(
    "/set-power-limit",
    tags=[TAG_SMART],
    summary="Limite de courant immédiate (alias profile-aware)",
    description=(
        "Impose une limite de courant **immédiate**.\n\n"
        "Profile-aware (TechnoVE / Grizzl-E / générique) :\n\n"
        "* TechnoVE + session OCPP : `TxProfile + transaction_id` (effet immédiat).\n"
        "* TechnoVE sans session : `TxDefaultProfile + cycle Inop/Op`.\n"
        "* Générique : `ChargePointMaxProfile` sur connector 0.\n\n"
        "* `max_amps=0` → utilise `stop_charging()` (verrou + boot_lock).\n"
        "* `max_amps>0` → lève le verrou serveur si actif.\n"
        "* `max_amps>hw_max` → 422 (modifier d'abord *Courant max* du modèle)."
    ),
)
async def set_power_limit(body: PowerLimitRequest, request: Request):
    cp = _get_cp(request, body.charger_id)
    if body.max_amps < 0:
        raise HTTPException(status_code=422, detail="max_amps ne peut pas être négatif")
    if body.max_amps == 0:
        result = await cp.stop_charging(lock=True)
        return {"detail": "Charge coupée et borne verrouillée",
                "method": result.get("method", "")}
    hw_max = cp._default_max_amps
    if hw_max and body.max_amps > hw_max:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Courant demandé ({body.max_amps:.0f}A) supérieur au maximum configuré "
                f"pour cette borne ({hw_max:.0f}A). "
                f"Modifiez 'Courant max' dans les paramètres si vous voulez dépasser cette limite."
            )
        )
    status = await cp.set_current_limit(body.max_amps)
    if status == "Failed":
        raise HTTPException(status_code=400, detail="SetChargingProfile échoué")
    if getattr(cp, '_server_lock_active', False):
        cp._server_lock_active = False
        log.info("set_power_limit — verrou serveur levé (limite positive)", id=cp.id)
    return {"detail": f"Limite imposée à {body.max_amps}A", "status": status}


@router.post(
    "/charging-profile/set",
    tags=[TAG_SMART],
    summary="SetChargingProfile (§6.21) — explicite",
    description=(
        "Construit un profil `TxDefaultProfile/Absolute` mono-période et "
        "l'envoie. Plus simple que `/set-power-limit` pour les cas où "
        "l'on veut une **durée** explicite (`duration_seconds`)."
    ),
)
async def set_charging_profile_endpoint(body: ChargingProfileRequest, request: Request):
    cp = _get_cp(request, body.charger_id)

    # Cohérence sémantique OCPP 1.6 §3.13.1
    if body.purpose == "ChargePointMaxProfile" and body.connector_id != 0:
        raise HTTPException(
            status_code=400,
            detail="ChargePointMaxProfile DOIT être posé sur connector_id=0 (§3.13.1)."
        )
    if body.purpose == "TxProfile" and body.transaction_id is None:
        raise HTTPException(
            status_code=400,
            detail="TxProfile requiert transaction_id (§7.10)."
        )
    if body.purpose != "TxProfile" and body.transaction_id is not None:
        raise HTTPException(
            status_code=400,
            detail=f"transaction_id ne doit PAS être fourni pour {body.purpose} (§7.10)."
        )
    if body.kind == "Absolute" and not body.valid_from:
        # Auto-injection : si Absolute sans valid_from, on met now() pour lever l'ambiguïté §7.10
        body.valid_from = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    schedule = {
        "charging_rate_unit":       "A",
        "charging_schedule_period": [{"start_period": 0, "limit": body.max_amps}],
    }
    if body.duration_seconds:
        schedule["duration"] = body.duration_seconds
    if body.kind == "Absolute" and body.valid_from:
        schedule["start_schedule"] = body.valid_from

    profile = {
        "charging_profile_id":      body.profile_id,
        "stack_level":              body.stack_level,
        "charging_profile_purpose": body.purpose,
        "charging_profile_kind":    body.kind,
        "charging_schedule":        schedule,
    }
    if body.transaction_id is not None:
        profile["transaction_id"] = body.transaction_id
    if body.valid_from:
        profile["valid_from"] = body.valid_from
    if body.valid_to:
        profile["valid_to"] = body.valid_to

    status = await cp.set_charging_profile(body.connector_id, profile)
    if status == "Failed":
        raise HTTPException(status_code=400, detail="SetChargingProfile échoué")
    return {
        "detail": f"Profil {body.purpose} {body.max_amps}A appliqué (kind={body.kind}, stack={body.stack_level})",
        "status": status,
    }


@router.post(
    "/charging-profile/clear",
    tags=[TAG_SMART],
    summary="ClearChargingProfile (§6.7) + invalidation snapshot A8",
    description=(
        "Efface un profil ET marque le snapshot persisté `expired_at` "
        "(Sprint 24 A8). Sans cette invalidation, A9 ré-appliquerait au "
        "boot un profil que l'opérateur vient d'effacer → incohérence."
    ),
)
async def clear_charging_profile(body: ClearProfileRequest, request: Request):
    cp = _get_cp(request, body.charger_id)
    status = await cp.clear_charging_profile(
        connector_id=body.connector_id,
        profile_id=body.profile_id,
    )
    if status == "Failed":
        raise HTTPException(status_code=400, detail="ClearChargingProfile échoué")
    return {"detail": "Profil effacé", "status": status}


@router.get(
    "/charging-profile/snapshots/{charger_id}",
    tags=[TAG_SMART],
    summary="Snapshots persistés (audit A9/A10)",
    description=(
        "Liste les profils de charge persistés par sprint A9. Filtrable "
        "via `?include_expired=true` pour retrouver l'historique des "
        "profils nettoyés (A10 = soft expire, pas DELETE)."
    ),
)
async def list_profile_snapshots(
    charger_id: str,
    include_expired: bool = False,
    db: AsyncSession = Depends(get_db),
):
    from db.models import ChargingProfileSnapshot
    q = select(ChargingProfileSnapshot).where(
        ChargingProfileSnapshot.charger_id == charger_id
    )
    if not include_expired:
        q = q.where(ChargingProfileSnapshot.expired_at.is_(None))
    result = await db.execute(q.order_by(desc(ChargingProfileSnapshot.applied_at)))
    snapshots = result.scalars().all()
    return {
        "charger_id": charger_id,
        "count": len(snapshots),
        "snapshots": [
            {
                "id": s.id,
                "connector_id": s.connector_id,
                "purpose": s.purpose,
                "applied_at": s.applied_at.isoformat() if s.applied_at else None,
                "expired_at": s.expired_at.isoformat() if s.expired_at else None,
                "profile": s.profile_json,
            }
            for s in snapshots
        ],
    }


@router.get(
    "/composite-schedule/{charger_id}/{connector_id}",
    tags=[TAG_SMART],
    summary="GetCompositeSchedule (§6.9)",
    description=(
        "Demande à la borne de calculer le **planning composite** "
        "(résultante de tous les profils stackés) sur les `duration` "
        "secondes à venir. Utile pour valider qu'un profil scheduler "
        "se traduit bien comme prévu."
    ),
)
async def get_composite_schedule(
    charger_id: str,
    connector_id: int,
    duration: int = 86400,
    request: Request = None,
):
    cp = _get_cp(request, charger_id)
    try:
        from ocpp.v16 import call as ocpp_call
        resp = await cp.call(ocpp_call.GetCompositeSchedule(
            connector_id=connector_id,
            duration=duration,
        ))
        return {
            "status":          resp.status,
            "connector_id":    getattr(resp, "connector_id", connector_id),
            "schedule_start":  getattr(resp, "schedule_start", None),
            "charging_schedule": getattr(resp, "charging_schedule", None),
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"GetCompositeSchedule échoué: {e}")


# ════════════════════════════════════════════════════════════════════════════
#                            🛡️ Reservation Profile (Sprint 28 A7)
# ════════════════════════════════════════════════════════════════════════════
# Cycle persistance :
#   ACTIVE   : ReserveNow Accepted par la borne
#   REJECTED : ReserveNow refusé (Occupied/Faulted/Unavailable)
#   USED     : StartTransaction matché (cf. ChargePoint._consume_reservation)
#   CANCELLED: CancelReservation explicite
#   EXPIRED  : boucle 60s (ocpp_server._reservation_expiry_loop)

@router.post(
    "/reserve",
    tags=[TAG_RESERVE],
    summary="ReserveNow (§6.18) + persistance",
    description=(
        "Crée une réservation **persistée** et appelle `ReserveNow` sur "
        "la borne.\n\n"
        "Le `reservation_id` est **généré côté serveur** (max+1 par "
        "borne) — à conserver côté UI pour annulation ultérieure via "
        "`/cancel-reserve`.\n\n"
        "Si la borne refuse (Occupied/Faulted/Unavailable), la ligne "
        "est tout de même persistée avec `db_status=\"REJECTED\"` pour "
        "audit (HTTP 200, pas 400)."
    ),
)
async def reserve_now(
    body: ReserveRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    cp = _get_cp(request, body.charger_id)
    now = datetime.now(timezone.utc)
    if body.expiry_date:
        try:
            expiry_dt = datetime.fromisoformat(body.expiry_date.replace("Z", "+00:00"))
            if expiry_dt.tzinfo is None:
                expiry_dt = expiry_dt.replace(tzinfo=timezone.utc)
        except Exception:
            raise HTTPException(status_code=400, detail=f"expiry_date invalide: {body.expiry_date}")
    else:
        expiry_dt = now + timedelta(hours=1)
    if expiry_dt <= now:
        raise HTTPException(status_code=400, detail="expiry_date doit être dans le futur")
    r = await db.execute(
        select(sa_func.max(Reservation.reservation_id))
        .where(Reservation.charger_id == body.charger_id)
    )
    next_rid = (r.scalar() or 0) + 1
    try:
        from ocpp.v16 import call as ocpp_call
        # M2: dateTime OCPP 1.6 §7.4 — préférer suffixe 'Z' (interop bornes TechnoVE/Grizzl-E)
        expiry_iso = expiry_dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        resp = await cp.call(ocpp_call.ReserveNow(
            connector_id=body.connector_id,
            expiry_date=expiry_iso,
            id_tag=body.id_tag,
            reservation_id=next_rid,
        ))
        ocpp_status = getattr(resp, "status", "Unknown")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"ReserveNow échoué: {e}")
    status_db = ReservationStatus.ACTIVE if ocpp_status == "Accepted" else ReservationStatus.REJECTED
    res = Reservation(
        reservation_id=next_rid,
        charger_id=body.charger_id,
        connector_id=body.connector_id,
        id_tag=body.id_tag,
        expiry_date=expiry_dt,
        status=status_db,
        created_at=now,
        updated_at=now,
        ocpp_status=ocpp_status,
    )
    db.add(res)
    try:
        await db.commit()
        await db.refresh(res)
    except Exception as e:
        await db.rollback()
        log.error("Reservation insert failed", charger=body.charger_id, error=str(e))
        raise HTTPException(status_code=500, detail=f"DB insert failed: {e}")
    log.info("ReserveNow", charger=body.charger_id, reservation_id=next_rid,
             connector=body.connector_id, id_tag=body.id_tag,
             expiry=expiry_dt.isoformat(), ocpp_status=ocpp_status)
    if ocpp_status != "Accepted":
        return {
            "detail": f"Réservation refusée par la borne ({ocpp_status})",
            "status": ocpp_status,
            "reservation_id": next_rid,
            "db_status": status_db.value,
        }
    return {
        "detail": "Réservation acceptée",
        "status": ocpp_status,
        "reservation_id": next_rid,
        "db_status": status_db.value,
        "expiry_date": expiry_dt.isoformat(),
    }


@router.post(
    "/cancel-reserve",
    tags=[TAG_RESERVE],
    summary="CancelReservation (§6.5)",
    description=(
        "Annule une réservation : appel OCPP `CancelReservation` + "
        "update DB (`status=CANCELLED`).\n\n"
        "Même si la borne répond `NotFound`, on marque CANCELLED côté "
        "serveur — le serveur reste source de vérité."
    ),
)
async def cancel_reserve(
    body: CancelReserveRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    cp = _get_cp(request, body.charger_id)
    r = await db.execute(
        select(Reservation).where(
            Reservation.charger_id == body.charger_id,
            Reservation.reservation_id == body.reservation_id,
        )
    )
    res = r.scalar_one_or_none()
    if res is None:
        raise HTTPException(
            status_code=404,
            detail=f"Réservation {body.reservation_id} introuvable pour {body.charger_id}",
        )
    if res.status != ReservationStatus.ACTIVE:
        raise HTTPException(
            status_code=400,
            detail=f"Réservation non annulable (status={res.status.value})",
        )
    try:
        from ocpp.v16 import call as ocpp_call
        resp = await cp.call(ocpp_call.CancelReservation(
            reservation_id=body.reservation_id,
        ))
        ocpp_status = getattr(resp, "status", "Unknown")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"CancelReservation échoué: {e}")
    res.status = ReservationStatus.CANCELLED
    res.updated_at = datetime.now(timezone.utc)
    res.ocpp_status = (res.ocpp_status or "") + f" | cancel={ocpp_status}"
    await db.commit()
    log.info("CancelReservation", charger=body.charger_id,
             reservation_id=body.reservation_id, ocpp_status=ocpp_status)
    return {
        "detail": "Réservation annulée",
        "status": ocpp_status,
        "reservation_id": body.reservation_id,
        "db_status": ReservationStatus.CANCELLED.value,
    }


@router.get(
    "/reservations/{charger_id}",
    tags=[TAG_RESERVE],
    summary="Liste réservations d'une borne",
    description="Par défaut, ACTIVE seulement. `?include_inactive=true` pour l'historique complet.",
)
async def list_reservations(
    charger_id: str,
    include_inactive: bool = False,
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Reservation).where(Reservation.charger_id == charger_id)
    if not include_inactive:
        stmt = stmt.where(Reservation.status == ReservationStatus.ACTIVE)
    stmt = stmt.order_by(desc(Reservation.created_at))
    r = await db.execute(stmt)
    rows = r.scalars().all()
    return {
        "charger_id": charger_id,
        "count": len(rows),
        "reservations": [
            {
                "reservation_id": x.reservation_id,
                "connector_id":   x.connector_id,
                "id_tag":         x.id_tag,
                "status":         x.status.value,
                "expiry_date":    x.expiry_date.isoformat() if x.expiry_date else None,
                "created_at":     x.created_at.isoformat() if x.created_at else None,
                "updated_at":     x.updated_at.isoformat() if x.updated_at else None,
                "ocpp_status":    x.ocpp_status,
            }
            for x in rows
        ],
    }


# ════════════════════════════════════════════════════════════════════════════
#                            🔁 Data Transfer Profile
# ════════════════════════════════════════════════════════════════════════════

@router.post(
    "/data-transfer/send",
    tags=[TAG_DATA],
    summary="DataTransfer Server→CP (§6.6) — Sprint 31",
    description=(
        "Envoie un `DataTransfer` **vers la borne** avec audit "
        "automatique dans `data_transfer_logs` (direction=`out`).\n\n"
        "Le `vendor_id` doit être un identifiant reverse-DNS du "
        "fabricant (ex: `com.technove.diag`). `data` peut être une "
        "string ou un objet JSON (sérialisé automatiquement).\n\n"
        "**Réponses OCPP** : `Accepted` / `Rejected` / `UnknownVendorId` / "
        "`UnknownMessageId`."
    ),
)
async def send_data_transfer(body: DataTransferRequest, request: Request):
    cp = _get_cp(request, body.charger_id)
    response_data = await cp.data_transfer(
        vendor_id=body.vendor_id,
        message_id=body.message_id or "",
        data=body.data if body.data is not None else "",
    )
    return {
        "detail": "DataTransfer envoyé",
        "vendor_id": body.vendor_id,
        "message_id": body.message_id,
        "response_data": response_data,
    }


@router.get(
    "/data-transfer/logs",
    tags=[TAG_DATA],
    summary="Audit log DataTransfer (in + out)",
    description=(
        "Liste les `DataTransfer` persistés (vendor-specific). "
        "Filtrable via `charger_id`, `direction` (`in`|`out`), `limit` "
        "(1..500). Ordonné par timestamp décroissant."
    ),
)
async def list_data_transfer_logs(
    charger_id: Optional[str] = None,
    direction:  Optional[str] = None,
    limit:      int           = 100,
    db: AsyncSession = Depends(get_db),
):
    limit = max(1, min(500, limit))
    stmt = select(DataTransferLog)
    if charger_id:
        stmt = stmt.where(DataTransferLog.charger_id == charger_id)
    if direction in ("in", "out"):
        stmt = stmt.where(DataTransferLog.direction == direction)
    stmt = stmt.order_by(desc(DataTransferLog.timestamp)).limit(limit)
    r = await db.execute(stmt)
    rows = r.scalars().all()
    return {
        "count": len(rows),
        "logs": [
            {
                "id":         x.id,
                "charger_id": x.charger_id,
                "direction":  x.direction,
                "vendor_id":  x.vendor_id,
                "message_id": x.message_id,
                "data":       x.data,
                "response":   x.response,
                "timestamp":  x.timestamp.isoformat() if x.timestamp else None,
            }
            for x in rows
        ],
    }


# ════════════════════════════════════════════════════════════════════════════
#                            📋 Local Auth List Management Profile
# ════════════════════════════════════════════════════════════════════════════
# Permet à la borne d'autoriser des idTags **hors-ligne** (sans Authorize
# vers le serveur). Sprint 31 — endpoints REST autour des méthodes existantes
# `cp.send_local_list()` et de la nouvelle `cp.get_local_list_version()`.

@router.get(
    "/local-list/version/{charger_id}",
    tags=[TAG_LOCALLIST],
    summary="GetLocalListVersion (§6.11) — Sprint 31",
    description=(
        "Demande à la borne sa version actuelle de Local Authorization "
        "List.\n\n"
        "**Conventions OCPP** :\n"
        "* `-1` → liste non supportée par la borne.\n"
        "* `0`  → liste vide.\n"
        "* `N≥1` → version courante.\n\n"
        "Retourne `null` si la borne n'a pas répondu (timeout, "
        "déconnexion)."
    ),
)
async def get_local_list_version(charger_id: str, request: Request):
    cp = _get_cp(request, charger_id)
    version = await cp.get_local_list_version()
    return {
        "charger_id": charger_id,
        "list_version": version,
        "supported": version is not None and version >= 0,
    }


@router.post(
    "/local-list/send",
    tags=[TAG_LOCALLIST],
    summary="SendLocalList (§6.20) — Sprint 31",
    description=(
        "Envoie ou patche la Local Authorization List de la borne.\n\n"
        "* `update_type=Full` : remplace **tout** le contenu (liste vide "
        "autorisée = vidange complète).\n"
        "* `update_type=Differential` : ajoute/met à jour les entrées "
        "listées sans toucher au reste.\n\n"
        "**Pré-requis** : `list_version` doit être strictement supérieur "
        "à la version actuelle de la borne (sinon `VersionMismatch`). "
        "Vérifier au préalable via `/local-list/version/{charger_id}`.\n\n"
        "**Réponses borne** : `Accepted` / `Failed` / `NotSupported` / "
        "`VersionMismatch`."
    ),
)
async def send_local_list(body: SendLocalListRequest, request: Request):
    cp = _get_cp(request, body.charger_id)
    auth_list = [entry.model_dump() for entry in body.local_authorization_list]
    status = await cp.send_local_list(
        list_version=body.list_version,
        update_type=body.update_type,
        local_auth_list=auth_list,
    )
    if status == "Failed":
        raise HTTPException(status_code=400, detail="SendLocalList échoué")
    return {
        "detail": f"SendLocalList envoyé ({len(auth_list)} entrée(s), "
                  f"version={body.list_version}, type={body.update_type})",
        "status": status,
    }


# ════════════════════════════════════════════════════════════════════════════
#                            🔧 Firmware Management Profile
# ════════════════════════════════════════════════════════════════════════════

@router.post(
    "/get-diagnostics",
    tags=[TAG_FIRMWARE],
    summary="GetDiagnostics (§6.8) + persistance",
    description=(
        "Déclenche `GetDiagnostics` + persiste la demande dans "
        "`diagnostics_requests` (Sprint 29 Volet B).\n\n"
        "`location` = URL d'upload (FTP/HTTP/HTTPS) où la borne enverra "
        "le fichier. Le suivi se fait ensuite via les "
        "`DiagnosticsStatusNotification` entrantes (Idle → Uploading → "
        "Uploaded ou UploadFailed)."
    ),
)
async def get_diagnostics(body: GetDiagnosticsRequestBody, request: Request):
    cp = _get_cp(request, body.charger_id)
    result = await cp.get_diagnostics(
        location=body.location,
        retries=body.retries,
        retry_interval=body.retry_interval,
        start_time=body.start_time,
        stop_time=body.stop_time,
    )
    return {
        "detail": "GetDiagnostics envoyé",
        "request_id": result.get("request_id"),
        "filename": result.get("filename"),
    }


@router.get(
    "/diagnostics/{charger_id}",
    tags=[TAG_FIRMWARE],
    summary="Historique GetDiagnostics",
    description="Les N (défaut 20, max 200) dernières demandes pour une borne, ordonnées par date.",
)
async def list_diagnostics(
    charger_id: str,
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
):
    limit = max(1, min(200, limit))
    r = await db.execute(
        select(DiagnosticsRequest)
        .where(DiagnosticsRequest.charger_id == charger_id)
        .order_by(desc(DiagnosticsRequest.requested_at))
        .limit(limit)
    )
    rows = r.scalars().all()
    return {
        "charger_id": charger_id,
        "count": len(rows),
        "requests": [
            {
                "id":                x.id,
                "location":          x.location,
                "retries":           x.retries,
                "retry_interval":    x.retry_interval,
                "start_time":        x.start_time.isoformat() if x.start_time else None,
                "stop_time":         x.stop_time.isoformat() if x.stop_time else None,
                "requested_at":      x.requested_at.isoformat() if x.requested_at else None,
                "filename_returned": x.filename_returned,
                "status":            x.status,
                "status_updated_at": x.status_updated_at.isoformat() if x.status_updated_at else None,
            }
            for x in rows
        ],
    }


@router.post(
    "/update-firmware",
    tags=[TAG_FIRMWARE],
    summary="UpdateFirmware (§6.19) + persistance",
    description=(
        "Déclenche `UpdateFirmware` + persiste dans `firmware_updates` "
        "(Sprint 30 Volet A).\n\n"
        "* `location` = URL HTTP/HTTPS/FTP où la borne télécharge le "
        "firmware.\n"
        "* `retrieve_date` = ISO 8601 UTC, défaut = `now + 1 minute`.\n\n"
        "**Cycle status** : `Requested` → `Downloading` → `Downloaded` → "
        "`Installing` → `Installed` (terminal) ou `DownloadFailed` / "
        "`InstallationFailed`. Mis à jour via "
        "`FirmwareStatusNotification`. Complétion automatique au boot "
        "suivant si la version a changé."
    ),
)
async def update_firmware(body: UpdateFirmwareRequestBody, request: Request,
                          db: AsyncSession = Depends(get_db)):
    cp = _get_cp(request, body.charger_id)
    if body.retrieve_date:
        try:
            retrieve_dt = datetime.fromisoformat(body.retrieve_date.replace("Z", "+00:00"))
            if retrieve_dt.tzinfo is None:
                retrieve_dt = retrieve_dt.replace(tzinfo=timezone.utc)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"retrieve_date invalide: {e}")
    else:
        retrieve_dt = datetime.now(timezone.utc) + timedelta(minutes=1)
    charger = await db.get(Charger, body.charger_id)
    current_version = charger.firmware_version if charger else None
    fw = FirmwareUpdate(
        charger_id=body.charger_id,
        location=body.location,
        retrieve_date=retrieve_dt,
        retries=body.retries,
        retry_interval=body.retry_interval,
        requested_at=datetime.now(timezone.utc),
        status="Requested",
        firmware_version_before=current_version,
    )
    db.add(fw)
    await db.commit()
    await db.refresh(fw)
    call_status = await cp.update_firmware(
        location=body.location,
        retrieve_date=retrieve_dt,
        retries=body.retries,
        retry_interval=body.retry_interval,
    )
    return {
        "id":              fw.id,
        "status":          fw.status,
        "call_status":     call_status,
        "location":        body.location,
        "retrieve_date":   retrieve_dt.isoformat(),
        "firmware_before": current_version,
    }


@router.get(
    "/firmware-updates/{charger_id}",
    tags=[TAG_FIRMWARE],
    summary="Historique UpdateFirmware",
    description="N dernières demandes (défaut 20, max 200) avec transitions before→after.",
)
async def list_firmware_updates(
    charger_id: str,
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
):
    limit = max(1, min(200, limit))
    r = await db.execute(
        select(FirmwareUpdate)
        .where(FirmwareUpdate.charger_id == charger_id)
        .order_by(desc(FirmwareUpdate.requested_at))
        .limit(limit)
    )
    rows = r.scalars().all()
    return {
        "charger_id": charger_id,
        "count":      len(rows),
        "updates": [
            {
                "id":                      x.id,
                "location":                x.location,
                "retrieve_date":           x.retrieve_date.isoformat() if x.retrieve_date else None,
                "retries":                 x.retries,
                "retry_interval":          x.retry_interval,
                "requested_at":            x.requested_at.isoformat() if x.requested_at else None,
                "status":                  x.status,
                "status_updated_at":       x.status_updated_at.isoformat() if x.status_updated_at else None,
                "firmware_version_before": x.firmware_version_before,
                "firmware_version_after":  x.firmware_version_after,
                "notes":                   x.notes,
            }
            for x in rows
        ],
    }


# ════════════════════════════════════════════════════════════════════════════
#                            🔐 Anti-Override Drift (extension)
# ════════════════════════════════════════════════════════════════════════════
# Sprint 29 Volet D + Sprint 30 Volet C
# Détection de modifications non autorisées :
#   * de configuration (cloud fabricant qui change HeartbeatInterval, ...)
#   * de courant Current.Import (borne qui ignore le ChargingProfile)

@router.get(
    "/config-snapshot/{charger_id}",
    tags=[TAG_OVERRIDE],
    summary="Snapshot configuration + flag drift",
    description=(
        "Toutes les clés persistées (`config_snapshots`) avec leur "
        "`expected_value` vs `observed_value`. Filtrable via "
        "`?watched_only=true` pour ne lister que les clés sensibles "
        "(HeartbeatInterval, MeterValueSampleInterval, etc.)."
    ),
)
async def get_config_snapshot(
    charger_id: str,
    watched_only: bool = False,
    db: AsyncSession = Depends(get_db),
):
    stmt = select(ConfigSnapshot).where(ConfigSnapshot.charger_id == charger_id)
    r = await db.execute(stmt.order_by(ConfigSnapshot.key))
    rows = r.scalars().all()
    out = []
    for s in rows:
        drift = (
            s.expected_value is not None
            and not s.readonly
            and s.observed_value != s.expected_value
        )
        if watched_only:
            from core.charge_point import ANTI_OVERRIDE_WATCHED_KEYS
            if s.key not in ANTI_OVERRIDE_WATCHED_KEYS:
                continue
        out.append({
            "key":            s.key,
            "expected_value": s.expected_value,
            "observed_value": s.observed_value,
            "readonly":       s.readonly,
            "drift":          drift,
            "drift_count":    s.drift_count,
            "last_drift_at":  s.last_drift_at.isoformat() if s.last_drift_at else None,
            "last_heal_at":   s.last_heal_at.isoformat() if s.last_heal_at else None,
            "captured_at":    s.captured_at.isoformat() if s.captured_at else None,
        })
    return {"charger_id": charger_id, "count": len(out), "keys": out}


@router.post(
    "/config-expected",
    tags=[TAG_OVERRIDE],
    summary="Fixer une valeur attendue + push immédiat",
    description=(
        "Définit `expected_value` pour une clé + pousse immédiatement "
        "via `ChangeConfiguration`.\n\n"
        "Si `observed_value` dérive ensuite (cloud fabricant intrusif), "
        "la boucle `_config_drift_loop` (5 min) remet `expected` "
        "automatiquement."
    ),
)
async def set_config_expected(body: ExpectedConfigBody, request: Request):
    cp = _get_cp(request, body.charger_id)
    status = await cp.set_expected_config(body.key, body.value)
    if status == "Failed":
        raise HTTPException(
            status_code=400,
            detail=f"ChangeConfiguration échoué pour {body.key}",
        )
    return {"detail": f"{body.key} = {body.value} (expected)", "status": status}


@router.get(
    "/config-drifts",
    tags=[TAG_OVERRIDE],
    summary="Historique des drifts (toutes bornes)",
    description="Filtre `?since=<ISO>`. Retourne les clés avec `drift_count > 0`.",
)
async def list_config_drifts(
    since: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    stmt = select(ConfigSnapshot).where(ConfigSnapshot.drift_count > 0)
    if since:
        try:
            since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
            if since_dt.tzinfo is None:
                since_dt = since_dt.replace(tzinfo=timezone.utc)
            stmt = stmt.where(ConfigSnapshot.last_drift_at >= since_dt)
        except Exception:
            raise HTTPException(status_code=400, detail=f"since invalide: {since}")
    r = await db.execute(stmt.order_by(desc(ConfigSnapshot.last_drift_at)))
    rows = r.scalars().all()
    return {
        "count": len(rows),
        "drifts": [
            {
                "charger_id":     s.charger_id,
                "key":            s.key,
                "expected_value": s.expected_value,
                "observed_value": s.observed_value,
                "drift_count":    s.drift_count,
                "last_drift_at":  s.last_drift_at.isoformat() if s.last_drift_at else None,
                "last_heal_at":   s.last_heal_at.isoformat() if s.last_heal_at else None,
            }
            for s in rows
        ],
    }


@router.get(
    "/override-incidents",
    tags=[TAG_OVERRIDE],
    summary="Incidents drift Current.Import vs ChargingProfile",
    description=(
        "Sprint 30 Volet C — borne qui dépasse la limite courant active "
        "≥3 samples consécutifs. Filtres : `?since=<ISO>`, "
        "`?charger_id=X`, `?limit=N` (1..500, défaut 100)."
    ),
)
async def list_override_incidents(
    since: Optional[str] = None,
    charger_id: Optional[str] = None,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
):
    limit = max(1, min(500, limit))
    stmt = select(OverrideIncident)
    if charger_id:
        stmt = stmt.where(OverrideIncident.charger_id == charger_id)
    if since:
        try:
            since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
            if since_dt.tzinfo is None:
                since_dt = since_dt.replace(tzinfo=timezone.utc)
            stmt = stmt.where(OverrideIncident.detected_at >= since_dt)
        except Exception:
            raise HTTPException(status_code=400, detail=f"since invalide: {since}")
    r = await db.execute(stmt.order_by(desc(OverrideIncident.detected_at)).limit(limit))
    rows = r.scalars().all()
    return {
        "count": len(rows),
        "incidents": [
            {
                "id":                  x.id,
                "charger_id":          x.charger_id,
                "connector_id":        x.connector_id,
                "transaction_id":      x.transaction_id,
                "expected_amps":       x.expected_amps,
                "observed_amps":       x.observed_amps,
                "delta_amps":          x.delta_amps,
                "samples_consecutive": x.samples_consecutive,
                "reapplied":           x.reapplied,
                "reapply_status":      x.reapply_status,
                "detected_at":         x.detected_at.isoformat() if x.detected_at else None,
                "notes":               x.notes,
            }
            for x in rows
        ],
    }


# ════════════════════════════════════════════════════════════════════════════
#                            🛠️ Diagnostics serveur (extension)
# ════════════════════════════════════════════════════════════════════════════

@router.get(
    "/lock-status/{charger_id}",
    tags=[TAG_DIAGSRV],
    summary="État du verrou serveur (debug dashboard)",
    description="Retourne `_server_lock_active`, `boot_lock`, statut connecteur, transactions actives, profil borne.",
)
async def get_lock_status(charger_id: str, request: Request):
    cp = _get_cp(request, charger_id)
    return {
        "charger_id":          charger_id,
        "server_lock_active":  getattr(cp, '_server_lock_active', False),
        "boot_lock":           cp._boot_lock,
        "connector_status":    cp._connector1_status,
        "active_transactions": list(cp._active_transactions.keys()),
        "profile":             cp.profile.name if hasattr(cp, 'profile') else "unknown",
    }


# ════════════════════════════════════════════════════════════════════════════
#                     SPRINT 31 — Volet A : LocalAuthList sync
# ════════════════════════════════════════════════════════════════════════════

@router.post(
    "/sync-local-list/{charger_id}",
    tags=[TAG_LOCALLIST],
    summary="Sprint 31 — Sync LocalAuthList (auto via OcppTags)",
    description=(
        "Synchronise la whitelist locale de la borne avec la table `ocpp_tags`.\n\n"
        "Construit automatiquement la `local_authorization_list` à partir des "
        "tags actifs (non expirés), bumpe la `server_version`, appelle "
        "`SendLocalList` (Full par défaut) et persiste dans `local_list_versions`.\n\n"
        "Si la borne n'annonce pas le profil `LocalAuthListManagement` dans "
        "`SupportedFeatureProfiles`, retourne `status=NotSupported` sans appel OCPP."
    ),
)
async def sync_local_list(charger_id: str, request: Request, update_type: str = "Full"):
    cp = _get_cp(request, charger_id)
    if update_type not in ("Full", "Differential"):
        raise HTTPException(status_code=400,
                            detail="update_type doit être Full ou Differential")
    result = await cp._sync_local_auth_list(update_type=update_type)
    return {"charger_id": charger_id, **result}


@router.get(
    "/local-list-status/{charger_id}",
    tags=[TAG_LOCALLIST],
    summary="Sprint 31 — Statut de la whitelist locale par borne",
    description=(
        "Retourne la dernière synchronisation connue (version serveur, date, "
        "nombre de tags envoyés, statut OCPP) + la version actuelle côté borne "
        "via `GetLocalListVersion` si la borne est connectée."
    ),
)
async def local_list_status(charger_id: str, request: Request,
                            db: AsyncSession = Depends(get_db)):
    r = await db.execute(
        select(LocalListVersion).where(LocalListVersion.charger_id == charger_id)
    )
    state_row = r.scalar_one_or_none()
    out: dict = {
        "charger_id": charger_id,
        "server_version": state_row.server_version if state_row else 0,
        "last_sync_at": state_row.last_sync_at.isoformat() if state_row and state_row.last_sync_at else None,
        "last_sync_status": state_row.last_sync_status if state_row else None,
        "last_sync_type": state_row.last_sync_type if state_row else None,
        "tag_count_sent": state_row.tag_count_sent if state_row else None,
        "charger_version": None,
    }
    # Best-effort — lire la version actuelle de la borne si connectée
    try:
        cp = _get_cp(request, charger_id)
        charger_version = await cp.get_local_list_version()
        out["charger_version"] = charger_version
    except HTTPException:
        pass
    except Exception as e:
        log.warning("local_list_status get_local_list_version failed",
                    id=charger_id, error=str(e))
    return out


@router.get(
    "/local-list-status",
    tags=[TAG_LOCALLIST],
    summary="Sprint 31 — Statut whitelist pour toutes les bornes",
)
async def local_list_status_all(db: AsyncSession = Depends(get_db)):
    r = await db.execute(select(LocalListVersion).order_by(LocalListVersion.charger_id))
    rows = r.scalars().all()
    return [
        {
            "charger_id": s.charger_id,
            "server_version": s.server_version,
            "last_sync_at": s.last_sync_at.isoformat() if s.last_sync_at else None,
            "last_sync_status": s.last_sync_status,
            "last_sync_type": s.last_sync_type,
            "tag_count_sent": s.tag_count_sent,
        }
        for s in rows
    ]


# ════════════════════════════════════════════════════════════════════════════
#                   SPRINT 31 — Volet B : Capabilities (Feature Profiles)
# ════════════════════════════════════════════════════════════════════════════

@router.get(
    "/capabilities/{charger_id}",
    tags=[TAG_DIAGSRV],
    summary="Sprint 31 — SupportedFeatureProfiles détectés",
    description=(
        "Retourne la liste CSV des feature profiles OCPP annoncés par la borne "
        "au BootNotification (Core, FirmwareManagement, LocalAuthListManagement, "
        "Reservation, SmartCharging, RemoteTrigger).\n\n"
        "NULL = pas encore détecté (première connexion ou erreur GetConfiguration)."
    ),
)
async def get_capabilities(charger_id: str, db: AsyncSession = Depends(get_db)):
    r = await db.execute(select(Charger).where(Charger.id == charger_id))
    c = r.scalar_one_or_none()
    if c is None:
        raise HTTPException(status_code=404, detail="Borne introuvable")
    raw = c.supported_profiles
    profiles = [p.strip() for p in str(raw).split(",") if p.strip()] if raw else []
    return {
        "charger_id": charger_id,
        "supported_profiles": profiles,
        "raw": raw,
        "detected": raw is not None,
    }


# ── Sprint 32 Point 1 — Détection forcée capabilities ──────────────────────
@router.post(
    "/detect-capabilities/{charger_id}",
    tags=[TAG_DIAGSRV],
    summary="Sprint 32 — Forcer la détection SupportedFeatureProfiles",
    description=(
        "Déclenche un `GetConfiguration(SupportedFeatureProfiles)` immédiat "
        "et persiste le résultat dans `chargers.supported_profiles`.\n\n"
        "Utile quand la borne était déjà connectée au boot du serveur ou "
        "quand la première détection a échoué (timeout, erreur WS).\n\n"
        "Requiert que la borne soit connectée (503 sinon)."
    ),
)
async def detect_capabilities(charger_id: str, request: Request):
    cp = request.app.state.ocpp_server.get_charger(charger_id)
    if not cp:
        raise HTTPException(
            status_code=503,
            detail=f"Borne '{charger_id}' non connectée — détection impossible",
        )
    try:
        profiles = await cp._detect_supported_profiles()
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Erreur détection : {e!s}",
        )
    if profiles is None:
        raise HTTPException(
            status_code=502,
            detail="La borne n'a pas répondu à GetConfiguration(SupportedFeatureProfiles)",
        )
    return {
        "charger_id": charger_id,
        "detected_at": datetime.now(timezone.utc).isoformat(),
        "supported_profiles": profiles,
    }


@router.post(
    "/detect-capabilities",
    tags=[TAG_DIAGSRV],
    summary="Sprint 32 — Détecter toutes les bornes connectées (bulk)",
    description=(
        "Itère sur toutes les bornes actuellement connectées et déclenche "
        "un `GetConfiguration(SupportedFeatureProfiles)` pour chacune. "
        "Retourne un rapport par borne (succès, échec, profils détectés)."
    ),
)
async def detect_capabilities_bulk(request: Request):
    server = request.app.state.ocpp_server
    connected = list(getattr(server, "_charge_points", {}).items())
    report = []
    for cid, cp in connected:
        try:
            profiles = await cp._detect_supported_profiles()
            report.append({
                "charger_id": cid,
                "ok": profiles is not None,
                "supported_profiles": profiles,
            })
        except Exception as e:
            report.append({
                "charger_id": cid,
                "ok": False,
                "error": str(e),
            })
    return {
        "detected_at": datetime.now(timezone.utc).isoformat(),
        "count": len(report),
        "results": report,
    }


# ════════════════════════════════════════════════════════════════════════════
#                   SPRINT 31 — Volet C : StatusNotification history
# ════════════════════════════════════════════════════════════════════════════

@router.get(
    "/status-history/{charger_id}",
    tags=[TAG_DIAGSRV],
    summary="Sprint 31 — Historique StatusNotification (§4.8)",
    description=(
        "Retourne les N derniers StatusNotification reçus pour une borne, "
        "filtrable par connector_id et depuis un timestamp ISO."
    ),
)
async def status_history(
    charger_id: str,
    connector_id: Optional[int] = None,
    since: Optional[str] = None,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
):
    if limit < 1 or limit > 1000:
        raise HTTPException(status_code=400, detail="limit doit être entre 1 et 1000")
    stmt = select(StatusNotificationLog).where(
        StatusNotificationLog.charger_id == charger_id
    )
    if connector_id is not None:
        stmt = stmt.where(StatusNotificationLog.connector_id == connector_id)
    if since:
        try:
            dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
            stmt = stmt.where(StatusNotificationLog.received_at >= dt)
        except Exception:
            raise HTTPException(status_code=400, detail="since doit être ISO 8601")
    stmt = stmt.order_by(desc(StatusNotificationLog.received_at)).limit(limit)
    r = await db.execute(stmt)
    rows = r.scalars().all()
    return [
        {
            "id": x.id,
            "charger_id": x.charger_id,
            "connector_id": x.connector_id,
            "status": x.status,
            "error_code": x.error_code,
            "info": x.info,
            "vendor_id": x.vendor_id,
            "vendor_error_code": x.vendor_error_code,
            "timestamp": x.timestamp.isoformat() if x.timestamp else None,
            "received_at": x.received_at.isoformat() if x.received_at else None,
        }
        for x in rows
    ]
