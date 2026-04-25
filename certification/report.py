"""Génération des rapports HTML et JSON de certification.

Le HTML est un document standalone (tout inline, pas de dépendances
externes) — print-friendly, utilisable en PDF via le navigateur.
"""
from __future__ import annotations

import html
import json
from typing import Any, Dict


_STATUS_BADGE = {
    "passed": ("#065f46", "#d1fae5", "RÉUSSI"),
    "failed": ("#991b1b", "#fee2e2", "ÉCHEC"),
    "skipped": ("#92400e", "#fef3c7", "SAUTÉ"),
}


def _esc(s: Any) -> str:
    if s is None:
        return ""
    return html.escape(str(s))


def _status_chip(status: str) -> str:
    fg, bg, label = _STATUS_BADGE.get(status, ("#1f2937", "#e5e7eb", status.upper()))
    return (
        f'<span style="display:inline-block;padding:2px 8px;border-radius:999px;'
        f'background:{bg};color:{fg};font-weight:600;font-size:12px;">{label}</span>'
    )


def _run_status_banner(status: str) -> str:
    color_map = {
        "completed": ("#065f46", "#d1fae5", "CERTIFICATION TERMINÉE"),
        "cancelled": ("#92400e", "#fef3c7", "ANNULÉE PAR OPÉRATEUR"),
        "error": ("#991b1b", "#fee2e2", "ERREUR TECHNIQUE"),
        "running": ("#1e40af", "#dbeafe", "EN COURS"),
    }
    fg, bg, label = color_map.get(status, ("#1f2937", "#e5e7eb", status.upper()))
    return (
        f'<div style="padding:12px 16px;background:{bg};color:{fg};'
        f'border-radius:8px;font-weight:700;font-size:15px;">{label}</div>'
    )


def render_json(ctx: Dict[str, Any]) -> str:
    return json.dumps(ctx, indent=2, default=str, ensure_ascii=False)


def render_html(ctx: Dict[str, Any]) -> str:
    """Rend un rapport HTML standalone.

    ``ctx`` contient les clés : run_id, charger_id, suite, suite_label,
    technician_name, notes, status, started_at, finished_at, duration_s,
    supported_profiles, firmware_before, passed, failed, skipped, total,
    results (liste de dicts).
    """
    total = ctx.get("total", 0) or 0
    passed = ctx.get("passed", 0) or 0
    failed = ctx.get("failed", 0) or 0
    skipped = ctx.get("skipped", 0) or 0
    pct_pass = int(round((passed / total) * 100)) if total else 0

    # Section résultats par catégorie
    results = ctx.get("results", []) or []
    by_cat: Dict[str, list] = {}
    for r in results:
        by_cat.setdefault(r.get("category", "Divers"), []).append(r)

    rows_html_parts = []
    for cat, items in by_cat.items():
        rows_html_parts.append(
            f'<tr class="cat-row"><td colspan="5" style="background:#f1f5f9;'
            f'font-weight:700;padding:8px 10px;color:#334155;">{_esc(cat)}</td></tr>'
        )
        for r in items:
            details_html = ""
            if r.get("details"):
                try:
                    dj = json.dumps(r["details"], indent=2, default=str, ensure_ascii=False)
                    details_html = (
                        f'<details style="margin-top:6px;"><summary style="cursor:pointer;'
                        f'color:#0369a1;font-size:12px;">Voir détails techniques</summary>'
                        f'<pre style="background:#0f172a;color:#e2e8f0;padding:8px;'
                        f'border-radius:4px;font-size:11px;overflow-x:auto;">{_esc(dj)}</pre>'
                        f'</details>'
                    )
                except Exception:
                    details_html = ""

            recom_html = ""
            if r.get("recommendation"):
                recom_html = (
                    f'<div style="margin-top:6px;padding:6px 10px;background:#fffbeb;'
                    f'border-left:3px solid #f59e0b;font-size:12px;color:#78350f;">'
                    f'<strong>Recommandation :</strong> {_esc(r["recommendation"])}</div>'
                )

            ocpp_ref = r.get("ocpp_ref", "")
            ocpp_html = (
                f'<div style="font-size:11px;color:#64748b;margin-top:2px;">Réf. OCPP 1.6J : {_esc(ocpp_ref)}</div>'
                if ocpp_ref else ""
            )

            rows_html_parts.append(f"""
                <tr>
                  <td style="padding:8px 10px;vertical-align:top;font-weight:600;color:#1e293b;">
                    {_esc(r.get('index', ''))}
                  </td>
                  <td style="padding:8px 10px;vertical-align:top;">
                    <div style="font-weight:600;color:#0f172a;">{_esc(r.get('title', r.get('name')))}</div>
                    <div style="font-size:12px;color:#475569;margin-top:2px;">{_esc(r.get('description', ''))}</div>
                    {ocpp_html}
                  </td>
                  <td style="padding:8px 10px;vertical-align:top;">{_status_chip(r.get('status', ''))}</td>
                  <td style="padding:8px 10px;vertical-align:top;font-variant-numeric:tabular-nums;">
                    {_esc(r.get('duration_s', 0))}s
                  </td>
                  <td style="padding:8px 10px;vertical-align:top;">
                    <div>{_esc(r.get('message', ''))}</div>
                    {details_html}
                    {recom_html}
                  </td>
                </tr>
            """)

    rows_html = "".join(rows_html_parts) if rows_html_parts else (
        '<tr><td colspan="5" style="padding:16px;text-align:center;color:#64748b;">'
        'Aucun résultat de test</td></tr>'
    )

    profiles_html = ""
    profs = ctx.get("supported_profiles") or []
    if profs:
        profiles_html = " ".join(
            f'<span style="display:inline-block;padding:2px 8px;border-radius:999px;'
            f'background:#e0f2fe;color:#075985;font-size:12px;margin-right:4px;">{_esc(p)}</span>'
            for p in profs
        )
    else:
        profiles_html = '<span style="color:#64748b;font-size:12px;">Non détectés</span>'

    notes_section = ""
    if ctx.get("notes"):
        notes_section = f"""
          <tr>
            <td style="padding:6px 10px;color:#475569;font-weight:600;">Notes opérateur</td>
            <td style="padding:6px 10px;">{_esc(ctx.get('notes'))}</td>
          </tr>
        """

    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<title>Rapport certification OCPP — {_esc(ctx.get('charger_id', ''))}</title>
<style>
  @media print {{
    body {{ margin: 0; }}
    .no-print {{ display: none !important; }}
  }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    color: #0f172a;
    background: #f8fafc;
    margin: 0;
    padding: 20px;
  }}
  .wrap {{
    max-width: 1100px;
    margin: 0 auto;
    background: white;
    padding: 24px 32px;
    border-radius: 12px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.08);
  }}
  h1 {{ margin: 0 0 6px 0; color: #0f172a; font-size: 22px; }}
  h2 {{ margin: 24px 0 12px 0; color: #1e293b; font-size: 16px; border-bottom: 2px solid #e2e8f0; padding-bottom: 6px; }}
  .kpis {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin: 16px 0; }}
  .kpi {{ padding: 12px; background: #f1f5f9; border-radius: 8px; text-align: center; }}
  .kpi .v {{ font-size: 24px; font-weight: 700; color: #0f172a; }}
  .kpi .l {{ font-size: 11px; color: #475569; text-transform: uppercase; letter-spacing: 0.5px; margin-top: 2px; }}
  table {{ width: 100%; border-collapse: collapse; }}
  table.meta td {{ padding: 6px 10px; border-bottom: 1px solid #f1f5f9; font-size: 13px; }}
  table.results {{ border: 1px solid #e2e8f0; border-radius: 8px; overflow: hidden; }}
  table.results th {{ background: #0f172a; color: #e2e8f0; padding: 8px 10px; text-align: left; font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; }}
  table.results tr:not(.cat-row):not(:last-child) {{ border-bottom: 1px solid #f1f5f9; }}
  .bar-wrap {{ background: #e2e8f0; border-radius: 999px; height: 12px; overflow: hidden; margin-top: 6px; }}
  .bar {{ height: 100%; background: linear-gradient(90deg, #10b981, #22c55e); transition: width 0.3s; }}
</style>
</head>
<body>
<div class="wrap">
  <div style="display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap;">
    <div>
      <h1>Rapport de certification OCPP 1.6J</h1>
      <div style="color:#64748b;font-size:13px;">Borne <strong>{_esc(ctx.get('charger_id',''))}</strong> · Suite <strong>{_esc(ctx.get('suite_label', ctx.get('suite','')))}</strong></div>
    </div>
    {_run_status_banner(ctx.get('status', 'unknown'))}
  </div>

  <div class="kpis">
    <div class="kpi">
      <div class="v" style="color:#065f46;">{passed}</div>
      <div class="l">Tests réussis</div>
    </div>
    <div class="kpi">
      <div class="v" style="color:#991b1b;">{failed}</div>
      <div class="l">Échecs</div>
    </div>
    <div class="kpi">
      <div class="v" style="color:#92400e;">{skipped}</div>
      <div class="l">Sautés</div>
    </div>
    <div class="kpi">
      <div class="v">{pct_pass}%</div>
      <div class="l">Taux de réussite</div>
      <div class="bar-wrap"><div class="bar" style="width:{pct_pass}%;"></div></div>
    </div>
  </div>

  <h2>Informations du run</h2>
  <table class="meta">
    <tr><td style="width:200px;color:#475569;font-weight:600;">Run ID</td><td><code style="font-size:11px;">{_esc(ctx.get('run_id',''))}</code></td></tr>
    <tr><td style="color:#475569;font-weight:600;">Technicien</td><td>{_esc(ctx.get('technician_name') or '—')}</td></tr>
    <tr><td style="color:#475569;font-weight:600;">Démarré</td><td>{_esc(ctx.get('started_at','')).replace('T',' ').split('+')[0]}</td></tr>
    <tr><td style="color:#475569;font-weight:600;">Terminé</td><td>{_esc(ctx.get('finished_at','')).replace('T',' ').split('+')[0]}</td></tr>
    <tr><td style="color:#475569;font-weight:600;">Durée</td><td>{round(ctx.get('duration_s', 0) or 0, 1)} s</td></tr>
    <tr><td style="color:#475569;font-weight:600;">Firmware borne</td><td>{_esc(ctx.get('firmware_before') or '—')}</td></tr>
    <tr><td style="color:#475569;font-weight:600;">Profils OCPP supportés</td><td>{profiles_html}</td></tr>
    {notes_section}
  </table>

  <h2>Résultats détaillés ({total} tests)</h2>
  <table class="results">
    <thead>
      <tr>
        <th style="width:40px;">#</th>
        <th>Test</th>
        <th style="width:90px;">Résultat</th>
        <th style="width:80px;">Durée</th>
        <th>Observations</th>
      </tr>
    </thead>
    <tbody>
      {rows_html}
    </tbody>
  </table>

  <div style="margin-top:24px;padding:12px;background:#f8fafc;border-radius:8px;font-size:11px;color:#64748b;text-align:center;">
    Généré par le serveur OCPP {_esc(ctx.get('charger_id',''))} — Certification 1.6J · {_esc(ctx.get('finished_at',''))}
  </div>
</div>
</body>
</html>"""
