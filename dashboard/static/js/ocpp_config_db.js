// ocpp_config_db.js — Base de donnees des cles OCPP 1.6 (std + TechnoVE + Grizzl-E).
// Extrait du monolithe dashboard.html (Refactor Phase 2, Sprint 33).
// Expose: window.OCPP_DB (lecture seule cote JS).

// ═══════════════════════════════════════════════════════
// OCPP Configuration Key Database
// Standard OCPP 1.6 + TechnoVE numérique + Grizzl-E numérique
// ═══════════════════════════════════════════════════════
const OCPP_DB = {
  std: {
    "AllowOfflineTxForUnknownId":            { ocpp:"AllowOfflineTxForUnknownId",            desc:"Autorise les transactions hors-ligne pour un idTag inconnu du serveur",         type:"bool" },
    "AuthorizationCacheEnabled":             { ocpp:"AuthorizationCacheEnabled",             desc:"Active le cache local des autorisations reçues",                               type:"bool" },
    "AuthorizeRemoteTxRequests":             { ocpp:"AuthorizeRemoteTxRequests",             desc:"Exige une autorisation pour les RemoteStartTransaction",                        type:"bool" },
    "ChargeProfileMaxStackLevel":            { ocpp:"ChargeProfileMaxStackLevel",            desc:"Nombre maximum de profils de charge empilables",                               type:"int",  range:"1–99",    ex:"4" },
    "ChargingRateUnit":                      { ocpp:"ChargingRateUnit",                      desc:"Unité des profils SmartCharging — Current=Ampères, Power=Watts",               type:"enum", opts:["Current","Power"] },
    "ChargingScheduleMaxPeriods":            { ocpp:"ChargingScheduleMaxPeriods",            desc:"Nombre maximum de périodes dans un planning de charge",                        type:"int",  range:"1–1024",  ex:"24" },
    "ClockAlignedDataInterval":              { ocpp:"ClockAlignedDataInterval",              desc:"Intervalle d'envoi des MeterValues alignés sur l'horloge (0=désactivé)",       type:"int",  range:"0–3600",  ex:"60",  unit:"s" },
    "ConnectionTimeOut":                     { ocpp:"ConnectionTimeOut",                     desc:"Délai avant d'annuler une connexion VE sans transaction",                      type:"int",  range:"10–600",  ex:"60",  unit:"s" },
    "ConnectorPhaseRotation":               { ocpp:"ConnectorPhaseRotation",               desc:"Rotation de phase du connecteur",                                              type:"enum", opts:["NotApplicable","Unknown","RST","RTS","SRT","STR","TRS","TSR"] },
    "ConnectorSwitch3to1PhaseSupported":    { ocpp:"ConnectorSwitch3to1PhaseSupported",    desc:"Supporte le passage dynamique triphasé → monophasé",                           type:"bool" },
    "GetConfigurationMaxKeys":              { ocpp:"GetConfigurationMaxKeys",              desc:"Nombre maximum de clés retournées par GetConfiguration",                       type:"int",  range:"1–999",   ex:"50" },
    "HeartbeatInterval":                    { ocpp:"HeartbeatInterval",                    desc:"Intervalle entre deux Heartbeat envoyés au serveur OCPP",                      type:"int",  range:"10–3600", ex:"30",  unit:"s" },
    "LocalAuthListEnabled":                 { ocpp:"LocalAuthListEnabled",                 desc:"Active l'utilisation de la liste d'autorisation locale",                       type:"bool" },
    "LocalAuthListMaxLength":               { ocpp:"LocalAuthListMaxLength",               desc:"Nombre maximum d'entrées dans la liste locale",                                type:"int",  range:"1–10000", ex:"100" },
    "LocalAuthorizeOffline":                { ocpp:"LocalAuthorizeOffline",                desc:"Autorise les transactions via la liste locale si le réseau est absent",        type:"bool" },
    "LocalPreAuthorize":                    { ocpp:"LocalPreAuthorize",                    desc:"Pré-autorise localement avant la réponse du serveur Central",                  type:"bool" },
    "MaxChargingProfilesInstalled":         { ocpp:"MaxChargingProfilesInstalled",         desc:"Nombre maximum de profils de charge installés simultanément",                  type:"int",  range:"1–99",    ex:"8" },
    "MeterValueSampleInterval":             { ocpp:"MeterValueSampleInterval",             desc:"Intervalle d'envoi des MeterValues pendant la session (0=désactivé)",          type:"int",  range:"0–3600",  ex:"30",  unit:"s" },
    "MeterValuesSampledData":               { ocpp:"MeterValuesSampledData",               desc:"Mesurandes envoyés dans MeterValues pendant la session",                       type:"csv",  opts:["Energy.Active.Import.Register","Power.Active.Import","Current.Import","Voltage","SoC","Temperature","Current.Offered"] },
    "NumberOfConnectors":                   { ocpp:"NumberOfConnectors",                   desc:"Nombre de connecteurs physiques de la borne (hors connecteur 0)",              type:"int",  range:"1–8",     ex:"1" },
    "ResetRetries":                         { ocpp:"ResetRetries",                         desc:"Nombre de tentatives de reset avant abandon",                                  type:"int",  range:"0–10",    ex:"3" },
    "SendLocalListMaxLength":               { ocpp:"SendLocalListMaxLength",               desc:"Nombre maximum d'entrées dans un SendLocalList",                               type:"int",  range:"1–10000", ex:"100" },
    "StopTransactionOnEVSideDisconnect":    { ocpp:"StopTransactionOnEVSideDisconnect",    desc:"Arrête la transaction si le VE se débranche physiquement",                     type:"bool", note:"true fortement recommandé" },
    "StopTransactionOnInvalidId":           { ocpp:"StopTransactionOnInvalidId",           desc:"Arrête la transaction si l'idTag est rejeté par le serveur",                   type:"bool" },
    "StopTxnAlignedData":                   { ocpp:"StopTxnAlignedData",                   desc:"Mesurandes alignés sur horloge inclus dans StopTransaction",                   type:"csv",  opts:["Energy.Active.Import.Register","Power.Active.Import","Voltage","SoC"] },
    "StopTxnSampledData":                   { ocpp:"StopTxnSampledData",                   desc:"Mesurandes inclus dans le message StopTransaction",                            type:"csv",  opts:["Energy.Active.Import.Register","Power.Active.Import","Current.Import","Voltage","SoC"] },
    "SupportedFeatureProfiles":             { ocpp:"SupportedFeatureProfiles",             desc:"Profils OCPP 1.6 supportés — lecture seule, défini par le firmware",           type:"csv-ro" },
    "SupportedFileTransferProtocols":       { ocpp:"SupportedFileTransferProtocols",       desc:"Protocoles supportés pour les transferts de firmware",                         type:"csv",  opts:["HTTP","HTTPS","FTP","FTPS","SFTP"] },
    "TransactionMessageAttempts":           { ocpp:"TransactionMessageAttempts",           desc:"Nombre de tentatives de renvoi pour les messages de transaction",              type:"int",  range:"1–20",    ex:"3" },
    "TransactionMessageRetryInterval":      { ocpp:"TransactionMessageRetryInterval",      desc:"Délai entre deux tentatives de renvoi d'un message de transaction",            type:"int",  range:"5–600",   ex:"60",  unit:"s" },
    "UnlockConnectorOnEVSideDisconnect":    { ocpp:"UnlockConnectorOnEVSideDisconnect",    desc:"Déverrouille le connecteur quand le VE se débranche",                          type:"bool" },
    "WebSocketPingInterval":                { ocpp:"WebSocketPingInterval",                desc:"Intervalle de ping WebSocket pour maintenir la connexion",                     type:"int",  range:"0–300",   ex:"30",  unit:"s" },
  },
  vendor: {
    technove: {
      "0":  { ocpp:"AllowOfflineTxForUnknownId",         desc:"Autorise les transactions hors-ligne pour un idTag inconnu",                 type:"bool" },
      "1":  { ocpp:"HeartbeatInterval",                  desc:"Intervalle entre deux Heartbeat (secondes)",                                 type:"int",  range:"10–3600", ex:"30",  unit:"s" },
      "2":  { ocpp:"LocalAuthListVersion",               desc:"Version de la liste d'autorisation locale (0 = vide / non initialisée)",    type:"int",  range:"0–9999",  ex:"0" },
      "3":  { ocpp:"LocalAuthorizeOffline",              desc:"Autorise les transactions via la liste locale si réseau absent",             type:"bool" },
      "4":  { ocpp:"LocalPreAuthorize",                  desc:"Pré-autorise localement avant la réponse du serveur Central",               type:"bool" },
      "5":  { ocpp:"AuthorizationCacheEnabled",          desc:"Active le cache local des autorisations reçues du serveur",                 type:"bool" },
      "6":  { ocpp:"StopTransactionOnInvalidId",         desc:"Stoppe la session si l'idTag est rejeté par le serveur",                    type:"bool" },
      "7":  { ocpp:"UnlockConnectorOnEVSideDisconnect",  desc:"Déverrouille le connecteur quand le VE se débranche physiquement",          type:"bool" },
      "8":  { ocpp:"ResetRetries",                       desc:"Nombre de tentatives de reset avant abandon",                               type:"int",  range:"0–10",    ex:"3" },
      "9":  { ocpp:"TransactionMessageAttempts",         desc:"Nombre de tentatives d'envoi pour les messages de transaction",             type:"int",  range:"1–20",    ex:"3" },
      "10": { ocpp:"Cst_MeterValuesInTxOnly (propriétaire)", desc:"false = MeterValues même hors session OCPP active — utile pour monitoring passif", type:"bool", note:"⭐ Mettre à false pour recevoir les métriques sans session" },
      "11": { ocpp:"(réservé)",                          desc:"Champ réservé — aucune correspondance OCPP identifiée",                    type:"text" },
      "12": { ocpp:"(propriétaire)",                     desc:"Flag interne TechnoVE — comportement non documenté publiquement",           type:"bool" },
      "13": { ocpp:"MeterValueSampleInterval",           desc:"Intervalle d'envoi des MeterValues pendant une session active",             type:"int",  range:"0–3600",  ex:"30",  unit:"s" },
      "14": { ocpp:"StopTransactionOnEVSideDisconnect",  desc:"Arrête la transaction si le VE se débranche physiquement du connecteur",    type:"bool" },
      "15": { ocpp:"NumberOfPhases / ConnectorType",     desc:"Nombre de phases ou type connecteur (2 = probable SAE J1772 monophasé)",   type:"int",  range:"1–3",     ex:"1" },
      "16": { ocpp:"MeterValuesSampledData",             desc:"Liste des mesurandes envoyés dans MeterValues pendant la session",          type:"csv",  opts:["Energy.Active.Import.Register","Power.Active.Import","Current.Import","Voltage","SoC","Temperature"] },
      "17": { ocpp:"MeterValuesSampledDataMaxLength",    desc:"Nombre maximum de mesurandes dans MeterValuesSampledData",                 type:"int",  range:"1–20",    ex:"8" },
      "18": { ocpp:"StopTxnSampledData",                 desc:"Mesurandes inclus dans le message StopTransaction à la fin de session",    type:"csv",  opts:["Energy.Active.Import.Register","Power.Active.Import","Current.Import","Voltage","SoC"] },
      "19": { ocpp:"StopTxnAlignedData",                 desc:"Mesurandes alignés sur horloge inclus dans StopTransaction",              type:"csv",  opts:["Energy.Active.Import.Register","Power.Active.Import"] },
      "20": { ocpp:"NumberOfConnectors",                 desc:"Nombre de connecteurs physiques de la borne (hors connecteur système 0)",  type:"int",  range:"1–8",     ex:"1" },
      "21": { ocpp:"ConnectionTimeOut",                  desc:"Délai d'attente max si VE branché sans démarrer une transaction",          type:"int",  range:"10–600",  ex:"60",  unit:"s" },
      "22": { ocpp:"TransactionMessageRetryInterval",    desc:"Délai entre deux tentatives de renvoi d'un message de transaction",        type:"int",  range:"5–600",   ex:"60",  unit:"s" },
      "23": { ocpp:"(propriétaire)",                     desc:"Flag interne TechnoVE — non documenté publiquement",                      type:"bool" },
      "24": { ocpp:"(propriétaire)",                     desc:"Flag interne TechnoVE — non documenté publiquement",                      type:"bool" },
      "25": { ocpp:"ChargeProfileMaxStackLevel",         desc:"Nombre maximum de profils SmartCharging empilables (stack)",               type:"int",  range:"1–99",    ex:"4" },
      "26": { ocpp:"ConnectorSwitch3to1PhaseSupported",  desc:"Supporte le passage dynamique triphasé → monophasé",                      type:"bool" },
      "27": { ocpp:"ChargingScheduleMaxPeriods",         desc:"Nombre maximum de périodes dans un planning de charge SmartCharging",     type:"int",  range:"1–1024",  ex:"24" },
      "28": { ocpp:"SmartChargingEnabled (propriétaire)",desc:"Active le module SmartCharging sur la borne",                            type:"bool" },
      "29": { ocpp:"MaxCurrentOffered (connecteur)",     desc:"Courant maximum offert par le connecteur EVSE",                           type:"int",  range:"6–80",    ex:"48",  unit:"A" },
      "30": { ocpp:"MaxCurrentOffered (système)",        desc:"Courant maximum offert au niveau système / réseau",                       type:"int",  range:"6–80",    ex:"48",  unit:"A" },
      "31": { ocpp:"StopTransactionOnEVSideDisconnect",  desc:"Doublon probable ou connecteur secondaire — même rôle que clé 14",        type:"bool" },
      "32": { ocpp:"MaxChargingProfilesInstalled",       desc:"Nombre maximum de profils de charge installés simultanément",             type:"int",  range:"1–99",    ex:"8" },
      "33": { ocpp:"(limite interne propriétaire)",      desc:"Limite interne TechnoVE — probablement ampérage ou puissance réseau",     type:"int",  range:"6–80",    ex:"8",   unit:"A" },
      "34": { ocpp:"(limite interne propriétaire)",      desc:"Limite interne TechnoVE",                                                 type:"int",  range:"6–80",    ex:"8",   unit:"A" },
      "35": { ocpp:"(limite interne propriétaire)",      desc:"Limite interne TechnoVE",                                                 type:"int",  range:"6–80",    ex:"8",   unit:"A" },
      "36": { ocpp:"ChargingRateUnit",                   desc:"Unité des profils de charge SmartCharging (Current=A, Power=W)",          type:"enum", opts:["Current","Power"] },
      "37": { ocpp:"ChargingScheduleMaxPeriods (global)",desc:"Nombre maximum de périodes dans un planning global",                      type:"int",  range:"1–1024",  ex:"24" },
      "38": { ocpp:"GetConfigurationMaxKeys",            desc:"Nombre maximum de clés retournées par GetConfiguration",                  type:"int",  range:"1–999",   ex:"10" },
      "39": { ocpp:"SupportedFeatureProfiles",           desc:"Profils OCPP supportés — lecture seule, défini par le firmware",          type:"csv-ro" },
    },
    "grizzl-e": {
      "0":  { ocpp:"ChargeProfileMaxStackLevel",         desc:"Nombre maximum de profils SmartCharging empilables",                      type:"int",  range:"1–99",    ex:"4" },
      "1":  { ocpp:"ChargingRateUnit",                   desc:"Unité des profils de charge (Current=Ampères, Power=Watts)",              type:"enum", opts:["Current","Power"] },
      "2":  { ocpp:"MaxCurrentOffered",                  desc:"Courant maximum offert par la borne — typiquement 35–40A sur Grizzl-E",   type:"int",  range:"6–48",    ex:"35",  unit:"A" },
      "3":  { ocpp:"MinimumCurrentSwitchingLevel",       desc:"Courant minimum avant de couper complètement la charge",                  type:"int",  range:"6–16",    ex:"12",  unit:"A" },
      "4":  { ocpp:"AllowOfflineTxForUnknownId",         desc:"Autorise les transactions hors-ligne pour un idTag inconnu",              type:"bool" },
      "5":  { ocpp:"ConnectorPhaseRotation",             desc:"Rotation de phase (NotApplicable = monophasé résidentiel)",               type:"enum", opts:["NotApplicable","Unknown","RST","RTS","SRT","STR","TRS","TSR"] },
      "6":  { ocpp:"HeartbeatInterval",                  desc:"Intervalle entre deux Heartbeat envoyés au serveur OCPP",                 type:"int",  range:"10–3600", ex:"30",  unit:"s" },
      "7":  { ocpp:"MeterValueSampleInterval",           desc:"Intervalle d'envoi des MeterValues pendant une session active",           type:"int",  range:"0–3600",  ex:"30",  unit:"s" },
      "8":  { ocpp:"StopTransactionOnEVSideDisconnect",  desc:"Arrête la transaction si le VE se débranche physiquement",               type:"bool" },
      "9":  { ocpp:"UnlockConnectorOnEVSideDisconnect",  desc:"Déverrouille le connecteur quand le VE se débranche",                    type:"bool" },
      "10": { ocpp:"ConnectionTimeOut",                  desc:"Délai d'attente max si VE branché sans démarrer une transaction",         type:"int",  range:"10–600",  ex:"60",  unit:"s" },
      "11": { ocpp:"NumberOfConnectors",                 desc:"Nombre de connecteurs physiques de la borne",                            type:"int",  range:"1–8",     ex:"1" },
      "12": { ocpp:"LocalAuthListVersion",               desc:"Version de la liste d'autorisation locale",                              type:"int",  range:"0–9999",  ex:"1" },
      "13": { ocpp:"LocalAuthorizeOffline",              desc:"Autorise les transactions locales quand le réseau est absent",            type:"bool" },
      "14": { ocpp:"SupportedFeatureProfiles",           desc:"Profils OCPP supportés — lecture seule, défini par le firmware",         type:"csv-ro" },
      "15": { ocpp:"LocalPreAuthorize",                  desc:"Pré-autorise localement avant la réponse du serveur Central",            type:"bool" },
      "16": { ocpp:"AuthorizationCacheEnabled",          desc:"Active le cache local des autorisations reçues",                         type:"bool" },
      "17": { ocpp:"MeterValuesSampledData (additionnel)",desc:"Mesurande additionnel — Grizzl-E peut remonter la température interne",  type:"enum", opts:["Temperature","Voltage","Current.Import","Power.Active.Import","Energy.Active.Import.Register"] },
      "18": { ocpp:"ClockAlignedDataInterval",           desc:"Intervalle d'envoi des MeterValues alignés sur l'horloge système",       type:"int",  range:"0–3600",  ex:"60",  unit:"s" },
      "19": { ocpp:"AmperageLimit (système)",            desc:"Limite d'ampérage système — typiquement disjoncteur résidentiel - marge", type:"int",  range:"6–48",    ex:"34",  unit:"A" },
      "20": { ocpp:"MeterValuesSampledData",             desc:"Mesurandes envoyés dans MeterValues pendant la session",                 type:"csv",  opts:["Energy.Active.Import.Register","Power.Active.Import","Current.Import","Voltage","SoC","Temperature"] },
      "21": { ocpp:"StopTransactionOnInvalidId",         desc:"Stoppe la session si l'idTag est rejeté par le serveur Central",         type:"bool" },
      "22": { ocpp:"StopTxnSampledData",                 desc:"Mesurandes inclus dans le message StopTransaction",                      type:"csv",  opts:["Energy.Active.Import.Register","Power.Active.Import","Current.Import","Voltage","SoC"] },
      "23": { ocpp:"StopTxnAlignedData",                 desc:"Mesurandes alignés sur horloge inclus dans StopTransaction",             type:"csv",  opts:["Energy.Active.Import.Register","Power.Active.Import"] },
      "24": { ocpp:"TransactionMessageAttempts",         desc:"Nombre de tentatives de renvoi pour les messages de transaction",        type:"int",  range:"1–20",    ex:"3" },
      "25": { ocpp:"TransactionMessageRetryInterval",    desc:"Délai entre deux tentatives de renvoi d'un message de transaction",      type:"int",  range:"5–600",   ex:"60",  unit:"s" },
      "26": { ocpp:"SupportedFileTransferProtocols",     desc:"Protocoles acceptés pour les transferts de firmware",                    type:"csv-ro" },
      "27": { ocpp:"(propriétaire)",                     desc:"Flag interne Grizzl-E — non documenté publiquement",                    type:"bool" },
      "28": { ocpp:"Port WebSocket interne",             desc:"Port d'écoute interne de la borne (probablement WebSocket local)",       type:"int",  range:"1–65535", ex:"80" },
      "29": { ocpp:"ChargeBoxSerialNumber / FirmwareVersion", desc:"Identifiant matériel ou firmware encodé — généralement lecture seule", type:"text-ro" },
      "30": { ocpp:"(réservé)",                          desc:"Champ réservé — peut contenir un identifiant matériel secondaire",      type:"text" },
      "31": { ocpp:"MaxChargingProfilesInstalled",       desc:"Nombre maximum de profils de charge installés simultanément",           type:"int",  range:"1–99",    ex:"3" },
      "32": { ocpp:"LocalAuthListVersion (courant)",     desc:"Version courante de la liste d'autorisation locale",                    type:"int",  range:"0–9999",  ex:"0" },
      "33": { ocpp:"AmperageLimit (connecteur)",         desc:"Limite d'ampérage au niveau connecteur — doit correspondre au fil installé", type:"int", range:"6–48", ex:"14", unit:"A" },
    }
  },

  // Aliases pour matcher les variantes orthographiques du fabricant
  aliases: {
    'technove':  ['technove','techno-ve','techno ve','technove s48','technove s32'],
    'grizzl-e':  ['grizzl-e','grizzle','grizzl_e','grizzl e','grizzl-e smart','grizzl-e pro','grizzl-e universal','grizzl','united chargers','unitedchargers','united-chargers','uc'],
  },

  // Labels affichés dans le sélecteur UI du modal
  vendorLabels: [
    { key: '',         label: '⚡ Auto-détect' },
    { key: 'technove', label: 'TechnoVE' },
    { key: 'grizzl-e', label: 'Grizzl-E (United Chargers)' },
    { key: 'ocpp16',   label: 'Clés OCPP 1.6 standard uniquement' },
  ],

  // Normalise un nom de fabricant brut vers la clé interne du vendor
  normalizeVendor(raw) {
    if (!raw) return '';
    const v = raw.toLowerCase().trim().replace(/[_]/g,'-');
    for (const [key, aliases] of Object.entries(this.aliases)) {
      if (aliases.some(a => v.includes(a) || a.includes(v.replace(/-/g,''))))
        return key;
    }
    return ''; // inconnu : pas de mapping vendor
  },

  resolve(key, vendor) {
    // 1. Clé nommée standard OCPP — priorité absolue
    if (this.std[key]) return this.std[key];

    // 2. Vendor expliquer (override manuel ou auto-détect)
    if (vendor && vendor !== 'ocpp16') {
      const norm = this.normalizeVendor(vendor);
      if (norm && this.vendor[norm]?.[key]) return this.vendor[norm][key];
    }

    // 3. Fallback ALL-VENDOR seulement si AUCUN vendor spécifié
    //    (ne jamais fallback si un vendor est fourni : évite TechnoVE de primer sur Grizzl-E)
    if (!vendor && /^\d+$/.test(key)) {
      for (const vmap of Object.values(this.vendor))
        if (vmap[key]) return vmap[key];
    }
    return null;
  }
};

