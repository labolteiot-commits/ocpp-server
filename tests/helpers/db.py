"""
Accès lecture SQLite du serveur OCPP (aiosqlite).

Toutes les requêtes sont read-only par design. Ne JAMAIS écrire dans la DB
depuis ici — les tests doivent déclencher les changements via les APIs REST
ou via la borne virtuelle, puis observer.
"""
from __future__ import annotations

from typing import Any, Optional

import aiosqlite


class OCPPDb:
    def __init__(self, db_path: str):
        self.path = db_path
        self._conn: Optional[aiosqlite.Connection] = None

    async def _get(self) -> aiosqlite.Connection:
        if self._conn is None:
            # uri=True pour autoriser mode ro
            self._conn = await aiosqlite.connect(
                f"file:{self.path}?mode=ro", uri=True
            )
            self._conn.row_factory = aiosqlite.Row
        return self._conn

    async def close(self):
        if self._conn:
            await self._conn.close()
            self._conn = None

    # ── Chargers ─────────────────────────────────────────────────────────
    async def get_charger(self, charger_id: str) -> Optional[dict]:
        c = await self._get()
        async with c.execute(
            "SELECT * FROM chargers WHERE id = ?", (charger_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def list_online_chargers(self) -> list[dict]:
        c = await self._get()
        async with c.execute(
            "SELECT id, status, last_heartbeat FROM chargers "
            "WHERE UPPER(COALESCE(status,'')) != 'OFFLINE'"
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    # ── Sessions (transactions OCPP) ─────────────────────────────────────
    async def get_latest_session(self, charger_id: str) -> Optional[dict]:
        c = await self._get()
        async with c.execute(
            "SELECT * FROM sessions WHERE charger_id = ? "
            "ORDER BY id DESC LIMIT 1", (charger_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def get_session_by_tx(self, charger_id: str, transaction_id: int) -> Optional[dict]:
        c = await self._get()
        async with c.execute(
            "SELECT * FROM sessions WHERE charger_id = ? AND transaction_id = ?",
            (charger_id, transaction_id),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    # ── MeterValues ──────────────────────────────────────────────────────
    async def get_metervalues_for_session(self, session_id: int,
                                          limit: int = 100) -> list[dict]:
        c = await self._get()
        async with c.execute(
            "SELECT * FROM meter_values WHERE session_id = ? "
            "ORDER BY timestamp DESC LIMIT ?",
            (session_id, limit),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def count_metervalues(self, session_id: int) -> int:
        c = await self._get()
        async with c.execute(
            "SELECT COUNT(*) AS n FROM meter_values WHERE session_id = ?",
            (session_id,),
        ) as cur:
            row = await cur.fetchone()
            return int(row["n"]) if row else 0

    # ── Connector status ─────────────────────────────────────────────────
    async def get_connector_status(self, charger_id: str,
                                   connector_id: int = 1) -> Optional[str]:
        c = await self._get()
        async with c.execute(
            "SELECT status FROM connectors WHERE charger_id = ? AND connector_id = ? "
            "ORDER BY updated_at DESC LIMIT 1",
            (charger_id, connector_id),
        ) as cur:
            row = await cur.fetchone()
            return row["status"] if row else None

    # ── Charging profile snapshots ───────────────────────────────────────
    async def get_snapshot(self, charger_id: str, connector_id: int,
                           purpose: str) -> Optional[dict]:
        c = await self._get()
        async with c.execute(
            "SELECT * FROM charging_profile_snapshots "
            "WHERE charger_id = ? AND connector_id = ? AND purpose = ?",
            (charger_id, connector_id, purpose),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    # ── Reservations ─────────────────────────────────────────────────────
    async def get_reservations(self, charger_id: str,
                               status: Optional[str] = None) -> list[dict]:
        c = await self._get()
        if status:
            q = ("SELECT * FROM reservations WHERE charger_id = ? AND status = ? "
                 "ORDER BY id DESC")
            args: tuple[Any, ...] = (charger_id, status)
        else:
            q = "SELECT * FROM reservations WHERE charger_id = ? ORDER BY id DESC"
            args = (charger_id,)
        async with c.execute(q, args) as cur:
            return [dict(r) for r in await cur.fetchall()]
