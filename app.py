"""
Routing + collante. Tutta la logica vive in:
  dnd/   — regole, schede, bestiario, stato
  dm/    — prompt DM, webchat Playwright, parser tag

Avvio: python app.py  →  http://localhost:5000
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import re
import sys
import threading
import webbrowser
from datetime import datetime

# Console Windows usa cp1252 di default: ogni print con un carattere non-
# Latin-1 (frecce →, simboli ⚔, ecc.) solleva UnicodeEncodeError e fa
# fallire il chiamante. Tutto il codebase italiano usa caratteri Unicode
# nei log: senza questa riconfigurazione il briefing chunked stampato
# in [WEBCHAT] crash → l'invio del prompt al DM non parte e Chromium
# non riceve nulla quando il giocatore preme «Carica».
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

from flask import Flask, Response, jsonify, render_template, request, stream_with_context

from dnd import bestiary, character as char_mod, rules, spells as spells_mod, state as state_mod
from dm import parser, prompt as prompt_mod
from dm.webchat import Webchat, WebchatConfig

# Voce umana via Microsoft Edge TTS (opzionale: pip install edge-tts)
try:
    import edge_tts
    _EDGE_TTS_OK = True
except Exception:
    edge_tts = None
    _EDGE_TTS_OK = False


# ────────────────────────────────────────────────────────────────────────
# Bootstrap
# ────────────────────────────────────────────────────────────────────────

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__,
            template_folder=os.path.join(BASE_DIR, "templates"),
            static_folder=os.path.join(BASE_DIR, "static"))
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
app.jinja_env.auto_reload = True

state_mod._ensure_runtime()

game_state: dict = state_mod.load_state()
conversation_history: list[dict] = state_mod.load_conversation()

# Migrazione one-shot all'avvio: normalizza tutte le schede in game_state +
# personaggi.json così bonus dichiarati in linguaggio naturale negli oggetti
# magici (es. "Anello di protezione · +2 CA") riflettono nei valori di gioco
# (CA, FOR, HP …) anche per schede salvate prima dell'introduzione del parser.
def _normalize_existing_sheets() -> None:
    changed = False
    for p in game_state.get("players", []):
        sheet = p.get("sheet") or {}
        if not sheet:
            continue
        before = (sheet.get("ac"), sheet.get("ac_base"))
        char_mod.upgrade_sheet(sheet)
        if before != (sheet.get("ac"), sheet.get("ac_base")):
            changed = True
    state_mod.sync_characters_from_players(game_state.get("players", []))
    if changed:
        state_mod.save_state(game_state)

_normalize_existing_sheets()

_state_lock = threading.Lock()
_debug: dict = {"exchanges": []}

# conteggio messaggi del giocatore: ogni 3 si chiede al DM di rigenerare
# la colonna sonora e gli sprite pixel-art coerenti col contesto.
_msg_count = 0
EXTRAS_EVERY = 3

# briefing iniziale: una sola sincronizzazione per volta col DM
_briefing_lock = threading.Lock()
_briefing_running = False

webchat = Webchat(WebchatConfig())

# Ripristina l'ultimo modello DM scelto in Setup: all'avvio l'app riapre
# quello invece del default DeepSeek. Solo url/name/timeout vengono persi.
_saved_webchat_cfg = state_mod.load_webchat_config()
if _saved_webchat_cfg:
    if _saved_webchat_cfg.get("url"):
        webchat.config.url = _saved_webchat_cfg["url"].strip()
    if _saved_webchat_cfg.get("name"):
        webchat.config.name = _saved_webchat_cfg["name"].strip()
    try:
        if _saved_webchat_cfg.get("timeout"):
            webchat.config.timeout = max(15, min(600, int(_saved_webchat_cfg["timeout"])))
    except (TypeError, ValueError):
        pass
    print(f"[WEBCHAT] modello ripristinato da config: {webchat.config.url}", flush=True)


def _apply_dm_response(raw_text: str, user_label: str = "[Briefing]") -> str | None:
    """Processa una risposta GREZZA del DM (es. quella del briefing iniziale
    dell'avventura precaricata): estrae mappa, STATE_UPDATE, CHAR_UPDATE,
    incantesimi, sprite e musica; applica al `game_state`; appende il testo
    "mostrato" allo `conversation_history` come messaggio assistant e
    salva su disco. Ritorna il testo pulito da mostrare in chat, o None
    se la risposta è vuota.

    Variante semplificata di `_dm_exchange` (in /api/chat): non gira il
    follow-up loop dei ROLL_REQ (i tiri umani vanno comunque al riquadro
    dadi; i ROLL_REQ IA li tirerà al prossimo messaggio del giocatore)."""
    if not (raw_text or "").strip():
        return None

    cleaned = parser.clean_text(raw_text)
    dm_msg_id = hashlib.md5(
        (raw_text or "").encode("utf-8", errors="replace")
    ).hexdigest()[:16]

    human_names = [
        p.get("name")
        for p in game_state.get("players", [])
        if p.get("type") == "human"
    ]
    with_rolls, roll_results, pending_rolls = parser.resolve_roll_requests(
        cleaned, human_names
    )

    state_upd   = parser.extract_state(with_rolls)
    char_upds   = parser.extract_chars(with_rolls)
    map_block   = parser.extract_map(with_rolls)
    music_upds  = parser.extract_music(with_rolls)
    sprite_upds = parser.extract_sprites(with_rolls)
    cast_upds   = parser.extract_spell_casts(with_rolls)

    map_report = None
    cast_report: list[dict] = []
    with _state_lock:
        prev_scene = (game_state.get("current_scene") or "").strip()
        if isinstance(state_upd, dict):
            state_upd.pop("long_rest", None)
            state_upd.pop("session_end", None)
        if state_upd:
            parser.apply_state_update(game_state, state_upd, message_id=dm_msg_id)
        if char_upds:
            parser.apply_char_updates(game_state, char_upds, message_id=dm_msg_id)
            for p in game_state.get("players", []):
                char_mod.upgrade_sheet(p.get("sheet") or {})
        cast_report = parser.apply_spell_casts(game_state, cast_upds)
        new_scene = (game_state.get("current_scene") or "").strip()
        scene_changed = bool(new_scene) and new_scene != prev_scene
        if map_block:
            bw, bh = state_mod.measure_map(map_block)
            norm = state_mod.normalize_map(map_block, bw, bh)
            at = state_mod.find_position(norm, "@")
            norm_no_at = norm.replace("@", ".")
            prev_w = game_state.get("map_width") or 0
            prev_h = game_state.get("map_height") or 0
            prev_full = game_state.get("map_full") or ""
            if (bw, bh) != (prev_w, prev_h) or norm_no_at != prev_full or scene_changed:
                # cambia dimensione, layout O scena → fog azzerata
                game_state["revealed_tiles"] = []
            game_state["map_base"]   = norm_no_at
            game_state["map_full"]   = norm_no_at
            game_state["map_width"]  = bw
            game_state["map_height"] = bh
            if at:
                game_state["current_position"] = [at[0], at[1]]
        elif scene_changed:
            # Nuova scena ma il DM non ha emesso la mappa: NON azzeriamo
            # quella precedente (lasciare il pannello vuoto = "mappa non
            # disegnata"). La teniamo a video finché il DM non manda la
            # nuova, e lo SOLLECITIAMO a ridisegnarla SUBITO al prossimo
            # turno. Meglio una mappa un turno vecchia che nessuna mappa.
            game_state.setdefault("pending_dm_notes", []).append(
                f"[Sistema] NUOVA SCENA «{new_scene}» — emetti SUBITO una "
                "MAPPA COMPLETAMENTE NUOVA (MAP_START…MAP_END) coerente "
                "con questa scena: nuove dimensioni adatte al luogo, "
                "nuovo layout di muri/decorazioni, tile coerenti "
                "coll'ambientazione, mostri/PNG/tesori della scena. "
                "NON riusare la mappa precedente."
            )
        pos = game_state.get("current_position") or [0, 0]
        if game_state.get("map_full"):
            map_report = state_mod.check_map_coherence(game_state["map_full"], pos)
            sug = map_report.get("suggested_position")
            if sug and list(sug) != list(pos):
                pos = [sug[0], sug[1]]
                game_state["current_position"] = pos
        if game_state.get("map_full"):
            state_mod.reveal_los(game_state, game_state["map_full"],
                                 pos[0], pos[1], radius=10)
        else:
            state_mod.reveal_around(game_state, pos[0], pos[1], radius=2)
        if game_state.get("map_full"):
            game_state["map_ascii"] = state_mod.apply_fog(
                game_state["map_full"],
                game_state.get("revealed_tiles", []),
                party_pos=pos,
            )
        for r in roll_results:
            state_mod.add_roll(game_state, r)
        if music_upds:
            parser.apply_music_update(game_state, music_upds)
        if sprite_upds:
            parser.apply_sprites(game_state, sprite_upds)
        state_mod.sync_characters_from_players(game_state.get("players", []))
        game_state["pending_rolls"] = pending_rolls
        state_mod.save_state(game_state)

    display_text = parser.strip_narrative(with_rolls)

    with _state_lock:
        conversation_history.append({"role": "assistant", "content": display_text})
        state_mod.save_conversation(conversation_history)
        if len(conversation_history) > 40:
            conversation_history[:] = conversation_history[-40:]
            state_mod.save_conversation(conversation_history)

    with _state_lock:
        _debug["exchanges"].append({
            "ts":      datetime.now().isoformat(timespec="seconds"),
            "user":    user_label,
            "raw":     (raw_text or "")[:2000],
            "shown":   display_text[:2000],
            "rolls":   roll_results,
            "map":     map_report,
            "sprites": sorted(sprite_upds.keys()),
            "casts":   cast_report,
        })
        if len(_debug["exchanges"]) > 20:
            _debug["exchanges"].pop(0)

    print(f"[DM/briefing] {len(display_text)} chars · {len(roll_results)} tiri IA "
          f"· {len(pending_rolls)} tiri attesi · {len(sprite_upds)} sprite "
          f"· {len(cast_report)} incantesimi", flush=True)
    return display_text


# ────────────────────────────────────────────────────────────────────────
# Pagine
# ────────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


# ────────────────────────────────────────────────────────────────────────
# Stato e dati statici
# ────────────────────────────────────────────────────────────────────────

@app.route("/api/state")
def api_state():
    with _state_lock:
        for p in game_state.get("players", []):
            char_mod.upgrade_sheet(p.setdefault("sheet", {}))
    return jsonify(game_state)


@app.route("/api/dnd_data")
def api_dnd_data():
    return jsonify({
        "species":     char_mod.species_list(),
        "classes":     char_mod.class_list(),
        "backgrounds": char_mod.background_list(),
        "alignments":  char_mod.alignment_list(),
        "genders":     char_mod.gender_list(),
    })


@app.route("/api/bestiary")
def api_bestiary():
    cr_min = float(request.args.get("cr_min", 0))
    cr_max = float(request.args.get("cr_max", 30))
    return jsonify({
        "meta":     bestiary.meta(),
        "monsters": bestiary.filter_by_cr(cr_min, cr_max),
    })


# ────────────────────────────────────────────────────────────────────────
# Personaggi
# ────────────────────────────────────────────────────────────────────────

@app.route("/api/characters", methods=["GET"])
def api_characters_get():
    data = state_mod.load_characters() or {"characters": []}
    for c in data.get("characters", []):
        char_mod.upgrade_sheet(c)
    return jsonify(data)


@app.route("/api/characters", methods=["POST"])
def api_characters_post():
    """
    Body: {"characters": [{name, species, class, background, alignment, player_type}, ...]}
    Genera la scheda completa per ogni PG e la persiste su personaggi.json.
    """
    body = request.get_json(silent=True) or {}
    raw_list = body.get("characters", [])
    if not isinstance(raw_list, list) or not raw_list:
        return jsonify({"error": "Lista 'characters' richiesta"}), 400
    if len(raw_list) > 5:
        return jsonify({"error": "Massimo 5 personaggi"}), 400

    sheets = []
    for raw in raw_list:
        try:
            sheet = char_mod.generate_sheet(
                name=raw.get("name", "Senza nome"),
                species=raw.get("species") or raw.get("race", "Umano"),
                cls=raw.get("class", "Guerriero"),
                level=int(raw.get("level", 1)),
                background=raw.get("background", "Soldato"),
                alignment=raw.get("alignment", "Neutrale"),
                player_type=raw.get("player_type", "human"),
                gender=raw.get("gender", ""),
            )
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        sheets.append(sheet)

    data = {
        "version": 1,
        "created": datetime.now().isoformat(),
        "characters": sheets,
    }
    state_mod.save_characters(data)

    # rispecchia in game_state.players
    with _state_lock:
        game_state["players"] = [
            {"name": s["name"], "type": s["player_type"], "sheet": s}
            for s in sheets
        ]
        game_state["characters_loaded"] = True
        state_mod.save_state(game_state)

    return jsonify({"status": "ok", "count": len(sheets), "characters": sheets})


@app.route("/api/characters", methods=["DELETE"])
def api_characters_delete():
    ok = state_mod.delete_characters()
    with _state_lock:
        game_state["players"] = []
        game_state["characters_loaded"] = False
        state_mod.save_state(game_state)
    return jsonify({"status": "deleted" if ok else "no_file"})


@app.route("/api/characters/available", methods=["GET"])
def api_characters_available():
    """Elenco delle schede 'nome.json' salvate in runtime/personaggi/.
    Restituisce un riassunto per ogni file (name, species, class, level,
    background, alignment, player_type, gender, file, saved) — il
    frontend lo usa per la selezione del party."""
    return jsonify({"characters": state_mod.list_character_files()})


@app.route("/api/characters/<name>", methods=["GET"])
def api_character_get(name: str):
    """Restituisce la scheda completa di un singolo PG salvato su file."""
    sheet = state_mod.load_character_file(name)
    if sheet is None:
        return jsonify({"error": f"Personaggio non trovato: {name}"}), 404
    char_mod.upgrade_sheet(sheet)
    return jsonify({"sheet": sheet})


@app.route("/api/characters/<name>", methods=["DELETE"])
def api_character_delete(name: str):
    """Cancella la singola scheda 'nome.json' (e la rimuove anche da
    personaggi.json e dal party corrente)."""
    state_mod.delete_character_file(name)
    state_mod.remove_character(name)
    with _state_lock:
        n = (name or "").strip().lower()
        game_state["players"] = [
            p for p in game_state.get("players", [])
            if (p.get("name") or "").strip().lower() != n
        ]
        if not game_state["players"]:
            game_state["characters_loaded"] = False
        state_mod.save_state(game_state)
    return jsonify({"status": "deleted"})


@app.route("/api/party/load", methods=["POST"])
def api_party_load():
    """Compone il party caricando le schede 'nome.json' richieste e le
    inserisce in game_state.players (e in personaggi.json come elenco
    ufficiale del party corrente).

    Body: {"names": ["Thorin", "Lyra", ...]}
    Max 5 personaggi. Almeno 1 umano richiesto."""
    body = request.get_json(silent=True) or {}
    names = body.get("names")
    if not isinstance(names, list) or not names:
        return jsonify({"error": "Lista 'names' richiesta"}), 400
    if len(names) > 5:
        return jsonify({"error": "Massimo 5 personaggi nel party"}), 400

    loaded: list[dict] = []
    missing: list[str] = []
    for n in names:
        sheet = state_mod.load_character_file(str(n))
        if sheet is None:
            missing.append(str(n))
        else:
            char_mod.upgrade_sheet(sheet)
            loaded.append(sheet)
    if missing:
        return jsonify({"error": f"Schede non trovate: {', '.join(missing)}"}), 404
    if not any((s.get("player_type") or "human") == "human" for s in loaded):
        return jsonify({"error": "Almeno 1 giocatore umano richiesto"}), 400

    data = {
        "version": 1,
        "created": datetime.now().isoformat(),
        "characters": loaded,
    }
    state_mod.save_characters(data)

    with _state_lock:
        game_state["players"] = [
            {"name": s.get("name"),
             "type": s.get("player_type", "human"),
             "sheet": s}
            for s in loaded
        ]
        game_state["characters_loaded"] = True
        state_mod.save_state(game_state)

    return jsonify({
        "status":     "ok",
        "count":      len(loaded),
        "characters": loaded,
    })


@app.route("/api/generate_sheet", methods=["POST"])
def api_generate_sheet():
    """Genera una scheda di anteprima SENZA salvarla."""
    body = request.get_json(silent=True) or {}
    try:
        sheet = char_mod.generate_sheet(
            name=body.get("name", "Anteprima"),
            species=body.get("species") or body.get("race", "Umano"),
            cls=body.get("class", "Guerriero"),
            level=int(body.get("level", 1)),
            background=body.get("background", "Soldato"),
            alignment=body.get("alignment", "Neutrale"),
            player_type=body.get("player_type", "human"),
            gender=body.get("gender", ""),
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"sheet": sheet})


@app.route("/api/character_update", methods=["POST"])
def api_character_update():
    """
    Modifica manuale di una scheda PG dall'editor scheda del frontend
    (parametri, equipaggiamento, incantesimi, slot).
    Body: {"name": "...", "updates": {...}} — merge profondo sulla scheda.
    Ricalcola i modificatori derivati e persiste su personaggi.json.
    """
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    updates = body.get("updates")
    if not name or not isinstance(updates, dict):
        return jsonify({"error": "Campi 'name' e 'updates' richiesti"}), 400

    with _state_lock:
        player = state_mod.find_player(game_state, name)
        if player is None:
            return jsonify({"error": f"Personaggio non trovato: {name}"}), 404
        sheet = player.setdefault("sheet", {})
        parser._deep_merge(sheet, updates)
        # Stessa pipeline di apply_char_updates: sincronizza i *_base con i
        # valori top-level cambiati, applica eventuale level-up automatico
        # da XP, poi ricalcola i derivati (mod, CD, bonus oggetti).
        char_mod.sync_bases_from_update(sheet, updates)
        char_mod.apply_level_progression(sheet)
        char_mod.recompute_derived(sheet)
        state_mod.upsert_character(dict(sheet))
        game_state["characters_loaded"] = True
        state_mod.save_state(game_state)

    return jsonify({"status": "ok", "sheet": sheet})


@app.route("/api/spells_catalog", methods=["GET"])
def api_spells_catalog():
    """Catalogo incantesimi SRD raggruppati per livello.

    Query param opzionale `class=Mago|Chierico|...` filtra alla lista di
    classe (CLASS_SPELL_LIST + CLASS_CANTRIPS). Senza filtro restituisce
    l'intero catalogo. Il campo `kind` indica il tipo di caster della
    classe richiesta (wizard|preparation|known|none) — il frontend lo usa
    per decidere se mostrare libro+preparati (Mago), solo preparati
    (Chierico/Druido/Paladino) o solo conosciuti (Bardo/Stregone/Warlock/Ranger).
    """
    cls = (request.args.get("class") or "").strip()
    if cls:
        entries = spells_mod.class_spells(cls)
    else:
        entries = [
            {"name": n, "level": int(info["level"]), "desc": info.get("desc", "")}
            for n, info in spells_mod.SPELLS.items()
        ]
    by_level: dict[int, list[dict]] = {}
    spells_dict: dict = {}
    for e in entries:
        lv = int(e["level"])
        by_level.setdefault(lv, []).append(e)
        spells_dict[e["name"]] = {"level": lv, "desc": e.get("desc", "")}
    for lv in by_level:
        by_level[lv].sort(key=lambda s: s["name"])
    return jsonify({
        "spells":   spells_dict,
        "by_level": by_level,
        "kind":     spells_mod.caster_kind(cls) if cls else "none",
        "class":    cls or None,
    })


@app.route("/api/prepare_spell", methods=["POST"])
def api_prepare_spell():
    """Gestione preparazione incantesimi dal libro/catalogo alla lista
    «known» (preparati, lanciabili negli slot del giorno).

    Body:
      {"character":"NomePG", "spell":"Nome incantesimo",
       "action": "prepare"|"unprepare"|"learn"|"forget"}

    Azioni:
      • prepare   — porta l'incantesimo (deve essere nel libro per il Mago)
                    in `spells.known`. Per Chierico/Druido prende dal
                    catalogo SRD direttamente.
      • unprepare — rimuove l'incantesimo da `spells.known`.
      • learn     — Mago: aggiunge al `spells.spellbook` (libro), partendo
                    dal catalogo SRD se non già presente.
      • forget    — Mago: rimuove l'incantesimo dal `spellbook` (e anche
                    da `known` se preparato).
    """
    body = request.get_json(silent=True) or {}
    name = (body.get("character") or "").strip()
    spell = (body.get("spell") or "").strip()
    action = (body.get("action") or "").strip().lower()
    if not name or not spell:
        return jsonify({"error": "Campi 'character' e 'spell' richiesti"}), 400
    if action not in ("prepare", "unprepare", "learn", "forget"):
        return jsonify({"error": "action: prepare|unprepare|learn|forget"}), 400

    with _state_lock:
        player = state_mod.find_player(game_state, name)
        if player is None:
            return jsonify({"error": f"Personaggio non trovato: {name}"}), 404
        sheet = player.setdefault("sheet", {})
        spells = sheet.setdefault("spells", {})
        known = spells.setdefault("known", [])
        spellbook = spells.setdefault("spellbook", [])
        cantrips = spells.setdefault("cantrips", [])
        if not isinstance(known, list): known = []
        if not isinstance(spellbook, list): spellbook = []
        if not isinstance(cantrips, list): cantrips = []

        catalog_entry = spells_mod.SPELLS.get(spell)
        entry = {
            "name":  spell,
            "level": int(catalog_entry["level"]) if catalog_entry else 1,
            "desc":  (catalog_entry or {}).get("desc", ""),
        }
        # I trucchetti (livello 0) vanno nella lista `cantrips`, non in
        # `known` o `spellbook`: sono sempre a volontà. Gestiamo come caso
        # speciale prima di entrare nei rami per classe.
        is_cantrip = entry["level"] == 0

        def _has(lst, nm):
            n = nm.lower()
            return any((s.get("name") if isinstance(s, dict) else str(s)).lower() == n
                       for s in lst)

        def _drop(lst, nm):
            n = nm.lower()
            return [s for s in lst
                    if (s.get("name") if isinstance(s, dict) else str(s)).lower() != n]

        cls = sheet.get("class") or ""
        kind = spells_mod.caster_kind(cls)

        # Trucchetti: il livello-0 si gestisce sempre nella lista cantrips,
        # indipendentemente dal tipo di caster. learn/prepare → aggiunge,
        # forget/unprepare → rimuove.
        if is_cantrip:
            if action in ("learn", "prepare"):
                if not _has(cantrips, spell):
                    cantrips.append(entry)
            elif action in ("forget", "unprepare"):
                cantrips = _drop(cantrips, spell)
        elif action == "learn":
            # wizard: aggiunge al libro; preparation/known: aggiunge a known
            if kind == "wizard":
                if not _has(spellbook, spell):
                    spellbook.append(entry)
            else:
                if not _has(known, spell):
                    known.append(entry)
        elif action == "forget":
            # wizard: rimuove dal libro (e dai preparati se preparato).
            # known/preparation: rimuove dai conosciuti/preparati.
            spellbook = _drop(spellbook, spell)
            known = _drop(known, spell)
        elif action == "prepare":
            # wizard: deve essere nel libro per prepararlo
            if kind == "wizard" and not _has(spellbook, spell):
                return jsonify({"error":
                    f"«{spell}» non è nel libro: prima impara l'incantesimo."}), 400
            if not _has(known, spell):
                known.append(entry)
        elif action == "unprepare":
            known = _drop(known, spell)

        spells["known"] = known
        spells["spellbook"] = spellbook
        spells["cantrips"] = cantrips
        char_mod.recompute_derived(sheet)
        state_mod.upsert_character(dict(sheet))
        state_mod.save_state(game_state)

    return jsonify({"status": "ok", "sheet": sheet})


@app.route("/api/cast_spell", methods=["POST"])
def api_cast_spell():
    """
    Consumo / ripristino degli slot incantesimo di un PG.
    Body:
      {"character": "Nome", "level": N}   → lancia un incantesimo di livello N
                                             (scala di 1 lo slot disponibile)
      {"character": "Nome", "rest": true} → riposo lungo: HP al massimo,
                                             death-saves azzerati e tutti
                                             gli slot usati ripristinati.
    I trucchetti (livello 0) sono a volontà e NON consumano slot.
    """
    body = request.get_json(silent=True) or {}
    name = (body.get("character") or "").strip()
    if not name:
        return jsonify({"error": "Campo 'character' richiesto"}), 400

    with _state_lock:
        player = state_mod.find_player(game_state, name)
        if player is None:
            return jsonify({"error": f"Personaggio non trovato: {name}"}), 404
        sheet = player.setdefault("sheet", {})

        if body.get("rest"):
            # Riposo lungo per il singolo PG: HP pieni, death-saves azzerati,
            # tutti gli slot incantesimo a 0 used. Permesso anche per non
            # incantatori (utile per ripristinare gli HP a fine sessione).
            hp = sheet.setdefault("hp", {})
            if isinstance(hp, dict):
                try:
                    mx_int = int(hp.get("max"))
                except (TypeError, ValueError):
                    mx_int = None
                if mx_int is not None and mx_int > 0:
                    hp["current"] = mx_int
                hp["temp"] = 0
                for k in ("_last_msg_id", "_last_damage", "_last_heal",
                          "_last_after"):
                    hp.pop(k, None)
            ds = sheet.get("death_saves")
            if isinstance(ds, dict) and not ds.get("dead"):
                ds["successes"] = 0
                ds["failures"] = 0
            if sheet.get("status") != "dead":
                sheet["status"] = "alive"
            spells = sheet.get("spells")
            if isinstance(spells, dict):
                slots = spells.setdefault("slots", {})
                for sl in slots.values():
                    if isinstance(sl, dict):
                        sl["used"] = 0
            state_mod.upsert_character(dict(sheet))
            state_mod.save_character_file(sheet)
            state_mod.save_state(game_state)
            return jsonify({"status": "ok", "sheet": sheet})

        spells = sheet.get("spells")
        if not isinstance(spells, dict):
            return jsonify({"error": "Il personaggio non è un incantatore"}), 400
        slots = spells.setdefault("slots", {})
        try:
            level = int(body.get("level"))
        except (TypeError, ValueError):
            return jsonify({"error": "Campo 'level' non valido"}), 400
        if level <= 0:
            return jsonify({"error": "I trucchetti non consumano slot"}), 400
        sl = slots.get(str(level))
        if not isinstance(sl, dict):
            return jsonify({"error": f"Nessuno slot di livello {level}"}), 400
        mx = max(0, int(sl.get("max", 0) or 0))
        used = max(0, int(sl.get("used", 0) or 0))
        if used >= mx:
            return jsonify({"error": f"Slot di livello {level} esauriti"}), 409
        sl["used"] = used + 1

        state_mod.upsert_character(dict(sheet))
        state_mod.save_state(game_state)

    return jsonify({"status": "ok", "sheet": sheet})


# ────────────────────────────────────────────────────────────────────────
# Dadi
# ────────────────────────────────────────────────────────────────────────

@app.route("/api/roll", methods=["POST"])
def api_roll():
    body = request.get_json(silent=True) or {}
    dice = body.get("dice", "1d20")
    try:
        r = rules.roll(
            dice,
            advantage=bool(body.get("advantage")),
            disadvantage=bool(body.get("disadvantage")),
            reason=body.get("reason", ""),
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    result = r.to_dict()
    by = body.get("by")
    with _state_lock:
        state_mod.add_roll(game_state, {**result, "by": by})
        # il giocatore ha lanciato: rimuovi il tiro in attesa corrispondente
        pend = game_state.get("pending_rolls") or []
        if pend and by:
            game_state["pending_rolls"] = [
                p for p in pend
                if (p.get("by") or "").strip().lower() != by.strip().lower()
            ]
        state_mod.save_state(game_state)
    return jsonify({"roll": result, "pretty": r.pretty()})


# ────────────────────────────────────────────────────────────────────────
# Salva / carica
# ────────────────────────────────────────────────────────────────────────

@app.route("/api/new_game", methods=["POST"])
def api_new_game():
    """Nuova avventura: azzera storico, mappa e stato.
    I personaggi già creati vengono MANTENUTI (keep_characters, default True):
    si riparte direttamente con i PG esistenti senza rigenerarli."""
    global game_state, conversation_history
    body = request.get_json(silent=True) or {}
    keep_chars = body.get("keep_characters", True)
    with _state_lock:
        # CANCELLA la vecchia avventura PRIMA di azzerare lo stato: cattura
        # il path attivo (avventura generata con nome univoco oppure
        # avventura.txt caricata a mano) e rimuovi entrambi i file dal disco,
        # così la nuova partita non eredita né l'avventura né i dialoghi.
        old_paths = {
            state_mod.current_adventure_path(game_state),
            state_mod.ADVENTURE_FILE,
        }
        game_state = state_mod.empty_state()
        conversation_history = []
        for p in old_paths:
            try:
                if p and os.path.exists(p):
                    os.remove(p)
            except OSError as e:
                print(f"[NEW_GAME] impossibile cancellare {p}: {e}", flush=True)
        # riusa i personaggi salvati su personaggi.json
        if keep_chars:
            chars_data = state_mod.load_characters()
            sheets = (chars_data or {}).get("characters") or []
            if sheets:
                game_state["players"] = [
                    {"name": s.get("name"),
                     "type": s.get("player_type", "human"),
                     "sheet": s}
                    for s in sheets
                ]
                game_state["characters_loaded"] = True
        state_mod.save_state(game_state)
        state_mod.save_conversation(conversation_history)

    # Ri-allinea il DM alla partita nuova: senza un nuovo briefing
    # resterebbe sul contesto della partita precedente (ora riceve solo
    # i messaggi del giocatore). force=True: rimpiazza il briefing vecchio.
    if webchat.is_open():
        briefing = _build_startup_briefing()
        if briefing:
            threading.Thread(
                target=webchat.send_briefing,
                args=(briefing,),
                kwargs={"force": True},
                daemon=True,
            ).start()

    return jsonify({"status": "ok", "state": game_state})


@app.route("/api/generate_adventure", methods=["POST"])
def api_generate_adventure():
    """Chiede al DM di GENERARE una avventura TXT su misura del party
    corrente, la salva su avventura.txt, la spezza in scene e riallinea
    il DM perché la faccia giocare. Operazione lunga (~30-90s)."""
    if not webchat.is_open():
        return jsonify({"error": "DM non collegato. Aprilo dal Setup."}), 409

    chars_data = state_mod.load_characters()
    chars_list = (chars_data or {}).get("characters") if chars_data else None
    if not chars_list:
        return jsonify({"error": "Crea i personaggi prima di generare un'avventura."}), 400

    # Richiesta di generazione: includi SYSTEM_PROMPT + PG se il DM non è
    # ancora briefato, altrimenti basta la richiesta (ha già il contesto).
    request_body = prompt_mod.build_adventure_generation_request(
        state=game_state, characters=chars_list)
    briefed = webchat.is_briefed()
    if briefed:
        full_prompt = request_body
    else:
        full_prompt = (
            prompt_mod.build_full_prompt(
                state=game_state, characters=chars_list, adventure_text=None)
            + "\n\n" + request_body
        )

    try:
        raw = webchat.send(full_prompt, timeout=300)
    except Exception as e:
        return jsonify({"error": f"Generazione fallita: {e}"}), 500
    if not briefed:
        webchat.mark_briefed()

    # Pulisci la risposta: togli reasoning, tag di gioco e rumore.
    text = parser.clean_text(raw or "")
    text = parser.strip_tags(text).strip()
    if not text or len(text) < 400:
        return jsonify({"error": "Il DM non ha restituito un'avventura valida."}), 502

    # Titolo: prima riga "TITOLO DELL'AVVENTURA" o prima riga maiuscola
    title = ""
    for line in text.splitlines():
        s = line.strip().strip("=").strip("-").strip()
        if s and not s.startswith("====") and not s.startswith("----"):
            if any(c.isalpha() for c in s) and s == s.upper():
                title = s.title()
                break
    if not title:
        title = "Avventura generata"

    beats = state_mod.split_into_beats(text)
    if not beats:
        return jsonify({"error": "Avventura generata non spezzabile in scene."}), 502

    # Salva con un NOME NUOVO e univoco (timestamp + slug del titolo) in
    # runtime/avventure: una "Nuova avventura" non sovrascrive le vecchie.
    adv_path = state_mod.new_adventure_path(title)
    try:
        with open(adv_path, "w", encoding="utf-8") as f:
            f.write(text)
    except OSError as e:
        return jsonify({"error": f"Salvataggio fallito: {e}"}), 500

    with _state_lock:
        game_state["adventure_loaded"] = True
        game_state["adventure_title"]  = title
        game_state["adventure_file"]   = adv_path
        game_state["adventure_beats"]  = beats
        game_state["adventure_index"]  = 0
        state_mod.save_state(game_state)

    # Re-briefing per attivare la modalità "fai giocare l'avventura
    # precaricata" col documento appena generato.
    briefing = prompt_mod.build_full_prompt(
        state=game_state, characters=chars_list, adventure_text=text)
    briefing += (
        "\n\n═══ AVVIO AVVENTURA APPENA GENERATA ═══\n"
        "L'avventura nel tag <AVVENTURA_PRECARICATA> è quella che TU "
        "stesso hai appena scritto.\n\n"
        "INIZIA SUBITO l'avventura nella tua RISPOSTA A QUESTO BRIEFING "
        "(non aspettare un messaggio del giocatore): "
        "(1) annuncia titolo e premessa in 2-3 righe, "
        "(2) presenta i PG in due righe totali, "
        "(3) emetti la MAPPA della prima scena con MAP_START…MAP_END "
        "(scegli dimensioni adatte all'ambientazione — vedi FASE 3; "
        "party @ alla partenza, mostri della scena con M/g/k/D), "
        "(4) narra la prima scena seguendo FEDELMENTE il documento e "
        "chiedi al primo PG umano \"**[Nome], cosa fai?**\". "
        "Includi un <STATE_UPDATE> con phase=\"adventure\" e active_player "
        "col nome del primo PG umano. "
        "NON dire \"pronto a iniziare\" o simili: questa è già la "
        "prima scena giocabile."
    )

    def _briefing_then_play():
        try:
            raw = webchat.send_briefing(briefing, force=True)
            if not raw:
                return
            _apply_dm_response(raw, user_label="[Avventura generata]")
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"[WEBCHAT] briefing avventura generata fallito: {e}", flush=True)

    threading.Thread(target=_briefing_then_play, daemon=True).start()

    return jsonify({
        "status":  "ok",
        "title":   title,
        "file":    os.path.basename(adv_path),
        "beats":   len(beats),
        "chars":   len(text),
        "preview": text[:400],
    })


@app.route("/api/load_adventure", methods=["POST"])
def api_load_adventure():
    """Carica un'avventura TXT e la prepara per essere giocata passo passo.

    Input accettato:
      - JSON {"text": "...", "title": "..."}
      - multipart/form-data con file 'file' (.txt)
    L'avventura viene salvata su avventura.txt, spezzata in "beat" (scene)
    e indicizzata: a ogni messaggio del giocatore in fase 'adventure' il
    sistema consegna al DM il beat successivo perché lo narri e lo faccia
    giocare ai PG. Quando i beat finiscono il DM prosegue libero."""
    text = ""
    title = ""
    if request.content_type and request.content_type.startswith("multipart/"):
        f = request.files.get("file")
        if f is None:
            return jsonify({"error": "Allega un file .txt nel campo 'file'"}), 400
        try:
            raw = f.read()
            text = raw.decode("utf-8", errors="replace")
        except Exception as e:
            return jsonify({"error": f"Lettura file fallita: {e}"}), 400
        title = (request.form.get("title") or "").strip() or f.filename or ""
    else:
        body = request.get_json(silent=True) or {}
        text = (body.get("text") or "").strip()
        title = (body.get("title") or "").strip()

    if not text.strip():
        return jsonify({"error": "Avventura vuota"}), 400
    if len(text) > 200_000:
        return jsonify({"error": "File troppo grande (max 200KB)"}), 413

    beats = state_mod.split_into_beats(text)
    if not beats:
        return jsonify({"error": "Impossibile spezzare l'avventura in scene"}), 400

    try:
        with open(state_mod.ADVENTURE_FILE, "w", encoding="utf-8") as f:
            f.write(text)
    except OSError as e:
        return jsonify({"error": f"Salvataggio fallito: {e}"}), 500

    with _state_lock:
        game_state["adventure_loaded"] = True
        game_state["adventure_title"]  = title or game_state.get("adventure_title") or "Avventura precaricata"
        game_state["adventure_file"]   = state_mod.ADVENTURE_FILE
        game_state["adventure_beats"]  = beats
        game_state["adventure_index"]  = 0
        state_mod.save_state(game_state)

    # Re-briefing del DM: la chat web ha già il system prompt vecchio (senza
    # l'avventura). Forziamo un nuovo briefing che include il blocco
    # <AVVENTURA_PRECARICATA>: così il DM la legge per intero, salta FASE 3
    # e parte direttamente dalla prima scena della Sezione 1.
    rebriefed = False
    if webchat.is_open():
        chars_data = state_mod.load_characters()
        chars_list = (chars_data or {}).get("characters") if chars_data else None
        briefing = prompt_mod.build_full_prompt(
            state=game_state,
            characters=chars_list,
            adventure_text=text,
        )
        briefing += (
            "\n\n═══ AVVIO AVVENTURA PRECARICATA ═══\n"
            "Hai ricevuto l'avventura scritta nel tag <AVVENTURA_PRECARICATA>. "
            "Leggila interamente PRIMA di rispondere. SALTA FASE 3 (non "
            "rigenerare nulla): la trama, le mappe, i mostri, i tesori e "
            "gli XP sono già scritti lì dentro — usa ESATTAMENTE quelli.\n\n"
            "INIZIA SUBITO l'avventura nella tua RISPOSTA A QUESTO BRIEFING "
            "(non aspettare un messaggio del giocatore): "
            "(1) annuncia titolo e premessa in 2-3 righe, "
            "(2) presenta i PG in due righe totali, "
            "(3) emetti la MAPPA della PRIMA scena della Sezione 1 "
            "(MAP_START…MAP_END) con dimensioni adatte all'ambientazione "
            "descritta (vedi FASE 3 per le taglie tipiche), il party @ "
            "alla partenza e i mostri della scena con M/g/k/D, "
            "(4) narra la prima scena seguendo FEDELMENTE il documento e "
            "chiedi al primo PG umano \"**[Nome], cosa fai?**\". "
            "Includi anche un <STATE_UPDATE> con phase=\"adventure\" e "
            "active_player col nome del primo PG umano. "
            "NON dire \"pronto a iniziare\" o simili: questa è già la "
            "prima scena giocabile, il prossimo messaggio sarà l'azione "
            "del giocatore."
        )

        def _briefing_then_play():
            try:
                raw = webchat.send_briefing(briefing, force=True)
                if not raw:
                    return
                _apply_dm_response(raw, user_label="[Avventura caricata]")
            except Exception as e:
                import traceback
                traceback.print_exc()
                print(f"[WEBCHAT] briefing avventura fallito: {e}", flush=True)

        threading.Thread(target=_briefing_then_play, daemon=True).start()
        rebriefed = True

    return jsonify({
        "status":   "ok",
        "title":    game_state["adventure_title"],
        "beats":    len(beats),
        "index":    0,
        "preview":  beats[0][:240],
        "chars":    len(text),
        "rebriefed": rebriefed,
    })


@app.route("/api/adventure", methods=["GET"])
def api_adventure_status():
    """Stato dell'avventura precaricata: titolo, totale beat, indice
    corrente, anteprima del prossimo beat. Lo usa il frontend per
    mostrare lo stato del tasto 'Carica TXT'."""
    beats = game_state.get("adventure_beats") or []
    idx   = int(game_state.get("adventure_index") or 0)
    nxt   = beats[idx][:240] if 0 <= idx < len(beats) else ""
    return jsonify({
        "loaded":  bool(game_state.get("adventure_loaded")),
        "title":   game_state.get("adventure_title") or "",
        "total":   len(beats),
        "index":   idx,
        "next":    nxt,
        "done":    bool(beats) and idx >= len(beats),
    })


@app.route("/api/adventure", methods=["DELETE"])
def api_adventure_clear():
    """Toglie l'avventura precaricata e la coda di beat: il DM riprende
    in modalità libera."""
    with _state_lock:
        # rimuovi sia il file attivo (generato, nome univoco) sia avventura.txt
        old_paths = {
            state_mod.current_adventure_path(game_state),
            state_mod.ADVENTURE_FILE,
        }
        game_state["adventure_loaded"] = False
        game_state["adventure_title"]  = None
        game_state["adventure_file"]   = None
        game_state["adventure_beats"]  = []
        game_state["adventure_index"]  = 0
        state_mod.save_state(game_state)
    for p in old_paths:
        try:
            if p and os.path.exists(p):
                os.remove(p)
        except OSError:
            pass
    return jsonify({"status": "cleared"})


@app.route("/api/save", methods=["POST"])
def api_save():
    with _state_lock:
        state_mod.save_state(game_state)
        state_mod.save_conversation(conversation_history)
    return jsonify({"status": "saved",
                    "messages": len(conversation_history),
                    "players":  len(game_state.get("players", []))})


@app.route("/api/load", methods=["POST"])
def api_load():
    global game_state, conversation_history
    with _state_lock:
        game_state = state_mod.load_state()
        conversation_history = state_mod.load_conversation()

    # Riallinea il DM: la chat web non conosce la partita appena caricata.
    # Invia SEMPRE un briefing di ripresa (stato + storico). Se la webchat
    # non è ancora aperta la apre prima del briefing — così "Carica" dal
    # frontend ricarica davvero il prompt nel modello anche quando il DM
    # è stato chiuso o ucciso dopo l'avvio.
    chars_data = state_mod.load_characters()
    chars_list = (chars_data or {}).get("characters") if chars_data else None

    adventure_text = None
    _adv_path = state_mod.current_adventure_path(game_state)
    if game_state.get("adventure_loaded") and os.path.exists(_adv_path):
        with open(_adv_path, "r", encoding="utf-8") as f:
            adventure_text = f.read()

    resume = prompt_mod.build_resume_prompt(
        state=game_state,
        characters=chars_list,
        adventure_text=adventure_text,
        conversation=conversation_history,
    )

    dm_was_open = webchat.is_open()

    def _resume_briefing_bg() -> None:
        """Garantisce DM connesso, invia il briefing di ripresa (chunked a
        ~2000 token a blocco) e processa la risposta finale come un normale
        turno DM così il «messaggio di ripartenza» compare nello storico e
        il frontend lo mostra al prossimo polling."""
        print(f"[LOAD] avvio briefing di ripresa "
              f"({len(resume)} char, dm_open={webchat.is_open()})",
              flush=True)
        if not webchat.is_open():
            try:
                print("[LOAD] DM non aperto: apertura Chromium prima del briefing",
                      flush=True)
                webchat.open()
            except Exception as e:
                print(f"[LOAD] apertura DM fallita: {e}", flush=True)
                return
        try:
            response = webchat.send_briefing(resume, force=True)
        except Exception as e:
            print(f"[LOAD] briefing di ripresa fallito: {e}", flush=True)
            return
        if not (response or "").strip():
            print("[LOAD] briefing di ripresa: nessuna risposta dal DM",
                  flush=True)
            return
        try:
            _apply_dm_response(response, user_label="[RipresaPartita]")
            print(f"[LOAD] briefing di ripresa completato: "
                  f"{len(response)} char di risposta", flush=True)
        except Exception as e:
            print(f"[LOAD] _apply_dm_response fallita: {e}", flush=True)

    threading.Thread(target=_resume_briefing_bg, daemon=True).start()

    return jsonify({"status":     "loaded",
                    "state":      game_state,
                    "messages":   len(conversation_history),
                    "resync":     True,
                    "dm_open":    dm_was_open,
                    "dm_opening": not dm_was_open})


# ────────────────────────────────────────────────────────────────────────
# Webchat (DM via Chromium/DeepSeek)
# ────────────────────────────────────────────────────────────────────────

@app.route("/api/webchat/status")
def api_webchat_status():
    return jsonify({
        "open":      webchat.is_open(),
        "briefed":   webchat.is_briefed(),
        "in_flight": webchat.is_briefing_in_flight(),
        "url":       webchat.config.url,
        "name":      webchat.config.name,
        "timeout":   webchat.config.timeout,
        "last":      webchat.last_status,
    })


@app.route("/api/webchat/sync", methods=["POST"])
def api_webchat_sync():
    """Allinea il DM allo stato/storico della partita: gli passa TUTTI i
    messaggi salvati. Lo chiama il frontend SOLO quando il LED del DM è
    verde (chat aperta), così il briefing parte a DM pronto.

    Idempotente: una sola sincronizzazione per volta; se il briefing
    fallisce (es. login necessario) il frontend ritenta al giro dopo."""
    global _briefing_running
    if not webchat.is_open():
        return jsonify({"status": "closed"})
    if webchat.is_briefed():
        return jsonify({"status": "ready"})
    # GATE LOGIN: finché la pagina del modello non mostra il campo chat
    # (utente non ancora loggato dopo un cambio modello) NON inviamo NULLA a
    # Chromium. La richiesta resta sospesa e riparte da sola al prossimo
    # polling, una volta fatto il login.
    if not webchat.login_ready():
        return jsonify({"status": "awaiting_login"})

    with _briefing_lock:
        if _briefing_running:
            return jsonify({"status": "syncing"})
        briefing = _build_startup_briefing()
        if not briefing:
            # nessuno storico né PG: niente da precaricare. Il system
            # prompt verrà inviato col primo messaggio del giocatore.
            return jsonify({"status": "empty"})
        _briefing_running = True

    def _bg():
        global _briefing_running
        try:
            # ATTENDI la risposta del DM al briefing iniziale e SCRIVILA nello
            # storico: è il messaggio di apertura/ripartenza dell'avventura
            # (riepilogo + scena + mappa). Senza questo passaggio la risposta
            # del modello andava persa e il frontend non aveva nulla da
            # mostrare: il giocatore non poteva cominciare a giocare.
            response = webchat.send_briefing(briefing)
            if (response or "").strip():
                try:
                    _apply_dm_response(response, user_label="[Avvio partita]")
                except Exception as e:
                    print(f"[WEBCHAT] sync: applicazione risposta DM fallita: {e}",
                          flush=True)
        except Exception as e:
            print(f"[WEBCHAT] sync briefing fallito: {e}", flush=True)
        finally:
            _briefing_running = False

    threading.Thread(target=_bg, daemon=True).start()
    return jsonify({"status": "syncing"})


@app.route("/api/map/redraw", methods=["POST"])
def api_map_redraw():
    """Chiede al DM di RIDISEGNARE la mappa completa della scena corrente.
    Invia un prompt mirato, estrae il blocco MAP_START…MAP_END, lo applica
    a game_state e rivela TUTTE le tile (mappa "completa" senza fog).
    Lo usa il tasto frontend «Ridisegna mappa»."""
    if not webchat.is_open():
        return jsonify({"error": "DM non collegato"}), 409
    try:
        raw = webchat.send(prompt_mod.build_map_redraw_request(game_state))
    except Exception as e:
        return jsonify({"error": f"Richiesta mappa fallita: {e}"}), 500

    cleaned = parser.clean_text(raw or "")
    map_block = parser.extract_map(cleaned)
    state_upd = parser.extract_state(cleaned)
    if not map_block:
        # Il DM a volte risponde a parole senza il blocco mappa. Riprova UNA
        # volta con una richiesta secca prima di arrendersi.
        try:
            raw2 = webchat.send(
                "Emetti SOLO il blocco mappa della scena corrente, tra "
                "MAP_START e MAP_END, con i caratteri canonici (un solo "
                "carattere per cella, niente numeri né etichette). "
                "Nessun'altra parola."
            )
            cleaned = parser.clean_text(raw2 or "")
            map_block = parser.extract_map(cleaned)
            state_upd = parser.extract_state(cleaned) or state_upd
        except Exception as e:
            print(f"[MAP] retry redraw fallito: {e}", flush=True)
    if not map_block:
        return jsonify({"error": "Il DM non ha emesso un blocco MAP valido"}), 502

    with _state_lock:
        # Sostituzione totale della mappa (stessa pipeline di /api/chat)
        bw, bh = state_mod.measure_map(map_block)
        norm = state_mod.normalize_map(map_block, bw, bh)
        at = state_mod.find_position(norm, "@")
        norm_no_at = norm.replace("@", ".")
        game_state["map_base"]   = norm_no_at
        game_state["map_full"]   = norm_no_at
        game_state["map_width"]  = bw
        game_state["map_height"] = bh
        if at:
            game_state["current_position"] = [at[0], at[1]]
        if state_upd:
            parser.apply_state_update(game_state, state_upd)

        # «Mappa completa»: rivela TUTTE le tile, azzera la fog.
        revealed = []
        for y, line in enumerate(norm_no_at.splitlines()):
            for x, _ in enumerate(line):
                revealed.append([x, y])
        game_state["revealed_tiles"] = revealed

        pos = game_state.get("current_position") or [0, 0]
        game_state["map_ascii"] = state_mod.apply_fog(
            game_state["map_full"], revealed, party_pos=pos)
        state_mod.save_state(game_state)

    return jsonify({
        "status": "ok",
        "width":  game_state.get("map_width"),
        "height": game_state.get("map_height"),
        "pos":    game_state.get("current_position"),
    })


@app.route("/api/music/generate", methods=["POST"])
def api_music_generate():
    """Chiede al DM di COMPORRE una nuova colonna sonora: invia un prompt
    mirato, estrae il tag <MUSIC>, lo applica a game_state['music'] e lo
    restituisce al frontend (che lo passa al motore audio)."""
    if not webchat.is_open():
        return jsonify({"error": "DM non collegato"}), 409
    try:
        raw = webchat.send(prompt_mod.build_music_request(game_state))
    except Exception as e:
        return jsonify({"error": f"Generazione musica fallita: {e}"}), 500
    music_upds = parser.extract_music(parser.clean_text(raw))
    if not music_upds:
        return jsonify({"error": "Il DM non ha generato una colonna sonora valida"}), 502
    with _state_lock:
        parser.apply_music_update(game_state, music_upds)
        state_mod.save_state(game_state)
        music = dict(game_state.get("music", {}))
    return jsonify({"status": "ok", "music": music})


@app.route("/api/webchat/open", methods=["POST"])
def api_webchat_open():
    body = request.get_json(silent=True) or {}
    old_url = (webchat.config.url or "").strip()
    new_url = (body.get("url") or "").strip()
    # Cambio modello: URL diverso da quello attivo. Anche riaprire lo STESSO
    # modello apre una chat nuova senza il contesto della partita, quindi in
    # entrambi i casi il DM va ri-allineato da zero.
    model_changed = bool(new_url) and new_url != old_url
    if new_url:
        webchat.config.url = new_url
    if body.get("name"):
        webchat.config.name = body["name"].strip()
    if body.get("timeout"):
        try:
            webchat.config.timeout = max(15, min(600, int(body["timeout"])))
        except (TypeError, ValueError):
            pass

    # Persisti il modello scelto: al prossimo avvio l'app riapre questo.
    state_mod.save_webchat_config({
        "url":     webchat.config.url,
        "name":    webchat.config.name,
        "timeout": webchat.config.timeout,
    })

    # SOSPENDI la comunicazione finché il nuovo modello non è loggato e
    # ri-briefato: scordando il briefing, is_briefed()→False, il frontend
    # disabilita l'input e il polling /api/webchat/sync ri-allinea il DM
    # appena la chat è pronta (dopo l'eventuale login manuale).
    webchat.reset_briefing()

    try:
        url = webchat.open()
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    if model_changed:
        print(f"[WEBCHAT] modello cambiato: {old_url or '—'} → {new_url}. "
              "Briefing resettato: attendo login + ri-sincronizzazione.",
              flush=True)

    # NB: il briefing iniziale NON parte qui. Lo invia il frontend via
    # /api/webchat/sync appena il LED del DM diventa verde (chat pronta).
    return jsonify({"status": "ok", "url": url, "name": webchat.config.name})


# ────────────────────────────────────────────────────────────────────────
# Chat principale — SSE streaming
# ────────────────────────────────────────────────────────────────────────

@app.route("/api/chat", methods=["POST"])
def api_chat():
    global conversation_history

    body = request.get_json(silent=True) or {}
    if not webchat.is_open():
        return jsonify({"error": "Backend DeepSeek non aperto. Usa /api/webchat/open prima."}), 409
    # GATE LOGIN: se non ancora briefato (es. subito dopo un cambio modello)
    # e la pagina non è loggata, NON inviare a Chromium: attendi il login.
    if not webchat.is_briefed() and not webchat.login_ready():
        return jsonify({"error": "Modello non ancora pronto: completa il "
                        "login nella pagina del modello, poi riprova."}), 409

    # retry=True: RICARICA l'ultimo turno del DM — scarta la sua ultima
    # risposta e la rigenera dall'ultimo messaggio del giocatore.
    retry = bool(body.get("retry"))
    if retry:
        with _state_lock:
            while (conversation_history
                   and conversation_history[-1].get("role") == "assistant"):
                conversation_history.pop()
            last_user = next((m for m in reversed(conversation_history)
                              if m.get("role") == "user"), None)
        if not last_user:
            return jsonify({"error": "Nessun messaggio del DM da ricaricare"}), 400
        user_message = (last_user.get("content") or "").strip()
    else:
        user_message = (body.get("message") or "").strip()
        if not user_message:
            return jsonify({"error": "Messaggio vuoto"}), 400

    def generate():
        global conversation_history
        import queue as _queue
        result_q: _queue.Queue = _queue.Queue()

        # Quanti turni di fila il DM completa da solo (risolvendo i tiri di
        # mostri / PG IA) prima di restituire il controllo ai giocatori.
        MAX_FOLLOWUPS = 6

        # in retry l'ultimo messaggio del giocatore è già nello storico
        if not retry:
            with _state_lock:
                conversation_history.append(
                    {"role": "user", "content": user_message})

        def _format_roll_feedback(results: list[dict]) -> str:
            """Riepilogo dei tiri risolti dal sistema (PG IA / mostri) da
            rimandare al DM perché ne narri l'esito e PROSEGUA la storia."""
            lines = ["[Sistema] Risultati dei tiri richiesti:"]
            for r in results:
                extra = []
                if r.get("reason"):
                    extra.append(str(r["reason"]))
                if r.get("target"):
                    extra.append(f"vs {r['target']}")
                crit = ""
                if r.get("is_crit"):
                    crit = " — CRITICO"
                elif r.get("is_fumble"):
                    crit = " — fallimento critico"
                suffix = f" ({' · '.join(extra)})" if extra else ""
                lines.append(f"- {r.get('by') or '?'}: {r.get('expr') or ''} "
                             f"= {r.get('total')}{crit}{suffix}")
            lines.append(
                "Dichiara l'esito di OGNI tiro, narralo in breve e "
                "PROSEGUI: conseguenze nella scena, reazioni di "
                "nemici/PNG. Poi chiedi agli ALTRI giocatori/PG cosa "
                "fanno (**[Nome], cosa fai?**). Aggiorna lo stato coi tag "
                "(mappa e current_position compresi).")
            return "\n".join(lines)

        def _format_cast_feedback(failed: list[dict]) -> str:
            """Avviso al DM: incantesimi che NON possono essere lanciati
            (slot esauriti o PG non incantatore). Il DM deve rinarrare la
            scena senza di essi."""
            lines = ["[Sistema] INCANTESIMO NON LANCIABILE — slot non disponibili:"]
            for f in failed:
                lines.append(
                    f"- {f.get('by') or '?'}: «{f.get('spell') or '?'}» "
                    f"(liv. {f.get('level')}) — {f.get('detail')}")
            lines.append(
                "Questi incantesimi NON sono stati lanciati e NON hanno "
                "avuto effetto. RINARRA la scena senza di essi: il PG è a "
                "corto di slot e deve agire altrimenti (trucchetto, arma, "
                "altra azione). Aggiorna mappa e stato di conseguenza.")
            return "\n".join(lines)

        def _dm_exchange(message_text: str,
                         with_extras: bool = False) -> tuple[str, list, list]:
            """Un giro col DM: invia `message_text`, risolve i ROLL_REQ,
            applica lo stato. Ritorna (display_text, roll_results IA,
            pending_rolls umani).

            `with_extras=True` appende anche la richiesta di rigenerare la
            colonna sonora e gli sprite pixel-art (cadenza ogni 3 messaggi
            del giocatore)."""
            # promemoria appesi a OGNI messaggio: mappa sempre; musica e
            # sprite ogni 3 messaggi.
            reminders = prompt_mod.map_reminder(game_state)
            if with_extras:
                reminders += "\n\n" + prompt_mod.music_reminder()
                reminders += "\n\n" + prompt_mod.sprite_reminder()
            # promemoria combat 5.5e: solo se combat_active. Tiene il DM
            # allineato a economia azioni, crit, concentrazione,
            # opportunità e increment di round/turn.
            combat_rem = prompt_mod.combat_reminder(game_state)
            if combat_rem:
                reminders += "\n\n" + combat_rem
            # slot incantesimo residui: appesi a OGNI messaggio così il DM
            # sa cosa ogni PG può ancora lanciare (e non spende slot esauriti)
            slot_rem = prompt_mod.spell_slot_reminder(game_state)
            if slot_rem:
                reminders += "\n\n" + slot_rem

            briefed = webchat.is_briefed()
            if briefed:
                # DM già allineato: gli arriva SOLO il nuovo messaggio +
                # i promemoria (mappa coerente col contesto, musica).
                full_prompt = f"{message_text}\n\n{reminders}\n\nDM:"
            else:
                # prima volta: contesto completo + TUTTO lo storico
                chars_data = state_mod.load_characters()
                chars_list = (chars_data or {}).get("characters") if chars_data else None
                adventure_text = None
                _adv_path = state_mod.current_adventure_path(game_state)
                if game_state.get("adventure_loaded") and os.path.exists(_adv_path):
                    with open(_adv_path, "r", encoding="utf-8") as f:
                        adventure_text = f.read()
                system = prompt_mod.build_full_prompt(
                    state=game_state,
                    characters=chars_list,
                    adventure_text=adventure_text,
                )
                conv_text = prompt_mod.conversation_to_text(
                    conversation_history[:-1], limit=None)
                full_prompt = (
                    f"{system}\n\n"
                    f"═══ STORICO PARTITA ═══\n{conv_text}\n\n"
                    f"═══ NUOVO MESSAGGIO ═══\n{message_text}\n\n"
                    f"{reminders}\n\nDM:"
                )

            ts = datetime.now().strftime("%H:%M:%S")
            print(f"\n{'═'*70}\n[CHAT {ts}] backend=webchat | "
                  f"briefed={briefed} | prompt={len(full_prompt)} chars", flush=True)
            print(f"[IN] {message_text[:200]}", flush=True)

            raw = webchat.send(full_prompt)
            if not briefed:
                webchat.mark_briefed()
            cleaned = parser.clean_text(raw)

            # ID univoco di questa risposta DM: serve a parser HP per
            # deduplicare gli update di damage/heal/current. Multipli
            # CHAR_UPDATE per lo stesso PG nello stesso messaggio applicano
            # HP una sola volta.
            dm_msg_id = hashlib.md5(
                (raw or "").encode("utf-8", errors="replace")
            ).hexdigest()[:16]

            # Risolvi ROLL_REQ: i tiri dei PG UMANI restano in attesa (li
            # lancia il giocatore dal riquadro dadi); gli altri li tira il
            # sistema e finiscono in roll_results.
            human_names = [
                p.get("name")
                for p in game_state.get("players", [])
                if p.get("type") == "human"
            ]
            with_rolls, roll_results, pending_rolls = parser.resolve_roll_requests(
                cleaned, human_names
            )

            state_upd   = parser.extract_state(with_rolls)
            char_upds   = parser.extract_chars(with_rolls)
            map_block   = parser.extract_map(with_rolls)
            music_upds  = parser.extract_music(with_rolls)
            sprite_upds = parser.extract_sprites(with_rolls)
            cast_upds   = parser.extract_spell_casts(with_rolls)

            map_report = None
            cast_report: list[dict] = []
            long_rest_names: list[str] = []
            with _state_lock:
                # Snapshot della scena PRIMA dello state_update così
                # possiamo rilevare il passaggio a una nuova scena (cambio
                # di "current_scene") e forzare la rigenerazione mappa.
                prev_scene = (game_state.get("current_scene") or "").strip()
                # Flag di servizio: il DM segnala riposo lungo o fine sessione
                # in STATE_UPDATE. Vengono estratti PRIMA dell'apply per non
                # finire nel game_state, e processati DOPO i CHAR_UPDATE così
                # un eventuale aggiornamento di hp.max (level-up nel riposo)
                # è già stato applicato prima del ripristino HP.
                long_rest_flag = False
                if isinstance(state_upd, dict):
                    if state_upd.pop("long_rest", False):
                        long_rest_flag = True
                    if state_upd.pop("session_end", False):
                        long_rest_flag = True
                if state_upd:
                    parser.apply_state_update(game_state, state_upd,
                                              message_id=dm_msg_id)
                if char_upds:
                    parser.apply_char_updates(game_state, char_upds,
                                              message_id=dm_msg_id)
                    for p in game_state.get("players", []):
                        char_mod.upgrade_sheet(p.get("sheet") or {})
                if long_rest_flag:
                    long_rest_names = parser.apply_long_rest(
                        game_state,
                        on_persist=lambda s: (
                            state_mod.upsert_character(s),
                            state_mod.save_character_file(s),
                        ),
                    )
                    if long_rest_names:
                        game_state.setdefault("pending_dm_notes", []).append(
                            "[Sistema] RIPOSO LUNGO applicato: HP ripristinati "
                            "al massimo e slot incantesimo azzerati per "
                            + ", ".join(long_rest_names) + "."
                        )
                # ── INCANTESIMI: scala gli slot dei lanci dichiarati dal DM.
                # Va DOPO i CHAR_UPDATE (che potrebbero rigenerare gli slot).
                cast_report = parser.apply_spell_casts(game_state, cast_upds)
                cast_fail = [c for c in cast_report if not c.get("ok")]
                if cast_fail:
                    game_state.setdefault("pending_dm_notes", []).append(
                        _format_cast_feedback(cast_fail))
                # ── MAPPA: skeleton immutabile (muri) + livello dinamico
                # (mostri, PG abbattuti, forzieri, party). Alla prima
                # generazione (o retry) si fissa la BASE — solo lo
                # scheletro di muri non cambia mai. Ogni mappa successiva
                # viene COMPOSTA sopra la base: il DM aggiorna mostri
                # (M/g/k/D), cadaveri (P), tesori saccheggiati ($→.),
                # decorazioni, e così la mappa riflette la situazione
                # reale di gioco a ogni turno. Il marker @ va in
                # current_position e apply_fog lo ridisegna sopra.
                new_scene = (game_state.get("current_scene") or "").strip()
                scene_changed = bool(new_scene) and new_scene != prev_scene
                if map_block:
                    # SOSTITUZIONE TOTALE: ogni mappa del DM rimpiazza
                    # quella precedente, con le dimensioni esatte che il
                    # DM ha disegnato. Niente skeleton immutabile: se la
                    # scena cambia (nuova sezione, allargamento, mappa
                    # più piccola) la mappa segue.
                    bw, bh = state_mod.measure_map(map_block)
                    norm = state_mod.normalize_map(map_block, bw, bh)
                    at = state_mod.find_position(norm, "@")
                    norm_no_at = norm.replace("@", ".")
                    prev_w = game_state.get("map_width") or 0
                    prev_h = game_state.get("map_height") or 0
                    prev_full = game_state.get("map_full") or ""
                    if (bw, bh) != (prev_w, prev_h) or norm_no_at != prev_full or scene_changed:
                        # cambia dimensione, layout O scena → fog azzerata
                        # (nuova scena: vecchie tile rivelate non valgono più)
                        game_state["revealed_tiles"] = []
                    game_state["map_base"]   = norm_no_at
                    game_state["map_full"]   = norm_no_at
                    game_state["map_width"]  = bw
                    game_state["map_height"] = bh
                    if at:
                        # accetta sempre la posizione del @ disegnato dal DM
                        game_state["current_position"] = [at[0], at[1]]
                elif scene_changed:
                    # Cambio scena dichiarato dal DM ma nessuna mappa
                    # emessa: NON azzeriamo quella vecchia (lasciare il
                    # pannello vuoto = "mappa non disegnata"). La teniamo a
                    # video finché arriva la nuova e SOLLECITIAMO il redraw
                    # al prossimo turno. Meglio una mappa un turno vecchia
                    # che nessuna mappa.
                    game_state.setdefault("pending_dm_notes", []).append(
                        f"[Sistema] NUOVA SCENA «{new_scene}» — emetti "
                        "SUBITO una MAPPA COMPLETAMENTE NUOVA "
                        "(MAP_START…MAP_END) coerente con questa scena: "
                        "nuove dimensioni adatte al luogo, nuovo layout "
                        "di muri/decorazioni, tile coerenti "
                        "coll'ambientazione, mostri/PNG/tesori della "
                        "scena. NON riusare la mappa precedente."
                    )
                    print(f"[SCENE] cambio «{prev_scene}» → «{new_scene}» "
                          "senza mappa: mappa precedente mantenuta, redraw "
                          "richiesto al prossimo turno", flush=True)
                # Se NON arriva una mappa nuova vale la posizione aggiornata
                # dallo STATE_UPDATE: apply_fog ridisegna il @ lì, quindi la
                # mappa si aggiorna comunque a ogni messaggio.
                pos = game_state.get("current_position") or [0, 0]
                if game_state.get("map_full"):
                    # Verifica coerenza pos↔mappa PRIMA della fog: se
                    # current_position cade sopra un muro o non corrisponde
                    # al marker @ disegnato dal DM, accetta la posizione
                    # suggerita (il @ della mappa) — altrimenti l'icona party
                    # finirebbe ridisegnata sopra un muro.
                    map_report = state_mod.check_map_coherence(
                        game_state["map_full"], pos)
                    sug = map_report.get("suggested_position")
                    if sug and list(sug) != list(pos):
                        pos = [sug[0], sug[1]]
                        game_state["current_position"] = pos
                    if not map_report["ok"]:
                        print(f"[MAP] incoerenze: {map_report['issues']}", flush=True)
                # Illuminazione a torcia con LINE-OF-SIGHT: la stanza
                # chiusa illuminata mostra TUTTO il suo interno (perimetro
                # incluso) senza nebbia residua; un corridoio si rivela
                # solo fin dove l'occhio arriva; gli spazi aperti restano
                # limitati dal raggio della torcia.
                if game_state.get("map_full"):
                    state_mod.reveal_los(game_state, game_state["map_full"],
                                         pos[0], pos[1], radius=10)
                else:
                    state_mod.reveal_around(game_state, pos[0], pos[1], radius=2)
                if game_state.get("map_full"):
                    game_state["map_ascii"] = state_mod.apply_fog(
                        game_state["map_full"],
                        game_state.get("revealed_tiles", []),
                        party_pos=pos,
                    )
                for r in roll_results:
                    state_mod.add_roll(game_state, r)
                if music_upds:
                    parser.apply_music_update(game_state, music_upds)
                if sprite_upds:
                    parser.apply_sprites(game_state, sprite_upds)
                # Persisti le schede su personaggi.json: XP, HP, livello e
                # ogni modifica del DM restano allineati anche nelle schede
                # personaggio (non solo nello stato di gioco).
                state_mod.sync_characters_from_players(game_state.get("players", []))
                game_state["pending_rolls"] = pending_rolls
                state_mod.save_state(game_state)

            display_text = parser.strip_narrative(with_rolls)

            with _state_lock:
                _debug["exchanges"].append({
                    "ts":      datetime.now().isoformat(timespec="seconds"),
                    "user":    message_text,
                    "raw":     raw[:2000],
                    "shown":   display_text[:2000],
                    "rolls":   roll_results,
                    "map":     map_report,
                    "sprites": sorted(sprite_upds.keys()),
                    "casts":   cast_report,
                })
                if len(_debug["exchanges"]) > 20:
                    _debug["exchanges"].pop(0)

            print(f"[DM] {len(display_text)} chars · {len(roll_results)} tiri IA "
                  f"· {len(pending_rolls)} tiri attesi · {len(sprite_upds)} sprite "
                  f"· {len(cast_report)} incantesimi", flush=True)
            print(f"{'═'*70}\n", flush=True)
            return display_text, roll_results, pending_rolls

        def _work():
            global _msg_count
            try:
                # Tiri di mostri/PG IA risolti in un turno precedente ma
                # non ancora narrati: consegnali ORA col nuovo messaggio.
                with _state_lock:
                    feedback = game_state.get("pending_roll_feedback") or []
                    game_state["pending_roll_feedback"] = []
                    # note di sistema in sospeso (es. slot incantesimo
                    # esauriti rilevati in un turno precedente)
                    start_notes = game_state.get("pending_dm_notes") or []
                    game_state["pending_dm_notes"] = []
                first_msg = f"Giocatore: {user_message}"
                if retry:
                    first_msg += (
                        "\n\n[Sistema: RIGENERA la tua ultima risposta — "
                        "non è arrivata correttamente. Riemetti per intero "
                        "la scena, la mappa MAP_START…MAP_END (stesse "
                        "dimensioni della mappa corrente) e il "
                        "<STATE_UPDATE>.]")
                prefix = list(start_notes)
                if feedback:
                    prefix.append(_format_roll_feedback(feedback))

                # ── Avventura precaricata passo passo: consegna il
                # prossimo beat al DM una volta per messaggio del giocatore
                # (non sui retry: si rigiocherebbe la stessa scena).
                if not retry:
                    with _state_lock:
                        beats = game_state.get("adventure_beats") or []
                        idx   = int(game_state.get("adventure_index") or 0)
                        beat = beats[idx] if 0 <= idx < len(beats) else None
                        if beat is not None:
                            game_state["adventure_index"] = idx + 1
                            state_mod.save_state(game_state)
                    if beat:
                        total = len(beats)
                        more = " (ultimo beat: dopo questa scena prosegui libero seguendo la trama già vista)" if idx + 1 >= total else ""
                        prefix.append(
                            f"[Sistema] AVVENTURA PRECARICATA — beat "
                            f"{idx + 1}/{total}. Narra la scena seguente "
                            f"passo passo, una scena per messaggio, "
                            f"adattandola al party e alla situazione "
                            f"corrente. Non saltare passaggi e non "
                            f"riassumere: vivila come scena giocabile. "
                            f"Quando il party ha agito FERMATI e attendi "
                            f"il prossimo messaggio del giocatore prima "
                            f"di passare alla scena successiva.{more}\n\n"
                            f"--- SCENA ---\n{beat}\n--- /SCENA ---"
                        )

                if prefix:
                    first_msg = "\n\n".join(prefix) + "\n\n" + first_msg

                # cadenza extra (musica + sprite): ogni EXTRAS_EVERY
                # messaggi del giocatore (il retry non conta).
                want_extras = False
                if not retry:
                    with _state_lock:
                        _msg_count += 1
                        want_extras = (_msg_count % EXTRAS_EVERY == 0)

                display, rolls, pending = _dm_exchange(
                    first_msg, with_extras=want_extras)
                if display.strip():
                    result_q.put(("token", display))

                # Dopo un <ROLL_REQ> di un PG IA / mostro il sistema tira
                # SUBITO. Rimanda il risultato al DM perché DICHIARI
                # l'esito, lo NARRI e PROSEGUA (poi chieda agli altri PG
                # cosa fanno): così il DM completa il suo turno invece di
                # fermarsi appeso al tiro. Stesso meccanismo per le note di
                # sistema (es. slot incantesimo esauriti: il DM rinarra).
                # Il ciclo si ferma:
                #   • appena si attende un tiro UMANO (riquadro dadi);
                #   • appena `active_player` diventa un PG UMANO (anche
                #     senza tiri pendenti: la narrazione gli ha appena
                #     passato il turno, deve poter agire prima che il DM
                #     produca altre bolle);
                #   • dopo MAX_FOLLOWUPS turni come safety cap.
                def _active_is_human() -> bool:
                    ap = (game_state.get("active_player") or "").strip().lower()
                    if not ap:
                        return False
                    for p in game_state.get("players", []):
                        if (p.get("name") or "").strip().lower() == ap:
                            return p.get("type") == "human"
                    return False

                def _text_passes_turn_to_human(text: str) -> bool:
                    """Safety net: se il DM passa il turno a un PG umano
                    nel testo (es. «**Efi, cosa fai?**») ma scorda di
                    aggiornare active_player o di emettere ROLL_REQ, il
                    loop seguiterebbe a girare. Qui rileviamo il pattern
                    «cosa fai» + nome di un PG umano nel testo per
                    fermare comunque il loop e restituire il turno al
                    giocatore."""
                    if not (text or "").strip():
                        return False
                    low = text.lower()
                    if "cosa fai" not in low and "tocca a te" not in low:
                        return False
                    human_names = [
                        (p.get("name") or "").strip().lower()
                        for p in game_state.get("players", [])
                        if p.get("type") == "human"
                    ]
                    return any(n and n in low for n in human_names)

                steps = 0
                while steps < MAX_FOLLOWUPS:
                    with _state_lock:
                        notes = list(game_state.get("pending_dm_notes") or [])
                        active_human = _active_is_human()
                    # PG umano di turno: STOP. Il DM ha già passato la
                    # parola al giocatore — eventuali tiri IA non ancora
                    # narrati restano in coda per il prossimo messaggio.
                    if active_human:
                        break
                    # Safety net: il testo del DM passa esplicitamente la
                    # parola a un umano (anche senza ROLL_REQ e senza
                    # aggiornare active_player). Fermati comunque: il
                    # giocatore deve poter rispondere prima di nuove bolle.
                    if _text_passes_turn_to_human(display):
                        print("[COMBAT] DM ha passato turno a umano via "
                              "testo senza active_player → break loop",
                              flush=True)
                        break
                    # Tiri umani pending: il giocatore deve lanciare prima
                    # di proseguire. Doppia sicurezza oltre al need_roll_fb.
                    if pending:
                        break
                    need_roll_fb = bool(rolls and not pending)
                    use_notes = bool(notes and not pending)
                    if not need_roll_fb and not use_notes:
                        break
                    steps += 1
                    if use_notes:
                        with _state_lock:
                            game_state["pending_dm_notes"] = []
                    parts = []
                    if use_notes:
                        parts.extend(notes)
                    if need_roll_fb:
                        parts.append(_format_roll_feedback(rolls))
                    display, rolls, pending = _dm_exchange("\n\n".join(parts))
                    if display.strip():
                        result_q.put(("token", display))

                # Tiri IA risolti ma non ancora narrati (si attende un tiro
                # umano, turno umano attivo, o limite turni): consegnali col
                # prossimo messaggio.
                if rolls:
                    with _state_lock:
                        game_state.setdefault(
                            "pending_roll_feedback", []).extend(rolls)
                        state_mod.save_state(game_state)

                result_q.put(("done", None))
            except Exception as e:
                import traceback
                traceback.print_exc()
                result_q.put(("error", str(e)))

        t = threading.Thread(target=_work, daemon=True)
        t.start()

        # Consuma i turni del DM man mano che vengono completati: ogni
        # turno = un token separato (bolla in chat), con keepalive ogni 3s
        # mentre il DM genera. La coda TTS li legge uno alla volta.
        sent_any = False
        while True:
            try:
                kind, payload = result_q.get(timeout=3)
            except _queue.Empty:
                if t.is_alive():
                    yield ": keepalive\n\n"
                    continue
                break  # thread terminato senza esito esplicito

            if kind == "token":
                sent_any = True
                with _state_lock:
                    conversation_history.append(
                        {"role": "assistant", "content": payload})
                    state_mod.save_conversation(conversation_history)
                    # serializza lo stato mentre il lock è preso: il worker
                    # non può mutarlo a metà di json.dumps
                    event = json.dumps({"token": payload, "state": game_state})
                yield f"data: {event}\n\n"
            elif kind == "error":
                # In retry l'ultimo messaggio user è quello ORIGINALE da
                # rigenerare (non appeso in questo giro): NON va rimosso,
                # altrimenti un retry fallito cancella la storia legittima.
                if not sent_any and not retry:
                    with _state_lock:
                        if conversation_history and conversation_history[-1]["role"] == "user":
                            conversation_history.pop()
                yield f"data: {json.dumps({'error': payload})}\n\n"
                return
            elif kind == "done":
                # Trim della storia una sola volta a fine turno (non per ogni
                # token): un turno con N follow-up del DM non deve far cadere
                # N messaggi di contesto utente in una volta sola.
                with _state_lock:
                    if len(conversation_history) > 40:
                        conversation_history[:] = conversation_history[-40:]
                        state_mod.save_conversation(conversation_history)
                break

        if not sent_any:
            # vedi nota sopra: in retry il messaggio user è l'originale,
            # non rimuoverlo se il DM non ha risposto.
            if not retry:
                with _state_lock:
                    if conversation_history and conversation_history[-1]["role"] == "user":
                        conversation_history.pop()
            yield f"data: {json.dumps({'error': 'Nessuna risposta dal DM'})}\n\n"
            return

        with _state_lock:
            event = json.dumps({"done": True, "state": game_state})
        yield f"data: {event}\n\n"

    return Response(stream_with_context(generate()),
                    mimetype="text/event-stream",
                    headers={
                        "Cache-Control": "no-cache",
                        "X-Accel-Buffering": "no",
                        "Connection": "keep-alive",
                    })


# ────────────────────────────────────────────────────────────────────────
# Voce umana — Edge TTS (sintesi vocale lato server)
# ────────────────────────────────────────────────────────────────────────

# Voci italiane neurali Microsoft Edge: timbro umano, qualità superiore
# alla sintesi del browser. Diego = narratore predefinito del DM.
EDGE_TTS_VOICES = [
    {"id": "it-IT-DiegoNeural",                "name": "Diego — maschile (narratore DM)"},
    {"id": "it-IT-GiuseppeMultilingualNeural", "name": "Giuseppe — maschile"},
    {"id": "it-IT-IsabellaNeural",             "name": "Isabella — femminile"},
    {"id": "it-IT-ElsaNeural",                 "name": "Elsa — femminile"},
]
DEFAULT_TTS_VOICE = "it-IT-DiegoNeural"


async def _edge_synth(text: str, voice: str, rate: str) -> bytes:
    """Sintetizza `text` con la voce Edge `voice`, ritorna MP3 in memoria."""
    communicate = edge_tts.Communicate(text, voice, rate=rate)
    buf = io.BytesIO()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            buf.write(chunk["data"])
    return buf.getvalue()


@app.route("/api/tts/voices")
def api_tts_voices():
    """Elenco voci Edge disponibili + stato modulo."""
    return jsonify({
        "available": _EDGE_TTS_OK,
        "default":   DEFAULT_TTS_VOICE,
        "voices":    EDGE_TTS_VOICES,
    })


@app.route("/api/tts", methods=["POST"])
def api_tts():
    """Body: {text, voice?, rate?} → audio MP3 (voce umana Edge)."""
    if not _EDGE_TTS_OK:
        return jsonify({"error": "edge-tts non installato. Esegui: pip install edge-tts"}), 503

    body = request.get_json(silent=True) or {}
    text = (body.get("text") or "").strip()
    if not text:
        return jsonify({"error": "Testo vuoto"}), 400
    if len(text) > 6000:
        text = text[:6000]

    voice = body.get("voice") or DEFAULT_TTS_VOICE
    if voice not in {v["id"] for v in EDGE_TTS_VOICES}:
        voice = DEFAULT_TTS_VOICE

    # rate Edge: formato "+N%" / "-N%" (default invariato)
    rate = str(body.get("rate") or "+0%")
    if not re.match(r"^[+-]\d{1,3}%$", rate):
        rate = "+0%"

    try:
        audio = asyncio.run(_edge_synth(text, voice, rate))
    except Exception as e:
        return jsonify({"error": f"Sintesi vocale fallita: {e}"}), 500
    if not audio:
        return jsonify({"error": "Nessun audio generato"}), 500

    return Response(audio, mimetype="audio/mpeg",
                    headers={"Cache-Control": "no-store"})


# ────────────────────────────────────────────────────────────────────────
# Debug
# ────────────────────────────────────────────────────────────────────────

@app.route("/api/debug")
def api_debug():
    # copia sotto lock: il worker thread muta _debug["exchanges"] mentre
    # serializziamo → senza copia jsonify può sollevare "list changed size".
    with _state_lock:
        snapshot = {"exchanges": list(_debug.get("exchanges", []))}
    return jsonify(snapshot)


@app.route("/api/conversation")
def api_conversation():
    return jsonify({"history": conversation_history})


# ────────────────────────────────────────────────────────────────────────
# Avvio
# ────────────────────────────────────────────────────────────────────────

def _build_startup_briefing() -> str | None:
    """Briefing da inviare al DM appena la webchat è pronta: ALLINEA il
    modello allo stato della partita all'inizio della sessione.

    Se esiste uno storico salvato → briefing di RIPRESA con TUTTI i
    messaggi (esattamente come il tasto CARICA), così il DM riprende dal
    punto esatto raggiunto nell'avventura. Con soli PG pronti e nessuno
    storico → briefing base. None se non c'è né storico né personaggi."""
    chars_data = state_mod.load_characters()
    chars_list = (chars_data or {}).get("characters") if chars_data else None

    adventure_text = None
    _adv_path = state_mod.current_adventure_path(game_state)
    if game_state.get("adventure_loaded") and os.path.exists(_adv_path):
        with open(_adv_path, "r", encoding="utf-8") as f:
            adventure_text = f.read()

    if conversation_history:
        return prompt_mod.build_resume_prompt(
            state=game_state,
            characters=chars_list,
            adventure_text=adventure_text,
            conversation=conversation_history,
        )
    if chars_list:
        return prompt_mod.build_full_prompt(
            state=game_state,
            characters=chars_list,
            adventure_text=adventure_text,
        )
    return None


def _auto_start_webchat():
    """Avvia Chromium + DeepSeek in background. Il briefing iniziale NON
    parte da qui: lo invia il frontend via /api/webchat/sync appena il LED
    del DM diventa verde (chat aperta e pronta). Così i messaggi salvati
    vengono passati al modello solo a DM confermato aperto."""
    try:
        print(f"[WEBCHAT] auto-start su {webchat.config.url}", flush=True)
        url = webchat.open()
        print(f"[WEBCHAT] aperto: {url} — briefing in attesa del LED verde", flush=True)
    except Exception as e:
        print(f"[WEBCHAT] auto-start FALLITO: {e}", flush=True)
        print(f"[WEBCHAT] usa il pulsante Setup per riprovare manualmente.", flush=True)


def main():
    port = int(os.environ.get("PORT", 5000))
    url = f"http://localhost:{port}"
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    # Auto-start Chromium se non disabilitato esplicitamente
    if os.environ.get("TAVOLO_NO_AUTOSTART") != "1":
        threading.Thread(target=_auto_start_webchat, daemon=True).start()
    else:
        print("[WEBCHAT] auto-start disabilitato (TAVOLO_NO_AUTOSTART=1)", flush=True)

    print(f"⚔ Tavolo — {url}", flush=True)
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
