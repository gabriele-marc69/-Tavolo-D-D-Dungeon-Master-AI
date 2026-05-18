// ⚔ Tavolo D&D 5.5e — frontend
// Comunica con Flask via /api/* + SSE per /api/chat

'use strict';

let gameState = null;
let dndData = { species: [], classes: [], backgrounds: [], alignments: [], genders: [] };
let busy = false;
let dmOpen = false;
let dmSynced = false;     // briefing iniziale (messaggi salvati) già passato al DM
let dmSyncNotified = false;
let lastDmMessage = '';   // ultimo messaggio del DM (usato da "Ripeti")
let lastRoll = null;      // ultimo tiro fatto dal giocatore (riquadro dadi)
let rollQueue = [];       // tiri accodati mentre il DM era occupato

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
function ttsSpeakBrowser(plain, done) {
  if (!('speechSynthesis' in window)) { if (done) done(); return; }
  try { window.speechSynthesis.cancel(); } catch (_) {}
  const u = new SpeechSynthesisUtterance(plain);
  u.lang = 'it-IT';
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

// Rilegge l'ultimo messaggio del DM (anche con TTS disattivato).
function ttsRepeat() {
  if (!lastDmMessage) {
    addMsg('system', 'Nessun messaggio del DM da rileggere.');
    return;
  }
  ttsSpeak(lastDmMessage, true);
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

function renderMarkdown(text) {
  // mini-markdown safe: bold, italic, headings, lists, code, line breaks
  let h = escapeHtml(text);
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
    $('chat-input').disabled = !dmOpen;
    $('chat-send').disabled = !dmOpen;

    // LED verde (DM aperto): SOLO ORA passa al modello i messaggi salvati,
    // così il DM riprende dal punto giusto. Una volta sola; se il briefing
    // fallisce (es. login necessario) si ritenta al giro dopo.
    if (d.briefed) {
      dmSynced = true;
    } else if (dmOpen && !dmSynced) {
      const s = await fetch('/api/webchat/sync', { method: 'POST' })
        .then(x => x.json()).catch(() => ({}));
      if (s.status === 'ready' || s.status === 'empty') {
        dmSynced = true;
      } else if (s.status === 'syncing' && !dmSyncNotified) {
        dmSyncNotified = true;
        addMsg('system', '⏳ DM collegato — allineo la partita salvata al '
          + 'modello. Attendi qualche secondo prima di scrivere.');
      }
    }
  } catch (e) { /* ignore */ }
}

function renderUI() {
  if (!gameState) return;
  $('phase-name').textContent = gameState.phase || 'setup';
  $('turn-num').textContent = gameState.turn || 0;
  renderPlayers();
  renderMap();
  renderScene();
  renderRolls();
  renderDiceBox();
  if (window.Music) Music.applyState(gameState);
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
        · CA ${s.ac || '?'} · XP ${s.xp || 0}
      </div>
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

// ── Mappa pixel-art: tabella 20×20 di quadratini, ogni quadratino è un
//    disegno 10×10. Palette FANTASY a 16 colori (indice esadecimale 0-f).
const MAP_GRID = 20;     // celle per lato (tabella 20×20)
const SPRITE_PX = 10;    // pixel per lato di ogni disegno (10×10)
const SCENE_PX = 64;     // lato dell'illustrazione di scena (64×64)
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
  'S': ['3333333333','3333553333','3333553333','3335577533','3357777533',
        '3357777533','3337777333','3337777333','3337337333','3337337333'],
  '+': ['1111111111','1666666661','1677777761','1677777761','16777d7761',
        '16777d7761','1677777761','1677777761','1666666661','1111111111'],
  '<': ['3344444444','3344444444','2233333333','2233333333','1122222222',
        '1122222222','0011111111','0011111111','0000000000','0000000000'],
  '>': ['0000000000','0000000000','0011111111','0011111111','1122222222',
        '1122222222','2233333333','2233333333','3344444444','3344444444'],
  '~': ['aaaaaaaaaa','abbaabbaab','aabbaabbaa','aaaaaaaaaa','baabbaabba',
        'bbaabbaabb','aaaaaaaaaa','abbaabbaab','aabbaabbaa','aaaaaaaaaa'],
  'T': ['3333333333','3f33f33f33','3f33f33f33','3f33f33f33','4444444444',
        '3333333333','3f33f33f33','3f33f33f33','3f33f33f33','4444444444'],
  't': ['3333333333','3338883333','3388998833','3899999883','8999999998',
        '8999999998','3899999883','3388998833','3333773333','3333773333'],
  ',': ['3333333333','3393333333','3893333933','3899339983','3839938393',
        '3333333333','3333393333','3333898333','3338999833','3333333333'],
  'o': ['3333333333','3333333333','3334444333','3345554433','3455554443',
        '3445544443','3444444433','3334444333','3333333333','3333333333'],
  'f': ['1111111111','1111dd1111','111dccd111','11dccccd11','11dccccd11',
        '1dcccccdd1','1dccddccd1','11dccccd11','1677777761','1166666611'],
  '=': ['aaaaaaaaaa','6666666666','7777777777','6777777776','6777777776',
        '7777777777','6666666666','aabbaabbaa','abbaabbaab','aaaaaaaaaa'],
  '$': ['3333333333','3366666633','3677777763','36ddddddd3','3677dd7763',
        '3677dd7763','3677777763','3677777763','3366666633','3333333333'],
  '@': ['3333333333','3333dd3333','3333dd3333','3335ff5333','3335ff5333',
        '3357777533','3377777733','3337777333','3337337333','3337337333'],
};

// Disegno 10×10 da usare per il carattere `ch`: prima le sprite del DM,
// poi quelle di default, infine il pavimento.
function spriteFor(ch) {
  const custom = (gameState && gameState.sprites) || {};
  return custom[ch] || DEFAULT_SPRITES[ch] || DEFAULT_SPRITES['.'];
}

// Disegna la mappa su un singolo <canvas> 200×200 (20 celle × 10 px),
// scalato dal CSS con resa a pixel netti. Una putImageData per render.
// Disegna la mappa dentro `root` (vi crea/aggiorna un <canvas> 200×200).
// Usata sia dal pannello mappa sia dalla finestra ingrandita.
function paintMap(root) {
  const m = gameState && gameState.map_ascii;
  if (!m) {
    root.classList.add('map-empty');
    root.innerHTML = '';
    root.textContent = '— nessuna mappa —';
    return false;
  }
  root.classList.remove('map-empty');
  const side = MAP_GRID * SPRITE_PX;            // 200×200 px interni
  let cv = root.querySelector('canvas.map-canvas');
  if (!cv) {
    root.textContent = '';
    cv = el('canvas', { class: 'map-canvas' });
    root.appendChild(cv);
  }
  if (cv.width !== side) { cv.width = side; cv.height = side; }
  const ctx = cv.getContext('2d');
  ctx.imageSmoothingEnabled = false;
  const img = ctx.createImageData(side, side);
  const data = img.data;
  const rows = m.split('\n');
  for (let gy = 0; gy < MAP_GRID; gy++) {
    for (let gx = 0; gx < MAP_GRID; gx++) {
      const ch = (rows[gy] || '')[gx] || ' ';
      const sp = spriteFor(ch);
      for (let py = 0; py < SPRITE_PX; py++) {
        const srow = sp[py] || '';
        for (let px = 0; px < SPRITE_PX; px++) {
          const col = PALETTE[parseInt(srow[px], 16) || 0] || PALETTE[0];
          const di = ((gy * SPRITE_PX + py) * side
                      + (gx * SPRITE_PX + px)) * 4;
          data[di] = col[0]; data[di + 1] = col[1];
          data[di + 2] = col[2]; data[di + 3] = 255;
        }
      }
    }
  }
  ctx.putImageData(img, 0, 0);
  return true;
}

function renderMap() {
  paintMap($('map-render'));
  $('zone-name').textContent = (gameState && gameState.current_zone) || '—';
  const pos = (gameState && gameState.current_position) || [0, 0];
  $('zone-pos').textContent = `[${pos[0]}, ${pos[1]}]`;
}

// Apre la mappa ingrandita a tutta finestra, con zona, posizione e
// legenda. Si chiama al click sul pannello mappa.
function openMapModal() {
  if (!gameState || !gameState.map_ascii) return;
  paintMap($('map-big-render'));
  $('map-big-zone').textContent = (gameState && gameState.current_zone) || '—';
  const pos = (gameState && gameState.current_position) || [0, 0];
  $('map-big-pos').textContent = `[${pos[0]}, ${pos[1]}]`;
  openModal('map');
}

// ── Disegno della scena: illustrazione pixel-art 64×64 della situazione
//    di gioco corrente, generata dal DM col tag <SCENE>. Il pannello
//    resta nascosto finché non arriva il primo disegno.
function renderScene() {
  const root = $('scene-render');
  if (!root) return;
  const panel = $('scene-panel');
  const scene = gameState && gameState.scene_art;
  const rows = scene && scene.rows;
  if (!rows || !rows.length) {
    if (panel) panel.classList.add('hidden');
    return;
  }
  if (panel) panel.classList.remove('hidden');
  const side = SCENE_PX;                       // 32×32 px interni
  let cv = root.querySelector('canvas.scene-canvas');
  if (!cv) {
    root.textContent = '';
    cv = el('canvas', { class: 'scene-canvas' });
    root.appendChild(cv);
  }
  if (cv.width !== side) { cv.width = side; cv.height = side; }
  const ctx = cv.getContext('2d');
  ctx.imageSmoothingEnabled = false;
  const img = ctx.createImageData(side, side);
  const data = img.data;
  for (let y = 0; y < side; y++) {
    const srow = rows[y] || '';
    for (let x = 0; x < side; x++) {
      const col = PALETTE[parseInt(srow[x], 16) || 0] || PALETTE[0];
      const di = (y * side + x) * 4;
      data[di] = col[0]; data[di + 1] = col[1];
      data[di + 2] = col[2]; data[di + 3] = 255;
    }
  }
  ctx.putImageData(img, 0, 0);
  const cap = $('scene-caption');
  if (cap) cap.textContent = (scene.caption || '').trim() || '—';
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
  const p = (gameState.players || []).find(x => x.name === ap);
  if (!p) return { name: ap, isHuman: false };
  return { name: p.name, isHuman: p.type === 'human' };
}

// Primo tiro in attesa richiesto dal DM (lo lancia il giocatore umano).
function pendingRoll() {
  const pend = (gameState && gameState.pending_rolls) || [];
  return pend.length ? pend[0] : null;
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

// Precompila il riquadro dadi dal ROLL_REQ richiesto dal DM.
function applyPendingRoll(req) {
  const m = /(\d+)\s*d\s*(\d+)\s*([+-]\s*\d+)?/i.exec(req.dice || req.expr || '');
  const qty   = m ? parseInt(m[1], 10) : 1;
  const sides = m ? parseInt(m[2], 10) : 20;
  const mod   = (m && m[3]) ? parseInt(m[3].replace(/\s/g, ''), 10) : 0;

  if ($('dice-qty')) $('dice-qty').value = qty;
  if ($('dice-mod')) $('dice-mod').value = mod;
  if ($('dice-adv')) $('dice-adv').checked = !!req.advantage;
  if ($('dice-dis')) $('dice-dis').checked = !!req.disadvantage;

  const dieBtn = document.querySelector(`.die[data-sides="${sides}"]`);
  if (dieBtn) dieBtn.classList.add('requested');

  const turnEl = $('dice-turn');
  const why = req.reason ? ` — ${req.reason}` : '';
  turnEl.textContent = `🎯 ${req.by || 'Giocatore'}, tira ${req.dice || (qty + 'd' + sides)}${why}`;
  turnEl.className = 'dice-turn active';
}

function renderDiceBox() {
  const box = $('dice-box');
  const turnEl = $('dice-turn');
  if (!box || !turnEl) return;

  // pulisci evidenziazione dado della richiesta precedente
  document.querySelectorAll('.die.requested').forEach(b => b.classList.remove('requested'));

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
  const cls = role === 'user' ? 'msg msg-user' : role === 'dm' ? 'msg msg-dm' : `msg msg-${role}`;
  const div = el('div', { class: cls });
  div.innerHTML = renderMarkdown(content);
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
  if (role === 'dm') {
    lastDmMessage = content;
    ttsSpeak(content);
  }
  return div;
}

// retry=true: ricarica/rigenera l'ultima risposta del DM (nessun nuovo
// messaggio del giocatore — il server riusa l'ultimo).
async function sendMessage(explicitText, retry) {
  if (busy) return;
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
  } else {
    if (typeof explicitText === 'string') {
      txt = explicitText.trim();      // tiro auto-inviato dal riquadro dadi
    } else {
      txt = input.value.trim();
      input.value = '';
    }
    if (!txt) return;
    addMsg('user', txt);
  }

  busy = true;
  $('chat-send').disabled = true;
  $('typing').classList.add('on');

  try {
    const resp = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(retry ? { retry: true } : { message: txt }),
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
            if (d.token) addMsg('dm', d.token);
            if (d.error) addMsg('error', '⚠ ' + d.error);
            if (d.state) { gameState = d.state; renderUI(); }
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
    // mappa SEMPRE allineata: a fine turno il frontend rilegge lo stato
    // dal server e ridisegna mappa + pannelli, anche se l'SSE ha perso
    // un evento di stato.
    try { await refreshState(); } catch (_) { /* ignore */ }
    // invia i tiri accodati mentre il DM era occupato: l'avventura
    // prosegue automaticamente dopo ogni lancio del giocatore
    if (rollQueue.length) sendMessage(rollQueue.shift());
  }
}

// ────────────────────────────────────────────────────────────────────
// Setup modal
// ────────────────────────────────────────────────────────────────────

$('btn-setup').addEventListener('click', () => openModal('setup'));

// Click sul pannello mappa → apre la mappa ingrandita a tutta finestra.
$('map-render').addEventListener('click', openMapModal);

// Preset modello DM: riempie URL + nome. 'custom' lascia i campi liberi.
const SETUP_PRESETS = {
  deepseek: { url: 'https://chat.deepseek.com/', name: 'DeepSeek Chat' },
  qwen:     { url: 'https://chat.qwen.ai/',      name: 'Qwen Chat' },
  grok:     { url: 'https://grok.com/',          name: 'Grok' },
  claude:   { url: 'https://claude.ai/',         name: 'Claude' },
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
});

$('chars-delete').addEventListener('click', async () => {
  if (!confirm('Eliminare TUTTI i personaggi? Operazione irreversibile.')) return;
  await fetch('/api/characters', { method: 'DELETE' });
  $('chars-form').innerHTML = '';
  $('chars-preview').innerHTML = '';
  addCharRow();
  await refreshState();
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
    return `<div class="sheet-stat"><div class="sheet-stat-name">${ab}</div><div class="sheet-stat-val">${v.score}</div><div class="sheet-stat-mod">${mod}</div></div>`;
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
    <div><strong>HP:</strong> ${s.hp ? s.hp.current + '/' + s.hp.max : '?'} · <strong>CA:</strong> ${s.ac || '?'} · <strong>Iniz:</strong> ${s.initiative >= 0 ? '+' + s.initiative : s.initiative} · <strong>Vel:</strong> ${s.speed || '?'}m</div>
    <div class="sheet-stats">${statsHtml}</div>
    <div><strong>TS:</strong> ${(s.saving_throws || []).join(', ')}</div>
    <div><strong>Competenze:</strong> ${(s.skills || []).join(', ')}</div>
    <div><strong>Lingue:</strong> ${(s.languages || []).join(', ')}</div>
    <div><strong>Equip:</strong> ${(s.equipment || []).join(', ')}</div>
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

function showSheet(s, name) {
  const root = $('sheet-content');
  root.innerHTML = '';
  root.appendChild(buildSheetEditor(s || {}, name || (s && s.name) || ''));
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
function collectSheetEdits(root) {
  const u = {};
  root.querySelectorAll('[data-f]').forEach(inp => {
    let val;
    if (inp.tagName === 'TEXTAREA') {
      val = inp.value.split('\n').map(x => x.trim()).filter(Boolean);
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

function buildSheetEditor(s, name) {
  const wrap = el('div', { class: 'sheet-editor' });
  const ABIL = ['FOR', 'DES', 'COS', 'INT', 'SAG', 'CAR'];
  const hp = s.hp || {};
  const sp = s.spells || {};
  const isCaster = s.caster_type && s.caster_type !== 'none';
  const gsym = s.gender === 'Femmina' ? '♀' : (s.gender === 'Maschio' ? '♂' : '');

  const numF = (f, val, min, max) =>
    `<input type="number" data-f="${f}" value="${val == null ? 0 : val}"`
    + `${min != null ? ` min="${min}"` : ''}${max != null ? ` max="${max}"` : ''}>`;

  const statsHtml = ABIL.map(ab => {
    const v = (s.stats && s.stats[ab]) || { score: 10, mod: 0 };
    const mod = v.mod >= 0 ? '+' + v.mod : v.mod;
    return `<div class="se-stat">
        <div class="se-stat-name">${ab}</div>
        <input type="number" class="se-stat-score" data-f="stats.${ab}.score"
               data-mod-for="${ab}" value="${v.score}" min="1" max="30">
        <div class="se-stat-mod" id="se-mod-${ab}">${mod}</div>
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
      <label>CA ${numF('ac', s.ac || 10, 0)}</label>
      <label>Velocità ${numF('speed', s.speed || 9, 0)}</label>
      <label>Iniziativa ${numF('initiative', s.initiative || 0)}</label>
    </div>
    <div class="se-row">
      <label>HP attuali ${numF('hp.current', hp.current || 0)}</label>
      <label>HP max ${numF('hp.max', hp.max || 0, 0)}</label>
      <label>HP temp ${numF('hp.temp', hp.temp || 0, 0)}</label>
    </div>
    <h4>Caratteristiche</h4>
    <div class="se-stats">${statsHtml}</div>
    <h4>Equipaggiamento</h4>
    <textarea data-f="equipment" rows="5" placeholder="Un oggetto per riga">${(s.equipment || []).map(escapeHtml).join('\n')}</textarea>
    ${isCaster ? `
      <h4>✦ Incantesimi</h4>
      <div class="se-spellmeta">Caratteristica <strong>${escapeHtml(sp.ability || '?')}</strong>
        · CD TS <strong>${sp.save_dc != null ? sp.save_dc : '?'}</strong>
        · Bonus attacco <strong>${ab != null ? (ab >= 0 ? '+' : '') + ab : '?'}</strong>
        <span class="se-hint">(ricalcolati al salvataggio)</span></div>

      <div class="se-label">Slot incantesimo — «Lancia» consuma uno slot</div>
      <div class="se-slots">${slotsHtml}</div>
      ${(sp.slots && Object.keys(sp.slots).length)
        ? `<button type="button" class="se-rest" data-char="${escapeHtml(cname)}">🌙 Riposo lungo — recupera tutti gli slot</button>`
        : ''}

      <div class="se-label">🔹 Trucchetti — a volontà, non consumano slot</div>
      ${spellListHtml(sp.cantrips)}
      <label class="se-edit-lbl">Modifica trucchetti <span class="se-hint">(una riga: Nome | descrizione)</span>
        <textarea data-f="spells.cantrips" rows="4" placeholder="Nome | descrizione">${escapeHtml(spellsToText(sp.cantrips))}</textarea>
      </label>

      <div class="se-label">🔸 Incantesimi — consumano uno slot del loro livello</div>
      ${spellListHtml(sp.known)}
      <label class="se-edit-lbl">Modifica incantesimi <span class="se-hint">(una riga: Nome | descrizione)</span>
        <textarea data-f="spells.known" rows="5" placeholder="Nome | descrizione">${escapeHtml(spellsToText(sp.known))}</textarea>
      </label>

      ${s.class === 'Mago' ? `
      <div class="se-label">📖 Libro degli incantesimi</div>
      ${spellListHtml(sp.spellbook)}
      <label class="se-edit-lbl">Modifica libro <span class="se-hint">(una riga: Nome | descrizione)</span>
        <textarea data-f="spells.spellbook" rows="5" placeholder="Nome | descrizione">${escapeHtml(spellsToText(sp.spellbook))}</textarea>
      </label>` : ''}
    ` : ''}
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

  wrap.querySelector('#se-save').addEventListener('click',
    () => saveSheet(wrap, s.name || name));
  return wrap;
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

// Riposo lungo: azzera gli slot usati del personaggio.
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
         <span class="debug-method">${escapeHtml(e.method)}</span>
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
           <span class="debug-method">DM</span>
           <span class="debug-ts">${escapeHtml(x.ts || '')}</span>
         </div>`,
        `<pre class="debug-body">👤 ${escapeHtml(x.user || '')}</pre>`,
        `<pre class="debug-body">📥 RAW: ${escapeHtml(x.raw || '')}</pre>`,
        `<pre class="debug-body">📺 MOSTRATO: ${escapeHtml(x.shown || '')}</pre>`,
        (x.rolls && x.rolls.length)
          ? `<pre class="debug-body">🎲 ${escapeHtml(JSON.stringify(x.rolls))}</pre>` : '',
        mapCoherenceHtml(x.map),
        (x.sprites && x.sprites.length)
          ? `<pre class="debug-body ok">🎨 SPRITE pixel-art: ${escapeHtml(x.sprites.join(' '))}</pre>`
          : '',
        x.scene
          ? `<pre class="debug-body ok">🖼 SCENA 32×32 generata</pre>`
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
    const n = await replayConversation();
    renderUI();
    addMsg('system', d.resync
      ? `📂 Partita ripristinata: ${n} messaggi caricati. Sto riallineando il DM al contesto — attendi qualche secondo prima di scrivere.`
      : `📂 Partita ripristinata: ${n} messaggi caricati. Puoi riprendere.`);
  } catch (e) {
    addMsg('error', '⚠ Errore nel caricamento: ' + e.message);
  }
});

$('btn-new').addEventListener('click', async () => {
  const hasChars = !!(gameState && (gameState.players || []).length);
  const ask = hasChars
    ? 'Iniziare una NUOVA avventura? Lo storico verrà cancellato. I personaggi già creati restano in gioco.'
    : 'Iniziare una NUOVA partita? Lo storico verrà cancellato.';
  if (!confirm(ask)) return;
  const r = await fetch('/api/new_game', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ keep_characters: true }),
  });
  const d = await r.json();
  gameState = d.state;
  $('chat-log').innerHTML = '';
  const kept = (gameState.players || []).length;
  addMsg('system', kept
    ? `Nuova avventura iniziata con ${kept} personagg${kept === 1 ? 'io' : 'i'} già pronti.`
    : 'Nuova partita iniziata. Apri Personaggi per crearne.');
  renderUI();
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

  // Musica di sottofondo adattiva (richiede un gesto utente per partire)
  $('btn-music').addEventListener('click', musicToggle);
  $('btn-music-gen').addEventListener('click', generateMusic);

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
  // ripristina lo storico conversazione salvato (riprende la partita)
  await replayConversation();
  // poi controlla il DM: se il LED è verde, sincronizza i messaggi salvati
  await refreshWebchatStatus();
  setInterval(refreshWebchatStatus, 5000);
})();
