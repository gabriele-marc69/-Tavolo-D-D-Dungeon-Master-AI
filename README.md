[README.md](https://github.com/user-attachments/files/27964340/README.md)
# 🎲 Tavolo D_IA_D — Dungeon Master AI

Applicazione web Flask che simula un **Dungeon Master AI**.
Il DM è pilotato da un'IA conversazionale: l'app guida una chat IA pubblica
(default [DeepSeek](https://chat.deepseek.com/)) tramite browser automatizzato,
con generazione di schede personaggio, mappa ASCII, bestiario, tiri di dado,
colonna sonora dinamica e voce narrante.

---

## ✨ Funzionalità

- **DM via webchat** — backend Playwright che pilota Chromium su una pagina chat IA pubblica
- **Schede personaggio** — generazione completa: caratteristiche, HP, CA, abilità, incantesimi, equipaggiamento
- **Mappa ASCII** — generata ed aggiornata dal DM durante l'avventura
- **Combattimento a turni** — tiri di dado, lancio incantesimi, aggiornamento HP
- **Bestiario** — `bestiary.json` con mostri SRD, ampliabile via `bestiary_add.py`
- **Colonna sonora dinamica** — il DM compone musica coerente col contesto
- **Voce narrante** — TTS via Microsoft Edge (`edge-tts`)
- **Persistenza** — stato partita, conversazione e personaggi salvati su disco

---

## 🚀 Avvio rapido

```bash
# 1. Installa le dipendenze
pip install -r requirements.txt

# 2. Installa il browser per Playwright
playwright install chromium

# 3. Avvia il server
python app.py
```

Il server apre automaticamente `http://localhost:5000`.

Su Windows è disponibile `installa.bat` (setup) e `avvia.bat` (avvio rapido).

### Variabili d'ambiente

| Variabile | Effetto |
|-----------|---------|
| `PORT` | Porta del server (default `5000`) |
| `TAVOLO_NO_AUTOSTART=1` | Non aprire il browser all'avvio |

---

## 📦 Dipendenze

- `flask>=3.0` — server web
- `requests>=2.31` — chiamate HTTP
- `playwright>=1.45` — automazione browser per il DM webchat
- `edge-tts>=7.0` — voce narrante (opzionale)

---

## 🗂️ Struttura

```
app.py            # server Flask: routing API + collante
bestiary.json     # bestiario mostri (SRD)
bestiary_add.py   # CLI per aggiungere mostri al bestiario
requirements.txt
dnd/              # regole di gioco
  rules.py        #   tiri dado, XP, bonus competenza, CA
  character.py    #   generazione schede personaggio
  bestiary.py     #   accesso al bestiario
  spells.py       #   incantesimi
  state.py        #   stato di gioco + persistenza su runtime/
dm/               # logica Dungeon Master
  prompt.py       #   costruzione prompt DM
  webchat.py      #   backend Playwright (pilota la chat IA)
  parser.py       #   estrazione tag <STATE_UPDATE>, <MUSIC>, mappa…
templates/
  index.html      # UI principale (chat + mappa)
static/           # CSS, JS, audio
```

File generati a runtime (non versionati):

```
runtime/game_state.json     # stato partita
runtime/conversation.json   # storico messaggi
runtime/personaggi.json     # schede personaggio persistenti
avventura.txt               # avventura pre-scritta opzionale
.browser_profiles/          # profili Chromium di Playwright
```

---

## 🔌 API REST principali

| Metodo | Endpoint | Descrizione |
|--------|----------|-------------|
| GET | `/` | UI principale |
| GET | `/api/state` | Stato partita corrente |
| GET | `/api/dnd_data` | Liste SRD (specie, classi, background) |
| GET | `/api/bestiary` | Dati bestiario |
| GET/POST/DELETE | `/api/characters` | Gestione schede personaggio |
| POST | `/api/generate_sheet` | Anteprima scheda senza salvare |
| POST | `/api/character_update` | Aggiorna scheda personaggio |
| POST | `/api/cast_spell` | Lancio incantesimo |
| POST | `/api/roll` | Tiro dadi (es. `{"dice": "2d6+3"}`) |
| POST | `/api/new_game` | Reset partita |
| POST | `/api/save` · `/api/load` | Salva / carica partita |
| GET | `/api/webchat/status` | Stato connessione DM |
| POST | `/api/webchat/open` | Apri la chat del DM |
| POST | `/api/webchat/sync` | Allinea il DM allo storico partita |
| POST | `/api/chat` | Invio messaggio → **SSE streaming** |
| POST | `/api/music/generate` | Genera colonna sonora |
| GET | `/api/tts/voices` | Voci TTS disponibili |
| POST | `/api/tts` | Sintesi vocale del testo |
| GET | `/api/debug` | Ultimi scambi col modello |
| GET | `/api/conversation` | Storico conversazione |

### `/api/chat` — formato SSE

```
: keepalive                       heartbeat ogni 3s durante la generazione
data: {"token": "..."}            token di testo
data: {"done": true, "state": {}} fine risposta + stato aggiornato
data: {"error": "..."}            errore
```

---

## 🎮 Come si gioca

1. Avvia il server e apri `http://localhost:5000`.
2. Premi **Apri DM** — si apre Chromium sulla chat IA. Esegui il login se richiesto.
3. Quando il LED del DM diventa verde, crea i personaggi.
4. Scrivi nella chat: il DM narra, genera la mappa, gestisce combattimenti e tiri.

---

## 📄 Licenza

Distribuito sotto licenza **MIT** — vedi [`LICENSE`](LICENSE).
