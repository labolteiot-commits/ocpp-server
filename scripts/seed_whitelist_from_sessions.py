#!/usr/bin/env python3
"""Sprint 32 Volet A — Seed whitelist ocpp_tags depuis RFID observés.

Extrait les id_tag distincts des sessions sur les bornes live et les insère
dans `ocpp_tags` avec active=True (INSERT OR IGNORE, idempotent).

Filtre les id_tag de test E2E (préfixe `E2E-`) et les id_tag de borne propre
(ex: `VIRTUAL-001`).

Usage:
    python seed_whitelist_from_sessions.py --dry-run
    python seed_whitelist_from_sessions.py --commit
    python seed_whitelist_from_sessions.py --commit --sync-after-commit
    python seed_whitelist_from_sessions.py --sync-only         # Sprint 32B

Idempotent : exécution répétée = 0 doublons.

Sprint 32B (2026-04-23) — Ajout --sync-after-commit / --sync-only qui
appelle POST /api/commands/sync-local-list/{charger_id} pour chaque borne
live supportant le profil LocalAuthListManagement. Respecte
OCPP_DASH_USER / OCPP_DASH_PASS (HTTP Basic, S29 Volet C) s'ils sont
définis dans l'environnement.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "ocpp.db"

# id_tag patterns à ignorer (tests E2E jetables)
IGNORE_PREFIXES = ("E2E-",)
# id_tag qui matchent un charger_id (id synthétique de la borne elle-même)
IGNORE_EQUAL_CHARGER = True

# --- Sprint 32B — Config sync ---
DEFAULT_SERVER_URL = os.environ.get("OCPP_SERVER_URL", "http://127.0.0.1:8000")
SYNC_PROFILE = "LocalAuthListManagement"


def audit(conn: sqlite3.Connection) -> list[dict]:
    """Retourne la liste des id_tag candidats avec stats d'usage."""
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id_tag,
               GROUP_CONCAT(DISTINCT charger_id) AS chargers,
               COUNT(*) AS uses,
               MIN(start_time) AS first_seen,
               MAX(start_time) AS last_seen
        FROM sessions
        WHERE id_tag IS NOT NULL AND id_tag != ''
        GROUP BY id_tag
        ORDER BY uses DESC
        """
    )
    rows = [dict(zip([d[0] for d in cur.description], r)) for r in cur.fetchall()]
    candidates = []
    for r in rows:
        tag = r["id_tag"]
        if any(tag.startswith(p) for p in IGNORE_PREFIXES):
            continue
        if IGNORE_EQUAL_CHARGER and tag in (r["chargers"] or "").split(","):
            continue
        candidates.append(r)
    return candidates


def existing_tags(conn: sqlite3.Connection) -> set[str]:
    cur = conn.cursor()
    cur.execute("SELECT id_tag FROM ocpp_tags")
    return {r[0] for r in cur.fetchall()}


def chargers_supporting_profile(conn: sqlite3.Connection, profile: str) -> list[str]:
    """Retourne les charger_id dont supported_profiles contient `profile`."""
    cur = conn.cursor()
    cur.execute(
        "SELECT id, supported_profiles FROM chargers "
        "WHERE supported_profiles IS NOT NULL AND supported_profiles != ''"
    )
    out = []
    for cid, sp in cur.fetchall():
        profiles = [p.strip() for p in (sp or "").split(",") if p.strip()]
        if profile in profiles:
            out.append(cid)
    return out


def seed(conn: sqlite3.Connection, candidates: list[dict], commit: bool) -> tuple[int, int]:
    """INSERT OR IGNORE chaque tag. Retourne (inserted, skipped)."""
    already = existing_tags(conn)
    inserted = 0
    skipped = 0
    now = datetime.now(timezone.utc).isoformat()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    for r in candidates:
        tag = r["id_tag"]
        if tag in already:
            skipped += 1
            continue
        note = (
            f"auto-seeded Sprint 32 on {today} -- chargers={r['chargers']} "
            f"uses={r['uses']} last={r['last_seen'][:10] if r['last_seen'] else '?'}"
        )[:200]
        if commit:
            conn.execute(
                """
                INSERT OR IGNORE INTO ocpp_tags
                    (id_tag, parent_id_tag, active, expiry_date,
                     max_active_tx, note, created_at, updated_at)
                VALUES (?, NULL, 1, NULL, 1, ?, ?, ?)
                """,
                (tag, note, now, now),
            )
        inserted += 1
    if commit:
        conn.commit()
    return inserted, skipped


def sync_local_list(server_url: str, charger_ids: list[str]) -> int:
    """Sprint 32B -- Appelle POST /sync-local-list/{id} pour chaque borne.

    Retourne le nombre de bornes synchronisees avec succes (status=Accepted).
    Silencieux/gracieux sur les bornes offline ou les profils non supportes.
    """
    try:
        import httpx
    except ImportError:
        print("ERREUR : httpx manquant dans l'env (pip install httpx).", file=sys.stderr)
        return 0

    user = os.environ.get("OCPP_DASH_USER", "")
    pw = os.environ.get("OCPP_DASH_PASS", "")
    auth = (user, pw) if (user and pw) else None

    print(f"\n=== Sync LocalAuthList -> {server_url} ===")
    if auth:
        print(f"Auth HTTP Basic : user={user}")
    else:
        print("Auth HTTP Basic : desactivee (mode ouvert)")
    print(f"Bornes candidates ({SYNC_PROFILE}) : {len(charger_ids)}")
    print("-" * 80)

    ok = 0
    with httpx.Client(timeout=15.0, auth=auth) as client:
        for cid in charger_ids:
            url = f"{server_url.rstrip('/')}/api/commands/sync-local-list/{cid}"
            try:
                r = client.post(url, json={"update_type": "Full"})
            except Exception as e:
                print(f"  {cid:<20} ERREUR reseau : {e}")
                continue
            if r.status_code == 401:
                print(f"  {cid:<20} 401 Unauthorized -- verifier OCPP_DASH_USER/PASS")
                continue
            if r.status_code >= 500:
                print(f"  {cid:<20} {r.status_code} server error")
                continue
            try:
                data = r.json()
            except Exception:
                data = {"raw": r.text[:120]}
            status = data.get("status", "?")
            version = data.get("list_version")
            count = data.get("tag_count")
            up_type = data.get("type", "?")
            if status == "Accepted":
                marker = "[OK]"
            elif status in ("NotSupported", "Offline"):
                marker = "[SKIP]"
            else:
                marker = "[WARN]"
            print(
                f"  {cid:<20} {marker} status={status} "
                f"version={version} tags={count} type={up_type}"
            )
            if status == "Accepted":
                ok += 1
    print("-" * 80)
    print(f"Sync reussi : {ok}/{len(charger_ids)} bornes")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="Affiche sans inserer")
    ap.add_argument("--commit", action="store_true", help="Insere reellement")
    ap.add_argument("--db", default=str(DB_PATH), help="Chemin SQLite")
    ap.add_argument(
        "--sync-after-commit",
        action="store_true",
        help="Sprint 32B : apres --commit, appelle /sync-local-list pour chaque borne supportant LocalAuthListManagement",
    )
    ap.add_argument(
        "--sync-only",
        action="store_true",
        help="Sprint 32B : ne reinsere rien, juste pousse la whitelist existante vers les bornes",
    )
    ap.add_argument(
        "--server-url",
        default=DEFAULT_SERVER_URL,
        help=f"URL serveur OCPP (defaut {DEFAULT_SERVER_URL})",
    )
    args = ap.parse_args()

    # Sprint 32B -- sync-only = shortcut sans seed
    if args.sync_only:
        if args.dry_run or args.commit:
            print("ERREUR : --sync-only est exclusif avec --dry-run et --commit.", file=sys.stderr)
            return 2
    else:
        if not (args.dry_run or args.commit):
            print("ERREUR : --dry-run ou --commit (ou --sync-only) requis.", file=sys.stderr)
            return 2
        if args.sync_after_commit and not args.commit:
            print("ERREUR : --sync-after-commit exige --commit.", file=sys.stderr)
            return 2

    db = Path(args.db)
    if not db.exists():
        print(f"ERREUR : DB introuvable : {db}", file=sys.stderr)
        return 2

    conn = sqlite3.connect(str(db))
    try:
        if args.sync_only:
            targets = chargers_supporting_profile(conn, SYNC_PROFILE)
            sync_local_list(args.server_url, targets)
            return 0

        candidates = audit(conn)
        already = existing_tags(conn)

        print(f"=== Sprint 32 Volet A -- Seed whitelist (db={db}) ===")
        print(f"Mode : {'DRY-RUN' if args.dry_run else 'COMMIT'}")
        print(f"Tags deja dans ocpp_tags : {len(already)}")
        print(f"Candidats (apres filtre E2E-* et match charger_id) : {len(candidates)}")
        print()
        print(f"{'id_tag':<15} {'chargers':<40} {'uses':>5} {'first_seen':<20} {'last_seen':<20}  etat")
        print("-" * 120)
        for r in candidates:
            state = "DEJA" if r["id_tag"] in already else "A INSERER"
            print(
                f"{r['id_tag']:<15} {(r['chargers'] or '')[:40]:<40} "
                f"{r['uses']:>5} {(r['first_seen'] or '')[:19]:<20} "
                f"{(r['last_seen'] or '')[:19]:<20}  {state}"
            )
        print()

        inserted, skipped = seed(conn, candidates, commit=args.commit)
        verb = "INSERES" if args.commit else "SERAIENT INSERES"
        print(f"Resultat : {inserted} tags {verb}, {skipped} deja presents.")
        if args.dry_run:
            print("(Aucune modification DB. Relance avec --commit pour appliquer.)")

        # Sprint 32B -- sync apres commit
        if args.commit and args.sync_after_commit:
            targets = chargers_supporting_profile(conn, SYNC_PROFILE)
            sync_local_list(args.server_url, targets)

        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
