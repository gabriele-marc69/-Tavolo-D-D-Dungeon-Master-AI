"""
Modello dello stato di gioco. Persiste su disco in runtime/game_state.json,
conversation.json, personaggi.json.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from typing import Any


BASE_DIR = os.path.dirname(os.path.dirname(__file__))
RUNTIME_DIR = os.path.join(BASE_DIR, "runtime")
# Directory dedicata alle schede personaggio: un file "nome.json" per ogni
# PG creato, così è possibile selezionarli singolarmente per comporre il party.
CHARACTERS_DIR = os.path.join(RUNTIME_DIR, "personaggi")
# Archivio delle avventure GENERATE dal DM: ogni generazione viene salvata
# con un NOME NUOVO (timestamp + slug del titolo), così le partite passate
# restano consultabili e una "Nuova avventura" non sovrascrive la vecchia.
ADVENTURES_DIR = os.path.join(RUNTIME_DIR, "avventure")

GAME_STATE_FILE   = os.path.join(RUNTIME_DIR, "game_state.json")
CONVERSATION_FILE = os.path.join(RUNTIME_DIR, "conversation.json")
CHARACTERS_FILE   = os.path.join(RUNTIME_DIR, "personaggi.json")
# avventura.txt (radice): avventura precaricata caricata MANUALMENTE
# dall'utente. Le avventure generate dal DM vanno invece in ADVENTURES_DIR
# con nome univoco; il file attivo è puntato da game_state["adventure_file"].
ADVENTURE_FILE    = os.path.join(BASE_DIR, "avventura.txt")
# Ultimo modello DM scelto in Setup (url/name/timeout): persiste tra i
# riavvii così l'app riapre l'ultimo modello configurato invece del default.
WEBCHAT_CONFIG_FILE = os.path.join(RUNTIME_DIR, "webchat_config.json")


PHASES = (
    "setup",                  # backend pronto, DeepSeek non ancora attivato
    "registration",           # raccolta nome + tipo (umano/AI) per ogni giocatore
    "character_creation",     # creazione schede PG una alla volta
    "adventure_generation",   # DM produce avventura (mappa, mostri, trama)
    "adventure",              # esplorazione/turni
    "combat",                 # combattimento
    "ended",                  # partita conclusa
)


def _ensure_runtime() -> None:
    os.makedirs(RUNTIME_DIR, exist_ok=True)
    os.makedirs(CHARACTERS_DIR, exist_ok=True)
    os.makedirs(ADVENTURES_DIR, exist_ok=True)


# Sanitizza il nome PG per generarne un filename sicuro:
# rimuove caratteri non alfanumerici, collassa spazi/trattini, taglia a 64 char.
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_\-]+")


def safe_char_filename(name: str) -> str:
    """Converte il nome PG in un nome file sicuro (senza estensione).
    Esempi: 'Thorìn Scudodiquercia' → 'Thorin_Scudodiquercia'."""
    import unicodedata
    n = unicodedata.normalize("NFKD", (name or "").strip())
    n = "".join(c for c in n if not unicodedata.combining(c))
    n = _SAFE_NAME_RE.sub("_", n).strip("_")
    return (n or "senza_nome")[:64]


def character_file_path(name: str) -> str:
    """Percorso assoluto del file per-personaggio 'nome.json'."""
    return os.path.join(CHARACTERS_DIR, safe_char_filename(name) + ".json")


def new_adventure_path(title: str = "") -> str:
    """Percorso NUOVO e univoco per un'avventura generata dal DM:
    'runtime/avventure/<timestamp>_<slug-del-titolo>.txt'. Ogni
    generazione ottiene un nome diverso, così non sovrascrive le
    precedenti (richiesto dal flusso "Nuova avventura")."""
    _ensure_runtime()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    slug = safe_char_filename(title)[:48] if title else "avventura"
    return os.path.join(ADVENTURES_DIR, f"{stamp}_{slug}.txt")


def current_adventure_path(state: dict) -> str:
    """Percorso del file dell'avventura ATTIVA. Preferisce
    game_state['adventure_file'] (avventura generata, nome univoco) e
    ripiega su ADVENTURE_FILE (avventura.txt caricata a mano) per
    retro-compatibilità con gli stati salvati prima di questo campo."""
    p = (state or {}).get("adventure_file")
    return p if p else ADVENTURE_FILE


def empty_state() -> dict:
    return {
        "phase":             "setup",
        "players":           [],     # [{name, type:"human"|"ai", sheet:{}}]
        "active_player":     None,   # nome del PG di turno
        "turn":              0,
        "round":             0,
        "initiative_order":  [],     # [{name, init}] in combat
        "map_base":           None,  # skeleton immutabile (muri) — fissato una volta
        "map_full":           None,  # mappa completa nota al DM (NON inviata all'utente)
        "map_ascii":          None,  # mappa visibile dall'utente (fog-of-war applicata)
        "map_width":          0,     # larghezza effettiva della mappa corrente
        "map_height":         0,     # altezza effettiva della mappa corrente
        "revealed_tiles":    [],     # lista di [x,y] tile rivelate dall'esplorazione
        "current_position":  [0, 0],
        "current_zone":      None,
        "current_scene":     None,   # etichetta scena corrente: ogni nuovo valore =
                                     # ambientazione diversa → mappa va ridisegnata da zero
        "zones":             [],     # [{pos:[x,y], type, name, desc}]
        "combat_active":     False,
        "encounter":         None,   # {monsters:[...], remaining_hp:{...}}
        "adventure_loaded":  False,
        "adventure_title":   None,
        "adventure_file":    None,   # path del TXT dell'avventura attiva
                                     # (generata: nome univoco in runtime/avventure)
        "adventure_beats":   [],     # avventura TXT precaricata, spezzata in scene
        "adventure_index":   0,      # prossimo beat da consegnare al DM
        "characters_loaded": False,
        "session_start":     datetime.now().isoformat(),
        "rolls_log":         [],     # ultimi 30 tiri per UI
        "pending_rolls":     [],     # ROLL_REQ in attesa: li tira il giocatore umano
        "pending_roll_feedback": [], # tiri IA risolti, non ancora narrati dal DM
        "pending_dm_notes":  [],     # note di sistema da consegnare al DM (es. slot esauriti)
        "music":             {},     # colonne sonore generate dal DM, per mood
        "sprites":           {},     # sprite pixel-art 16×16 generate dal DM
                                    # (vecchie 10×10 ancora accettate: scaling
                                    # nearest-neighbor in fase di render)
        "map_legend":        [],     # legenda della mappa: [{char, label}]
                                    # emessa dal DM in LEGENDA_START…LEGENDA_END
    }


def _read_json(path: str, default: Any) -> Any:
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default


def _write_json(path: str, data: Any) -> None:
    _ensure_runtime()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ──────────────── game state ─────────────────────────────────────────

def load_state() -> dict:
    """Legge game_state.json. Aggiunge campi mancanti dai default
    (migrazione automatica per schemi più vecchi)."""
    stored = _read_json(GAME_STATE_FILE, None)
    if stored is None:
        return empty_state()
    base = empty_state()
    for k, v in base.items():
        if k not in stored:
            stored[k] = v
    return stored


def save_state(state: dict) -> None:
    _write_json(GAME_STATE_FILE, state)


# ──────────────── config webchat (ultimo modello DM) ─────────────────

def load_webchat_config() -> dict | None:
    """Ultimo modello DM salvato (url/name/timeout), o None se mai scelto."""
    cfg = _read_json(WEBCHAT_CONFIG_FILE, None)
    return cfg if isinstance(cfg, dict) else None


def save_webchat_config(cfg: dict) -> None:
    """Salva il modello DM corrente per riproporlo al prossimo avvio."""
    if not isinstance(cfg, dict):
        return
    keep = {k: cfg[k] for k in ("url", "name", "timeout") if k in cfg}
    _write_json(WEBCHAT_CONFIG_FILE, keep)


# ──────────────── conversation history ───────────────────────────────

def load_conversation() -> list[dict]:
    return _read_json(CONVERSATION_FILE, [])


def save_conversation(history: list[dict]) -> None:
    _write_json(CONVERSATION_FILE, history)


# ──────────────── personaggi.json ───────────────────────────────────

def load_characters() -> dict | None:
    return _read_json(CHARACTERS_FILE, None)


def save_characters(data: dict) -> None:
    """Salva personaggi.json E scrive un file 'nome.json' per ogni PG nella
    cartella runtime/personaggi/: così le schede sono selezionabili
    singolarmente per comporre il party."""
    data["last_updated"] = datetime.now().isoformat()
    _write_json(CHARACTERS_FILE, data)
    for sheet in (data.get("characters") or []):
        if isinstance(sheet, dict):
            save_character_file(sheet)


def delete_characters() -> bool:
    """Cancella personaggi.json E tutte le schede 'nome.json' in
    runtime/personaggi/. Restituisce True se almeno un file è stato rimosso."""
    removed = False
    if os.path.exists(CHARACTERS_FILE):
        os.remove(CHARACTERS_FILE)
        removed = True
    if os.path.isdir(CHARACTERS_DIR):
        for fn in os.listdir(CHARACTERS_DIR):
            if fn.lower().endswith(".json"):
                try:
                    os.remove(os.path.join(CHARACTERS_DIR, fn))
                    removed = True
                except OSError:
                    pass
    return removed


# ──────────────── schede PG per-file (nome.json) ─────────────────────

def save_character_file(sheet: dict) -> str | None:
    """Salva la scheda PG su runtime/personaggi/<nome>.json.
    Restituisce il path scritto, oppure None se la scheda non ha nome."""
    name = (sheet.get("name") or "").strip()
    if not name:
        return None
    _ensure_runtime()
    path = character_file_path(name)
    payload = {
        "version": 1,
        "saved":   datetime.now().isoformat(),
        "name":    name,
        "sheet":   sheet,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return path


def load_character_file(name: str) -> dict | None:
    """Legge la scheda PG da runtime/personaggi/<nome>.json. Compatibile sia
    col formato wrapper {sheet:{...}} sia con un dict-scheda nudo."""
    path = character_file_path(name)
    raw = _read_json(path, None)
    if not isinstance(raw, dict):
        return None
    if isinstance(raw.get("sheet"), dict):
        return raw["sheet"]
    # formato legacy: il file è già la scheda
    return raw


def list_character_files() -> list[dict]:
    """Elenco delle schede salvate in runtime/personaggi/: per ognuna
    restituisce un riassunto (name, species, class, level, background,
    alignment, player_type, file, gender)."""
    _ensure_runtime()
    out: list[dict] = []
    if not os.path.isdir(CHARACTERS_DIR):
        return out
    for fn in sorted(os.listdir(CHARACTERS_DIR)):
        if not fn.lower().endswith(".json"):
            continue
        path = os.path.join(CHARACTERS_DIR, fn)
        raw = _read_json(path, None)
        if not isinstance(raw, dict):
            continue
        sheet = raw.get("sheet") if isinstance(raw.get("sheet"), dict) else raw
        if not isinstance(sheet, dict):
            continue
        out.append({
            "file":        fn,
            "name":        sheet.get("name") or os.path.splitext(fn)[0],
            "species":     sheet.get("species") or sheet.get("race") or "",
            "class":       sheet.get("class") or "",
            "level":       sheet.get("level") or 1,
            "background":  sheet.get("background") or "",
            "alignment":   sheet.get("alignment") or "",
            "player_type": sheet.get("player_type") or "human",
            "gender":      sheet.get("gender") or "",
            "saved":       raw.get("saved") if isinstance(raw.get("saved"), str) else None,
        })
    return out


def delete_character_file(name: str) -> bool:
    """Cancella la singola scheda 'nome.json' in runtime/personaggi/."""
    path = character_file_path(name)
    if os.path.exists(path):
        try:
            os.remove(path)
            return True
        except OSError:
            return False
    return False


def _deep_update(dst: dict, src: dict) -> None:
    """Merge profondo in-place: i dict annidati (hp, stats) sono uniti campo
    per campo, così un update parziale (es. solo hp.current) non azzera
    hp.max né gli altri campi della scheda salvata."""
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_update(dst[k], v)
        else:
            dst[k] = v


def upsert_character(sheet: dict) -> None:
    """Inserisce o aggiorna una scheda in personaggi.json (match per nome case-insensitive)."""
    name = (sheet.get("name") or "").strip()
    if not name:
        return
    data = load_characters() or {
        "version": 1,
        "created": datetime.now().isoformat(),
        "characters": [],
    }
    chars = data.setdefault("characters", [])
    idx = next((i for i, c in enumerate(chars)
                if (c.get("name") or "").lower() == name.lower()), None)
    if idx is not None:
        _deep_update(chars[idx], sheet)
        merged = chars[idx]
    else:
        chars.append(sheet)
        merged = sheet
    save_characters(data)
    # ridondante con save_characters() ma esplicita l'intento:
    # ogni upsert riflette SUBITO la scheda aggiornata nel file per-PG.
    save_character_file(merged)


def remove_character(name: str) -> bool:
    data = load_characters()
    if not data:
        return False
    n = (name or "").lower()
    chars = data.get("characters", [])
    new = [c for c in chars if (c.get("name") or "").lower() != n]
    if len(new) == len(chars):
        return False
    data["characters"] = new
    save_characters(data)
    delete_character_file(name)
    return True


def sync_characters_from_players(players: list[dict]) -> None:
    """Allinea personaggi.json alle schede correnti dei giocatori in
    game_state: ogni modifica fatta dal DM (XP, HP, livello, ecc.) finisce
    SEMPRE anche nelle schede personaggio persistenti. Merge profondo, una
    sola scrittura del file."""
    if not players:
        return
    data = load_characters() or {
        "version": 1,
        "created": datetime.now().isoformat(),
        "characters": [],
    }
    chars = data.setdefault("characters", [])
    # La sync gira a OGNI avvio (vedi app._normalize_existing_sheets). Senza
    # questo gate ogni import riscriverebbe personaggi.json E i file per-PG
    # anche quando nulla è cambiato: importare il modulo avrebbe un effetto
    # collaterale su disco. Confronto pre/post-merge → scrivi solo a
    # contenuto effettivamente diverso.
    before = json.dumps(chars, ensure_ascii=False, sort_keys=True)
    for p in players:
        sheet = p.get("sheet")
        if not isinstance(sheet, dict):
            continue
        name = (sheet.get("name") or "").strip()
        if not name:
            continue
        idx = next((i for i, c in enumerate(chars)
                    if (c.get("name") or "").lower() == name.lower()), None)
        if idx is not None:
            _deep_update(chars[idx], sheet)
        else:
            chars.append(dict(sheet))
    if json.dumps(chars, ensure_ascii=False, sort_keys=True) != before:
        save_characters(data)


# ──────────────── fasi ───────────────────────────────────────────────

def set_phase(state: dict, phase: str) -> None:
    if phase not in PHASES:
        raise ValueError(f"Fase sconosciuta: {phase!r}. Valide: {PHASES}")
    state["phase"] = phase


def add_roll(state: dict, roll_info: dict) -> None:
    """Aggiunge un tiro al log (max 30)."""
    log = state.setdefault("rolls_log", [])
    log.append({"ts": datetime.now().strftime("%H:%M:%S"), **roll_info})
    if len(log) > 30:
        del log[:-30]


def add_player(state: dict, name: str, player_type: str = "human",
               sheet: dict | None = None) -> dict:
    """Aggiunge un giocatore. Max 5. Restituisce il dict del player."""
    if len(state.get("players", [])) >= 5:
        raise ValueError("Massimo 5 giocatori al tavolo")
    if player_type not in ("human", "ai"):
        raise ValueError(f"Tipo giocatore non valido: {player_type!r}")
    player = {"name": name, "type": player_type, "sheet": sheet or {}}
    state.setdefault("players", []).append(player)
    return player


def find_player(state: dict, name: str) -> dict | None:
    n = (name or "").lower()
    for p in state.get("players", []):
        if (p.get("name") or "").lower() == n:
            return p
    return None


def has_human(state: dict) -> bool:
    return any(p.get("type") == "human" for p in state.get("players", []))


# ──────────────── mappa ──────────────────────────────────────────────

MAP_TILE_GLYPHS = {
    "#": "▓",   # muro
    ".": "·",   # corridoio
    "*": "✦",   # partenza
    "@": "☻",   # party
    "C": "⚔",   # combattimento
    "E": "?",   # esplorazione
    "S": "☺",   # PNG/social
    "X": "★",   # obiettivo
    "+": "┼",   # porta
    "<": "‹",   # scale su
    ">": "›",   # scale giù
    "~": "≈",   # acqua
    "T": "^",   # trappola
    # ── tile scenografici (mappa più ricca, stile retro/Game Boy) ──────
    "t": "♣",   # albero
    ",": "„",   # erba alta / sterpaglia
    "o": "●",   # masso / roccia
    "f": "♨",   # falò / fuoco
    "=": "≡",   # ponte
    "$": "⊡",   # forziere / tesoro
    # ── mostri/nemici sulla mappa (icone RPG distinte da 'C' che è ZONA) ──
    "M": "☠",   # mostro generico
    "g": "ɢ",   # goblin / coboldo / piccolo nemico
    "k": "☠",   # scheletro / non-morto
    "D": "Ɖ",   # drago / grande creatura
    "P": "✟",   # PG abbattuto / corpo
}


def render_map(ascii_map: str) -> str:
    """Converte caratteri ASCII in glifi Unicode per il rendering frontend."""
    if not ascii_map:
        return ""
    return "".join(MAP_TILE_GLYPHS.get(ch, ch) if ch != "\n" else "\n"
                   for ch in ascii_map)


def map_to_grid(ascii_map: str) -> list[list[str]]:
    """Trasforma la mappa ASCII in matrice 2D (per UI a celle)."""
    if not ascii_map:
        return []
    return [list(line) for line in ascii_map.splitlines() if line]


def find_position(ascii_map: str, marker: str = "@") -> tuple[int, int] | None:
    """Restituisce (x,y) del marker richiesto. None se non trovato."""
    if not ascii_map:
        return None
    for y, line in enumerate(ascii_map.splitlines()):
        for x, ch in enumerate(line):
            if ch == marker:
                return (x, y)
    return None


def reveal_around(state: dict, x: int, y: int, radius: int = 1) -> None:
    """Rivela tutte le tile attorno a (x,y) nel raggio dato (quadrato). Aggiorna
    state['revealed_tiles'] in-place (deduplica). Versione legacy senza
    line-of-sight: usata come fallback quando map_full non è disponibile."""
    revealed = state.setdefault("revealed_tiles", [])
    seen = {tuple(t) for t in revealed}
    for dx in range(-radius, radius + 1):
        for dy in range(-radius, radius + 1):
            tile = (x + dx, y + dy)
            if tile not in seen:
                seen.add(tile)
                revealed.append([tile[0], tile[1]])


def _los_clear(grid: list[str], x0: int, y0: int, x1: int, y1: int) -> bool:
    """True se nessun muro '#' interseca la linea dritta da (x0,y0) a
    (x1,y1) (esclusi gli estremi). Algoritmo di Bresenham.

    L'estremo (x1,y1) — se è esso stesso un muro — è considerato visibile
    (vedi il bordo della stanza dall'interno): solo i muri INTERMEDI
    occludono. Così una stanza chiusa illumina TUTTE le sue celle (incluso
    il perimetro), mentre un corridoio rivela soltanto fin dove l'occhio
    arriva prima di sbattere contro un muro.
    """
    dx = abs(x1 - x0)
    dy = abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx - dy
    x, y = x0, y0
    h = len(grid)
    while True:
        if (x, y) == (x1, y1):
            return True
        e2 = 2 * err
        if e2 > -dy:
            err -= dy
            x += sx
        if e2 < dx:
            err += dx
            y += sy
        if (x, y) == (x1, y1):
            return True
        if 0 <= y < h:
            row = grid[y]
            ch = row[x] if 0 <= x < len(row) else " "
            if ch == "#":
                return False


def reveal_los(state: dict, map_full: str, x: int, y: int,
               radius: int = 10) -> None:
    """Rivela le tile visibili da (x,y) con LINE-OF-SIGHT entro `radius`.

    Per ogni cella dentro un cerchio approssimato di raggio `radius`,
    traccia una linea (Bresenham) dal party alla cella. Se nessun muro
    interrompe la linea → cella rivelata. Conseguenze pratiche:
      • Stanza chiusa illuminata: tutto l'interno (incluso il perimetro
        di muri) viene rivelato in un solo turno, senza nebbia residua.
      • Corridoio: si vede solo il tratto in linea visiva, gli angoli
        restano nascosti finché non li si imbocca.
      • Spazio aperto (foresta, valle): visibile fino al limite di raggio.
    """
    if not map_full:
        return
    grid = [line for line in map_full.splitlines() if line is not None]
    if not grid:
        return
    revealed = state.setdefault("revealed_tiles", [])
    seen = {tuple(t) for t in revealed}
    h = len(grid)
    r2 = radius * radius
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            if dx * dx + dy * dy > r2:
                continue  # cerchio, non quadrato — torcia rotonda
            tx, ty = x + dx, y + dy
            if tx < 0 or ty < 0 or ty >= h:
                continue
            if tx >= len(grid[ty]):
                continue
            if (tx, ty) in seen:
                continue
            if _los_clear(grid, x, y, tx, ty):
                seen.add((tx, ty))
                revealed.append([tx, ty])


def apply_fog(map_full: str, revealed_tiles: list, party_pos: tuple | list | None = None) -> str:
    """
    Produce versione fog-of-war della mappa: solo le tile rivelate sono
    visibili. Le altre diventano ' ' (spazio). I muri ai bordi del visibile
    restano visibili (servono per dare contesto).
    Il marker @ è SEMPRE mostrato (posizione corrente del party).
    """
    if not map_full:
        return map_full
    grid = [list(line) for line in map_full.splitlines() if line]
    if not grid:
        return map_full
    revealed = {tuple(t) for t in (revealed_tiles or [])}
    if party_pos:
        revealed.add(tuple(party_pos))
    h = len(grid)
    w = max(len(r) for r in grid)
    # normalizza padding
    for row in grid:
        while len(row) < w:
            row.append(' ')

    out = [[' '] * w for _ in range(h)]
    for y in range(h):
        for x in range(w):
            if (x, y) in revealed:
                out[y][x] = grid[y][x]
            # rendi visibili anche i muri adiacenti a una tile rivelata
            elif grid[y][x] == '#':
                for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                    if (x + dx, y + dy) in revealed:
                        out[y][x] = '#'
                        break
    # marker @: forza visibilità
    if party_pos:
        px, py = party_pos
        if 0 <= py < h and 0 <= px < w:
            out[py][px] = '@'
    # NIENTE rstrip: ogni riga resta larga `w`. Righe a larghezza uniforme
    # → la griglia frontend ha dimensioni stabili e la mappa si ridisegna
    # sempre uguale (le celle non rivelate restano spazi = fog).
    return "\n".join("".join(row) for row in out)


def check_map_coherence(ascii_map: str,
                        current_position: list | tuple | None = None) -> dict:
    """
    Verifica che la mappa sia ben formata e coerente con la narrazione.
    Controlla: griglia rettangolare, glifi validi, presenza di partenza '*'
    e obiettivo 'X', raggiungibilità di 'X', e coerenza tra il marker '@'
    (dove il DM ha disegnato il party) e `current_position` (la posizione
    narrata nel testo).

    Restituisce un report con `issues` (lista di problemi) e
    `suggested_position`: la posizione con cui allineare il party perché
    mappa e testo restino coerenti.
    """
    report = {
        "ok": True, "issues": [], "width": 0, "height": 0,
        "start": None, "exit": None, "party": None,
        "connected": False, "suggested_position": None,
    }
    grid = [list(line) for line in (ascii_map or "").splitlines() if line]
    if not grid:
        report["ok"] = False
        report["issues"].append("Mappa assente o vuota.")
        return report

    h = len(grid)
    w = max(len(r) for r in grid)
    report["width"], report["height"] = w, h
    if any(len(r) != w for r in grid):
        report["issues"].append(f"Righe di lunghezza disuguale (attesa {w}).")

    valid = set(MAP_TILE_GLYPHS) | {" "}
    bad = sorted({ch for r in grid for ch in r if ch not in valid})
    if bad:
        report["issues"].append("Caratteri non validi: " + " ".join(bad))

    def _find(marker: str):
        for y, r in enumerate(grid):
            for x, ch in enumerate(r):
                if ch == marker:
                    return (x, y)
        return None

    start = _find("*")
    exit_ = _find("X")
    party = _find("@")
    report["start"], report["exit"], report["party"] = start, exit_, party
    if start is None:
        report["issues"].append("Manca il marker di partenza '*'.")
    if exit_ is None:
        report["issues"].append("Manca il marker obiettivo 'X'.")

    # connettività: flood-fill sulle tile non-muro
    origin = start or party
    if origin and exit_:
        seen: set[tuple[int, int]] = set()
        stack = [origin]
        while stack:
            x, y = stack.pop()
            if (x, y) in seen:
                continue
            if not (0 <= y < h and 0 <= x < len(grid[y])):
                continue
            if grid[y][x] == "#":
                continue
            seen.add((x, y))
            stack += [(x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)]
        report["connected"] = exit_ in seen
        if not report["connected"]:
            report["issues"].append("L'obiettivo 'X' non è raggiungibile dalla partenza '*'.")

    # coerenza tra marker @ (mappa) e posizione narrata (testo)
    cur = list(current_position) if current_position is not None else None
    if party is not None:
        report["suggested_position"] = [party[0], party[1]]
        if cur is not None and cur != [party[0], party[1]]:
            report["issues"].append(
                f"Posizione nel testo {cur} diversa dal marker @ {[party[0], party[1]]} sulla mappa.")
    elif cur is not None:
        x, y = cur
        if 0 <= y < h and 0 <= x < len(grid[y]) and grid[y][x] != "#":
            report["suggested_position"] = [x, y]
            report["party"] = [x, y]
        else:
            report["issues"].append(f"Posizione {cur} fuori mappa o sopra un muro.")
            # snap automatico: cerca la cella non-muro più vicina (BFS).
            # Serve quando map_full ha già il @ rimosso (caso normale in
            # app.py): in assenza di party sulla mappa il check non avrebbe
            # un fallback e il marker resterebbe sopra un muro.
            from collections import deque
            q: deque = deque([(x, y, 0)])
            seen = {(x, y)}
            while q:
                cx, cy, d = q.popleft()
                if (0 <= cy < h and 0 <= cx < len(grid[cy])
                        and grid[cy][cx] != "#"):
                    report["suggested_position"] = [cx, cy]
                    break
                if d > 20:
                    continue
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nx, ny = cx + dx, cy + dy
                    if (nx, ny) not in seen:
                        seen.add((nx, ny))
                        q.append((nx, ny, d + 1))

    report["ok"] = not report["issues"]
    return report


def move_party(ascii_map: str, new_x: int, new_y: int) -> str:
    """Sposta il marker @ sulla mappa nella nuova posizione (se valida)."""
    grid = map_to_grid(ascii_map)
    if not grid:
        return ascii_map
    if not (0 <= new_y < len(grid) and 0 <= new_x < len(grid[new_y])):
        return ascii_map
    if grid[new_y][new_x] == "#":
        return ascii_map  # collisione muro
    # rimuovi @ esistente
    for y, row in enumerate(grid):
        for x, ch in enumerate(row):
            if ch == "@":
                grid[y][x] = "."
    grid[new_y][new_x] = "@"
    return "\n".join("".join(row) for row in grid)


# ──────────────── avventura precaricata ──────────────────────────────

_BEAT_SEP_RE = re.compile(r"^[-=*#_]{3,}\s*$")
_BEAT_HEADING_RE = re.compile(
    r"^\s*(?:#{1,4}\s+|\*{0,2})?"
    r"(?:scena|capitolo|atto|parte|beat|incontro|prologo|epilogo)\b",
    re.IGNORECASE,
)


def split_into_beats(text: str, target_chars: int = 600) -> list[str]:
    """Spezza una avventura TXT in "beat" (scene) da giocare uno alla volta.

    Confini di beat ESPLICITI (forzano sempre un nuovo beat):
      - Righe di separazione: '---', '===', '***', '###', '___' (≥3 char).
      - Intestazioni di scena: una riga che inizia con
        Scena/Capitolo/Atto/Parte/Beat/Incontro/Prologo/Epilogo
        (con o senza heading markdown '#').
    Confini DEBOLI: una riga vuota chiude il paragrafo ma NON chiude il
    beat — i paragrafi corti vengono uniti fino a ~`target_chars`. Beat
    lunghi NON vengono spezzati: il DM regge."""
    if not text or not text.strip():
        return []
    raw = text.replace("\r\n", "\n").replace("\r", "\n")

    # Fase 1: raccolta "sezioni" tra confini espliciti.
    sections: list[list[str]] = []
    cur: list[str] = []
    cur_para: list[str] = []

    def flush_para():
        if cur_para:
            cur.append("\n".join(cur_para).strip())
            cur_para.clear()

    def flush_section(start_with: str | None = None):
        flush_para()
        if cur:
            sections.append(cur.copy())
            cur.clear()
        if start_with:
            cur.append(start_with)

    for line in raw.split("\n"):
        s = line.strip()
        if not s:
            flush_para()
            continue
        if _BEAT_SEP_RE.match(s):
            flush_section()
            continue
        if _BEAT_HEADING_RE.match(s) and len(s) < 120:
            # intestazione: chiude il beat precedente, apre uno nuovo
            # con l'intestazione come prima riga
            flush_section(start_with=s)
            continue
        cur_para.append(line)
    flush_section()

    # Fase 2: dentro ogni sezione, raggruppa paragrafi fino a target_chars
    # in beat distinti (per testi con sezioni MOLTO lunghe).
    beats: list[str] = []
    for sec in sections:
        if not sec:
            continue
        buf = ""
        for p in sec:
            if not buf:
                buf = p
            elif len(buf) + len(p) + 2 <= target_chars:
                buf = buf + "\n\n" + p
            else:
                beats.append(buf)
                buf = p
        if buf:
            beats.append(buf)
    return beats


IMMUTABLE_TILES = set("#")
"""Caratteri di mappa che NON cambiano mai una volta fissato il dungeon.
Solo i muri sono davvero immutabili: il DM aggiorna i mostri, i tesori
(saccheggiati), le trappole (scattate), il party e i PG abbattuti."""


def compose_map(base: str, new_map: str) -> str:
    """Compone la nuova mappa del DM sopra quella base (la skeleton
    immutabile del dungeon).

    - Le celle muro (#) della base restano SEMPRE muro: protegge la
      coerenza del dungeon se il DM "buca" un muro per sbaglio.
    - Tutte le altre celle (pavimento, decorazioni, mostri, tesori, PG
      abbattuti, party @) prendono il valore da `new_map`: così mostri,
      cadaveri, forzieri vuoti e cambi d'ambiente si aggiornano.
    Entrambe le mappe sono normalizzate 20×20."""
    if not base:
        return new_map
    if not new_map:
        return base
    b = base.splitlines()
    n = new_map.splitlines()
    h = max(len(b), len(n))
    out = []
    for y in range(h):
        br = b[y] if y < len(b) else ""
        nr = n[y] if y < len(n) else ""
        w = max(len(br), len(nr))
        row = []
        for x in range(w):
            bc = br[x] if x < len(br) else "#"
            nc = nr[x] if x < len(nr) else bc
            if bc in IMMUTABLE_TILES:
                row.append(bc)
            else:
                row.append(nc if nc and nc != " " else bc)
        out.append("".join(row))
    return "\n".join(out)


MAP_MIN_SIDE = 20
MAP_MAX_SIDE = 40


def measure_map(ascii_map: str) -> tuple[int, int]:
    """Restituisce (larghezza, altezza) effettive della mappa ASCII —
    larghezza = riga più lunga, altezza = numero di righe non vuote.
    Clamp ai limiti consentiti."""
    rows = [line for line in (ascii_map or "").splitlines() if line.strip()]
    if not rows:
        return (0, 0)
    h = min(MAP_MAX_SIDE, max(MAP_MIN_SIDE, len(rows)))
    w = min(MAP_MAX_SIDE, max(MAP_MIN_SIDE,
                              max(len(r.rstrip()) for r in rows)))
    return (w, h)


def _regularize_width(rows: list[str]) -> list[str]:
    """Rende tutte le righe della STESSA larghezza, così la griglia è un
    rettangolo perfetto anche quando il modello sbaglia a contare i
    caratteri (off-by-one: una riga 21 fra righe da 20).

    Larghezza obiettivo = la più FREQUENTE fra le righe non vuote (quella
    che il modello "intendeva"). Righe più corte: si allungano replicando
    l'ultimo carattere (di norma il bordo '#', così il muro resta chiuso);
    righe più lunghe: si troncano mantenendo l'ULTIMO carattere (il bordo
    destro non si perde). Le righe vuote vengono scartate."""
    body = [r for r in rows if r.strip()]
    if not body:
        return body
    from collections import Counter
    target = Counter(len(r) for r in body).most_common(1)[0][0]
    out: list[str] = []
    for r in body:
        if len(r) == target:
            out.append(r)
        elif len(r) < target:
            pad = (r[-1] if r else "#")
            out.append(r + pad * (target - len(r)))
        else:  # troppo lunga: tieni inizio + ultimo carattere (bordo)
            out.append(r[:target - 1] + r[-1])
    return out


def set_raw_map(state: dict, map_block: str) -> tuple[int, int]:
    """Imposta la mappa nello stato come l'ha disegnata il modello, con
    l'UNICA correzione di regolarizzare la larghezza delle righe (vedi
    _regularize_width): niente normalizzazione dei caratteri, niente
    fog-of-war, niente controllo di coerenza posizione↔muri.

    `map_ascii` (la vista mostrata) = `map_full` = blocco del DM con le
    righe portate tutte alla stessa larghezza. Il marker @ resta dentro il
    blocco, così il frontend disegna il party e l'illuminazione sulla cella
    dove l'ha messo il modello. Aggiorna `current_position` con le
    coordinate di @ se presente.

    Restituisce (larghezza, altezza) effettive del blocco regolarizzato."""
    rows = _regularize_width((map_block or "").rstrip("\n").splitlines())
    raw = "\n".join(rows)
    h = len(rows)
    w = max((len(r) for r in rows), default=0)
    state["map_base"]       = raw
    state["map_full"]       = raw
    state["map_ascii"]      = raw
    state["map_width"]      = w
    state["map_height"]     = h
    state["revealed_tiles"] = []     # niente fog: tutto sempre visibile
    at = find_position(raw, "@")
    if at:
        state["current_position"] = [at[0], at[1]]
    return (w, h)


def normalize_map(ascii_map: str,
                  width: int | None = None,
                  height: int | None = None) -> str:
    """Normalizza la mappa a `width`×`height` (rettangolare). Se `width`
    o `height` non sono passati li ricava dalla mappa stessa (larghezza =
    riga più lunga, altezza = numero righe), con clamp [MAP_MIN_SIDE,
    MAP_MAX_SIDE]. Righe/colonne mancanti riempite con muri '#',
    eccedenze tagliate: la griglia resta SEMPRE regolare."""
    # retro-compatibilità: vecchia firma `normalize_map(map, 20)` con
    # `width` passato come int → significa quadrata di lato `width`.
    if width is not None and height is None:
        height = width
    if width is None or height is None:
        mw, mh = measure_map(ascii_map)
        width  = width  if width  is not None else (mw or MAP_MIN_SIDE)
        height = height if height is not None else (mh or MAP_MIN_SIDE)
    width  = max(MAP_MIN_SIDE, min(MAP_MAX_SIDE, int(width)))
    height = max(MAP_MIN_SIDE, min(MAP_MAX_SIDE, int(height)))
    src = [line for line in (ascii_map or "").splitlines() if line.strip()]
    out = []
    for y in range(height):
        row = list(src[y]) if y < len(src) else []
        row = row[:width]
        while len(row) < width:
            row.append("#")
        out.append("".join(row))
    return "\n".join(out)


__all__ = [
    "PHASES", "GAME_STATE_FILE", "CONVERSATION_FILE",
    "CHARACTERS_FILE", "CHARACTERS_DIR", "ADVENTURE_FILE", "ADVENTURES_DIR",
    "new_adventure_path", "current_adventure_path", "RUNTIME_DIR",
    "WEBCHAT_CONFIG_FILE", "load_webchat_config", "save_webchat_config",
    "empty_state", "load_state", "save_state",
    "load_conversation", "save_conversation",
    "load_characters", "save_characters", "delete_characters",
    "upsert_character", "remove_character", "sync_characters_from_players",
    "save_character_file", "load_character_file",
    "list_character_files", "delete_character_file",
    "safe_char_filename", "character_file_path",
    "set_phase", "add_roll", "add_player", "find_player", "has_human",
    "MAP_TILE_GLYPHS", "IMMUTABLE_TILES", "render_map", "map_to_grid",
    "find_position", "move_party", "normalize_map", "measure_map",
    "MAP_MIN_SIDE", "MAP_MAX_SIDE", "compose_map", "reveal_around",
    "reveal_los", "apply_fog", "check_map_coherence", "split_into_beats",
]
