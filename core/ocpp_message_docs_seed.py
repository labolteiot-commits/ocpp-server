# core/ocpp_message_docs_seed.py
"""
Sprint 32 — Documentation FR de tous les messages OCPP 1.6J Edition 2 (juillet 2017).

Seedé au boot du serveur dans la table `ocpp_message_docs`. Aucun scraping live
de la norme : OCPP 1.6 est figée depuis 2017, donc le contenu statique suffit.
Pour OCPP 2.0.1 (futur), prévoir un seed séparé `ocpp_message_docs_seed_v201.py`.

Sources :
  * OCPP 1.6 Edition 2 specification (Open Charge Alliance, 2017-07-21)
  * SteVe community wiki (interprétations terrain)
  * Lib MobilityHouse `ocpp` (signatures CALL/CALLRESULT)

Convention :
  * direction_norm : 'CP→CSMS' (borne → serveur), 'CSMS→CP' (serveur → borne),
                     ou 'both' si défini dans les deux sens (DataTransfer)
  * profile : Core, FirmwareManagement, LocalAuthListManagement, Reservation,
              SmartCharging, RemoteTrigger
  * section_norm : '§X.Y' tel que dans la spec OCA
"""
from __future__ import annotations

OCPP_MESSAGE_DOCS: list[dict] = [
    # ─────────────────────────────────── Core Profile ──────────────────────
    {
        "name": "BootNotification",
        "direction_norm": "CP→CSMS",
        "profile": "Core",
        "section_norm": "§4.2",
        "summary_fr": "Annonce d'une borne au serveur lors de son démarrage.",
        "description_fr": (
            "Premier message envoyé par la borne après ouverture du WebSocket. "
            "Contient l'identité matérielle (vendor, model, serial, firmware). "
            "Le serveur répond avec un statut (Accepted/Pending/Rejected) et "
            "un intervalle de heartbeat. Tant que le statut n'est pas Accepted, "
            "la borne ne doit envoyer aucun autre message OCPP (sauf BootNotification)."
        ),
        "fields_fr": {
            "chargePointVendor": "Nom du fabricant (ex: TechnoVE, Grizzl-E).",
            "chargePointModel": "Modèle commercial.",
            "chargePointSerialNumber": "Numéro de série matériel (optionnel).",
            "firmwareVersion": "Version du firmware actuellement installé.",
            "iccid": "ICCID de la carte SIM (bornes cellulaires).",
            "imsi": "IMSI de la carte SIM.",
            "meterType": "Modèle du compteur d'énergie intégré.",
            "meterSerialNumber": "Numéro de série du compteur.",
            "status": "Réponse serveur : Accepted/Pending/Rejected.",
            "currentTime": "Heure courante UTC du serveur (la borne peut s'y synchroniser).",
            "interval": "Intervalle (s) entre 2 Heartbeats que la borne doit respecter.",
        },
        "triggered_by": "Démarrage borne (cold boot, après reset, après long downtime WiFi).",
        "cs_response": "BootNotificationResponse {status, currentTime, interval}.",
    },
    {
        "name": "Heartbeat",
        "direction_norm": "CP→CSMS",
        "profile": "Core",
        "section_norm": "§4.6",
        "summary_fr": "Battement de cœur périodique borne → serveur.",
        "description_fr": (
            "Envoyé à intervalle régulier (typique 30-300 s, négocié au BootNotification) "
            "pour maintenir la liaison WebSocket et synchroniser l'horloge. La réponse "
            "du serveur contient son heure UTC, que la borne peut utiliser pour "
            "ajuster son RTC interne."
        ),
        "fields_fr": {
            "currentTime": "Heure UTC du serveur (réponse).",
        },
        "triggered_by": "Timer interne borne (intervalle BootNotification.interval).",
        "cs_response": "HeartbeatResponse {currentTime}.",
    },
    {
        "name": "StatusNotification",
        "direction_norm": "CP→CSMS",
        "profile": "Core",
        "section_norm": "§4.8",
        "summary_fr": "Changement d'état d'un connecteur ou de la borne entière.",
        "description_fr": (
            "Émis dès qu'un connecteur change d'état (Available → Preparing → Charging → "
            "Finishing → Available, ou vers Faulted). connectorId=0 désigne la borne "
            "entière (panne globale). Les codes d'erreur sont normalisés (NoError, "
            "ConnectorLockFailure, GroundFailure, HighTemperature, OverCurrentFailure, "
            "etc.). Historisé en DB dans `status_notifications`."
        ),
        "fields_fr": {
            "connectorId": "0 = borne entière, ≥1 = connecteur individuel.",
            "errorCode": "NoError ou code de panne normalisé OCPP.",
            "status": "Available/Preparing/Charging/SuspendedEV/SuspendedEVSE/Finishing/Reserved/Unavailable/Faulted.",
            "info": "Texte libre fabricant (max 50 char).",
            "timestamp": "Heure de changement d'état côté borne.",
            "vendorId": "Identifiant fabricant (extension custom).",
            "vendorErrorCode": "Code d'erreur propriétaire fabricant.",
        },
        "triggered_by": "Transition d'état connecteur (plug, unplug, démarrage charge, fault).",
        "cs_response": "StatusNotificationResponse (vide).",
    },
    {
        "name": "Authorize",
        "direction_norm": "CP→CSMS",
        "profile": "Core",
        "section_norm": "§4.1",
        "summary_fr": "Validation d'une carte RFID / id_tag avant démarrage de session.",
        "description_fr": (
            "Avant qu'une transaction ne démarre, la borne demande au serveur si "
            "le tag RFID présenté est autorisé. Le serveur répond avec un IdTagInfo "
            "contenant le statut (Accepted/Blocked/Expired/Invalid/ConcurrentTx) "
            "et optionnellement une expiration et un parentIdTag. Si une "
            "LocalAuthList est synchronisée, la borne peut court-circuiter cet appel."
        ),
        "fields_fr": {
            "idTag": "Identifiant RFID (max 20 char).",
            "idTagInfo.status": "Accepted/Blocked/Expired/Invalid/ConcurrentTx.",
            "idTagInfo.expiryDate": "Expiration optionnelle du tag.",
            "idTagInfo.parentIdTag": "Tag parent (groupe RFID).",
        },
        "triggered_by": "Présentation carte RFID, code PIN, ou scan QR sur la borne.",
        "cs_response": "AuthorizeResponse {idTagInfo}.",
    },
    {
        "name": "StartTransaction",
        "direction_norm": "CP→CSMS",
        "profile": "Core",
        "section_norm": "§4.10",
        "summary_fr": "Démarrage d'une session de charge (transaction).",
        "description_fr": (
            "Émis par la borne quand la charge démarre effectivement (contacteur "
            "fermé, énergie qui commence à couler). Le serveur attribue un "
            "transactionId unique que la borne utilisera dans tous les MeterValues "
            "et le StopTransaction. Si idTagInfo.status ≠ Accepted, la borne doit "
            "interrompre la transaction immédiatement (transactionId=0)."
        ),
        "fields_fr": {
            "connectorId": "Connecteur sur lequel la transaction démarre (≥1).",
            "idTag": "Tag RFID ayant autorisé la session.",
            "meterStart": "Compteur Wh au démarrage.",
            "reservationId": "Si la session démarre via une réservation, son ID.",
            "timestamp": "Heure de démarrage côté borne.",
            "transactionId": "ID unique attribué par le serveur (réponse).",
            "idTagInfo": "Validation du tag (peut différer de Authorize).",
        },
        "triggered_by": "Contacteur fermé après autorisation, début de l'écoulement d'énergie.",
        "cs_response": "StartTransactionResponse {transactionId, idTagInfo}.",
    },
    {
        "name": "StopTransaction",
        "direction_norm": "CP→CSMS",
        "profile": "Core",
        "section_norm": "§4.11",
        "summary_fr": "Fin d'une session de charge.",
        "description_fr": (
            "Émis quand la charge se termine (utilisateur débranche, RemoteStop, "
            "EV plein, fault). Contient le compteur final, la raison d'arrêt, et "
            "optionnellement les MeterValues collectés pendant la session "
            "(transactionData). Le transactionId réfère au StartTransaction."
        ),
        "fields_fr": {
            "transactionId": "ID de la transaction qui se termine.",
            "idTag": "Tag ayant autorisé l'arrêt (si différent du StartTx).",
            "meterStop": "Compteur Wh à la fin.",
            "timestamp": "Heure de fin côté borne.",
            "reason": "EmergencyStop/EVDisconnected/HardReset/Local/Other/PowerLoss/Reboot/Remote/SoftReset/UnlockCommand/DeAuthorized.",
            "transactionData": "Liste de MeterValues collectés (optionnel, recommandé).",
            "idTagInfo": "Validation tag (réponse, indique si idTag accepté).",
        },
        "triggered_by": "Débranchement, RemoteStop, fin de charge, fault.",
        "cs_response": "StopTransactionResponse {idTagInfo}.",
    },
    {
        "name": "MeterValues",
        "direction_norm": "CP→CSMS",
        "profile": "Core",
        "section_norm": "§4.7",
        "summary_fr": "Mesures périodiques (énergie, puissance, tension, courant, SoC, T°).",
        "description_fr": (
            "Cadence configurable via MeterValueSampleInterval. Chaque MeterValue "
            "contient un timestamp et N sampledValue, chacun avec measurand, value, "
            "unit, phase, location, context. Les unités OCPP 1.6 sont normalisées "
            "(Wh, W, A, V, Celsius, Kelvin, Fahrenheit, Percent) — le serveur "
            "convertit en unités de base (W, Wh, °C) à l'ingest."
        ),
        "fields_fr": {
            "connectorId": "Connecteur concerné (≥1) ou 0 pour borne entière.",
            "transactionId": "Si lié à une transaction active.",
            "meterValue": "Liste de mesures, chacune avec timestamp + sampledValue[].",
            "sampledValue.measurand": "Energy.Active.Import.Register / Power.Active.Import / SoC / Voltage / Current.Import / Temperature / Frequency / etc.",
            "sampledValue.unit": "Wh/kWh/W/kW/A/V/Celsius/Kelvin/Fahrenheit/Percent/Hertz.",
            "sampledValue.phase": "L1/L2/L3 ou L1-N/L2-L3 pour triphasé.",
            "sampledValue.location": "Inlet/Outlet/Body/Cable/EV.",
            "sampledValue.context": "Sample.Periodic/Sample.Clock/Transaction.Begin/Transaction.End/Trigger/Other.",
        },
        "triggered_by": "Timer MeterValueSampleInterval (config), démarrage/fin de transaction (Transaction.Begin/End), TriggerMessage.",
        "cs_response": "MeterValuesResponse (vide).",
    },
    {
        "name": "RemoteStartTransaction",
        "direction_norm": "CSMS→CP",
        "profile": "Core",
        "section_norm": "§5.13",
        "summary_fr": "Demande serveur → borne de démarrer une session.",
        "description_fr": (
            "Permet à un usager de démarrer la charge depuis une appli mobile sans "
            "présenter de carte RFID physique. La borne répond Accepted si elle "
            "peut tenter le démarrage (mais le StartTransaction réel viendra plus "
            "tard, après autorisation locale et fermeture du contacteur). "
            "Optionnellement embarque un chargingProfile à appliquer dès le start."
        ),
        "fields_fr": {
            "idTag": "Tag à utiliser pour la session (devra être autorisé).",
            "connectorId": "Connecteur cible (optionnel, sinon n'importe lequel libre).",
            "chargingProfile": "Profil de courant à appliquer (TxProfile, optionnel).",
            "status": "Accepted/Rejected (réponse).",
        },
        "triggered_by": "API serveur POST /commands/remote-start.",
        "cs_response": "RemoteStartTransactionResponse {status}.",
    },
    {
        "name": "RemoteStopTransaction",
        "direction_norm": "CSMS→CP",
        "profile": "Core",
        "section_norm": "§5.14",
        "summary_fr": "Demande serveur → borne d'arrêter une session.",
        "description_fr": (
            "Coupe une transaction active à distance. La borne répond Accepted "
            "puis envoie un StopTransaction normal (avec reason='Remote'). Si la "
            "transactionId est inconnue, retourne Rejected."
        ),
        "fields_fr": {
            "transactionId": "ID de la transaction à arrêter.",
            "status": "Accepted/Rejected (réponse).",
        },
        "triggered_by": "API serveur POST /commands/remote-stop.",
        "cs_response": "RemoteStopTransactionResponse {status}.",
    },
    {
        "name": "Reset",
        "direction_norm": "CSMS→CP",
        "profile": "Core",
        "section_norm": "§5.15",
        "summary_fr": "Redémarrage de la borne (Soft ou Hard).",
        "description_fr": (
            "Soft = redémarrage propre du software borne (sessions actives "
            "interrompues proprement avec StopTransaction). Hard = power-cycle "
            "matériel équivalent à débrancher l'alim (toute transaction active "
            "se termine en StopTransaction reason='HardReset' au boot suivant)."
        ),
        "fields_fr": {
            "type": "Soft ou Hard.",
            "status": "Accepted/Rejected (réponse).",
        },
        "triggered_by": "API serveur POST /commands/reset, ou maintenance opérateur.",
        "cs_response": "ResetResponse {status}.",
    },
    {
        "name": "ChangeAvailability",
        "direction_norm": "CSMS→CP",
        "profile": "Core",
        "section_norm": "§5.2",
        "summary_fr": "Mise hors service / remise en service d'un connecteur.",
        "description_fr": (
            "type='Inoperative' désactive le connecteur (ne peut plus accepter de "
            "session). type='Operative' le réactive. connectorId=0 cible la borne "
            "entière. Si une transaction est en cours, la borne peut répondre "
            "Scheduled (changement appliqué à la fin de la transaction) au lieu "
            "d'Accepted (immédiat)."
        ),
        "fields_fr": {
            "connectorId": "0 = borne entière, ≥1 = connecteur spécifique.",
            "type": "Inoperative ou Operative.",
            "status": "Accepted/Rejected/Scheduled (réponse).",
        },
        "triggered_by": "API serveur POST /commands/change-availability.",
        "cs_response": "ChangeAvailabilityResponse {status}.",
    },
    {
        "name": "ChangeConfiguration",
        "direction_norm": "CSMS→CP",
        "profile": "Core",
        "section_norm": "§5.3",
        "summary_fr": "Modification d'une clé de configuration borne.",
        "description_fr": (
            "Modifie une variable runtime de la borne (HeartbeatInterval, "
            "MeterValueSampleInterval, MeterValuesSampledData, etc.). Réponse "
            "Accepted (effet immédiat), RebootRequired (effet au prochain reboot), "
            "NotSupported (clé inconnue), ou Rejected (valeur invalide)."
        ),
        "fields_fr": {
            "key": "Nom de la clé (voir StandardConfigurationKey ou clé propriétaire).",
            "value": "Nouvelle valeur (string, à parser selon le type de la clé).",
            "status": "Accepted/Rejected/RebootRequired/NotSupported (réponse).",
        },
        "triggered_by": "API serveur POST /commands/change-config, anti-override drift loop.",
        "cs_response": "ChangeConfigurationResponse {status}.",
    },
    {
        "name": "GetConfiguration",
        "direction_norm": "CSMS→CP",
        "profile": "Core",
        "section_norm": "§5.8",
        "summary_fr": "Lecture des clés de configuration borne.",
        "description_fr": (
            "Sans paramètre `key` : retourne TOUTES les clés. Avec `key` : "
            "retourne uniquement les clés demandées. La borne distingue "
            "configurationKey (clés connues) et unknownKey (clés inconnues). "
            "Chaque clé indique si elle est readonly."
        ),
        "fields_fr": {
            "key": "Liste optionnelle de clés à lire.",
            "configurationKey": "Liste {key, value, readonly} des clés trouvées.",
            "unknownKey": "Liste des clés demandées mais inconnues de la borne.",
        },
        "triggered_by": "API serveur GET /commands/config, snapshot anti-override, audit.",
        "cs_response": "GetConfigurationResponse {configurationKey, unknownKey}.",
    },
    {
        "name": "ClearCache",
        "direction_norm": "CSMS→CP",
        "profile": "Core",
        "section_norm": "§5.5",
        "summary_fr": "Vidage du cache d'autorisation local de la borne.",
        "description_fr": (
            "Force la borne à oublier ses entrées AuthorizationCache (cache LRU "
            "des derniers idTags Authorize Accepted, utilisé en mode offline). "
            "N'affecte pas la LocalAuthList (synchronisée explicitement)."
        ),
        "fields_fr": {
            "status": "Accepted/Rejected (réponse).",
        },
        "triggered_by": "API serveur POST /commands/clear-cache, opération maintenance.",
        "cs_response": "ClearCacheResponse {status}.",
    },
    {
        "name": "UnlockConnector",
        "direction_norm": "CSMS→CP",
        "profile": "Core",
        "section_norm": "§5.18",
        "summary_fr": "Déverrouillage forcé du câble de charge.",
        "description_fr": (
            "Force la borne à libérer le verrouillage électromécanique du "
            "connecteur. Utile si l'EV est resté coincé après une fault ou si "
            "l'utilisateur a oublié de déconnecter via l'EV. La borne répond "
            "Unlocked, UnlockFailed, ou NotSupported (Type 1 sans verrou)."
        ),
        "fields_fr": {
            "connectorId": "Connecteur à déverrouiller (≥1).",
            "status": "Unlocked/UnlockFailed/NotSupported (réponse).",
        },
        "triggered_by": "API serveur POST /commands/unlock-connector.",
        "cs_response": "UnlockConnectorResponse {status}.",
    },
    {
        "name": "DataTransfer",
        "direction_norm": "both",
        "profile": "Core",
        "section_norm": "§6.6",
        "summary_fr": "Échange de données vendor-specific (extension propriétaire).",
        "description_fr": (
            "Mécanisme d'extension OCPP : permet à un fabricant de transmettre "
            "des données custom non couvertes par la norme. Identifié par "
            "vendorId (obligatoire) + messageId (optionnel) + data (string libre). "
            "Le destinataire répond Accepted/Rejected/UnknownMessageId/UnknownVendorId. "
            "Persisté en DB dans `data_transfer_logs` pour audit."
        ),
        "fields_fr": {
            "vendorId": "Identifiant unique du fabricant (reverse-DNS recommandé).",
            "messageId": "Identifiant optionnel du type de message custom.",
            "data": "Payload libre (typiquement JSON sérialisé en string).",
            "status": "Accepted/Rejected/UnknownMessageId/UnknownVendorId (réponse).",
            "data (response)": "Payload optionnel de réponse.",
        },
        "triggered_by": "Logique fabricant (côté borne) ou API serveur POST /commands/data-transfer/send.",
        "cs_response": "DataTransferResponse {status, data}.",
    },

    # ───────────────────────────── FirmwareManagement Profile ──────────────
    {
        "name": "GetDiagnostics",
        "direction_norm": "CSMS→CP",
        "profile": "FirmwareManagement",
        "section_norm": "§6.10",
        "summary_fr": "Demande à la borne d'uploader ses logs vers une URL fournie.",
        "description_fr": (
            "Le serveur fournit une URL FTP/HTTP où la borne doit uploader son "
            "fichier de diagnostic. La borne répond avec le nom du fichier qu'elle "
            "va uploader (peut être vide si rien à envoyer), puis envoie des "
            "DiagnosticsStatusNotification pour suivre la progression."
        ),
        "fields_fr": {
            "location": "URL FTP/HTTP cible pour l'upload.",
            "retries": "Nombre de retries en cas d'échec.",
            "retryInterval": "Délai (s) entre retries.",
            "startTime": "Filtre logs depuis cette date.",
            "stopTime": "Filtre logs jusqu'à cette date.",
            "fileName": "Nom du fichier que la borne va uploader (réponse).",
        },
        "triggered_by": "API serveur POST /commands/get-diagnostics, demande support.",
        "cs_response": "GetDiagnosticsResponse {fileName}.",
    },
    {
        "name": "DiagnosticsStatusNotification",
        "direction_norm": "CP→CSMS",
        "profile": "FirmwareManagement",
        "section_norm": "§4.5",
        "summary_fr": "Suivi de progression d'un upload de diagnostic.",
        "description_fr": (
            "Émis par la borne pendant le traitement d'un GetDiagnostics. Indique "
            "Idle (rien à faire), Uploading (en cours), Uploaded (succès), "
            "UploadFailed (échec). Le serveur met à jour la ligne `diagnostics_requests`."
        ),
        "fields_fr": {
            "status": "Idle/Uploaded/UploadFailed/Uploading.",
        },
        "triggered_by": "Borne pendant traitement GetDiagnostics.",
        "cs_response": "DiagnosticsStatusNotificationResponse (vide).",
    },
    {
        "name": "UpdateFirmware",
        "direction_norm": "CSMS→CP",
        "profile": "FirmwareManagement",
        "section_norm": "§6.19",
        "summary_fr": "Demande à la borne de télécharger et installer un firmware.",
        "description_fr": (
            "Le serveur fournit une URL où la borne doit télécharger le binaire "
            "firmware. retrieveDate indique l'heure à partir de laquelle la borne "
            "doit commencer le téléchargement (permet de programmer une mise à "
            "jour hors heures d'usage). La borne répond avec un ACK vide puis "
            "envoie des FirmwareStatusNotification pour suivre la progression."
        ),
        "fields_fr": {
            "location": "URL HTTP du firmware.",
            "retrieveDate": "Heure UTC à partir de laquelle commencer le téléchargement.",
            "retries": "Nombre de retries de téléchargement.",
            "retryInterval": "Délai (s) entre retries.",
        },
        "triggered_by": "API serveur POST /commands/update-firmware.",
        "cs_response": "UpdateFirmwareResponse (vide — juste un ACK).",
    },
    {
        "name": "FirmwareStatusNotification",
        "direction_norm": "CP→CSMS",
        "profile": "FirmwareManagement",
        "section_norm": "§4.4",
        "summary_fr": "Suivi de progression d'une mise à jour firmware.",
        "description_fr": (
            "Cycle de vie : Idle → Downloading → Downloaded → Installing → "
            "Installed → (reboot) → BootNotification avec nouvelle version. "
            "En cas d'échec : DownloadFailed ou InstallationFailed. Le serveur "
            "met à jour la ligne `firmware_updates` correspondante."
        ),
        "fields_fr": {
            "status": "Idle/Downloaded/DownloadFailed/Downloading/InstallationFailed/Installed/Installing.",
        },
        "triggered_by": "Borne pendant traitement UpdateFirmware.",
        "cs_response": "FirmwareStatusNotificationResponse (vide).",
    },

    # ──────────────────────────── LocalAuthListManagement Profile ──────────
    {
        "name": "GetLocalListVersion",
        "direction_norm": "CSMS→CP",
        "profile": "LocalAuthListManagement",
        "section_norm": "§6.11",
        "summary_fr": "Lecture du numéro de version actuel de la LocalAuthList.",
        "description_fr": (
            "Le serveur peut interroger la borne pour vérifier qu'elle est à "
            "jour avant un SendLocalList Differential. Si la borne ne supporte "
            "pas LocalAuthListManagement, retourne -1. Version 0 = liste vide."
        ),
        "fields_fr": {
            "listVersion": "Version actuelle de la liste sur la borne (réponse).",
        },
        "triggered_by": "API serveur GET /commands/local-list/version, audit sync.",
        "cs_response": "GetLocalListVersionResponse {listVersion}.",
    },
    {
        "name": "SendLocalList",
        "direction_norm": "CSMS→CP",
        "profile": "LocalAuthListManagement",
        "section_norm": "§6.20",
        "summary_fr": "Mise à jour de la LocalAuthList (Full ou Differential).",
        "description_fr": (
            "updateType=Full : remplace entièrement la liste. updateType=Differential : "
            "applique seulement les ajouts/modifications/suppressions listées. "
            "listVersion doit être > version actuelle. Permet aux bornes de fonctionner "
            "offline (autorisation locale sans Authorize)."
        ),
        "fields_fr": {
            "listVersion": "Nouvelle version de la liste.",
            "updateType": "Full (remplacement complet) ou Differential (delta).",
            "localAuthorizationList": "Liste de {idTag, idTagInfo}.",
            "status": "Accepted/Failed/NotSupported/VersionMismatch (réponse).",
        },
        "triggered_by": "API serveur POST /commands/local-list/send, auto-sync sur CRUD ocpp_tags.",
        "cs_response": "SendLocalListResponse {status}.",
    },

    # ──────────────────────────────── Reservation Profile ──────────────────
    {
        "name": "ReserveNow",
        "direction_norm": "CSMS→CP",
        "profile": "Reservation",
        "section_norm": "§6.16",
        "summary_fr": "Réservation d'un connecteur pour un usager identifié.",
        "description_fr": (
            "Bloque un connecteur jusqu'à expiryDate pour un idTag spécifique "
            "(seul ce tag peut démarrer la session). connectorId=0 = n'importe "
            "quel connecteur disponible. Réponses : Accepted, Faulted (panne), "
            "Occupied (déjà en charge), Rejected (refus borne), "
            "Unavailable (Inoperative)."
        ),
        "fields_fr": {
            "connectorId": "0 (any) ou ≥1 (spécifique).",
            "expiryDate": "Heure UTC d'expiration de la réservation.",
            "idTag": "Tag autorisé à démarrer la session.",
            "parentIdTag": "Tag parent (groupe).",
            "reservationId": "ID unique de la réservation (généré serveur).",
            "status": "Accepted/Faulted/Occupied/Rejected/Unavailable (réponse).",
        },
        "triggered_by": "API serveur POST /commands/reserve.",
        "cs_response": "ReserveNowResponse {status}.",
    },
    {
        "name": "CancelReservation",
        "direction_norm": "CSMS→CP",
        "profile": "Reservation",
        "section_norm": "§6.4",
        "summary_fr": "Annulation d'une réservation existante.",
        "description_fr": (
            "Libère un connecteur réservé. Réponses : Accepted (OK) ou Rejected "
            "(reservationId inconnue ou déjà expirée). La borne envoie ensuite un "
            "StatusNotification pour confirmer le retour à Available."
        ),
        "fields_fr": {
            "reservationId": "ID de la réservation à annuler.",
            "status": "Accepted/Rejected (réponse).",
        },
        "triggered_by": "API serveur POST /commands/cancel-reserve.",
        "cs_response": "CancelReservationResponse {status}.",
    },

    # ─────────────────────────────── SmartCharging Profile ─────────────────
    {
        "name": "SetChargingProfile",
        "direction_norm": "CSMS→CP",
        "profile": "SmartCharging",
        "section_norm": "§6.18",
        "summary_fr": "Application d'un profil de courant/puissance limite.",
        "description_fr": (
            "Limite la puissance/courant maximal qu'une transaction peut tirer. "
            "3 niveaux : ChargePointMaxProfile (limite globale borne), "
            "TxDefaultProfile (défaut nouvelle session), TxProfile (session "
            "spécifique en cours). chargingSchedule peut contenir plusieurs "
            "périodes (rampe horaire). Le snapshot est persisté en DB pour "
            "replay au reboot (A9)."
        ),
        "fields_fr": {
            "connectorId": "0 (borne) ou ≥1 (connecteur).",
            "csChargingProfiles.chargingProfileId": "ID unique du profil.",
            "csChargingProfiles.stackLevel": "Priorité (plus haut = override).",
            "csChargingProfiles.chargingProfilePurpose": "ChargePointMaxProfile/TxDefaultProfile/TxProfile.",
            "csChargingProfiles.chargingProfileKind": "Absolute/Recurring/Relative.",
            "csChargingProfiles.recurrencyKind": "Daily/Weekly (si Recurring).",
            "csChargingProfiles.validFrom": "Début de validité.",
            "csChargingProfiles.validTo": "Fin de validité.",
            "csChargingProfiles.transactionId": "Si TxProfile : ID de la session ciblée.",
            "csChargingProfiles.chargingSchedule.duration": "Durée en s du schedule.",
            "csChargingProfiles.chargingSchedule.startSchedule": "Heure de début (Absolute/Recurring).",
            "csChargingProfiles.chargingSchedule.chargingRateUnit": "A (ampères) ou W (watts).",
            "csChargingProfiles.chargingSchedule.chargingSchedulePeriod[]": "Liste {startPeriod, limit, numberPhases}.",
            "csChargingProfiles.chargingSchedule.minChargingRate": "Limite basse optionnelle.",
            "status": "Accepted/Rejected/NotSupported (réponse).",
        },
        "triggered_by": "API serveur POST /commands/charging-profile, scheduler OCPP, anti-override re-apply.",
        "cs_response": "SetChargingProfileResponse {status}.",
    },
    {
        "name": "ClearChargingProfile",
        "direction_norm": "CSMS→CP",
        "profile": "SmartCharging",
        "section_norm": "§6.5",
        "summary_fr": "Suppression de profils de charge.",
        "description_fr": (
            "Filtres optionnels combinables (id, connectorId, purpose, stackLevel). "
            "Sans filtre = tous les profils effacés. Le serveur marque les "
            "snapshots correspondants comme expired_at=now (A8) pour conserver "
            "l'audit sans les replay au reboot."
        ),
        "fields_fr": {
            "id": "Filtre par chargingProfileId.",
            "connectorId": "Filtre par connecteur (0 = tous).",
            "chargingProfilePurpose": "Filtre par type de profil.",
            "stackLevel": "Filtre par niveau de pile.",
            "status": "Accepted/Unknown (réponse).",
        },
        "triggered_by": "API serveur POST /commands/charging-profile/clear.",
        "cs_response": "ClearChargingProfileResponse {status}.",
    },
    {
        "name": "GetCompositeSchedule",
        "direction_norm": "CSMS→CP",
        "profile": "SmartCharging",
        "section_norm": "§6.9",
        "summary_fr": "Lecture du planning composite effectif sur un connecteur.",
        "description_fr": (
            "Demande à la borne de calculer et retourner la limite effective "
            "résultant de la combinaison de tous les profils actifs (max, "
            "tx_default, tx) sur une fenêtre temporelle donnée. Utile pour "
            "vérifier ce que la borne va vraiment appliquer."
        ),
        "fields_fr": {
            "connectorId": "Connecteur cible.",
            "duration": "Durée (s) de la fenêtre.",
            "chargingRateUnit": "A ou W (unité de retour).",
            "status": "Accepted/Rejected (réponse).",
            "scheduleStart": "Début du schedule retourné.",
            "chargingSchedule": "Schedule composite calculé.",
        },
        "triggered_by": "API serveur GET /commands/composite-schedule.",
        "cs_response": "GetCompositeScheduleResponse {status, connectorId, scheduleStart, chargingSchedule}.",
    },

    # ─────────────────────────────── RemoteTrigger Profile ─────────────────
    {
        "name": "TriggerMessage",
        "direction_norm": "CSMS→CP",
        "profile": "RemoteTrigger",
        "section_norm": "§6.21",
        "summary_fr": "Demande à la borne d'émettre un message spontanément.",
        "description_fr": (
            "Force la borne à émettre un message qu'elle aurait normalement "
            "émis selon sa propre cadence. Messages supportés : "
            "BootNotification, DiagnosticsStatusNotification, "
            "FirmwareStatusNotification, Heartbeat, MeterValues, "
            "StatusNotification. Utile pour rafraîchir l'état serveur sans "
            "attendre le prochain timer borne."
        ),
        "fields_fr": {
            "requestedMessage": "Type de message à déclencher.",
            "connectorId": "Filtre optionnel par connecteur.",
            "status": "Accepted/Rejected/NotImplemented (réponse).",
        },
        "triggered_by": "API serveur POST /commands/trigger.",
        "cs_response": "TriggerMessageResponse {status}.",
    },
]


def expected_count() -> int:
    """Nombre de messages OCPP 1.6J documentés par ce seed."""
    return len(OCPP_MESSAGE_DOCS)
