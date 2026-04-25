// ═══════════════════════════════════════════════════════════════════
// OCPP Configuration Key Database
// Couvre : OCPP 1.6 standard + TechnoVE (clés numériques) + Grizzl-E
// ═══════════════════════════════════════════════════════════════════

const OCPP_CONFIG_DB = {

  // ─────────────────────────────────────────────────────────────────
  // CLÉS STANDARD OCPP 1.6 (nommées)
  // ─────────────────────────────────────────────────────────────────
  standard: {
    "AllowOfflineTxForUnknownId": {
      ocpp: "AllowOfflineTxForUnknownId",
      desc: "Autorise les transactions hors-ligne pour les idTag inconnus",
      type: "boolean",
      options: ["true","false"],
      note: "false recommandé pour la sécurité résidentielle"
    },
    "AuthorizationCacheEnabled": {
      ocpp: "AuthorizationCacheEnabled",
      desc: "Active le cache local des autorisations OCPP",
      type: "boolean",
      options: ["true","false"]
    },
    "AuthorizeRemoteTxRequests": {
      ocpp: "AuthorizeRemoteTxRequests",
      desc: "Exige une autorisation pour les RemoteStartTransaction",
      type: "boolean",
      options: ["true","false"]
    },
    "BlinkRepeat": {
      ocpp: "BlinkRepeat",
      desc: "Nombre de clignotements LED lors d'une notification",
      type: "integer", range: "1–10", example: "3"
    },
    "ChargeProfileMaxStackLevel": {
      ocpp: "ChargeProfileMaxStackLevel",
      desc: "Nombre maximum de profils de charge empilables (stack)",
      type: "integer", range: "1–99", example: "4"
    },
    "ChargingScheduleAllowedChargingRateUnit": {
      ocpp: "ChargingScheduleAllowedChargingRateUnit",
      desc: "Unités acceptées dans les planifications de charge",
      type: "enum",
      options: ["Current","Power","Current,Power"]
    },
    "ChargingScheduleMaxPeriods": {
      ocpp: "ChargingScheduleMaxPeriods",
      desc: "Nombre maximum de périodes dans un planning de charge",
      type: "integer", range: "1–1024", example: "24"
    },
    "ConnectorPhaseRotation": {
      ocpp: "ConnectorPhaseRotation",
      desc: "Rotation de phase du connecteur",
      type: "enum",
      options: ["NotApplicable","Unknown","RST","RTS","SRT","STR","TRS","TSR"]
    },
    "ConnectorPhaseRotationMaxLength": {
      ocpp: "ConnectorPhaseRotationMaxLength",
      desc: "Longueur maximale de la liste ConnectorPhaseRotation",
      type: "integer", range: "1–10", example: "4"
    },
    "ConnectorSwitch3to1PhaseSupported": {
      ocpp: "ConnectorSwitch3to1PhaseSupported",
      desc: "Supporte le passage dynamique triphasé → monophasé",
      type: "boolean",
      options: ["true","false"]
    },
    "ConnectionTimeOut": {
      ocpp: "ConnectionTimeOut",
      desc: "Délai avant d'annuler une connexion VE sans transaction (secondes)",
      type: "integer", range: "10–600", example: "60", unit: "secondes"
    },
    "GetConfigurationMaxKeys": {
      ocpp: "GetConfigurationMaxKeys",
      desc: "Nombre maximum de clés retournées par GetConfiguration",
      type: "integer", range: "1–999", example: "50"
    },
    "HeartbeatInterval": {
      ocpp: "HeartbeatInterval",
      desc: "Intervalle entre deux Heartbeat envoyés au serveur OCPP",
      type: "integer", range: "10–3600", example: "30", unit: "secondes"
    },
    "LightIntensity": {
      ocpp: "LightIntensity",
      desc: "Intensité lumineuse de la LED (0 = éteint, 100 = max)",
      type: "integer", range: "0–100", example: "80", unit: "%"
    },
    "LocalAuthorizeOffline": {
      ocpp: "LocalAuthorizeOffline",
      desc: "Autorise les transactions via liste locale si le réseau est absent",
      type: "boolean",
      options: ["true","false"]
    },
    "LocalAuthListEnabled": {
      ocpp: "LocalAuthListEnabled",
      desc: "Active l'utilisation de la liste d'autorisation locale",
      type: "boolean",
      options: ["true","false"]
    },
    "LocalAuthListMaxLength": {
      ocpp: "LocalAuthListMaxLength",
      desc: "Nombre maximum d'entrées dans la liste locale",
      type: "integer", range: "1–10000", example: "100"
    },
    "LocalPreAuthorize": {
      ocpp: "LocalPreAuthorize",
      desc: "Pré-autorise localement avant de recevoir la réponse du serveur",
      type: "boolean",
      options: ["true","false"]
    },
    "MaxChargingProfilesInstalled": {
      ocpp: "MaxChargingProfilesInstalled",
      desc: "Nombre maximum de profils de charge installés simultanément",
      type: "integer", range: "1–99", example: "8"
    },
    "MaxEnergyOnInvalidId": {
      ocpp: "MaxEnergyOnInvalidId",
      desc: "Énergie max (Wh) autorisée avant coupure si idTag invalide",
      type: "integer", range: "0–100000", example: "0", unit: "Wh"
    },
    "MeterValueSampleInterval": {
      ocpp: "MeterValueSampleInterval",
      desc: "Intervalle d'envoi des MeterValues pendant une transaction",
      type: "integer", range: "0–3600", example: "60", unit: "secondes",
      note: "0 = désactivé. 30–60s recommandé pour résidentiel"
    },
    "MeterValuesSampledData": {
      ocpp: "MeterValuesSampledData",
      desc: "Liste des mesurandes envoyés dans MeterValues pendant la session",
      type: "csv",
      options: [
        "Energy.Active.Import.Register",
        "Power.Active.Import",
        "Current.Import",
        "Voltage",
        "SoC",
        "Temperature",
        "Current.Offered",
        "Power.Offered"
      ],
      example: "Energy.Active.Import.Register,Power.Active.Import"
    },
    "MeterValuesSampledDataMaxLength": {
      ocpp: "MeterValuesSampledDataMaxLength",
      desc: "Nombre maximum de mesurandes dans MeterValuesSampledData",
      type: "integer", range: "1–20", example: "8"
    },
    "MinimumStatusDuration": {
      ocpp: "MinimumStatusDuration",
      desc: "Durée minimale d'un statut avant envoi de StatusNotification (secondes)",
      type: "integer", range: "0–600", example: "0", unit: "secondes"
    },
    "NumberOfConnectors": {
      ocpp: "NumberOfConnectors",
      desc: "Nombre de connecteurs physiques de la borne (hors connecteur 0)",
      type: "integer", range: "1–8", example: "1"
    },
    "ReserveConnectorZeroSupported": {
      ocpp: "ReserveConnectorZeroSupported",
      desc: "Supporte la réservation globale via le connecteur 0",
      type: "boolean",
      options: ["true","false"]
    },
    "ResetRetries": {
      ocpp: "ResetRetries",
      desc: "Nombre de tentatives de reset avant abandon",
      type: "integer", range: "0–10", example: "3"
    },
    "SendLocalListMaxLength": {
      ocpp: "SendLocalListMaxLength",
      desc: "Nombre maximum d'entrées dans un SendLocalList",
      type: "integer", range: "1–10000", example: "100"
    },
    "StopTransactionOnEVSideDisconnect": {
      ocpp: "StopTransactionOnEVSideDisconnect",
      desc: "Arrête automatiquement la transaction si le VE se débranche physiquement",
      type: "boolean",
      options: ["true","false"],
      note: "true fortement recommandé"
    },
    "StopTransactionOnInvalidId": {
      ocpp: "StopTransactionOnInvalidId",
      desc: "Arrête la transaction si l'idTag est rejeté par le serveur",
      type: "boolean",
      options: ["true","false"]
    },
    "StopTxnAlignedData": {
      ocpp: "StopTxnAlignedData",
      desc: "Mesurandes inclus dans le StopTransaction (alignés sur horloge)",
      type: "csv",
      options: [
        "Energy.Active.Import.Register",
        "Power.Active.Import",
        "Current.Import",
        "Voltage",
        "SoC"
      ],
      example: "Energy.Active.Import.Register"
    },
    "StopTxnAlignedDataMaxLength": {
      ocpp: "StopTxnAlignedDataMaxLength",
      desc: "Nombre maximum de mesurandes dans StopTxnAlignedData",
      type: "integer", range: "1–20", example: "8"
    },
    "StopTxnSampledData": {
      ocpp: "StopTxnSampledData",
      desc: "Mesurandes inclus dans le message StopTransaction",
      type: "csv",
      options: [
        "Energy.Active.Import.Register",
        "Power.Active.Import",
        "Current.Import",
        "Voltage",
        "SoC"
      ],
      example: "Energy.Active.Import.Register"
    },
    "StopTxnSampledDataMaxLength": {
      ocpp: "StopTxnSampledDataMaxLength",
      desc: "Nombre maximum de mesurandes dans StopTxnSampledData",
      type: "integer", range: "1–20", example: "8"
    },
    "SupportedFeatureProfiles": {
      ocpp: "SupportedFeatureProfiles",
      desc: "Profils OCPP 1.6 supportés par la borne (lecture seule en général)",
      type: "csv-ro",
      options: ["Core","FirmwareManagement","LocalAuthListManagement","Reservation","SmartCharging","RemoteTrigger"]
    },
    "SupportedFileTransferProtocols": {
      ocpp: "SupportedFileTransferProtocols",
      desc: "Protocoles supportés pour les transferts de fichiers (firmware)",
      type: "csv",
      options: ["HTTP","HTTPS","FTP","FTPS","SFTP"]
    },
    "TransactionMessageAttempts": {
      ocpp: "TransactionMessageAttempts",
      desc: "Nombre de tentatives d'envoi pour les messages de transaction",
      type: "integer", range: "1–20", example: "3"
    },
    "TransactionMessageRetryInterval": {
      ocpp: "TransactionMessageRetryInterval",
      desc: "Délai entre deux tentatives de renvoi d'un message de transaction",
      type: "integer", range: "5–600", example: "60", unit: "secondes"
    },
    "UnlockConnectorOnEVSideDisconnect": {
      ocpp: "UnlockConnectorOnEVSideDisconnect",
      desc: "Déverrouille physiquement le connecteur quand le VE se débranche",
      type: "boolean",
      options: ["true","false"]
    },
    "WebSocketPingInterval": {
      ocpp: "WebSocketPingInterval",
      desc: "Intervalle de ping WebSocket pour maintenir la connexion",
      type: "integer", range: "0–300", example: "30", unit: "secondes"
    },
    "ClockAlignedDataInterval": {
      ocpp: "ClockAlignedDataInterval",
      desc: "Intervalle d'envoi des MeterValues alignés sur l'horloge (secondes)",
      type: "integer", range: "0–3600", example: "60", unit: "secondes",
      note: "0 = désactivé"
    },
    "ChargingRateUnit": {
      ocpp: "ChargingRateUnit",
      desc: "Unité utilisée dans les profils de charge SmartCharging",
      type: "enum",
      options: ["Current","Power"],
      note: "Current = Ampères, Power = Watts"
    }
  },

  // ─────────────────────────────────────────────────────────────────
  // CLÉS PROPRIÉTAIRES NUMÉRIQUES — TechnoVE (S48 et compatibles)
  // Index 0–39 mappés depuis les observations terrain
  // ─────────────────────────────────────────────────────────────────
  vendor: {
    "technove": {
      "0":  { ocpp: "AllowOfflineTxForUnknownId",         desc: "Autorise les transactions hors-ligne pour un idTag inconnu du serveur",      type: "boolean", options: ["true","false"] },
      "1":  { ocpp: "HeartbeatInterval",                  desc: "Intervalle entre deux Heartbeat (secondes)",                                  type: "integer", range: "10–3600", example: "30", unit: "s" },
      "2":  { ocpp: "LocalAuthListVersion",               desc: "Version de la liste d'autorisation locale (0 = vide)",                       type: "integer", range: "0–9999", example: "0" },
      "3":  { ocpp: "LocalAuthorizeOffline",              desc: "Autorise les transactions via la liste locale si le réseau est absent",       type: "boolean", options: ["true","false"] },
      "4":  { ocpp: "LocalPreAuthorize",                  desc: "Pré-autorise localement avant la réponse du serveur Central",                type: "boolean", options: ["true","false"] },
      "5":  { ocpp: "AuthorizationCacheEnabled",          desc: "Active le cache local des autorisations reçues",                             type: "boolean", options: ["true","false"] },
      "6":  { ocpp: "StopTransactionOnInvalidId",         desc: "Stoppe la session si l'idTag est rejeté par le serveur",                     type: "boolean", options: ["true","false"] },
      "7":  { ocpp: "UnlockConnectorOnEVSideDisconnect",  desc: "Déverrouille le connecteur quand le VE se débranche physiquement",           type: "boolean", options: ["true","false"] },
      "8":  { ocpp: "ResetRetries",                       desc: "Nombre de tentatives de reset avant abandon",                                type: "integer", range: "0–10", example: "3" },
      "9":  { ocpp: "TransactionMessageAttempts",         desc: "Nombre de tentatives d'envoi pour les messages de transaction",              type: "integer", range: "1–20", example: "3" },
      "10": { ocpp: "Cst_MeterValuesInTxOnly (propriétaire)", desc: "Si false : la borne envoie des MeterValues même hors transaction OCPP — utile pour monitoring passif", type: "boolean", options: ["true","false"], note: "⭐ Mettre à false pour recevoir les métriques sans session active" },
      "11": { ocpp: "(réservé / inutilisé)",              desc: "Champ réservé — aucune correspondance OCPP identifiée",                     type: "text", example: "" },
      "12": { ocpp: "(propriétaire)",                     desc: "Flag interne TechnoVE — comportement non documenté publiquement",            type: "boolean", options: ["true","false"] },
      "13": { ocpp: "MeterValueSampleInterval",           desc: "Intervalle d'envoi des MeterValues pendant une session active",              type: "integer", range: "0–3600", example: "30", unit: "s" },
      "14": { ocpp: "StopTransactionOnEVSideDisconnect",  desc: "Arrête la transaction si le VE se débranche physiquement du connecteur",     type: "boolean", options: ["true","false"] },
      "15": { ocpp: "NumberOfPhases / ConnectorType",     desc: "Nombre de phases ou type de connecteur (2 = probable SAE J1772 monophasé)", type: "integer", range: "1–3", example: "1" },
      "16": { ocpp: "MeterValuesSampledData",             desc: "Liste des mesurandes envoyés dans MeterValues pendant la session",           type: "csv", options: ["Energy.Active.Import.Register","Power.Active.Import","Current.Import","Voltage","SoC","Temperature"], example: "Energy.Active.Import.Register,Power.Active.Import" },
      "17": { ocpp: "MeterValuesSampledDataMaxLength",    desc: "Nombre maximum de mesurandes autorisés dans MeterValuesSampledData",        type: "integer", range: "1–20", example: "8" },
      "18": { ocpp: "StopTxnSampledData",                 desc: "Mesurandes inclus dans le message StopTransaction à la fin de session",     type: "csv", options: ["Energy.Active.Import.Register","Power.Active.Import","Current.Import","Voltage","SoC"], example: "Energy.Active.Import.Register" },
      "19": { ocpp: "StopTxnAlignedData",                 desc: "Mesurandes alignés sur horloge inclus dans StopTransaction",               type: "csv", options: ["Energy.Active.Import.Register","Power.Active.Import"], example: "" },
      "20": { ocpp: "NumberOfConnectors",                 desc: "Nombre de connecteurs physiques de la borne (hors connecteur système 0)",   type: "integer", range: "1–8", example: "1" },
      "21": { ocpp: "ConnectionTimeOut",                  desc: "Délai d'attente max avant annulation si le VE est branché sans transaction", type: "integer", range: "10–600", example: "60", unit: "s" },
      "22": { ocpp: "MessageTimeout / TransactionMessageRetryInterval", desc: "Délai entre deux tentatives de renvoi d'un message de transaction", type: "integer", range: "5–600", example: "60", unit: "s" },
      "23": { ocpp: "(propriétaire)",                     desc: "Flag interne TechnoVE — non documenté publiquement",                       type: "boolean", options: ["true","false"] },
      "24": { ocpp: "(propriétaire)",                     desc: "Flag interne TechnoVE — non documenté publiquement",                       type: "boolean", options: ["true","false"] },
      "25": { ocpp: "ChargeProfileMaxStackLevel",         desc: "Nombre maximum de profils de charge empilables (SmartCharging stack)",      type: "integer", range: "1–99", example: "4" },
      "26": { ocpp: "ConnectorSwitch3to1PhaseSupported",  desc: "Supporte le passage dynamique triphasé → monophasé (souvent false en résidentiel)", type: "boolean", options: ["true","false"] },
      "27": { ocpp: "ChargingScheduleMaxPeriods",         desc: "Nombre maximum de périodes dans un planning de charge SmartCharging",      type: "integer", range: "1–1024", example: "24" },
      "28": { ocpp: "SmartChargingEnabled (propriétaire)", desc: "Active le module SmartCharging sur la borne",                            type: "boolean", options: ["true","false"] },
      "29": { ocpp: "MaxCurrentOffered (connecteur)",     desc: "Courant maximum offert par le connecteur EVSE (Ampères)",                  type: "integer", range: "6–80", example: "48", unit: "A" },
      "30": { ocpp: "MaxCurrentOffered (système)",        desc: "Courant maximum offert au niveau système/réseau",                          type: "integer", range: "6–80", example: "48", unit: "A" },
      "31": { ocpp: "StopTransactionOnEVSideDisconnect (doublon?)", desc: "Second flag de coupure au débranchement — doublon probable ou connecteur 2", type: "boolean", options: ["true","false"] },
      "32": { ocpp: "MaxChargingProfilesInstalled",       desc: "Nombre maximum de profils de charge installés simultanément",              type: "integer", range: "1–99", example: "8" },
      "33": { ocpp: "(propriétaire — limite interne)",    desc: "Limite interne TechnoVE — probablement ampérage ou puissance réseau",      type: "integer", range: "6–80", example: "8", unit: "A" },
      "34": { ocpp: "(propriétaire — limite interne)",    desc: "Limite interne TechnoVE — probablement ampérage ou puissance réseau",      type: "integer", range: "6–80", example: "8", unit: "A" },
      "35": { ocpp: "(propriétaire — limite interne)",    desc: "Limite interne TechnoVE — probablement ampérage ou puissance réseau",      type: "integer", range: "6–80", example: "8", unit: "A" },
      "36": { ocpp: "ChargingRateUnit",                   desc: "Unité des profils de charge SmartCharging (Current = Ampères, Power = Watts)", type: "enum", options: ["Current","Power"] },
      "37": { ocpp: "ChargingScheduleMaxPeriods (global)", desc: "Nombre maximum de périodes dans un planning global",                     type: "integer", range: "1–1024", example: "24" },
      "38": { ocpp: "GetConfigurationMaxKeys",            desc: "Nombre maximum de clés retournées par GetConfiguration",                   type: "integer", range: "1–999", example: "10" },
      "39": { ocpp: "SupportedFeatureProfiles",           desc: "Profils OCPP supportés — lecture seule, défini par le firmware",          type: "csv-ro", options: ["Core","FirmwareManagement","LocalAuthListManagement","Reservation","SmartCharging","RemoteTrigger"] }
    },

    // ─────────────────────────────────────────────────────────────────
    // CLÉS PROPRIÉTAIRES NUMÉRIQUES — Grizzl-E (Smart / Pro)
    // ─────────────────────────────────────────────────────────────────
    "grizzl-e": {
      "0":  { ocpp: "ChargeProfileMaxStackLevel",         desc: "Nombre maximum de profils de charge SmartCharging empilables",             type: "integer", range: "1–99", example: "4" },
      "1":  { ocpp: "ChargingRateUnit",                   desc: "Unité des profils de charge (Current = Ampères, Power = Watts)",           type: "enum", options: ["Current","Power"] },
      "2":  { ocpp: "MaxCurrentOffered",                  desc: "Courant maximum offert par la borne (A) — typiquement 35–40A sur Grizzl-E", type: "integer", range: "6–48", example: "35", unit: "A" },
      "3":  { ocpp: "MinimumCurrentSwitchingLevel",       desc: "Courant minimum avant de couper complètement la charge (A)",              type: "integer", range: "6–16", example: "12", unit: "A" },
      "4":  { ocpp: "AllowOfflineTxForUnknownId",         desc: "Autorise les transactions hors-ligne pour un idTag inconnu",              type: "boolean", options: ["true","false"] },
      "5":  { ocpp: "ConnectorPhaseRotation",             desc: "Rotation de phase du connecteur (NotApplicable = monophasé)",             type: "enum", options: ["NotApplicable","Unknown","RST","RTS","SRT","STR","TRS","TSR"] },
      "6":  { ocpp: "HeartbeatInterval",                  desc: "Intervalle entre deux Heartbeat envoyés au serveur OCPP (secondes)",      type: "integer", range: "10–3600", example: "30", unit: "s" },
      "7":  { ocpp: "MeterValueSampleInterval",           desc: "Intervalle d'envoi des MeterValues pendant une session active",           type: "integer", range: "0–3600", example: "30", unit: "s" },
      "8":  { ocpp: "StopTransactionOnEVSideDisconnect",  desc: "Arrête la transaction si le VE se débranche physiquement du câble",       type: "boolean", options: ["true","false"] },
      "9":  { ocpp: "UnlockConnectorOnEVSideDisconnect",  desc: "Déverrouille le connecteur quand le VE se débranche",                    type: "boolean", options: ["true","false"] },
      "10": { ocpp: "ConnectionTimeOut",                  desc: "Délai d'attente max avant annulation si le VE branché ne démarre pas",    type: "integer", range: "10–600", example: "60", unit: "s" },
      "11": { ocpp: "NumberOfConnectors",                 desc: "Nombre de connecteurs physiques de la borne",                            type: "integer", range: "1–8", example: "1" },
      "12": { ocpp: "LocalAuthListVersion",               desc: "Version de la liste d'autorisation locale",                              type: "integer", range: "0–9999", example: "1" },
      "13": { ocpp: "LocalAuthorizeOffline",              desc: "Autorise les transactions locales quand le réseau est absent",           type: "boolean", options: ["true","false"] },
      "14": { ocpp: "SupportedFeatureProfiles",           desc: "Profils OCPP supportés — lecture seule, défini par le firmware",         type: "csv-ro", options: ["Core","FirmwareManagement","SmartCharging","RemoteTrigger"] },
      "15": { ocpp: "LocalPreAuthorize",                  desc: "Pré-autorise localement avant la réponse du serveur Central",            type: "boolean", options: ["true","false"] },
      "16": { ocpp: "AuthorizationCacheEnabled",          desc: "Active le cache local des autorisations reçues du serveur",              type: "boolean", options: ["true","false"] },
      "17": { ocpp: "MeterValuesSampledData (additionnel)", desc: "Mesurande additionnel — Grizzl-E peut remonter la température interne", type: "enum", options: ["Temperature","Voltage","Current.Import","Power.Active.Import","Energy.Active.Import.Register"] },
      "18": { ocpp: "ClockAlignedDataInterval",           desc: "Intervalle d'envoi des MeterValues alignés sur l'horloge système",      type: "integer", range: "0–3600", example: "60", unit: "s" },
      "19": { ocpp: "AmperageLimit (système)",            desc: "Limite d'ampérage au niveau système — correspond typiquement au disjoncteur résidentiel moins une marge", type: "integer", range: "6–48", example: "34", unit: "A" },
      "20": { ocpp: "MeterValuesSampledData",             desc: "Liste des mesurandes envoyés dans MeterValues pendant la session",       type: "csv", options: ["Energy.Active.Import.Register","Power.Active.Import","Current.Import","Voltage","SoC","Temperature"], example: "Energy.Active.Import.Register" },
      "21": { ocpp: "StopTransactionOnInvalidId",         desc: "Stoppe la session si l'idTag est rejeté par le serveur Central",        type: "boolean", options: ["true","false"] },
      "22": { ocpp: "StopTxnSampledData",                 desc: "Mesurandes inclus dans le message StopTransaction à la fin de session", type: "csv", options: ["Energy.Active.Import.Register","Power.Active.Import","Current.Import","Voltage","SoC"], example: "Energy.Active.Import.Register" },
      "23": { ocpp: "StopTxnAlignedData",                 desc: "Mesurandes alignés sur horloge inclus dans StopTransaction",           type: "csv", options: ["Energy.Active.Import.Register","Power.Active.Import"], example: "Energy.Active.Import.Register" },
      "24": { ocpp: "TransactionMessageAttempts",         desc: "Nombre de tentatives de renvoi pour les messages de transaction",       type: "integer", range: "1–20", example: "3" },
      "25": { ocpp: "TransactionMessageRetryInterval",    desc: "Délai entre deux tentatives de renvoi d'un message de transaction",     type: "integer", range: "5–600", example: "60", unit: "s" },
      "26": { ocpp: "SupportedFileTransferProtocols",     desc: "Protocoles acceptés pour les transferts de firmware",                   type: "csv-ro", options: ["HTTP","HTTPS","FTP","FTPS"] },
      "27": { ocpp: "(propriétaire)",                     desc: "Flag interne Grizzl-E — non documenté publiquement",                   type: "boolean", options: ["true","false"] },
      "28": { ocpp: "Port WebSocket interne",             desc: "Port d'écoute interne de la borne (probablement port WebSocket local)", type: "integer", range: "1–65535", example: "80" },
      "29": { ocpp: "ChargeBoxSerialNumber / FirmwareVersion", desc: "Identifiant matériel ou version firmware encodé — lecture seule en général", type: "text-ro", example: "0019003a..." },
      "30": { ocpp: "(réservé)",                          desc: "Champ réservé — peut contenir un identifiant matériel secondaire",     type: "text", example: "" },
      "31": { ocpp: "MaxChargingProfilesInstalled",       desc: "Nombre maximum de profils de charge installés simultanément",          type: "integer", range: "1–99", example: "3" },
      "32": { ocpp: "LocalAuthListVersion (courant)",     desc: "Version courante de la liste d'autorisation locale",                   type: "integer", range: "0–9999", example: "0" },
      "33": { ocpp: "AmperageLimit (connecteur)",         desc: "Limite d'ampérage au niveau connecteur — correspond au fil de charge installé", type: "integer", range: "6–48", example: "14", unit: "A" }
    }
  },

  // ─────────────────────────────────────────────────────────────────
  // Résolution : retourne les infos pour une clé donnée
  // vendor : "technove" | "grizzl-e" | null
  // ─────────────────────────────────────────────────────────────────
  resolve(key, vendor) {
    // 1. Clé nommée standard OCPP
    if (this.standard[key]) return this.standard[key];

    // 2. Clé numérique vendor
    if (vendor) {
      const vendorKey = vendor.toLowerCase().replace(/\s+/g, '');
      // Essayer le nom exact, puis des variantes courantes
      const candidates = [vendorKey, vendorKey.replace('-',''), vendorKey.split(' ')[0]];
      for (const v of candidates) {
        if (this.vendor[v] && this.vendor[v][key]) return this.vendor[v][key];
      }
    }
    // 3. Recherche numérique dans tous les vendors si pas de vendor spécifié
    if (/^\d+$/.test(key)) {
      for (const v of Object.values(this.vendor)) {
        if (v[key]) return v[key];
      }
    }
    return null;
  }
};
