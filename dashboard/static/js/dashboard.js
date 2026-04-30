// dashboard.js — Logique SPA du dashboard OCPP (WebSocket, Chart.js, modals,
// config inline editor). Depend de window.OCPP_DB (voir ocpp_config_db.js).
// Extrait du monolithe dashboard.html (Refactor Phase 2, Sprint 33).

// ═══════════════════════════════════════════════════════
// État global
// ═══════════════════════════════════════════════════════
const serverHost = location.hostname;
let activeTxIds    = {};
let chargerDefaults= {};
let _cfgData       = {};   // { key: {value, readonly} }
let _cfgVendor     = '';

// ═══════════════════════════════════════════════════════
// WebSocket
// ═══════════════════════════════════════════════════════
let ws;
function connectWS() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  ws = new WebSocket(`${proto}://${location.host}/ws/dashboard`);
  const statusEl = document.getElementById('ws-status');
  ws.onopen  = () => { statusEl.textContent = '● Connecté';    statusEl.className = 'badge b-available'; };
  ws.onclose = () => { statusEl.textContent = '● Déconnecté';  statusEl.className = 'badge b-offline'; setTimeout(connectWS, 5000); };
  ws.onerror = () => {};
  ws.onmessage = (e) => {
    const d = JSON.parse(e.data);
    if      (d.type === 'status_update')      onStatusUpdate(d);
    else if (d.type === 'meter_value')        onMeterValue(d);
    else if (d.type === 'heartbeat')          onHeartbeat(d);
    else if (d.type === 'transaction')        onTransaction(d);
    else if (d.type === 'connected_chargers') onConnectedList(d);
    else if (d.type === 'config')             onConfig(d);
  };
}
connectWS();

// ── WebSocket handlers ─────────────────────────────────
function onConnectedList(d) {
  document.getElementById('s-conn').textContent = d.chargers.length;
  // Marquer les bornes connectées
  d.chargers.forEach(id => setConnected(id, true));
  // FIX: Marquer les bornes absentes comme Offline
  // Couvre : redémarrage serveur, crash, reconnexion dashboard WS
  // Sans ce fix, le badge reste 'Available' (dernière valeur DB connue)
  // même si la conn-bar est rouge (borne absente de connected_chargers)
  document.querySelectorAll('[id^="card-"]').forEach(card => {
    const id = card.id.replace('card-', '');
    if (!d.chargers.includes(id)) {
      setConnected(id, false);
      const badge = document.getElementById(`status-${id}`);
      if (badge) { badge.textContent = 'Offline'; badge.className = 'badge b-offline'; }
    }
  });
}

function onStatusUpdate(d) {
  const { charger_id, connector_id, status } = d;
  if (connector_id === 0 || connector_id === undefined) {
    const badge = document.getElementById(`status-${charger_id}`);
    if (badge) { badge.textContent = status; badge.className = `badge b-${status.toLowerCase()}`; }
    setConnected(charger_id, status !== 'Offline');
  } else {
    const cs = document.getElementById(`cs-${charger_id}-${connector_id}`);
    if (cs) cs.textContent = status;
  }
}

function setConnected(id, on) {
  const card = document.getElementById(`card-${id}`);
  const bar  = document.getElementById(`connbar-${id}`);
  const dot  = document.getElementById(`dot-${id}`);
  const lbl  = document.getElementById(`connlbl-${id}`);
  if (card) card.classList.toggle('connected', on);
  if (bar)  bar.className = `conn-bar ${on ? 'on' : 'off'}`;
  if (dot)  dot.className = `conn-dot ${on ? 'on' : 'off'}`;
  if (lbl)  lbl.textContent = on ? 'WebSocket connecté' : 'Hors ligne — commandes indisponibles';
  ['start','stop','cut','restore','avail','unavail','unlock','reset','cfg'].forEach(b => {
    const btn = document.getElementById(`btn-${b}-${id}`);
    if (btn) btn.disabled = !on;
  });
}

function onHeartbeat(d) {
  const el = document.getElementById(`hb-${d.charger_id}`);
  if (el) el.textContent = `Heartbeat : ${new Date(d.timestamp).toLocaleTimeString('fr-CA')}`;
}

function onTransaction(d) {
  const txEl = document.getElementById(`tx-${d.charger_id}`);
  if (d.action === 'start') {
    activeTxIds[d.charger_id] = d.transaction_id;
    if (txEl) { txEl.textContent = `Session active — Transaction #${d.transaction_id}`; txEl.className = 'tx-info active'; }
    const s = document.getElementById('s-active');
    if (s) s.textContent = parseInt(s.textContent||'0') + 1;
  } else {
    delete activeTxIds[d.charger_id];
    if (txEl) { txEl.textContent = 'Aucune session active'; txEl.className = 'tx-info'; }
    const box = document.getElementById(`powerbox-${d.charger_id}`);
    if (box) box.classList.remove('power-cut');
    const s = document.getElementById('s-active');
    if (s) s.textContent = Math.max(0, parseInt(s.textContent||'1') - 1);
  }
}

function onMeterValue(d) {
  const { charger_id, power_w, energy_wh, session_energy_wh, current_a, voltage_v, soc_percent } = d;
  if (power_w != null) addPowerPoint(charger_id, power_w);
  const ep = document.getElementById(`m-power-${charger_id}`);
  if (ep) ep.textContent = power_w != null ? `${Math.round(power_w)} W` : '— W';
  const ec = document.getElementById(`m-current-${charger_id}`);
  if (ec && current_a != null) ec.textContent = `${current_a.toFixed(1)} A`;
  const es = document.getElementById(`m-session-${charger_id}`);
  const dispE = session_energy_wh != null ? session_energy_wh : energy_wh;
  if (es && dispE != null) es.textContent = `${(Math.max(0,dispE)/1000).toFixed(2)} kWh`;
  const ev = document.getElementById(`m-voltage-${charger_id}`);
  if (ev && voltage_v != null) ev.textContent = `${Math.round(voltage_v)} V`;
}

function onConfig(d) {
  if (document.getElementById('cfg-cid')?.value === d.charger_id) {
    _cfgData = d.config || {};
    renderCfgTable(_cfgData);
  }
}

// ═══════════════════════════════════════════════════════
// Graphique puissance
// ═══════════════════════════════════════════════════════
const pData  = { labels:[], datasets:[] };
const pChart = new Chart(document.getElementById('powerChart'), {
  type: 'line', data: pData,
  options: {
    responsive: true, animation: false,
    scales: {
      x: { ticks:{ color:'#64748b', maxTicksLimit:8 }, grid:{ color:'#1e293b' } },
      y: { ticks:{ color:'#64748b' }, grid:{ color:'#334155' }, min:0 },
    },
    plugins: { legend:{ labels:{ color:'#e2e8f0', font:{ size:11 } } } },
  },
});
const COLORS=['#38bdf8','#34d399','#f59e0b','#f87171','#a78bfa','#fb923c'];
const cIdx={}; let cidx=0;
function addPowerPoint(id, w) {
  const t = new Date().toLocaleTimeString('fr-CA',{hour:'2-digit',minute:'2-digit',second:'2-digit'});
  if (pData.labels[pData.labels.length-1] !== t) {
    pData.labels.push(t);
    if (pData.labels.length > 50) pData.labels.shift();
  }
  if (!cIdx[id]) cIdx[id] = COLORS[cidx++ % COLORS.length];
  let ds = pData.datasets.find(d => d.label === id);
  if (!ds) { ds = { label:id, data:[], borderColor:cIdx[id], tension:.3, fill:false, pointRadius:2 }; pData.datasets.push(ds); }
  ds.data.push(Math.round(w));
  if (ds.data.length > 50) ds.data.shift();
  pChart.update('none');
}

// ═══════════════════════════════════════════════════════
// Stats initiales
// ═══════════════════════════════════════════════════════
async function loadStats(id) {
  try {
    const r = await fetch(`/api/chargers/${id}/stats`);
    if (!r.ok) return;
    const d = await r.json();
    const et = document.getElementById(`total-${id}`);
    if (et) et.textContent = `Total livré : ${d.total_delivered_kwh} kWh`;
    if (d.current_power_w != null) {
      const ep = document.getElementById(`m-power-${id}`);
      if (ep) ep.textContent = `${Math.round(d.current_power_w)} W`;
    }
    if (d.session_energy_wh != null) {
      const es = document.getElementById(`m-session-${id}`);
      if (es) es.textContent = `${(d.session_energy_wh/1000).toFixed(2)} kWh`;
    }
    if (d.active_transaction_id) {
      activeTxIds[id] = d.active_transaction_id;
      const txEl = document.getElementById(`tx-${id}`);
      if (txEl) { txEl.textContent = `Session active — Transaction #${d.active_transaction_id}`; txEl.className = 'tx-info active'; }
    }
  } catch (e) { console.warn("[OCPP refresh]", e); }
}
document.querySelectorAll('.charger-card').forEach(c => {
  const id = c.id.replace('card-','');
  loadStats(id);
  fetch(`/api/chargers/${id}`).then(r=>r.json()).then(d => {
    chargerDefaults[id] = { boot_lock: d.boot_lock, max_amps: d.default_max_amps };
  }).catch(()=>{});
});
setInterval(() => {
  document.querySelectorAll('.charger-card').forEach(c => loadStats(c.id.replace('card-','')));
}, 30000);

// ═══════════════════════════════════════════════════════
// Poll live (filet de sécurité quand un broadcast WS est manqué)
// Reconcilie l'état Connecté / Offline + badge + heartbeat sans
// recharger la page. Détecte aussi les nouvelles bornes (reload).
// ═══════════════════════════════════════════════════════
let _knownIds = new Set(
  Array.from(document.querySelectorAll('.charger-card')).map(c => c.id.replace('card-',''))
);
// Tolerance heartbeat fallback : si une borne a un heartbeat recent (< 90s)
// mais n'est pas dans le set 'connected' in-memory du serveur (race condition
// reconnexion WS apres broadcast manque), on l'affiche live quand meme.
// Evite l'affichage 'Offline' transitoire qui inquietait l'usager.
const HEARTBEAT_LIVE_TOLERANCE_MS = 90 * 1000;
function _heartbeatRecent(isoTs) {
  if (!isoTs) return false;
  const t = Date.parse(isoTs);
  if (Number.isNaN(t)) return false;
  return (Date.now() - t) < HEARTBEAT_LIVE_TOLERANCE_MS;
}

async function refreshLiveState() { // refreshLiveState_patched
  try {
    const r = await fetch('/api/chargers/_live', { cache: 'no-store' });
    if (!r.ok) {
      console.warn('[dashboard] /_live HTTP', r.status);
      return;
    }
    const d = await r.json();
    const liveSet = new Set(d.connected || []);

    // Détecter une nouvelle borne enregistrée → reload pour afficher la carte
    const dbIds = new Set((d.chargers || []).map(c => c.id));
    for (const id of dbIds) {
      if (!_knownIds.has(id)) { location.reload(); return; }
    }

    // Compteur KPI = WS connectes + heartbeat recent (fallback anti-race).
    let liveCount = liveSet.size;
    (d.chargers || []).forEach(c => {
      if (!liveSet.has(c.id) && _heartbeatRecent(c.last_heartbeat)) liveCount++;
    });
    const sc = document.getElementById('s-conn');
    if (sc) sc.textContent = liveCount;

    // Reconciliation par carte
    (d.chargers || []).forEach(c => {
      const card = document.getElementById(`card-${c.id}`);
      if (!card) return;
      // isLive = WS active OU heartbeat recent (filet anti-race reconnect).
      const isLive = liveSet.has(c.id) || _heartbeatRecent(c.last_heartbeat);
      setConnected(c.id, isLive);
      const badge = document.getElementById(`status-${c.id}`);
      if (badge) {
        const txt = isLive ? c.status : 'Offline';
        if (badge.textContent !== txt) {
          badge.textContent = txt;
          badge.className = `badge b-${txt.toLowerCase()}`;
        }
      }
      if (c.last_heartbeat) {
        const hb = document.getElementById(`hb-${c.id}`);
        if (hb) {
          const t = new Date(c.last_heartbeat).toLocaleTimeString('fr-CA');
          hb.textContent = `Heartbeat : ${t}`;
        }
      }
    });
  } catch (e) {
    // Plus de catch silencieux : on log pour diagnostiquer si setInterval
    // s'interrompt sur une exception (cause probable de l'affichage fige).
    console.error('[dashboard] refreshLiveState exception:', e);
  }
}
refreshLiveState();
setInterval(async () => {
  try { await refreshLiveState(); }
  catch (err) { console.warn("[OCPP refresh interval]", err); }
}, 5000);

// ═══════════════════════════════════════════════════════
// Helpers API
// ═══════════════════════════════════════════════════════
async function api(url, body) {
  const r = await fetch(url, { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body) });
  const d = await r.json();
  return { ok: r.ok, data: d };
}

// ═══════════════════════════════════════════════════════
// Démarrer
// ═══════════════════════════════════════════════════════
function openStartModal(id) {
  document.getElementById('start-cid').value = id;
  document.getElementById('start-cid-lbl').value = id;
  document.getElementById('start-amps').value = '';
  const def = chargerDefaults[id];
  const hint = document.getElementById('start-amps-hint');
  hint.textContent = def?.max_amps
    ? `Défaut configuré : ${def.max_amps} A. Laisser vide pour utiliser cette valeur.`
    : 'Aucun courant par défaut — la borne utilisera son maximum si ce champ est vide.';
  openModal('start-modal');
}
async function submitStart() {
  const id   = document.getElementById('start-cid').value;
  const conn = parseInt(document.getElementById('start-conn').value);
  const tag  = document.getElementById('start-tag').value || 'DASHBOARD';
  const amps = document.getElementById('start-amps').value;
  const body = { charger_id:id, connector_id:conn, id_tag:tag };
  if (amps) body.max_amps = parseFloat(amps);
  const { ok, data } = await api('/api/commands/remote-start', body);
  closeModals();
  toast(ok ? `✓ ${data.detail}` : `✗ ${data.detail}`, ok);
}

// ═══════════════════════════════════════════════════════
// Arrêter
// ═══════════════════════════════════════════════════════
function openStopModal(id) {
  document.getElementById('stop-cid').value = id;
  document.getElementById('stop-cid-lbl').value = id;
  document.getElementById('stop-txid').value = activeTxIds[id] || '';
  const hint = document.getElementById('stop-tx-hint');
  hint.textContent = activeTxIds[id]
    ? `Transaction active détectée : #${activeTxIds[id]}`
    : 'Cliquez sur Détecter pour trouver la session active.';
  hint.style.color = '';
  openModal('stop-modal');
}
async function detectTxId() {
  const id = document.getElementById('stop-cid').value;
  const hint = document.getElementById('stop-tx-hint');
  hint.textContent = 'Recherche en cours…'; hint.style.color = '';
  try {
    const r = await fetch(`/api/commands/active-transaction/${id}`);
    const d = await r.json();
    if (d.transaction_id) {
      document.getElementById('stop-txid').value = d.transaction_id;
      activeTxIds[id] = d.transaction_id;
      hint.textContent = `✓ Transaction #${d.transaction_id} — démarrée à ${new Date(d.start_time).toLocaleTimeString('fr-CA')} — badge: ${d.id_tag}`;
      hint.style.color = '#86efac';
    } else {
      hint.textContent = '✗ Aucune session active trouvée.';
      hint.style.color = '#fca5a5';
    }
  } catch(e) { hint.textContent = '✗ Erreur lors de la détection.'; hint.style.color = '#fca5a5'; }
}
async function submitStop() {
  const id   = document.getElementById('stop-cid').value;
  const txid = parseInt(document.getElementById('stop-txid').value);
  const lock = document.getElementById('stop-lock').value === 'true';
  if (!txid) { toast('✗ Transaction ID requis — cliquez sur Détecter', false); return; }
  const { ok, data } = await api('/api/commands/remote-stop', { charger_id:id, transaction_id:txid, lock });
  closeModals();
  toast(ok ? `✓ ${data.detail}` : `✗ ${data.detail}`, ok);
}

// ═══════════════════════════════════════════════════════
// Couper / Rétablir
// ═══════════════════════════════════════════════════════
function openCutModal(id) {
  document.getElementById('cut-cid').value = id;
  document.getElementById('cut-cid-lbl').value = id;
  openModal('cut-modal');
}
async function submitCut() {
  const id = document.getElementById('cut-cid').value;
  const { ok, data } = await api('/api/commands/set-power-limit', { charger_id:id, connector_id:0, max_amps:0 });
  closeModals();
  toast(ok ? `✓ ${data.detail}` : `✗ ${data.detail}`, ok);
  if (ok) document.getElementById(`powerbox-${id}`)?.classList.add('power-cut');
}
function openRestoreModal(id) {
  document.getElementById('restore-cid').value = id;
  document.getElementById('restore-cid-lbl').value = id;
  const def = chargerDefaults[id];
  document.getElementById('restore-amps').value = def?.max_amps || 24;
  document.getElementById('restore-amps-hint').textContent = def?.max_amps
    ? `Courant par défaut de cette borne : ${def.max_amps} A`
    : 'Aucun défaut configuré — entrez le courant désiré.';
  openModal('restore-modal');
}
async function submitRestore() {
  const id   = document.getElementById('restore-cid').value;
  const amps = parseFloat(document.getElementById('restore-amps').value);
  if (!amps || amps <= 0) { toast('✗ Courant invalide', false); return; }
  const { ok, data } = await api('/api/commands/set-power-limit', { charger_id:id, connector_id:0, max_amps:amps });
  closeModals();
  toast(ok ? `✓ ${data.detail}` : `✗ ${data.detail}`, ok);
  if (ok) document.getElementById(`powerbox-${id}`)?.classList.remove('power-cut');
}

// ═══════════════════════════════════════════════════════
// Disponibilité / Unlock / Reset
// ═══════════════════════════════════════════════════════
async function setAvail(id, avail) {
  const url = avail ? '/api/commands/set-available' : '/api/commands/set-unavailable';
  const { ok, data } = await api(url, { charger_id:id, connector_id:1 });
  toast(ok ? `✓ ${data.detail}` : `✗ ${data.detail}`, ok);
}
async function unlock(id) {
  const { ok, data } = await api('/api/commands/unlock', { charger_id:id, connector_id:1 });
  toast(ok ? `✓ ${data.detail}` : `✗ ${data.detail}`, ok);
}
async function doReset(id) {
  if (!confirm(`Reset Soft de la borne ${id} ?`)) return;
  const { ok, data } = await api('/api/commands/reset', { charger_id:id, reset_type:'Soft' });
  toast(ok ? `✓ ${data.detail}` : `✗ ${data.detail}`, ok);
}
async function doHardReset() {
  const id = document.getElementById('cfg-cid').value;
  if (!confirm(`HARD RESET de ${id} ?\nCette opération est forcée et peut interrompre une session active.`)) return;
  const { ok, data } = await api('/api/commands/reset', { charger_id:id, reset_type:'Hard' });
  toast(ok ? `✓ ${data.detail}` : `✗ ${data.detail}`, ok);
}

// ═══════════════════════════════════════════════════════
// Configuration — Modal
// ═══════════════════════════════════════════════════════
// Stockage de l'override vendor par borne (clé = charger_id)
const _vendorOverrides = {};

function openCfgModal(id, vendor, model) {
  document.getElementById('cfg-cid').value           = id;
  document.getElementById('cfg-cid-lbl').textContent  = id;
  document.getElementById('cfg-vendor').value         = vendor || '';
  document.getElementById('cfg-model').value          = model  || '';
  _cfgVendor = vendor || '';
  _cfgData   = {};
  document.getElementById('cfg-count-lbl').textContent = '';
  document.getElementById('cfg-search').value = '';
  document.getElementById('cfg-tbody').innerHTML =
    '<tr><td colspan="7" style="color:#64748b;padding:14px">Cliquez sur <strong>Récupérer config</strong> pour charger la configuration.</td></tr>';

  // Peupler le sélecteur vendor
  const sel = document.getElementById('cfg-vendor-override');
  sel.innerHTML = OCPP_DB.vendorLabels.map(({key, label}) =>
    `<option value="${key}">${label}</option>`
  ).join('');

  // Sélectionner : override mémorisé > auto-détect depuis manufacturer
  const savedOverride = _vendorOverrides[id];
  if (savedOverride !== undefined) {
    sel.value = savedOverride;
  } else {
    // Auto-détect depuis le champ manufacturer
    const autoDetected = OCPP_DB.normalizeVendor(vendor);
    sel.value = autoDetected || '';
  }

  updateVendorHint();
  switchTab('cfg-read');
  openModal('cfg-modal');
}

function updateVendorHint() {
  const sel     = document.getElementById('cfg-vendor-override');
  const hint    = document.getElementById('cfg-vendor-hint');
  const rawVendor = document.getElementById('cfg-vendor').value;
  const autoNorm  = OCPP_DB.normalizeVendor(rawVendor);
  if (!rawVendor) {
    hint.textContent = 'Fabricant non renseigné dans la borne';
    hint.style.color = '#f59e0b';
  } else if (!autoNorm) {
    hint.textContent = `Auto-détect échoué pour "${rawVendor}" — sélectionnez manuellement`;
    hint.style.color = '#f59e0b';
  } else if (sel.value !== autoNorm && sel.value !== '') {
    hint.textContent = 'Override manuel actif';
    hint.style.color = '#a78bfa';
  } else {
    hint.textContent = `Détecté : ${rawVendor}`;
    hint.style.color = '#4ade80';
  }
}

function onVendorOverride() {
  const id  = document.getElementById('cfg-cid').value;
  const sel = document.getElementById('cfg-vendor-override');
  _vendorOverrides[id] = sel.value;
  updateVendorHint();
  // Si des données sont déjà chargées, re-rendre avec le nouveau mapping
  if (Object.keys(_cfgData).length > 0) renderCfgTable(_cfgData);
}

function getActiveVendor() {
  const id  = document.getElementById('cfg-cid').value;
  const sel = document.getElementById('cfg-vendor-override');
  // Override mémorisé ou valeur du sélecteur
  return sel ? sel.value : (_vendorOverrides[id] ?? OCPP_DB.normalizeVendor(_cfgVendor));
}

async function loadConfig() {
  const id = document.getElementById('cfg-cid').value;
  const tbody = document.getElementById('cfg-tbody');
  tbody.innerHTML = '<tr><td colspan="7" style="color:#64748b;padding:14px">⏳ Chargement en cours…</td></tr>';
  document.getElementById('cfg-count-lbl').textContent = '';
  const r = await fetch('/api/commands/config/get', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({ charger_id:id, keys:[] })
  });
  const d = await r.json();
  if (r.ok) {
    _cfgData = d.configuration || {};
    renderCfgTable(_cfgData);
    const n = Object.keys(_cfgData).length;
    document.getElementById('cfg-count-lbl').textContent = `${n} paramètre${n>1?'s':''} chargé${n>1?'s':''}`;
  } else {
    tbody.innerHTML = `<tr><td colspan="7" style="color:#f87171;padding:14px">✗ ${d.detail||'Erreur'}</td></tr>`;
  }
}

// ═══════════════════════════════════════════════════════
// Rendu du tableau config
// ═══════════════════════════════════════════════════════
function renderCfgTable(cfg) {
  const tbody = document.getElementById('cfg-tbody');
  if (!cfg || !Object.keys(cfg).length) {
    tbody.innerHTML = '<tr><td colspan="7" style="color:#64748b;padding:14px">Aucune configuration disponible.</td></tr>';
    return;
  }
  const vendor = getActiveVendor();

  tbody.innerHTML = Object.entries(cfg)
    .sort(([a],[b]) => {
      const na=parseInt(a), nb=parseInt(b);
      return (!isNaN(na)&&!isNaN(nb)) ? na-nb : a.localeCompare(b);
    })
    .map(([k,v]) => {
      const val   = typeof v === 'object' ? (v.value ?? '') : String(v ?? '');
      const ro    = typeof v === 'object' ? !!v.readonly : false;
      const info  = OCPP_DB.resolve(k, vendor);
      const ocppTxt = info ? info.ocpp : '—';
      const descTxt = info ? info.desc  : '—';
      const noteTxt = info?.note || '';
      const hint    = info ? buildHint(info) : '—';
      const valClass= `cfg-val${ro?' ro':''}`;
      const click   = ro ? '' : `onclick="startEdit('${ea(k)}',this)"`;
      const titleAttr= ro ? '🔒 Lecture seule — non modifiable' : '✏️ Cliquez pour modifier';
      const valDisp  = val === '' ? '<span style="color:#475569;font-style:italic">vide</span>' : eh(val);

      return `<tr id="cfgr-${ea(k)}" data-key="${ea(k)}" data-val="${ea(val)}">
        <td class="cfg-key" title="${eh(k)}">${eh(k)}</td>
        <td class="${valClass}" id="cfgv-${ea(k)}" ${click} title="${titleAttr}">${valDisp}</td>
        <td class="cfg-ro-icon">${ro?'🔒':''}</td>
        <td class="cfg-ocpp" title="${eh(ocppTxt)}">${eh(ocppTxt)}</td>
        <td class="cfg-desc">${eh(descTxt)}${noteTxt?`<br><span style="color:#f59e0b;font-size:.65rem">⚠ ${eh(noteTxt)}</span>`:''}</td>
        <td class="cfg-hint">${hint}</td>
        <td class="cfg-stat" id="cfgs-${ea(k)}"></td>
      </tr>`;
    }).join('');
}

// ═══════════════════════════════════════════════════════
// Hint HTML (valeurs acceptées)
// ═══════════════════════════════════════════════════════
function buildHint(info) {
  if (!info) return '—';
  const c = s => `<code style="background:#0f172a;padding:1px 4px;border-radius:3px;margin:1px;display:inline-block;font-size:.65rem">${eh(s)}</code>`;
  if (info.type === 'bool')
    return `${c('true')} ${c('false')}`;
  if ((info.type === 'enum') && info.opts)
    return info.opts.map(c).join(' ');
  if ((info.type === 'csv' || info.type === 'csv-ro') && info.opts)
    return info.opts.map(o => `<span style="display:inline-block;background:#0f172a;padding:1px 4px;border-radius:3px;font-size:.63rem;margin:1px;color:#94a3b8">${eh(o)}</span>`).join(' ');
  if (info.range) {
    let h = `<span style="color:#a78bfa">${eh(info.range)}</span>`;
    if (info.unit) h += ` <span style="color:#64748b;font-size:.63rem">${eh(info.unit)}</span>`;
    if (info.ex)   h += `<br><span style="color:#475569">ex: ${eh(info.ex)}</span>`;
    return h;
  }
  if (info.ex) return `<span style="color:#475569">ex: ${eh(info.ex)}</span>`;
  return '—';
}

// ═══════════════════════════════════════════════════════
// Édition inline
// ═══════════════════════════════════════════════════════
function startEdit(key, cell) {
  // Annuler une édition précédente
  document.querySelectorAll('.inline-editor').forEach(el => {
    const k = el.dataset.key;
    if (k && k !== key) cancelEdit(k);
  });

  const row    = document.getElementById(`cfgr-${key}`);
  const curVal = row.dataset.val;
  const info   = OCPP_DB.resolve(key, getActiveVendor());

  let inp = '';
  if (info?.type === 'bool') {
    inp = `<select id="ie-${key}">
      <option value="true"  ${curVal==='true' ?'selected':''}>true</option>
      <option value="false" ${curVal==='false'?'selected':''}>false</option>
    </select>`;
  } else if (info?.type === 'enum' && info.opts) {
    inp = `<select id="ie-${key}">
      ${info.opts.map(o=>`<option value="${ea(o)}" ${curVal===o?'selected':''}>${eh(o)}</option>`).join('')}
    </select>`;
  } else if (info?.type === 'csv' && info.opts) {
    const cur = curVal ? curVal.split(',').map(s=>s.trim()).filter(Boolean) : [];
    inp = `<div style="min-width:160px">
      <input type="hidden" id="ie-${key}" value="${ea(curVal)}">
      <div class="csv-chips" id="ie-chips-${key}">
        ${info.opts.map(o=>`<span class="csv-chip${cur.includes(o)?' on':''}"
          onclick="toggleChip('${ea(key)}','${ea(o)}')" data-v="${ea(o)}">${eh(o)}</span>`).join('')}
      </div>
    </div>`;
  } else if (info?.type === 'int' && info.range) {
    const [mn,mx] = info.range.split('–').map(Number);
    inp = `<input type="number" id="ie-${key}" value="${ea(curVal)}" min="${mn}" max="${mx}" placeholder="${info.ex||''}" style="width:85px">`;
  } else {
    inp = `<input type="text" id="ie-${key}" value="${ea(curVal)}" placeholder="${info?.ex||''}" style="width:130px">`;
  }

  cell.innerHTML = `<div class="inline-editor" data-key="${key}" onclick="event.stopPropagation()">
    ${inp}
    <button class="ie-save"   onclick="event.stopPropagation(); saveEdit('${key}')">✓</button>
    <button class="ie-cancel" onclick="event.stopPropagation(); cancelEdit('${key}')">✗</button>
  </div>`;

  // Focus automatique
  const el = document.getElementById(`ie-${key}`);
  if (el && el.tagName !== 'DIV') { el.focus(); if(el.select) el.select(); }
  // Enter pour sauvegarder, Escape pour annuler
  cell.onkeydown = (e) => {
    if (e.key === 'Enter')  { e.preventDefault(); saveEdit(key); }
    if (e.key === 'Escape') { e.preventDefault(); cancelEdit(key); }
  };
}

function toggleChip(key, val) {
  const inp  = document.getElementById(`ie-${key}`);
  const chip = document.querySelector(`#ie-chips-${key} [data-v="${val}"]`);
  const cur  = inp.value ? inp.value.split(',').map(s=>s.trim()).filter(Boolean) : [];
  const idx  = cur.indexOf(val);
  if (idx === -1) cur.push(val); else cur.splice(idx,1);
  inp.value = cur.join(',');
  chip.classList.toggle('on', idx === -1);
}

function cancelEdit(key) {
  const cell = document.getElementById(`cfgv-${key}`);
  const row  = document.getElementById(`cfgr-${key}`);
  if (!cell || !row) return;
  const val = row.dataset.val;
  const ro  = cell.classList.contains('ro');
  cell.innerHTML = val === '' ? '<span style="color:#475569;font-style:italic">vide</span>' : eh(val);
  if (!ro) cell.onclick = () => startEdit(key, cell);
  cell.onkeydown = null;
}

async function saveEdit(key) {
  const id    = document.getElementById('cfg-cid').value;
  const inp   = document.getElementById(`ie-${key}`);
  if (!inp) return;
  const newVal = inp.value.trim();
  const cell   = document.getElementById(`cfgv-${key}`);
  const stat   = document.getElementById(`cfgs-${key}`);
  const row    = document.getElementById(`cfgr-${key}`);

  stat.innerHTML = '<span class="sbadge pending">⏳ Envoi…</span>';
  cell.innerHTML  = '<span style="color:#64748b;font-size:.72rem">⏳</span>';

  try {
    const r = await fetch('/api/commands/config/set', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ charger_id:id, key, value:newVal })
    });
    const d = await r.json();

    // Le backend retourne HTTP 400 pour Rejected/NotSupported/Failed
    // et HTTP 200 pour Accepted/RebootRequired.
    // On résout le statut réel depuis les deux cas.
    let cls, lbl;
    if (r.ok) {
      const st = (d.status||'accepted').toLowerCase().replace(/\s+/g,'');
      if      (st === 'rebootrequired') { cls='rebootrequired'; lbl='↺ Reboot requis'; }
      else                              { cls='accepted';       lbl='✓ Accepté'; }
    } else {
      // Lire la raison dans le detail du 400
      const detail = (d.detail||'').toLowerCase();
      if      (detail.includes('rejet') || detail.includes('reject') || detail.includes('read-only')) {
        cls='rejected';     lbl='✗ Rejeté (read-only?)';
      } else if (detail.includes('support')) {
        cls='notsupported'; lbl='⚠ Non supporté';
      } else {
        cls='rejected';     lbl=`✗ ${d.detail||'Erreur'}`;
      }
    }

    stat.innerHTML = `<span class="sbadge ${cls}">${lbl}</span>`;

    const ok = cls==='accepted' || cls==='rebootrequired';
    const displayVal = ok ? newVal : row.dataset.val;
    cell.innerHTML = displayVal==='' ? '<span style="color:#475569;font-style:italic">vide</span>' : eh(displayVal);
    cell.onclick   = () => startEdit(key, cell);
    cell.onkeydown = null;
    if (ok) {
      row.dataset.val = newVal;
      _cfgData[key] = typeof _cfgData[key]==='object' ? {..._cfgData[key], value:newVal} : newVal;
    }
    // Toast bien visible en bas de l'écran
    toast(`${lbl} — clé ${key}`, ok);
    setTimeout(() => { stat.innerHTML=''; }, 9000);

  } catch(e) {
    stat.innerHTML = '<span class="sbadge rejected">✗ Erreur JS</span>';
    const old = row.dataset.val;
    cell.innerHTML = old==='' ? '<span style="color:#475569;font-style:italic">vide</span>' : eh(old);
    cell.onclick   = () => startEdit(key, cell);
    cell.onkeydown = null;
    toast(`✗ Erreur JavaScript: ${e.message}`, false);
    setTimeout(() => { stat.innerHTML=''; }, 9000);
  }
}

function filterCfgTable() {
  const q = document.getElementById('cfg-search').value.toLowerCase();
  document.querySelectorAll('#cfg-tbody tr[data-key]').forEach(row => {
    row.style.display = row.textContent.toLowerCase().includes(q) ? '' : 'none';
  });
}

// ── Modifier (onglet manuel) ───────────────────────────
async function setConfig() {
  const id  = document.getElementById('cfg-cid').value;
  const key = document.getElementById('cfg-key').value.trim();
  const val = document.getElementById('cfg-val-input').value.trim();
  const el  = document.getElementById('cfg-write-status');
  if (!key) { toast('✗ Clé requise', false); return; }
  el.innerHTML = '<span style="color:#38bdf8">⏳ Envoi…</span>';
  try {
    const r = await fetch('/api/commands/config/set', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ charger_id:id, key, value:val })
    });
    const d = await r.json();
    const st = d.status||(r.ok?'Accepted':'Error');
    const sl = st.toLowerCase().replace(/\s+/g,'');
    const color = sl==='rejected'?'#f87171':sl==='notsupported'?'#fb923c':sl==='rebootrequired'?'#c4b5fd':'#4ade80';
    const icon  = sl==='rejected'?'✗':sl==='notsupported'?'⚠':sl==='rebootrequired'?'↺':'✓';
    el.innerHTML = `<span style="color:${color}">${icon} ${st}${d.detail?' — '+d.detail:''}</span>`;
    toast(`${icon} ChangeConfiguration: ${st}`, sl==='accepted'||sl==='rebootrequired');
  } catch(e) {
    el.innerHTML = '<span style="color:#f87171">✗ Erreur réseau</span>';
    toast('✗ Erreur réseau', false);
  }
}

// ── Trigger / Cache / LocalList ────────────────────────
async function doTrigger(msg) {
  const id = document.getElementById('cfg-cid').value;
  const { ok, data } = await api('/api/commands/trigger', { charger_id:id, message:msg, connector_id:1 });
  toast(ok ? `✓ ${data.detail}` : `✗ ${data.detail}`, ok);
}
async function doClearCache() {
  const id = document.getElementById('cfg-cid').value;
  const { ok, data } = await api('/api/commands/clear-cache', { charger_id:id });
  toast(ok ? `✓ ${data.detail}` : `✗ ${data.detail}`, ok);
}
async function doGetLocalListVersion() {
  const id = document.getElementById('cfg-cid').value;
  const r  = await fetch(`/api/commands/local-list/version/${id}`);
  const d  = await r.json();
  toast(r.ok ? `📋 Version liste locale : ${d.list_version}` : `✗ ${d.detail}`, r.ok);
}

// ═══════════════════════════════════════════════════════
// Éditer borne
// ═══════════════════════════════════════════════════════
function openEditModal(id, bootLock, maxAmps) {
  document.getElementById('edit-cid').value = id;
  document.getElementById('edit-cid-lbl').value = id;
  document.getElementById('edit-boot-lock').value = bootLock;
  document.getElementById('edit-max-amps').value = maxAmps;
  document.getElementById('edit-desc').value = '';
  openModal('edit-modal');
}
async function submitEdit() {
  const id   = document.getElementById('edit-cid').value;
  const lock = document.getElementById('edit-boot-lock').value === 'true';
  const amps = document.getElementById('edit-max-amps').value;
  const desc = document.getElementById('edit-desc').value;
  const body = { boot_lock: lock, default_max_amps: amps ? parseFloat(amps) : null };
  if (desc) body.description = desc;
  const r = await fetch(`/api/chargers/${id}`, {
    method:'PATCH', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)
  });
  const d = await r.json();
  if (r.ok) {
    closeModals();
    toast(`✓ Borne ${id} mise à jour`, true);
    chargerDefaults[id] = { boot_lock:lock, max_amps:amps?parseFloat(amps):null };
    setTimeout(() => location.reload(), 1200);
  } else {
    toast(`✗ ${d.detail||'Erreur'}`, false);
  }
}

// ═══════════════════════════════════════════════════════
// Ajouter borne
// ═══════════════════════════════════════════════════════
function updatePreview() {
  const id = document.getElementById('new-id').value.trim() || '[ID_BORNE]';
  document.getElementById('url-preview').textContent = `ws://${serverHost}:9000/ocpp/${id}`;
}
async function submitAdd() {
  const id = document.getElementById('new-id').value.trim();
  if (!id) { toast('✗ ID requis', false); return; }
  const amps = document.getElementById('new-max-amps').value;
  const { ok, data } = await api('/api/chargers/', {
    id,
    description:      document.getElementById('new-desc').value   || null,
    manufacturer:     document.getElementById('new-vendor').value || null,
    model:            document.getElementById('new-model').value  || null,
    auth_password:    document.getElementById('new-pass').value   || null,
    notes:            document.getElementById('new-notes').value  || null,
    boot_lock:        document.getElementById('new-boot-lock').value === 'true',
    default_max_amps: amps ? parseFloat(amps) : null,
  });
  if (ok) {
    closeModals();
    toast(`✓ Borne "${id}" ajoutée`, true);
    setTimeout(() => location.reload(), 1400);
  } else {
    toast(`✗ ${data.detail||'Erreur'}`, false);
  }
}

// ═══════════════════════════════════════════════════════
// UI helpers
// ═══════════════════════════════════════════════════════
function openModal(id)  { document.getElementById(id).classList.add('open'); }
function closeModals()  { document.querySelectorAll('.modal-bg').forEach(m => m.classList.remove('open')); }
document.querySelectorAll('.modal-bg').forEach(bg => {
  bg.addEventListener('click', e => { if (e.target === bg) closeModals(); });
});
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeModals(); });

function switchTab(id) {
  document.querySelectorAll('#cfg-modal .tab-panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('#cfg-modal .tab').forEach(t => t.classList.remove('active'));
  document.getElementById(id).classList.add('active');
  const idx = ['cfg-read','cfg-write','cfg-actions'].indexOf(id);
  document.querySelectorAll('#cfg-modal .tab')[idx]?.classList.add('active');
}

function toast(msg, ok) {
  const t = document.createElement('div');
  t.textContent = msg;
  t.className = 'toast';
  Object.assign(t.style, { background: ok ? '#166534' : '#7f1d1d', color: ok ? '#86efac' : '#fca5a5' });
  document.body.appendChild(t);
  setTimeout(() => { t.style.opacity = '0'; setTimeout(() => t.remove(), 400); }, 3500);
}

// Helpers sécurité HTML
function eh(s) {
  if (s==null) return '';
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
function ea(s) {
  if (s==null) return '';
  return String(s).replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}
