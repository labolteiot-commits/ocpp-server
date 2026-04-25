# api/routes/admin_ui_certification.py
"""Sprint 33 — Page admin HTML : Certification OCPP 1.6J.

Page Jinja2 minimaliste qui consomme l'API `/api/certification/*` via
fetch() côté client, plus WebSocket `/ws/certification/{run_id}` pour le
streaming d'événements en direct.

Auth HTTP Basic appliquée globalement via `Depends(require_auth)` dans
`api/main.py` (cf. admin_ui.py).
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(
    directory=Path(__file__).parent.parent.parent / "dashboard" / "templates"
)


@router.get("/admin/certification", response_class=HTMLResponse)
async def admin_certification(request: Request):
    """Dashboard certification : sélection borne + suite, run live, rapport."""
    return templates.TemplateResponse(
        "admin_certification.html", {"request": request, "active": "certif"}
    )


@router.get("/admin/certification/runs/{run_id}/report", response_class=HTMLResponse)
async def admin_certification_report(request: Request, run_id: str):
    """Rendu standalone d'un rapport (proxy vers /api/certification/runs/{id}/report).

    Garde une URL propre pour l'impression/partage côté technicien. Le
    rendu réel se fait côté API — ici on se contente d'un wrapper qui
    pointe la page sur le bon iframe.
    """
    return templates.TemplateResponse(
        "certification_report.html",
        {"request": request, "run_id": run_id, "active": "certif"},
    )
