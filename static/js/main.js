// static/js/main.js
const API = {
  chat: '/api/chat',
  status: '/api/model_status',
  loadModel: '/api/load_model',
  roll: '/api/roll',
  state: '/api/state',
  adventure: '/api/load_adventure',
  debug: '/api/debug'
};

let isStreaming = false;
let abortController = null;

// ─── UI Helpers ───────────────────────────────────────────────
const $ = id => document.getElementById(id);
const appendChat = (text, role='dm') => {
  const log = $('chat-log');
  const div = document.createElement('div');
  div.className = `msg msg-${role}`;
  div.innerHTML = role === 'user' ? `<strong>Tu:</strong> ${text}` : text.replace(/\n/g, '<br>');
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
};
const updateMap = (ascii) => {
  if (!ascii) return;
  $('map-display').textContent = ascii.replace(/\\n/g, '\n');
};
const updatePlayers = (players) => {
  const panel = $('players-panel');
  if (!players?.length) return panel.innerHTML = '<em>Nessun personaggio registrato.</em>';
  panel.innerHTML = players.map(p => `
    <div class="player-card ${p.sheet?.hp?.current <= 0 ? 'dead' : ''}">
      <strong>${p.name}</strong> (${p.type})<br>
      HP: ${p.sheet?.hp?.current ?? '?'} / ${p.sheet?.hp?.max ?? '?'} |
      Classe: ${p.sheet?.class ?? 'N/A'} | Fase: ${p.sheet?.phase ?? 'N/A'}
    </div>
  `).join('');
};

// ─── Modello: Caricamento & Polling ───────────────────────────
async function pollModelStatus() {
  try {
    const res = await fetch(API.status);
    const s = await res.json();
    const statusEl = $('model-status');
    if (s.loaded) {
      statusEl.textContent = `✅ ${s.name} (Backend: ${s.backend})`;
      statusEl.dataset.loaded = 'true';
    } else if (s.loading) {
      statusEl.textContent = `⏳ Caricamento in corso...`;
    } else if (s.error) {
      statusEl.textContent = `❌ Errore: ${s.error}`;
    }
    return s.loaded;
  } catch { return false; }
}

async function loadModel(index = 0) {
  await fetch(API.loadModel, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({choice: 'local', index})
  });
  $('model-status').textContent = '⏳ Caricamento avviato...';
  const check = setInterval(async () => {
    if (await pollModelStatus()) clearInterval(check);
  }, 2000);
}

// ─── Chat Streaming (SSE POST) ────────────────────────────────
async function sendMessage(text) {
  if (!text.trim() || isStreaming) return;
  const loaded = $('model-status')?.dataset?.loaded === 'true';
  if (!loaded) return alert('Modello non pronto. Attendi il caricamento.');

  appendChat(text, 'user');
  $('chat-input').value = '';
  isStreaming = true;
  $('send-btn').disabled = true;

  abortController = new AbortController();
  let buffer = '';

  try {
    const res = await fetch(API.chat, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({message: text}),
      signal: abortController.signal
    });

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let currentMsg = '';

    while (true) {
      const {value, done} = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, {stream: true});

      // Parsa righe SSE
      const lines = buffer.split('\n');
      buffer = lines.pop(); // tieni l'ultimo frammento
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        try {
          const data = JSON.parse(line.slice(6));
          if (data.error) throw new Error(data.error);
          if (data.token) {
            currentMsg += data.token;
            $('chat-log').lastChild.innerHTML = currentMsg.replace(/\n/g, '<br>');
            $('chat-log').scrollTop = $('chat-log').scrollHeight;
          }
          if (data.done) {
            if (data.state) {
              updateMap(data.state.map_ascii);
              updatePlayers(data.state.players);
              localStorage.setItem('last_state', JSON.stringify(data.state));
            }
            break; // stop reading further tokens
          }
        } catch (e) {
          console.warn('SSE Parse Error:', e, line);
        }
      }
    }
  } catch (err) {
    appendChat(`❌ Errore: ${err.message}`, 'system');
  } finally {
    isStreaming = false;
    $('send-btn').disabled = false;
  }
}

function stopChat() {
  if (abortController) { abortController.abort(); abortController = null; }
}

// ─── Dadi & Avventura ─────────────────────────────────────────
async function rollDice(diceStr) {
  const res = await fetch(API.roll, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({dice: diceStr})
  });
  const d = await res.json();
  const crit = d.is_critical ? '🔥 CRITICO!' : d.is_fumble ? '💀 FUMBLE!' : '';
  appendChat(`🎲 ${diceStr} → [${d.rolls.join(', ')}]${d.modifier ? ` + ${d.modifier}` : ''} = <strong>${d.total}</strong> ${crit}`, 'dm');
}

async function loadAdventure() {
  if (!confirm('Caricare "avventura.txt"? La sessione corrente verrà resettata.')) return;
  await fetch(API.adventure, {method: 'POST'});
  $('chat-log').innerHTML = '';
  appendChat('📜 Avventura caricata. Digita START per iniziare.', 'system');
}

// ─── Debug Overlay ────────────────────────────────────────────
async function loadDebug() {
  const res = await fetch(API.debug);
  const dbg = await res.json();
  const el = $('debug-log');
  el.innerHTML = `<pre>${JSON.stringify(dbg, null, 2)}</pre>`;
}

// ─── Init ─────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  $('send-btn').addEventListener('click', () => sendMessage($('chat-input').value));
  $('chat-input').addEventListener('keydown', e => e.key === 'Enter' && sendMessage(e.target.value));
  document.querySelector('.stop-btn')?.addEventListener('click', stopChat);
  document.querySelectorAll('.dice-btn').forEach(btn =>
    btn.addEventListener('click', () => rollDice(btn.dataset.dice))
  );
  document.querySelector('#load-adv-btn')?.addEventListener('click', loadAdventure);
  document.querySelector('#debug-toggle')?.addEventListener('click', loadDebug);

  pollModelStatus();
  // Carica stato iniziale se presente
  const saved = localStorage.getItem('last_state');
  if (saved) {
    const s = JSON.parse(saved);
    updateMap(s.map_ascii);
    updatePlayers(s.players);
  }
});