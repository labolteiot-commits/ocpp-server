# core/seed_message_docs.py
"""
Sprint 32 — UPSERT du seed `OCPP_MESSAGE_DOCS` dans la table `ocpp_message_docs`.

Idempotent : appelé au boot (db.init_db). Si une entrée existe déjà avec le
même `name`, elle est mise à jour (le seed est source de vérité). Pour permettre
un override manuel via UI plus tard, on pourrait ajouter un flag `is_user_override`
qui empêche le seed de l'écraser — pas encore fait.
"""
from __future__ import annotations

import json
from datetime import datetime

from core.logging import log
from core.ocpp_message_docs_seed import OCPP_MESSAGE_DOCS


async def seed_message_docs() -> int:
    """Insère ou met à jour les docs des messages OCPP. Retourne le nombre de
    lignes touchées."""
    from db.database import AsyncSessionLocal
    from db.models import OcppMessageDoc
    from sqlalchemy import select

    touched = 0
    async with AsyncSessionLocal() as db:
        for entry in OCPP_MESSAGE_DOCS:
            name = entry["name"]
            r = await db.execute(select(OcppMessageDoc).where(OcppMessageDoc.name == name))
            existing = r.scalar_one_or_none()
            fields_json = json.dumps(entry.get("fields_fr") or {}, ensure_ascii=False)
            payload = {
                "direction_norm": entry["direction_norm"],
                "profile":        entry["profile"],
                "section_norm":   entry.get("section_norm"),
                "summary_fr":     entry["summary_fr"],
                "description_fr": entry["description_fr"],
                "fields_fr":      fields_json,
                "triggered_by":   entry.get("triggered_by"),
                "cs_response":    entry.get("cs_response"),
                "updated_at":     datetime.utcnow(),
            }
            if existing is None:
                db.add(OcppMessageDoc(name=name, **payload))
                touched += 1
            else:
                # Update si différent
                changed = False
                for k, v in payload.items():
                    if getattr(existing, k) != v:
                        setattr(existing, k, v)
                        changed = True
                if changed:
                    touched += 1
        await db.commit()

    log.info("[S32-msgdocs] seed appliqué", touched=touched, total=len(OCPP_MESSAGE_DOCS))
    return touched
