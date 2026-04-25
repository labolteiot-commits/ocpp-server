"""
PATCH : core/charge_point.py
Applique les correctifs suivants :

  BUG-1  _build_charging_profile : charging_profile_id utilisait `p`
         (la string purpose) au lieu de `profile_id` (int).
         Résultat : certaines bornes recevaient {"charging_profile_id":"TxDefaultProfile",...}
         et rejetaient silencieusement le profil.

  BUG-2  _server_lock_active non initialisé dans __init__
         → AttributeError dès le premier stop_charging / remote_start.

  BUG-3  _do_remote_start : purpose "TxProfile" envoyé sans transaction_id
         dans SetChargingProfile (avant RemoteStart).
         "TxProfile" sans transaction_id est invalide selon la spec OCPP 1.6 §7.3.
         Correction : "ChargePointMaxProfile" pour les bornes génériques.

  BUG-4  _handle_status_transition : absence de re-application du profil
         quand un véhicule se branche APRÈS le boot (Available → Preparing).
         C'est la cause directe du problème BORNE-CABANON4 : le profil 24A est
         bien envoyé au boot (borne Available à ce moment) mais ignoré ensuite
         car rien ne le ré-envoie quand le connecteur passe Preparing.
         Correction : déclenchement de _apply_profile_on_connect() lors de la
         transition, avec la même logique de sécurité que _post_boot_sequence.

Instructions d'application
──────────────────────────
Chercher dans core/charge_point.py les blocs marqués "REMPLACER PAR" ci-dessous
et remplacer le bloc original par le bloc de remplacement.
Chaque bloc est délimité par ≡≡≡ ORIGINAL ≡≡≡ / ≡≡≡ REMPLACEMENT ≡≡≡.

Pour un patch automatisé, lancer :
  python3 PATCH_charge_point.py core/charge_point.py
"""

import sys
import re

PATCHES = [
    # ─────────────────────────────────────────────────────────────
    # BUG-2 : _server_lock_active manquant dans __init__
    # ─────────────────────────────────────────────────────────────
    dict(
        description="BUG-2 — ajouter _server_lock_active dans __init__",
        original='''\
        self._remote_start_lock = asyncio.Lock()
        self._auto_start_pending: bool = False''',
        replacement='''\
        self._remote_start_lock = asyncio.Lock()
        self._auto_start_pending: bool = False
        self._server_lock_active: bool = False   # BUG-2 FIX : évite AttributeError
        self._scheduler_applied_amps: float | None = None  # tracker pour le scheduler''',
    ),

    # ─────────────────────────────────────────────────────────────
    # BUG-1 : _build_charging_profile — charging_profile_id = p (string)
    # ─────────────────────────────────────────────────────────────
    dict(
        description="BUG-1 — _build_charging_profile : charging_profile_id doit être profile_id (int)",
        original='''\
    def _build_charging_profile(self, amps, profile_id=99, purpose=None, stack_level=None):
        p = purpose or ("TxDefaultProfile" if self.profile.use_tx_default_profile else "ChargePointMaxProfile")
        sl = stack_level if stack_level is not None else self.profile.profile_stack_level
        return {"charging_profile_id":p,"stack_level":sl,"charging_profile_purpose":p,
                "charging_profile_kind":"Absolute",
                "charging_schedule":{"charging_rate_unit":"A",
                                     "charging_schedule_period":[{"start_period":0,"limit":amps}]}}''',
        replacement='''\
    def _build_charging_profile(self, amps, profile_id=99, purpose=None, stack_level=None):
        # BUG-1 FIX : charging_profile_id doit être l'entier profile_id, PAS la purpose string
        purpose_str = purpose or ("TxDefaultProfile" if self.profile.use_tx_default_profile else "ChargePointMaxProfile")
        sl = stack_level if stack_level is not None else self.profile.profile_stack_level
        return {
            "charging_profile_id":      profile_id,       # ← int, pas purpose_str
            "stack_level":              sl,
            "charging_profile_purpose": purpose_str,
            "charging_profile_kind":    "Absolute",
            "charging_schedule": {
                "charging_rate_unit":       "A",
                "charging_schedule_period": [{"start_period": 0, "limit": amps}],
            },
        }''',
    ),

    # ─────────────────────────────────────────────────────────────
    # BUG-3 : _do_remote_start — "TxProfile" sans transaction_id
    # ─────────────────────────────────────────────────────────────
    dict(
        description="BUG-3 — _do_remote_start : TxProfile → ChargePointMaxProfile pour bornes génériques",
        original='''\
        if amps is not None:
            prof={"charging_profile_id":99,"stack_level":p.profile_stack_level,
                  "charging_profile_purpose":"TxDefaultProfile" if p.use_tx_default_profile else "TxProfile",
                  "charging_profile_kind":"Absolute",
                  "charging_schedule":{"charging_rate_unit":"A","charging_schedule_period":[{"start_period":0,"limit":amps}]}}
            try:
                resp=await self._safe_call(call.SetChargingProfile(connector_id=p.profile_connector_id,cs_charging_profiles=prof),context="SetChargingProfile pre-RemoteStart")
                log.info("Profil pré-RemoteStart",id=self.id,amps=amps,status=resp.status if resp else "none")
            except Exception as e: log.warning("SetChargingProfile pre-RemoteStart failed",id=self.id,error=str(e))
            await asyncio.sleep(1)''',
        replacement='''\
        if amps is not None:
            # BUG-3 FIX : TxProfile SANS transaction_id est invalide (OCPP spec §7.3).
            # SetChargingProfile avant RemoteStart doit être TxDefaultProfile ou ChargePointMaxProfile.
            pre_purpose = "TxDefaultProfile" if p.use_tx_default_profile else "ChargePointMaxProfile"
            pre_connector = p.profile_connector_id  # 1 pour TxDefault, 0 pour MaxProfile
            prof = {
                "charging_profile_id":      99,
                "stack_level":              p.profile_stack_level,
                "charging_profile_purpose": pre_purpose,
                "charging_profile_kind":    "Absolute",
                "charging_schedule": {
                    "charging_rate_unit":       "A",
                    "charging_schedule_period": [{"start_period": 0, "limit": amps}],
                },
            }
            try:
                resp = await self._safe_call(
                    call.SetChargingProfile(connector_id=pre_connector, cs_charging_profiles=prof),
                    context="SetChargingProfile pre-RemoteStart",
                )
                log.info("Profil pré-RemoteStart", id=self.id, amps=amps,
                         purpose=pre_purpose, status=resp.status if resp else "none")
            except Exception as e:
                log.warning("SetChargingProfile pre-RemoteStart failed", id=self.id, error=str(e))
            await asyncio.sleep(1)''',
    ),

    # ─────────────────────────────────────────────────────────────
    # BUG-4 : _handle_status_transition — pas de re-application du profil
    # ─────────────────────────────────────────────────────────────
    dict(
        description="BUG-4 — _handle_status_transition : re-appliquer le profil sur Available→Preparing",
        original='''\
    async def _handle_status_transition(self, prev, new):
        if new in VEHICLE_PRESENT_STATUSES and prev not in VEHICLE_PRESENT_STATUSES:
            self._start_meter_polling()
        if new in {"Available","Unavailable","Faulted"} and prev in VEHICLE_PRESENT_STATUSES:
            self._stop_meter_polling()
            if self._synthetic_session_id is not None:
                await self._close_synthetic_session()''',
        replacement='''\
    async def _handle_status_transition(self, prev, new):
        if new in VEHICLE_PRESENT_STATUSES and prev not in VEHICLE_PRESENT_STATUSES:
            self._start_meter_polling()
            # BUG-4 FIX : re-appliquer le profil quand un véhicule se branche APRÈS le boot.
            # Au boot, _post_boot_sequence applique le profil si la borne était Available.
            # Si le véhicule se branche ensuite (Available → Preparing), rien ne ré-envoie
            # le profil → la borne charge à sa limite hardware (ex: 48A pour TechnoVE).
            if self._default_max_amps is not None and not self._boot_lock:
                self._add_task(self._apply_profile_on_connect())

        if new in {"Available","Unavailable","Faulted"} and prev in VEHICLE_PRESENT_STATUSES:
            self._stop_meter_polling()
            if self._synthetic_session_id is not None:
                await self._close_synthetic_session()

    async def _apply_profile_on_connect(self):
        """
        Ré-applique le profil de charge dès qu'un véhicule se branche (Available→Preparing).

        Différences avec _post_boot_sequence :
        - On est APRÈS le boot : defer_profile_if_occupied n'est plus pertinent.
        - Pour TechnoVE (use_tx_default_profile=True) en Preparing : envoyer TxDefaultProfile
          SANS le cycle Inop/Op (qui provoque un reboot en état Preparing).
        - Si boot_lock est actif : ne pas démarrer automatiquement.
        - Lancer _auto_remote_start si profile.send_remote_start_after_boot et pas de verrou.
        """
        p = self.profile
        # Laisser le temps au StatusNotification d'être traité
        await asyncio.sleep(2)

        # Ne rien faire si le connecteur n'est plus en état véhicule-présent
        if self._connector1_status not in VEHICLE_PRESENT_STATUSES:
            return

        # Appliquer le profil de courant
        try:
            status = await self.set_current_limit(self._default_max_amps)
            log.info("Profil appliqué — vehicle connect",
                     id=self.id, amps=self._default_max_amps,
                     profile=p.name, status=status)
        except Exception as e:
            log.warning("_apply_profile_on_connect échoué", id=self.id, error=str(e))

        # Lancer RemoteStart auto si configuré et pas de verrou serveur
        if (p.send_remote_start_after_boot
                and not self._boot_lock
                and not self._server_lock_active
                and not self._active_transactions):
            await asyncio.sleep(p.remote_start_delay)
            if self._connector1_status in VEHICLE_PRESENT_STATUSES and not self._active_transactions:
                log.info("Auto-RemoteStart post-connect", id=self.id, profile=p.name)
                self._add_task(self._do_remote_start(1, self._default_max_amps, self._local_id_tag))''',
    ),
]


def apply_patches(filepath: str) -> None:
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    for patch in PATCHES:
        orig = patch["original"]
        repl = patch["replacement"]
        desc = patch["description"]

        # Normaliser les indentations pour la comparaison
        if orig in content:
            content = content.replace(orig, repl, 1)
            print(f"✅ Appliqué : {desc}")
        else:
            # Essayer avec strip (tolérance aux espaces de fin de ligne)
            lines_orig = [l.rstrip() for l in orig.splitlines()]
            lines_content = [l.rstrip() for l in content.splitlines()]
            orig_stripped = "\n".join(lines_orig)
            content_stripped_lines = "\n".join(lines_content)

            if orig_stripped in content_stripped_lines:
                # Reconstituer en remplaçant dans le contenu original
                content = content.replace(orig.rstrip(), repl, 1)
                print(f"✅ Appliqué (stripped) : {desc}")
            else:
                print(f"❌ ÉCHEC — bloc introuvable : {desc}")
                print(f"   Extrait attendu (50 premiers chars) : {repr(orig[:80])}")

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"\n✅ Fichier mis à jour : {filepath}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage : python3 PATCH_charge_point.py <chemin>/core/charge_point.py")
        sys.exit(1)
    apply_patches(sys.argv[1])
