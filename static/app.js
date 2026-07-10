// ⚔ Tavolo — frontend
// Comunica con Flask via /api/* + SSE per /api/chat

'use strict';

let gameState = null;
let dndData = { species: [], classes: [], backgrounds: [], alignments: [], genders: [] };
let busy = false;
let dmOpen = false;
let dmSynced = false;     // briefing iniziale (messaggi salvati) già passato al DM
let dmSyncNotified = false;
let awaitingLogin = false; // cambio modello: in attesa di login alla pagina
let loginNotified = false; // avviso "fai login" già mostrato una volta
let lastDmMessage = '';   // ultimo singolo messaggio del DM
let lastDmTurn = [];      // tutti i messaggi del turno DM corrente (per "Ripeti")
let lastRoll = null;      // ultimo tiro fatto dal giocatore (riquadro dadi)
let rollQueue = [];       // tiri accodati mentre il DM era occupato
let pendingRollIdx = 0;   // indice del tiro umano selezionato (pillole multi-PG)

// ─── Cadenza azioni DM ──────────────────────────────────────────────
// Il DM, in un solo messaggio del giocatore, può rispondere con PIÙ azioni
// in sequenza (PG del party AI, mostri, PNG). Se arrivano tutte di fila è
// difficile capire cosa succede. Le accodiamo: mostriamo la PRIMA subito,
// le successive una alla volta col tasto "Prossima azione". Lo stato di
// gioco (mappa, dadi, schede) viene applicato passo passo, allineato alla
// bolla mostrata, non in anticipo.
let actionQueue = [];     // azioni DM in attesa di essere mostrate {text, state}
let awaitingReveal = false; // una bolla già mostrata: le prossime aspettano il tasto
let streamDone = false;   // lo streaming SSE è finito
let finalState = null;    // stato finale (evento done) da applicare a coda svuotata

// ─── Debug — log comunicazione frontend ⇄ server ────────────────────
const DEBUG = { net: [], max: 60, tab: 'net', timer: null };

// Intercetta window.fetch per registrare ogni richiesta verso il server.
(function installFetchLogger() {
  const orig = window.fetch.bind(window);
  window.fetch = async function (input, init) {
    const method = (init && init.method) || (input && input.method) || 'GET';
    const url = (typeof input === 'string') ? input : ((input && input.url) || String(input));
    const t0 = performance.now();
    const entry = {
      ts: new Date().toLocaleTimeString(),
      method, url,
      reqBody: (init && init.body) ? String(init.body).slice(0, 600) : '',
      status: 0, ms: 0, resBody: '', error: '',
    };
    try {
      const resp = await orig(input, init);
      entry.status = resp.status;
      entry.ms = Math.round(performance.now() - t0);
      const ct = resp.headers.get('content-type') || '';
      if (ct.includes('application/json')) {
        try { entry.resBody = (await resp.clone().text()).slice(0, 900); } catch (_) {}
      } else if (ct.includes('audio')) {
        entry.resBody = `[audio ${resp.headers.get('content-length') || '?'} byte]`;
      } else if (ct.includes('event-stream')) {
        entry.resBody = '[SSE stream]';
      } else {
        entry.resBody = `[${ct || 'risposta non-JSON'}]`;
      }
      return resp;
    } catch (e) {
      entry.error = e.message;
      entry.ms = Math.round(performance.now() - t0);
      throw e;
    } finally {
      DEBUG.net.push(entry);
      if (DEBUG.net.length > DEBUG.max) DEBUG.net.shift();
    }
  };
})();

// ─── TTS — voce umana Edge (server) con fallback browser ─────────────
const TTS = {
  enabled: localStorage.getItem('tts_enabled') === '1',
  voice: localStorage.getItem('tts_voice') || '',
  rate: localStorage.getItem('tts_rate') || '+0%',
  available: false,   // edge-tts disponibile lato server
  voices: [],
  audio: null,        // audio MP3 in riproduzione
  queue: [],          // testi in attesa di lettura (UNA voce alla volta)
  speaking: false,    // pump di lettura attiva
  _resolve: null,     // risolve la lettura corrente a fine/interruzione
};

async function ttsLoadVoices() {
  const sel = $('tts-voice');
  try {
    const r = await fetch('/api/tts/voices');
    const d = await r.json();
    TTS.available = !!d.available;
    TTS.voices = d.voices || [];
    if (!TTS.voice || !TTS.voices.some(v => v.id === TTS.voice)) {
      TTS.voice = d.default || (TTS.voices[0] && TTS.voices[0].id) || '';
    }
  } catch (_) {
    TTS.available = false;
  }
  if (!sel) return;
  sel.innerHTML = '';
  for (const v of TTS.voices) {
    const o = el('option', { text: v.name });
    o.value = v.id;
    if (v.id === TTS.voice) o.selected = true;
    sel.appendChild(o);
  }
  if (!TTS.available) {
    sel.appendChild(el('option', { text: 'edge-tts non installato' }));
    sel.disabled = true;
  }
}

// Ripulisce markdown e glifi per una lettura pulita.
function ttsPlainText(text) {
  return String(text || '')
    .replace(/\*\*([^*]+)\*\*/g, '$1')
    .replace(/\*([^*\n]+)\*/g, '$1')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/^#{1,4}\s+/gm, '')
    .replace(/\[([^\]]+)\]\([^\)]+\)/g, '$1')
    .replace(/🎲|⚔|✦|✗|☻|☺|★|┼|≈|▓|·|🔊|🔇|🐉|🧙|🤖|💾|🔄|🐞|🔁/g, '')
    .trim();
}

function ttsStop() {
  TTS.queue = [];
  if (TTS.audio) {
    try { TTS.audio.pause(); } catch (_) {}
    TTS.audio = null;
  }
  try { if ('speechSynthesis' in window) window.speechSynthesis.cancel(); } catch (_) {}
  // sblocca la lettura in corso così la pump non resta appesa
  if (TTS._resolve) { const f = TTS._resolve; TTS._resolve = null; f(); }
}

// Accoda `text` per la lettura. I messaggi vengono letti UNO ALLA VOLTA,
// mai sovrapposti. force=true ("Ripeti"): svuota la coda e legge subito.
function ttsSpeak(text, force) {
  if (!force && !TTS.enabled) return;
  const plain = ttsPlainText(text);
  if (!plain) return;
  if (force) ttsStop();          // interrompe e svuota la coda
  TTS.queue.push(plain);
  ttsPump();
}

// Pump: legge la coda in sequenza, una voce alla volta. Finché una
// lettura non termina, la successiva non parte → niente accavallamenti.
async function ttsPump() {
  if (TTS.speaking) return;
  TTS.speaking = true;
  try {
    while (TTS.queue.length) {
      await ttsSpeakOne(TTS.queue.shift());
    }
  } finally {
    TTS.speaking = false;
  }
}

// Legge UN testo. La Promise si risolve quando la lettura finisce (o
// viene interrotta da ttsStop), così la pump può passare al successivo.
function ttsSpeakOne(plain) {
  return new Promise((resolve) => {
    let done = false;
    const finish = () => {
      if (done) return;
      done = true;
      if (TTS._resolve === finish) TTS._resolve = null;
      resolve();
    };
    TTS._resolve = finish;
    (async () => {
      if (TTS.available) {
        try {
          const r = await fetch('/api/tts', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text: plain, voice: TTS.voice, rate: TTS.rate }),
          });
          if (!r.ok) throw new Error('HTTP ' + r.status);
          const blob = await r.blob();
          if (done) return;        // interrotto durante il download
          const audio = new Audio(URL.createObjectURL(blob));
          TTS.audio = audio;
          audio.onended = () => {
            URL.revokeObjectURL(audio.src);
            if (TTS.audio === audio) TTS.audio = null;
            finish();
          };
          audio.onerror = finish;
          await audio.play();
          return;
        } catch (e) {
          if (done) return;
          console.warn('Edge TTS non disponibile, uso voce del browser:', e);
        }
      }
      ttsSpeakBrowser(plain, finish);
    })();
  });
}

// Fallback: sintesi vocale del browser. `done` invocata a fine lettura.
// Converte il rate Edge ("+20%", "-10%", "+0%") in fattore moltiplicativo
// per la voce del browser (1.0 = normale). Limitato a [0.5, 2.0].
function ttsRateFactor() {
  const pct = parseInt(String(TTS.rate).replace('%', ''), 10) || 0;
  return Math.min(2, Math.max(0.5, 1 + pct / 100));
}

function ttsSpeakBrowser(plain, done) {
  if (!('speechSynthesis' in window)) { if (done) done(); return; }
  try { window.speechSynthesis.cancel(); } catch (_) {}
  const u = new SpeechSynthesisUtterance(plain);
  u.lang = 'it-IT';
  u.rate = ttsRateFactor();
  const it = window.speechSynthesis.getVoices()
    .find(v => (v.lang || '').toLowerCase().startsWith('it'));
  if (it) u.voice = it;
  u.onend = () => { if (done) done(); };
  u.onerror = () => { if (done) done(); };
  window.speechSynthesis.speak(u);
}

function ttsToggle() {
  TTS.enabled = !TTS.enabled;
  localStorage.setItem('tts_enabled', TTS.enabled ? '1' : '0');
  const btn = $('btn-tts');
  btn.textContent = TTS.enabled ? '🔊' : '🔇';
  btn.setAttribute('aria-pressed', TTS.enabled ? 'true' : 'false');
  if (!TTS.enabled) ttsStop();
}

// Rilegge l'INTERO ultimo turno del DM (anche con TTS disattivato): se il
// DM ha prodotto più follow-up nello stesso turno (es. esiti di tiri IA +
// nuova domanda al party), li legge tutti in coda. Fallback sull'ultimo
// singolo messaggio se per qualche motivo `lastDmTurn` è vuoto.
function ttsRepeat() {
  if (lastDmTurn.length > 0) {
    ttsStop();
    for (let i = 0; i < lastDmTurn.length; i++) {
      ttsSpeak(lastDmTurn[i], i === 0);
    }
    return;
  }
  if (lastDmMessage) {
    ttsSpeak(lastDmMessage, true);
    return;
  }
  addMsg('system', 'Nessun messaggio del DM da rileggere.');
}

// ────────────────────────────────────────────────────────────────────
// Utils
// ────────────────────────────────────────────────────────────────────

function $(id) { return document.getElementById(id); }
function el(tag, opts = {}) {
  const e = document.createElement(tag);
  if (opts.class) e.className = opts.class;
  if (opts.text) e.textContent = opts.text;
  if (opts.html) e.innerHTML = opts.html;
  if (opts.on) for (const k in opts.on) e.addEventListener(k, opts.on[k]);
  return e;
}

function escapeHtml(s) {
  if (s == null) return '';
  return String(s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

// Ripulisce il testo del DM PRIMA del render: toglie le righe vuote (il
// testo va compatto, senza buchi verticali) e la leggenda della mappa
// scritta in chat (è già mostrata nel pannello mappa → in chat è rumore).
function cleanDisplayText(text) {
  const lines = String(text || '').split('\n');
  const out = [];
  for (const raw of lines) {
    const line = raw.trim();
    if (!line) continue;                       // riga vuota → via
    // intestazione di legenda: "Legenda:" / "Leggenda" (anche con **, #, >)
    if (/^[*#>\s]*legg?enda\b/i.test(line)) continue;
    // riga di sole coppie "simbolo = etichetta" separate da , ; | (≥2 coppie):
    // è una legenda inline tipo "S = sentinella, @ = party, # = muro"
    const noMd = line.replace(/[*_`]/g, '').trim();
    if (/^[^\s=]{1,3}\s*=\s*[^=,;|]+([,;|]\s*[^\s=]{1,3}\s*=\s*[^=,;|]+)+$/.test(noMd)) continue;
    out.push(line);
  }
  return out.join('\n');
}

function renderMarkdown(text) {
  // mini-markdown safe: bold, italic, headings, lists, code, line breaks
  let h = escapeHtml(cleanDisplayText(text));
  h = h.replace(/^### (.+)$/gm, '<h3>$1</h3>');
  h = h.replace(/^## (.+)$/gm, '<h2>$1</h2>');
  h = h.replace(/^# (.+)$/gm, '<h1>$1</h1>');
  h = h.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  h = h.replace(/\*([^*\n]+)\*/g, '<em>$1</em>');
  h = h.replace(/`([^`]+)`/g, '<code>$1</code>');
  h = h.replace(/^[-*] (.+)$/gm, '<li>$1</li>');
  h = h.replace(/(<li>.*<\/li>\n?)+/g, m => '<ul>' + m + '</ul>');
  h = h.replace(/\n/g, '<br>');
  return h;
}

function openModal(name)  { $('modal-' + name).classList.remove('hidden'); }
function closeModal(name) { $('modal-' + name).classList.add('hidden'); }
window.openModal = openModal;
window.closeModal = closeModal;

// ────────────────────────────────────────────────────────────────────
// Stato/render
// ────────────────────────────────────────────────────────────────────

async function refreshState() {
  const r = await fetch('/api/state');
  gameState = await r.json();
  renderUI();
}

async function loadDndData() {
  const r = await fetch('/api/dnd_data');
  dndData = await r.json();
}

async function refreshWebchatStatus() {
  try {
    const r = await fetch('/api/webchat/status');
    const d = await r.json();
    dmOpen = !!d.open;
    $('dm-name').textContent = d.name || '—';
    $('dm-dot').classList.toggle('on', dmOpen);
    // Tooltip sul badge DM: ultima fase del webchat (cosa sta facendo
    // Chromium) senza dover aprire il pannello Debug.
    const last = d.last || {};
    $('dm-status').title = last.phase
      ? `${last.phase}${last.detail ? ' — ' + last.detail : ''}${last.error ? ' ⚠ ' + last.error : ''}`
      : '';

    // Verità sul briefing arriva SEMPRE dal server: se durante una nuova
    // avventura precaricata il server ha resettato `is_briefed()` perché
    // un re-briefing è in volo, il frontend deve scoprirlo (e ridisabilitare
    // l'input) — quindi non basta accendere dmSynced una volta sola.
    dmSynced = !!d.briefed;
    let syncPlaceholder = '';
    if (dmOpen && !dmSynced) {
      const s = await fetch('/api/webchat/sync', { method: 'POST' })
        .then(x => x.json()).catch(() => ({}));
      if (s.status === 'ready' || s.status === 'empty') {
        dmSynced = true;
        awaitingLogin = false;
      } else if (s.status === 'awaiting_login') {
        // Cambio modello: la richiesta a Chromium è SOSPESA finché non fai
        // login nella pagina del modello. Riparte da sola al polling dopo.
        awaitingLogin = true;
        syncPlaceholder = '🔑 Esegui il login nella pagina del modello…';
        if (!loginNotified) {
          loginNotified = true;
          addMsg('system', '🔑 Modello cambiato: comunicazione sospesa. '
            + 'Esegui il login nella finestra del modello (Chromium). '
            + 'Appena la chat è pronta riprendo da solo.');
        }
      } else if (s.status === 'syncing' && !dmSyncNotified) {
        awaitingLogin = false;
        dmSyncNotified = true;
        addMsg('system', '⏳ DM collegato — allineo la partita salvata al '
          + 'modello. Attendi qualche secondo prima di scrivere.');
        // Il backend invia il briefing al DM in background e, alla risposta,
        // la appende a conversation_history. Polliamo finché compare il
        // messaggio di apertura/ripartenza e lo mostriamo come bolla DM:
        // così, ricevuto l'intero prompt, la risposta del DM finisce in chat
        // e si può cominciare a giocare. Fire-and-forget: non blocca il
        // polling di stato (dmSyncNotified evita doppi avvii).
        pollForOpeningScene({ timeoutMs: 360000, intervalMs: 4000 })
          .then(arrived => {
            if (!arrived) {
              addMsg('system', '⚠ Il DM non ha emesso un messaggio di '
                + 'apertura entro il timeout. Scrivi un\'azione per rilanciare.');
            }
          })
          .catch(() => {});
      }
    } else if (dmSynced) {
      // login confermato e briefing fatto: azzera i flag così un PROSSIMO
      // cambio modello li ri-arma e rinotifica.
      awaitingLogin = false;
      loginNotified = false;
      dmSyncNotified = false;
    }

    // Input chat abilitato SOLO quando il DM è aperto E il briefing
    // iniziale è andato a buon fine (dmSynced). Calcolato QUI, a flag
    // aggiornati: prima avveniva all'inizio (su dmSynced del giro
    // precedente) e l'abilitazione arrivava con ~5s di ritardo.
    const ready = dmOpen && dmSynced;
    $('chat-input').disabled = !ready;
    $('chat-send').disabled = !ready || busy;
    $('chat-input').placeholder = !dmOpen
      ? 'DM non collegato — apri il Setup'
      : !dmSynced
        ? (syncPlaceholder || 'Sincronizzazione con il DM in corso…')
        : 'Scrivi…';

    // Tiri IA risolti ma mai narrati (es. parcheggiati da un briefing di
    // ripresa): consegnali al DM da soli, la partita non deve congelarsi
    // in attesa che il giocatore scriva qualcosa.
    if (ready && !busy) maybeAutoContinue();
  } catch (e) { /* ignore */ }
}

// Esegue un sotto-render isolandone gli errori: se UNO fallisce (es. una
// scheda malformata in renderPlayers) NON deve impedire il rendering dei
// pannelli SEGUENTI — in particolare la MAPPA, che prima restava vuota
// quando un render precedente lanciava un'eccezione.
function _safeRender(label, fn) {
  try { fn(); }
  catch (e) { console.error('[render] ' + label + ' fallito:', e); }
}

function renderUI() {
  if (!gameState) return;
  $('phase-name').textContent = gameState.phase || 'setup';
  $('turn-num').textContent = gameState.turn || 0;
  $('round-num').textContent = gameState.round || 0;
  _safeRender('initiative', renderInitiative);
  _safeRender('players', renderPlayers);
  _safeRender('map', renderMap);
  _safeRender('rolls', renderRolls);
  _safeRender('diceBox', renderDiceBox);
  _safeRender('adventureBadge', renderAdventureBadge);
  _safeRender('openSheetModal', refreshOpenSheetModal);
  if (window.Music) _safeRender('music', () => Music.applyState(gameState));
}

// Pannello iniziativa: visibile solo in combat. Mostra la coda nell'ordine
// inviato dal DM via STATE_UPDATE.initiative_order = [{name, init}, ...] e
// evidenzia active_player.
function renderInitiative() {
  const panel = $('initiative-panel');
  if (!panel) return;
  const order = (gameState && gameState.initiative_order) || [];
  const active = (gameState && gameState.active_player) || '';
  const inCombat = !!(gameState && gameState.combat_active) && order.length > 0;
  panel.hidden = !inCombat;
  if (!inCombat) return;
  $('ini-round').textContent = `Round ${gameState.round || 0}`;
  const list = $('ini-list');
  list.innerHTML = '';
  for (const e of order) {
    const name = (e && e.name) || '?';
    const init = (e && (e.init != null ? e.init : e.initiative)) ?? '—';
    const isActive = active && active.toLowerCase() === String(name).toLowerCase();
    const player = (gameState.players || []).find(
      p => (p.name || '').toLowerCase() === String(name).toLowerCase());
    const isHuman = player && player.type === 'human';
    const li = el('li', {
      class: 'ini-row'
        + (isActive ? ' active' : '')
        + (isHuman ? ' human' : ' npc'),
    });
    li.innerHTML = `<span class="ini-icon">${isHuman ? '🧙' : '🤖'}</span>`
      + `<span class="ini-name">${escapeHtml(String(name))}</span>`
      + `<span class="ini-init">${init}</span>`;
    list.appendChild(li);
  }
}

// Se la modale scheda è aperta su un PG presente nello stato, ridisegna
// l'editor con i dati FRESCHI (HP, XP, slot, equipaggiamento…). Senza
// questa chiamata un <CHAR_UPDATE> del DM aggiornerebbe solo la card del
// pannello sinistro, lasciando la scheda dettagliata stale.
function refreshOpenSheetModal() {
  const modal = $('modal-sheet');
  if (!modal || modal.classList.contains('hidden')) return;
  const root = $('sheet-content');
  if (!root) return;
  // Il nome del PG corrente è nel data-attr del wrapper editor (settato da
  // buildSheetEditor) — è la nostra "ancora" per ritrovare il player giusto.
  const name = root.dataset.sheetName;
  if (!name) return;
  const p = (gameState.players || []).find(x => x.name === name);
  if (!p || !p.sheet) return;
  // Evita re-render mentre l'utente sta digitando in un input dell'editor.
  const ae = document.activeElement;
  if (ae && root.contains(ae) && /INPUT|TEXTAREA|SELECT/.test(ae.tagName)) return;
  root.innerHTML = '';
  root.appendChild(buildSheetEditor(p.sheet, name));
}

// ─── Incantesimi: riepilogo per la scheda nel pannello sinistra ──────

// Pip degli slot: ● = disponibile, ○ = usato.
function spellSlotPips(slots) {
  return Object.keys(slots || {}).sort((a, b) => a - b).map(lv => {
    const sl = slots[lv] || {};
    const max = Math.max(0, sl.max | 0);
    const used = Math.max(0, Math.min(max, sl.used | 0));
    let dots = '';
    for (let i = 0; i < max; i++) dots += (i < max - used) ? '●' : '○';
    return `<span class="slot-grp"><b>L${lv}</b><span class="slot-pips">${dots || '—'}</span></span>`;
  }).join('');
}

// Riga sintetica incantesimi nella card giocatore. '' se non incantatore.
function spellSummaryHtml(s) {
  if (!s || s.caster_type === 'none') return '';
  const sp = s.spells || {};
  const slots = sp.slots || {};
  const cc = (sp.cantrips || []).length;
  const kn = (sp.known || []).length;
  if (!Object.keys(slots).length && !cc && !kn) return '';
  const meta = [];
  if (cc) meta.push(`${cc} truc.`);
  if (kn) meta.push(`${kn} inc.`);
  const metaHtml = meta.length ? `<span class="pc-spell-meta">${meta.join(' · ')}</span>` : '';
  const pips = Object.keys(slots).length ? spellSlotPips(slots) : '';
  return `<div class="pc-spells">✦ ${metaHtml}${pips}</div>`;
}

// ─── Monete possedute — borsa del personaggio ───────────────────────
// Denominazioni: platino, oro, argento, rame.
const COIN_LABELS = { mp: 'MP', po: 'MO', ma: 'MA', mr: 'MR' };

// "2 MP · 561 MO · 8 MA" — solo le denominazioni con valore > 0.
function treasureText(t) {
  if (!t || typeof t !== 'object') return '0 MO';
  const parts = ['mp', 'po', 'ma', 'mr']
    .filter(k => (t[k] | 0) > 0)
    .map(k => `${t[k] | 0} ${COIN_LABELS[k]}`);
  return parts.length ? parts.join(' · ') : '0 MO';
}

function renderPlayers() {
  const root = $('players-list');
  const players = gameState.players || [];
  if (!players.length) {
    root.innerHTML = '<p class="empty">Nessun giocatore. Apri <button class="link" onclick="openModal(\'chars\')">Personaggi</button>.</p>';
    return;
  }
  root.innerHTML = '';
  for (const p of players) {
    const s = p.sheet || {};
    const hp = s.hp || { current: 0, max: 0 };
    const pct = hp.max ? Math.max(0, Math.min(100, (hp.current / hp.max) * 100)) : 0;
    const status = (s.status === 'dead') ? 'dead' : (s.status === 'down' ? 'down' : '');
    const active = gameState.active_player === p.name ? 'active' : '';

    const gsym = s.gender === 'Femmina' ? '♀' : (s.gender === 'Maschio' ? '♂' : '');

    const card = el('div', { class: `player-card ${status} ${active}` });
    card.innerHTML = `
      <div class="pc-head">
        <span class="pc-name">${escapeHtml(p.name)} ${gsym} ${p.type === 'ai' ? '🤖' : '🧙'}</span>
        <span class="pc-type">${escapeHtml(p.type)}</span>
      </div>
      <div class="pc-info">
        ${escapeHtml(s.species || s.race || '?')} ${escapeHtml(s.class || '?')} Lv${s.level || 1}
        · CA <span title="Totale: ${acTotal(s)}">${acDisplay(s)}</span>
        · <span class="pc-info-hp" title="Punti ferita correnti / massimi">❤ ${hp.current ?? 0}/${hp.max ?? 0}</span>
        · XP ${s.xp || 0}
      </div>
      <div class="pc-coins">💰 ${escapeHtml(treasureText(s.treasure))}</div>
      ${(s.magic_items && s.magic_items.length)
        ? `<div class="pc-magic" title="${escapeHtml((s.magic_items || []).map(itemName).join(', '))}">✨ ${s.magic_items.length} artefatt${s.magic_items.length === 1 ? 'o' : 'i'}</div>`
        : ''}
      <div class="pc-hp">
        <div class="pc-hp-bar" style="width:${pct}%"></div>
        <div class="pc-hp-text">${hp.current}/${hp.max} HP</div>
      </div>
      ${spellSummaryHtml(s)}
    `;
    card.addEventListener('click', () => showSheet(s, p.name));
    root.appendChild(card);
  }
}

// ── Mappa pixel-art: tabella di W×H quadratini, ogni quadratino è un
//    disegno 16×16. Palette FANTASY a 16 colori (indice esadecimale 0-f).
// La taglia (larghezza × altezza) NON è più fissa: la decide il DM in
// base alla scena dell'avventura, e arriva nel gameState.
// I default sprite restano disegnati a 10×10 e vengono scalati on-the-fly
// a 16×16 in fase di rendering (nearest-neighbor): il DM emette nuovi
// sprite nativamente a 16×16 (16 righe da 16 cifre esadecimali).
const SPRITE_PX = 16;    // pixel per lato di ogni cella renderizzata
const MAP_MIN_SIDE = 20;
const MAP_MAX_SIDE = 40;

// Larghezza/altezza correnti della mappa: legge da gameState; fallback su
// quanto effettivamente disegnato nella stringa ASCII.
function mapDims() {
  const m = (gameState && (gameState.map_ascii || gameState.map_full)) || '';
  const rows = m.split('\n').filter(r => r.length > 0);
  const h0 = (gameState && gameState.map_height) || rows.length || 0;
  const w0 = (gameState && gameState.map_width)
           || (rows.length ? Math.max(...rows.map(r => r.length)) : 0);
  const w = Math.max(MAP_MIN_SIDE, Math.min(MAP_MAX_SIDE, w0));
  const h = Math.max(MAP_MIN_SIDE, Math.min(MAP_MAX_SIDE, h0));
  return { w, h, rows };
}
// Palette a 16 colori: ombre calde, pietra, legno, fogliame, acqua,
// fiamme e oro. Indicizzata in esadecimale (0-f) come nei tag <SPRITE>.
const PALETTE = [
  [ 20,  16,  12],       // 0 nero-ombra
  [ 42,  31,  22],       // 1 bruno scuro
  [ 74,  53,  34],       // 2 pietra in ombra
  [110,  85,  54],       // 3 pietra
  [156, 128,  80],       // 4 pietra chiara
  [200, 168, 106],       // 5 oro-sabbia
  [ 74,  44,  20],       // 6 legno scuro
  [122,  74,  34],       // 7 legno / cuoio
  [ 46,  77,  34],       // 8 verde bosco
  [ 90, 140,  58],       // 9 verde fogliame
  [ 30,  58,  82],       // a acqua profonda
  [ 63, 125, 166],       // b acqua chiara
  [224, 123,  42],       // c fiamma arancio
  [242, 207,  82],       // d oro / fiamma viva
  [156,  40,  40],       // e rosso sangue
  [240, 228, 200],       // f pergamena / osso
];

// Sprite 10×10 di default (fallback): il DM le sovrascrive/aggiunge via
// tag <SPRITE> → gameState.sprites. Ogni riga = 10 cifre esadecimali
// della palette a 16 colori.
const DEFAULT_SPRITES = {
  ' ': ['0000000000','0000000000','0000000000','0000000000','0000000000',
        '0000000000','0000000000','0000000000','0000000000','0000000000'],
  '#': ['4444444444','4332332334','4332332334','1111111111','3324332433',
        '3324332433','1111111111','4332332334','4332332334','1111111111'],
  '.': ['3333333333','3343333233','3333333333','3333334333','2333333333',
        '3333333333','3333233333','3333333343','3333333333','3343333333'],
  '*': ['3333333333','3333333333','333ddd3333','33d555d333','33d555d333',
        '33d555d333','333ddd3333','3333333333','3333333333','3333333333'],
  'X': ['3333333333','3333dd3333','3333dd3333','33ddffdd33','3dffffffd3',
        '3dffffffd3','33ddffdd33','3333dd3333','3333dd3333','3333333333'],
  'C': ['e33333333e','3f333333f3','33f3333f33','333f33f333','3333ff3333',
        '3333ff3333','333d33d333','33d3333d33','3d333333d3','e33333333e'],
  'E': ['3333333333','333bbbb333','33b3333b33','3b333333b3','3b3f00f3b3',
        '3b3f00f3b3','33b3333b33','333bbbb333','3333b33333','33333b3333'],
  // S = PNG/abitante: figura umanoide con testa chiara e veste blu
  'S': ['3333553333','3333553333','3335ff5333','3335ff5333','333bbbb333',
        '33bbaabb33','33baabbab3','3333bb3333','3333663333','3333663333'],
  // + = porta in legno con anta a doghe e maniglia dorata
  '+': ['1111111111','1666666661','1677177761','1677177761','16771d7761',
        '16771d7761','1677177761','1677177761','1666666661','1111111111'],
  '<': ['3344444444','3344444444','2233333333','2233333333','1122222222',
        '1122222222','0011111111','0011111111','0000000000','0000000000'],
  '>': ['0000000000','0000000000','0011111111','0011111111','1122222222',
        '1122222222','2233333333','2233333333','3344444444','3344444444'],
  '~': ['aaaaaaaaaa','abbaabbaab','aabbaabbaa','aaaaaaaaaa','baabbaabba',
        'bbaabbaabb','aaaaaaaaaa','abbaabbaab','aabbaabbaa','aaaaaaaaaa'],
  'T': ['3333333333','3f33f33f33','3f33f33f33','3f33f33f33','4444444444',
        '3333333333','3f33f33f33','3f33f33f33','3f33f33f33','4444444444'],
  // t = albero frondoso: chioma verde fogliame con ombre + tronco bruno
  't': ['3338983333','3389999833','3899999983','3999999993','8999999998',
        '3899999983','3389999833','3338983333','3333773333','3333773333'],
  ',': ['3333333333','3393333333','3893333933','3899339983','3839938393',
        '3333333333','3333393333','3333898333','3338999833','3333333333'],
  'o': ['3333333333','3333333333','3334444333','3345554433','3455554443',
        '3445544443','3444444433','3334444333','3333333333','3333333333'],
  'f': ['1111111111','1111dd1111','111dccd111','11dccccd11','11dccccd11',
        '1dcccccdd1','1dccddccd1','11dccccd11','1677777761','1166666611'],
  '=': ['aaaaaaaaaa','6666666666','7777777777','6777777776','6777777776',
        '7777777777','6666666666','aabbaabbaa','abbaabbaab','aaaaaaaaaa'],
  // $ = forziere: cassa di legno con doghe scure, banda dorata e serratura nera
  '$': ['3333333333','3366666633','3611111163','6dddddddd6','6dd0000dd6',
        '6dd0dd0dd6','6dddddddd6','6111111116','6611111166','3666666663'],
  '@': ['3333333333','3333dd3333','3333dd3333','3335ff5333','3335ff5333',
        '3357777533','3377777733','3337777333','3337337333','3337337333'],
  // ── Mostri ──────────────────────────────────────────────────────────
  // M = mostro generico (orchetto/troll): testa massiccia verde scuro, occhi
  // rossi luminosi, zanne bianche, corpo robusto con gambe brune
  'M': ['3338998333','3389999833','3e89ee98e3','3389ff9833','3389999833',
        '3398888933','3988888893','3888888883','3370000733','3377007733'],
  // g = goblin: piccolo verde fogliame, orecchie a punta, occhio rosso singolo
  'g': ['3333333333','3338998333','3389998983','398e9e9893','3389ff9833',
        '3338998833','3338888333','3338888333','3337337333','3337337333'],
  // k = scheletro: teschio bianco con orbite scure, mandibola, costole
  'k': ['3333333333','333fffff33','33f0ff0f33','33fffffff3','33f0fff0f3',
        '333fffff33','333f0f0f33','3fffffff03','3f0f0f0f03','333f3f3333'],
  // D = drago: ali rosso sangue aperte, corpo squamato, occhio dorato,
  // collo che si stacca dalla silhouette principale
  'D': ['ee00000ee0','eee000eee0','3eeeeeee30','3eedeeee30','33eeeeee30',
        '333eeee300','3333ee3300','3333ee3300','333e33e300','3333333333'],
  // P = PG abbattuto a terra: sagoma sdraiata in toni grigio-bruno
  'P': ['3333333333','3333333333','3333333333','3311111113','3171171113',
        '3111111113','3111111113','3333333333','3666336663','3333333333'],
  // B = boss / nemico designato: silhouette imponente con corona rossa,
  // armatura nera con accenti dorati, occhi luminosi.
  'B': ['e00000000e','3eee00eee3','3eedeeeed3','3eeffffeee','3eef00feee',
        '3eeffffeee','3eeeeeeee3','33eeeeee33','3370000733','3377007733'],
  // N = PNG amico/quest-giver: figura con cappuccio chiaro, tunica
  // marrone, lieve luce dorata.
  'N': ['3333553333','333f55f333','3335ff5333','333fff5333','335fff5533',
        '3355ff5533','333dddd333','333d77d333','3337337333','3337337333'],
};

// ── Sprite del PARTY in base alla CLASSE del PG di turno ──────────────
// Sostituisce il tassello generico '@' con un'icona da RPG distintiva:
// guerriero con elmo+spada, mago con cappello+bastone, ladro incappucciato
// con pugnale, chierico con mazza, ranger con arco. Se la classe non è
// nota o non è categorizzabile, si usa l'eroe generico (sprite di default).
const CLASS_SPRITES = {
  warrior: [
    '0003333000','003fff4300','0034f44300','000333000d','0e333330d0',
    '0e3eee30d0','00e333d000','0003330000','0006600000','0006600000'],
  mage: [
    '000bb00000','00bbbb0000','00abbb0000','003fff7000','0033ff7d00',
    '03ffff7000','0aaaa07000','0aaaaa7000','00aaaa7000','000aa07000'],
  rogue: [
    '0001110000','00111f1100','0011f1f100','01111f1100','00111110d0',
    '00111111d0','0011110000','0001110000','0006600000','0006600000'],
  cleric: [
    '0003333000','003fdf4300','003ff4f300','000555000d','0055555000',
    '0055d550d0','0055d55070','0055555070','0006600700','0006600000'],
  ranger: [
    '0008880000','00888f8000','008f8f8000','008888d000','0078888d00',
    '00788871d0','0008880000','0008880000','0006600000','0006600000'],
};

function _classCategory(cls) {
  const s = String(cls || '').toLowerCase();
  if (!s) return 'hero';
  if (/guerrier|barbar|paladin/.test(s)) return 'warrior';
  if (/mago|streg|warlock/.test(s))      return 'mage';
  if (/ladro|bardo/.test(s))             return 'rogue';
  if (/chier|druid|monaco/.test(s))      return 'cleric';
  if (/ranger/.test(s))                  return 'ranger';
  return 'hero';
}

// Sprite da usare per il marker @ del party: deriva la classe dal PG di
// turno (active_player), oppure dal primo PG umano. Se il DM ha definito
// uno sprite '@' personalizzato in gameState.sprites, quello vince.
function _partySprite() {
  const players = (gameState && gameState.players) || [];
  let cls = '';
  const ap = gameState && gameState.active_player;
  if (ap) {
    const p = players.find(x => x.name === ap);
    if (p && p.sheet) cls = p.sheet.class || p.sheet['classe'] || '';
  }
  if (!cls) {
    const h = players.find(x => x.type === 'human') || players[0];
    if (h && h.sheet) cls = h.sheet.class || h.sheet['classe'] || '';
  }
  const cat = _classCategory(cls);
  return CLASS_SPRITES[cat] || DEFAULT_SPRITES['@'];
}

// Disegno 10×10 da usare per il carattere `ch`: prima le sprite del DM,
// poi quelle di default, infine il pavimento.
function spriteFor(ch) {
  const custom = (gameState && gameState.sprites) || {};
  return custom[ch] || DEFAULT_SPRITES[ch] || DEFAULT_SPRITES['.'];
}

// ── Pareti consapevoli dei vicini (stile GrandpaDangeon TileL/R/T/B) ──
// 16 varianti indicizzate da bitmask: T=1, R=2, B=4, L=8 (bit=1 → vicino
// è un muro, quindi quel lato è "chiuso" e non mostra il bordo).
function _wallBase() {
  return [
    [3,3,4,4,3,3,4,4,3,3],
    [3,3,2,2,3,3,2,2,3,3],
    [3,3,4,4,3,3,4,4,3,3],
    [1,1,1,1,1,1,1,1,1,1],
    [3,2,2,3,3,4,4,3,3,2],
    [3,3,4,4,3,3,2,2,3,3],
    [3,2,2,3,3,4,4,3,3,2],
    [1,1,1,1,1,1,1,1,1,1],
    [3,3,4,4,3,3,4,4,3,3],
    [3,3,2,2,3,3,2,2,3,3],
  ];
}
function _buildWallSprite(mask) {
  const g = _wallBase();
  const openT = !(mask & 1), openR = !(mask & 2);
  const openB = !(mask & 4), openL = !(mask & 8);
  if (openT) { for (let x=0;x<10;x++) g[0][x]=4; for (let x=0;x<10;x++) if (x&1) g[1][x]=4; }
  if (openB) { for (let x=0;x<10;x++) g[9][x]=1; for (let x=0;x<10;x++) if (x&1) g[8][x]=1; }
  if (openL) { for (let y=0;y<10;y++) g[y][0]=4; for (let y=0;y<10;y++) if (y&1) g[y][1]=4; }
  if (openR) { for (let y=0;y<10;y++) g[y][9]=2; for (let y=0;y<10;y++) if (y&1) g[y][8]=2; }
  if (openT && openL) g[0][0]=5;
  if (openT && openR) g[0][9]=5;
  if (openB && openL) g[9][0]=0;
  if (openB && openR) g[9][9]=0;
  return g.map(r=>r.map(v=>v.toString(16)).join(''));
}
const WALL_SPRITES = Array.from({length:16}, (_,m)=>_buildWallSprite(m));

// 3 varianti pavimento per togliere uniformità: scelta stabile per cella
const FLOOR_VARIANTS = [
  DEFAULT_SPRITES['.'],
  ['3343333333','3333334333','3433333333','3333343333','3343333343',
   '3333333333','3334333333','3333333343','3343333333','3333343333'],
  ['3333333343','3343333333','3333333334','3333334333','3433333333',
   '3343333333','3333334333','3333343333','3334333333','3333333334'],
];
// Pavimento con ombra del muro proiettata dall'alto (riga 0 più scura)
const FLOOR_SHADOW = (() => {
  const base = DEFAULT_SPRITES['.'];
  return base.map((r, i) => i === 0 ? '1111111111'
                          : i === 1 ? '2322322232'
                          : r);
})();
function _floorHash(x, y) { return ((x * 73856093) ^ (y * 19349663)) & 0xff; }

// Sprite finale per la cella (x,y). Custom DM > muro-vicini > pavimento
// con variante e ombra > sprite di default per il carattere > pavimento.
function spriteForCell(ch, rows, x, y) {
  const custom = (gameState && gameState.sprites) || {};
  if (custom[ch]) return custom[ch];
  // marker @ del party: scegli l'icona in base alla classe del PG di turno
  if (ch === '@') return _partySprite();
  if (ch === '#') {
    const isW = (xx, yy) => ((rows[yy] || '')[xx] || ' ') === '#';
    const m = (isW(x, y-1) ? 1 : 0) | (isW(x+1, y) ? 2 : 0)
            | (isW(x, y+1) ? 4 : 0) | (isW(x-1, y) ? 8 : 0);
    return WALL_SPRITES[m];
  }
  if (ch === '.') {
    const above = ((rows[y-1] || '')[x] || ' ');
    if (above === '#') return FLOOR_SHADOW;
    const idx = _floorHash(x, y) % FLOOR_VARIANTS.length;
    return FLOOR_VARIANTS[idx];
  }
  return DEFAULT_SPRITES[ch] || DEFAULT_SPRITES['.'];
}

// Disegna la mappa su un singolo <canvas> di W·16 × H·16 px (ogni cella
// è uno sprite 16×16; mappa minima 20×20 → almeno 320×320 px), scalato
// dal CSS con resa a pixel netti. Una putImageData per render.
// Disegna la mappa dentro `root` (vi crea/aggiorna un <canvas>).
// Usata sia dal pannello mappa sia dalla finestra ingrandita.
function paintMap(root) {
  // Fallback: se la vista con fog (map_ascii) manca ma esiste la mappa
  // completa (map_full), disegna quella — meglio la mappa senza nebbia che
  // un pannello vuoto quando per qualche motivo map_ascii non è stato
  // calcolato.
  const m = (gameState && (gameState.map_ascii || gameState.map_full)) || '';
  if (!m.trim()) {
    root.classList.add('map-empty');
    root.innerHTML = '';
    root.textContent = '— nessuna mappa —';
    return false;
  }
  root.classList.remove('map-empty');
  const { w, h, rows } = mapDims();
  const cvW = w * SPRITE_PX;
  const cvH = h * SPRITE_PX;
  // espone larghezza/altezza alla griglia CSS via custom property
  root.style.setProperty('--map-w', w);
  root.style.setProperty('--map-h', h);
  root.style.setProperty('--map-aspect', `${w} / ${h}`);
  let cv = root.querySelector('canvas.map-canvas');
  if (!cv) {
    root.textContent = '';
    cv = el('canvas', { class: 'map-canvas' });
    root.appendChild(cv);
  }
  if (cv.width !== cvW || cv.height !== cvH) {
    cv.width = cvW; cv.height = cvH;
  }
  const ctx = cv.getContext('2d');
  ctx.imageSmoothingEnabled = false;
  const img = ctx.createImageData(cvW, cvH);
  const data = img.data;
  for (let gy = 0; gy < h; gy++) {
    for (let gx = 0; gx < w; gx++) {
      const ch = (rows[gy] || '')[gx] || ' ';
      const sp = spriteForCell(ch, rows, gx, gy);
      // Nearest-neighbor: lo sprite può essere 10×10 (default storici) o
      // 16×16 (nuovo formato DM). Scaliamo dinamicamente al target
      // SPRITE_PX per evitare bordi neri quando un'icona è più piccola.
      const srcH = sp.length || 1;
      for (let py = 0; py < SPRITE_PX; py++) {
        const sy = Math.min(srcH - 1, Math.floor(py * srcH / SPRITE_PX));
        const srow = sp[sy] || '';
        const srcW = srow.length || 1;
        for (let px = 0; px < SPRITE_PX; px++) {
          const sx = Math.min(srcW - 1, Math.floor(px * srcW / SPRITE_PX));
          const col = PALETTE[parseInt(srow[sx], 16) || 0] || PALETTE[0];
          const di = ((gy * SPRITE_PX + py) * cvW
                      + (gx * SPRITE_PX + px)) * 4;
          data[di] = col[0]; data[di + 1] = col[1];
          data[di + 2] = col[2]; data[di + 3] = 255;
        }
      }
    }
  }
  ctx.putImageData(img, 0, 0);
  // illuminazione a torcia: alone caldo sul party + penombra ai bordi
  paintMapLighting(ctx, cvW, cvH, rows, w, h);
  return true;
}

// Caratteri considerati elementi ATTIVI sulla mappa: ricevono un alone
// pulsante (rosso minaccioso per i mostri, dorato scintillante per i
// tesori, magenta etereo per l'obiettivo). Lascia inalterati gli altri.
const _ENEMY_CHARS = new Set(['M', 'g', 'k', 'D', 'B']);
const _LOOT_CHARS  = new Set(['$']);
const _GOAL_CHARS  = new Set(['X', '*']);

// Illuminazione a torcia disegnata DOPO le sprite: alone caldo attorno al
// party, penombra ai bordi, pulse sui nemici/tesori. Il parametro `t`
// (timestamp performance.now()) modula gli aloni animati: a chiamata
// singola ferma il pulse al frame corrente, dentro al loop di animazione
// dà la pulsazione viva.
function paintMapLighting(ctx, cvW, cvH, rows, gridW, gridH, t) {
  if (typeof t !== 'number') t = (typeof performance !== 'undefined'
    ? performance.now() : Date.now());
  // posizione del party (@) sulla griglia
  let px = -1, py = -1;
  for (let y = 0; y < gridH; y++) {
    const i = (rows[y] || '').indexOf('@');
    if (i >= 0) { px = i; py = y; break; }
  }
  ctx.save();
  // vignettatura calda: concentra lo sguardo sul centro dell'azione
  const r0 = Math.min(cvW, cvH) * 0.20;
  const r1 = Math.max(cvW, cvH) * 0.74;
  const vg = ctx.createRadialGradient(
    cvW / 2, cvH / 2, r0, cvW / 2, cvH / 2, r1);
  vg.addColorStop(0, 'rgba(0,0,0,0)');
  vg.addColorStop(1, 'rgba(8,4,0,0.62)');
  ctx.fillStyle = vg;
  ctx.fillRect(0, 0, cvW, cvH);
  // alone della torcia sul party (additivo → bagliore caldo)
  ctx.globalCompositeOperation = 'lighter';
  if (px >= 0) {
    const cx = px * SPRITE_PX + SPRITE_PX / 2;
    const cy = py * SPRITE_PX + SPRITE_PX / 2;
    // respirazione lenta dell'alone party: ±10% di raggio in 4s
    const breath = 1 + 0.10 * Math.sin(t * 0.0016);
    const glow = ctx.createRadialGradient(
      cx, cy, 1, cx, cy, SPRITE_PX * 4.6 * breath);
    glow.addColorStop(0,    'rgba(255,206,120,0.55)');
    glow.addColorStop(0.45, 'rgba(228,138,52,0.24)');
    glow.addColorStop(1,    'rgba(228,138,52,0)');
    ctx.fillStyle = glow;
    ctx.fillRect(0, 0, cvW, cvH);
  }
  // pulse sugli elementi attivi: ogni tipo ha frequenza e colore propri
  for (let y = 0; y < gridH; y++) {
    const row = rows[y] || '';
    for (let x = 0; x < gridW; x++) {
      const ch = row[x];
      if (!ch) continue;
      let color = null, period = 0, base = 1.6, peak = 2.6;
      if (_ENEMY_CHARS.has(ch)) {
        color = [220, 60, 60]; period = 1500;
        base = 1.5; peak = 2.7;
      } else if (_LOOT_CHARS.has(ch)) {
        color = [255, 210, 90]; period = 2200;
        base = 1.2; peak = 2.1;
      } else if (_GOAL_CHARS.has(ch)) {
        color = [180, 120, 230]; period = 2600;
        base = 1.3; peak = 2.4;
      }
      if (!color) continue;
      const phase = (t / period) * Math.PI * 2;
      const pulse = 0.5 + 0.5 * Math.sin(phase);
      const radius = SPRITE_PX * (base + (peak - base) * pulse);
      const a = 0.18 + 0.30 * pulse;
      const cx = x * SPRITE_PX + SPRITE_PX / 2;
      const cy = y * SPRITE_PX + SPRITE_PX / 2;
      const g = ctx.createRadialGradient(cx, cy, 0.5, cx, cy, radius);
      g.addColorStop(0,
        `rgba(${color[0]},${color[1]},${color[2]},${a.toFixed(3)})`);
      g.addColorStop(1, `rgba(${color[0]},${color[1]},${color[2]},0)`);
      ctx.fillStyle = g;
      ctx.fillRect(cx - radius, cy - radius, radius * 2, radius * 2);
    }
  }
  ctx.restore();
}

// Loop di animazione mappa: ridisegna a ~12fps SOLO se la mappa contiene
// almeno un elemento animabile (party @, nemici, tesori, obiettivo).
// requestAnimationFrame con throttle: il browser pausa il loop quando il
// tab è in background. Costo trascurabile (~80KB/frame su 14×14).
let _mapAnimRaf = null;
let _mapAnimNextAt = 0;
const MAP_ANIM_INTERVAL = 80;     // ms tra frame (~12fps)

function _mapHasAnimated() {
  const m = (gameState && gameState.map_ascii) || '';
  if (!m) return false;
  for (const ch of m) {
    if (ch === '@' || _ENEMY_CHARS.has(ch) || _LOOT_CHARS.has(ch)
        || _GOAL_CHARS.has(ch)) return true;
  }
  return false;
}

function _mapAnimTick(now) {
  _mapAnimRaf = null;
  if (now >= _mapAnimNextAt && _mapHasAnimated()) {
    paintMap($('map-render'));
    const big = $('map-big-render');
    if (big && big.querySelector('canvas.map-canvas')
        && big.offsetParent !== null) {
      paintMap(big);
    }
    _mapAnimNextAt = now + MAP_ANIM_INTERVAL;
  }
  _mapAnimRaf = requestAnimationFrame(_mapAnimTick);
}

function startMapAnim() {
  if (_mapAnimRaf || typeof requestAnimationFrame !== 'function') return;
  _mapAnimNextAt = 0;
  _mapAnimRaf = requestAnimationFrame(_mapAnimTick);
}

function renderMap() {
  paintMap($('map-render'));
  $('zone-name').textContent = (gameState && gameState.current_zone) || '—';
  const pos = (gameState && gameState.current_position) || [0, 0];
  $('zone-pos').textContent = `[${pos[0]}, ${pos[1]}]`;
  renderLegend();
}

// Legenda DINAMICA: ogni voce è legata ai caratteri di cella che la
// rappresentano. renderLegend() mostra SOLO le voci i cui caratteri
// compaiono davvero nella mappa corrente (map_ascii) — come la mappa,
// la legenda riflette la scena qui e ora invece di elencare tutti i
// simboli possibili. Ordine = ordine di questa lista.
const LEGEND_DEFS = [
  { cls: 'lg-party',   glyph: '☻', label: 'Party',         chars: ['@'] },
  { cls: 'lg-start',   glyph: '✦', label: 'Partenza',      chars: ['*'] },
  { cls: 'lg-exit',    glyph: '★', label: 'Obiettivo',     chars: ['X'] },
  { cls: 'lg-combat',  glyph: '⚔', label: 'Combattimento', chars: ['C'] },
  { cls: 'lg-explore', glyph: '❖', label: 'Esplora',       chars: ['E'] },
  { cls: 'lg-social',  glyph: '☺', label: 'Incontro',      chars: ['S'] },
  { cls: 'lg-door',    glyph: '▣', label: 'Porta',         chars: ['+'] },
  { cls: 'lg-trap',    glyph: '✸', label: 'Trappola',      chars: ['T'] },
  { cls: 'lg-water',   glyph: '≈', label: 'Acqua',         chars: ['~'] },
  { cls: 'lg-stair',   glyph: '⇕', label: 'Scale',         chars: ['<', '>'] },
  { cls: 'lg-tree',    glyph: '♣', label: 'Albero',        chars: ['t'] },
  { cls: 'lg-grass',   glyph: '„', label: 'Sterpaglia',    chars: [','] },
  { cls: 'lg-rock',    glyph: '●', label: 'Masso',         chars: ['o'] },
  { cls: 'lg-fire',    glyph: '♨', label: 'Falò',          chars: ['f'] },
  { cls: 'lg-bridge',  glyph: '≡', label: 'Ponte',         chars: ['='] },
  { cls: 'lg-chest',   glyph: '⊡', label: 'Forziere',      chars: ['$'] },
  { cls: 'lg-monster', glyph: '☠', label: 'Mostro',        chars: ['M'] },
  { cls: 'lg-goblin',  glyph: 'ɢ', label: 'Goblin',        chars: ['g'] },
  { cls: 'lg-skel',    glyph: '☠', label: 'Non-morto',     chars: ['k'] },
  { cls: 'lg-dragon',  glyph: 'Ɖ', label: 'Drago',         chars: ['D'] },
  { cls: 'lg-down',    glyph: '✟', label: 'PG a terra',    chars: ['P'] },
  { cls: 'lg-wall',    glyph: '',  label: 'Muro',          chars: ['#'] },
  { cls: 'lg-fog',     glyph: '',  label: 'Inesplorato',   chars: [' '] },
];

// Disegna lo sprite di UN carattere su un piccolo <canvas> 16×16 (scalato
// dal CSS): serve come pastiglia di legenda. Usa lo sprite custom del DM se
// presente, altrimenti il default del carattere, altrimenti il pavimento.
function _legendSwatchCanvas(ch) {
  const custom = (gameState && gameState.sprites) || {};
  const sp = custom[ch] || DEFAULT_SPRITES[ch] || DEFAULT_SPRITES['.'];
  const cv = el('canvas', { class: 'lg-swatch' });
  cv.width = SPRITE_PX; cv.height = SPRITE_PX;
  const ctx = cv.getContext('2d');
  ctx.imageSmoothingEnabled = false;
  const img = ctx.createImageData(SPRITE_PX, SPRITE_PX);
  const data = img.data;
  const srcH = sp.length || 1;
  for (let py = 0; py < SPRITE_PX; py++) {
    const sy = Math.min(srcH - 1, Math.floor(py * srcH / SPRITE_PX));
    const srow = sp[sy] || '';
    const srcW = srow.length || 1;
    for (let px = 0; px < SPRITE_PX; px++) {
      const sx = Math.min(srcW - 1, Math.floor(px * srcW / SPRITE_PX));
      const col = PALETTE[parseInt(srow[sx], 16) || 0] || PALETTE[0];
      const di = (py * SPRITE_PX + px) * 4;
      data[di] = col[0]; data[di + 1] = col[1];
      data[di + 2] = col[2]; data[di + 3] = 255;
    }
  }
  ctx.putImageData(img, 0, 0);
  return cv;
}

function renderLegend() {
  const grids = document.querySelectorAll('.legend-grid');
  if (!grids.length) return;
  const m = (gameState && gameState.map_ascii) || '';
  const dmLegend = (gameState && gameState.map_legend) || [];

  // PRIORITÀ: legenda emessa dal DM (LEGENDA_START…LEGENDA_END). Mostra una
  // pastiglia con lo sprite reale + l'etichetta, SOLO per i caratteri che
  // compaiono davvero nella mappa corrente.
  if (dmLegend.length) {
    const present = new Set(m);
    const entries = dmLegend.filter(e => e && e.char && present.has(e.char));
    for (const g of grids) {
      g.innerHTML = '';
      if (!entries.length) {
        g.innerHTML = '<span class="muted">— nessun simbolo —</span>';
        continue;
      }
      for (const e of entries) {
        const span = el('span', { class: 'lg-entry' });
        span.appendChild(_legendSwatchCanvas(e.char));
        span.appendChild(document.createTextNode(' ' + (e.label || e.char)));
        g.appendChild(span);
      }
    }
    return;
  }

  // Fallback storico: legenda hardcoded filtrata sui simboli presenti.
  if (!m) {
    for (const g of grids) g.innerHTML =
      '<span class="muted">— nessuna mappa —</span>';
    return;
  }
  const present = new Set(m);  // set dei caratteri usati nella mappa
  const html = LEGEND_DEFS
    .filter(d => d.chars.some(c => present.has(c)))
    .map(d => `<span><i class="lg ${d.cls}">${d.glyph}</i> `
              + `${escapeHtml(d.label)}</span>`)
    .join('');
  for (const g of grids) g.innerHTML = html
    || '<span class="muted">— nessun simbolo —</span>';
}

// Apre la mappa ingrandita a tutta finestra, con zona, posizione e
// legenda. Si chiama al click sul pannello mappa.
function openMapModal() {
  if (!gameState || !gameState.map_ascii) return;
  paintMap($('map-big-render'));
  $('map-big-zone').textContent = (gameState && gameState.current_zone) || '—';
  const pos = (gameState && gameState.current_position) || [0, 0];
  $('map-big-pos').textContent = `[${pos[0]}, ${pos[1]}]`;
  renderLegend();
  openModal('map');
}

function renderRolls() {
  const ul = $('rolls-log');
  const log = (gameState.rolls_log || []).slice(-8).reverse();
  if (!log.length) { ul.innerHTML = '<li class="empty">—</li>'; return; }
  ul.innerHTML = '';
  for (const r of log) {
    const li = el('li');
    if (r.is_crit) li.className = 'crit';
    if (r.is_fumble) li.className = 'fumble';
    const tag = r.advantage ? ' V' : (r.disadvantage ? ' S' : '');
    const why = r.reason ? ` (${escapeHtml(r.reason)})` : '';
    li.innerHTML = `🎲 ${r.expr}${tag} = <strong>${r.total}</strong>${why}`;
    ul.appendChild(li);
  }
}

// ────────────────────────────────────────────────────────────────────
// Riquadro dadi — il giocatore umano lancia i propri dadi
// ────────────────────────────────────────────────────────────────────

// Ritorna {name, isHuman} del PG di turno, o null se nessun turno attivo.
function activePlayerInfo() {
  if (!gameState) return null;
  const ap = gameState.active_player;
  if (!ap) return null;
  // confronto case-insensitive: il DM può scrivere il nome con maiuscole
  // diverse dalla scheda — con il match esatto il PG umano di turno veniva
  // mostrato come «(IA)» e il riquadro dadi non segnalava «Tocca a te».
  const apLow = String(ap).trim().toLowerCase();
  const p = (gameState.players || []).find(
    x => (x.name || '').trim().toLowerCase() === apLow);
  if (!p) return { name: ap, isHuman: false };
  return { name: p.name, isHuman: p.type === 'human' };
}

// Tiro in attesa richiesto dal DM (lo lancia il giocatore umano). In scene
// multi-PG il giocatore SCEGLIE quale con le pillole: rispetta quella scelta
// (`pendingRollIdx`), così il tiro lanciato — e quindi inviato al DM — è
// quello del PG selezionato e non sempre il primo.
function pendingRoll() {
  const pend = (gameState && gameState.pending_rolls) || [];
  if (!pend.length) return null;
  return pend[pendingRollIdx] || pend[0];
}

// Nome da usare nel campo "by" del tiro.
function rollerName() {
  const req = pendingRoll();
  if (req && req.by) return req.by;
  const info = activePlayerInfo();
  if (info && info.isHuman) return info.name;
  const firstHuman = (gameState && gameState.players || []).find(p => p.type === 'human');
  return (firstHuman && firstHuman.name) || 'Giocatore';
}

// Precompila il riquadro dadi dal ROLL_REQ richiesto dal DM. Sopporta
// espressioni composte (es. "1d20+4+1d4"): l'UI evidenzia il dado del
// PRIMO termine dxN, ma `rollDice` lancerà l'espressione INTERA (raw).
function applyPendingRoll(req) {
  const expr = String(req.dice || req.expr || '');
  // Estrai TUTTI i termini dXX per capire dado principale + composti.
  const dice_re = /(\d+)\s*d\s*(\d+)/gi;
  const matches = [...expr.matchAll(dice_re)];
  const first = matches[0] || [];
  const qty   = first[1] ? parseInt(first[1], 10) : 1;
  const sides = first[2] ? parseInt(first[2], 10) : 20;
  // Modificatore: somma dei termini "+N"/"-N" che NON sono parte di un
  // termine dado (così "+1d4" non viene confuso col modificatore).
  let mod = 0;
  const flat = expr.replace(/\d+\s*d\s*\d+/gi, '').match(/[+-]\s*\d+/g) || [];
  for (const m of flat) mod += parseInt(m.replace(/\s/g, ''), 10);

  if ($('dice-qty')) $('dice-qty').value = qty;
  if ($('dice-mod')) $('dice-mod').value = mod;
  if ($('dice-adv')) $('dice-adv').checked = !!req.advantage;
  if ($('dice-dis')) $('dice-dis').checked = !!req.disadvantage;

  const dieBtn = document.querySelector(`.die[data-sides="${sides}"]`);
  if (dieBtn) dieBtn.classList.add('requested');

  const turnEl = $('dice-turn');
  const why = req.reason ? ` — ${req.reason}` : '';
  const composite = matches.length > 1 ? ' (composto)' : '';
  turnEl.textContent = `🎯 ${req.by || 'Giocatore'}, tira ${expr || (qty + 'd' + sides)}${composite}${why}`;
  turnEl.className = 'dice-turn active';
}

// Pillole con i PG umani che hanno un tiro in attesa: mostra TUTTI (non
// solo il primo). Click su una pillola → riquadro dadi precompilato per
// quel PG. Senza questo, in scene multi-PG il 2°/3° tiro umano restava
// silente finché il 1° non veniva lanciato.
function renderPendingPlayers() {
  const list = $('dice-pending-list');
  if (!list) return;
  const pend = (gameState && gameState.pending_rolls) || [];
  // Tieni la selezione (`pendingRollIdx`) entro i tiri ancora in attesa:
  // se quello scelto è stato risolto/rimosso, torna al primo.
  if (pendingRollIdx >= pend.length) pendingRollIdx = 0;
  if (pend.length <= 1) { list.innerHTML = ''; pendingRollIdx = 0; return; }
  list.innerHTML = '<span class="pending-lbl">In attesa:</span> ' + pend.map((r, i) => {
    const by = escapeHtml(r.by || '?');
    const d = escapeHtml(r.dice || r.expr || '1d20');
    return `<button class="pending-pill${i === pendingRollIdx ? ' active' : ''}" data-idx="${i}" title="${by}: ${d}">${by}</button>`;
  }).join(' ');
  list.querySelectorAll('.pending-pill').forEach(btn => {
    btn.addEventListener('click', () => {
      const idx = parseInt(btn.dataset.idx, 10);
      const req = pend[idx];
      if (req) {
        pendingRollIdx = idx;   // la scelta del PG arriva fino a rollDice → DM
        applyPendingRoll(req);
        list.querySelectorAll('.pending-pill').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
      }
    });
  });
}

function renderDiceBox() {
  const box = $('dice-box');
  const turnEl = $('dice-turn');
  if (!box || !turnEl) return;

  // pulisci evidenziazione dado della richiesta precedente
  document.querySelectorAll('.die.requested').forEach(b => b.classList.remove('requested'));

  // pillole multi-PG (visibili solo se ci sono ≥2 tiri umani in attesa)
  renderPendingPlayers();

  // tiro richiesto dal DM: precompila e segnala
  const req = pendingRoll();
  if (req) {
    box.classList.add('your-turn');
    applyPendingRoll(req);
    return;
  }

  const info = activePlayerInfo();
  const human = !!(info && info.isHuman);

  box.classList.toggle('your-turn', human);
  if (human) {
    turnEl.textContent = `🎯 Tocca a ${info.name} — lancia tu i dadi!`;
    turnEl.className = 'dice-turn active';
  } else if (info) {
    turnEl.textContent = `Turno di ${info.name} (IA)`;
    turnEl.className = 'dice-turn';
  } else {
    turnEl.textContent = 'Tiri liberi';
    turnEl.className = 'dice-turn';
  }
}

async function rollDice(sides) {
  let dice, adv, dis, reason;
  const req = pendingRoll();
  if (req && (req.dice || req.expr)) {
    // tiro RICHIESTO dal DM: lancia ESATTAMENTE l'espressione richiesta
    // (gestisce anche espressioni composte tipo "1d20+4+1d4")
    dice = String(req.dice || req.expr);
    adv = !!req.advantage || $('dice-adv').checked;
    dis = !!req.disadvantage || $('dice-dis').checked;
    reason = req.reason || 'Tiro del giocatore';
  } else {
    const qty = Math.max(1, Math.min(12, parseInt($('dice-qty').value, 10) || 1));
    const mod = Math.max(-20, Math.min(20, parseInt($('dice-mod').value, 10) || 0));
    adv = $('dice-adv').checked;
    dis = $('dice-dis').checked;
    dice = `${qty}d${sides}`;
    if (mod > 0) dice += `+${mod}`;
    else if (mod < 0) dice += `${mod}`;
    reason = 'Tiro del giocatore';
  }

  const by = rollerName();
  const resEl = $('dice-result');
  resEl.className = 'dice-result rolling';
  resEl.textContent = '🎲 …';

  try {
    const r = await fetch('/api/roll', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ dice, advantage: adv, disadvantage: dis, reason, by }),
    });
    const d = await r.json();
    if (d.error) {
      resEl.className = 'dice-result err';
      resEl.textContent = '⚠ ' + d.error;
      return;
    }
    const roll = d.roll;
    lastRoll = { ...roll, by, dice };
    resEl.className = 'dice-result';
    if (roll.is_crit)   resEl.classList.add('crit');
    if (roll.is_fumble) resEl.classList.add('fumble');
    resEl.innerHTML = `
      <span class="dice-total">${roll.total}</span>
      <span class="dice-detail">${escapeHtml(d.pretty || dice)}</span>`;
    await refreshState();   // aggiorna log tiri lato server

    // passa il tiro direttamente al DM
    const msg = rollToText(lastRoll);
    if (!dmOpen) {
      addMsg('system', '🎲 Tiro registrato (DM non collegato): ' + msg);
    } else if (busy) {
      // DM occupato: accoda il tiro invece di perderlo — sarà inviato
      // appena il DM è libero, così l'avventura prosegue dopo il lancio.
      rollQueue.push(msg);
      addMsg('system', '🎲 Tiro in coda — verrà inviato al DM appena è libero.');
    } else {
      sendMessage(msg);
    }
  } catch (e) {
    resEl.className = 'dice-result err';
    resEl.textContent = '⚠ ' + e.message;
  }
}

// Costruisce il testo del tiro da inviare al DM.
// SOLO il blocco [Nome lancia ...: risultato N] — è il formato che il DM
// riconosce dal prompt. Nessun testo aggiuntivo: al DM arriva solo il dato.
function rollToText(r) {
  const tag = r.advantage ? ' con vantaggio'
            : (r.disadvantage ? ' con svantaggio' : '');
  const crit = r.is_crit ? ' — CRITICO!'
             : (r.is_fumble ? ' — fallimento critico!' : '');
  return `[${r.by} lancia ${r.expr || r.dice}${tag}: risultato ${r.total}${crit}]`;
}

document.querySelectorAll('.die').forEach(btn => {
  btn.addEventListener('click', () => rollDice(parseInt(btn.dataset.sides, 10)));
});
// vantaggio e svantaggio mutuamente esclusivi
$('dice-adv').addEventListener('change', (e) => {
  if (e.target.checked) $('dice-dis').checked = false;
});
$('dice-dis').addEventListener('change', (e) => {
  if (e.target.checked) $('dice-adv').checked = false;
});

// ────────────────────────────────────────────────────────────────────
// Chat
// ────────────────────────────────────────────────────────────────────

function addMsg(role, content) {
  const log = $('chat-log');
  // Guardia anti-doppione (solo DM): la chat web a volte rimanda la STESSA
  // risposta del DM due volte di fila. Filtriamo solo se la bolla
  // precedente è stata aggiunta DI RECENTE (< 1.5s): due turni distinti
  // del DM che, per caso, producono testo identico restano due bolle.
  if (role === 'dm') {
    const last = log.lastElementChild;
    if (last && last.classList.contains('msg-dm')
        && last.dataset.dm === content) {
      const ts = parseInt(last.dataset.ts || '0', 10);
      if (ts && (Date.now() - ts) < 1500) return last;
    }
  }
  const cls = role === 'user' ? 'msg msg-user' : role === 'dm' ? 'msg msg-dm' : `msg msg-${role}`;
  const div = el('div', { class: cls });
  div.innerHTML = renderMarkdown(content);
  if (role === 'dm') {
    div.dataset.dm = content;
    div.dataset.ts = String(Date.now());
  }
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
  if (role === 'dm') {
    lastDmMessage = content;
    lastDmTurn.push(content);
    ttsSpeak(content);
  } else if (role === 'user') {
    // Nuovo messaggio del giocatore: il turno DM precedente si chiude;
    // il prossimo "Ripeti" leggerà solo le risposte DM al NUOVO turno.
    lastDmTurn = [];
  }
  return div;
}

// ─── Coda azioni DM (cadenza) ───────────────────────────────────────

// Azzera la coda: a inizio di ogni nuovo messaggio del giocatore.
function resetActionQueue() {
  actionQueue = [];
  awaitingReveal = false;
  streamDone = false;
  finalState = null;
  updateNextActionBar();
}

// Mostra/aggiorna il tasto "Prossima azione" col numero di azioni in coda.
function updateNextActionBar() {
  const bar = $('next-action-bar');
  const btn = $('next-action');
  if (!bar || !btn) return;
  const n = actionQueue.length;
  if (n > 0) {
    bar.classList.remove('hidden');
    btn.textContent = n > 1 ? `▶ Prossima azione (${n})` : '▶ Prossima azione';
  } else {
    bar.classList.add('hidden');
  }
}

// Accoda un'azione del DM. La PRIMA del turno si mostra subito; le altre
// restano in coda finché il giocatore non preme il tasto.
function enqueueDmAction(item) {
  actionQueue.push(item);
  if (!awaitingReveal) revealNextAction();
  else updateNextActionBar();
}

// Mostra la prossima azione in coda (bolla DM + stato allineato).
function revealNextAction() {
  const item = actionQueue.shift();
  if (!item) { awaitingReveal = false; updateNextActionBar(); applyFinalStateIfDone(); return; }
  addMsg('dm', item.text);
  if (item.state) { gameState = item.state; renderUI(); }
  awaitingReveal = true;
  updateNextActionBar();
  if (actionQueue.length === 0) applyFinalStateIfDone();
}

// Evento done dello streaming: ricorda lo stato finale, applicalo se la
// coda è già vuota (altrimenti lo applica revealNextAction a fine coda).
function onStreamDone(state) {
  streamDone = true;
  finalState = state;
  if (actionQueue.length === 0) applyFinalStateIfDone();
}

function applyFinalStateIfDone() {
  if (streamDone && actionQueue.length === 0 && finalState) {
    gameState = finalState;
    renderUI();
    finalState = null;
  }
  // coda svuotata e streaming finito: ora è sicuro inviare i tiri accodati
  if (streamDone && actionQueue.length === 0) {
    maybeDrainRollQueue();
    maybeAutoContinue();   // tiri IA non narrati in coda → consegnali al DM
  }
}

// Invia il prossimo tiro accodato SOLO se non resta nulla da scoprire e il
// DM è libero: così l'avventura prosegue senza saltare le bolle non lette.
function maybeDrainRollQueue() {
  if (!busy && actionQueue.length === 0 && rollQueue.length) {
    sendMessage(rollQueue.shift());
  }
}

// Prosecuzione automatica dei turni IA: se ci sono tiri IA risolti ma non
// ancora narrati (pending_roll_feedback) e NESSUN tiro umano in attesa,
// il DM è appeso a quei risultati (es. dopo un briefing di ripresa che li
// ha lasciati parcheggiati) — glieli consegniamo da soli con
// /api/chat {continue:true}, senza aspettare che il giocatore scriva.
// Ogni turno IA resta una bolla separata (coda «Prossima azione»):
// sequenza chiara, uno step alla volta, ma la partita non si congela.
let autoContinueKey = '';
async function maybeAutoContinue() {
  if (busy || actionQueue.length || rollQueue.length) return;
  if (!dmOpen || !dmSynced || !gameState) return;
  let fb = gameState.pending_roll_feedback || [];
  let pend = gameState.pending_rolls || [];
  if (!fb.length || pend.length) return;
  // ricontrolla su stato FRESCO: gameState può essere stantio (es. il
  // feedback è già stato consumato da un messaggio del giocatore).
  try { await refreshState(); } catch (_) { return; }
  if (busy) return;
  fb = (gameState && gameState.pending_roll_feedback) || [];
  pend = (gameState && gameState.pending_rolls) || [];
  if (!fb.length || pend.length) return;
  // anti-ripetizione: non rilanciare due volte per lo stesso feedback
  const key = JSON.stringify(fb);
  if (key === autoContinueKey) return;
  autoContinueKey = key;
  sendMessage(null, false, true);
}

// Tasto "Prossima azione": scopre la bolla successiva.
(function () {
  const btn = $('next-action');
  if (btn) btn.addEventListener('click', revealNextAction);
})();

// retry=true: ricarica/rigenera l'ultima risposta del DM (nessun nuovo
// messaggio del giocatore — il server riusa l'ultimo).
// cont=true: prosecuzione automatica — nessun messaggio del giocatore, il
// server consegna al DM i tiri IA/note in sospeso e fa avanzare i turni.
async function sendMessage(explicitText, retry, cont) {
  if (busy) return;
  resetActionQueue();   // nuovo turno: svuota le azioni in coda dal precedente
  const input = $('chat-input');
  let txt = '';
  if (retry) {
    // togli le bolle DM finali dal log: il server le rigenera
    const log = $('chat-log');
    while (log.lastElementChild &&
           log.lastElementChild.classList.contains('msg-dm')) {
      log.removeChild(log.lastElementChild);
    }
    addMsg('system', '🔃 Rigenero l\'ultima risposta del DM…');
  } else if (cont) {
    addMsg('system', '▶ I PG IA e i mostri agiscono…');
  } else {
    if (typeof explicitText === 'string') {
      txt = explicitText.trim();      // tiro auto-inviato dal riquadro dadi
    } else {
      txt = input.value.trim();
      input.value = '';
    }
    if (!txt) return;
    addMsg('user', txt);
    autoContinueKey = '';   // nuovo input del giocatore: ri-arma l'auto-continue
  }

  busy = true;
  $('chat-send').disabled = true;
  $('typing').classList.add('on');

  try {
    const resp = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(retry ? { retry: true }
                         : cont  ? { 'continue': true }
                         : { message: txt }),
    });

    if (!resp.body) {
      const d = await resp.json();
      if (d.error) addMsg('error', '⚠ ' + d.error);
      return;
    }

    const reader = resp.body.getReader();
    const dec = new TextDecoder();
    let buf = '';
    while (true) {
      const { done, value } = await reader.read();
      if (value) {
        buf += dec.decode(value, { stream: !done });
        const lines = buf.split('\n');
        buf = done ? '' : lines.pop();
        for (const line of lines) {
          if (!line.trim()) continue;
          const j = line.startsWith('data: ') ? line.slice(6) : line;
          try {
            const d = JSON.parse(j);
            if (d.error) { addMsg('error', '⚠ ' + d.error); continue; }
            if (d.token) {
              // accoda: la prima azione si mostra subito, le altre col tasto.
              // Lo stato viaggia con l'azione e si applica quando la mostriamo.
              enqueueDmAction({ text: d.token, state: d.state });
            } else if (d.done) {
              onStreamDone(d.state);
            } else if (d.state) {
              gameState = d.state; renderUI();
            }
          } catch (_) {
            // ignora keepalive
          }
        }
      }
      if (done) break;
    }
  } catch (e) {
    addMsg('error', '⚠ Errore di rete: ' + e.message);
  } finally {
    busy = false;
    $('chat-send').disabled = false;
    $('typing').classList.remove('on');
    input.focus();
    // SSE già aggiorna gameState a ogni token + sul done event finale.
    // Niente refreshState() qui: era un doppio fetch ridondante che
    // generava un round trip e un re-render in più senza dati nuovi.
    // invia i tiri accodati mentre il DM era occupato: l'avventura
    // prosegue automaticamente dopo ogni lancio del giocatore. MA solo se
    // non restano azioni da scoprire: altrimenti un auto-invio le
    // cancellerebbe (resetActionQueue) prima che il giocatore le legga.
    // Se la coda non è vuota, il drain avviene quando il giocatore la
    // svuota col tasto "Prossima azione" (vedi maybeDrainRollQueue).
    maybeDrainRollQueue();
    maybeAutoContinue();
  }
}

// ────────────────────────────────────────────────────────────────────
// Setup modal
// ────────────────────────────────────────────────────────────────────

$('btn-setup').addEventListener('click', async () => {
  // Pre-compila i campi con l'ultimo modello configurato sul server
  // (persiste tra i riavvii) invece dei default statici dell'HTML.
  try {
    const r = await fetch('/api/webchat/status');
    const d = await r.json();
    if (d.url)  $('setup-url').value = d.url;
    if (d.name) $('setup-name').value = d.name;
    if (d.timeout) $('setup-timeout').value = d.timeout;
    // allinea il preset al modello corrente
    const match = Object.keys(SETUP_PRESETS).find(k => SETUP_PRESETS[k].url === (d.url || '').trim());
    $('setup-preset').value = match || 'custom';
  } catch (e) { /* lascia i default dell'HTML */ }
  openModal('setup');
});

// Click sul pannello mappa → apre la mappa ingrandita a tutta finestra.
$('map-render').addEventListener('click', openMapModal);

// Preset modello DM: riempie URL + nome. 'custom' lascia i campi liberi.
const SETUP_PRESETS = {
  deepseek: { url: 'https://chat.deepseek.com/', name: 'DeepSeek Chat' },
  qwen:     { url: 'https://chat.qwen.ai/',      name: 'Qwen Chat' },
  grok:     { url: 'https://grok.com/',          name: 'Grok' },
  claude:   { url: 'https://claude.ai/chat/',    name: 'Claude' },
};
$('setup-preset').addEventListener('change', (e) => {
  const p = SETUP_PRESETS[e.target.value];
  if (p) {
    $('setup-url').value = p.url;
    $('setup-name').value = p.name;
  }
});
// se l'utente modifica l'URL a mano, il preset passa a "Personalizzato"
$('setup-url').addEventListener('input', () => {
  const url = $('setup-url').value.trim();
  const match = Object.keys(SETUP_PRESETS).find(k => SETUP_PRESETS[k].url === url);
  $('setup-preset').value = match || 'custom';
});
$('setup-open').addEventListener('click', async () => {
  const url = $('setup-url').value.trim();
  const name = $('setup-name').value.trim();
  const timeout = parseInt($('setup-timeout').value, 10) || 180;
  const r = $('setup-result');
  r.textContent = 'Apro Chromium...';
  r.className = '';
  try {
    const resp = await fetch('/api/webchat/open', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url, name, timeout }),
    });
    const d = await resp.json();
    if (d.error) { r.textContent = '⚠ ' + d.error; r.className = 'error'; }
    else {
      r.textContent = '✓ ' + (d.name || 'DM') + ' aperto a ' + d.url;
      r.className = 'ok';
      refreshWebchatStatus();
    }
  } catch (e) {
    r.textContent = '⚠ ' + e.message;
    r.className = 'error';
  }
});

// ────────────────────────────────────────────────────────────────────
// Personaggi modal
// ────────────────────────────────────────────────────────────────────

$('btn-chars').addEventListener('click', async () => {
  await loadDndData();
  await refreshState();
  await loadCharsIntoForm();
  await loadPartyPickList();
  openModal('chars');
});

async function loadCharsIntoForm() {
  const form = $('chars-form');
  form.innerHTML = '';
  try {
    const r = await fetch('/api/characters');
    const d = await r.json();
    const chars = d.characters || [];
    if (chars.length) {
      for (const c of chars) addCharRow(c);
    } else {
      addCharRow();
    }
    renderCharsPreview(chars);
  } catch (e) {
    addCharRow();
  }
}

function addCharRow(c = null) {
  const row = el('div', { class: 'char-row' });
  const mk = (key, label, vals, def) => {
    const lbl = el('label', { text: label });
    const sel = el('select');
    sel.dataset.key = key;
    for (const v of vals) {
      const o = el('option', { text: v });
      o.value = v;
      if (def === v) o.selected = true;
      sel.appendChild(o);
    }
    lbl.appendChild(sel);
    return lbl;
  };
  const nameLbl = el('label', { text: 'Nome PG' });
  const nameIn = el('input');
  nameIn.type = 'text';
  nameIn.dataset.key = 'name';
  nameIn.value = c ? c.name : '';
  nameIn.placeholder = 'Es. Thorin';
  nameLbl.appendChild(nameIn);
  row.appendChild(nameLbl);

  row.appendChild(mk('species', 'Specie', dndData.species, c && (c.species || c.race)));
  row.appendChild(mk('class',   'Classe', dndData.classes, c && c.class));
  row.appendChild(mk('gender', 'Sesso', ['Casuale'].concat(dndData.genders), c && c.gender));
  row.appendChild(mk('background', 'Background', dndData.backgrounds, c && c.background));
  row.appendChild(mk('alignment', 'Allineamento', dndData.alignments, c && c.alignment));
  row.appendChild(mk('player_type', 'Tipo', ['human', 'ai'], c && c.player_type));

  const rem = el('button', { class: 'remove', text: '✕' });
  rem.addEventListener('click', () => row.remove());
  row.appendChild(rem);

  $('chars-form').appendChild(row);
}
window.addCharRow = addCharRow;

$('chars-save').addEventListener('click', async () => {
  const rows = $('chars-form').querySelectorAll('.char-row');
  const chars = [];
  for (const r of rows) {
    const obj = {};
    r.querySelectorAll('[data-key]').forEach(inp => obj[inp.dataset.key] = inp.value);
    if (!obj.name || !obj.name.trim()) continue;
    chars.push(obj);
  }
  if (!chars.length) { alert('Nessun PG da salvare'); return; }
  if (chars.length > 5) { alert('Massimo 5 PG'); return; }
  if (!chars.some(c => c.player_type === 'human')) {
    alert('Almeno 1 giocatore umano richiesto'); return;
  }
  const r = await fetch('/api/characters', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ characters: chars }),
  });
  const d = await r.json();
  if (d.error) { alert('⚠ ' + d.error); return; }
  await refreshState();
  renderCharsPreview(d.characters);
  await loadPartyPickList();
});

$('chars-delete').addEventListener('click', async () => {
  if (!confirm('Eliminare TUTTI i personaggi? Operazione irreversibile.')) return;
  await fetch('/api/characters', { method: 'DELETE' });
  $('chars-form').innerHTML = '';
  $('chars-preview').innerHTML = '';
  addCharRow();
  await refreshState();
  await loadPartyPickList();
});

// ─── Selezione party da schede salvate (runtime/personaggi/<nome>.json) ──
// Lista checkbox: le schede salvate sono indipendenti dal party corrente,
// e l'utente seleziona quali caricare come party della prossima partita.
async function loadPartyPickList() {
  const list = $('party-pick-list');
  if (!list) return;
  list.innerHTML = '<p class="muted">Caricamento…</p>';
  try {
    const r = await fetch('/api/characters/available');
    const d = await r.json();
    const arr = (d && d.characters) || [];
    const cnt = $('party-count');
    if (cnt) cnt.textContent = arr.length ? `(${arr.length} scheda${arr.length === 1 ? '' : 'e'} salvata${arr.length === 1 ? '' : 'e'})` : '';
    if (!arr.length) {
      list.innerHTML = '<p class="empty">Nessuna scheda salvata. Crea un PG qui sotto e la troverai in questa lista.</p>';
      return;
    }
    list.innerHTML = '';
    for (const c of arr) {
      const row = el('label', { class: 'party-pick-row' });
      const cb = el('input');
      cb.type = 'checkbox';
      cb.value = c.name;
      cb.dataset.file = c.file;
      const meta = `${c.species || '?'} ${c.class || '?'} Lv${c.level || 1}` +
                   ` · ${c.background || ''} · ${c.alignment || ''}` +
                   ` · ${c.player_type === 'ai' ? '🤖 IA' : '🙂 umano'}`;
      const txt = el('span', { class: 'party-pick-meta' });
      txt.innerHTML = `<strong>${escapeHtml(c.name)}</strong><br><small>${escapeHtml(meta)}</small>`;
      const del = el('button', { class: 'party-pick-del', text: '🗑' });
      del.title = 'Cancella questa scheda salvata';
      del.addEventListener('click', async (ev) => {
        ev.preventDefault();
        if (!confirm(`Eliminare la scheda di ${c.name}?`)) return;
        await fetch('/api/characters/' + encodeURIComponent(c.name), { method: 'DELETE' });
        await loadPartyPickList();
        await refreshState();
      });
      row.appendChild(cb);
      row.appendChild(txt);
      row.appendChild(del);
      list.appendChild(row);
    }
  } catch (e) {
    list.innerHTML = '<p class="empty">⚠ Errore nel caricamento delle schede salvate.</p>';
  }
}

const partyReloadBtn = $('party-reload');
if (partyReloadBtn) partyReloadBtn.addEventListener('click', loadPartyPickList);

const partyLoadBtn = $('party-load');
if (partyLoadBtn) partyLoadBtn.addEventListener('click', async () => {
  const checks = $('party-pick-list').querySelectorAll('input[type=checkbox]:checked');
  const names = Array.from(checks).map(c => c.value);
  if (!names.length) { alert('Seleziona almeno 1 personaggio'); return; }
  if (names.length > 5) { alert('Massimo 5 personaggi nel party'); return; }
  const r = await fetch('/api/party/load', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ names }),
  });
  const d = await r.json();
  if (d.error) { alert('⚠ ' + d.error); return; }
  await refreshState();          // aggiorna gameState + colonna giocatori
  await loadCharsIntoForm();
  renderCharsPreview(d.characters);
  // Chiudi il modale così la colonna sinistra (dietro al modale) con i
  // giocatori appena caricati è subito visibile.
  closeModal('chars');
  renderPlayers();               // ridisegno esplicito della colonna
  alert(`🎒 Party caricato: ${d.count} personagg${d.count === 1 ? 'io' : 'i'}.`);
});

function renderCharsPreview(chars) {
  const root = $('chars-preview');
  root.innerHTML = '';
  if (!chars || !chars.length) return;
  const h = el('h3', { text: 'Schede generate' });
  root.appendChild(h);
  for (const c of chars) root.appendChild(sheetBlock(c));
}

function sheetBlock(s) {
  const b = el('div', { class: 'sheet-block' });
  const stats = ['FOR', 'DES', 'COS', 'INT', 'SAG', 'CAR'];
  const statsHtml = stats.map(ab => {
    const v = (s.stats && s.stats[ab]) || { score: 10, mod: 0 };
    const mod = v.mod >= 0 ? '+' + v.mod : v.mod;
    const disp = statDisplay(v, ab, s.magic_items);
    const tot = statTotal(v, ab, s.magic_items);
    const title = (disp !== String(tot)) ? ` title="Totale: ${tot}"` : '';
    return `<div class="sheet-stat"><div class="sheet-stat-name">${ab}</div><div class="sheet-stat-val"${title}>${disp}</div><div class="sheet-stat-mod">${mod}</div></div>`;
  }).join('');
  const ph = s.physical || {};
  const gsym = s.gender === 'Femmina' ? '♀' : (s.gender === 'Maschio' ? '♂' : '');
  const physHtml = (s.gender || ph.height_cm) ? `
    <div class="sheet-phys">
      <div><strong>${gsym} ${escapeHtml(s.gender || '?')}</strong>${
        ph.age ? ` · Età ${ph.age} anni` : ''}${
        ph.build ? ` · corporatura ${escapeHtml(ph.build)}` : ''}</div>
      ${(ph.height_cm || ph.weight_kg) ? `<div><strong>Statura:</strong> ${ph.height_cm || '?'} cm · <strong>Peso:</strong> ${ph.weight_kg || '?'} kg</div>` : ''}
      ${(ph.hair || ph.eyes || ph.skin) ? `<div><strong>Capelli:</strong> ${escapeHtml(ph.hair || '?')} · <strong>Occhi:</strong> ${escapeHtml(ph.eyes || '?')} · <strong>Pelle:</strong> ${escapeHtml(ph.skin || '?')}</div>` : ''}
      ${ph.distinctive ? `<div><strong>Segni distintivi:</strong> ${escapeHtml(ph.distinctive)}</div>` : ''}
    </div>` : '';
  b.innerHTML = `
    <h3>${escapeHtml(s.name)} ${gsym} — ${escapeHtml(s.species || s.race || '')} ${escapeHtml(s.class || '')} Lv${s.level || 1}</h3>
    <div><strong>BG:</strong> ${escapeHtml(s.background || '')} · <strong>All:</strong> ${escapeHtml(s.alignment || '')} · <strong>Tipo:</strong> ${escapeHtml(s.player_type || '')}</div>
    ${physHtml}
    <div><strong>HP:</strong> ${s.hp ? s.hp.current + '/' + s.hp.max : '?'} · <strong>CA:</strong> ${acDisplay(s)}${(acDisplay(s) !== String(acTotal(s))) ? ` <span class="se-hint">(totale ${acTotal(s)})</span>` : ''} · <strong>Iniz:</strong> ${s.initiative >= 0 ? '+' + s.initiative : s.initiative} · <strong>Vel:</strong> ${s.speed || '?'}m</div>
    <div class="sheet-stats">${statsHtml}</div>
    <div><strong>TS:</strong> ${(s.saving_throws || []).join(', ')}</div>
    <div><strong>Competenze:</strong> ${(s.skills || []).join(', ')}</div>
    <div><strong>Lingue:</strong> ${(s.languages || []).join(', ')}</div>
    <div><strong>Equip:</strong> ${(s.equipment || []).join(', ')}</div>
    ${(s.weapons && s.weapons.length) ? `<div><strong>⚔ Armi:</strong> ${(s.weapons || []).map(w => escapeHtml(weaponLabel(w))).join(', ')}</div>` : ''}
    ${(s.magic_items && s.magic_items.length) ? `<div class="sheet-magic"><strong>✨ Oggetti magici:</strong>${itemListHtml(s.magic_items, { attune: true })}</div>` : ''}
    <div><strong>💰 Monete:</strong> ${escapeHtml(treasureText(s.treasure))}</div>
    ${s.weapon_masteries && s.weapon_masteries.length ? `<div><strong>Weapon Mastery:</strong> ${s.weapon_masteries.join(', ')}</div>` : ''}
    <div><strong>Tratti:</strong> ${(s.traits || []).join('; ')}</div>
    <div><strong>Capacità:</strong> ${(s.class_features || []).join('; ')}</div>
    ${spellsBlockHtml(s)}
    <div><strong>Talento Origine:</strong> ${escapeHtml(s.feat_origin || '—')}</div>
  `;
  return b;
}

// Nome di un incantesimo, qualunque sia il formato (stringa o {name,...}).
function spellName(sp) { return typeof sp === 'string' ? sp : (sp && sp.name) || ''; }
// Descrizione di un incantesimo ('' se non disponibile).
function spellDesc(sp) { return typeof sp === 'string' ? '' : (sp && sp.desc) || ''; }
// Livello di un incantesimo (0 = trucchetto).
function spellLevel(sp) { return typeof sp === 'string' ? 0 : (sp && sp.level) | 0; }

// Aggregatore lato client dei bonus oggetti magici (specchio JS di
// `dnd.character._aggregate_item_modifiers`). Serve per due cose:
//   1) calcolare il break-down "base+bonus" anche quando il server invia
//      ancora dati stale (ac == ac_base) ma gli oggetti hanno modifiers;
//   2) mostrare il totale aggiornato nel tooltip CA/stat sulla scheda.
// Considera l'oggetto attivo se `itemAttuned` (esplicito O auto da desc).
function aggregateItemMods(items) {
  const out = {
    ac: 0, initiative: 0, speed: 0, hp_max: 0,
    save_dc: 0, attack_bonus: 0, save_all: 0, proficiency_bonus: 0,
    stat: {}, save: {},
  };
  if (!Array.isArray(items)) return out;
  for (const it of items) {
    if (!itemAttuned(it)) continue;
    const mods = itemModifiers(it);
    if (!mods || typeof mods !== 'object') continue;
    for (const k of ['ac','initiative','speed','hp_max',
                     'save_dc','attack_bonus','save_all','proficiency_bonus']) {
      if (mods[k]) out[k] += +mods[k];
    }
    if (mods.stat) for (const ab of Object.keys(mods.stat)) {
      if (mods.stat[ab]) out.stat[ab] = (out.stat[ab] || 0) + (+mods.stat[ab]);
    }
    if (mods.save) for (const ab of Object.keys(mods.save)) {
      if (mods.save[ab]) out.save[ab] = (out.save[ab] || 0) + (+mods.save[ab]);
    }
  }
  return out;
}

// CA visualizzata come "base+bonus" quando un oggetto magico aggiunge un
// bonus alla CA (es. "12+2" per Anello di Protezione su PG con CA base 12).
// Strategia in cascata:
//   • se il server ha già applicato il bonus → usa ac/ac_base
//   • altrimenti calcola il bonus aggregando i magic_items lato client,
//     così la scheda mostra il break-down anche con dati non normalizzati
//     (server pre-restart, schede vecchie, …).
function acDisplay(s) {
  if (!s || s.ac == null) return '?';
  const total = s.ac | 0;
  const serverBase = s.ac_base != null ? s.ac_base | 0 : total;
  let base, bonus;
  if (total !== serverBase) {
    base = serverBase;
    bonus = total - serverBase;
  } else {
    const clientBonus = aggregateItemMods(s.magic_items).ac | 0;
    if (!clientBonus) return String(total);
    base = total;
    bonus = clientBonus;
  }
  return `${base}${bonus >= 0 ? '+' : ''}${bonus}`;
}

// CA effettiva (numero singolo) tenendo conto del bonus oggetti anche
// quando il server non l'ha ancora propagato a `s.ac`. Usato nei tooltip.
function acTotal(s) {
  if (!s || s.ac == null) return '?';
  const total = s.ac | 0;
  const base = s.ac_base != null ? s.ac_base | 0 : total;
  if (total !== base) return total;
  return total + (aggregateItemMods(s.magic_items).ac | 0);
}

// Idem per le caratteristiche (FOR/DES/...). Un oggetto come "Cintura della
// Forza dei Giganti · +6 FOR" alza `score` ma conserva `score_base`: la
// scheda deve mostrare "10+6". Fallback client-side analogo al caso CA.
function statDisplay(v, ab, items) {
  if (!v || typeof v !== 'object') return '—';
  const total = v.score | 0;
  const serverBase = v.score_base != null ? v.score_base | 0 : total;
  let base, bonus;
  if (total !== serverBase) {
    base = serverBase;
    bonus = total - serverBase;
  } else if (ab) {
    const agg = aggregateItemMods(items).stat || {};
    const clientBonus = (agg[ab] | 0);
    if (!clientBonus) return String(total);
    base = total;
    bonus = clientBonus;
  } else {
    return String(total);
  }
  return `${base}${bonus >= 0 ? '+' : ''}${bonus}`;
}

// Totale di una stat dopo bonus oggetti — analogo a `acTotal`.
function statTotal(v, ab, items) {
  if (!v || typeof v !== 'object') return '—';
  const total = v.score | 0;
  const base = v.score_base != null ? v.score_base | 0 : total;
  if (total !== base) return total;
  if (!ab) return total;
  return total + ((aggregateItemMods(items).stat || {})[ab] | 0);
}

// ─── Oggetti / armi / artefatti magici — accesso uniforme ────────────────
// Una voce dell'inventario può essere una stringa ("Spada lunga" oppure
// "Anello di Protezione | +1 a CA e TS | attuned") o un dict ricco
// ({name, desc, attuned, slot}). Questi helper estraggono i campi in
// modo sicuro così l'UI non deve sapere quale forma è.
function _itemParts(it) {
  if (typeof it !== 'string') return null;
  return it.split('|').map(s => s.trim());
}
// Etichetta arma: nome + tiri pronti se l'arma è strutturata
// ("Spada lunga (1d20+4 → 1d8+3)"); ricade sul solo nome per le stringhe
// o le armi magiche aggiunte dal DM senza dado.
function weaponLabel(w) {
  const n = itemName(w);
  if (w && typeof w === 'object' && w.damage_roll) {
    const atk = w.attack_roll || '1d20';
    return `${n} (${atk} → ${w.damage_roll})`;
  }
  return n;
}
function itemName(it) {
  if (typeof it === 'string') {
    const p = _itemParts(it);
    return (p && p[0]) || '';
  }
  return (it && it.name) || '';
}
function itemDesc(it) {
  if (typeof it === 'string') {
    const p = _itemParts(it);
    if (!p || p.length < 2) return '';
    // descrizione = tutti i pezzi tra nome e l'eventuale flag finale
    const tail = p.slice(1).filter(x => x.toLowerCase() !== 'attuned'
                                    && x.toLowerCase() !== 'sintonizzato');
    return tail.join(' · ');
  }
  return (it && it.desc) || '';
}
function itemAttuned(it) {
  if (typeof it === 'string') {
    const p = _itemParts(it);
    return !!(p && p.some(x => /^(attuned|sintonizzato)$/i.test(x)));
  }
  if (typeof it !== 'object' || it === null) return false;
  if (it.attuned) return true;
  // Specchio della normalizzazione backend: un oggetto con bonus dichiarati
  // nella descrizione è considerato attivo sulla scheda.
  const auto = extractDescModifiers(it.desc || '');
  return Object.keys(auto).length > 0;
}

// Lista <ul> di oggetti generici (armi, oggetti magici, equipaggiamento
// quando arriva già in formato strutturato): nome + descrizione + badge
// "sintonizzato" + bonus oggetto (es. "+1 CA · +1 a tutti i TS").
function itemListHtml(arr, opts) {
  if (!arr || !arr.length) return '<p class="empty">— nessuno —</p>';
  const showAttune = !!(opts && opts.attune);
  return '<ul class="sp-ul">' + arr.map(it => {
    const n = itemName(it), d = itemDesc(it);
    const attuned = itemAttuned(it);
    const att = (showAttune && attuned)
      ? ' <span class="sp-lv">sintonizzato</span>' : '';
    const mods = itemModifiers(it);
    const modTxt = modifiersText(mods);
    // I bonus si applicano SOLO se sintonizzati; quando non lo sono, mostra
    // i mod come "inattivi" così l'utente sa che l'oggetto c'è ma è dormiente.
    const modHtml = modTxt
      ? `<div class="sp-desc sp-mods${attuned ? '' : ' inactive'}">${
            escapeHtml(modTxt)}${attuned ? '' : ' <em>(non sintonizzato)</em>'}</div>`
      : '';
    return `<li class="sp-row"><b>${escapeHtml(n)}</b>${att}`
         + `${d ? `<div class="sp-desc">${escapeHtml(d)}</div>` : ''}`
         + `${modHtml}</li>`;
  }).join('') + '</ul>';
}

// Modifiers riconosciuti dall'editor (formato flag string): coincidono
// coi campi accettati da `dnd.character._aggregate_item_modifiers`.
// Sintassi nell'input: "ac+1", "save_all+1", "hp+5", "init+2",
// "for+1"/"des+2"/... (stat), "save_sag+1" (TS singolo), "dc+1" (save_dc),
// "atk+1" (attack_bonus), "speed+1.5".
const ITEM_FLAG_REGEX = /^(ac|init(?:iative)?|speed|hp(?:_max)?|dc|save_dc|atk|attack_bonus|save_all|for|des|cos|int|sag|car|save_for|save_des|save_cos|save_int|save_sag|save_car)([+-]\d+(?:\.\d+)?)$/i;
const ITEM_FLAG_ALIASES = {
  init: 'initiative', hp: 'hp_max', dc: 'save_dc', atk: 'attack_bonus',
};
const STAT_KEYS = new Set(['FOR','DES','COS','INT','SAG','CAR']);

// Estrae bonus dichiarati in linguaggio naturale dalla descrizione di un
// oggetto magico: "+2 CA", "+1 a tutti i TS", "+2 FOR", "-1 iniziativa".
// Specchio JS di `dnd.character._extract_desc_modifiers` (Python). Quando
// la scheda passa dal backend i bonus sono già nel campo `modifiers`; qui
// li ricaviamo lato client così l'editor visualizza il bonus subito senza
// round-trip e la PC-card mostra "CA 12+2" anche per voci appena scritte.
const DESC_LABEL_TO_KEY = {
  'ca': ['scalar','ac'], 'ac': ['scalar','ac'],
  'hp': ['scalar','hp_max'], 'hp max': ['scalar','hp_max'],
  'hp massimi': ['scalar','hp_max'],
  'pf': ['scalar','hp_max'], 'pf max': ['scalar','hp_max'],
  'pf massimi': ['scalar','hp_max'], 'punti ferita': ['scalar','hp_max'],
  'iniziativa': ['scalar','initiative'], 'init': ['scalar','initiative'],
  'velocità': ['scalar','speed'], 'velocita': ['scalar','speed'],
  'speed': ['scalar','speed'], 'movimento': ['scalar','speed'],
  'cd': ['scalar','save_dc'], 'cd incantesimi': ['scalar','save_dc'],
  'save dc': ['scalar','save_dc'],
  'tpc': ['scalar','attack_bonus'], 'attacco': ['scalar','attack_bonus'],
  'attacchi': ['scalar','attack_bonus'], 'bonus attacco': ['scalar','attack_bonus'],
  'tutti i ts': ['scalar','save_all'], 'ts generale': ['scalar','save_all'],
  'ts generali': ['scalar','save_all'], 'salvezze': ['scalar','save_all'],
  'tiri salvezza': ['scalar','save_all'],
  'competenza': ['scalar','proficiency_bonus'],
};
const DESC_ABILITY_ALIASES = {
  'for':'FOR','forza':'FOR','des':'DES','destrezza':'DES',
  'cos':'COS','costituzione':'COS','int':'INT','intelligenza':'INT',
  'sag':'SAG','saggezza':'SAG','car':'CAR','carisma':'CAR',
};
const DESC_BONUS_RE = new RegExp(
  '([+-]?\\d+)\\s*' +
  '(?:a(?:lla|lle|i|gli|l)?\\s+|al\\s+|alla\\s+)?' +
  '(' +
    'ts\\s+(?:for|des|cos|int|sag|car|forza|destrezza|costituzione|intelligenza|saggezza|carisma)' +
    '|tutti\\s+i\\s+ts|ts\\s+general[ei]|tiri\\s+salvezza|salvezze' +
    '|cd\\s+incantesimi|save\\s+dc' +
    '|hp\\s+(?:max|massimi)?|pf\\s+(?:max|massimi)?|punti\\s+ferita' +
    '|bonus\\s+attacco' +
    '|velocit[àa]|iniziativa|movimento|speed|init' +
    '|competenza' +
    '|ca|ac' +
    '|tpc|attacco|attacchi' +
    '|for(?:za)?|des(?:trezza)?|cos(?:tituzione)?|int(?:elligenza)?' +
    '|sag(?:gezza)?|car(?:isma)?' +
  ')\\b',
  'gi'
);
function extractDescModifiers(desc) {
  if (typeof desc !== 'string' || !desc.trim()) return {};
  const out = {};
  DESC_BONUS_RE.lastIndex = 0;
  let m;
  while ((m = DESC_BONUS_RE.exec(desc)) !== null) {
    const val = parseInt(m[1], 10);
    if (!Number.isFinite(val) || val === 0) continue;
    const label = m[2].toLowerCase().replace(/\s+/g, ' ').trim();
    // TS singola caratteristica
    if (label.startsWith('ts ')) {
      const abRaw = label.slice(3).trim();
      const ab = DESC_ABILITY_ALIASES[abRaw];
      if (ab) {
        out.save = out.save || {};
        out.save[ab] = (out.save[ab] || 0) + val;
      }
      continue;
    }
    const mapping = DESC_LABEL_TO_KEY[label];
    if (mapping) {
      out[mapping[1]] = (out[mapping[1]] || 0) + val;
      continue;
    }
    const ab = DESC_ABILITY_ALIASES[label];
    if (ab) {
      out.stat = out.stat || {};
      out.stat[ab] = (out.stat[ab] || 0) + val;
    }
  }
  return out;
}

// Parsa una stringa di item "Nome | descrizione | attuned, ac+1, save_all+1"
// → {name, desc, attuned, modifiers}. Tollerante: virgole o spazi tra le
// flag, case-insensitive. Le flag sconosciute finiscono in `desc` come
// parole sciolte (non gettate via).
function parseItemString(s) {
  const parts = String(s || '').split('|').map(x => x.trim()).filter(Boolean);
  if (!parts.length) return null;
  const out = { name: parts[0], desc: '', attuned: false, modifiers: {} };
  const flagParts = [];
  for (let i = 1; i < parts.length; i++) {
    // Una pipe può contenere sia descrizione che flag. Distinguiamo:
    // se TUTTI i token (separati da virgola/spazio) sono flag valide
    // → è un blocco flag; altrimenti è descrizione.
    const tokens = parts[i].split(/[,\s]+/).filter(Boolean);
    const allFlags = tokens.every(t =>
      /^(attuned|sintonizzato)$/i.test(t) || ITEM_FLAG_REGEX.test(t)
    );
    if (allFlags && tokens.length) {
      flagParts.push(...tokens);
    } else if (!out.desc) {
      out.desc = parts[i];
    } else {
      out.desc += ' · ' + parts[i];
    }
  }
  for (const tk of flagParts) {
    if (/^(attuned|sintonizzato)$/i.test(tk)) { out.attuned = true; continue; }
    const m = ITEM_FLAG_REGEX.exec(tk);
    if (!m) continue;
    let key = m[1].toLowerCase();
    let val = parseFloat(m[2]);
    if (ITEM_FLAG_ALIASES[key]) key = ITEM_FLAG_ALIASES[key];
    if (STAT_KEYS.has(key.toUpperCase())) {
      out.modifiers.stat = out.modifiers.stat || {};
      out.modifiers.stat[key.toUpperCase()] =
        (out.modifiers.stat[key.toUpperCase()] || 0) + val;
    } else if (key.startsWith('save_') && STAT_KEYS.has(key.slice(5).toUpperCase())) {
      const ab = key.slice(5).toUpperCase();
      out.modifiers.save = out.modifiers.save || {};
      out.modifiers.save[ab] = (out.modifiers.save[ab] || 0) + val;
    } else {
      out.modifiers[key] = (out.modifiers[key] || 0) + val;
    }
  }
  // Bonus dichiarati in linguaggio naturale dentro `desc` (es. "+2 CA"):
  // se non ci sono già flag esplicite, li promuoviamo a modifiers così
  // l'editor mostra "CA +2" sotto al nome e il backend applica la CA
  // anche quando il DM scrive solo descrizione testuale.
  if (!Object.keys(out.modifiers).length) {
    const auto = extractDescModifiers(out.desc);
    if (Object.keys(auto).length) {
      out.modifiers = auto;
      // Un oggetto che dichiara bonus meccanici è considerato sintonizzato:
      // il PG sta beneficiando dell'effetto descritto sulla scheda.
      if (!out.attuned) out.attuned = true;
    }
  }
  if (!Object.keys(out.modifiers).length) delete out.modifiers;
  return out;
}

// Modifiers di un item, qualunque sia il formato (dict o stringa parsata).
// Per i dict senza `modifiers` esplicito ricaviamo i bonus dalla
// descrizione (es. "Anello di protezione · +2 CA" → {ac:2}) così l'UI
// resta consistente col backend che fa la stessa normalizzazione.
function itemModifiers(it) {
  if (typeof it === 'string') {
    const p = parseItemString(it);
    return (p && p.modifiers) || {};
  }
  if (it && typeof it === 'object') {
    if (it.modifiers && Object.keys(it.modifiers).length) return it.modifiers;
    return extractDescModifiers(it.desc || '');
  }
  return {};
}

// Riassunto leggibile dei modifiers di un item: "+1 CA · +1 a tutti i TS".
function modifiersText(mods) {
  if (!mods || typeof mods !== 'object') return '';
  const parts = [];
  const sgn = n => (n >= 0 ? '+' : '') + n;
  const labels = {
    ac: 'CA', initiative: 'Iniziativa', speed: 'Velocità',
    hp_max: 'HP max', save_dc: 'CD incantesimi',
    attack_bonus: 'Bonus attacco', save_all: 'a tutti i TS',
    proficiency_bonus: 'Competenza',
  };
  for (const k of Object.keys(labels)) {
    if (mods[k]) parts.push(`${sgn(mods[k])} ${labels[k]}`);
  }
  if (mods.stat) {
    for (const ab of Object.keys(mods.stat)) {
      if (mods.stat[ab]) parts.push(`${sgn(mods.stat[ab])} ${ab}`);
    }
  }
  if (mods.save) {
    for (const ab of Object.keys(mods.save)) {
      if (mods.save[ab]) parts.push(`${sgn(mods.save[ab])} TS ${ab}`);
    }
  }
  return parts.join(' · ');
}

// Serializza un inventario per l'editor. Format esteso:
//   "Nome | descrizione | attuned, ac+1, save_all+1"
// La riga flags compare solo se ci sono flag attive (così armi e oggetti
// senza modifiers restano semplici).
function itemsToText(arr, opts) {
  const showAttune = !!(opts && opts.attune);
  return (arr || []).map(it => {
    const n = itemName(it), d = itemDesc(it);
    const parts = [n];
    if (d) parts.push(d);
    if (showAttune) {
      const flags = [];
      if (itemAttuned(it)) flags.push('attuned');
      const mods = itemModifiers(it);
      const sgn = n => (n >= 0 ? '+' : '') + n;
      for (const k of ['ac','initiative','speed','hp_max','save_dc',
                       'attack_bonus','save_all','proficiency_bonus']) {
        if (mods[k]) {
          const short = { initiative:'init', hp_max:'hp',
                          save_dc:'dc', attack_bonus:'atk' }[k] || k;
          flags.push(`${short}${sgn(mods[k])}`);
        }
      }
      if (mods.stat) for (const ab of Object.keys(mods.stat)) {
        if (mods.stat[ab]) flags.push(`${ab.toLowerCase()}${sgn(mods.stat[ab])}`);
      }
      if (mods.save) for (const ab of Object.keys(mods.save)) {
        if (mods.save[ab]) flags.push(`save_${ab.toLowerCase()}${sgn(mods.save[ab])}`);
      }
      if (flags.length) parts.push(flags.join(', '));
    }
    return parts.join(' | ');
  }).join('\n');
}

// Lista <ul> di incantesimi: nome in grassetto + descrizione sotto.
function spellListHtml(arr) {
  if (!arr || !arr.length) return '<p class="empty">— nessuno —</p>';
  return '<ul class="sp-ul">' + arr.map(sp => {
    const n = spellName(sp), d = spellDesc(sp), lv = spellLevel(sp);
    const lvTag = lv ? `<span class="sp-lv">L${lv}</span>` : '';
    return `<li class="sp-row">${lvTag}<b>${escapeHtml(n)}</b>`
         + `${d ? `<div class="sp-desc">${escapeHtml(d)}</div>` : ''}</li>`;
  }).join('') + '</ul>';
}

// Serializza una lista incantesimi per l'editor: "Nome | descrizione" per riga.
function spellsToText(arr) {
  return (arr || []).map(sp => {
    const n = spellName(sp), d = spellDesc(sp);
    return d ? `${n} | ${d}` : n;
  }).join('\n');
}

// ─── Catalogo SRD incantesimi (caricato on-demand, cache lato client) ──
// Il frontend lo usa per: (a) mostrare la descrizione di un incantesimo
// selezionato nell'editor scheda (help inline), (b) popolare i selettori
// "Aggiungi al libro" / "Aggiungi ai preparati" del Mago e degli altri
// incantatori, (c) ordinare gli incantesimi per livello.
//
// Una cache per CLASSE (più una "ALL" per il catalogo intero) così la
// stessa apertura dell'editor scheda di un Chierico filtra automatico
// alle voci clericali invece di mostrare l'intero catalogo SRD.
const _spellCache = {};            // {key: {spells, by_level, kind}}
let _spellCatalog = null;          // alias all'ultimo catalogo caricato
let _spellCatalogByLv = null;
let _spellCatalogKind = 'none';

async function loadSpellCatalog(cls) {
  const key = cls || '__ALL__';
  if (_spellCache[key]) {
    const c = _spellCache[key];
    _spellCatalog = c.spells;
    _spellCatalogByLv = c.by_level;
    _spellCatalogKind = c.kind || 'none';
    return _spellCatalog;
  }
  const url = cls ? `/api/spells_catalog?class=${encodeURIComponent(cls)}`
                  : '/api/spells_catalog';
  try {
    const r = await fetch(url);
    const d = await r.json();
    _spellCache[key] = {
      spells:   d.spells || {},
      by_level: d.by_level || {},
      kind:     d.kind || 'none',
    };
  } catch (e) {
    _spellCache[key] = { spells: {}, by_level: {}, kind: 'none' };
  }
  const c = _spellCache[key];
  _spellCatalog = c.spells;
  _spellCatalogByLv = c.by_level;
  _spellCatalogKind = c.kind;
  return _spellCatalog;
}

// Descrizione di un incantesimo cercando prima nella lista del PG, poi
// nel catalogo SRD globale (così funziona anche per voci aggiunte come
// solo nome senza desc).
function spellDescResolved(sp) {
  const d = spellDesc(sp);
  if (d) return d;
  const name = spellName(sp);
  const info = _spellCatalog && _spellCatalog[name];
  return (info && info.desc) || '';
}

// Livello di un incantesimo, fallback al catalogo SRD se assente.
function spellLevelResolved(sp) {
  const lv = spellLevel(sp);
  if (lv) return lv;
  const name = spellName(sp);
  const info = _spellCatalog && _spellCatalog[name];
  return (info && info.level) | 0;
}

// Lista incantesimi INTERATTIVA per l'editor: ogni voce è cliccabile e
// mostra la descrizione completa sotto al nome (toggleabile). Quando
// `opts.actions` è passato, aggiunge i bottoni per ciascuna voce.
//
// opts.actions: array di {label, icon, action, title?}
//   action(name, level, desc) → callback al click.
function spellListInteractive(arr, opts) {
  opts = opts || {};
  const actions = opts.actions || [];
  if (!arr || !arr.length) {
    return '<p class="empty">— nessuno —</p>';
  }
  return '<ul class="sp-ul sp-interactive">' + arr.map((sp, i) => {
    const n = spellName(sp);
    const d = spellDescResolved(sp);
    const lv = spellLevelResolved(sp);
    const lvTag = lv ? `<span class="sp-lv">L${lv}</span>` : '';
    const btns = actions.map(a =>
      `<button type="button" class="sp-btn ${a.cls || ''}" `
      + `data-sp-action="${a.action}" data-sp-name="${escapeHtml(n)}" `
      + `${a.title ? `title="${escapeHtml(a.title)}"` : ''}>`
      + `${a.icon || ''} ${escapeHtml(a.label || '')}</button>`).join('');
    return `<li class="sp-row" data-sp-row="${i}">
        <div class="sp-row-head">
          ${lvTag}<b class="sp-name" title="${escapeHtml(d || '(nessuna descrizione)')}">${escapeHtml(n)}</b>
          <span class="sp-row-actions">${btns}</span>
        </div>
        ${d ? `<div class="sp-desc">${escapeHtml(d)}</div>` : ''}
      </li>`;
  }).join('') + '</ul>';
}

// Slots <select> per scegliere un incantesimo dal catalogo. Raggruppato
// per livello, con descrizione mostrata sotto al cambio selezione.
function spellPickerHtml(prefix, opts) {
  opts = opts || {};
  const maxLv = opts.maxLevel != null ? opts.maxLevel : 9;
  const minLv = opts.minLevel != null ? opts.minLevel : 0;
  const cat = _spellCatalogByLv || {};
  const groups = [];
  for (let lv = minLv; lv <= maxLv; lv++) {
    const list = cat[lv] || [];
    if (!list.length) continue;
    const label = lv === 0 ? 'Trucchetti (livello 0)' : `Livello ${lv}`;
    groups.push(
      `<optgroup label="${label}">`
      + list.map(s =>
          `<option value="${escapeHtml(s.name)}" `
          + `data-lv="${s.level}" `
          + `data-desc="${escapeHtml(s.desc || '')}">${escapeHtml(s.name)}</option>`)
        .join('')
      + `</optgroup>`);
  }
  if (!groups.length) {
    return `<div class="empty">Catalogo non disponibile</div>`;
  }
  return `
    <div class="sp-picker">
      <select id="${prefix}-sel" class="sp-picker-sel">
        <option value="">— scegli un incantesimo —</option>
        ${groups.join('')}
      </select>
      <button type="button" class="primary" id="${prefix}-btn">${escapeHtml(opts.btnLabel || 'Aggiungi')}</button>
    </div>
    <div class="sp-picker-desc" id="${prefix}-desc"><em>Seleziona un incantesimo per leggerne la descrizione.</em></div>
  `;
}

// Sezione incantesimi sola-lettura (anteprima schede). '' se non incantatore.
function spellsBlockHtml(s) {
  if (!s || s.caster_type === 'none') return '';
  const sp = s.spells || {};
  const slots = sp.slots || {};
  if (!sp.ability && !Object.keys(slots).length) return '';
  const slotLine = Object.keys(slots).sort((a, b) => a - b).map(lv => {
    const sl = slots[lv] || {};
    const max = sl.max | 0, used = sl.used | 0;
    return `L${lv}: ${max - used}/${max}`;
  }).join(' · ');
  const ab = sp.attack_bonus;
  return `
    <div class="sheet-spells">
      <div><strong>✦ Incantesimi</strong>${sp.ability
        ? ` — ${escapeHtml(sp.ability)} · CD TS ${sp.save_dc} · attacco ${ab >= 0 ? '+' : ''}${ab}` : ''}</div>
      ${(sp.cantrips || []).length ? `<div class="sp-group"><strong>🔹 Trucchetti</strong> <span class="se-hint">(a volontà)</span>${spellListHtml(sp.cantrips)}</div>` : ''}
      ${(sp.known || []).length ? `<div class="sp-group"><strong>🔸 Incantesimi</strong>${spellListHtml(sp.known)}</div>` : ''}
      ${(sp.spellbook || []).length ? `<div class="sp-group"><strong>📖 Libro</strong>${spellListHtml(sp.spellbook)}</div>` : ''}
      ${slotLine ? `<div class="sp-slotline"><strong>Slot:</strong> ${slotLine}</div>` : ''}
    </div>`;
}

// ─── Editor scheda — modifica parametri, equipaggiamento, incantesimi ─

async function showSheet(s, name) {
  const root = $('sheet-content');
  root.innerHTML = '';
  const cname = name || (s && s.name) || '';
  root.dataset.sheetName = cname;
  // catalogo SRD: filtrato per classe del PG così Chierico vede solo
  // incantesimi divini, Mago vede arcani, ecc. Caricamento idempotente
  // e cachato per classe.
  if ((s && s.caster_type && s.caster_type !== 'none') || s && s.spells) {
    await loadSpellCatalog(s && s.class);
  }
  root.appendChild(buildSheetEditor(s || {}, cname));
  openModal('sheet');
}

// Imposta un valore su percorso annidato ("hp.current", "spells.slots.1.max").
function setNested(obj, path, val) {
  const parts = path.split('.');
  let cur = obj;
  for (let i = 0; i < parts.length - 1; i++) {
    if (typeof cur[parts[i]] !== 'object' || cur[parts[i]] === null) {
      cur[parts[i]] = {};
    }
    cur = cur[parts[i]];
  }
  cur[parts[parts.length - 1]] = val;
}

// Raccoglie tutti gli [data-f] dell'editor in un oggetto updates annidato.
// Per i campi inventario (magic_items, weapons) parsa ogni riga in dict
// {name, desc, attuned, modifiers}: così il backend riceve la struttura
// completa e `recompute_derived` può applicare i bonus (CA, TS, …).
function collectSheetEdits(root) {
  const u = {};
  root.querySelectorAll('[data-f]').forEach(inp => {
    let val;
    if (inp.tagName === 'TEXTAREA') {
      const lines = inp.value.split('\n').map(x => x.trim()).filter(Boolean);
      const key = inp.dataset.f;
      if (key === 'magic_items' || key === 'weapons') {
        val = lines.map(line => parseItemString(line)).filter(Boolean);
      } else {
        val = lines;
      }
    } else if (inp.type === 'number') {
      val = parseInt(inp.value, 10);
      if (isNaN(val)) val = 0;
    } else {
      val = inp.value.trim();
    }
    setNested(u, inp.dataset.f, val);
  });
  return u;
}

// Numero base di incantesimi CONOSCIUTI per known caster (Bardo/Stregone/
// Warlock/Ranger) in 5e — usato come limite "morbido" nel badge della UI.
// Per il Ranger in 5.5e i valori sono identici al Bardo entry-level.
const KNOWN_SPELLS_LIMIT = {
  Bardo:    [4, 5, 6, 7, 8, 9, 10, 11, 12, 14, 15, 15, 16, 18, 19, 19, 20, 22, 22, 22],
  Stregone: [2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 12, 13, 13, 14, 14, 15, 15, 15, 15],
  Warlock:  [2, 3, 4, 5, 6, 7, 8, 9, 10, 10, 11, 11, 12, 12, 13, 13, 14, 14, 15, 15],
  Ranger:   [0, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 11, 12, 12, 13, 13, 14, 14, 15, 15],
};

// Sezione incantesimi dell'editor scheda: slot pip + riposo lungo +
// trucchetti / preparati o conosciuti / libro interattivi.
//
// L'UI cambia in base al TIPO di caster:
//  • wizard (Mago): preparati + libro + bottoni learn/prepare/unprepare/forget
//  • preparation (Chierico/Druido/Paladino): preparati + picker dalla lista
//    di classe per aggiungere/togliere quotidianamente
//  • known (Bardo/Stregone/Warlock/Ranger): incantesimi conosciuti (lista
//    fissa modificabile solo a level-up, ma a runtime permettiamo gestione
//    diretta) + picker dalla lista di classe.
function _spellsEditorHtml(s, cname, sp, ab) {
  const slots = sp.slots || {};
  const lvs = Object.keys(slots).sort((a, b) => a - b);
  const slotsHtml = lvs.length ? lvs.map(lv => {
    const sl = slots[lv] || {};
    const mx = Math.max(0, sl.max | 0);
    const us = Math.max(0, Math.min(mx, sl.used | 0));
    let pips = '';
    for (let i = 0; i < mx; i++) pips += (i < mx - us) ? '●' : '○';
    return `<div class="se-slot">
        <span class="se-slot-lv">Liv. ${lv}</span>
        <span class="slot-pips">${pips || '—'}</span>
        <label>usati <input type="number" data-f="spells.slots.${lv}.used" value="${us}" min="0" max="99"></label>
        <label>max <input type="number" data-f="spells.slots.${lv}.max" value="${mx}" min="0" max="99"></label>
        <button type="button" class="sp-cast" data-cast="${lv}" data-char="${escapeHtml(cname)}"
          ${us >= mx ? 'disabled' : ''}>🔥 Lancia</button>
      </div>`;
  }).join('') : '<p class="empty">Nessuno slot a questo livello.</p>';

  const cls = s.class || '';
  // Tipo di caster, derivato dal catalogo appena caricato (filtrato per
  // classe): wizard|preparation|known. Fallback hard-coded se il catalogo
  // non l'ha esposto.
  const KIND_BY_CLASS = {
    Mago: 'wizard', Chierico: 'preparation', Druido: 'preparation',
    Paladino: 'preparation', Bardo: 'known', Stregone: 'known',
    Warlock: 'known', Ranger: 'known',
  };
  const kind = _spellCatalogKind !== 'none' ? _spellCatalogKind
                                            : (KIND_BY_CLASS[cls] || 'none');
  const isWizard = kind === 'wizard';
  const isKnown = kind === 'known';

  const lvNum = Math.max(1, parseInt(s.level || 1, 10) || 1);
  const abMod = (s.stats && sp.ability && s.stats[sp.ability]
                 && s.stats[sp.ability].mod) | 0;

  // Limite "conosciuti" / "preparati" calcolato secondo regole SRD:
  //  • preparation caster (Mago/Chierico/Druido/Paladino): livello + mod
  //    (per Paladino half-caster: livello/2 + mod, arrotondato per eccesso)
  //  • known caster: tabella SRD per livello PG.
  let preparedLimit = null;
  let limitLabel = 'preparati';
  if (kind === 'wizard' || cls === 'Chierico' || cls === 'Druido') {
    preparedLimit = Math.max(1, lvNum + abMod);
  } else if (cls === 'Paladino') {
    preparedLimit = Math.max(1, Math.ceil(lvNum / 2) + abMod);
  } else if (isKnown) {
    const table = KNOWN_SPELLS_LIMIT[cls];
    preparedLimit = table ? table[Math.min(table.length, lvNum) - 1] : null;
    limitLabel = 'conosciuti';
  }
  const preparedCount = (sp.known || []).length;

  // Numero MAX di livello slot disponibile → filtra il selettore "Prepara":
  // non si possono preparare incantesimi di livello > slot massimo posseduto.
  let maxSlotLv = 0;
  for (const lv of lvs) maxSlotLv = Math.max(maxSlotLv, parseInt(lv, 10) || 0);
  if (!maxSlotLv) maxSlotLv = 9;

  // Etichette adatte al tipo di caster.
  const knownTitle = isKnown ? '🔸 Incantesimi conosciuti — sempre lanciabili negli slot'
                             : '🔸 Incantesimi preparati — lanciabili oggi negli slot';
  const removeBtn = isKnown
    ? { label: 'Dimentica', icon: '🗑', action: 'forget', cls: 'sp-btn-danger',
        title: 'Rimuovi dagli incantesimi conosciuti' }
    : { label: 'Togli dai preparati', icon: '✖', action: 'unprepare',
        cls: 'sp-btn-danger',
        title: isWizard
          ? 'Rimuovi dagli incantesimi preparati (resta nel libro)'
          : 'Rimuovi dagli incantesimi preparati per oggi' };

  // Liste interattive
  const cantripsHtml = spellListInteractive(sp.cantrips, {
    actions: [{
      label: 'Dimentica', icon: '🗑', action: 'forget', cls: 'sp-btn-danger',
      title: 'Rimuovi questo trucchetto'
    }],
  });
  const knownHtml = spellListInteractive(sp.known, { actions: [removeBtn] });
  const bookHtml = isWizard ? spellListInteractive(sp.spellbook, {
    actions: [
      { label: 'Prepara', icon: '✦', action: 'prepare', cls: 'sp-btn-go',
        title: 'Aggiungilo agli incantesimi preparati per oggi' },
      { label: 'Dimentica', icon: '🗑', action: 'forget', cls: 'sp-btn-danger',
        title: 'Rimuovi dal libro (e dai preparati)' },
    ],
  }) : '';

  // Selettori per AGGIUNGERE incantesimi.
  //  • wizard: picker "Aggiungi al libro" → action learn (poi prepara dal libro).
  //  • preparation: picker "Prepara" → action prepare diretto.
  //  • known: picker "Impara" → action learn (entra direttamente in known).
  let addLabel, addAction, addHint;
  if (isWizard) {
    addLabel = 'Aggiungi al libro'; addAction = 'learn';
    addHint  = 'Sceglie un incantesimo dalla lista del Mago e lo aggiunge al tuo libro.';
  } else if (isKnown) {
    addLabel = 'Impara'; addAction = 'learn';
    addHint  = 'Sceglie un incantesimo dalla lista di classe e lo aggiunge ai tuoi conosciuti.';
  } else {
    addLabel = 'Prepara'; addAction = 'prepare';
    addHint  = 'Sceglie un incantesimo dalla lista di classe e lo prepara per oggi.';
  }
  const learnPicker = `<div class="se-sub">
        <div class="se-label">➕ ${escapeHtml(addLabel)}</div>
        <div class="se-hint">${escapeHtml(addHint)}</div>
        ${spellPickerHtml('sp-learn', { minLevel: 1, maxLevel: maxSlotLv, btnLabel: addLabel })}
       </div>`;
  const cantripPicker = `<div class="se-sub">
        <div class="se-label">➕ Aggiungi un trucchetto</div>
        ${spellPickerHtml('sp-cantrip', { minLevel: 0, maxLevel: 0, btnLabel: 'Aggiungi trucchetto' })}
       </div>`;

  const preparedBadge = (preparedLimit != null)
    ? `<span class="se-badge ${preparedCount > preparedLimit ? 'over' : ''}">`
      + `${preparedCount}/${preparedLimit} ${escapeHtml(limitLabel)}</span>`
    : '';

  let limitHint = '';
  if (isWizard) {
    limitHint = 'Il Mago prepara incantesimi attingendo dal suo libro. Limite quotidiano = livello + mod INT.';
  } else if (cls === 'Paladino') {
    limitHint = 'Half-caster: limite preparati = (livello/2 arrotondato per eccesso) + mod CAR.';
  } else if (kind === 'preparation') {
    limitHint = 'Limite quotidiano = livello + mod caratteristica da incantatore. Sceglili dalla lista di classe.';
  } else if (isKnown) {
    limitHint = `Numero di incantesimi conosciuti fissato dalla classe. Si modifica al level-up — qui per comodità puoi gestirli liberamente.`;
  }

  return `
    <h4>✦ Incantesimi</h4>
    <div class="se-spellmeta">Caratteristica <strong>${escapeHtml(sp.ability || '?')}</strong>
      · CD TS <strong>${sp.save_dc != null ? sp.save_dc : '?'}</strong>
      · Bonus attacco <strong>${ab != null ? (ab >= 0 ? '+' : '') + ab : '?'}</strong>
      · Tipo <strong>${escapeHtml({wizard:'Libro+Preparati',preparation:'Preparati (lista di classe)',known:'Conosciuti (lista di classe)'}[kind] || '—')}</strong>
      <span class="se-hint">(ricalcolati al salvataggio)</span></div>

    <div class="se-label">Slot incantesimo — «Lancia» consuma uno slot</div>
    <div class="se-slots">${slotsHtml}</div>
    ${(sp.slots && Object.keys(sp.slots).length)
      ? `<button type="button" class="se-rest" data-char="${escapeHtml(cname)}">🌙 Riposo lungo — HP pieni + slot ripristinati</button>`
      : ''}

    <div class="se-label">🔹 Trucchetti — a volontà, non consumano slot</div>
    ${cantripsHtml}
    ${cantripPicker}

    <div class="se-label">${knownTitle} ${preparedBadge}</div>
    ${limitHint ? `<div class="se-hint">${escapeHtml(limitHint)}</div>` : ''}
    ${knownHtml}
    ${isWizard
      ? `<div class="se-hint">Per aggiungere ai preparati, scegli un incantesimo dal libro qui sotto e clicca «Prepara».</div>`
      : learnPicker}

    ${isWizard ? `
      <div class="se-label">📖 Libro degli incantesimi</div>
      <div class="se-hint">Tutti gli incantesimi che hai appreso. Da qui puoi «Prepararne» alcuni per la giornata.</div>
      ${bookHtml}
      ${learnPicker}
    ` : ''}
  `;
}


function buildSheetEditor(s, name) {
  const wrap = el('div', { class: 'sheet-editor' });
  const ABIL = ['FOR', 'DES', 'COS', 'INT', 'SAG', 'CAR'];
  const hp = s.hp || {};
  const sp = s.spells || {};
  const tre = s.treasure || {};
  const isCaster = s.caster_type && s.caster_type !== 'none';
  const gsym = s.gender === 'Femmina' ? '♀' : (s.gender === 'Maschio' ? '♂' : '');

  const numF = (f, val, min, max) =>
    `<input type="number" data-f="${f}" value="${val == null ? 0 : val}"`
    + `${min != null ? ` min="${min}"` : ''}${max != null ? ` max="${max}"` : ''}>`;

  const statsHtml = ABIL.map(ab => {
    const v = (s.stats && s.stats[ab]) || { score: 10, mod: 0 };
    const mod = v.mod >= 0 ? '+' + v.mod : v.mod;
    const disp = statDisplay(v, ab, s.magic_items);
    const tot = statTotal(v, ab, s.magic_items);
    const bonusHint = (disp !== String(tot))
      ? `<div class="se-hint" title="Totale: ${tot}">${disp} = ${tot}</div>` : '';
    return `<div class="se-stat">
        <div class="se-stat-name">${ab}</div>
        <input type="number" class="se-stat-score" data-f="stats.${ab}.score"
               data-mod-for="${ab}" value="${v.score}" min="1" max="30">
        <div class="se-stat-mod" id="se-mod-${ab}">${mod}</div>
        ${bonusHint}
      </div>`;
  }).join('');

  const cname = s.name || name || '';
  let slotsHtml = '';
  if (isCaster) {
    const slots = sp.slots || {};
    const lvs = Object.keys(slots).sort((a, b) => a - b);
    slotsHtml = lvs.length ? lvs.map(lv => {
      const sl = slots[lv] || {};
      const mx = Math.max(0, sl.max | 0);
      const us = Math.max(0, Math.min(mx, sl.used | 0));
      let pips = '';
      for (let i = 0; i < mx; i++) pips += (i < mx - us) ? '●' : '○';
      return `<div class="se-slot">
          <span class="se-slot-lv">Liv. ${lv}</span>
          <span class="slot-pips">${pips || '—'}</span>
          <label>usati ${numF('spells.slots.' + lv + '.used', us, 0, 99)}</label>
          <label>max ${numF('spells.slots.' + lv + '.max', mx, 0, 99)}</label>
          <button type="button" class="sp-cast" data-cast="${lv}" data-char="${escapeHtml(cname)}"
            ${us >= mx ? 'disabled' : ''}>🔥 Lancia</button>
        </div>`;
    }).join('') : '<p class="empty">Nessuno slot a questo livello.</p>';
  }

  const ab = sp.attack_bonus;
  wrap.innerHTML = `
    <h3>${escapeHtml(s.name || name || '?')} ${gsym} — ${escapeHtml(s.species || s.race || '')} ${escapeHtml(s.class || '')}</h3>
    <div class="se-row">
      <label>Livello ${numF('level', s.level || 1, 1, 20)}</label>
      <label>XP ${numF('xp', s.xp || 0, 0)}</label>
      <label>CA ${numF('ac', s.ac || 10, 0)}${(acDisplay(s) !== String(acTotal(s))) ? ` <span class="se-hint" title="Totale CA: ${acTotal(s)}">${acDisplay(s)} = ${acTotal(s)}</span>` : ''}</label>
      <label>Velocità ${numF('speed', s.speed || 9, 0)}</label>
      <label>Iniziativa ${numF('initiative', s.initiative || 0)}</label>
    </div>
    ${(() => {
      const cur = Math.max(0, hp.current | 0);
      const mx  = Math.max(0, hp.max | 0);
      const tmp = Math.max(0, hp.temp | 0);
      const pct = mx ? Math.max(0, Math.min(100, (cur / mx) * 100)) : 0;
      const tmpPct = mx ? Math.max(0, Math.min(100 - pct, (tmp / mx) * 100)) : 0;
      const dmg = Math.max(0, mx - cur);
      const dmgClass = (cur === 0 && mx) ? 'dead'
                     : (pct <= 25) ? 'crit'
                     : (pct <= 50) ? 'low' : '';
      return `
      <div class="se-hp">
        <div class="se-hp-label">❤ HP</div>
        <div class="se-hp-bar-wrap ${dmgClass}" title="${cur} / ${mx} HP${tmp ? ` (+${tmp} temp)` : ''}${dmg ? ` · ${dmg} danni` : ''}">
          <div class="se-hp-bar" style="width:${pct}%"></div>
          ${tmpPct > 0 ? `<div class="se-hp-temp" style="left:${pct}%;width:${tmpPct}%"></div>` : ''}
          <div class="se-hp-text">${cur}/${mx}${tmp ? ` (+${tmp})` : ''}${dmg ? ` · −${dmg}` : ''}</div>
        </div>
      </div>`;
    })()}
    <div class="se-row">
      <label>HP attuali ${numF('hp.current', hp.current || 0)}</label>
      <label>HP max ${numF('hp.max', hp.max || 0, 0)}</label>
      <label>HP temp ${numF('hp.temp', hp.temp || 0, 0)}</label>
    </div>
    <h4>Caratteristiche</h4>
    <div class="se-stats">${statsHtml}</div>
    <h4>Equipaggiamento</h4>
    <textarea data-f="equipment" rows="5" placeholder="Un oggetto per riga">${(s.equipment || []).map(escapeHtml).join('\n')}</textarea>

    <h4>⚔ Armi tenute</h4>
    ${itemListHtml(s.weapons)}
    <label class="se-edit-lbl">Modifica armi <span class="se-hint">(una riga: Nome | descrizione)</span>
      <textarea data-f="weapons" rows="4" placeholder="Es: Spada lunga +1 | bonus +1 ai TPC e ai danni">${escapeHtml(itemsToText(s.weapons))}</textarea>
    </label>

    <h4>✨ Oggetti magici / artefatti</h4>
    ${itemListHtml(s.magic_items, { attune: true })}
    <label class="se-edit-lbl">Modifica oggetti magici
      <span class="se-hint">Una riga: Nome | descrizione | attuned, flag…<br>
      Flag supportate: ac+N · init+N · speed+N · hp+N · dc+N · atk+N ·
      save_all+N · for+N/des+N/cos+N/int+N/sag+N/car+N ·
      save_sag+N (TS singolo). Esempio: <code>Anello di Protezione | +1 CA e TS | attuned, ac+1, save_all+1</code></span>
      <textarea data-f="magic_items" rows="5" placeholder="Es: Cintura Forza Gigante | bonus FOR | attuned, for+2">${escapeHtml(itemsToText(s.magic_items, { attune: true }))}</textarea>
    </label>

    <h4>💰 Monete possedute</h4>
    <div class="se-row se-coins">
      <label>Platino (MP) ${numF('treasure.mp', tre.mp || 0, 0)}</label>
      <label>Oro (MO) ${numF('treasure.po', tre.po || 0, 0)}</label>
      <label>Argento (MA) ${numF('treasure.ma', tre.ma || 0, 0)}</label>
      <label>Rame (MR) ${numF('treasure.mr', tre.mr || 0, 0)}</label>
    </div>
    ${isCaster ? _spellsEditorHtml(s, cname, sp, ab) : ''}
    <div class="se-actions">
      <button class="primary" id="se-save">💾 Salva modifiche</button>
      <span id="se-msg" class="se-msg"></span>
    </div>
  `;

  // modificatore aggiornato in tempo reale mentre si modifica il punteggio
  wrap.querySelectorAll('.se-stat-score').forEach(inp => {
    inp.addEventListener('input', () => {
      const sc = parseInt(inp.value, 10);
      const m = isNaN(sc) ? 0 : Math.floor((sc - 10) / 2);
      const mEl = wrap.querySelector('#se-mod-' + inp.dataset.modFor);
      if (mEl) mEl.textContent = (m >= 0 ? '+' : '') + m;
    });
  });

  // lancio incantesimo: consuma uno slot del livello indicato
  wrap.querySelectorAll('.sp-cast').forEach(btn => {
    btn.addEventListener('click', () =>
      castSpell(btn.dataset.char, parseInt(btn.dataset.cast, 10)));
  });
  // riposo lungo: ripristina tutti gli slot
  const restBtn = wrap.querySelector('.se-rest');
  if (restBtn) {
    restBtn.addEventListener('click', () => restSpells(restBtn.dataset.char));
  }

  // Bottoni Prepara/Togli/Dimentica sulle liste interattive degli
  // incantesimi: ogni bottone porta data-sp-action e data-sp-name.
  wrap.querySelectorAll('button.sp-btn[data-sp-action]').forEach(btn => {
    btn.addEventListener('click', () => {
      const character = s.name || name;
      const spell = btn.dataset.spName;
      const action = btn.dataset.spAction;
      if (!character || !spell || !action) return;
      prepareSpellAction(character, spell, action);
    });
  });

  // Picker incantesimi (Aggiungi al libro / Prepara / Impara / Aggiungi
  // trucchetto): mostra descrizione SRD al cambio selezione; al click del
  // bottone chiama /api/prepare_spell con l'azione appropriata al caster.
  //  • wizard: learn → libro
  //  • known caster: learn → known (incantesimi conosciuti permanenti)
  //  • preparation caster: prepare → known (preparati quotidiani)
  const KIND_BY_CLASS = {
    Mago: 'wizard', Chierico: 'preparation', Druido: 'preparation',
    Paladino: 'preparation', Bardo: 'known', Stregone: 'known',
    Warlock: 'known', Ranger: 'known',
  };
  const kind = _spellCatalogKind !== 'none' ? _spellCatalogKind
                                            : (KIND_BY_CLASS[s.class] || 'none');
  const learnAction = (kind === 'wizard' || kind === 'known') ? 'learn'
                                                              : 'prepare';
  // Trucchetti: per Mago/known/preparation li trattiamo come "imparati"
  // (`learn` aggiunge a cantrips solo se è un trucchetto, ma il nostro
  // backend tratta tutto come known/spellbook a livello ≥1 — per cantrip
  // l'azione corretta è `prepare` su known, che però sposta in known
  // anziché in cantrips. Per coerenza con il modello esistente, il
  // picker trucchetto usa `prepare`: l'utente può comunque modificare
  // l'elenco trucchetti via merge profondo del CHAR_UPDATE in editor.)
  const pickers = [
    { sel: 'sp-learn-sel',   btn: 'sp-learn-btn',   desc: 'sp-learn-desc',
      action: learnAction },
    { sel: 'sp-cantrip-sel', btn: 'sp-cantrip-btn', desc: 'sp-cantrip-desc',
      action: 'prepare' },
  ];
  for (const p of pickers) {
    const sel = wrap.querySelector('#' + p.sel);
    const btn = wrap.querySelector('#' + p.btn);
    const desc = wrap.querySelector('#' + p.desc);
    if (!sel || !btn || !desc) continue;
    // Help inline: appena selezioni un incantesimo, la sua descrizione SRD
    // appare sotto al menu. Senza click: niente azione, solo lettura.
    sel.addEventListener('change', () => {
      const opt = sel.options[sel.selectedIndex];
      if (!opt || !opt.value) {
        desc.innerHTML = '<em>Seleziona un incantesimo per leggerne la descrizione.</em>';
        return;
      }
      const lv = opt.dataset.lv;
      const d = opt.dataset.desc || '(nessuna descrizione)';
      desc.innerHTML =
        `<div class="sp-picker-name"><span class="sp-lv">L${lv}</span><b>${escapeHtml(opt.value)}</b></div>`
      + `<div class="sp-picker-text">${escapeHtml(d)}</div>`;
    });
    btn.addEventListener('click', () => {
      const character = s.name || name;
      const spell = sel.value;
      if (!character || !spell) return;
      // I trucchetti vengono SEMPRE preparati direttamente (non vanno nel
      // libro): scelta azione in base al picker.
      const action = (p.sel === 'sp-cantrip-sel') ? 'prepare' : p.action;
      prepareSpellAction(character, spell, action);
    });
  }

  wrap.querySelector('#se-save').addEventListener('click',
    () => saveSheet(wrap, s.name || name));
  return wrap;
}

// Esegue un'azione di gestione incantesimo (prepare/unprepare/learn/forget)
// via /api/prepare_spell, poi ricarica la scheda nell'editor.
async function prepareSpellAction(character, spell, action) {
  try {
    const r = await fetch('/api/prepare_spell', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ character, spell, action }),
    });
    const d = await r.json();
    if (d.error) { alert('⚠ ' + d.error); return; }
    await refreshState();
    showSheet(d.sheet, character);
  } catch (e) {
    alert('⚠ ' + e.message);
  }
}

// Consuma uno slot incantesimo del livello dato, poi ricarica la scheda.
async function castSpell(character, level) {
  if (!character || !level) return;
  try {
    const r = await fetch('/api/cast_spell', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ character, level }),
    });
    const d = await r.json();
    if (d.error) { alert('⚠ ' + d.error); return; }
    await refreshState();
    showSheet(d.sheet, character);
  } catch (e) {
    alert('⚠ ' + e.message);
  }
}

// Riposo lungo: HP del personaggio al massimo, death-saves azzerati e
// tutti gli slot incantesimo usati ripristinati.
async function restSpells(character) {
  if (!character) return;
  try {
    const r = await fetch('/api/cast_spell', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ character, rest: true }),
    });
    const d = await r.json();
    if (d.error) { alert('⚠ ' + d.error); return; }
    await refreshState();
    showSheet(d.sheet, character);
  } catch (e) {
    alert('⚠ ' + e.message);
  }
}

async function saveSheet(root, name) {
  if (!name) return;
  const updates = collectSheetEdits(root);
  const msg = root.querySelector('#se-msg');
  const btn = root.querySelector('#se-save');
  btn.disabled = true;
  msg.textContent = 'Salvataggio...';
  msg.className = 'se-msg';
  try {
    const r = await fetch('/api/character_update', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, updates }),
    });
    const d = await r.json();
    if (d.error) {
      msg.textContent = '⚠ ' + d.error;
      msg.className = 'se-msg err';
      btn.disabled = false;
      return;
    }
    msg.textContent = '✓ Scheda aggiornata';
    msg.className = 'se-msg ok';
    await refreshState();
    showSheet(d.sheet, name);   // ricarica l'editor coi valori ricalcolati
  } catch (e) {
    msg.textContent = '⚠ ' + e.message;
    msg.className = 'se-msg err';
    btn.disabled = false;
  }
}

// ────────────────────────────────────────────────────────────────────
// Bestiary modal
// ────────────────────────────────────────────────────────────────────

$('btn-bestiary').addEventListener('click', () => {
  openModal('bestiary');
  loadBestiary();
});

async function loadBestiary() {
  const root = $('bestiary-list');
  root.textContent = 'Caricamento...';
  const cmin = $('cr-min').value || 0;
  const cmax = $('cr-max').value || 30;
  const r = await fetch(`/api/bestiary?cr_min=${cmin}&cr_max=${cmax}`);
  const d = await r.json();
  root.innerHTML = '';
  for (const m of d.monsters || []) {
    const c = el('div', { class: 'monster-card' });
    c.innerHTML = `
      <div class="mn">${escapeHtml(m.name_it || m.name)}</div>
      <div class="mi">CR ${m.cr} · XP ${m.xp} · HP ${(m.hp || {}).avg || '?'} · CA ${m.ac}</div>
      <div class="mi">${escapeHtml((m.habitat || []).join(', '))}</div>
    `;
    c.addEventListener('click', () => alert(JSON.stringify(m, null, 2)));
    root.appendChild(c);
  }
}
window.loadBestiary = loadBestiary;

// ────────────────────────────────────────────────────────────────────
// Debug modal — comunicazione frontend ⇄ server ⇄ DM
// ────────────────────────────────────────────────────────────────────

function debugEntryHtml(parts) {
  return parts.filter(Boolean).join('');
}

function renderDebugNet() {
  const root = $('debug-net');
  if (!DEBUG.net.length) {
    root.innerHTML = '<p class="empty">Nessuna richiesta registrata.</p>';
    return;
  }
  root.innerHTML = '';
  for (const e of [...DEBUG.net].reverse()) {
    const cls = e.error ? 'err' : (e.status >= 200 && e.status < 400 ? 'ok' : 'err');
    const d = el('div', { class: 'debug-entry ' + cls });
    d.innerHTML = debugEntryHtml([
      `<div class="debug-head">
         <span class="debug-method" data-method="${escapeHtml((e.method || '').toUpperCase())}">${escapeHtml(e.method)}</span>
         <span class="debug-url">${escapeHtml(e.url)}</span>
         <span class="debug-status">${e.error ? 'ERR' : e.status} · ${e.ms}ms</span>
         <span class="debug-ts">${escapeHtml(e.ts)}</span>
       </div>`,
      e.reqBody ? `<pre class="debug-body">→ ${escapeHtml(e.reqBody)}</pre>` : '',
      e.resBody ? `<pre class="debug-body">← ${escapeHtml(e.resBody)}</pre>` : '',
      e.error  ? `<pre class="debug-body err">⚠ ${escapeHtml(e.error)}</pre>` : '',
    ]);
    root.appendChild(d);
  }
}

// Esito del controllo coerenza mappa ↔ testo per uno scambio col DM.
function mapCoherenceHtml(rep) {
  if (!rep) return '';
  if (rep.ok) {
    return `<pre class="debug-body ok">🗺 Mappa coerente — ${rep.width}×${rep.height}`
         + `, party @ ${JSON.stringify(rep.party)}, X raggiungibile</pre>`;
  }
  return `<pre class="debug-body err">🗺 Mappa: ${escapeHtml(rep.issues.join(' · '))}</pre>`;
}

async function renderDebugDm() {
  const root = $('debug-dm');
  root.innerHTML = '<p class="empty">Caricamento...</p>';
  try {
    const r = await fetch('/api/debug');
    const d = await r.json();
    const ex = d.exchanges || [];
    if (!ex.length) {
      root.innerHTML = '<p class="empty">Nessuno scambio col DM.</p>';
      return;
    }
    root.innerHTML = '';
    for (const x of [...ex].reverse()) {
      const d2 = el('div', { class: 'debug-entry' });
      d2.innerHTML = debugEntryHtml([
        `<div class="debug-head">
           <span class="debug-method" data-method="DM">DM</span>
           <span class="debug-ts">${escapeHtml(x.ts || '')}</span>
         </div>`,
        `<pre class="debug-body">👤 ${escapeHtml(x.user || '')}</pre>`,
        `<pre class="debug-body">📥 RAW: ${escapeHtml(x.raw || '')}</pre>`,
        `<pre class="debug-body">📺 MOSTRATO: ${escapeHtml(x.shown || '')}</pre>`,
        (x.rolls && x.rolls.length)
          ? `<pre class="debug-body">🎲 ${escapeHtml(JSON.stringify(x.rolls))}</pre>` : '',
        (x.casts && x.casts.length)
          ? `<pre class="debug-body ${x.casts.some(c => !c.ok) ? 'err' : 'ok'}">✦ INCANTESIMI: ${
              escapeHtml(x.casts.map(c =>
                `${c.by || '?'} «${c.spell || '?'}» L${c.level} ${c.ok ? '✓' : '✗'} ${c.detail || ''}`
              ).join(' | '))}</pre>`
          : '',
        mapCoherenceHtml(x.map),
        `<pre class="debug-body ${x.map_extracted ? 'ok' : 'err'}">🗺 MAPPA ${
            x.map_extracted ? 'estratta' : 'NON estratta'} ${
            x.map_dims ? `(${x.map_dims[0]}×${x.map_dims[1]})` : ''}\n${
            escapeHtml(x.map_ascii || '— vuota —')}</pre>`,
        (x.sprites && x.sprites.length)
          ? `<pre class="debug-body ok">🎨 SPRITE pixel-art: ${escapeHtml(x.sprites.join(' '))}</pre>`
          : '',
      ]);
      root.appendChild(d2);
    }
  } catch (e) {
    root.innerHTML = '<p class="empty">Errore: ' + escapeHtml(e.message) + '</p>';
  }
}

function refreshDebug() {
  if (DEBUG.tab === 'net') renderDebugNet();
  else renderDebugDm();
}

$('btn-debug').addEventListener('click', () => {
  openModal('debug');
  refreshDebug();
});
$('debug-refresh').addEventListener('click', refreshDebug);
$('debug-clear').addEventListener('click', () => { DEBUG.net = []; refreshDebug(); });
$('debug-autorefresh').addEventListener('change', (e) => {
  clearInterval(DEBUG.timer);
  DEBUG.timer = e.target.checked ? setInterval(refreshDebug, 2000) : null;
});
document.querySelectorAll('.debug-tab').forEach(t => {
  t.addEventListener('click', () => {
    document.querySelectorAll('.debug-tab').forEach(x => x.classList.remove('active'));
    t.classList.add('active');
    DEBUG.tab = t.dataset.tab;
    $('debug-net').classList.toggle('hidden', DEBUG.tab !== 'net');
    $('debug-dm').classList.toggle('hidden', DEBUG.tab !== 'dm');
    refreshDebug();
  });
});

// ────────────────────────────────────────────────────────────────────
// Save / new game
// ────────────────────────────────────────────────────────────────────

// Ricostruisce la chat dallo storico conversazione salvato sul server.
// Ritorna il numero di messaggi ripristinati. Niente TTS durante il replay.
async function replayConversation() {
  try {
    const r = await fetch('/api/conversation');
    const d = await r.json();
    const history = d.history || [];
    const ttsBackup = TTS.enabled;
    TTS.enabled = false;
    // Reset lastDmTurn prima del replay: addMsg accumulerà naturalmente,
    // ma vogliamo che alla fine `lastDmTurn` contenga SOLO l'ultimo turno
    // DM (dall'ultimo `user` in poi) — non l'intero storico.
    lastDmTurn = [];
    for (const m of history) {
      if (m.role === 'user') addMsg('user', m.content);
      else if (m.role === 'assistant') addMsg('dm', m.content);
    }
    TTS.enabled = ttsBackup;
    return history.length;
  } catch (_) {
    return 0;
  }
}

// ────────────────────────────────────────────────────────────────────
// Polling: scena di apertura dell'avventura
// ────────────────────────────────────────────────────────────────────
// Dopo il caricamento (TXT o generato) il backend invia il briefing al
// DM e gli chiede di produrre IMMEDIATAMENTE la prima scena (mappa +
// narrazione + domanda al primo PG umano). La risposta del modello
// viene appesa a `conversation_history` lato server. Qui polliamo
// l'API finché non vediamo il nuovo messaggio assistant e lo
// renderizziamo come bolla DM.
async function pollForOpeningScene({ timeoutMs = 240000, intervalMs = 3000 } = {}) {
  // baseline: quanti messaggi assistant esistono già adesso
  let baseline = 0;
  try {
    const r = await fetch('/api/conversation');
    const d = await r.json();
    baseline = (d.history || []).filter(m => m.role === 'assistant').length;
  } catch (_) { /* baseline 0 */ }

  const t0 = Date.now();
  $('typing').classList.add('on');
  let lastPhase = '';
  try {
    while (Date.now() - t0 < timeoutMs) {
      await new Promise(res => setTimeout(res, intervalMs));
      // Stato briefing del webchat: se è in stato error, smettiamo subito
      // di aspettare e segnaliamo all'utente. Se cambia fase, lo loggiamo
      // così l'utente vede che Chromium sta lavorando.
      try {
        const sr = await fetch('/api/webchat/status');
        const sd = await sr.json();
        const last = sd.last || {};
        if (last.phase && last.phase !== lastPhase) {
          lastPhase = last.phase;
          if (last.phase === 'error') {
            addMsg('error', `⚠ DM in errore: ${last.error || 'sconosciuto'}`);
            return false;
          }
          if (last.phase === 'opening') {
            addMsg('system', `🌐 Apertura Chromium in corso: ${last.detail || ''}`);
          } else if (last.phase === 'sending') {
            addMsg('system', `📡 Invio briefing al DM: ${last.detail || ''}`);
          } else if (last.phase === 'waiting_inflight') {
            addMsg('system', `⏳ ${last.detail || 'attendo altro briefing'}`);
          }
        }
      } catch (_) { /* riprova al prossimo giro */ }
      try {
        const r = await fetch('/api/conversation');
        const d = await r.json();
        const hist = d.history || [];
        const asstMsgs = hist.filter(m => m.role === 'assistant');
        if (asstMsgs.length > baseline) {
          // nuovi messaggi DM: appendili in ordine
          const newOnes = asstMsgs.slice(baseline);
          for (const m of newOnes) addMsg('dm', m.content);
          await refreshState();
          await refreshAdventureBadge();
          return true;
        }
      } catch (_) { /* riprova al prossimo giro */ }
    }
  } finally {
    $('typing').classList.remove('on');
  }
  return false;
}

// ────────────────────────────────────────────────────────────────────
// Avventura TXT precaricata — il DM la fa giocare passo passo
// ────────────────────────────────────────────────────────────────────

function renderAdventureBadge() {
  const badge = $('adventure-badge');
  if (!badge) return;
  const beats = (gameState && gameState.adventure_beats) || [];
  const idx   = (gameState && gameState.adventure_index) || 0;
  const loaded = !!(gameState && gameState.adventure_loaded) && beats.length > 0;
  if (loaded) {
    badge.style.display = '';
    $('adv-name').textContent = gameState.adventure_title || 'Avventura';
    $('adv-progress').textContent = `${Math.min(idx, beats.length)}/${beats.length}`;
    const done = idx >= beats.length;
    badge.title = done
      ? 'Avventura conclusa — il DM prosegue libero'
      : ('Prossima scena:\n' + (beats[idx] || '').slice(0, 240));
  } else {
    badge.style.display = 'none';
  }
}

async function refreshAdventureBadge() {
  try {
    await refreshState();   // riallinea gameState (badge incluso)
  } catch (_) { /* ignore */ }
  renderAdventureBadge();
}

$('btn-adventure').addEventListener('click', () => {
  $('adventure-file').value = '';
  $('adventure-file').click();
});

$('adventure-file').addEventListener('change', async (e) => {
  const f = e.target.files && e.target.files[0];
  if (!f) return;
  if (f.size > 200 * 1024) {
    addMsg('error', '⚠ File troppo grande (max 200KB)');
    return;
  }
  try {
    const text = await f.text();
    const fd = new FormData();
    fd.append('file', new Blob([text], { type: 'text/plain' }), f.name);
    fd.append('title', f.name.replace(/\.txt$/i, ''));
    const r = await fetch('/api/load_adventure', { method: 'POST', body: fd });
    const d = await r.json();
    if (d.error) { addMsg('error', '⚠ ' + d.error); return; }
    if (d.rebriefed) {
      addMsg('system',
        `📜 Avventura «${d.title}» caricata: ${d.beats} scene (${d.chars} caratteri). `
        + `Il DM sta leggendo la trama e preparando la prima scena… (può richiedere 30-90 secondi)`);
      await refreshAdventureBadge();
      // Aspetta la scena di apertura prodotta dal DM in background.
      // Timeout largo: il briefing chunked con l'avventura intera può
      // superare abbondantemente i 4 minuti.
      const ok = await pollForOpeningScene({ timeoutMs: 360000, intervalMs: 4000 });
      if (!ok) {
        addMsg('system', '⚠ Timeout in attesa della scena di apertura. '
          + 'Prova a scrivere **START** manualmente.');
      }
    } else {
      addMsg('system',
        `📜 Avventura «${d.title}» caricata: ${d.beats} scene (${d.chars} caratteri). `
        + `DM non collegato: aprilo dal Setup, poi ricarica l'avventura per farla iniziare.`);
      await refreshAdventureBadge();
    }
  } catch (err) {
    addMsg('error', '⚠ Caricamento avventura fallito: ' + err.message);
  }
});

$('adv-clear').addEventListener('click', async (e) => {
  e.stopPropagation();
  if (!confirm('Togliere l\'avventura precaricata? Il DM proseguirà in modalità libera.')) return;
  try {
    await fetch('/api/adventure', { method: 'DELETE' });
    await refreshAdventureBadge();
    addMsg('system', '📜 Avventura rimossa.');
  } catch (err) {
    addMsg('error', '⚠ ' + err.message);
  }
});

$('btn-save').addEventListener('click', async () => {
  const r = await fetch('/api/save', { method: 'POST' });
  const d = await r.json();
  if (d.error) { addMsg('error', '⚠ ' + d.error); return; }
  addMsg('system', `💾 Partita salvata: ${d.messages} messaggi, ${d.players} PG.`);
});

$('btn-load').addEventListener('click', async () => {
  if (busy) return;
  if (!confirm('Caricare la partita salvata? I progressi non salvati andranno persi.')) return;
  try {
    const r = await fetch('/api/load', { method: 'POST' });
    const d = await r.json();
    if (d.error) { addMsg('error', '⚠ ' + d.error); return; }
    gameState = d.state;
    $('chat-log').innerHTML = '';
    // reset code client: azioni DM e tiri della sessione precedente non
    // devono sopravvivere al ripristino (stesso reset di «Nuova»).
    resetActionQueue();
    rollQueue = [];
    lastDmMessage = '';
    const n = await replayConversation();
    renderUI();
    if (d.resync) {
      const prefix = d.dm_opening
        ? `📂 Partita ripristinata: ${n} messaggi caricati. DM non era `
          + `collegato → sto aprendo Chromium e dopo passerò il `
          + `contesto in blocchi da ~2000 token. `
        : `📂 Partita ripristinata: ${n} messaggi caricati. Sto passando il `
          + `contesto al DM in blocchi da ~2000 token. `;
      addMsg('system', prefix
        + `Al termine pubblicherò il messaggio di ripartenza dell'avventura.`);
      // Il backend invia il briefing chunkato al modello in background e,
      // alla risposta finale, la passa a _apply_dm_response che la appende
      // a conversation_history. Polliamo finché compare il nuovo messaggio
      // DM e lo mostriamo come prima scena di ripresa.
      const arrived = await pollForOpeningScene({ timeoutMs: 360000, intervalMs: 4000 });
      if (!arrived) {
        addMsg('system',
          '⚠ Il DM non ha emesso un messaggio di ripartenza entro il timeout. '
          + 'Riprova premendo «Carica» oppure scrivi tu un\'azione per rilanciare.');
      }
    } else {
      addMsg('system',
        `📂 Partita ripristinata: ${n} messaggi caricati. Puoi riprendere.`);
    }
  } catch (e) {
    addMsg('error', '⚠ Errore nel caricamento: ' + e.message);
  }
});

$('btn-new').addEventListener('click', async () => {
  if (busy) { addMsg('system', 'DM occupato — attendi la fine del turno.'); return; }
  const hasChars = !!(gameState && (gameState.players || []).length);
  const ask = hasChars
    ? 'Iniziare una NUOVA avventura? Lo storico verrà cancellato. I personaggi già creati restano in gioco.'
    : 'Iniziare una NUOVA partita? Lo storico verrà cancellato.';
  if (!confirm(ask)) return;

  // Con party pronto e DM collegato genereremo SUBITO l'avventura su
  // misura: il server allora salta il briefing generico (il contesto
  // completo parte con la richiesta di generazione, in una chat nuova).
  const autoAdventure = hasChars && dmOpen;
  let d;
  try {
    const r = await fetch('/api/new_game', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ keep_characters: true, auto_adventure: autoAdventure }),
    });
    d = await r.json();
    if (!r.ok || d.error) {
      addMsg('error', '⚠ Nuova partita fallita: ' + (d.error || ('HTTP ' + r.status)));
      return;
    }
  } catch (e) {
    addMsg('error', '⚠ Nuova partita fallita: ' + e.message);
    return;
  }
  gameState = d.state;
  // Pulizia COMPLETA del client: chat, coda azioni DM, tiri accodati,
  // turno TTS. Senza questo reset un tiro rimasto in rollQueue dalla
  // partita vecchia partiva da solo nella nuova.
  $('chat-log').innerHTML = '';
  resetActionQueue();
  rollQueue = [];
  lastDmTurn = [];
  lastDmMessage = '';
  const kept = (gameState.players || []).length;
  addMsg('system', kept
    ? `Nuova avventura iniziata con ${kept} personagg${kept === 1 ? 'io' : 'i'} già pronti.`
    : 'Nuova partita iniziata. Apri Personaggi per crearne.');
  renderUI();
  await refreshAdventureBadge();

  // Generazione automatica dell'avventura su misura del party: premere
  // "Nuova" cancella la vecchia avventura (sopra) e chiede SUBITO al DM
  // una avventura nuova, salvata da app.py con un nome univoco.
  if (!kept) return;
  if (!dmOpen) {
    addMsg('system', 'Per generare un\'avventura su misura collega il DM dal Setup.');
    return;
  }
  try {
    busy = true;
    $('chat-send').disabled = true;
    $('typing').classList.add('on');
    addMsg('system', '📜 Apro una conversazione nuova col DM e gli chiedo '
      + 'un\'avventura su misura per il party… può richiedere fino a un '
      + 'minuto e mezzo. Non chiudere la pagina.');
    const gr = await fetch('/api/generate_adventure', { method: 'POST' });
    const gd = await gr.json();
    if (gd.error) {
      addMsg('error', '⚠ ' + gd.error);
      return;
    }
    addMsg('system',
      `📜 Avventura «${gd.title}» pronta: ${gd.beats} scene (${gd.chars} caratteri)`
      + (gd.file ? `, salvata come ${gd.file}` : '') + '. '
      + `Il DM sta preparando la prima scena…`);
    await refreshAdventureBadge();
    // Aspetta la scena di apertura prodotta dal briefing (chunked, lungo).
    const ok = await pollForOpeningScene({ timeoutMs: 360000, intervalMs: 4000 });
    if (!ok) {
      addMsg('system', '⚠ Timeout in attesa della scena di apertura. '
        + 'Prova a scrivere **START** manualmente.');
    }
  } catch (e) {
    addMsg('error', '⚠ Generazione fallita: ' + e.message);
  } finally {
    busy = false;
    $('chat-send').disabled = !dmOpen;
    $('typing').classList.remove('on');
  }
});

// ────────────────────────────────────────────────────────────────────
// Chat form
// ────────────────────────────────────────────────────────────────────

$('chat-form').addEventListener('submit', (e) => {
  e.preventDefault();
  sendMessage();
});

// Accende/spegne la musica di sottofondo e aggiorna il pulsante.
function musicToggle() {
  const on = window.Music ? Music.toggle() : false;
  const btn = $('btn-music');
  btn.textContent = on ? '🎵 On' : '🎵 Off';
  btn.setAttribute('aria-pressed', on ? 'true' : 'false');
  btn.classList.toggle('on', on);
  if (on && gameState) Music.applyState(gameState);
}

// Chiede al DM di COMPORRE una nuova colonna sonora per la scena attuale.
async function generateMusic() {
  const btn = $('btn-music-gen');
  if (!btn || btn.disabled) return;
  if (!dmOpen) { addMsg('system', '🎼 DM non collegato.'); return; }
  const orig = btn.textContent;
  btn.disabled = true;
  btn.textContent = '⏳';
  try {
    const r = await fetch('/api/music/generate', { method: 'POST' });
    const d = await r.json();
    if (d.error) { addMsg('system', '🎼 ' + d.error); return; }
    addMsg('system', '🎼 Il DM ha composto una nuova colonna sonora.');
    await refreshState();                         // applica la nuova musica
    if (window.Music && !Music.enabled) musicToggle();  // accendila se spenta
  } catch (e) {
    addMsg('system', '🎼 Errore: ' + e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = orig;
  }
}

// Chiede al DM di RIDISEGNARE la mappa completa della scena corrente.
// Server: rivela tutte le tile (no fog) e applica la nuova mappa.
async function redrawMap() {
  const btn = $('btn-map-redraw');
  if (!btn || btn.disabled) return;
  if (!dmOpen) { addMsg('system', '🗺 DM non collegato.'); return; }
  const orig = btn.textContent;
  btn.disabled = true;
  btn.textContent = '⏳';
  try {
    const r = await fetch('/api/map/redraw', { method: 'POST' });
    const d = await r.json();
    if (d.error) { addMsg('system', '🗺 ' + d.error); return; }
    addMsg('system', `🗺 Mappa ridisegnata (${d.width}×${d.height}).`);
    await refreshState();
  } catch (e) {
    addMsg('system', '🗺 Errore: ' + e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = orig;
  }
}

// ────────────────────────────────────────────────────────────────────
// Bootstrap
// ────────────────────────────────────────────────────────────────────

(async function init() {
  // TTS bootstrap — voce umana Edge (server)
  await ttsLoadVoices();
  $('btn-tts').textContent = TTS.enabled ? '🔊' : '🔇';
  $('btn-tts').setAttribute('aria-pressed', TTS.enabled ? 'true' : 'false');
  $('btn-tts').addEventListener('click', ttsToggle);
  $('btn-repeat').addEventListener('click', ttsRepeat);
  $('btn-reload').addEventListener('click', () => {
    if (busy) { addMsg('system', 'DM occupato — attendi la fine del turno.'); return; }
    if (!dmOpen) { addMsg('system', 'DM non collegato.'); return; }
    sendMessage(null, true);
  });
  $('tts-voice').addEventListener('change', (e) => {
    TTS.voice = e.target.value;
    localStorage.setItem('tts_voice', TTS.voice);
  });

  // Cursore velocità di lettura — agisce sia su Edge TTS (rate "+N%") sia
  // sulla voce del browser (fattore). 0% = normale, valori positivi = più
  // veloce. La modifica vale dal prossimo brano letto.
  const rateSlider = $('tts-rate');
  if (rateSlider) {
    const initPct = parseInt(String(TTS.rate).replace('%', ''), 10) || 0;
    const rateLabel = (pct) => `Velocità di lettura: ${pct === 0 ? 'normale' : (pct > 0 ? '+' : '') + pct + '%'}`;
    rateSlider.value = String(initPct);
    rateSlider.title = rateLabel(initPct);
    rateSlider.addEventListener('input', (e) => {
      const pct = parseInt(e.target.value, 10) || 0;
      TTS.rate = (pct >= 0 ? '+' : '') + pct + '%';
      localStorage.setItem('tts_rate', TTS.rate);
      rateSlider.title = rateLabel(pct);
    });
  }

  // Musica di sottofondo adattiva (richiede un gesto utente per partire)
  $('btn-music').addEventListener('click', musicToggle);
  $('btn-music-gen').addEventListener('click', generateMusic);
  const btnMapRedraw = $('btn-map-redraw');
  if (btnMapRedraw) btnMapRedraw.addEventListener('click', redrawMap);

  // Cursore volume musica — sincronizzato con Music.userVolume
  const volSlider = $('music-vol');
  if (volSlider && window.Music) {
    volSlider.value = Math.round(Music.userVolume * 100);
    volSlider.title = `Volume musica: ${volSlider.value}%`;
    volSlider.addEventListener('input', (e) => {
      const pct = parseInt(e.target.value, 10) || 0;
      Music.setVolume(pct / 100);
      e.target.title = `Volume musica: ${pct}%`;
    });
  }

  await loadDndData();
  await refreshState();
  await refreshAdventureBadge();
  // ripristina lo storico conversazione salvato (riprende la partita)
  await replayConversation();
  // poi controlla il DM: se il LED è verde, sincronizza i messaggi salvati
  await refreshWebchatStatus();
  setInterval(refreshWebchatStatus, 5000);
  // loop di animazione mappa: pulse mostri/tesori, respiro alone party.
  // Si autoregola: nessun ridisegno se non ci sono elementi animabili.
  startMapAnim();
})();
