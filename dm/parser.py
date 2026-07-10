"""
Parser per estrarre tag strutturati dalla risposta del DM IA:

  <STATE_UPDATE>{...}</STATE_UPDATE>
  <CHAR_UPDATE>{...}</CHAR_UPDATE>
  <ROLL_REQ>{"dice":"1d20+5",...}</ROLL_REQ>
  MAP_START ... MAP_END

Espone anche `clean_text` per rimuovere reasoning/debug dal testo finale.
"""
from __future__ import annotations

import json
import re
from typing import Any

from dnd import rules


# ────────────────────────────────────────────────────────────────────────
# Regex
# ────────────────────────────────────────────────────────────────────────

RE_THINK     = re.compile(r"<think>[\s\S]*?</think>", re.IGNORECASE)
RE_STATE     = re.compile(r"<STATE_UPDATE>([\s\S]*?)</STATE_UPDATE>")
RE_CHAR      = re.compile(r"<CHAR_UPDATE>([\s\S]*?)</CHAR_UPDATE>")
RE_ROLL_REQ  = re.compile(r"<ROLL_REQ>([\s\S]*?)</ROLL_REQ>")
# Sentinel posato dopo il PRIMO <ROLL_REQ> di un PG umano: la narrazione
# successiva (esiti speculati, conseguenze "anticipate" dal DM) va TAGLIATA
# perché il giocatore non ha ancora lanciato il dado. I tag strutturati che
# seguono (MAP, STATE_UPDATE, CHAR_UPDATE) restano comunque processati.
HALT_HUMAN_ROLL = "<<HALT_HUMAN_ROLL>>"
RE_HALT_HUMAN_ROLL = re.compile(re.escape(HALT_HUMAN_ROLL))
# Riga-risultato di un tiro risolto dal SISTEMA (_format_roll_inline:
# «🎲 1d20+4 = **17** …»). Il DM ha il divieto assoluto di scrivere 🎲,
# quindi una riga col dado è sempre roba nostra: quando il taglio su
# HALT_HUMAN_ROLL rimuove la narrazione successiva al tiro umano, queste
# righe (tiri dei PG IA/mostri già eseguiti) vanno CONSERVATE in chat.
RE_SYSTEM_ROLL_LINE = re.compile(r"^\s*\**\s*🎲")
RE_MUSIC     = re.compile(r"<MUSIC>([\s\S]*?)</MUSIC>")
RE_SPRITE    = re.compile(r"<SPRITE>([\s\S]*?)</SPRITE>")
RE_SCENE     = re.compile(r"<SCENE>([\s\S]*?)</SCENE>")
RE_SPELL_CAST = re.compile(r"<SPELL_CAST>([\s\S]*?)</SPELL_CAST>")
# Marker XML minuscoli che delimitano sezioni e chiamate nel PROMPT inviato
# al DM (vedi dm/prompt.py: <identita>, <sistema_mappa>, <richiesta_*>, …).
# Il DM non deve mai riprodurli; se li fa eco per errore vanno tolti dalla
# narrazione visibile in chat.
RE_PROMPT_MARKER = re.compile(
    r"</?(?:identita|narrazione_giocatori|regole_fondamentali|tiro_dadi"
    r"|attacchi_due_tiri|flusso_di_gioco|fase_[a-z0-9_]+"
    r"|interruzione_pg_umano|riferimento_combattimento|mappa_aggiornata"
    r"|pixel_art|sprite_personalizzati|tag_di_stato|riposo_e_fine_sessione"
    r"|aggiornamento_schede|equipaggiamento|incantesimi_slot|colonna_sonora"
    r"|progressione|morte|regole_personaggi_precaricati"
    r"|regole_avventura_precaricata|lunghezza_e_ritmo|formato_risposta"
    r"|stato_corrente|storico_partita|istruzione_ripresa"
    r"|sistema_[a-z0-9_]+|richiesta_[a-z0-9_]+)>")
# Decorazioni che i modelli mettono attorno ai marcatori: parentesi quadre
# ([MAP_START]), angolari (<MAP_START>), grassetto (**MAP_START**), backtick,
# cancelletti, due punti finali (MAP_START:). Le tolleriamo così il blocco
# viene riconosciuto anche se il modello mette i < > solo da un lato (es.
# <MAP_START> ma MAP_END nudo). _MK_L = a sinistra del marcatore, _MK_R = a
# destra.
_MK_L = r"[ \t]*[\[\<\*`#_~>\-]*[ \t]*"
_MK_R = r"[ \t]*[\]\>\<\*`:_~\-]*[ \t]*"
RE_MAP       = re.compile(
    _MK_L + r"MAP_START" + _MK_R + r"\n([\s\S]*?)\n" + _MK_L + r"MAP_END" + _MK_R)
RE_MAP_BLOCK = re.compile(
    _MK_L + r"MAP_START" + r"[\s\S]*?" + r"MAP_END" + _MK_R, re.IGNORECASE)
# Tag <MAP>…</MAP>: forma alternativa che alcuni modelli usano spontaneamente.
# Va trattata ESATTAMENTE come MAP_START…MAP_END: estratta come mappa,
# rimossa dalla narrazione visibile.
RE_MAP_TAG   = re.compile(r"<MAP>\s*([\s\S]*?)\s*</MAP>", re.IGNORECASE)
RE_MAP_TAG_BLOCK = re.compile(r"<MAP>[\s\S]*?</MAP>\s*", re.IGNORECASE)
# Cattura permissiva del CONTENUTO fra i marcatori, senza pretendere a-capo:
# serve a recuperare il blocco COLLASSATO su una riga sola (il rendering
# markdown della chat fonde le righe di un paragrafo non recintato e
# l'innerText arriva come "MAP_START #### #..@ ... MAP_END").
RE_MAP_INLINE = re.compile(
    _MK_L + r"MAP_START" + _MK_R + r"\s*([\s\S]*?)\s*" + _MK_L + r"MAP_END" + _MK_R,
    re.IGNORECASE)
# Sezioni LEGENDA_START…LEGENDA_END e SPRITE_START…SPRITE_END: il DM le
# emette PRIMA di MAP_START…MAP_END (nuovo formato a tre blocchi). La
# legenda mappa carattere→nome; lo SPRITE i disegni 16×16. Tolleriamo le
# stesse decorazioni dei marcatori MAP (_MK_L/_MK_R).
RE_LEGEND       = re.compile(
    _MK_L + r"LEGENDA_START" + _MK_R + r"\n?([\s\S]*?)\n?" + _MK_L + r"LEGENDA_END" + _MK_R,
    re.IGNORECASE)
RE_LEGEND_BLOCK = re.compile(
    _MK_L + r"LEGENDA_START" + r"[\s\S]*?" + r"LEGENDA_END" + _MK_R, re.IGNORECASE)
RE_SPRITE_SEC       = re.compile(
    _MK_L + r"SPRITE_START" + _MK_R + r"\n?([\s\S]*?)\n?" + _MK_L + r"SPRITE_END" + _MK_R,
    re.IGNORECASE)
RE_SPRITE_SEC_BLOCK = re.compile(
    _MK_L + r"SPRITE_START" + r"[\s\S]*?" + r"SPRITE_END" + _MK_R, re.IGNORECASE)
# Oggetto sprite "nudo" dentro la sezione SPRITE_START…SPRITE_END: il DM può
# elencare i {…} senza racchiuderli in <SPRITE>. Gli sprite non hanno graffe
# annidate (rows è un array), quindi un match non-greedy di {…} basta.
RE_SPRITE_OBJ   = re.compile(r"\{[^{}]*\}")
# Etichette delle barre dei code-block delle chat web (pulsanti Copia/
# Scarica/Copy/Download/text che l'estrazione innerText cattura come righe).
RE_CODE_TOOLBAR = re.compile(
    r"(?im)^\s*(?:copia|copy|scarica|download|text|json|markdown)\s*$",
)
RE_DEBUG_LN  = re.compile(r"^\s*\*(?:Calcolo|Formula Base|Dettaglio).*?\*\s*$",
                          re.IGNORECASE | re.MULTILINE)

# Sezioni FASE 3 da nascondere all'utente (sorpresa). Il DM web può scrivere
# le intestazioni in vari modi: con bold (**N. NAME**), heading (### NAME),
# o anche solo numero+keyword PLAIN (es. "2. MAPPA DUNGEON 10×10").
# Sezioni rimosse: MAPPA, LISTA ZONE, MOSTRI, PNG, TESORI, TRAMA, ARCO,
# LEGENDA, BOSS, STAT BLOCK, EPILOGO, RIVELAZIONI.
# Sezione mantenuta: 1 TITOLO + HOOK (presentazione narrativa).
_SECRET_KEYWORDS = (
    r"MAPPA(?:\s+DUNGEON)?(?:\s+\d+\s*[x×]\s*\d+)?",
    r"LISTA\s+ZONE",
    r"ZONE\s+DEL\s+DUNGEON",
    r"ZONE",
    r"MOSTRI",
    r"STAT\s*BLOCK",
    r"PNG",
    r"NPC\b",
    r"TESORI",
    r"TRAMA",
    r"ARCO\s+NARRATIVO",
    r"LEGENDA",
    r"BOSS(?:\s+FINALE)?",
    r"EPILOGO",
    r"RIVELAZIONI",
)
_HDR_KW = "(?:" + "|".join(_SECRET_KEYWORDS) + ")"

# Stop condition (lookahead): match qualsiasi NUOVO header per terminare
# la sezione corrente.
_STOP_AHEAD = (
    r"\n[ \t]*\*\*\s*\d+\s*[.)]"                               # **N. ...
    r"|\n[ \t]*\d+\s*[.)]\s*[A-ZÀ-ÿ]"                          # N. UPPER (plain)
    r"|\n[ \t]*\*\*\s*[A-ZÀ-Ý][A-ZÀ-Ý \t\-]{2,40}\*\*"          # **HEADER**
    r"|\n[ \t]*#{1,4}\s+\S"                                     # ### HEADER
    r"|\n[ \t]*---+\s*\n"                                        # ---
    r"|\Z"                                                       # EOT
)

RE_SECRET_SECTION = re.compile(
    r"(?:^|\n)[ \t]*"
    r"(?:"
        r"\*\*\s*\d+\s*[.)]\s*" + _HDR_KW + r"[^*\n]*\*\*"      # **2. MAPPA**
        r"|"
        r"\*\*\s*" + _HDR_KW + r"[^*\n]*\*\*"                    # **MOSTRI**
        r"|"
        r"#{1,4}\s*" + _HDR_KW + r"[^\n]*"                       # ### MOSTRI
        r"|"
        r"\d+\s*[.)]\s*" + _HDR_KW + r"[^\n]*"                   # 2. MAPPA DUNGEON
        r"|"
        r"" + _HDR_KW + r"[ \t]*$"                               # MOSTRI da solo (riga)
    r")"
    r"[\s\S]*?(?=" + _STOP_AHEAD + r")",
    re.IGNORECASE | re.MULTILINE,
)

# Footer di disclaimer DeepSeek/altre chat web
RE_AI_FOOTER = re.compile(
    r"\n*\s*(?:"
        r"Questa risposta è generata da AI[^\n]*"
        r"|Generated by AI[^\n]*"
        r"|AI[- ]generated[^\n]*"
        r"|This response is AI[- ]generated[^\n]*"
    r")\s*$",
    re.IGNORECASE,
)

# Righe che spoilerano nemici/tesori al giocatore (formato "etichetta: …").
# La FASE 3 mette queste info nelle sezioni segrete; eventuali righe
# sciolte vanno comunque tolte dalla narrazione visibile.
RE_LEAK_LINE = re.compile(
    r"(?im)^[ \t>*_·•\-]*\*{0,2}\s*"
    r"(?:nemici|nemico|mostri|mostro|tesori|tesoro|bottino|loot|"
    r"ricompens[ae]|incontri|avversari)"
    r"[^\n:]{0,28}:.*$"
)

# HP a video dei NEMICI: il DM tende a scrivere gli HP delle creature nella
# narrazione (es. "Goblin (7/9 HP)", "HP: 12/15", "5 HP rimasti"). Gli HP del
# party sono già nel pannello schede, quindi togliamo i readout numerici di HP
# dalla prosa visibile — così non trapelano gli HP dei mostri. Tre forme:
#   1) parentetica:  (7/9 HP)  [HP 12]  (PF 5/8)
#   2) etichettata:  HP: 7/9   PF 12/15
#   3) "rimasti":    7 HP rimasti / gli restano 5 PF
# Frasi-filler di ATTESA del DM («(Attendo il tiro per colpire di Mira)»,
# «Attendo il risultato del sistema.», «In attesa dei tiri di iniziativa…»):
# il sistema risolve i tiri da solo e risponde subito — in chat queste
# righe confondono e basta. Per non toccare la prosa legittima
# («dall'aspetto orribile», «attende il suo destino») servono ENTRAMBE:
# una parola di attesa (attendo/in attesa/aspetto…) E una parola di
# contesto-tiro (tiro/risultato/sistema/dado/esito/iniziativa).
_WAIT_WORD = r"(?:attend\w+|attes[ao]|in\s+attesa|aspett\w+)"
_WAIT_CTX  = r"(?:tir[oi]|risultat[oi]|sistema|lanci\w*|dad[oi]|esit[oi]|iniziativa)"
RE_WAIT_PAREN = re.compile(
    r"(?i)[\(\[]"
    r"(?=[^()\[\]\n]*\b" + _WAIT_WORD + r")"
    r"(?=[^()\[\]\n]*\b" + _WAIT_CTX + r")"
    r"[^()\[\]\n]*[\)\]]")
RE_WAIT_LINE = re.compile(
    r"(?im)^[ \t]*[\*_~`]*" + _WAIT_WORD + r"\b"
    r"[^\n]*\b" + _WAIT_CTX + r"[^\n]*$")

# Esito di prova DICHIARATO dal DM (es. «Percezione CD 12 superata»,
# «supera la CD 14», «prova fallita»): legittimo SOLO dopo che un numero
# gli è stato consegnato (tiro del giocatore o del sistema). La guardia in
# app.py usa questi pattern per scoprire gli esiti inventati — risposta
# senza alcun <ROLL_REQ> e nessun risultato in ingresso — e li CANCELLA
# dalla chat, imponendo al DM di chiedere il tiro col tag.
_CHECK_OUTCOME = r"(?:superat\w+|fallit\w+|riuscit\w+|mancat\w+)"
RE_CHECK_CLAIM = re.compile(
    r"(?i)(?:"
    r"\bCD\s*\d+[^\n.!?]{0,24}?\b" + _CHECK_OUTCOME +          # CD 12 superata
    r"|\b(?:supera\w*|fallisc\w*|fallit\w*|batt\w*|raggiung\w*)"
    r"\s+(?:la\s+|una\s+)?CD\s*\d+" +                          # supera la CD 12
    r"|\b(?:prova|tiro|ts)\b[^\n.!?]{0,40}?\b" + _CHECK_OUTCOME +  # prova … superata
    r")"
)

_HP_WORD = r"(?:hp|pf|punti\s+ferita)"
RE_HP_READOUT = re.compile(
    r"(?i)"
    r"[\(\[]\s*" + _HP_WORD + r"\s*\d{1,3}(?:\s*/\s*\d{1,3})?\s*[\)\]]"  # (HP 12)
    r"|"
    r"[\(\[]\s*\d{1,3}(?:\s*/\s*\d{1,3})?\s*" + _HP_WORD + r"\s*[\)\]]"  # (7/9 HP)
    r"|"
    r"\b" + _HP_WORD + r"\s*[:=]\s*\d{1,3}(?:\s*/\s*\d{1,3})?\b"         # HP: 7/9
    r"|"
    r"\b\d{1,3}\s*" + _HP_WORD + r"\s+(?:rimast\w+|restant\w+|rimanent\w+)\b"
)


# ────────────────────────────────────────────────────────────────────────
# Pulizia
# ────────────────────────────────────────────────────────────────────────

# Etichette di interfaccia che alcune chat web antepongono alla risposta
# come testo per screen-reader (es. Claude.ai con locale italiano:
# "Claude ha risposto:"). L'estrazione via innerText le cattura: da togliere.
RE_UI_LABEL = re.compile(
    r"(?:^|\n)[ \t]*(?:Claude|Assistant|Assistente|ChatGPT|Gemini|DeepSeek|"
    r"Grok|Qwen)\s+(?:ha\s+(?:risposto|detto|scritto)|said|replied|"
    r"responded)[ \t]*:[ \t]*",
    re.IGNORECASE,
)


def _norm_cmp(s: str) -> str:
    """Forma normalizzata per il CONFRONTO fra testi: senza markdown, senza
    punteggiatura di coda, whitespace collassato, minuscolo. Così due copie
    che differiscono solo per markdown o punteggiatura risultano uguali."""
    s = re.sub(r"[*_`#>~]+", "", s)
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s.rstrip(" .!?,;:—-…")


def _same_text(a: str, b: str) -> bool:
    """True se due testi sono 'la stessa cosa' a meno di markdown,
    punteggiatura di coda o lieve troncamento (copia raddoppiata)."""
    a, b = _norm_cmp(a), _norm_cmp(b)
    if not a or not b:
        return False
    if a == b:
        return True
    lo, hi = (a, b) if len(a) <= len(b) else (b, a)
    return len(lo) >= 25 and hi.startswith(lo) and (len(hi) - len(lo)) <= 4


def _dedup_consecutive(units: list, min_len: int) -> list:
    """Rimuove ogni elemento uguale (per _same_text) a quello tenuto prima.
    Salta il confronto sulle unità troppo corte (sotto min_len)."""
    out: list = []
    for u in units:
        if out and len(_norm_cmp(u)) >= min_len and _same_text(out[-1], u):
            continue
        out.append(u)
    return out


def _dehalve(units: list) -> list:
    """Se `units` è una sequenza raddoppiata [A,B,…][A,B,…], restituisce
    solo la prima metà."""
    m = len(units)
    if m >= 2 and m % 2 == 0:
        first, second = units[: m // 2], units[m // 2:]
        if (all(_same_text(a, b) for a, b in zip(first, second))
                and any(len(_norm_cmp(x)) >= 15 for x in first)):
            return first
    return units


def _collapse_doubled(s: str) -> str:
    """Collassa un testo che è la stessa cosa ripetuta due volte di fila
    (separata da whitespace; la seconda copia può essere markdown grezzo o
    leggermente troncata): restituisce una sola copia."""
    norm = _norm_cmp(s)
    L = len(norm)
    if L < 60:
        return s
    half = L // 2
    for h in range(max(1, half - 6), half + 7):
        if _same_text(norm[:h], norm[h:]):
            # ritaglia la prima copia nel testo ORIGINALE: conta i caratteri
            # di confronto (markdown escluso) finché si raggiunge `h`.
            count, cut, prev_ws = 0, len(s), False
            for i, ch in enumerate(s):
                if ch in "*_`#>~":
                    continue
                ws = ch.isspace()
                if not ws or not prev_ws:
                    count += 1
                prev_ws = ws
                if count >= h:
                    cut = i + 1
                    break
            return s[:cut].strip()
    return s


def _dedup_sentences(block: str) -> str:
    """Dentro un blocco, rimuove le FRASI consecutive duplicate (riga per
    riga). Usato solo sulla narrazione finale (testo già privo di tag)."""
    out_lines = []
    for line in block.split("\n"):
        sents = re.split(r"(?<=[.!?…])\s+", line)
        if len(sents) > 1:
            sents = _dedup_consecutive(sents, min_len=25)
        out_lines.append(" ".join(sents))
    return "\n".join(out_lines)


def _dedup_response(text: str, deep: bool = False) -> str:
    """Collassa le risposte duplicate dalle chat web (es. Claude.ai, che
    rimanda il messaggio raddoppiato — preceduto da un'etichetta UI e con
    una copia in markdown grezzo). Toglie l'etichetta, poi collassa la
    ripetizione a livello di blocco e di intero messaggio. `deep=True`
    aggiunge la dedup a livello di FRASE: usarla SOLO sulla narrazione
    finale (testo già privo di mappa e tag)."""
    if not text:
        return text
    s = RE_UI_LABEL.sub("\n", text).strip()
    blocks = re.split(r"\n{2,}", s)
    if len(blocks) >= 2:
        blocks = _dedup_consecutive(_dehalve(blocks), min_len=15)
        s = "\n\n".join(blocks).strip()
    if deep:
        s = "\n\n".join(_dedup_sentences(b)
                        for b in re.split(r"\n{2,}", s))
    return _collapse_doubled(s)


def clean_text(text: str) -> str:
    """Rimuove blocchi <think>, righe di debug, spazi multipli eccessivi e
    collassa le risposte duplicate (chat web che rimandano il messaggio due
    volte). NON rimuove i tag STATE_UPDATE/CHAR_UPDATE/ROLL_REQ
    (gestiti separatamente)."""
    if not text:
        return ""
    out = RE_THINK.sub("", text)
    out = RE_DEBUG_LN.sub("", out)
    # toolbar dei code-block delle chat web ("Copia", "Scarica", ecc.)
    out = RE_CODE_TOOLBAR.sub("", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    out = _dedup_response(out)
    return out.strip()


def strip_tags(text: str) -> str:
    """Rimuove TUTTI i tag strutturati (per la visualizzazione utente)."""
    out = RE_STATE.sub("", text)
    out = RE_CHAR.sub("", out)
    out = RE_ROLL_REQ.sub("", out)
    out = RE_MUSIC.sub("", out)
    out = RE_SPRITE.sub("", out)
    out = RE_SCENE.sub("", out)
    out = RE_SPELL_CAST.sub("", out)
    out = RE_MAP_TAG_BLOCK.sub("", out)
    out = RE_HALT_HUMAN_ROLL.sub("", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def strip_narrative(text: str) -> str:
    """
    Versione "solo narrazione" del testo, per il display chat.
    Rimuove: tag strutturati, blocchi MAP_START..END, sezioni segrete
    della FASE 3 (mappa, zone, mostri, PNG, tesori, trama, boss, epilogo),
    e footer AI-generated.
    Mantiene la narrazione (titolo+hook, dialoghi, descrizioni delle scene).
    """
    out = RE_STATE.sub("", text)
    out = RE_CHAR.sub("", out)
    out = RE_ROLL_REQ.sub("", out)
    out = RE_MUSIC.sub("", out)
    out = RE_SPRITE.sub("", out)
    out = RE_SCENE.sub("", out)
    out = RE_SPELL_CAST.sub("", out)
    # marker di sezione del prompt eventualmente fatti eco dal DM
    out = RE_PROMPT_MARKER.sub("", out)
    # blocchi a tre sezioni (nuovo formato mappa): LEGENDA / SPRITE / MAP.
    # Vanno SOLO nel riquadro mappa, mai nel testo chat al centro.
    out = RE_LEGEND_BLOCK.sub("", out)
    out = RE_SPRITE_SEC_BLOCK.sub("", out)
    out = RE_MAP_BLOCK.sub("", out)
    out = RE_MAP_TAG_BLOCK.sub("", out)
    # mappa ASCII NUDA (senza marcatori) o dentro un code-fence: toglila dal
    # testo chat (va solo nel riquadro mappa, dove la rende extract_map).
    out = _strip_bare_grid(out)
    # recinti ``` rimasti VUOTI dopo aver tolto la mappa al loro interno
    # (es. ```text\n\n``` ) → via, altrimenti restano nel testo chat.
    out = re.sub(r"```[ \t]*[a-zA-Z0-9]*[ \t]*\n?\s*```", "", out)
    out = RE_AI_FOOTER.sub("", out)
    # PAUSA SU TIRO UMANO: se è presente il sentinel HALT_HUMAN_ROLL, taglia
    # qui la narrazione (qualunque cosa il DM abbia scritto dopo il tiro
    # umano è un esito anticipato — il giocatore deve ancora tirare).
    # ECCEZIONE: le righe 🎲 dopo il sentinel sono tiri di PG IA/mostri GIÀ
    # risolti dal sistema (es. iniziative chieste dopo quella dell'umano):
    # non sono speculazione del DM e restano visibili in chat.
    halt = RE_HALT_HUMAN_ROLL.search(out)
    if halt:
        kept_rolls = [ln.strip() for ln in out[halt.end():].splitlines()
                      if RE_SYSTEM_ROLL_LINE.match(ln)]
        out = out[:halt.start()].rstrip()
        if kept_rolls:
            out += ("\n\n" if out else "") + "\n".join(kept_rolls)
    out = RE_HALT_HUMAN_ROLL.sub("", out)
    # applica più volte: sezioni adiacenti possono lasciare residui che
    # diventano stop-anchor per altre sezioni al passaggio successivo
    for _ in range(3):
        new_out = RE_SECRET_SECTION.sub("", out)
        if new_out == out:
            break
        out = new_out
    # righe sciolte che spoilerano nemici/tesori
    out = RE_LEAK_LINE.sub("", out)
    # readout numerici di HP nella prosa (nascondono gli HP dei nemici;
    # quelli del party sono già nel pannello schede)
    out = RE_HP_READOUT.sub("", out)
    # frasi di attesa del DM («(Attendo il tiro di Mira)», «Attendo il
    # risultato del sistema.»): il sistema risolve e risponde da solo,
    # in chat non devono comparire.
    out = RE_WAIT_PAREN.sub("", out)
    out = RE_WAIT_LINE.sub("", out)
    # parentesi/spazi rimasti vuoti dopo il taglio dei readout HP
    out = re.sub(r"[ \t]*[\(\[]\s*[\)\]]", "", out)
    out = re.sub(r"[ \t]{2,}", " ", out)
    # rimuovi separatori "---" rimasti orfani
    out = re.sub(r"(?m)^\s*---+\s*$", "", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    # Dedup FINALE della narrazione: la chat web può raddoppiare il
    # messaggio (con un blocco tag MAP/STATE FRA le due copie, oppure
    # ripetendo solo l'ultima frase). Qui i tag sono già stati tolti →
    # dedup profonda (blocco + frase) sul testo di pura narrazione.
    out = _dedup_response(out, deep=True)
    return out.strip()


def has_check_claim(text: str) -> bool:
    """True se il testo dichiara l'esito di una prova/tiro («CD 12
    superata», «prova fallita», …). Da usare SOLO assieme al contesto
    (vedi guardia in app.py): l'esito è legittimo quando un risultato è
    stato davvero consegnato al DM."""
    return bool(RE_CHECK_CLAIM.search(text or ""))


def strip_check_claims(text: str) -> str:
    """Rimuove le FRASI che dichiarano l'esito di una prova/tiro. Usato
    SOLO quando la guardia rileva un esito inventato (nessun dado tirato):
    la frase non deve restare in chat, il tiro verrà richiesto col tag."""
    if not text:
        return text
    out_lines: list[str] = []
    for line in text.split("\n"):
        sents = re.split(r"(?<=[.!?…])\s+", line)
        kept = [s for s in sents if not RE_CHECK_CLAIM.search(s)]
        out_lines.append(" ".join(kept))
    out = "\n".join(out_lines)
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


# ────────────────────────────────────────────────────────────────────────
# Estrazione tag
# ────────────────────────────────────────────────────────────────────────

def _lenient_json(raw: str) -> Any:
    """Ultimo tentativo: ripara gli errori JSON tipici dei modelli web —
    tag rimasti incollati dentro il payload (es. `<MUSIC>{<MUSIC>{...`),
    graffe doppie, chiavi senza virgolette, virgole finali. Usato SOLO
    dopo che il parsing stretto è fallito."""
    s = (raw or "").strip()
    # frammenti di tag rimasti DENTRO il payload (<MUSIC>, </SCENE>, ...)
    s = re.sub(r"</?[A-Za-z_]+>", "", s)
    if "{" not in s or "}" not in s:
        return None
    # isola dal primo { all'ultimo }
    s = s[s.index("{"): s.rindex("}") + 1]
    # graffe di apertura/chiusura duplicate adiacenti (es. "{{" → "{")
    s = re.sub(r"\{(?:\s*\{)+", "{", s)
    s = re.sub(r"\}(?:\s*\})+", "}", s)
    # chiavi non quotate: {mood:  / ,bpm:  →  {"mood":  / ,"bpm":
    s = re.sub(r'([{,]\s*)([A-Za-z_]\w*)(\s*:)', r'\1"\2"\3', s)
    # virgole finali prima di } o ]
    s = re.sub(r",(\s*[}\]])", r"\1", s)
    try:
        return json.loads(s)
    except (ValueError, json.JSONDecodeError):
        return None


def _safe_json(payload: str) -> Any:
    try:
        return json.loads(payload.strip())
    except (json.JSONDecodeError, ValueError):
        pass
    # tentativo: estrai il primo oggetto valido
    try:
        start = payload.index("{")
        end = payload.rindex("}") + 1
        return json.loads(payload[start:end])
    except (ValueError, json.JSONDecodeError):
        pass
    # ultimo tentativo: ripara gli errori JSON comuni dei modelli web
    return _lenient_json(payload)


def _as_dicts(obj: Any) -> list[dict]:
    """Normalizza un payload JSON in lista di dict.
    Il DM può emettere un singolo oggetto {...} o un array [{...},{...}]."""
    if isinstance(obj, dict):
        return [obj]
    if isinstance(obj, list):
        return [x for x in obj if isinstance(x, dict)]
    return []


def extract_state(text: str) -> dict | None:
    """Unisce TUTTI i <STATE_UPDATE> del testo in un unico dict, o None.
    Tollera payload array. Se il DM emette più STATE_UPDATE in un turno
    (es. pre-azione + post-azione) i campi vengono fusi nell'ordine:
    l'ULTIMO valore di ogni chiave vince."""
    upd: dict = {}
    for m in RE_STATE.finditer(text):
        for d in _as_dicts(_safe_json(m.group(1))):
            upd.update(d)
    if not upd:
        return None
    if isinstance(upd.get("map_ascii"), str):
        upd["map_ascii"] = upd["map_ascii"].replace("\\n", "\n")
    return upd


def extract_chars(text: str) -> list[dict]:
    """Tutti i <CHAR_UPDATE> come lista di dict (gestisce anche array)."""
    out: list[dict] = []
    for m in RE_CHAR.finditer(text):
        out.extend(_as_dicts(_safe_json(m.group(1))))
    return out


def _last_match(rx: "re.Pattern", text: str) -> "re.Match | None":
    """Ultimo match di `rx` in `text` (re non offre rsearch). Serve perché
    quando il DM emette PIÙ blocchi mappa nella stessa risposta — tipico
    dopo la richiesta di una nuova mappa: ridisegna la vecchia e poi mette
    quella aggiornata — conta SOLO l'ultima generata. `.search()` prende il
    primo match e mostrerebbe la mappa vecchia."""
    m = None
    for m in rx.finditer(text):
        pass
    return m


def extract_map(text: str) -> str | None:
    """Estrae la mappa ASCII da MAP_START…MAP_END (forma canonica) o dal
    tag <MAP>…</MAP> (forma alternativa che alcuni modelli emettono).
    Restituisce il blocco interno, già normalizzato dei caratteri non
    canonici (vedi _normalize_map_chars).

    Se il testo contiene PIÙ blocchi mappa, considera solo l'ULTIMO: dopo
    una richiesta di nuova mappa il DM tende a ridisegnare la precedente
    prima di quella aggiornata, e va mostrata quella aggiornata.

    FALLBACK: se mancano del tutto i marcatori (alcuni modelli — DeepSeek
    in primis — disegnano la griglia ASCII NUDA, magari dentro un blocco
    ``` ``` ```), prova a riconoscere comunque una griglia di mappa
    (vedi _extract_bare_grid). Così la mappa appare anche quando il DM
    ignora la richiesta di racchiuderla nei marcatori."""
    # ULTIMA mappa generata: fra forma canonica e tag <MAP>, prendi il match
    # con posizione più avanzata nel testo (non la prima né per-forma).
    m_canon = _last_match(RE_MAP, text)
    m_tag   = _last_match(RE_MAP_TAG, text)
    m = max((x for x in (m_canon, m_tag) if x),
            key=lambda x: x.start(), default=None)
    if m:
        # 1) RIPULISCI il rumore che le chat web infilano nel blocco:
        #    recinti ```), barre toolbar (Copia/Download), numeri di riga e
        #    marcatori di lista a inizio riga. Questo è ciò che faceva
        #    arrivare al renderer una griglia sballata (= mappa "rotta").
        # 2) NORMALIZZA i caratteri al set canonico: i modelli disegnano
        #    spesso muri/bordi con box-drawing (│ ─ ┌) o lettere (W/H/B) e
        #    numerano i tile (S1, M2). Questi caratteri NON hanno sprite e
        #    il renderer li mostrerebbe come PAVIMENTO, bucando muri e
        #    geometria (mappa incoerente). _normalize_map_chars li riporta
        #    ai tile pixel-art noti (box-drawing→muro, cifre→pavimento,
        #    alias→tile equivalente). Gli sprite personalizzati del DM usano
        #    id canonici, quindi restano intatti.
        cleaned = _normalize_map_chars(_clean_map_block(m.group(1)))
        if cleaned.strip():
            return cleaned
    bare = _extract_bare_grid(text)
    if bare:
        return _normalize_map_chars(bare)
    # ULTIMA SPIAGGIA: blocco COLLASSATO — i marcatori ci sono ma il
    # contenuto è arrivato senza a-capo (markdown non recintato: il
    # renderer fonde le righe del paragrafo in spazi). Le righe di griglia
    # non contengono spazi, quindi i token separati da whitespace SONO le
    # righe: se ne escono abbastanza di larghezza simile, ricostruiamo.
    # Senza questo recupero il blocco viene comunque TOLTO dalla chat
    # (RE_MAP_BLOCK non richiede a-capo) ma il riquadro resta sulla mappa
    # vecchia: fallimento invisibile.
    m = _last_match(RE_MAP_INLINE, text)
    if m:
        rebuilt = _rebuild_collapsed_grid(m.group(1))
        return _normalize_map_chars(rebuilt) if rebuilt else None
    return None


def _rebuild_collapsed_grid(inner: str) -> str | None:
    """Ricostruisce la griglia da un blocco mappa collassato su una riga.
    `inner` è il testo fra MAP_START e MAP_END: i token separati da
    whitespace sono candidati-riga. Accetta solo se quasi tutti i token
    sembrano righe di mappa (≥5, larghezze simili): mai inventare una
    mappa da prosa."""
    toks = [t for t in re.split(r"\s+", (inner or "").strip()) if t]
    rows = [t for t in toks if _looks_like_map_row(t)]
    if len(rows) < 5 or not toks or len(rows) < len(toks) * 0.8:
        return None
    widths = [len(r) for r in rows]
    if max(widths) - min(widths) > max(widths) * 0.5:
        return None
    return "\n".join(rows)


# ── Pulizia righe di mappa ────────────────────────────────────────────
# Le chat web (DeepSeek/Claude/…) restituiscono il blocco mappa con
# decorazioni che ROMPONO la griglia: recinti ```, etichette toolbar dei
# code-block, numerazione di riga ("1  ####", "12| ####"), marcatori di
# lista/citazione ("- ####", "> ####"). Tolti questi, restano i tile.
_RE_MAP_FENCE   = re.compile(r"^\s*```")
_RE_MAP_TOOLBAR = re.compile(
    r"(?i)^\s*(?:copia|copy|scarica|download|text|json|markdown|plaintext)\s*$")
# numero di riga: "12| ", "2: ", "3. ", oppure "1 " (numero + ≥1 spazio).
# I tile della mappa non sono cifre nude, quindi un numero a inizio riga
# seguito da separatore o spazio è quasi sempre una numerazione di riga.
_RE_MAP_LINENO  = re.compile(r"^\s*\d{1,3}(?:\s*[|:.\)]\s*|[ \t]+)")
# marcatore di lista/citazione a inizio riga
_RE_MAP_ROWMARK = re.compile(r"^\s*[>*•·]\s+|^\s*-\s+")


def _clean_map_line(line: str) -> str:
    """Toglie da UNA riga di mappa i prefissi-rumore (lista/citazione,
    numero di riga). Conserva i caratteri-tile così come sono."""
    s = line.rstrip("\n")
    s = _RE_MAP_ROWMARK.sub("", s)
    s = _RE_MAP_LINENO.sub("", s)
    return s


def _clean_map_block(block: str) -> str:
    """Ripulisce un blocco mappa: scarta righe di recinto ``` e toolbar,
    toglie numeri di riga e marcatori di lista, elimina le righe vuote in
    testa e in coda. Restituisce i tile GREZZI (nessuna normalizzazione
    dei caratteri: ogni tile lo disegna lo sprite del modello)."""
    out: list[str] = []
    for ln in (block or "").splitlines():
        if _RE_MAP_FENCE.match(ln):
            continue
        if _RE_MAP_TOOLBAR.match(ln):
            continue
        out.append(_clean_map_line(ln))
    while out and not out[0].strip():
        out.pop(0)
    while out and not out[-1].strip():
        out.pop()
    return "\n".join(out)


def _looks_like_map_row(line: str) -> bool:
    """Una riga sembra una riga di mappa se: lunga ≥5, fatta quasi solo di
    caratteri di mappa (canonici o alias di muro), e contiene almeno un
    muro '#' o un pavimento '.'. La soglia alta esclude la prosa (parole
    con lettere non-mappa: b,d,h,i,l,n,r,u…)."""
    s = line.rstrip()
    if len(s) < 5:
        return False
    mapchars = sum(1 for ch in s if ch in _CANON_MAP_CHARS or ch in _MAP_ALIASES)
    if mapchars / len(s) < 0.85:
        return False
    return any(ch in "#." for ch in s)


def _bare_grid_span_qualifies(lines: list[str], start: int, end: int) -> bool:
    """Lo span [start,end) di righe contigue "da mappa" qualifica come mappa
    nuda: ≥3 righe, larghezze simili, almeno ~1 muro per riga in media."""
    if end - start < 3:        # accetta anche stanze piccole (3+ righe)
        return False
    rows = [lines[k].rstrip() for k in range(start, end)]
    widths = [len(r) for r in rows]
    if max(widths) - min(widths) > max(widths) * 0.5:
        return False           # troppo irregolare: probabile non-mappa
    joined = "".join(rows)
    walls = sum(1 for ch in joined if ch == "#" or ch in _MAP_ALIASES)
    return walls >= len(rows)


def _bare_grid_spans(lines: list[str]) -> list[tuple[int, int]]:
    """Tutti gli span [start,end) contigui che qualificano come mappa nuda,
    in ordine di apparizione nel testo."""
    spans: list[tuple[int, int]] = []
    cur_start: int | None = None
    for i, ln in enumerate(lines):
        if _looks_like_map_row(ln):
            if cur_start is None:
                cur_start = i
        else:
            if cur_start is not None:
                if _bare_grid_span_qualifies(lines, cur_start, i):
                    spans.append((cur_start, i))
                cur_start = None
    if cur_start is not None and _bare_grid_span_qualifies(lines, cur_start, len(lines)):
        spans.append((cur_start, len(lines)))
    return spans


def _find_bare_grid_span(lines: list[str]) -> tuple[int, int] | None:
    """Indici [start, end) dell'ULTIMA mappa nuda qualificante in `lines`
    (None se nessuna). 'Ultima' e non 'più lunga': se il DM disegna più
    griglie nude — la vecchia poi quella aggiornata — conta solo l'ultima
    generata. Condiviso da estrazione e rimozione così restano coerenti."""
    spans = _bare_grid_spans(lines)
    return spans[-1] if spans else None


def _extract_bare_grid(text: str) -> str | None:
    """Riconosce una mappa ASCII NUDA (senza marcatori MAP_START/<MAP>),
    eventualmente dentro un blocco ``` ```. Restituisce la griglia
    normalizzata, o None se non c'è un blocco abbastanza simile a una mappa."""
    if not text:
        return None
    # Pulisci PRIMA le righe (numeri di riga, marcatori di lista, toolbar):
    # così una griglia "sporca" dentro un fence viene comunque riconosciuta.
    lines = [_clean_map_line(ln)
             for ln in text.replace("```", "").splitlines()
             if not _RE_MAP_TOOLBAR.match(ln)]
    span = _find_bare_grid_span(lines)
    if not span:
        return None
    rows = [lines[k].rstrip() for k in range(span[0], span[1])]
    # GREZZO: niente normalizzazione — disegniamo i caratteri del modello
    # così come sono (la sprite per ogni tile la definisce il DM).
    return "\n".join(rows)


def _strip_bare_grid(text: str) -> str:
    """Toglie dalla narrazione visibile una mappa ASCII NUDA (senza
    marcatori) e gli eventuali code-fence ``` che la racchiudono — così la
    griglia finisce SOLO nel riquadro mappa, non nel testo della chat."""
    if not text:
        return text
    # 1) code-fence il cui contenuto è una mappa → via tutto il blocco
    def _fence_repl(m: "re.Match") -> str:
        inner = m.group(1)
        return "" if _find_bare_grid_span(inner.splitlines()) else m.group(0)
    text = re.sub(r"```[^\n]*\n([\s\S]*?)```", _fence_repl, text)
    # 2) griglia/e nuda/e non recintata/e → togli TUTTE le loro righe (non
    #    solo l'ultima): se il DM ha disegnato sia la vecchia sia la nuova,
    #    nessuna deve restare nel testo della chat. Cancella dall'ultimo
    #    span al primo così gli indici non si spostano.
    lines = text.splitlines()
    for span in reversed(_bare_grid_spans(lines)):
        del lines[span[0]:span[1]]
    text = "\n".join(lines)
    return text


# Set di caratteri di mappa CANONICI: tutto ciò che non è in questo set
# viene normalizzato secondo _MAP_ALIASES (o cade su muro '#' di default).
# Lo spazio resta spazio (fog).
_CANON_MAP_CHARS = set("#.*@CESX+<>~Tt,of=$MgkDP ")

# Alias comuni che i modelli usano spontaneamente: caratteri box-drawing,
# lettere decorative, varianti di muro. Normalizziamo al set canonico in
# modo intelligente invece di trattare TUTTO come muro: una porta '|' può
# essere voluta come passaggio verticale, un '+' negli incroci box-drawing
# è ambiguo (porta o angolo di muro?) — qui scegliamo l'interpretazione
# più conservativa: tutto il box-drawing diventa muro '#', le lettere
# decorative comuni vengono mappate sul tile pixel-art più simile.
_MAP_ALIASES = {
    # box-drawing semplici → muro
    "|": "#", "-": "#", "_": "#", "/": "#", "\\": "#",
    # box-drawing Unicode → muro (cornici di stanza, corridoi, angoli)
    "═": "#", "║": "#", "╔": "#", "╗": "#", "╚": "#", "╝": "#",
    "╠": "#", "╣": "#", "╦": "#", "╩": "#", "╬": "#",
    "─": "#", "│": "#", "┌": "#", "┐": "#", "└": "#", "┘": "#",
    "├": "#", "┤": "#", "┬": "#", "┴": "#", "┼": "#",
    "━": "#", "┃": "#", "┏": "#", "┓": "#", "┗": "#", "┛": "#",
    "█": "#", "▓": "#", "▒": "#", "░": ".",
    # lettere decorative spesso usate come bordi
    "A": "#", "B": "#", "W": "#", "H": "#",
    # alias di tile narrativi
    "b": "$", "c": "C", "e": "E", "s": "S", "x": "X",
    "m": "M", "p": "P",  # mostro/PG in minuscolo
    "·": ".", "•": ".", "◦": ".",
    "O": "o",  # O grande → masso
    # Cifre attaccate a un tile (etichette tipo S1, S2, M1): il DM numera
    # i tile per distinguere più PNG/mostri, ma le cifre NON sono canoniche
    # e diventerebbero MURI in mezzo al pavimento, bucando la mappa. Le
    # trattiamo come pavimento '.' (neutro), così "S2" → "S." e la
    # geometria resta intatta. ('0' = masso 'o' per retro-compatibilità.)
    "0": "o",
    "1": ".", "2": ".", "3": ".", "4": ".",
    "5": ".", "6": ".", "7": ".", "8": ".", "9": ".",
    # acqua e simboli
    "≈": "~", "∼": "~",
    # PNG/social emoji-like
    "?": "E",
    # carro/oggetto generico → masso
    "%": "o",
}


def _normalize_map_chars(block: str) -> str:
    """Converte i caratteri non canonici nei tile pixel-art conosciuti.

    Strategia: 1) prima passa attraverso _MAP_ALIASES (es. box-drawing →
    '#', '0'/'O' → 'o' masso, 'b' → '$' tesoro); 2) tutto ciò che non è
    né canonico né alias → '#' (muro), così la mappa resta coerente anche
    quando il modello usa simboli imprevisti per delimitare le pareti.
    Spazi → spazi (fog/area non disegnata)."""
    if not block:
        return block
    out_lines: list[str] = []
    for line in block.splitlines():
        chars = []
        for ch in line:
            if ch in _CANON_MAP_CHARS:
                chars.append(ch)
            elif ch in _MAP_ALIASES:
                chars.append(_MAP_ALIASES[ch])
            else:
                chars.append("#")
        out_lines.append("".join(chars))
    return "\n".join(out_lines)


def extract_music(text: str) -> list[dict]:
    """Tutti i <MUSIC> come lista di dict: colonne sonore generate dal DM."""
    out: list[dict] = []
    for m in RE_MUSIC.finditer(text):
        out.extend(_as_dicts(_safe_json(m.group(1))))
    return out


# Palette pixel-art a 16 colori: ogni pixel è UNA cifra esadecimale.
_HEX = set("0123456789abcdef")


def _normalize_grid(rows: Any, size: int) -> list[str] | None:
    """Normalizza una griglia pixel-art a `size`×`size` cifre esadecimali
    (palette 16 colori). Righe/colonne mancanti riempite con '0', eccedenze
    tagliate, caratteri non validi → '0'. None se `rows` non è una lista."""
    if not isinstance(rows, list):
        return None
    grid: list[str] = []
    for i in range(size):
        row = str(rows[i]).lower() if i < len(rows) else ""
        row = "".join(c if c in _HEX else "0" for c in row)[:size]
        grid.append(row.ljust(size, "0"))
    return grid


def extract_sprites(text: str) -> dict[str, list[str]]:
    """Tutti i <SPRITE> come dict {id: griglia pixel-art quadrata}. Il DM
    disegna in pixel-art (palette fantasy 16 colori, cifre esadecimali)
    gli elementi della scena; `id` è il carattere di cella che la sprite
    raffigura.

    Dimensione preferita: 16×16 (16 righe da 16 cifre). Accetta anche
    sprite più piccole (8–16 per lato) per tolleranza coi modelli che
    emettono 10×10 in stile vecchio: il renderer le scala su 16×16 via
    nearest-neighbor."""
    out: dict[str, list[str]] = {}

    def _add(d: dict) -> None:
        sid = str(d.get("id") or "").strip()
        rows = d.get("rows")
        # Dimensione naturale = numero di righe fornite, clampato in
        # [8, 16]. Default 16 se mancante o non lista.
        if isinstance(rows, list) and rows:
            natural = max(8, min(16, len(rows)))
        else:
            natural = 16
        grid = _normalize_grid(rows, natural)
        if sid and grid:
            out[sid] = grid

    # 1) tag <SPRITE>{…}</SPRITE> (formato storico, ovunque nel testo)
    for m in RE_SPRITE.finditer(text):
        for d in _as_dicts(_safe_json(m.group(1))):
            _add(d)
    # 2) oggetti {…} "nudi" dentro la sezione SPRITE_START…SPRITE_END
    #    (nuovo formato a tre blocchi: il DM può elencarli senza <SPRITE>).
    sec = RE_SPRITE_SEC.search(text)
    if sec:
        body = sec.group(1)
        for mo in RE_SPRITE_OBJ.finditer(body):
            d = _safe_json(mo.group(0))
            if isinstance(d, dict):
                _add(d)
    return out


# Riga di legenda con separatore ESPLICITO `=`/`:` fra carattere ed
# etichetta (es. `* = Partenza`, `# : Muro`). Provata PER PRIMA, così un
# carattere-chiave che è anche un marcatore di lista (`*`, `>`, `-`) non
# viene scambiato per un bullet.
_RE_LEGEND_SEP  = re.compile(r"^(\S)\s*[=:]\s*(.*)$")
# Forma senza separatore (`X Nome`): il carattere, spazio, poi l'etichetta.
_RE_LEGEND_BARE = re.compile(r"^(\S)\s+(.+)$")
# Bullet/citazione iniziale da togliere SOLO se la riga non è già una
# coppia `chiave = etichetta` valida.
_RE_LEGEND_BULLET = re.compile(r"^[\-\*•·>]\s+")


def extract_legend(text: str) -> list[dict]:
    """Legenda mappa dal blocco LEGENDA_START…LEGENDA_END: una riga per
    carattere di cella, nella forma `X = Nome` (accetta anche `X: Nome`,
    `X - Nome`, `X Nome`). Restituisce [{char, label}] nell'ordine emesso.
    Lista vuota se il blocco manca o è illeggibile."""
    m = RE_LEGEND.search(text)
    if not m:
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for ln in m.group(1).splitlines():
        s = ln.strip()
        if not s:
            continue
        # 1) coppia `chiave = etichetta`: provata PRIMA di togliere bullet,
        #    così un carattere-chiave che è anche un bullet (* > -) resta
        #    la chiave e non viene mangiato.
        mm = _RE_LEGEND_SEP.match(s)
        # 2) se non c'è separatore esplicito, togli UN bullet iniziale e
        #    riprova (`- # = Muro`), poi accetta la forma `X Nome`.
        if not mm:
            s = _RE_LEGEND_BULLET.sub("", s).strip()
            mm = _RE_LEGEND_SEP.match(s) or _RE_LEGEND_BARE.match(s)
        if not mm:
            continue
        ch = mm.group(1)
        label = mm.group(2).strip().strip("*`_")
        if ch and ch not in seen:
            seen.add(ch)
            out.append({"char": ch, "label": label})
    return out


# Lato dell'illustrazione di scena (griglia pixel-art quadrata).
SCENE_SIZE = 64


def extract_scene(text: str) -> dict | None:
    """L'ULTIMO <SCENE> del testo come dict {rows: griglia 64×64,
    caption: str}. Il DM disegna in pixel-art un'illustrazione della
    situazione di gioco corrente (non la mappa dall'alto: la scena in
    prospettiva). None se assente o malformato."""
    last: Any = None
    for m in RE_SCENE.finditer(text):
        d = _safe_json(m.group(1))
        if isinstance(d, dict):
            last = d
    if not isinstance(last, dict):
        return None
    grid = _normalize_grid(last.get("rows"), SCENE_SIZE)
    if not grid:
        return None
    return {"rows": grid, "caption": str(last.get("caption") or "").strip()}


def extract_roll_requests(text: str) -> list[dict]:
    """Tutti i <ROLL_REQ> come lista di dict payload (gestisce anche array)."""
    out: list[dict] = []
    for m in RE_ROLL_REQ.finditer(text):
        out.extend(_as_dicts(_safe_json(m.group(1))))
    return out


def extract_spell_casts(text: str) -> list[dict]:
    """Tutti i <SPELL_CAST> come lista di dict {by, spell, level}: il DM
    li emette quando un PG/PNG lancia un incantesimo, così il sistema può
    scalare lo slot corrispondente sulla scheda."""
    out: list[dict] = []
    for m in RE_SPELL_CAST.finditer(text):
        out.extend(_as_dicts(_safe_json(m.group(1))))
    return out


# ────────────────────────────────────────────────────────────────────────
# Risoluzione ROLL_REQ
# ────────────────────────────────────────────────────────────────────────

def resolve_roll_requests(
    text: str, human_names: list[str] | None = None,
    default_human: str | None = None,
) -> tuple[str, list[dict], list[dict]]:
    """
    Esegue i <ROLL_REQ> del testo. I tiri il cui campo `by` corrisponde a un
    PG UMANO NON vengono eseguiti: spettano al giocatore (riquadro dadi).

    `default_human`: nome del PG umano a cui attribuire un <ROLL_REQ> SENZA
    campo `by` (il DM dovrebbe sempre metterlo — è OBBLIGATORIO — ma a volte
    lo scorda). Senza questo ripiego un tiro non etichettato veniva tirato
    dal SISTEMA anche quando toccava al giocatore: il chiamante lo passa solo
    quando il tiro è chiaramente di un umano (suo turno, o unico umano senza
    PG IA), così a lanciarlo è il giocatore. Un `by` NON vuoto ma ignoto resta
    tirato dal sistema (è quasi sempre un mostro/PNG).

    Restituisce:
      - testo con i tag sostituiti dal risultato inline leggibile
      - lista dei risultati eseguiti (dict) per il log e la conversazione
      - lista dei tiri in attesa (payload dict) che deve tirare il giocatore
    """
    # lower → nome canonico: serve sia per il match sia per normalizzare
    # il campo `by` dei pending (il frontend e /api/roll confrontano per
    # nome, devono ricevere il nome ESATTO della scheda).
    human = {(n or "").strip().lower(): (n or "").strip()
             for n in (human_names or []) if n}
    results: list[dict] = []
    pending: list[dict] = []
    halt_added = [False]   # nonlocal flag: sentinel posato una sola volta

    def _human_canonical(by: str) -> str | None:
        """Nome canonico del PG umano se `by` lo identifica, altrimenti None.
        Match esatto (case-insensitive) oppure nome umano come parola intera
        dentro `by`: il DM a volte decora il nome («Efi (Guerriera)»,
        «la maga Lyra») e il tiro finiva tirato dal sistema invece che
        proposto al giocatore."""
        b = (by or "").strip().lower()
        if not b:
            return None
        if b in human:
            return human[b]
        for low, canon in human.items():
            if low and re.search(rf"(?<!\w){re.escape(low)}(?!\w)", b):
                return canon
        return None

    def _sub(match: re.Match) -> str:
        payloads = _as_dicts(_safe_json(match.group(1)))
        if not payloads:
            return f"[ROLL_REQ malformata: {match.group(1)[:60]}]"
        parts: list[str] = []
        saw_human = False
        for payload in payloads:
            by = (payload.get("by") or "").strip()
            # `by` vuoto + tiro chiaramente di un umano → lo lancia lui, non
            # il sistema. `by` valorizzato ma ignoto → resta al sistema.
            canon = _human_canonical(by) or (default_human if not by else None)
            if canon:
                # tiro di un PG umano: lo lancia il giocatore, non il sistema.
                # NON aggiungiamo testo "tira XdY" inline nella narrazione:
                # il riquadro dadi del frontend mostra già nome, espressione
                # e ragione del tiro. Duplicarlo in chat era ridondante.
                payload["by"] = canon
                pending.append(payload)
                saw_human = True
                continue
            try:
                r = rules.parse_roll_request(payload)
            except ValueError as e:
                parts.append(f"[ROLL_REQ errore: {e}]")
                continue
            info = r.to_dict()
            # campi extra utili al log
            info["target"] = payload.get("target")
            info["by"] = payload.get("by")
            results.append(info)
            parts.append(_format_roll_inline(r, payload))
        out = " ".join(parts)
        # Sentinel dopo il PRIMO ROLL_REQ umano: la narrazione successiva
        # viene tagliata da strip_narrative (il DM tende ad anticipare
        # l'esito, ma il giocatore non ha ancora tirato).
        if saw_human and not halt_added[0]:
            halt_added[0] = True
            out += ("\n" if out else "") + HALT_HUMAN_ROLL
        return out

    new_text = RE_ROLL_REQ.sub(_sub, text)
    return new_text, results, pending


def _format_roll_pending(payload: dict) -> str:
    """Messaggio inline per un tiro che deve lanciare il giocatore umano."""
    by = payload.get("by") or "Giocatore"
    dice = payload.get("dice") or payload.get("expr") or "1d20"
    reason = payload.get("reason") or ""
    target = payload.get("target")
    tag = ""
    if payload.get("advantage"):
        tag = " con vantaggio"
    elif payload.get("disadvantage"):
        tag = " con svantaggio"
    extras = []
    if reason:
        extras.append(reason)
    if target:
        extras.append(f"vs {target}")
    suffix = f" — *{' · '.join(extras)}*" if extras else ""
    return f"**🎲 {by}, tira {dice}{tag}** dal riquadro dadi{suffix}"


def _format_roll_inline(r: rules.RollResult, payload: dict) -> str:
    by = payload.get("by")
    target = payload.get("target")
    parts = [f"🎲 {r.expr}"]
    if r.advantage:
        parts.append(" (vant)")
    if r.disadvantage:
        parts.append(" (svant)")
    parts.append(f" = **{r.total}**")
    if r.is_crit:
        parts.append(" ✦CRITICO")
    if r.is_fumble:
        parts.append(" ✗FUMBLE")
    extras = []
    if r.reason:
        extras.append(r.reason)
    if by:
        extras.append(f"da {by}")
    if target:
        extras.append(f"vs {target}")
    if extras:
        parts.append(f" — *{' · '.join(extras)}*")
    rolls_s = "+".join(str(x) for x in r.rolls)
    mod_s = f"{r.modifier:+d}" if r.modifier else ""
    parts.append(f" [{rolls_s}{mod_s}]")
    return "".join(parts)


# ────────────────────────────────────────────────────────────────────────
# Applicazione a game_state
# ────────────────────────────────────────────────────────────────────────

# Campi di game_state che il DM NON può sovrascrivere via STATE_UPDATE.
# Sono sotto il controllo del sistema (sprite e mappa derivano dai tag
# dedicati, i player dalle schede generate, i pending_* dai cicli interni,
# l'avventura dal flusso di caricamento). Evitare il clobber accidentale:
# un payload con queste chiavi viene ignorato silenziosamente per quei
# campi, gli altri campi dell'update vengono applicati normalmente.
_STATE_UPDATE_BLOCKED = frozenset({
    "players",
    "rolls_log",
    "revealed_tiles",
    "map_base", "map_full", "map_ascii",
    "map_width", "map_height",
    "sprites", "music",
    "pending_rolls", "pending_roll_feedback", "pending_dm_notes",
    "adventure_beats", "adventure_index", "adventure_loaded",
    "session_start",
    # Flag di servizio: gestiti separatamente in app.py prima dell'apply
    # (vedi apply_long_rest). Non vanno mai a finire dentro game_state.
    "long_rest", "session_end",
})


def _filter_hp_update(sheet_hp: dict, hp_upd: dict,
                      message_id: str | None = None) -> dict:
    """Filtra un aggiornamento HP in arrivo dal DM per evitare il "reset a
    HP pieni" che il modello tende a inviare a ogni messaggio anche quando
    il PG non ha preso danni né è stato curato.

    Regole base:
      • hp.damage:N → sottrae N agli HP correnti (clamp a 0).
      • hp.heal:N   → aggiunge N (clamp al max).
      • hp.current  → applicato SOLO se è strettamente inferiore agli HP
                      correnti (= ferita reale) oppure se l'update porta
                      anche hp.max (es. level-up o cambio CON COS). Un
                      hp.current uguale o superiore al valore in scheda,
                      senza max esplicito, viene SCARTATO.
      • hp.max / hp.temp passano sempre.

    Dedup anti-drift fra messaggi (`message_id` = id del messaggio DM):
      1) Se HP è già stato modificato per QUESTO PG in QUESTO messaggio
         (più CHAR_UPDATE per lo stesso PG nello stesso turno), gli
         update HP successivi vengono ignorati: passano solo max/temp.
      2) Se il DM rispedisce gli STESSI (damage, heal) di un evento già
         applicato e l'HP corrente coincide con quello LASCIATO da
         quell'evento (= nulla è cambiato nel frattempo), è una
         ripetizione narrativa del DM: la richiesta viene SCARTATA per
         evitare che il PG subisca lo stesso danno a ogni messaggio.

    Restituisce un nuovo dict con SOLO i campi HP che vanno applicati
    (incluso il book-keeping `_last_*` per il dedup successivo)."""
    if not isinstance(hp_upd, dict):
        return {}
    if not isinstance(sheet_hp, dict):
        sheet_hp = {}
    # Dedup 1: stesso messaggio DM già processato per HP di questo PG.
    if (message_id is not None
            and sheet_hp.get("_last_msg_id") == message_id):
        passthrough: dict = {}
        for k in ("max", "temp"):
            if k in hp_upd:
                passthrough[k] = hp_upd[k]
        return passthrough

    cleaned: dict = {}
    cur_sheet = sheet_hp.get("current")
    max_sheet = sheet_hp.get("max")
    # passa-through di campi non-current
    for k in ("max", "temp"):
        if k in hp_upd:
            cleaned[k] = hp_upd[k]
    # max esplicito → consenti anche current (level-up o ricalcolo voluto)
    has_explicit_max = "max" in hp_upd
    # 1) deltas damage/heal (preferiti — meno fragili)
    dmg = hp_upd.get("damage")
    heal = hp_upd.get("heal")
    if dmg is not None or heal is not None:
        # Dedup 2: ripetizione di un evento già applicato (damage/heal
        # identici, HP non cambiato dall'ultima applicazione).
        last_dmg   = sheet_hp.get("_last_damage")
        last_heal  = sheet_hp.get("_last_heal")
        last_after = sheet_hp.get("_last_after")
        if (last_after is not None
                and last_dmg == dmg and last_heal == heal
                and cur_sheet == last_after):
            return cleaned
        try:
            base = int(cur_sheet if cur_sheet is not None
                       else (max_sheet if max_sheet is not None else 0))
            delta = -int(dmg or 0) + int(heal or 0)
            new_cur = base + delta
            mx = int(cleaned.get("max", max_sheet or 0) or 0)
            if mx > 0:
                new_cur = min(mx, new_cur)
            new_cur = max(0, new_cur)
            cleaned["current"]     = new_cur
            cleaned["_last_msg_id"] = message_id
            cleaned["_last_damage"] = dmg
            cleaned["_last_heal"]   = heal
            cleaned["_last_after"]  = new_cur
        except (TypeError, ValueError):
            pass
        return cleaned
    # 2) hp.current assoluto: filtro anti-drift
    if "current" in hp_upd:
        try:
            new_cur = int(hp_upd["current"])
        except (TypeError, ValueError):
            return cleaned
        # Senza valore in scheda non c'è confronto: accetta (init/migration).
        if cur_sheet is None:
            cleaned["current"]      = new_cur
            cleaned["_last_msg_id"] = message_id
            cleaned["_last_after"]  = new_cur
        else:
            try:
                cur_int = int(cur_sheet)
            except (TypeError, ValueError):
                cur_int = new_cur
            # Diminuzione = ferita reale, sempre ammessa.
            if new_cur < cur_int:
                cleaned["current"]     = new_cur
                cleaned["_last_msg_id"] = message_id
                cleaned["_last_damage"] = None
                cleaned["_last_heal"]   = None
                cleaned["_last_after"]  = new_cur
            elif new_cur > cur_int:
                # Aumento di hp.current: distinguiamo una CURA reale dal
                # "reset a HP pieni" che il DM tende a rispedire a ogni
                # messaggio. Max effettivo per il confronto.
                try:
                    mx_eff = int(cleaned["max"]) if "max" in cleaned else (
                        int(max_sheet) if max_sheet is not None else None)
                except (TypeError, ValueError):
                    mx_eff = None
                accept = False
                # 1) Level-up vero: max esplicito che sale → accetta.
                if has_explicit_max:
                    try:
                        new_max = int(hp_upd["max"])
                        old_max = int(max_sheet) if max_sheet is not None else 0
                        if new_max > old_max:
                            accept = True
                    except (TypeError, ValueError):
                        pass
                # 2) Cura PARZIALE: il nuovo valore resta SOTTO il massimo →
                # è un aumento reale, non un reset a fondo barra. Senza il
                # max noto non possiamo distinguerli: in tal caso scartiamo
                # (comportamento prudente di prima). Un current == max senza
                # max esplicito resta bloccato (è il reset spurio).
                if not accept and mx_eff is not None and new_cur < mx_eff:
                    accept = True
                if accept:
                    cleaned["current"]     = new_cur
                    cleaned["_last_msg_id"] = message_id
                    cleaned["_last_damage"] = None
                    cleaned["_last_heal"]   = None
                    cleaned["_last_after"]  = new_cur
    return cleaned


def apply_state_update(state: dict, update: dict,
                       message_id: str | None = None) -> None:
    """Aggiorna game_state con i campi presenti nello STATE_UPDATE.

    Filtra i campi sotto controllo del sistema (vedi _STATE_UPDATE_BLOCKED)
    così che un DM disattento — o un payload accidentalmente ricco — non
    possa azzerare i player, la fog, gli sprite o la coda dei pending.

    `message_id` è l'id univoco del messaggio DM corrente: viene passato
    al filtro HP per deduplicare ripetizioni (vedi _filter_hp_update).
    """
    if not isinstance(update, dict) or not update:
        return
    # players_hp è un caso speciale: aggiorna sheet di ogni player.
    # Passa dal filtro anti-drift: un DM che rispedisce HP pieni a ogni
    # messaggio NON deve poter "curare" il party in automatico.
    if "players_hp" in update:
        for p in state.get("players", []):
            name = p.get("name", "")
            if name in update["players_hp"]:
                hp = update["players_hp"][name]
                if isinstance(hp, dict):
                    sheet = p.setdefault("sheet", {})
                    sheet_hp = sheet.setdefault("hp", {})
                    cleaned = _filter_hp_update(sheet_hp, hp, message_id)
                    if cleaned:
                        sheet_hp.update(cleaned)
                    if sheet_hp.get("current", 1) <= 0:
                        sheet["status"] = "down"
                    else:
                        sheet["status"] = "alive"
        update = {k: v for k, v in update.items() if k != "players_hp"}
    # Whitelist: applica solo le chiavi non protette.
    safe = {k: v for k, v in update.items() if k not in _STATE_UPDATE_BLOCKED}
    # active_player: normalizza al nome ESATTO della scheda. Il DM scrive
    # spesso il nome con maiuscole diverse o decorato («la maga Lyra»): il
    # frontend confronta per nome per decidere se è il turno del giocatore
    # umano (riquadro dadi «Tocca a te») e un mismatch lo mostrava come IA.
    ap = safe.get("active_player")
    if isinstance(ap, str) and ap.strip():
        ap_low = ap.strip().lower()
        for p in state.get("players", []):
            name = (p.get("name") or "").strip()
            if not name:
                continue
            low = name.lower()
            if low == ap_low or re.search(
                    rf"(?<!\w){re.escape(low)}(?!\w)", ap_low):
                safe["active_player"] = name
                break
    state.update(safe)


def _deep_merge(dst: dict, src: dict) -> None:
    """Fonde `src` dentro `dst` in-place. I dict annidati (hp, stats,
    death_saves) vengono uniti campo per campo invece di essere sostituiti:
    un CHAR_UPDATE parziale come {"hp":{"current":8}} NON cancella hp.max."""
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_merge(dst[k], v)
        else:
            dst[k] = v


def apply_char_updates(state: dict, updates: list[dict],
                       on_persist: Any = None,
                       message_id: str | None = None) -> None:
    """Aggiorna o inserisce le schede personaggio. Se `on_persist` callable,
    viene chiamato con ogni scheda completa (utile per upsert su file).

    Il merge è profondo: un update parziale del DM (es. solo i danni su
    hp.current) NON azzera gli altri campi della scheda né hp.max.

    Dopo il merge ricalcola i valori derivati (mod caratteristiche, CD/bonus
    incantesimi) tramite `dnd.character.recompute_derived` così la scheda
    visualizzata resta sempre consistente quando il DM modifica score, level
    o slot."""
    # Import lazy per evitare ciclo (dnd.character usa solo state via
    # game_state — il parser è agnostico al dominio).
    from dnd.character import (apply_level_progression, recompute_derived,
                               sync_bases_from_update)

    for char in updates:
        if not isinstance(char, dict):
            continue
        name = (char.get("name") or "").strip()
        if not name:
            continue

        # Filtra l'HP in arrivo prima del merge: blocca i reset a HP pieni
        # che il DM tende a inviare a ogni messaggio. Solo damage/heal
        # espliciti o un current STRETTAMENTE in calo modificano hp.current.
        player = next(
            (p for p in state.get("players", [])
             if (p.get("name") or "").lower() == name.lower()),
            None,
        )
        existing_hp = (player.get("sheet", {}).get("hp")
                       if isinstance(player, dict) else None)
        if isinstance(char.get("hp"), dict):
            char["hp"] = _filter_hp_update(existing_hp or {}, char["hp"],
                                           message_id)
            if not char["hp"]:
                # se il filtro ha svuotato l'update HP, rimuovi la chiave
                # così _deep_merge non sovrascrive con un dict vuoto.
                char.pop("hp", None)

        hp = char.get("hp", {})
        if isinstance(hp, dict):
            cur = hp.get("current")
            if cur is not None and cur <= 0:
                hp["current"] = 0
                char["status"] = "down" if not char.get("death_saves", {}).get("dead") else "dead"
            elif cur is not None and cur > 0:
                char.setdefault("status", "alive")
        if player is None:
            # Nome non presente nel party. Lo aggiungiamo alla lista PG SOLO
            # se il DM lo marca ESPLICITAMENTE come personaggio giocante
            # (player_type "human"/"ai"): es. PNG promosso a PG o PG mancante
            # nel roster iniziale. I nemici/mostri NON vanno nella lista
            # personaggi: vivono solo nella sequenza dei turni
            # (initiative_order, via STATE_UPDATE). Un CHAR_UPDATE su un nome
            # sconosciuto senza player_type esplicito viene quindi ignorato.
            ptype = (char.get("player_type") or "").strip().lower()
            if ptype not in ("human", "ai"):
                continue
            player = {"name": name, "type": ptype, "sheet": {}}
            state.setdefault("players", []).append(player)
        else:
            # Allinea `type` del wrapper se il DM ha esplicitamente cambiato
            # `player_type` (es. da AI a umano o viceversa).
            new_ptype = (char.get("player_type") or "").strip().lower()
            if new_ptype in ("human", "ai"):
                player["type"] = new_ptype
        sheet = player.setdefault("sheet", {})
        _deep_merge(sheet, char)
        # Pipeline post-merge:
        #  1) sync_bases_from_update: se il DM ha scritto direttamente i
        #     valori finali (ac, hp.max, stats.X.score, ...), riallinea i
        #     *_base così apply_item_modifiers non li sovrascrive col vecchio
        #     calcolo ignorando il valore voluto.
        #  2) apply_level_progression: se gli XP superano la soglia, alza
        #     livello, HP max base, PB e rigenera gli slot dell'incantatore
        #     (preservando lo `used`).
        #  3) recompute_derived: ricalcola mod caratteristiche, CD/bonus
        #     incantesimi e applica i bonus degli oggetti sintonizzati.
        try:
            sync_bases_from_update(sheet, char)
            apply_level_progression(sheet)
            recompute_derived(sheet)
        except Exception as e:
            print(f"[parser] recompute_derived fallito per {name}: {e}", flush=True)

        if callable(on_persist):
            try:
                # persiste la scheda COMPLETA fusa, non l'update parziale
                on_persist(dict(sheet))
            except Exception as e:
                print(f"[parser] persist fallita per {name}: {e}", flush=True)


def apply_spell_casts(state: dict, casts: list[dict]) -> list[dict]:
    """Consuma uno slot incantesimo per ogni <SPELL_CAST> di livello ≥ 1.

    I trucchetti (livello 0) sono a volontà: NON consumano slot.
    Restituisce un report con un dict per ogni lancio:
      {by, spell, level, ok, detail}
    `ok=False` se il PG non esiste, non è incantatore, non ha uno slot di
    quel livello, oppure ha già esaurito gli slot di quel livello — in tal
    caso lo slot NON viene scalato e il DM va avvisato (l'incantesimo non
    può essere lanciato)."""
    results: list[dict] = []
    if not casts:
        return results
    for c in casts:
        if not isinstance(c, dict):
            continue
        by = (c.get("by") or c.get("name") or c.get("character") or "").strip()
        spell = (c.get("spell") or c.get("name") or "").strip()
        try:
            level = int(c.get("level", 0) or 0)
        except (TypeError, ValueError):
            level = 0
        rec = {"by": by, "spell": spell, "level": level, "ok": True, "detail": ""}
        if level <= 0:
            rec["detail"] = "trucchetto — a volontà, nessuno slot consumato"
            results.append(rec)
            continue
        player = next(
            (p for p in state.get("players", [])
             if (p.get("name") or "").lower() == by.lower()),
            None,
        )
        if player is None:
            rec["ok"] = False
            rec["detail"] = "personaggio non trovato"
            results.append(rec)
            continue
        sheet = player.setdefault("sheet", {})
        spells = sheet.get("spells")
        slots = spells.get("slots") if isinstance(spells, dict) else None
        if not isinstance(slots, dict) or not slots:
            rec["ok"] = False
            rec["detail"] = "il personaggio non è un incantatore"
            results.append(rec)
            continue
        sl = slots.get(str(level))
        if not isinstance(sl, dict):
            rec["ok"] = False
            rec["detail"] = f"nessuno slot di livello {level}"
            results.append(rec)
            continue
        mx = max(0, int(sl.get("max", 0) or 0))
        used = max(0, int(sl.get("used", 0) or 0))
        if used >= mx:
            rec["ok"] = False
            rec["detail"] = f"slot di livello {level} esauriti ({used}/{mx})"
            results.append(rec)
            continue
        sl["used"] = used + 1
        rec["detail"] = f"slot L{level} consumato — restano {mx - sl['used']}/{mx}"
        results.append(rec)
    return results


def apply_long_rest(state: dict, on_persist=None) -> list[str]:
    """Riposo lungo (o fine sessione): per ogni PG ripristina HP al massimo,
    azzera HP temporanei, resetta i death-saves e azzera tutti gli slot
    incantesimo usati.

    Esclude i PG marcati morti permanenti (death_saves.dead). Cancella anche
    il book-keeping anti-drift dell'HP (vedi `_filter_hp_update`) così il
    primo CHAR_UPDATE post-riposo riparte da una scheda pulita.

    `on_persist(sheet)` viene chiamato per ogni scheda aggiornata (utile per
    salvare la scheda su personaggi.json / runtime/personaggi/).

    Restituisce la lista dei nomi PG su cui il riposo è stato applicato.
    """
    refreshed: list[str] = []
    for p in state.get("players", []):
        if not isinstance(p, dict):
            continue
        sheet = p.get("sheet")
        if not isinstance(sheet, dict):
            continue
        ds = sheet.get("death_saves") if isinstance(sheet.get("death_saves"), dict) else None
        if ds and ds.get("dead"):
            continue
        name = (sheet.get("name") or p.get("name") or "").strip()

        # HP → pieni
        hp = sheet.setdefault("hp", {})
        if isinstance(hp, dict):
            mx = hp.get("max")
            try:
                mx_int = int(mx or 0)
            except (TypeError, ValueError):
                mx_int = None
            if mx_int is not None and mx_int > 0:
                hp["current"] = mx_int
            hp["temp"] = 0
            for k in ("_last_msg_id", "_last_damage", "_last_heal", "_last_after"):
                hp.pop(k, None)

        sheet["status"] = "alive"

        # Death saves → azzerati (solo i contatori, non il flag "dead"
        # che già escluderebbe il PG sopra).
        if isinstance(ds, dict):
            ds["successes"] = 0
            ds["failures"] = 0

        # Slot incantesimo → tutti gli used a 0.
        spells = sheet.get("spells")
        if isinstance(spells, dict):
            slots = spells.get("slots")
            if isinstance(slots, dict):
                for sl in slots.values():
                    if isinstance(sl, dict):
                        sl["used"] = 0

        refreshed.append(name)
        if callable(on_persist):
            try:
                on_persist(dict(sheet))
            except Exception as e:
                print(f"[parser] persist long_rest fallita per {name}: {e}",
                      flush=True)
    return refreshed


# Mood validi per il motore musicale (slot rimpiazzabili dal DM).
_MUSIC_MOODS = {"menu", "generation", "explore", "social", "combat", "boss"}
# 5 canali melodici (pad/bass/lead/arp/pluck) + 5 ritmici
# (kick/snare/hats/clap/tom) = 10 canali. `drum` resta accettato come forma
# legacy (batteria su un canale solo).
_MUSIC_MELODIC = ("pad", "bass", "lead", "arp", "pluck")
_MUSIC_RHYTHM  = ("kick", "snare", "hats", "clap", "tom", "drum")
_MUSIC_FIELDS = ("bpm", "wave", "cutoff", "gain") + _MUSIC_MELODIC + _MUSIC_RHYTHM
_MUSIC_WAVES = ("sine", "triangle", "sawtooth", "square")
# Range tollerati dal motore audio. Valori fuori vengono clampati così il
# modello non può rompere la sintesi mandando numeri estremi.
_MUSIC_RANGES = {
    "bpm":    (40, 160),
    "cutoff": (300, 2000),
    "gain":   (0.3, 0.9),
}


def _clamp_num(val, lo, hi, default):
    """Restituisce val clampato in [lo, hi]; default se non numerico."""
    try:
        n = float(val)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def _sanitize_tune(u: dict) -> dict:
    """Pulisce una tune dal DM: clampa bpm/cutoff/gain, valida wave,
    stringifica pad/bass/lead/drum. Campi mancanti restano fuori dal
    dict così il frontend eredita dal mood base."""
    tune: dict = {}
    if "bpm" in u:
        tune["bpm"] = int(_clamp_num(u["bpm"], *_MUSIC_RANGES["bpm"], default=60))
    if "cutoff" in u:
        tune["cutoff"] = int(_clamp_num(u["cutoff"], *_MUSIC_RANGES["cutoff"], default=820))
    if "gain" in u:
        tune["gain"] = round(_clamp_num(u["gain"], *_MUSIC_RANGES["gain"], default=0.6), 2)
    if "wave" in u:
        w = str(u["wave"] or "").strip().lower()
        if w in _MUSIC_WAVES:
            tune["wave"] = w
    for field in _MUSIC_MELODIC + _MUSIC_RHYTHM:
        if field in u and u[field] is not None:
            v = str(u[field]).strip()
            # Strip wrapper <…> erroneo sui canali ritmici (sono UNA battuta).
            if field in _MUSIC_RHYTHM and v.startswith("<") and v.endswith(">"):
                v = v[1:-1].strip()
            tune[field] = v
    return tune


def apply_music_update(state: dict, updates: list[dict]) -> None:
    """Registra le colonne sonore generate dal DM in `state['music']`,
    indicizzate per mood. Il frontend le passa al motore audio generativo.

    Ogni <MUSIC> rimpiazza lo slot del mood indicato (default: 'explore').
    I parametri numerici (bpm/cutoff/gain) sono clampati ai range
    accettati dal sintetizzatore così il DM non può rompere l'audio
    mandando numeri estremi."""
    if not updates:
        return
    music = state.setdefault("music", {})
    for u in updates:
        if not isinstance(u, dict):
            continue
        mood = (u.get("mood") or "explore").strip().lower()
        if mood not in _MUSIC_MOODS:
            mood = "explore"
        tune = _sanitize_tune(u)
        if tune:
            music[mood] = tune


def apply_sprites(state: dict, sprites: dict) -> None:
    """Registra/aggiorna le sprite pixel-art generate dal DM in
    `state['sprites']`, indicizzate per carattere di cella. Il frontend le
    usa per disegnare la mappa (con fallback su una sprite di default)."""
    if not sprites:
        return
    store = state.setdefault("sprites", {})
    for sid, grid in sprites.items():
        if isinstance(grid, list) and grid:
            store[sid] = grid


def apply_legend(state: dict, legend: list[dict]) -> None:
    """Registra la legenda mappa in `state['map_legend']`. Sovrascrive solo
    se il DM ha emesso una legenda non vuota: un ridisegno senza LEGENDA
    mantiene quella precedente (meglio una legenda vecchia che nessuna)."""
    if legend:
        state["map_legend"] = legend


def apply_scene(state: dict, scene: dict | None) -> None:
    """Registra in `state['scene_art']` l'illustrazione pixel-art della
    scena corrente generata dal DM (griglia 32×32 + didascalia)."""
    if scene and scene.get("rows"):
        state["scene_art"] = scene


__all__ = [
    "clean_text", "strip_tags", "strip_narrative",
    "extract_state", "extract_chars", "extract_map", "extract_music",
    "extract_sprites", "extract_scene", "extract_legend", "extract_roll_requests",
    "extract_spell_casts", "resolve_roll_requests",
    "apply_state_update", "apply_char_updates", "apply_spell_casts",
    "apply_long_rest",
    "apply_music_update", "apply_sprites", "apply_legend", "apply_scene", "_deep_merge",
]
