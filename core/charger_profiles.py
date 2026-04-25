# core/charger_profiles.py
"""
Profils de compatibilité par fabricant de borne OCPP 1.6.

POURQUOI CE MODULE EXISTE
─────────────────────────
OCPP est censé être un standard universel. En pratique, chaque fabricant
implémente le firmware différemment, avec des bugs et des comportements
non-conformes qui varient selon le modèle ET la version de firmware.

SteVe (la référence open-source en Java) adopte une approche "one size fits all" :
il implémente le standard à la lettre et documente quelles bornes fonctionnent.
Cette approche est viable pour les déploiements commerciaux avec des bornes certifiées
OCA. Pour des bornes résidentielles grand public (TechnoVE, Grizzl-E), c'est insuffisant.

Ce module centralise TOUS les quirks connus en une seule source de vérité :
- La détection se fait UNE SEULE FOIS au BootNotification
- Les décisions dans charge_point.py consultent `cp.profile.flag` au lieu de
  multiplier les `if self._is_technove / elif self._is_grizzle`
- Ajouter un nouveau fabricant = ajouter un bloc ici, RIEN d'autre à toucher

SOURCES
───────
- Tests terrain TechnoVE (ce projet)
- Home Assistant OCPP integration issue tracker (lbbrhzn/ocpp)
- Fork ocpp-grizzl-e (stefanthoss) — firmware 5.x workarounds
- ChargeLab documentation publique
- SteVe Charging Station Compatibility wiki
- Terrain ABB Terra AC (community reports)
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


# ─────────────────────────────────────────────────────────────────────────────
# Dataclass principal — un profil par borne détectée
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ChargerProfile:
    """
    Ensemble de flags et paramètres qui pilotent le comportement du serveur
    vis-à-vis d'une borne spécifique.

    Valeurs par défaut = comportement générique OCPP 1.6 conforme au standard.
    Les profils fabricant ne surchargent QUE ce qui diffère.
    """

    # ── Identité ─────────────────────────────────────────────────────────────
    name: str = "Generic"
    """Nom lisible du profil (pour les logs)."""

    # ── Profils de charge (SetChargingProfile) ────────────────────────────────
    use_tx_default_profile: bool = False
    """
    True  → utiliser TxDefaultProfile sur connector 1 (stack_level=0).
    False → utiliser ChargePointMaxProfile sur connector 0 (stack_level élevé).

    TechnoVE : True — ChargePointMaxProfile est rejeté hors transaction.
    Grizzl-E 3.x : True — ChargePointMaxProfile est accepté mais IGNORÉ (bug firmware).
    Grizzl-E 5.x : True — même problème + réponses JSON invalides.
    ABB Terra : False — ChargePointMaxProfile fonctionne correctement.
    """

    profile_connector_id: int = 0
    """
    Connector ID cible pour SetChargingProfile.
    0 = toute la borne (ChargePointMaxProfile).
    1 = connecteur 1 spécifiquement (TxDefaultProfile).
    """

    profile_stack_level: int = 8
    """Stack level du profil de charge par défaut."""

    no_profile_in_remote_start: bool = False
    """
    True → NE PAS inclure de charging_profile dans RemoteStartTransaction.req.
    Appliquer le profil via SetChargingProfile séparé AVANT l'envoi.

    TechnoVE : True — reboot si TxProfile inclus dans RemoteStart.
    Grizzl-E 5.x : True — même comportement.
    Standard : False — RemoteStart peut inclure un TxProfile.
    """

    defer_profile_if_occupied: bool = False
    """
    True → ne pas envoyer SetChargingProfile si le connecteur est déjà occupé
    (Preparing/Charging/etc.) au moment du boot.

    TechnoVE : True — SetChargingProfile en état Preparing → timeout 30s → reboot.
    Grizzl-E : True — même comportement observé.
    """

    # ── ChangeAvailability ────────────────────────────────────────────────────
    skip_availability_when_occupied: bool = False
    """
    True → ne jamais envoyer ChangeAvailability(Operative) si le connecteur
    est déjà en état Preparing/Charging/Finishing/SuspendedEV/SuspendedEVSE.

    TechnoVE : True — ChangeAvailability en état Preparing → reboot immédiat.
    Grizzl-E : True — même comportement documenté.
    Standard : False — ChangeAvailability est idempotent selon la spec.
    """

    # ── RemoteStart ───────────────────────────────────────────────────────────
    remote_start_delay: float = 1.0
    """Délai (secondes) entre ChangeAvailability et RemoteStartTransaction."""

    requires_local_list: bool = False
    """
    True → envoyer SendLocalList avec l'idTag admin au boot.
    Nécessaire si AllowOfflineTxForUnknownId=False + LocalAuthorizeOffline=True.

    TechnoVE : True — crashe si idTag absent de la liste locale.
    Grizzl-E 3.x : True — même configuration par défaut.
    Grizzl-E 5.x : False — gestion différente de l'autorisation.
    """

    # ── MeterValues ───────────────────────────────────────────────────────────
    meter_values_require_transaction: bool = False
    """
    True → la borne n'envoie des MeterValues QUE pendant une transaction OCPP.
    Activer le polling via TriggerMessage pour obtenir des données hors-transaction.

    Grizzl-E : True — MeterValues uniquement pendant les sessions actives.
    TechnoVE : False — supporte Cst_MeterValuesInTxOnly=false (clé non-standard).
    """

    skip_measurand_detection: bool = False
    """
    True → ne pas faire de GetConfiguration pour détecter les measurands supportés.
    Utiliser fixed_measurands à la place.

    ABB Terra AC (fw <= 1.8.21) : True — répond comme si tous sont supportés,
    puis reboot quand on essaie de les configurer tous. Boucle de reboot garantie.
    """

    fixed_measurands: list[str] = field(default_factory=lambda: [
        "Energy.Active.Import.Register",
        "Power.Active.Import",
        "Current.Import",
        "Voltage",
    ])
    """
    Measurands à configurer si skip_measurand_detection=True.
    Ignoré si skip_measurand_detection=False (détection automatique utilisée).
    """

    # ── ChangeConfiguration ───────────────────────────────────────────────────
    skip_meter_config: bool = False
    """
    True → ne pas envoyer ChangeConfiguration pour MeterValuesSampledData.
    Certains firmwares retournent NotSupported et peuvent devenir instables.

    Grizzl-E 5.x (certains firmwares) : True.
    """

    no_connection_timeout_config: bool = False
    """
    True → ne pas envoyer ChangeConfiguration(ConnectionTimeOut).
    Certaines bornes rejettent cette clé et logguent une erreur non-fatale.
    """

    # ── Robustesse JSON ───────────────────────────────────────────────────────
    lenient_json_parsing: bool = False
    """
    True → tolérer les réponses JSON malformées (ignorer au lieu de crasher).

    Grizzl-E 5.x : True — répond parfois avec du JSON invalide à GetConfiguration,
    ChangeConfiguration, SendLocalList. La lib ocpp lève une exception ; on la catch
    et on continue plutôt que de déconnecter.
    """

    # ── Heartbeat / Keepalive ─────────────────────────────────────────────────
    heartbeat_interval: int = 30
    """Intervalle Heartbeat demandé à la borne (secondes)."""

    # ── Profil de charge : valeurs qui peuvent être poussées après un reboot ─
    current_reverts_after_reboot: bool = False
    """
    True → la borne oublie les profils de charge après un reboot et revient
    aux valeurs hardware (ex: DIP switch sur Grizzl-E).
    On doit re-pousser le profil à chaque BootNotification.

    Grizzl-E : True — Maximum Current revient au DIP switch après chaque reboot.
    """

    # ── Comportements spéciaux ────────────────────────────────────────────────
    local_auth_id_tag: str = "ADMIN"
    """idTag utilisé pour RemoteStart et SendLocalList."""

    send_remote_start_after_boot: bool = False
    """
    True → si un véhicule est présent au boot et boot_lock=False,
    tenter un RemoteStart automatique (après SendLocalList).

    TechnoVE : True — peut rebooter et se reconnecter en Preparing sans StartTransaction.
    Grizzl-E 3.x : True — même comportement possible.
    """


# ─────────────────────────────────────────────────────────────────────────────
# Profils prédéfinis par fabricant / firmware
# ─────────────────────────────────────────────────────────────────────────────

#: Profil standard OCPP — toutes bornes non reconnues
PROFILE_GENERIC = ChargerProfile(
    name="Generic",
)

#: TechnoVE — bornes résidentielles canadiennes
PROFILE_TECHNOVE = ChargerProfile(
    name                        = "TechnoVE",
    use_tx_default_profile      = True,
    profile_connector_id        = 1,
    profile_stack_level         = 0,
    no_profile_in_remote_start  = True,
    defer_profile_if_occupied   = True,
    skip_availability_when_occupied = True,
    remote_start_delay          = 3.0,
    requires_local_list         = True,
    meter_values_require_transaction = False,  # supporte Cst_MeterValuesInTxOnly=false
    skip_measurand_detection    = False,
    lenient_json_parsing        = False,
    current_reverts_after_reboot = False,
    send_remote_start_after_boot = True,
)

#: Grizzl-E (United Chargers / ChargeLab) — firmware 3.x
#: Référence : lbbrhzn/ocpp issues, SteVe compatibility wiki
PROFILE_GRIZZLE_V3 = ChargerProfile(
    name                        = "Grizzl-E (fw 3.x)",
    use_tx_default_profile      = True,   # ChargePointMaxProfile accepté mais IGNORÉ
    profile_connector_id        = 1,
    profile_stack_level         = 0,
    no_profile_in_remote_start  = True,
    defer_profile_if_occupied   = True,
    skip_availability_when_occupied = True,
    remote_start_delay          = 2.0,
    requires_local_list         = True,
    meter_values_require_transaction = True,  # MeterValues uniquement en session
    skip_measurand_detection    = False,
    skip_meter_config           = False,
    lenient_json_parsing        = False,
    current_reverts_after_reboot = True,   # DIP switch overrides après reboot
    send_remote_start_after_boot = True,
    fixed_measurands            = [
        "Energy.Active.Import.Register",
        "Power.Active.Import",
        "Current.Import",
        "Voltage",
    ],
)

#: Grizzl-E (United Chargers / ChargeLab) — firmware 5.x
#: Référence : stefanthoss/ocpp-grizzl-e user-guide.md, lbbrhzn/ocpp issue #442
#: Bugs confirmés : JSON invalide sur GetConfiguration/ChangeConfiguration,
#: ChargePointMaxProfile ignoré, MeterValuesSampledData NotSupported
PROFILE_GRIZZLE_V5 = ChargerProfile(
    name                        = "Grizzl-E (fw 5.x)",
    use_tx_default_profile      = True,
    profile_connector_id        = 1,
    profile_stack_level         = 0,
    no_profile_in_remote_start  = True,
    defer_profile_if_occupied   = True,
    skip_availability_when_occupied = True,
    remote_start_delay          = 2.0,
    requires_local_list         = False,   # Gestion différente en fw 5.x
    meter_values_require_transaction = True,
    skip_measurand_detection    = True,    # GetConfiguration → JSON invalide → reboot
    skip_meter_config           = True,    # MeterValuesSampledData → NotSupported
    lenient_json_parsing        = True,    # Répond parfois avec JSON invalide
    current_reverts_after_reboot = True,
    send_remote_start_after_boot = True,
    fixed_measurands            = [
        "Energy.Active.Import.Register",
        "Power.Active.Import",
        "Current.Import",
    ],
)

#: Grizzl-E — firmware GWM (nouveau format, 2023+)
#: Connecte maintenant par défaut à ChargeLab mais supporte URL custom
PROFILE_GRIZZLE_GWM = ChargerProfile(
    name                        = "Grizzl-E (fw GWM)",
    use_tx_default_profile      = True,
    profile_connector_id        = 1,
    profile_stack_level         = 0,
    no_profile_in_remote_start  = True,
    defer_profile_if_occupied   = True,
    skip_availability_when_occupied = True,
    remote_start_delay          = 2.0,
    requires_local_list         = False,
    meter_values_require_transaction = True,
    skip_measurand_detection    = True,
    skip_meter_config           = True,
    lenient_json_parsing        = True,
    current_reverts_after_reboot = True,
    send_remote_start_after_boot = False,  # Comportement encore incertain
    fixed_measurands            = [
        "Energy.Active.Import.Register",
        "Power.Active.Import",
        "Current.Import",
    ],
)

#: ABB Terra AC — firmware <= 1.8.21
#: Bug confirmé : répond comme si tous les measurands sont supportés,
#: puis reboot lors du GetConfiguration de confirmation. Boucle fatale.
#: Référence : home-assistant-ocpp documentation
PROFILE_ABB_TERRA = ChargerProfile(
    name                        = "ABB Terra AC",
    use_tx_default_profile      = False,  # ChargePointMaxProfile fonctionnel
    profile_connector_id        = 0,
    profile_stack_level         = 8,
    no_profile_in_remote_start  = False,
    defer_profile_if_occupied   = False,
    skip_availability_when_occupied = False,
    remote_start_delay          = 1.0,
    requires_local_list         = False,
    meter_values_require_transaction = False,
    skip_measurand_detection    = True,   # GetConfiguration → reboot si trop de measurands
    skip_meter_config           = False,
    lenient_json_parsing        = False,
    current_reverts_after_reboot = False,
    send_remote_start_after_boot = False,
    fixed_measurands            = [       # Liste safe documentée par ABB fw 1.6.6
        "Energy.Active.Import.Register",
        "Power.Active.Import",
        "Current.Import",
        "Voltage",
        "Temperature",
    ],
)

#: Wallbox Pulsar Plus / Copper SB
#: Laisser le champ password vide dans la config OCPP de la borne.
PROFILE_WALLBOX = ChargerProfile(
    name                        = "Wallbox",
    use_tx_default_profile      = False,
    profile_connector_id        = 0,
    profile_stack_level         = 8,
    no_profile_in_remote_start  = False,
    defer_profile_if_occupied   = False,
    skip_availability_when_occupied = False,
    remote_start_delay          = 1.0,
    requires_local_list         = False,
    meter_values_require_transaction = False,
    skip_measurand_detection    = False,
    lenient_json_parsing        = False,
    current_reverts_after_reboot = False,
    send_remote_start_after_boot = False,
)

#: Alfen ICU Eve Mini — OCPP 1.6J
PROFILE_ALFEN = ChargerProfile(
    name                        = "Alfen",
    use_tx_default_profile      = False,
    profile_connector_id        = 0,
    profile_stack_level         = 8,
    no_profile_in_remote_start  = False,
    defer_profile_if_occupied   = False,
    skip_availability_when_occupied = False,
    remote_start_delay          = 1.5,
    requires_local_list         = False,
    meter_values_require_transaction = False,
    skip_measurand_detection    = False,
    lenient_json_parsing        = False,
    current_reverts_after_reboot = False,
    send_remote_start_after_boot = False,
)


# ─────────────────────────────────────────────────────────────────────────────
# Moteur de détection
# ─────────────────────────────────────────────────────────────────────────────

def detect_profile(
    vendor: str,
    model: str,
    firmware: Optional[str] = None,
) -> ChargerProfile:
    """
    Retourne le profil de compatibilité approprié selon le fabricant, le modèle
    et (si disponible) la version du firmware.

    La détection se fait par correspondance de chaînes sur vendor + model.
    Elle est insensible à la casse et tolère les espaces.

    Appeler cette fonction UNE SEULE FOIS au BootNotification et stocker
    le résultat dans cp.profile.

    Args:
        vendor:   chargePointVendor du BootNotification
        model:    chargePointModel du BootNotification
        firmware: firmwareVersion du BootNotification (optionnel)

    Returns:
        ChargerProfile approprié (PROFILE_GENERIC si non reconnu)
    """
    v = (vendor or "").upper().strip()
    m = (model  or "").upper().strip()
    fw = (firmware or "").strip()

    # ── TechnoVE ─────────────────────────────────────────────────────────────
    if "TECHNOVE" in v or "TECHNOVE" in m:
        return PROFILE_TECHNOVE

    # ── Grizzl-E (United Chargers / ChargeLab) ───────────────────────────────
    # Le vendor change selon les firmwares :
    #   fw 3.x/5.x : "United Chargers" / model "GRS-*"
    #   fw GWM     : "United Chargers" / model "GWM-*"
    #   Parfois vendor = "ChargeLab" ou "Grizzl-E"
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
    )
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
        return PROFILE_GRIZZLE_V5

    # ── ABB Terra ─────────────────────────────────────────────────────────────
    if "ABB" in v and "TERRA" in m:
        # Bug measurand uniquement sur fw <= 1.8.21
        # Sur fw >= 1.9.x le bug est corrigé — on applique quand même le profil
        # safe par précaution (skip_measurand_detection ne nuit pas aux bonnes bornes)
        return PROFILE_ABB_TERRA

    # ── Wallbox ───────────────────────────────────────────────────────────────
    if "WALLBOX" in v or "COPPER" in m or "PULSAR" in m or "COMMANDER" in m:
        return PROFILE_WALLBOX

    # ── Alfen ─────────────────────────────────────────────────────────────────
    if "ALFEN" in v or "ICU" in m or "NG910" in m:
        return PROFILE_ALFEN

    # ── Mennekes ──────────────────────────────────────────────────────────────
    # Fully working avec SteVe sans quirks particuliers
    if "MENNEKES" in v:
        return PROFILE_GENERIC

    # ── Delta Electronics ─────────────────────────────────────────────────────
    if "DELTA" in v:
        return PROFILE_GENERIC

    # ── EVBox ─────────────────────────────────────────────────────────────────
    if "EVBOX" in v or "ELVI" in m:
        return PROFILE_GENERIC

    # ── Keba ─────────────────────────────────────────────────────────────────
    if "KEBA" in v or "KECONTACT" in v:
        return PROFILE_GENERIC

    # ── Zaptec ───────────────────────────────────────────────────────────────
    if "ZAPTEC" in v:
        return PROFILE_GENERIC

    # ── Go-e ─────────────────────────────────────────────────────────────────
    if "GO-E" in v or "GOE" in v:
        return PROFILE_GENERIC

    # ── Compleo ───────────────────────────────────────────────────────────────
    if "COMPLEO" in v or "EBOX" in m:
        return PROFILE_GENERIC

    # ── Défaut ────────────────────────────────────────────────────────────────
    return PROFILE_GENERIC


# ─────────────────────────────────────────────────────────────────────────────
# Helpers utilitaires
# ─────────────────────────────────────────────────────────────────────────────

def profile_summary(profile: ChargerProfile) -> dict:
    """Retourne un résumé du profil pour les logs et le dashboard."""
    return {
        "profile_name":                  profile.name,
        "use_tx_default_profile":        profile.use_tx_default_profile,
        "no_profile_in_remote_start":    profile.no_profile_in_remote_start,
        "skip_availability_when_occupied": profile.skip_availability_when_occupied,
        "defer_profile_if_occupied":     profile.defer_profile_if_occupied,
        "requires_local_list":           profile.requires_local_list,
        "meter_values_require_transaction": profile.meter_values_require_transaction,
        "skip_measurand_detection":      profile.skip_measurand_detection,
        "skip_meter_config":             profile.skip_meter_config,
        "lenient_json_parsing":          profile.lenient_json_parsing,
        "current_reverts_after_reboot":  profile.current_reverts_after_reboot,
        "remote_start_delay":            profile.remote_start_delay,
    }
