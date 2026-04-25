"""Sprint 32 Volet B — Routes jetables pour test E2E fail-safe (UpdateFirmware).

Ce module expose une route HTTP volontairement défaillante pour permettre de
tester le chemin "DownloadFailed" du flow UpdateFirmware côté borne sans avoir
à publier un vrai firmware invalide.

PAS d'auth Basic — la borne ne fournit pas de credentials lors du download
firmware (c'est l'OS de la borne qui télécharge via HTTP simple).

Reconstruction depuis __pycache__ (source supprimée par erreur au sprint 32).
"""
from __future__ import annotations

from fastapi import APIRouter, Response

router = APIRouter(prefix="/api/test", tags=["Tests jetables"])


@router.get(
    "/fake-firmware.bin",
    summary="Sprint 32 — Cible OTA volontairement invalide (404)",
    description=(
        "Retourne HTTP 404 pour forcer un `DownloadFailed` côté borne lors "
        "d'un test E2E UpdateFirmware. Utilisée par le scénario fail-safe : "
        "vérifie que le serveur persiste correctement le statut `DownloadFailed` "
        "dans `firmware_updates` après réception du `FirmwareStatusNotification`."
    ),
)
async def fake_firmware_404() -> Response:
    return Response(status_code=404)
