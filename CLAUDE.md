# CLAUDE.md — Tavolo D&D: Dungeon Master AI

Applicazione web Flask che simula un Dungeon Master per D&D 5e. Il DM è un
modello remoto (DeepSeek) pilotato via **browser automation** (Playwright +
Chromium), non un'API a chiavi né un modello locale.

---

## Avvio

```bash
pip install -r requirements.txt
playwright install chromium      # una tantum: scarica il browser
python app.py                    # apre http://localhost:5000 + Chromium
```

Dipendenze (`requirements.txt`): `flask>=3.0`, `requests>=2.31`,
`playwright>=1.45`, `edge-tts>=7.0` (voce, opzionale).

Variabili d'ambiente:
- `PORT` — porta del server (default `5000`).
- `TAVOLO_NO_AUTOSTART=1` — NON aprire Chromium all'avvio (lo si apre poi col
  pulsante Setup, o via `POST /api/webchat/open`). Utile per test/headless.

---

## Architettura

```
app.py                # routing Flask + collante (nessuna logica di dominio)
dnd/                  # MOTORE di gioco
  character.py        # generate_sheet, upgrade_sheet, recompute_derived,
                      #   apply_level_progression, liste SRD (specie/classi/…)
  rules.py            # roll()/RollResult, modificatori, CA, TS morte, XP/livelli
  spells.py           # SPELLS, CLASS_SPELL_LIST, CLASS_CANTRIPS, CASTER_KIND,
                      #   class_spells(), caster_kind()
  state.py            # modello game_state, persistenza, fog-of-war, fasi
  bestiary.py         # carica bestiary.json: meta(), filter_by_cr()
dm/                   # interfaccia col Dungeon Master
  prompt.py           # system prompt, briefing, reminder, richieste avventura/mappa
  webchat.py          # Webchat + WebchatConfig — Playwright/Chromium → DeepSeek
  parser.py           # estrazione/applicazione dei tag nelle risposte del DM
templates/index.html  # UI unica (chat + mappa + schede)
static/               # app.css, app.js, music.js
bestiary.json         # bestiario mostri (SRD 5.1)
bestiary_add.py       # script CLI per aggiungere mostri al bestiario
```

File di stato generati a runtime (cartella `runtime/`):
- `runtime/game_state.json`        — stato partita (vedi sotto)
- `runtime/conversation.json`      — storico messaggi (ultimi 40)
- `runtime/personaggi.json`        — schede PG consolidate del party corrente
- `runtime/personaggi/<nome>.json` — una scheda per file, per comporre il party
- `avventura.txt` (radice repo)    — avventura pre-scritta opzionale

> All'avvio `app._normalize_existing_sheets()` normalizza le schede in
> `game_state` + `personaggi.json` (migrazione bonus oggetti → valori di
> gioco). La scrittura su disco avviene **solo se qualcosa è cambiato**.

---

## Backend LLM — DeepSeek via Playwright

Non ci sono chiavi API né modelli locali. `dm/webchat.py` apre Chromium su
`https://chat.deepseek.com/` (`WebchatConfig`: `url`, `name="DeepSeek Chat"`,
`timeout=180`s) e invia/raccoglie i messaggi pilotando la pagina. Il login è
manuale dell'utente nella finestra del browser.

Flusso briefing iniziale: l'auto-start apre la chat ma **non** invia nulla
finché il frontend non rileva il "LED verde" (chat pronta) e chiama
`POST /api/webchat/sync`. Un gate di login (`webchat.login_ready()`) impedisce
invii prima del login. Prompt lunghi vengono spezzati in chunk.

Voce: `edge-tts` (Microsoft Edge TTS) sintetizza l'audio del DM se installato.
Musica e sprite pixel-art sono generati dal DM stesso tramite tag (vedi sotto).

---

## API REST (Flask)

| Metodo | Endpoint | Descrizione |
|--------|----------|-------------|
| GET | `/` | UI principale |
| GET | `/api/state` | Stato partita corrente |
| GET | `/api/dnd_data` | Liste SRD (specie, classi, background, allineamenti, generi) |
| GET | `/api/bestiary` | Bestiario (filtri `cr_min`/`cr_max`) |
| GET | `/api/characters` | Schede del party corrente |
| POST | `/api/characters` | Genera + salva schede (max 5) |
| DELETE | `/api/characters` | Elimina party + schede per-file |
| GET | `/api/characters/available` | Elenco schede salvate (`runtime/personaggi/`) |
| GET | `/api/characters/<name>` | Singola scheda salvata |
| DELETE | `/api/characters/<name>` | Elimina singola scheda |
| POST | `/api/party/load` | Compone il party da schede salvate |
| POST | `/api/generate_sheet` | Anteprima scheda senza salvare |
| POST | `/api/character_update` | Modifica manuale scheda (merge profondo) |
| GET | `/api/spells_catalog` | Catalogo incantesimi (filtro `class=`) |
| POST | `/api/prepare_spell` | Prepara/dimentica incantesimo |
| POST | `/api/cast_spell` | Lancia incantesimo (consuma slot) |
| POST | `/api/roll` | Tiro dadi (es: `"dice":"2d6+3"`, `advantage`) |
| POST | `/api/new_game` | Nuova partita (mantiene i PG per default) |
| POST | `/api/generate_adventure` | Chiede al DM di generare l'avventura |
| POST | `/api/load_adventure` | Carica `avventura.txt` |
| GET / DELETE | `/api/adventure` | Legge / elimina l'avventura caricata |
| POST | `/api/save` / `/api/load` | Salva / carica stato + conversazione |
| GET | `/api/webchat/status` | Stato webchat (aperta, briefed, in_flight…) |
| POST | `/api/webchat/sync` | Allinea il DM allo stato (briefing) |
| POST | `/api/webchat/open` | Apre/riapre Chromium sul DM |
| POST | `/api/map/redraw` | Chiede al DM di ridisegnare la mappa |
| POST | `/api/music/generate` | Chiede al DM una nuova colonna sonora |
| POST | `/api/chat` | **Invio messaggio → SSE streaming** |
| GET | `/api/tts/voices` | Voci edge-tts disponibili |
| POST | `/api/tts` | Sintetizza audio (edge-tts) |
| GET | `/api/debug` | Ultimi 20 scambi col DM |
| GET | `/api/conversation` | Storico conversazione |

### `/api/chat` — Formato SSE
- `": keepalive"` — heartbeat mentre il DM genera
- `data: {"token":"..."}` — token di testo
- `data: {"done":true, "state":{...}}` — fine risposta + stato aggiornato
- `data: {"error":"..."}` — errore

---

## Stato di gioco (`game_state`)

Definito da `state.empty_state()`. Campi principali:

```json
{
  "phase": "setup|registration|character_creation|adventure_generation|adventure|combat|ended",
  "players": [{ "name": "...", "type": "human|ai", "sheet": { /* scheda D&D */ } }],
  "active_player": null,
  "turn": 0, "round": 0,
  "initiative_order": [{ "name": "...", "init": 0 }],
  "map_base": null, "map_full": null, "map_ascii": null,
  "map_width": 0, "map_height": 0, "revealed_tiles": [],
  "current_position": [x, y], "current_zone": null, "current_scene": null,
  "zones": [], "combat_active": false, "encounter": null,
  "adventure_loaded": false, "adventure_title": null,
  "adventure_beats": [], "adventure_index": 0,
  "characters_loaded": false, "session_start": "ISO8601",
  "rolls_log": [], "pending_rolls": [], "pending_roll_feedback": [],
  "pending_dm_notes": [], "music": {}, "sprites": {}
}
```

`map_full` = mappa completa nota al DM; `map_ascii` = vista col fog-of-war
applicato (`revealed_tiles`). Un cambio di `current_scene` forza il ridisegno
della mappa.

---

## Parsing risposte del DM (`dm/parser.py`)

`clean_text()` deduplica testo ripetuto e ripulisce; `strip_narrative()` toglie
i tag/blocchi strutturati lasciando solo la narrazione mostrata in chat.

Tag estratti e applicati al `game_state`:
- **`MAP_START … MAP_END`** → mappa ASCII (`extract_map` → `map_base/full/ascii`)
- **`<STATE_UPDATE>{…}</STATE_UPDATE>`** → fase, posizione, scena, combat…
- **`<CHAR_UPDATE>{…}</CHAR_UPDATE>`** → scheda PG (persiste su `personaggi.json`)
- **`<MUSIC>{…}</MUSIC>`** → colonna sonora per mood
- **`<SPRITE>{…}</SPRITE>`** → sprite pixel-art (griglia)
- **`<SPELL_CAST>{…}</SPELL_CAST>`** → incantesimi lanciati (consumo slot)
- **`ROLL_REQ`** → richieste di tiro (`resolve_roll_requests`): i tiri umani
  vanno al riquadro dadi, quelli IA li risolve il sistema.

---

## Schede Personaggio (`dnd/character.py`)

`generate_sheet(name, species, cls, level, background, alignment, player_type,
gender)` genera una scheda D&D 5e completa:
- Array standard `[15,14,13,12,10,8]` assegnato ottimalmente per classe + bonus specie
- HP = dado classe + mod COS
- CA, Iniziativa, Velocità, TS, Competenze, Linguaggi, Equipaggiamento, Tratti, Capacità, Incantesimi

`upgrade_sheet` / `recompute_derived` ricalcolano i derivati (mod, CD, bonus da
oggetti magici parsati dal linguaggio naturale); `apply_level_progression`
gestisce il level-up da XP.

---

## Convenzioni

- Codice e commenti in italiano (nomi variabili in inglese).
- `webchat` (Playwright) gira in thread separati: l'invio del briefing e dei
  messaggi al DM non blocca Flask.
- `conversation_history` mantiene gli ultimi 40 messaggi.
- Persistenza idempotente: `sync_characters_from_players` scrive solo a
  contenuto cambiato (importare i moduli non tocca il disco se nulla cambia).
- Nessuna chiave API per l'LLM: l'autenticazione al DM è la sessione browser.
