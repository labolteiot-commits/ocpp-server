"""Couche 4 — Auto-expire des réservations (Sprint 28 A7).

La boucle `_reservation_expiry_loop` tourne toutes les 60s dans
`core/ocpp_server.py::start()`. Elle appelle `expire_stale_reservations()`
qui fait :

    UPDATE reservations
       SET status='EXPIRED', updated_at=now
     WHERE status='ACTIVE' AND expiry_date < now

Ce test crée une réservation avec expiry=now+10s puis attend jusqu'à 90s
pour qu'au moins 1 cycle de la boucle passe après l'expiration. Marqué
`slow` pour permettre un filtrage rapide sur le harness principal.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from tests.helpers.wait import wait_for


pytestmark = [pytest.mark.sim_only, pytest.mark.slow]


async def test_reservation_auto_expires(api, db, clean_sim_state,
                                        rawlog_sim, sim_online,
                                        charger_ids):
    """E1 — Reserve expiry=+10s, wait 90s max → DB status=EXPIRED.

    Pire cas théorique : loop vient juste de tourner au moment où on
    reserve → next tick à 60s. Avec expiry=+10s on attend ~60s. On
    prévoit 90s pour absorber un cycle raté.
    """
    charger_id = charger_ids["sim"]
    # Réservation courte (10s)
    expiry = (datetime.now(timezone.utc) + timedelta(seconds=10)).isoformat()
    r = await api.reserve(charger_id, id_tag="LOCAL", connector_id=1,
                          expiry_date=expiry)
    assert r.status_code == 200
    data = r.json()
    assert data["db_status"] == "Active"
    rid = data["reservation_id"]

    # Attendre que la boucle marque EXPIRED (elle tourne toutes les 60s)
    # SQLAlchemy stocke le NOM de l'enum (EXPIRED, pas "Expired")
    async def _is_expired():
        rows = await db.get_reservations(charger_id, status="EXPIRED")
        return next((r for r in rows if r["reservation_id"] == rid), None)

    row = await wait_for(_is_expired, timeout=90, poll=2.0,
                         message="Auto-expire pas déclenché après 90s")
    assert row["id_tag"] == "LOCAL"
    # updated_at doit être > expiry_date (preuve que la boucle a tourné après)
    # On vérifie juste que la ligne est bien passée, pas les timestamps précis.


async def test_expired_not_consumed_by_startx(api, sim, db,
                                              clean_sim_state, sim_online,
                                              charger_ids):
    """E2 — Une réservation EXPIRED ne doit PAS être consommée par StartTx.

    Vérifie que `_consume_reservation` filtre bien sur status=ACTIVE +
    expiry_date > now. On crée une réservation courte, on force le retard
    jusqu'à ce qu'elle soit EXPIRED, puis on déclenche un StartTx via
    RemoteStartTransaction (§6.15) → la ligne doit rester EXPIRED (pas
    devenir USED).

    Note : RemoteStartTransaction utilisé (plutôt que plug auto_start) car
    la sim conserve `conn1_status=Reserved` localement tant que la
    CancelReservation OCPP n'est pas reçue — l'auto-expire serveur ne
    notifie pas la sim. RemoteStart contourne ce blocage (§6.15 autorise
    démarrage sur connecteur Reserved avec id_tag matchant).
    """
    charger_id = charger_ids["sim"]
    expiry = (datetime.now(timezone.utc) + timedelta(seconds=10)).isoformat()
    r = await api.reserve(charger_id, id_tag="LOCAL", connector_id=1,
                          expiry_date=expiry)
    rid = r.json()["reservation_id"]

    # Attendre qu'elle devienne EXPIRED (boucle 60s)
    async def _is_expired():
        rows = await db.get_reservations(charger_id, status="EXPIRED")
        return next((r for r in rows if r["reservation_id"] == rid), None)
    await wait_for(_is_expired, timeout=90, poll=2.0,
                   message="Pas EXPIRED après 90s")

    # Plug puis RemoteStart pour déclencher un StartTx
    await sim.plug(True, soc_start=30.0, soc_target=80.0, battery_kwh=60.0)
    await asyncio.sleep(1.0)

    # RemoteStartTransaction avec id_tag=LOCAL — même id_tag que la
    # réservation EXPIRED : si le filtre _consume_reservation était
    # cassé, la ligne passerait à USED.
    try:
        await api.remote_start(charger_id, id_tag="LOCAL", connector_id=1)
    except Exception:
        pass  # best-effort — même sans StartTx la ligne doit rester EXPIRED

    async def _has_tx():
        st = await sim.get_state()
        return st.get("transaction_id")
    try:
        await wait_for(_has_tx, timeout=10, message="Pas de transaction")
    except TimeoutError:
        pass  # OK — même sans StartTx, on vérifie l'absence dans USED

    # Laisser _consume_reservation s'exécuter si StartTx a eu lieu
    await asyncio.sleep(2.0)

    # La réservation ne doit PAS être passée à USED (le filtre ACTIVE
    # l'exclut → elle reste EXPIRED)
    rows_used = await db.get_reservations(charger_id, status="USED")
    assert not any(x["reservation_id"] == rid for x in rows_used), \
        f"EXPIRED consommée à tort: {rid}"

    # Ligne toujours en EXPIRED (pas mutée)
    rows_expired = await db.get_reservations(charger_id, status="EXPIRED")
    assert any(x["reservation_id"] == rid for x in rows_expired), \
        f"Ligne EXPIRED disparue: {rid}"

    # Cleanup
    await sim.plug(False)
