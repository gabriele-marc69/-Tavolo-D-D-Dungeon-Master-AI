"""
Tavolo D&D 5.5e — server Flask sottile.

Routing + collante. Tutta la logica vive in:
  dnd/   — regole, schede, bestiario, stato
  dm/    — prompt DM, webchat Playwright, parser tag

Avvio: python app.py  →  http://localhost:5000
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import re
import threading
import webbrowser
from datetime import datetime

from flask import Flask, Response, jsonify, render_template, request, stream_with_context

from dnd import bestiary, character as char_mod, rules, state as state_mod
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

state_mod._ensure_runtime()

game_state: dict = state_mod.load_state()
conversation_history: list[dict] = state_mod.load_conversation()

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
        char_mod.recompute_derived(sheet)
        state_mod.upsert_character(dict(sheet))
        game_state["characters_loaded"] = True
        state_mod.save_state(game_state)

    return jsonify({"status": "ok", "sheet": sheet})


@app.route("/api/cast_spell", methods=["POST"])
def api_cast_spell():
    """
    Consumo / ripristino degli slot incantesimo di un PG.
    Body:
      {"character": "Nome", "level": N}   → lancia un incantesimo di livello N
                                             (scala di 1 lo slot disponibile)
      {"character": "Nome", "rest": true} → riposo lungo: azzera gli slot usati
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
        spells = sheet.get("spells")
        if not isinstance(spells, dict):
            return jsonify({"error": "Il personaggio non è un incantatore"}), 400
        slots = spells.setdefault("slots", {})

        if body.get("rest"):
            for sl in slots.values():
                if isinstance(sl, dict):
                    sl["used"] = 0
        else:
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
        game_state = state_mod.empty_state()
        conversation_history = []
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
    # Invia un briefing di ripresa (stato + storico) in background così
    # il modello può continuare la partita dal punto giusto.
    resync = False
    if webchat.is_open():
        chars_data = state_mod.load_characters()
        chars_list = (chars_data or {}).get("characters") if chars_data else None

        adventure_text = None
        if (game_state.get("adventure_loaded")
                and os.path.exists(state_mod.ADVENTURE_FILE)):
            with open(state_mod.ADVENTURE_FILE, "r", encoding="utf-8") as f:
                adventure_text = f.read()

        resume = prompt_mod.build_resume_prompt(
            state=game_state,
            characters=chars_list,
            adventure_text=adventure_text,
            conversation=conversation_history,
        )
        threading.Thread(
            target=webchat.send_briefing,
            args=(resume,),
            kwargs={"force": True},
            daemon=True,
        ).start()
        resync = True

    return jsonify({"status": "loaded",
                    "state": game_state,
                    "messages": len(conversation_history),
                    "resync": resync})


# ────────────────────────────────────────────────────────────────────────
# Webchat (DM via Chromium/DeepSeek)
# ────────────────────────────────────────────────────────────────────────

@app.route("/api/webchat/status")
def api_webchat_status():
    return jsonify({
        "open":    webchat.is_open(),
        "briefed": webchat.is_briefed(),
        "url":     webchat.config.url,
        "name":    webchat.config.name,
        "timeout": webchat.config.timeout,
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
            webchat.send_briefing(briefing)
        except Exception as e:
            print(f"[WEBCHAT] sync briefing fallito: {e}", flush=True)
        finally:
            _briefing_running = False

    threading.Thread(target=_bg, daemon=True).start()
    return jsonify({"status": "syncing"})


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
    if body.get("url"):
        webchat.config.url = body["url"].strip()
    if body.get("name"):
        webchat.config.name = body["name"].strip()
    if body.get("timeout"):
        try:
            webchat.config.timeout = max(15, min(600, int(body["timeout"])))
        except (TypeError, ValueError):
            pass
    try:
        url = webchat.open()
    except Exception as e:
        return jsonify({"error": str(e)}), 500

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
            reminders = prompt_mod.map_reminder()
            if with_extras:
                reminders += "\n\n" + prompt_mod.music_reminder()
                reminders += "\n\n" + prompt_mod.sprite_reminder()
                reminders += "\n\n" + prompt_mod.scene_reminder()

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
                if (game_state.get("adventure_loaded")
                        and os.path.exists(state_mod.ADVENTURE_FILE)):
                    with open(state_mod.ADVENTURE_FILE, "r", encoding="utf-8") as f:
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
            scene_upd   = parser.extract_scene(with_rolls)

            map_report = None
            with _state_lock:
                if state_upd:
                    parser.apply_state_update(game_state, state_upd)
                if char_upds:
                    parser.apply_char_updates(game_state, char_upds)
                    for p in game_state.get("players", []):
                        char_mod.upgrade_sheet(p.get("sheet") or {})
                # ── MAPPA: ricalcolata a OGNI messaggio ───────────────
                # La posizione del party ha UNA sola fonte di verità:
                # game_state["current_position"]. map_full è la mappa base
                # SENZA marker @ — il @ lo disegna apply_fog nel render.
                if map_block:
                    # nuova mappa dal DM: FORZALA a 20×20, estrai la
                    # posizione dal marker @, poi togli il @ dalla mappa
                    # base (la posizione vive in current_position).
                    norm = state_mod.normalize_map(map_block, 20)
                    at = state_mod.find_position(norm, "@")
                    game_state["map_full"] = norm.replace("@", ".")
                    if at:
                        game_state["current_position"] = [at[0], at[1]]
                # Se NON arriva una mappa nuova vale la posizione aggiornata
                # dallo STATE_UPDATE: apply_fog ridisegna il @ lì, quindi la
                # mappa si aggiorna comunque a ogni messaggio.
                pos = game_state.get("current_position") or [0, 0]
                state_mod.reveal_around(game_state, pos[0], pos[1], radius=2)
                if game_state.get("map_full"):
                    game_state["map_ascii"] = state_mod.apply_fog(
                        game_state["map_full"],
                        game_state.get("revealed_tiles", []),
                        party_pos=pos,
                    )
                    map_report = state_mod.check_map_coherence(
                        game_state["map_full"], pos)
                    if not map_report["ok"]:
                        print(f"[MAP] incoerenze: {map_report['issues']}", flush=True)
                for r in roll_results:
                    state_mod.add_roll(game_state, r)
                if music_upds:
                    parser.apply_music_update(game_state, music_upds)
                if sprite_upds:
                    parser.apply_sprites(game_state, sprite_upds)
                if scene_upd:
                    parser.apply_scene(game_state, scene_upd)
                # Persisti le schede su personaggi.json: XP, HP, livello e
                # ogni modifica del DM restano allineati anche nelle schede
                # personaggio (non solo nello stato di gioco).
                state_mod.sync_characters_from_players(game_state.get("players", []))
                game_state["pending_rolls"] = pending_rolls
                state_mod.save_state(game_state)

            display_text = parser.strip_narrative(with_rolls)

            _debug["exchanges"].append({
                "ts":      datetime.now().isoformat(timespec="seconds"),
                "user":    message_text,
                "raw":     raw[:2000],
                "shown":   display_text[:2000],
                "rolls":   roll_results,
                "map":     map_report,
                "sprites": sorted(sprite_upds.keys()),
                "scene":   bool(scene_upd),
            })
            if len(_debug["exchanges"]) > 20:
                _debug["exchanges"].pop(0)

            print(f"[DM] {len(display_text)} chars · {len(roll_results)} tiri IA "
                  f"· {len(pending_rolls)} tiri attesi · {len(sprite_upds)} sprite",
                  flush=True)
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
                first_msg = f"Giocatore: {user_message}"
                if retry:
                    first_msg += (
                        "\n\n[Sistema: RIGENERA la tua ultima risposta — "
                        "non è arrivata correttamente. Riemetti per intero "
                        "la scena, la mappa MAP_START…MAP_END (20×20) e il "
                        "<STATE_UPDATE>.]")
                if feedback:
                    first_msg = _format_roll_feedback(feedback) + "\n\n" + first_msg

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
                # fermarsi appeso al tiro. Il ciclo si ferma appena si
                # attende un tiro UMANO (lo lancia il giocatore dal
                # riquadro dadi) o dopo MAX_FOLLOWUPS turni.
                steps = 0
                while rolls and not pending and steps < MAX_FOLLOWUPS:
                    steps += 1
                    display, rolls, pending = _dm_exchange(
                        _format_roll_feedback(rolls))
                    if display.strip():
                        result_q.put(("token", display))

                # Tiri IA risolti ma non ancora narrati (si attende un tiro
                # umano, o limite turni): consegnali col prossimo messaggio.
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
                    if len(conversation_history) > 40:
                        conversation_history[:] = conversation_history[-40:]
                    state_mod.save_conversation(conversation_history)
                    # serializza lo stato mentre il lock è preso: il worker
                    # non può mutarlo a metà di json.dumps
                    event = json.dumps({"token": payload, "state": game_state})
                yield f"data: {event}\n\n"
            elif kind == "error":
                if not sent_any:
                    with _state_lock:
                        if conversation_history and conversation_history[-1]["role"] == "user":
                            conversation_history.pop()
                yield f"data: {json.dumps({'error': payload})}\n\n"
                return
            elif kind == "done":
                break

        if not sent_any:
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
    return jsonify(_debug)


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
    if (game_state.get("adventure_loaded")
            and os.path.exists(state_mod.ADVENTURE_FILE)):
        with open(state_mod.ADVENTURE_FILE, "r", encoding="utf-8") as f:
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

    print(f"⚔ Tavolo D&D 5.5e — {url}", flush=True)
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
