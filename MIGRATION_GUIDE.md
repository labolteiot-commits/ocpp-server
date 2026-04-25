# OCPP Server — Guide de migration des correctifs
# Comparaison avec SteVe (steve-community/steve) — Avril 2026

## Résumé des fichiers modifiés

| Fichier | Bugs corrigés |
|---------|---------------|
| `core/ocpp_server.py` | BUG-3, BUG-6, BUG-7, BUG-8 |
| `core/charge_point.py` | BUG-1, BUG-2, BUG-4, BUG-5, BUG-8 |
| `config.py`            | BUG-6 |

---

## BUG-1 — _post_boot_sequence ne attend pas le vrai statut ⚠️ CRITIQUE

### Symptôme
TechnoVE reboot aléatoirement après connexion.
Grizzl-E déconnecte 2-5 secondes après le BootNotification.

### Cause
```python
# AVANT (cassé)
async def _post_boot_sequence(self) -> None:
    await asyncio.sleep(2)          # ← _connector1_status = "Unknown" si StatusNotification > 2s
    vehicle_present = self._connector1_status in VEHICLE_PRESENT_STATUSES  # TOUJOURS False!
```

La borne prend jusqu'à 5-10 secondes pour envoyer le premier StatusNotification après BootNotification.
Avec un sleep fixe de 2s, la logique "véhicule présent?" est basée sur un statut "Unknown" —
donc toujours False. Résultat : ChangeAvailability(Operative) est envoyé sur une borne TechnoVE
en état Preparing (câble branché) → reboot immédiat.

### Correction
```python
# APRÈS (corrigé)
async def _post_boot_sequence(self) -> None:
    try:
        await asyncio.wait_for(
            self._status_received.wait(),   # Attend le vrai StatusNotification
            timeout=15.0                    # Timeout de sécurité si la borne ne répond pas
        )
    except asyncio.TimeoutError:
        log.warning("Timeout attente StatusNotification — séquence continue", id=self.id)
```

### Comment SteVe le fait
SteVe utilise un état de session WebSocket qui attend explicitement la première
StatusNotification avant d'envoyer des commandes de configuration.

---

## BUG-2 — Transaction ID repart à 1 après restart serveur ⚠️ CRITIQUE

### Symptôme
Après un restart du serveur, les nouvelles transactions ont des IDs qui entrent
en collision avec des sessions existantes en DB. La borne reçoit un transaction_id
qui pointe vers une ancienne session terminée.

### Cause
```python
# AVANT (cassé)
def __init__(self, ...):
    self._next_transaction_id: int = 1   # ← Repart toujours à 1 après restart
```

### Correction
Ajouter `_init_transaction_id()` appelé dans `on_boot_notification()` :
```python
async def _init_transaction_id(self) -> None:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(sa_func.max(Session.transaction_id))
            .where(Session.charger_id == self.id, Session.transaction_id > 0)
        )
        max_id = result.scalar_one_or_none()
        if max_id is not None and max_id >= self._next_transaction_id:
            self._next_transaction_id = max_id + 1
```

Et dans `on_boot_notification()` :
```python
self._add_task(self._init_transaction_id())  # ← Ajouter cette ligne
```

### Comment SteVe le fait
SteVe utilise une séquence SQL auto-incrémentée pour les transaction IDs, ce qui
garantit l'unicité même après restart.

---

## BUG-3 — Grizzl-E non détecté / séquence générique échoue ⚠️ CRITIQUE

### Symptôme
Grizzl-E reçoit `ChangeAvailability` en état Preparing → déconnexion ou comportement erratique.
SetChargingProfile avec TxProfile dans RemoteStart → rejet ou reboot.

### Correction
Ajouter la propriété `_is_grizzle` et la méthode `_remote_start_grizzle()` :

```python
@property
def _is_grizzle(self) -> bool:
    mfr = (self._manufacturer or "").upper()
    mdl = (self._model or "").upper()
    return "GRIZZL" in mfr or "GRIZZL" in mdl or "EMOTORWERKS" in mfr
```

Et dans `remote_start_transaction()` :
```python
if self._is_technove:
    return await self._remote_start_technove(connector_id, amps)
elif self._is_grizzle:                                    # ← NOUVEAU
    return await self._remote_start_grizzle(connector_id, amps)
else:
    return await self._remote_start_generic(...)
```

Aussi dans `_post_boot_sequence()`, les gardes TechnoVE s'appliquent maintenant à Grizzl-E :
```python
_skip_avail = (
    (self._is_technove or self._is_grizzle)   # ← Ajouter or self._is_grizzle
    and self._connector1_status in VEHICLE_PRESENT_STATUSES
)
```

---

## BUG-4 — _remote_start_generic envoie ChangeAvailability sans vérifier le statut

### Symptôme
Sur bornes génériques avec câble déjà branché, ChangeAvailability(Operative) est
envoyé même si le connecteur est déjà Available ou Preparing.

### Correction
```python
# AVANT (cassé)
async def _remote_start_generic(self, ...):
    await self.call(call.ChangeAvailability(...))   # ← Toujours envoyé

# APRÈS (corrigé)
async def _remote_start_generic(self, ...):
    connector_status = self._connector1_status
    if connector_status not in VEHICLE_PRESENT_STATUSES and connector_status != "Available":
        await self.call(call.ChangeAvailability(...))   # ← Seulement si nécessaire
```

---

## BUG-5 — Sessions ACTIVE non fermées à la déconnexion

### Symptôme
Après une coupure réseau ou un crash de borne, les sessions restent en statut
ACTIVE en DB indéfiniment. Le dashboard montre des sessions "en cours" fantômes.

### Correction
Dans `on_disconnect()`, ajouter la fermeture des sessions orphelines :

```python
async def on_disconnect(self) -> None:
    # ... code existant ...

    # NOUVEAU : fermer les sessions ACTIVE orphelines
    orphan_ids = list(self._active_transactions.keys())
    if orphan_ids or self._synthetic_session_id is not None:
        async with AsyncSessionLocal() as db:
            now = datetime.now(timezone.utc)
            for txid in orphan_ids:
                result = await db.execute(
                    select(Session).where(
                        Session.charger_id == self.id,
                        Session.transaction_id == txid,
                        Session.status == SessionStatus.ACTIVE,
                    )
                )
                session = result.scalar_one_or_none()
                if session:
                    session.stop_time = now
                    session.stop_reason = "EVDisconnected"
                    session.status = SessionStatus.COMPLETED
            await db.commit()
```

---

## BUG-6 — ocpp2.0.1 accepté sans handler correspondant

### Symptôme
Si une borne négocie le subprotocol `ocpp2.0.1`, le serveur l'accepte mais répond
avec des messages OCPP 1.6 → messages invalides → déconnexion immédiate.

### Correction dans config.py
```python
# AVANT (cassé)
supported_protocols: list[str] = ["ocpp1.6", "ocpp2.0.1"]

# APRÈS (corrigé)
supported_protocols: list[str] = ["ocpp1.6"]   # ChargePoint est OCPP 1.6 uniquement
```

---

## BUG-7 — Grizzl-E envoie "ocpp1.6.0" — rejeté avec code 1002

### Symptôme
Grizzl-E (et certains firmwares anciens) envoie `Sec-WebSocket-Protocol: ocpp1.6.0`
au lieu de `ocpp1.6`. Le serveur rejette la connexion avec code 1002 Protocol Error.
La borne retente indéfiniment sans succès.

### Correction dans ocpp_server.py
```python
# Variantes acceptées — normalisées vers "ocpp1.6"
_OCPP16_VARIANTS = {"ocpp1.6", "ocpp1.6.0", "ocpp16", "ocpp1.6j"}

def _normalize_subprotocol(requested: Optional[str]) -> Optional[str]:
    if not requested:
        return None
    normalized = requested.lower().replace(" ", "")
    if normalized in _OCPP16_VARIANTS:
        return "ocpp1.6"
    return None
```

Et dans `websockets.serve()` :
```python
async with websockets.serve(
    self.on_connect,
    settings.ocpp.host,
    settings.ocpp.port,
    subprotocols=list(_OCPP16_VARIANTS),   # ← Toutes les variantes
    max_size=256 * 1024,                   # ← NOUVEAU : 256 KB max
    process_request=self.process_request,  # ← NOUVEAU : rejet pendant handshake
):
```

---

## BUG-8 — broadcast_disconnect manquant — dashboard figé après déconnexion

### Correction dans ocpp_server.py
Ajouter la méthode :
```python
async def broadcast_disconnect(self, charger_id: str) -> None:
    await self._broadcast({
        "type":       "status_update",
        "charger_id": charger_id,
        "connector_id": 0,
        "status":     "Offline",
    })
```

Et appeler dans `on_disconnect()` :
```python
await self.server.broadcast_disconnect(self.id)   # ← Ajouter à la fin
```

---

## Ordre d'application recommandé

1. `config.py` — retirer `ocpp2.0.1` des protocols supportés
2. `core/ocpp_server.py` — remplacer entièrement
3. `core/charge_point.py` — remplacer entièrement
4. Redémarrer le serveur : `python main.py`
5. Vérifier les logs au premier BootNotification d'une TechnoVE ou Grizzl-E :
   - Chercher "Statut reçu — démarrage séquence post-boot"
   - Si "Timeout attente StatusNotification" → la borne est lente, c'est toléré
   - Vérifier qu'il n'y a pas de "ChangeAvailability Operative" si le câble est branché

## Tests de validation

### Test 1 : Connexion avec câble déjà branché (Preparing au boot)
La séquence correcte dans les logs doit être :
```
BootNotification received
Statut reçu — démarrage séquence post-boot  (status=Preparing)
ChangeAvailability Operative skippé — véhicule déjà présent
TxDefaultProfile appliqué (ou différé)
Démarrage polling MeterValues
Auto-RemoteStart (si boot_lock=False)
```
NON :
```
BootNotification received
ChangeAvailability Operative envoyé  ← MAUVAIS si câble branché
```

### Test 2 : Restart serveur avec session active
Vérifier que `_next_transaction_id` repart après le max en DB :
```
Transaction ID initialisé depuis DB  next_id=X
```

### Test 3 : Connexion Grizzl-E
Vérifier dans les logs :
```
is_grizzle=True
Grizzl-E — TxDefaultProfile pré-RemoteStart
Grizzl-E — RemoteStart  success=True
```
