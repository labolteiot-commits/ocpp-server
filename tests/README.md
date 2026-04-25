# Harness de test OCPP — `/home/lteiot/ocpp-server/tests/`

Tests pytest-asyncio contre le serveur OCPP live (port 8000), la borne virtuelle
(port 8001) et la DB SQLite (`data/ocpp.db`, lecture seule).

## Lancer

```bash
cd /home/lteiot/ocpp-server
source .venv/bin/activate
./tests/run_all.sh                     # couche 1 sim_only (safe, ≈ 5 min)
./tests/run_all.sh "not requires_vehicle"   # + bornes réelles sans EV
./tests/run_all.sh "requires_vehicle"       # EV physique requis
```

Rapports JUnit XML + HTML dans `logs/test_reports/ocpp-<stamp>.{xml,html}`.

## Markers

| Marker | Sens |
|---|---|
| `sim_only` | Ne touche que VIRTUAL-001 — non destructif, CI nocturne OK |
| `live_charger` | Commande BORNE-CABANON4 ou GRIZZLE-001 (sans EV) |
| `requires_vehicle` | EV physique branché attendu |
| `slow` | > 30 s |

## Couches

| Couche | Portée | Statut |
|---|---|---|
| 1 — Core OCPP | Heartbeat, Authorize, Session plug→stop, MeterValues units, Fault | **livré** |
| 2 — Contrôle puissance | SetChargingProfile rampe, A9 replay, A8 clear | à livrer |
| 3 — Commandes distance | Trigger, ChangeAvailability, Unlock, ConfigChange | à livrer |
| 4 — A7 | Reserve, Cancel, auto-expire, DataTransfer | à livrer |
| 5 — Véhicule réel | Golden path, reboot mid-charge, HQ peak | à livrer |
| 6 — Faults sim | stuck_charging, contactor_welded, comm_slow | à livrer |

## Conventions

- **Lecture DB uniquement** — jamais d'INSERT/UPDATE depuis les tests. Déclencher
  via API REST ou borne virtuelle, puis observer.
- **Fixtures autoclean** — `clean_sim_state` garantit le sim unplugged + sans
  fault avant et après chaque test.
- **Timeouts généreux** — 10-30 s par défaut pour tolérer la latence OCPP.
- **Raw log tailing** — `rawlog_sim` fixture se positionne en fin de fichier à
  l'ouverture du test ; ne voit que les frames post-open.
- **Pas d'assertion SOC/kWh stricte** — tolérance ±10 % minimum, le bruit sim
  est volontaire (§25 Sprint 27).
