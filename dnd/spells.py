"""
Database incantesimi — sottoinsieme SRD usato dalle schede iniziali.

Ogni voce: `level` (0 = trucchetto, non consuma slot) e `desc` (breve
descrizione in italiano di cosa fa l'incantesimo).

`spell_entry` / `normalize_spell_list` convertono nomi sciolti, stringhe
"Nome | descrizione" o dict parziali in dict strutturati {name, level, desc}
pronti per il blocco incantesimi della scheda personaggio.
"""
from __future__ import annotations


SPELLS: dict[str, dict] = {
    # ── Trucchetti (livello 0, a volontà — NON consumano slot) ──
    "Vicious Mockery":   {"level": 0, "desc": "Insulto magico: TS SAG o 1d6 danni psichici e svantaggio al prossimo tiro per colpire."},
    "Light":             {"level": 0, "desc": "Un oggetto emette luce viva entro 6 m per 1 ora."},
    "Sacred Flame":      {"level": 0, "desc": "Fiamma radiante sul bersaglio: TS DES o 1d8 danni radiosi, ignora copertura."},
    "Guidance":          {"level": 0, "desc": "Tocco benedetto: +1d4 a una prova di caratteristica."},
    "Druidcraft":        {"level": 0, "desc": "Piccoli effetti naturali: prevede il meteo, fa sbocciare un fiore, accende o spegne fuochi."},
    "Produce Flame":     {"level": 0, "desc": "Fiamma in mano: illumina, oppure lanciala come attacco per 1d8 danni da fuoco."},
    "Fire Bolt":         {"level": 0, "desc": "Dardo di fuoco: attacco a distanza, 1d10 danni da fuoco."},
    "Prestidigitation":  {"level": 0, "desc": "Trucchi minori: pulisce o sporca, scalda o raffredda, crea odori o suoni innocui."},
    "Mage Hand":         {"level": 0, "desc": "Mano spettrale che manipola oggetti leggeri fino a 9 m di distanza."},
    "Minor Illusion":    {"level": 0, "desc": "Crea un suono oppure un'immagine illusoria statica."},
    "Eldritch Blast":    {"level": 0, "desc": "Raggio di energia: attacco a distanza, 1d10 danni di forza."},

    # ── Incantesimi di 1° livello (consumano uno slot di livello 1) ──
    "Charm Person":       {"level": 1, "desc": "TS SAG o il bersaglio ti considera un conoscente amichevole per 1 ora."},
    "Cure Wounds":        {"level": 1, "desc": "Tocco curativo: ripristina 2d8 + mod da incantatore punti ferita."},
    "Faerie Fire":        {"level": 1, "desc": "Creature e oggetti in area illuminati: gli attacchi contro di loro hanno vantaggio."},
    "Healing Word":       {"level": 1, "desc": "Cura a distanza con azione bonus: 2d4 + mod da incantatore punti ferita."},
    "Bless":              {"level": 1, "desc": "Fino a 3 creature: +1d4 ai tiri per colpire e ai tiri salvezza (concentrazione)."},
    "Guiding Bolt":       {"level": 1, "desc": "Dardo radiante: attacco, 4d6 danni; il prossimo attacco contro il bersaglio ha vantaggio."},
    "Shield of Faith":    {"level": 1, "desc": "+2 CA a una creatura entro 18 m per 10 minuti (concentrazione)."},
    "Entangle":           {"level": 1, "desc": "Piante afferrano l'area: TS FOR o le creature restano trattenute."},
    "Speak with Animals": {"level": 1, "desc": "Comunichi con le bestie per 10 minuti."},
    "Hunter's Mark":      {"level": 1, "desc": "Marchi un bersaglio: +1d6 danni ai tuoi attacchi contro di esso (concentrazione)."},
    "Magic Missile":      {"level": 1, "desc": "3 dardi di forza infallibili: 1d4+1 danni ciascuno."},
    "Shield":             {"level": 1, "desc": "Reazione: +5 CA fino al prossimo turno, annulla Magic Missile."},
    "Hex":                {"level": 1, "desc": "Maledici un bersaglio: +1d6 danni necrotici dai tuoi attacchi (concentrazione)."},
    "Armor of Agathys":   {"level": 1, "desc": "5 punti ferita temporanei; chi ti colpisce in mischia subisce 5 danni da freddo."},
    "Mage Armor":         {"level": 1, "desc": "CA base 13 + DES per 8 ore su un bersaglio senza armatura."},
    "Detect Magic":       {"level": 1, "desc": "Percepisci presenza e scuola di magia entro 9 m per 10 minuti (concentrazione)."},
    "Burning Hands":      {"level": 1, "desc": "Cono di fuoco: TS DES, 3d6 danni da fuoco."},
    "Sleep":              {"level": 1, "desc": "Addormenta le creature in area per un totale di 5d8 punti ferita."},
    "Thunderwave":        {"level": 1, "desc": "Onda d'urto sonora 4,5 m: TS COS, 2d8 danni tuono e spinta indietro 3 m."},
    "Color Spray":        {"level": 1, "desc": "Cono colorato: 6d10 PF di creature accecate fino al prossimo turno (dal totale più basso)."},
    "Disguise Self":       {"level": 1, "desc": "Cambi aspetto (volto, abiti, voce) per 1 ora; resta illusione."},
    "Feather Fall":       {"level": 1, "desc": "Reazione: 5 creature cadenti scendono dolcemente per 1 minuto, nessun danno da caduta."},
    "Identify":           {"level": 1, "desc": "Rituale: scopri proprietà magiche di un oggetto in 1 minuto."},
    "Comprehend Languages": {"level": 1, "desc": "Capisci qualunque lingua scritta o parlata per 1 ora."},
    "Find Familiar":      {"level": 1, "desc": "Rituale: evochi un piccolo spirito-bestia tuo servitore."},
    "Bane":               {"level": 1, "desc": "3 bersagli: TS CAR o -1d4 ai tiri per colpire e ai TS (concentrazione)."},
    "Inflict Wounds":     {"level": 1, "desc": "Tocco maledetto: 3d10 danni necrotici al bersaglio."},
    "Goodberry":          {"level": 1, "desc": "Crea 10 bacche; ciascuna nutre 1 giorno e cura 1 PF."},
    "Sanctuary":          {"level": 1, "desc": "Una creatura: chi la attacca fa TS SAG o sceglie un altro bersaglio."},

    # ── Incantesimi di 2° livello ─────────────────────────────────────
    "Misty Step":         {"level": 2, "desc": "Azione bonus: teletrasporto fino a 9 m in una casella che vedi."},
    "Scorching Ray":      {"level": 2, "desc": "3 raggi di fuoco: ogni attacco infligge 2d6 danni da fuoco."},
    "Web":                {"level": 2, "desc": "Riempie cubo 6 m di ragnatele: TS DES o trattenuto; terreno difficile."},
    "Invisibility":       {"level": 2, "desc": "Bersaglio diventa invisibile per 1 ora finché non attacca o lancia (concentrazione)."},
    "Mirror Image":       {"level": 2, "desc": "3 duplicati illusori distolgono gli attacchi: ogni colpo va su un'immagine prima."},
    "Hold Person":        {"level": 2, "desc": "Umanoide: TS SAG o paralizzato per 1 minuto (concentrazione, ritira a ogni turno)."},
    "Suggestion":         {"level": 2, "desc": "Suggerisci un corso d'azione ragionevole: TS SAG o lo segue per 8 ore."},
    "Darkness":           {"level": 2, "desc": "Sfera di buio magico raggio 4,5 m per 10 minuti (concentrazione)."},
    "Shatter":            {"level": 2, "desc": "Suono distruttivo in sfera 3 m: TS COS, 3d8 danni tuono."},
    "Spider Climb":       {"level": 2, "desc": "Cammini su muri e soffitti come un ragno per 1 ora (concentrazione)."},
    "Levitate":           {"level": 2, "desc": "Sollevi un bersaglio fino a 6 m: TS COS per resistere; 10 minuti (concentrazione)."},
    "Detect Thoughts":    {"level": 2, "desc": "Leggi i pensieri superficiali di una creatura per 1 minuto (concentrazione)."},
    "Aid":                {"level": 2, "desc": "3 creature: +5 PF massimi e correnti per 8 ore."},
    "Spiritual Weapon":   {"level": 2, "desc": "Arma spettrale: azione bonus per attaccare, 1d8+mod danni di forza."},
    "Lesser Restoration": {"level": 2, "desc": "Rimuove una condizione (cieco, paralizzato, avvelenato, sordo)."},
    "Hold Person":        {"level": 2, "desc": "Umanoide: TS SAG o paralizzato per 1 minuto (concentrazione, ritira a ogni turno)."},
    "Moonbeam":           {"level": 2, "desc": "Raggio lunare 1,5 m: TS COS, 2d10 danni radianti; muovi 18 m a turno (concentrazione)."},
    "Heat Metal":         {"level": 2, "desc": "Riscaldi metallo: 2d8 danni da fuoco e svantaggio finché lo tocca (concentrazione)."},
    "Flaming Sphere":     {"level": 2, "desc": "Sfera di fuoco 1,5 m: 2d6 danni da fuoco a chi tocca; muovi 9 m a turno (concentrazione)."},

    # ── Incantesimi di 3° livello ─────────────────────────────────────
    "Fireball":           {"level": 3, "desc": "Esplosione 6 m: TS DES, 8d6 danni da fuoco."},
    "Counterspell":       {"level": 3, "desc": "Reazione: annulla un incantesimo di livello ≤3, oppure prova di lancio per livelli superiori."},
    "Fly":                {"level": 3, "desc": "Bersaglio vola con velocità 18 m per 10 minuti (concentrazione)."},
    "Lightning Bolt":     {"level": 3, "desc": "Linea 30 m: TS DES, 8d6 danni da fulmine."},
    "Haste":              {"level": 3, "desc": "Bersaglio: +2 CA, vantaggio TS DES, azione extra, velocità raddoppiata (concentrazione)."},
    "Dispel Magic":       {"level": 3, "desc": "Termina un effetto magico di livello ≤3, o prova di lancio per superiori."},
    "Fireball":           {"level": 3, "desc": "Esplosione 6 m: TS DES, 8d6 danni da fuoco."},
    "Slow":               {"level": 3, "desc": "Fino a 6 creature: TS SAG o velocità dimezzata, -2 CA/TS DES, 1 azione (concentrazione)."},
    "Animate Dead":       {"level": 3, "desc": "Animi un cadavere come scheletro/zombi al tuo comando per 24 ore."},
    "Spirit Guardians":   {"level": 3, "desc": "Spiriti orbitanti raggio 4,5 m: TS SAG, 3d8 danni radianti/necrotici, terreno difficile (concentrazione)."},
    "Mass Healing Word":  {"level": 3, "desc": "Azione bonus: fino a 6 creature curano 1d4 + mod incantatore PF."},
    "Revivify":           {"level": 3, "desc": "Riporti in vita una creatura morta da meno di 1 minuto, 1 PF."},
    "Conjure Animals":    {"level": 3, "desc": "Evochi creature bestiali (varie taglie/CR) per 1 ora (concentrazione)."},
    "Call Lightning":     {"level": 3, "desc": "Nuvola tempestosa: azione per fulmine 3d10 in colonna 1,5 m (concentrazione)."},

    # ── Incantesimi di 4° livello ─────────────────────────────────────
    "Polymorph":          {"level": 4, "desc": "Trasformi una creatura in una bestia di CR pari o inferiore per 1 ora (concentrazione)."},
    "Greater Invisibility": {"level": 4, "desc": "Bersaglio invisibile per 1 minuto anche se attacca o lancia (concentrazione)."},
    "Ice Storm":          {"level": 4, "desc": "Tempesta grandine 6 m: TS DES, 2d8 contundente + 4d6 freddo, terreno difficile."},
    "Wall of Fire":       {"level": 4, "desc": "Muro di fuoco 18 m: 5d8 danni da fuoco a chi entra o termina lì (concentrazione)."},
    "Banishment":         {"level": 4, "desc": "TS CAR o bersaglio relegato in piano demi-sub per 1 minuto (concentrazione)."},
    "Confusion":          {"level": 4, "desc": "Sfera 3 m: TS SAG o azione casuale ogni turno (concentrazione)."},

    # ── Incantesimi di 5° livello ─────────────────────────────────────
    "Cone of Cold":       {"level": 5, "desc": "Cono 18 m: TS COS, 8d8 danni da freddo."},
    "Hold Monster":       {"level": 5, "desc": "Qualunque creatura non non-morta: TS SAG o paralizzata 1 minuto (concentrazione)."},
    "Telekinesis":        {"level": 5, "desc": "Muovi a distanza creature/oggetti con la mente per 10 minuti (concentrazione)."},
    "Wall of Force":      {"level": 5, "desc": "Muro invisibile indistruttibile per 10 minuti (concentrazione)."},
    "Raise Dead":         {"level": 5, "desc": "Riporti in vita una creatura morta da meno di 10 giorni, 1 PF."},
}


# ────────────────────────────────────────────────────────────────────────
# Lista incantesimi per CLASSE (sottoinsieme del catalogo SRD 5.5e).
#
# I set qui sotto rappresentano gli incantesimi a cui ogni classe ha
# accesso quando seleziona / prepara la propria lista. Si applica come
# filtro nel picker del frontend: un Chierico non vede Fireball, un
# Druido non vede Magic Missile.
#
# I tipi di accesso sono due:
#  • PREPARATION caster (Mago, Chierico, Druido, Paladino) — prepara
#    una lista quotidiana attingendo da: il LIBRO (Mago) o l'INTERA
#    lista di classe (gli altri). Modificabile a ogni riposo lungo.
#  • KNOWN caster (Bardo, Stregone, Warlock, Ranger) — conosce un
#    numero limitato di incantesimi della lista di classe (non
#    riselezionabili a ogni riposo, solo a salita di livello). In UI
#    l'azione `learn` aggiunge direttamente a `known`.
# ────────────────────────────────────────────────────────────────────────

# Trucchetti per ogni classe — set di nomi presenti nel catalogo.
CLASS_CANTRIPS: dict[str, set[str]] = {
    "Bardo":    {"Vicious Mockery", "Light", "Minor Illusion", "Mage Hand", "Prestidigitation"},
    "Chierico": {"Sacred Flame", "Light", "Guidance"},
    "Druido":   {"Druidcraft", "Produce Flame", "Guidance"},
    "Mago":     {"Fire Bolt", "Mage Hand", "Light", "Minor Illusion", "Prestidigitation"},
    "Stregone": {"Fire Bolt", "Prestidigitation", "Mage Hand", "Minor Illusion", "Light"},
    "Warlock":  {"Eldritch Blast", "Mage Hand", "Minor Illusion", "Prestidigitation", "Light"},
}

# Incantesimi di livello ≥1 per classe.
CLASS_SPELL_LIST: dict[str, set[str]] = {
    "Bardo": {
        "Charm Person", "Cure Wounds", "Faerie Fire", "Healing Word",
        "Bless", "Disguise Self", "Feather Fall", "Comprehend Languages",
        "Detect Magic", "Sleep", "Identify",
        "Misty Step", "Invisibility", "Mirror Image", "Hold Person",
        "Suggestion", "Shatter", "Levitate", "Detect Thoughts",
        "Lesser Restoration", "Aid",
        "Counterspell", "Dispel Magic", "Fly", "Haste", "Slow",
        "Revivify", "Mass Healing Word",
        "Polymorph", "Confusion", "Greater Invisibility", "Banishment",
        "Hold Monster", "Raise Dead",
    },
    "Chierico": {
        "Bless", "Cure Wounds", "Guiding Bolt", "Shield of Faith",
        "Healing Word", "Bane", "Inflict Wounds", "Sanctuary",
        "Identify", "Comprehend Languages", "Detect Magic",
        "Aid", "Spiritual Weapon", "Lesser Restoration", "Hold Person",
        "Spider Climb", "Moonbeam",
        "Spirit Guardians", "Mass Healing Word", "Revivify",
        "Dispel Magic", "Animate Dead",
        "Banishment", "Wall of Fire",
        "Raise Dead", "Hold Monster",
    },
    "Druido": {
        "Cure Wounds", "Entangle", "Faerie Fire", "Speak with Animals",
        "Goodberry", "Healing Word", "Detect Magic", "Thunderwave",
        "Flaming Sphere", "Heat Metal", "Moonbeam", "Spider Climb",
        "Lesser Restoration", "Hold Person", "Levitate",
        "Conjure Animals", "Call Lightning", "Dispel Magic", "Revivify",
        "Polymorph", "Wall of Fire", "Ice Storm", "Confusion",
        "Cone of Cold",
    },
    "Mago": {
        "Burning Hands", "Charm Person", "Color Spray", "Comprehend Languages",
        "Detect Magic", "Disguise Self", "Feather Fall", "Find Familiar",
        "Identify", "Mage Armor", "Magic Missile", "Shield", "Sleep",
        "Thunderwave",
        "Darkness", "Detect Thoughts", "Invisibility", "Levitate",
        "Mirror Image", "Misty Step", "Scorching Ray", "Shatter",
        "Spider Climb", "Suggestion", "Web", "Hold Person",
        "Counterspell", "Dispel Magic", "Fireball", "Fly", "Haste",
        "Lightning Bolt", "Slow", "Animate Dead",
        "Banishment", "Confusion", "Greater Invisibility", "Ice Storm",
        "Polymorph", "Wall of Fire",
        "Cone of Cold", "Hold Monster", "Telekinesis", "Wall of Force",
    },
    "Stregone": {
        "Burning Hands", "Charm Person", "Color Spray", "Comprehend Languages",
        "Detect Magic", "Disguise Self", "Feather Fall", "Mage Armor",
        "Magic Missile", "Shield", "Sleep", "Thunderwave",
        "Darkness", "Detect Thoughts", "Invisibility", "Levitate",
        "Mirror Image", "Misty Step", "Scorching Ray", "Shatter",
        "Spider Climb", "Suggestion", "Web", "Hold Person",
        "Counterspell", "Dispel Magic", "Fireball", "Fly", "Haste",
        "Lightning Bolt", "Slow",
        "Banishment", "Confusion", "Greater Invisibility", "Ice Storm",
        "Polymorph", "Wall of Fire",
        "Cone of Cold", "Hold Monster", "Telekinesis",
    },
    "Warlock": {
        "Hex", "Armor of Agathys", "Charm Person", "Comprehend Languages",
        "Detect Magic", "Find Familiar", "Identify",
        "Darkness", "Hold Person", "Invisibility", "Mirror Image",
        "Misty Step", "Shatter", "Spider Climb", "Suggestion",
        "Counterspell", "Dispel Magic", "Fly", "Slow",
        "Banishment", "Hold Monster",
    },
    "Paladino": {
        "Bless", "Cure Wounds", "Shield of Faith", "Sanctuary",
        "Bane", "Inflict Wounds", "Detect Magic", "Comprehend Languages",
        "Aid", "Lesser Restoration", "Spiritual Weapon",
        "Revivify", "Dispel Magic", "Haste",
        "Banishment",
    },
    "Ranger": {
        "Hunter's Mark", "Cure Wounds", "Speak with Animals", "Goodberry",
        "Detect Magic", "Faerie Fire", "Entangle",
        "Spider Climb", "Lesser Restoration", "Heat Metal", "Moonbeam",
        "Conjure Animals", "Call Lightning", "Revivify",
        "Wall of Fire", "Polymorph",
    },
}

# Classificazione del CASTER per UI/regole di gestione:
#  • "wizard" — preparation caster con libro (solo Mago)
#  • "preparation" — preparation caster senza libro (lista di classe completa
#    accessibile per la preparazione quotidiana)
#  • "known" — known caster (numero fisso di incantesimi conosciuti,
#    riselezionabili solo al level-up)
CASTER_KIND: dict[str, str] = {
    "Mago":     "wizard",
    "Chierico": "preparation",
    "Druido":   "preparation",
    "Paladino": "preparation",
    "Bardo":    "known",
    "Stregone": "known",
    "Warlock":  "known",
    "Ranger":   "known",
}


def class_spells(cls: str, *, include_cantrips: bool = True) -> list[dict]:
    """Lista incantesimi disponibili per la classe `cls`, ordinata per
    livello poi per nome. Restituisce dict {name, level, desc} per ogni
    voce — solo quelle presenti nel catalogo SPELLS.

    `cls` vuoto = nessun filtro → intero catalogo. Una classe con nome
    esplicito ma senza mapping (non incantatrice come Guerriero/Barbaro, o
    nome sconosciuto) non ha incantesimi → lista vuota. Tutte le 8 classi
    incantatrici (CASTER_KIND) hanno una voce in CLASS_SPELL_LIST, quindi
    nessun caster cade nel ramo vuoto."""
    cantrips = CLASS_CANTRIPS.get(cls)
    spells_lv1 = CLASS_SPELL_LIST.get(cls)
    if not cantrips and not spells_lv1:
        names = set(SPELLS.keys()) if not cls else set()
    else:
        names = (cantrips or set()) | (spells_lv1 or set())
        if not include_cantrips:
            names -= (cantrips or set())
    out: list[dict] = []
    for n in names:
        info = SPELLS.get(n)
        if not info:
            continue
        out.append({"name": n, "level": int(info["level"]),
                    "desc": info.get("desc", "")})
    out.sort(key=lambda s: (s["level"], s["name"]))
    return out


def caster_kind(cls: str) -> str:
    """Tipo di gestione incantesimi per la classe (wizard|preparation|
    known|none). 'none' = classe non incantatrice."""
    return CASTER_KIND.get(cls or "", "none")


def spell_entry(raw, *, cantrip: bool = False) -> dict:
    """
    Normalizza un incantesimo in dict {name, level, desc}.
    `raw` può essere:
      - una stringa "Nome"
      - una stringa "Nome | descrizione"
      - un dict parziale {name, level?, desc?}
    `cantrip=True` forza il livello a 0 (trucchetto).
    Livello e descrizione mancanti vengono cercati in SPELLS.
    """
    name = ""
    desc = ""
    level = None
    if isinstance(raw, dict):
        name = str(raw.get("name", "")).strip()
        desc = str(raw.get("desc", "")).strip()
        lv = raw.get("level")
        if lv is not None:
            try:
                level = int(lv)
            except (TypeError, ValueError):
                level = None
    else:
        text = str(raw or "").strip()
        if "|" in text:
            head, _, tail = text.partition("|")
            name, desc = head.strip(), tail.strip()
        else:
            name = text

    info = SPELLS.get(name, {})
    if not desc:
        desc = info.get("desc", "")
    if level is None:
        level = info.get("level", 0 if cantrip else 1)
    if cantrip:
        level = 0
    return {"name": name, "level": max(0, int(level)), "desc": desc}


def normalize_spell_list(lst, *, cantrip: bool = False) -> list[dict]:
    """Converte una lista mista (nomi/stringhe/dict) in lista di dict
    {name, level, desc}. Scarta le voci prive di nome."""
    if not isinstance(lst, list):
        return []
    out: list[dict] = []
    for raw in lst:
        e = spell_entry(raw, cantrip=cantrip)
        if e["name"]:
            out.append(e)
    return out


__all__ = ["SPELLS", "CLASS_CANTRIPS", "CLASS_SPELL_LIST", "CASTER_KIND",
           "class_spells", "caster_kind",
           "spell_entry", "normalize_spell_list"]
