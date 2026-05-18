"""
Database incantesimi — usato dalle schede iniziali.

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
}


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


__all__ = ["SPELLS", "spell_entry", "normalize_spell_list"]
