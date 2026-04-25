"""
PATCH : core/charger_profiles.py
Applique le correctif suivant :

  BUG-5  Grizzl-E non détecté :
         - "UNITED CHARGER" (avec espace) n'est pas une sous-chaîne de
           "UNITEDCHARGERS" (sans espace) → is_grizzle = False
         - Le modèle "GRU 80A 2024" (série GRU) n'est pas couvert par
           startswith("GRS-") ni startswith("GWM-")
         Conséquence : la Grizzl-E GRIZZLE-001 (vendor=UnitedChargers,
         model=GRU 80A 2024, firmware GRU 80A 2024) reçoit le profil
         Generic → ChargePointMaxProfile sur connector 0 → accepté mais ignoré.

Instructions d'application
──────────────────────────
  python3 PATCH_charger_profiles.py core/charger_profiles.py
"""

import sys

PATCHES = [
    dict(
        description="BUG-5 — détection Grizzl-E : corriger correspondance vendor sans espace + modèle GRU",
        original='''\
    is_grizzle = (
        "UNITED CHARGER" in v
        or "GRIZZL" in v or "GRIZZL" in m
        or "CHARGELAB" in v
        or m.startswith("GRS-")
        or m.startswith("GWM-")
    )''',
        replacement='''\
    # BUG-5 FIX :
    #  - "UNITED CHARGER" (espace) ≠ "UNITEDCHARGERS" → utiliser "UNITED" in v and "CHARGER" in v
    #  - Modèles GRU (ex: "GRU 80A 2024") absent de GRS-/GWM- → ajouter GRU
    #  - Garder "UNITED CHARGER" pour tolérance (certains firmwares ont l'espace)
    is_grizzle = (
        ("UNITED" in v and "CHARGER" in v)   # "UnitedChargers", "United Charger", "United Chargers"
        or "GRIZZL" in v or "GRIZZL" in m
        or "CHARGELAB" in v
        or m.startswith("GRS-")
        or m.startswith("GWM-")
        or m.startswith("GRU")               # GRU 80A 2024, GRU 40A, etc.
    )''',
    ),

    # ── Détection firmware GRU : ajouter avant le check GWM ──────────────────
    dict(
        description="BUG-5 — détection firmware GRU dans le bloc Grizzl-E",
        original='''\
    if is_grizzle:
        # Firmware GWM (nouveau format 2023+)
        if fw.upper().startswith("GWM") or "GWM" in m:
            return PROFILE_GRIZZLE_GWM
        # Firmware 5.x — numéro majeur >= 5
        try:
            major = int(fw.split(".")[0])
            if major >= 5:
                return PROFILE_GRIZZLE_V5
            elif major >= 3:
                return PROFILE_GRIZZLE_V3
        except (ValueError, IndexError):
            pass
        # Firmware inconnu → appliquer le profil le plus conservateur (5.x)
        return PROFILE_GRIZZLE_V5''',
        replacement='''\
    if is_grizzle:
        # Firmware GWM (nouveau format 2023+)
        if fw.upper().startswith("GWM") or "GWM" in m:
            return PROFILE_GRIZZLE_GWM
        # BUG-5 FIX : série GRU — firmware non versionné au format X.Y
        # Ex: vendor=UnitedChargers, model="GRU 80A 2024", fw="GRU 80A 2024"
        # → appliquer le profil le plus safe (V5 : skip_meter_config, lenient JSON)
        if m.startswith("GRU") or fw.upper().startswith("GRU"):
            return PROFILE_GRIZZLE_V5
        # Firmware 5.x — numéro majeur >= 5
        try:
            major = int(fw.split(".")[0])
            if major >= 5:
                return PROFILE_GRIZZLE_V5
            elif major >= 3:
                return PROFILE_GRIZZLE_V3
        except (ValueError, IndexError):
            pass
        # Firmware inconnu → appliquer le profil le plus conservateur (5.x)
        return PROFILE_GRIZZLE_V5''',
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
        print("Usage : python3 PATCH_charger_profiles.py <chemin>/core/charger_profiles.py")
        sys.exit(1)
    apply_patches(sys.argv[1])
