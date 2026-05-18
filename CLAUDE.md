# CLAUDE.md — Tavolo D&D: Dungeon Master AI

Applicazione web Flask che simula un Dungeon Master per D&D 5e usando modelli LLM locali o API remote.

---

## Avvio

```bash
pip install -r requirements.txt
python app.py        # apre http://localhost:5000 automaticamente
```

Dipendenze: `flask>=3.0`, `requests>=2.31`.
Per modelli locali servono anche `torch`, `transformers`.

---

## Architettura

```
app.py              # server Flask, logica di gioco, routing API
dnd_data.py         # generatore schede D&D (generate_sheet, RACES, CLASSES, BACKGROUNDS, xp_for_level)
bestiary.json       # bestiario mostri
bestiary_add.py     # script CLI per aggiungere mostri al bestiario
templates/
  index.html        # UI principale (chat + mappa)
  narrate.html      # pagina narrativa
  bestiary.html     # visualizzatore bestiario
static/             # asset statici
```

File di stato generati a runtime:
- `game_state.json`    — stato partita (fase, giocatori, mappa, HP)
- `conversation.json`  — storico messaggi
- `personaggi.json`    — schede personaggio persistenti
- `avventura.txt`      — avventura pre-scritta opzionale
- `.env`               — chiavi API (auto-creato da `/api/hf_login` o `/api/use_preset`)

---

## Backend LLM

`BACKEND` è una variabile globale: `"local"` o `"api"`.

### Locale (HuggingFace Transformers)
Carica automaticamente il primo modello disponibile in `~/.cache/huggingface/hub` tra:
1. `Jackrong/Qwen3.5-4B-Claude-4.6-Opus-Reasoning-Distilled-v2`
2. `Qwen/Qwen3.5-4B`
3. `Qwen/Qwen3.5-2B`
4. `LiquidAI/LFM2.5-1.2B-Instruct`
5. `Qwen/Qwen3.5-0.8B`

Device auto-rilevato: Intel Arc XPU → CUDA → CPU.

### API remote (compatibile OpenAI Chat Completions)
Preset configurati in `API_PRESETS` (app.py:38–111):
- **DeepSeek** (`DEEPSEEK_API_KEY`): `deepseek-chat`, `deepseek-reasoner`
- **Together AI** (`TOGETHER_API_KEY`): `together-deepseek-v3`
- **OpenRouter** (`OPENROUTER_API_KEY`): `openrouter-deepseek`, `openrouter-deepseek-r1`, `openrouter-qwen-plus`, `openrouter-qwen-72b`
- **DashScope/Qwen** (`DASHSCOPE_API_KEY`): `qwen-plus`, `qwen-turbo`, `qwen-max`, `qwen2.5-72b`

### HuggingFace Inference Router
Preset HF (`HF_PRESETS`):
- `glm-5.1` → Together AI (`zai-org/GLM-5.1`)
- `deepseek-v4-pro` → Novita (`deepseek/deepseek-v4-pro`)

Chiavi lette da variabili d'ambiente o file `.env` locale.

---

## API REST (Flask)

| Metodo | Endpoint | Descrizione |
|--------|----------|-------------|
| GET | `/` | UI principale |
| GET | `/narrate` | Pagina narrativa |
| GET | `/bestiary` | Bestiario |
| GET | `/api/model_status` | Stato caricamento modello |
| POST | `/api/load_model` | Carica modello (local/api/preset/hf) |
| GET | `/api/candidates` | Lista modelli locali in cache |
| GET | `/api/presets` | Lista preset API con stato chiave |
| POST | `/api/use_preset` | Attiva preset API esterno |
| GET | `/api/hf_status` | Stato token HuggingFace |
| POST | `/api/hf_login` | Imposta token HF + configura backend |
| GET | `/api/backend` | Configurazione backend corrente |
| POST | `/api/set_backend` | Cambia backend (local/api) |
| POST | `/api/chat` | **Invio messaggio → SSE streaming** |
| GET | `/api/state` | Stato partita corrente |
| POST | `/api/new_game` | Reset partita |
| POST | `/api/save` | Salva stato + conversazione |
| POST | `/api/load` | Carica stato + conversazione |
| POST | `/api/load_adventure` | Carica `avventura.txt` |
| GET | `/api/characters` | Leggi personaggi salvati |
| POST | `/api/characters` | Salva personaggi (genera schede complete) |
| DELETE | `/api/characters` | Elimina `personaggi.json` |
| POST | `/api/generate_sheet` | Anteprima scheda senza salvare |
| GET | `/api/dnd_data` | Liste SRD (razze, classi, background) |
| POST | `/api/roll` | Tiro dadi (es: `"dice": "2d6+3"`) |
| GET | `/api/debug` | Ultimi 20 scambi con il modello |
| GET | `/api/conversation` | Storico conversazione |
| GET | `/api/bestiary` | Dati bestiario JSON |

### `/api/chat` — Formato SSE
Il client riceve Server-Sent Events:
- `": keepalive"` — heartbeat ogni 3s mentre il modello genera
- `data: {"token": "..."}` — token di testo
- `data: {"done": true, "state": {...}}` — fine risposta + stato aggiornato
- `data: {"error": "..."}` — errore

---

## Stato di gioco (`game_state`)

Campi principali in `game_state.json`:

```json
{
  "phase": "setup|registration|character_creation|adventure_generation|adventure|combat",
  "players": [
    {
      "name": "NomeGiocatore",
      "type": "human|ai",
      "sheet": { /* scheda completa D&D */ }
    }
  ],
  "seed": "YYYYMMDD-HHMMSS-RAND4",
  "map_ascii": "##########\n#*...@...#\n...",
  "current_position": [x, y],
  "combat_active": false,
  "turn": 0,
  "adventure_loaded": false,
  "characters_loaded": false,
  "session_start": "ISO8601"
}
```

---

## Parsing risposte LLM

`_clean_response()` (app.py:996) rimuove:
- Blocchi `<think>…</think>` (DeepSeek-R1, Qwen3)
- Paragrafi di calcolo interni `*(…)*`
- Righe debug come `*Formula Base:…*`

Il sistema estrae automaticamente dalla risposta del DM:
- **`MAP_START…MAP_END`** → `game_state["map_ascii"]`
- **`<STATE_UPDATE>{…}</STATE_UPDATE>`** → aggiorna fase, posizione, HP
- **`<CHAR_UPDATE>{…}</CHAR_UPDATE>`** → aggiorna scheda personaggio + persiste su `personaggi.json`

---

## Schede Personaggio (`dnd_data.py`)

`generate_sheet(name, race, cls, level, background, alignment, ...)` genera scheda completa D&D 5e con:
- Array standard `[15,14,13,12,10,8]` assegnato ottimalmente per classe + bonus razza
- HP = dado classe + mod COS
- CA, Iniziativa, Velocità, TS, Competenze, Linguaggi, Equipaggiamento, Tratti, Capacità, Incantesimi

---

## Convenzioni

- Tutto il codice e i commenti sono in italiano (nomi variabili in inglese)
- Il modello locale gira in thread separato con coda `queue.Queue` per non bloccare Flask
- `conversation_history` mantiene gli ultimi 20 messaggi nel contesto
- Le chiavi API vengono lette da env vars → file `.env` → body richiesta (ordine di priorità)
- `_save_key_to_env()` aggiorna il `.env` senza sovrascrivere le altre chiavi
