# api/security.py
"""
Sprint 29 Volet C — HTTP Basic Auth pour les routes administratives.

Comportement :
- Si OCPP_DASH_USER et OCPP_DASH_PASS sont définis (via .env ou environnement) :
  require_auth vérifie les credentials et retourne 401 si invalides.
- Si l'un des deux est vide ou absent : mode ouvert (retrocompat). Un warning
  est loggé UNE SEULE FOIS au premier appel pour prévenir l'opérateur.

Ne pas appliquer sur les routes WebSocket OCPP (port 9000) — les bornes ne
passent pas par HTTP Basic, elles utilisent leur propre Basic auth validée
dans core/ocpp_server.py::_authenticate.

Exemple d'activation :
    echo 'export OCPP_DASH_USER=admin' >> /home/lteiot/ocpp-server/.env
    echo 'export OCPP_DASH_PASS=<votre-mot-de-passe>' >> /home/lteiot/ocpp-server/.env
"""
import os
import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from core.logging import log


security = HTTPBasic(auto_error=False)

_USER = os.getenv("OCPP_DASH_USER", "")
_PASS = os.getenv("OCPP_DASH_PASS", "")
_OPEN_WARNED = False


def _warn_once_if_open() -> None:
    global _OPEN_WARNED
    if not _OPEN_WARNED:
        _OPEN_WARNED = True
        log.warning(
            "OCPP_DASH_USER/PASS non défini — dashboard ouvert",
            hint="Définir dans .env pour activer HTTP Basic auth",
        )


def require_auth(
    credentials: HTTPBasicCredentials | None = Depends(security),
) -> str | None:
    """Dépendance FastAPI : vérifie le Basic Auth si configuré.

    - Pas de creds configurés → mode ouvert, retourne None (+ warn 1 fois).
    - Creds configurés + manquants côté client → 401.
    - Creds configurés + mauvais → 401.
    - Creds configurés + bons → retourne le username.
    """
    if not _USER or not _PASS:
        _warn_once_if_open()
        return None

    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentification requise",
            headers={"WWW-Authenticate": "Basic"},
        )

    ok_u = secrets.compare_digest(credentials.username, _USER)
    ok_p = secrets.compare_digest(credentials.password, _PASS)
    if not (ok_u and ok_p):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Identifiants invalides",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username
