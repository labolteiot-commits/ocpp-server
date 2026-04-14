# api/routes/commands.py
import asyncio
from fastapi import APIRouter, HTTPException, Request, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from pydantic import BaseModel
from typing import Optional, List

from db.database import get_db
from db.models import Session as ChargingSession, SessionStatus

router = APIRouter()


# ── Schémas ───────────────────────────────────────────────

class RemoteStartRequest(BaseModel):
    charger_id:  str
    connector_id: int = 1
    id_tag:      str = "DASHBOARD"
    max_amps:    Optional[float] = None

class RemoteStopRequest(BaseModel):
    charger_id:     str
    transaction_id: int
    lock:           bool = True

class ResetRequest(BaseModel):
    charger_id:  str
    reset_type:  str = "Soft"

class AvailabilityRequest(BaseModel):
    charger_id:   str
    connector_id: int = 1

class UnlockRequest(BaseModel):
    charger_id:   str
    connector_id: int = 1

class ConfigGetRequest(BaseModel):
    charger_id: str
    keys:       List[str] = []

class ConfigSetRequest(BaseModel):
    charger_id: str
    key:        str
    value:      str

class TriggerRequest(BaseModel):
    charger_id:   str
    message:      str
    connector_id: Optional[int] = None

class ChargingProfileRequest(BaseModel):
    charger_id:       str
    connector_id:     int   = 1
    max_amps:         float = 16.0
    duration_seconds: Optional[int] = None

class PowerLimitRequest(BaseModel):
    charger_id:   str
    connector_id: int   = 0      # 0 = borne entière
    max_amps:     float = 0.0    # 0 = coupe la charge

class ClearProfileRequest(BaseModel):
    charger_id:  str
    profile_id:  Optional[int] = None
    connector_id: Optional[int] = None

class ReserveRequest(BaseModel):
    charger_id:      str
    connector_id:    int = 1
    id_tag:          str = "RESERVED"
    expiry_minutes:  int = 30

class CancelReservationRequest(BaseModel):
    charger_id:     str
    reservation_id: int

class LocalListRequest(BaseModel):
    charger_id:  str
    id_tags:     List[str] = []
    update_type: str = "Full"

class DataTransferRequest(BaseModel):
    charger_id:  str
    vendor_id:   str
    message_id:  str = ""
    data:        str = ""

class DiagnosticsRequest(BaseModel):
    charger_id: str
    location:   str
    retries:    int = 1

class FirmwareRequest(BaseModel):
    charger_id:    str
    location:      str
    retrieve_date: str
    retries:       int = 1


# ── Utilitaire ────────────────────────────────────────────

def _get_cp(request: Request, charger_id: str):
    cp = request.app.state.ocpp_server.get_charger(charger_id)
    if not cp:
        connected = request.app.state.ocpp_server.connected_chargers
        raise HTTPException(
            status_code=404,
            detail=f"Borne '{charger_id}' non connectée. Connectées: {connected}"
        )
    return cp


# ── Connexion ─────────────────────────────────────────────

@router.get("/connected")
async def connected_chargers(request: Request):
    """Liste les bornes présentement connectées."""
    return {"connected": request.app.state.ocpp_server.connected_chargers}


# ── Session ───────────────────────────────────────────────

@router.post("/remote-start")
async def remote_start(body: RemoteStartRequest, request: Request):
    """Démarre une session de charge à distance."""
    cp = _get_cp(request, body.charger_id)
    success = await cp.remote_start_transaction(
        body.connector_id, body.id_tag, max_amps=body.max_amps
    )
    if not success:
        raise HTTPException(status_code=400, detail="La borne a refusé le démarrage")
    return {"detail": "Démarrage accepté"}


@router.post("/remote-stop")
async def remote_stop(
    body: RemoteStopRequest,
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """Arrête une session de charge à distance."""
    cp = _get_cp(request, body.charger_id)

    # Transaction synthétique (ID négatif) = charge locale sans transaction OCPP.
    # RemoteStopTransaction ne fonctionnera pas → ChangeAvailability Inoperative
    if body.transaction_id < 0:
        success = await cp.set_unavailable(1)
        if not success:
            raise HTTPException(
                status_code=400,
                detail="Arrêt de charge locale refusé (ChangeAvailability Inoperative)"
            )
        return {"detail": "Charge locale arrêtée (borne mise Inoperative)"}

    # Transaction OCPP normale — vérifier qu'elle est active en DB
    result = await db.execute(
        select(ChargingSession).where(
            ChargingSession.transaction_id == body.transaction_id,
            ChargingSession.status         == SessionStatus.ACTIVE,
        )
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(
            status_code=400,
            detail=f"Aucune session active avec transaction_id={body.transaction_id}"
        )

    success = await cp.remote_stop_transaction(body.transaction_id, lock=body.lock)
    if not success:
        raise HTTPException(status_code=400, detail="La borne a refusé l'arrêt")
    return {"detail": "Arrêt accepté"}


@router.get("/active-transaction/{charger_id}")
async def get_active_transaction(
    charger_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Retourne la transaction active d'une borne."""
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
    }


# ── Disponibilité ─────────────────────────────────────────

@router.post("/set-available")
async def set_available(body: AvailabilityRequest, request: Request):
    """Remet un connecteur en mode Operative (disponible)."""
    cp = _get_cp(request, body.charger_id)
    success = await cp.set_available(body.connector_id)
    if not success:
        raise HTTPException(status_code=400, detail="La borne a refusé")
    return {"detail": f"Connecteur {body.connector_id} remis en disponible"}


@router.post("/set-unavailable")
async def set_unavailable(body: AvailabilityRequest, request: Request):
    """
    Met un connecteur en mode Inoperative (indisponible).
    Note : si une session est en cours, la borne répondra 'Scheduled'
    et passera Inoperative seulement après la fin de la session.
    Pour couper immédiatement, utiliser /set-power-limit avec max_amps=0.
    """
    cp = _get_cp(request, body.charger_id)
    success = await cp.set_unavailable(body.connector_id)
    if not success:
        raise HTTPException(status_code=400, detail="La borne a refusé")
    return {"detail": f"Connecteur {body.connector_id} mis hors service"}


@router.post("/unlock")
async def unlock_connector(body: UnlockRequest, request: Request):
    """Déverrouille un connecteur."""
    cp = _get_cp(request, body.charger_id)
    status = await cp.unlock_connector(body.connector_id)
    if status == "Failed":
        raise HTTPException(status_code=400, detail="Déverrouillage échoué")
    return {"detail": f"Connecteur {body.connector_id} déverrouillé", "status": status}


# ── Système ───────────────────────────────────────────────

@router.post("/reset")
async def reset_charger(body: ResetRequest, request: Request):
    """Redémarre la borne (Soft = propre, Hard = forcé)."""
    cp = _get_cp(request, body.charger_id)
    success = await cp.reset(body.reset_type)
    if not success:
        raise HTTPException(status_code=400, detail="La borne a refusé le reset")
    return {"detail": f"Reset {body.reset_type} accepté"}


@router.post("/clear-cache")
async def clear_cache(body: dict, request: Request):
    """Vide le cache d'autorisation de la borne."""
    charger_id = body.get("charger_id")
    if not charger_id:
        raise HTTPException(status_code=400, detail="charger_id requis")
    cp = _get_cp(request, charger_id)
    success = await cp.clear_cache()
    if not success:
        raise HTTPException(status_code=400, detail="ClearCache refusé")
    return {"detail": "Cache vidé"}


# ── Configuration ─────────────────────────────────────────

@router.post("/config/get")
async def get_configuration(body: ConfigGetRequest, request: Request):
    """Récupère la configuration de la borne."""
    cp = _get_cp(request, body.charger_id)
    config = await cp.get_configuration(body.keys)
    return {"charger_id": body.charger_id, "configuration": config}


@router.post("/config/set")
async def set_configuration(body: ConfigSetRequest, request: Request):
    """Modifie un paramètre de configuration."""
    cp = _get_cp(request, body.charger_id)
    status = await cp.change_configuration(body.key, body.value)
    if status == "Failed":
        raise HTTPException(status_code=400, detail="ChangeConfiguration échoué")
    if status == "NotSupported":
        raise HTTPException(status_code=400, detail=f"Clé '{body.key}' non supportée")
    if status == "Rejected":
        raise HTTPException(status_code=400, detail=f"Clé '{body.key}' rejetée (read-only?)")
    return {"detail": f"{body.key} = {body.value}", "status": status}


@router.get("/config/cached/{charger_id}")
async def get_cached_config(charger_id: str, request: Request):
    """Retourne la configuration mise en cache (sans appeler la borne)."""
    cp = _get_cp(request, charger_id)
    return {"charger_id": charger_id, "configuration": cp._config_cache}


# ── Messages déclenchés ───────────────────────────────────

@router.post("/trigger")
async def trigger_message(body: TriggerRequest, request: Request):
    """Force la borne à envoyer un message spécifique."""
    cp = _get_cp(request, body.charger_id)
    status = await cp.trigger_message(body.message, body.connector_id)
    if status == "Failed":
        raise HTTPException(status_code=400, detail="TriggerMessage échoué")
    return {"detail": f"TriggerMessage {body.message} envoyé", "status": status}


# ── Smart Charging ────────────────────────────────────────

@router.post("/set-power-limit")
async def set_power_limit(body: PowerLimitRequest, request: Request):
    """
    Impose une limite de courant immédiate sur la borne.
    max_amps=0  → coupe la charge immédiatement (même si session active)
    max_amps=24 → limite à 24A
    Utilise ChargePointMaxProfile (connector_id=0) pour affecter toute la borne.
    """
    cp = _get_cp(request, body.charger_id)

    profile = {
        "charging_profile_id":      99,
        "stack_level":              9,   # > boot (8) et cloud (≤8) → priorité absolue
        "charging_profile_purpose": "ChargePointMaxProfile",
        "charging_profile_kind":    "Absolute",
        "charging_schedule": {
            "charging_rate_unit": "A",
            "charging_schedule_period": [
                {"start_period": 0, "limit": body.max_amps}
            ],
        },
    }

    status = await cp.set_charging_profile(0, profile)
    if status == "Failed":
        raise HTTPException(status_code=400, detail="SetChargingProfile échoué")

    if body.max_amps == 0:
        msg = "Charge coupée (limite 0A)"
    else:
        # Valider que le courant demandé ne dépasse pas le maximum configuré pour cette borne
        configured_max = cp._default_max_amps
        if configured_max and body.max_amps > configured_max:
            raise HTTPException(
                status_code=422,
                detail=f"Courant demandé ({body.max_amps:.0f}A) supérieur au maximum configuré "
                       f"pour cette borne ({configured_max:.0f}A). "
                       f"Modifiez 'Courant max' dans les paramètres de la borne pour changer cette limite."
            )
        msg = f"Limite imposée à {body.max_amps}A"
        # Rétablissement : si la borne est suspendue, un ChangeAvailability(Operative)
        # relance le cycle de charge sans avoir besoin d'un RemoteStart.
        if cp._connector1_status in ("SuspendedEVSE", "SuspendedEV", "Preparing", "Finishing"):
            asyncio.create_task(cp.set_available(1))

    return {"detail": msg, "status": status}


@router.post("/charging-profile/set")
async def set_charging_profile(body: ChargingProfileRequest, request: Request):
    """Définit un profil de charge (limite de puissance) pour une session."""
    cp = _get_cp(request, body.charger_id)

    profile = {
        "charging_profile_id":      1,
        "stack_level":              0,
        "charging_profile_purpose": "TxDefaultProfile",
        "charging_profile_kind":    "Absolute",
        "charging_schedule": {
            "charging_rate_unit": "A",
            "charging_schedule_period": [
                {"start_period": 0, "limit": body.max_amps}
            ],
        },
    }
    if body.duration_seconds:
        profile["charging_schedule"]["duration"] = body.duration_seconds

    status = await cp.set_charging_profile(body.connector_id, profile)
    if status == "Failed":
        raise HTTPException(status_code=400, detail="SetChargingProfile échoué")
    return {"detail": f"Profil de charge {body.max_amps}A appliqué", "status": status}


@router.post("/charging-profile/clear")
async def clear_charging_profile(body: ClearProfileRequest, request: Request):
    """Efface le profil de charge."""
    cp = _get_cp(request, body.charger_id)
    status = await cp.clear_charging_profile(
        profile_id=body.profile_id,
        connector_id=body.connector_id,
    )
    return {"detail": "Profil effacé", "status": status}


@router.get("/composite-schedule/{charger_id}/{connector_id}")
async def get_composite_schedule(
    charger_id: str, connector_id: int,
    duration: int = 86400, request: Request = None
):
    """Récupère le planning de charge composite."""
    cp = _get_cp(request, charger_id)
    result = await cp.get_composite_schedule(connector_id, duration)
    if result is None:
        raise HTTPException(status_code=400, detail="GetCompositeSchedule échoué")
    return result


# ── Réservation ───────────────────────────────────────────

@router.post("/reserve")
async def reserve_now(body: ReserveRequest, request: Request):
    """Réserve un connecteur pour un ID tag."""
    import random
    from datetime import datetime, timedelta, timezone
    cp = _get_cp(request, body.charger_id)
    expiry = (
        datetime.now(timezone.utc) + timedelta(minutes=body.expiry_minutes)
    ).isoformat()
    reservation_id = random.randint(1000, 9999)
    status = await cp.reserve_now(
        body.connector_id, expiry, body.id_tag, reservation_id
    )
    if status not in ["Accepted"]:
        raise HTTPException(status_code=400, detail=f"Réservation refusée: {status}")
    return {
        "detail":         f"Connecteur {body.connector_id} réservé",
        "reservation_id": reservation_id,
        "expiry":         expiry,
        "status":         status,
    }


@router.post("/cancel-reservation")
async def cancel_reservation(body: CancelReservationRequest, request: Request):
    """Annule une réservation."""
    cp = _get_cp(request, body.charger_id)
    status = await cp.cancel_reservation(body.reservation_id)
    if status != "Accepted":
        raise HTTPException(status_code=400, detail=f"Annulation refusée: {status}")
    return {"detail": f"Réservation {body.reservation_id} annulée"}


# ── Liste locale ──────────────────────────────────────────

@router.post("/local-list/send")
async def send_local_list(body: LocalListRequest, request: Request):
    """Envoie la liste locale d'autorisation à la borne."""
    cp = _get_cp(request, body.charger_id)
    auth_list = [
        {"id_tag": tag, "id_tag_info": {"status": "Accepted"}}
        for tag in body.id_tags
    ]
    status = await cp.send_local_list(
        list_version=1,
        update_type=body.update_type,
        local_authorization_list=auth_list,
    )
    if status not in ["Accepted"]:
        raise HTTPException(status_code=400, detail=f"SendLocalList refusé: {status}")
    return {"detail": f"{len(body.id_tags)} badges envoyés", "status": status}


@router.get("/local-list/version/{charger_id}")
async def get_local_list_version(charger_id: str, request: Request):
    """Récupère la version de la liste locale."""
    cp = _get_cp(request, charger_id)
    version = await cp.get_local_list_version()
    return {"charger_id": charger_id, "list_version": version}


# ── Diagnostics & Firmware ────────────────────────────────

@router.post("/diagnostics")
async def get_diagnostics(body: DiagnosticsRequest, request: Request):
    """Demande à la borne d'uploader ses logs de diagnostic."""
    cp = _get_cp(request, body.charger_id)
    filename = await cp.get_diagnostics(body.location, retries=body.retries)
    return {"detail": "Demande de diagnostics envoyée", "filename": filename}


@router.post("/firmware/update")
async def update_firmware(body: FirmwareRequest, request: Request):
    """Demande à la borne de mettre à jour son firmware."""
    cp = _get_cp(request, body.charger_id)
    success = await cp.update_firmware(
        body.location, body.retrieve_date, retries=body.retries
    )
    if not success:
        raise HTTPException(status_code=400, detail="UpdateFirmware échoué")
    return {"detail": "Mise à jour firmware initiée"}


# ── Data Transfer ─────────────────────────────────────────

@router.post("/data-transfer")
async def data_transfer(body: DataTransferRequest, request: Request):
    """Échange de données propriétaires avec la borne."""
    cp = _get_cp(request, body.charger_id)
    response_data = await cp.data_transfer(
        body.vendor_id, body.message_id, body.data
    )
    return {"detail": "DataTransfer envoyé", "response_data": response_data}
