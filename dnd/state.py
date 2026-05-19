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

GAME_STATE_FILE   = os.path.join(RUNTIME_DIR, "game_state.json")
CONVERSATION_FILE = os.path.join(RUNTIME_DIR, "conversation.json")
CHARACTERS_FILE   = os.path.join(RUNTIME_DIR, "personaggi.json")
ADVENTURE_FILE    = os.path.join(BASE_DIR, "avventura.txt")


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


def empty_state() -> dict:
    return {
        "phase":             "setup",
        "players":           [],     # [{name, type:"human"|"ai", sheet:{}}]
        "active_player":     None,   # nome del PG di turno
        "turn":              0,
        "round":             0,
        "initiative_order":  [],     # [{name, init}] in combat
        "map_full":           None,  # mappa completa nota al DM (NON inviata all'utente)
        "map_ascii":          None,  # mappa visibile dall'utente (fog-of-war applicata)
        "revealed_tiles":    [],     # lista di [x,y] tile rivelate dall'esplorazione
        "current_position":  [0, 0],
        "current_zone":      None,
        "zones":             [],     # [{pos:[x,y], type, name, desc}]
        "combat_active":     False,
        "encounter":         None,   # {monsters:[...], remaining_hp:{...}}
        "adventure_loaded":  False,
        "adventure_title":   None,
        "characters_loaded": False,
        "session_start":     datetime.now().isoformat(),
        "rolls_log":         [],     # ultimi 30 tiri per UI
        "pending_rolls":     [],     # ROLL_REQ in attesa: li tira il giocatore umano
        "pending_roll_feedback": [], # tiri IA risolti, non ancora narrati dal DM
        "pending_dm_notes":  [],     # note di sistema da consegnare al DM (es. slot esauriti)
        "music":             {},     # colonne sonore generate dal DM, per mood
        "sprites":           {},     # sprite pixel-art 10×10 generate dal DM
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


# ──────────────── conversation history ───────────────────────────────

def load_conversation() -> list[dict]:
    return _read_json(CONVERSATION_FILE, [])


def save_conversation(history: list[dict]) -> None:
    _write_json(CONVERSATION_FILE, history)


# ──────────────── personaggi.json ───────────────────────────────────

def load_characters() -> dict | None:
    return _read_json(CHARACTERS_FILE, None)


def save_characters(data: dict) -> None:
    data["last_updated"] = datetime.now().isoformat()
    _write_json(CHARACTERS_FILE, data)


def delete_characters() -> bool:
    if os.path.exists(CHARACTERS_FILE):
        os.remove(CHARACTERS_FILE)
        return True
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
    else:
        chars.append(sheet)
    save_characters(data)


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
    """Rivela tutte le tile attorno a (x,y) nel raggio dato. Aggiorna
    state['revealed_tiles'] in-place (deduplica)."""
    revealed = state.setdefault("revealed_tiles", [])
    seen = {tuple(t) for t in revealed}
    for dx in range(-radius, radius + 1):
        for dy in range(-radius, radius + 1):
            tile = (x + dx, y + dy)
            if tile not in seen:
                seen.add(tile)
                revealed.append([tile[0], tile[1]])


def apply_fog(map_full: str, revealed_tiles: list, party_pos: tuple | list = None) -> str:
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


def normalize_map(ascii_map: str, size: int = 10) -> str:
    """Forza la mappa a `size`×`size`. Righe e colonne mancanti riempite
    con muri '#', quelle in eccesso tagliate: la griglia resta SEMPRE
    regolare e quadrata (default 10×10)."""
    src = [line for line in (ascii_map or "").splitlines() if line.strip()]
    out = []
    for y in range(size):
        row = list(src[y]) if y < len(src) else []
        row = row[:size]
        while len(row) < size:
            row.append("#")
        out.append("".join(row))
    return "\n".join(out)


__all__ = [
    "PHASES", "GAME_STATE_FILE", "CONVERSATION_FILE",
    "CHARACTERS_FILE", "ADVENTURE_FILE", "RUNTIME_DIR",
    "empty_state", "load_state", "save_state",
    "load_conversation", "save_conversation",
    "load_characters", "save_characters", "delete_characters",
    "upsert_character", "remove_character", "sync_characters_from_players",
    "set_phase", "add_roll", "add_player", "find_player", "has_human",
    "MAP_TILE_GLYPHS", "render_map", "map_to_grid", "find_position",
    "move_party", "normalize_map", "reveal_around", "apply_fog",
    "check_map_coherence",
]
