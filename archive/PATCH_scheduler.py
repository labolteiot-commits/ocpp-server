"""
PATCH : core/scheduler.py
Applique les correctifs suivants :

  BUG-6  _apply_amps : utilise cp._is_technove (seulement TechnoVE) au lieu de
         cp.profile.use_tx_default_profile (TechnoVE + Grizzl-E + toutes bornes
         nécessitant TxDefaultProfile). De plus, ignore TxProfile si une session
         OCPP est active — or TxProfile a effet immédiat sur la transaction active.
         Correction : déléguer à cp.set_current_limit(amps) qui est déjà
         entièrement profile-aware et gère TxProfile/TxDefault/ChargePointMax.

  BUG-7  _process_vehicle : compare target_amps avec cp._default_max_amps
         (la limite configurée en DB par l'opérateur) au lieu de la dernière
         valeur réellement appliquée par le scheduler.
         Conséquence : si le scheduler applique 16A mais que default_max_amps=24A,
         au prochain cycle il considère Δ=8A > 1A et renvoie inutilement le profil.
         Correction : utiliser cp._scheduler_applied_amps (initialisé par BUG-2 fix).

Instructions d'application
──────────────────────────
  python3 PATCH_scheduler.py core/scheduler.py
"""

import sys

PATCHES = [
    # ─────────────────────────────────────────────────────────────
    # BUG-7 : comparer avec _scheduler_applied_amps, pas _default_max_amps
    # ─────────────────────────────────────────────────────────────
    dict(
        description="BUG-7 — comparer target_amps avec _scheduler_applied_amps",
        original='''\
        # Appliquer le courant du plan si différent de la limite actuelle
        target_amps = plan.required_amps
        current_limit = cp._default_max_amps  # limite actuelle configurée''',
        replacement='''\
        # Appliquer le courant du plan si différent de la dernière valeur appliquée
        target_amps = plan.required_amps
        # BUG-7 FIX : utiliser _scheduler_applied_amps (dernière valeur poussée par
        # le scheduler) et non _default_max_amps (limite configurée par l'opérateur).
        current_limit = cp._scheduler_applied_amps  # None si jamais appliqué ce cycle''',
    ),

    # ─────────────────────────────────────────────────────────────
    # BUG-6 : _apply_amps → déléguer à set_current_limit
    # ─────────────────────────────────────────────────────────────
    dict(
        description="BUG-6 — _apply_amps : déléguer à cp.set_current_limit (profile-aware)",
        original='''\
    async def _apply_amps(self, cp, amps: float, reason: str = "") -> None:
        """Applique un courant via SetChargingProfile sur la borne."""
        if cp._is_technove:
            profile = {
                "charging_profile_id":      99,
                "stack_level":              0,
                "charging_profile_purpose": "TxDefaultProfile",
                "charging_profile_kind":    "Absolute",
                "charging_schedule": {
                    "charging_rate_unit": "A",
                    "charging_schedule_period": [
                        {"start_period": 0, "limit": amps}
                    ],
                },
            }
            connector_id = 1
        else:
            profile = {
                "charging_profile_id":      99,
                "stack_level":              8,
                "charging_profile_purpose": "ChargePointMaxProfile",
                "charging_profile_kind":    "Absolute",
                "charging_schedule": {
                    "charging_rate_unit": "A",
                    "charging_schedule_period": [
                        {"start_period": 0, "limit": amps}
                    ],
                },
            }
            connector_id = 0

        try:
            status = await cp.set_charging_profile(connector_id, profile)
            log.info("Scheduler — SetChargingProfile",
                     charger_id=cp.id, amps=amps, status=status, reason=reason)
        except Exception as e:
            log.warning("Scheduler — SetChargingProfile échoué",
                        charger_id=cp.id, error=str(e))''',
        replacement='''\
    async def _apply_amps(self, cp, amps: float, reason: str = "") -> None:
        """
        Applique un courant via set_current_limit (entièrement profile-aware).

        BUG-6 FIX :
        - Ancienne implémentation : if cp._is_technove → TxDefaultProfile/connector_1
          sinon ChargePointMaxProfile/connector_0.
          Problème : _is_technove est False pour Grizzl-E (use_tx_default_profile=True
          mais profile.name != "TechnoVE"). Grizzl-E recevait ChargePointMaxProfile
          qui est accepté mais IGNORÉ par son firmware.
        - Nouvelle implémentation : déléguer à cp.set_current_limit() qui :
            • Session OCPP active → TxProfile + transaction_id (effet immédiat)
            • Pas de session → TxDefaultProfile (bornes use_tx_default_profile=True)
                              → ChargePointMaxProfile (bornes génériques)
          Toute la logique fabricant est centralisée dans set_current_limit.
        """
        try:
            status = await cp.set_current_limit(amps)
            # Tracker la dernière valeur appliquée (BUG-7 fix)
            cp._scheduler_applied_amps = amps
            log.info("Scheduler — set_current_limit",
                     charger_id=cp.id, amps=amps, status=status, reason=reason,
                     profile=cp.profile.name)
        except Exception as e:
            log.warning("Scheduler — set_current_limit échoué",
                        charger_id=cp.id, error=str(e))''',
    ),
]


def apply_patches(filepath: str) -> None:
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    for patch in PATCHES:
        orig = patch["original"]
        repl = patch["replacement"]
        desc = patch["description"]

        if orig in content:
            content = content.replace(orig, repl, 1)
            print(f"✅ Appliqué : {desc}")
        else:
            print(f"❌ ÉCHEC — bloc introuvable : {desc}")
            print(f"   Extrait attendu (80 premiers chars) : {repr(orig[:80])}")

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"\n✅ Fichier mis à jour : {filepath}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage : python3 PATCH_scheduler.py <chemin>/core/scheduler.py")
        sys.exit(1)
    apply_patches(sys.argv[1])
