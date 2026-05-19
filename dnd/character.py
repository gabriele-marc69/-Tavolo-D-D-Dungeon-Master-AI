"""
Generatore schede personaggio(SRD 5.2 — Playtest 2024).

Tabelle: SPECIES (specie), CLASSES (classi), BACKGROUNDS (background, con
talento iniziale), ALIGNMENTS.

`generate_sheet(...)` produce una scheda completa Lv1 con array standard
[15,14,13,12,10,8] assegnato ottimalmente per classe + bonus specie.
"""
from __future__ import annotations

import random
from datetime import datetime
from typing import Optional

from .rules import (ability_mod, armor_class, initiative_mod,
                    proficiency_bonus, spell_slots)
from .spells import normalize_spell_list


STANDARD_ARRAY = [15, 14, 13, 12, 10, 8]
ABILITIES = ["FOR", "DES", "COS", "INT", "SAG", "CAR"]


# ────────────────────────────────────────────────────────────────────────
# SPECIE 5.5e (selezione SRD-compatible)
# In 5.5e i bonus caratteristica vengono assegnati dal giocatore (+2/+1 o +1/+1/+1).
# Usiamo qui un "suggerimento" coerente con i tratti classici, modificabile.
# ────────────────────────────────────────────────────────────────────────

SPECIES = {
    "Umano":     {"size": "Media", "speed": 9, "asi_hint": {"any": 3},
                  "traits": ["Versatile (+1 a 3 caratteristiche)",
                             "Talento di Origine extra Lv1"],
                  "languages": ["Comune", "una a scelta"]},
    "Elfo":      {"size": "Media", "speed": 9, "asi_hint": {"DES": 2, "INT": 1},
                  "traits": ["Scurovisione 18m", "Sensi Acuti (Percezione)",
                             "Riposo Fata", "Trance"],
                  "languages": ["Comune", "Elfico"]},
    "Nano":      {"size": "Media", "speed": 7.5, "asi_hint": {"COS": 2, "SAG": 1},
                  "traits": ["Scurovisione 36m", "Resistenza Nanica (veleno)",
                             "Tenacia Nanica (TS COS contro tratti)",
                             "Maestria delle Pietre"],
                  "languages": ["Comune", "Nanico"]},
    "Halfling":  {"size": "Piccola", "speed": 7.5, "asi_hint": {"DES": 2, "CAR": 1},
                  "traits": ["Fortuna Halfling (re-roll dei 1 sui d20)",
                             "Coraggio (vantaggio TS paura)",
                             "Agilità Halfling (movimento tra creature più grandi)"],
                  "languages": ["Comune", "Halfling"]},
    "Gnomo":     {"size": "Piccola", "speed": 7.5, "asi_hint": {"INT": 2, "DES": 1},
                  "traits": ["Scurovisione 18m",
                             "Astuzia Gnomesca (vantaggio TS INT/SAG/CAR contro magia)"],
                  "languages": ["Comune", "Gnomesco"]},
    "Tiefling":  {"size": "Media", "speed": 9, "asi_hint": {"CAR": 2, "INT": 1},
                  "traits": ["Scurovisione 18m", "Resistenza al Fuoco",
                             "Eredità Infernale (cantrip)"],
                  "languages": ["Comune", "Infernale"]},
    "Draconico": {"size": "Media", "speed": 9, "asi_hint": {"FOR": 2, "CAR": 1},
                  "traits": ["Eredità Draconica (resistenza + soffio)",
                             "Soffio Draconico (azione, 2d6 area)"],
                  "languages": ["Comune", "Draconico"]},
    "Goliath":   {"size": "Media", "speed": 9, "asi_hint": {"FOR": 2, "COS": 1},
                  "traits": ["Eredità Goliath (potere sovrannaturale 1/lungo)",
                             "Stazza Possente (capacità di carico raddoppiata)"],
                  "languages": ["Comune", "Gigante"]},
    "Aasimar":   {"size": "Media", "speed": 9, "asi_hint": {"CAR": 2, "SAG": 1},
                  "traits": ["Scurovisione 18m", "Resistenza Necrotica/Radiante",
                             "Mani Curative", "Rivelazione Celeste 1/lungo"],
                  "languages": ["Comune", "Celestiale"]},
    "Orco":      {"size": "Media", "speed": 9, "asi_hint": {"FOR": 2, "COS": 1},
                  "traits": ["Scurovisione 36m", "Aggressivo (movimento bonus)",
                             "Resistenza Implacabile (1/lungo)"],
                  "languages": ["Comune", "Orchesco"]},
}


# ────────────────────────────────────────────────────────────────────────
# CLASSI 5.5e
# - hit_die: dado vita
# - primary: caratteristica primaria (per assegnare il 15)
# - secondary: secondaria (per il 14)
# - saves: tiri salvezza in cui ha competenza
# - armor: armature usabili
# - weapons: armi usabili
# - skills_pick: numero abilità a scelta da `skills_list`
# - features_lv1: capacità iniziali
# - weapon_mastery_count: numero di armi con cui può sfruttare Maestria a Lv1 (0 se non marziale)
# - spellcasting: True se incantatore Lv1
# ────────────────────────────────────────────────────────────────────────

CLASSES = {
    "Barbaro": {
        "hit_die": 12, "primary": "FOR", "secondary": "COS",
        "saves": ["FOR", "COS"],
        "armor": ["Leggere", "Medie", "Scudi"],
        "weapons": ["Semplici", "da Guerra"],
        "skills_pick": 2,
        "skills_list": ["Addestrare Animali", "Atletica", "Intimidire",
                        "Natura", "Percezione", "Sopravvivenza"],
        "features_lv1": ["Ira (2 usi)", "Difesa Senza Armatura (CA=10+DES+COS)",
                         "Maestria d'Armi (2)"],
        "weapon_mastery_count": 2, "spellcasting": False,
    },
    "Bardo": {
        "hit_die": 8, "primary": "CAR", "secondary": "DES",
        "saves": ["DES", "CAR"],
        "armor": ["Leggere"],
        "weapons": ["Semplici"],
        "skills_pick": 3,
        "skills_list": ["qualsiasi (3 a scelta)"],
        "features_lv1": ["Incantesimi", "Ispirazione Bardica (d6)",
                         "Esperienza Bardica"],
        "weapon_mastery_count": 0, "spellcasting": True,
        "spells_known": 4, "cantrips_known": 2,
    },
    "Chierico": {
        "hit_die": 8, "primary": "SAG", "secondary": "COS",
        "saves": ["SAG", "CAR"],
        "armor": ["Leggere", "Medie", "Scudi"],
        "weapons": ["Semplici"],
        "skills_pick": 2,
        "skills_list": ["Conoscenza", "Intuizione", "Medicina",
                        "Persuasione", "Religione"],
        "features_lv1": ["Incantesimi", "Dominio Divino",
                         "Ordine Divino (Custode o Saggio)"],
        "weapon_mastery_count": 0, "spellcasting": True,
        "spells_prepared": 4, "cantrips_known": 3,
    },
    "Druido": {
        "hit_die": 8, "primary": "SAG", "secondary": "COS",
        "saves": ["INT", "SAG"],
        "armor": ["Leggere", "Scudi (non di metallo)"],
        "weapons": ["Semplici"],
        "skills_pick": 2,
        "skills_list": ["Addestrare Animali", "Conoscenza", "Intuizione",
                        "Medicina", "Natura", "Percezione", "Religione",
                        "Sopravvivenza"],
        "features_lv1": ["Incantesimi", "Druidico (linguaggio)",
                         "Pratica Primordiale"],
        "weapon_mastery_count": 0, "spellcasting": True,
        "spells_prepared": 4, "cantrips_known": 2,
    },
    "Guerriero": {
        "hit_die": 10, "primary": "FOR", "secondary": "COS",
        "saves": ["FOR", "COS"],
        "armor": ["Tutte", "Scudi"],
        "weapons": ["Semplici", "da Guerra"],
        "skills_pick": 2,
        "skills_list": ["Acrobazia", "Addestrare Animali", "Atletica",
                        "Intimidire", "Intuizione", "Percezione",
                        "Persuasione", "Sopravvivenza"],
        "features_lv1": ["Stile di Combattimento", "Secondo Fiato",
                         "Maestria d'Armi (3)"],
        "weapon_mastery_count": 3, "spellcasting": False,
    },
    "Monaco": {
        "hit_die": 8, "primary": "DES", "secondary": "SAG",
        "saves": ["FOR", "DES"],
        "armor": [],
        "weapons": ["Semplici", "armi marziali leggere"],
        "skills_pick": 2,
        "skills_list": ["Acrobazia", "Atletica", "Furtività",
                        "Intuizione", "Religione", "Storia"],
        "features_lv1": ["Difesa Senza Armatura (CA=10+DES+SAG)",
                         "Arti Marziali (1d6)", "Maestria d'Armi (2)"],
        "weapon_mastery_count": 2, "spellcasting": False,
    },
    "Paladino": {
        "hit_die": 10, "primary": "FOR", "secondary": "CAR",
        "saves": ["SAG", "CAR"],
        "armor": ["Tutte", "Scudi"],
        "weapons": ["Semplici", "da Guerra"],
        "skills_pick": 2,
        "skills_list": ["Atletica", "Intimidire", "Intuizione",
                        "Medicina", "Persuasione", "Religione"],
        "features_lv1": ["Imposizione delle Mani (riserva=Lv*5)",
                         "Senso Divino (3 usi)", "Maestria d'Armi (2)"],
        "weapon_mastery_count": 2, "spellcasting": False,
    },
    "Ranger": {
        "hit_die": 10, "primary": "DES", "secondary": "SAG",
        "saves": ["FOR", "DES"],
        "armor": ["Leggere", "Medie", "Scudi"],
        "weapons": ["Semplici", "da Guerra"],
        "skills_pick": 3,
        "skills_list": ["Addestrare Animali", "Atletica", "Conoscenza",
                        "Furtività", "Intuizione", "Investigare",
                        "Natura", "Percezione", "Sopravvivenza"],
        "features_lv1": ["Esploratore Esperto", "Nemico Designato",
                         "Maestria d'Armi (2)"],
        "weapon_mastery_count": 2, "spellcasting": True,
        "spells_prepared": 2, "cantrips_known": 0,
    },
    "Ladro": {
        "hit_die": 8, "primary": "DES", "secondary": "INT",
        "saves": ["DES", "INT"],
        "armor": ["Leggere"],
        "weapons": ["Semplici", "armi marziali leggere"],
        "skills_pick": 4,
        "skills_list": ["Acrobazia", "Atletica", "Furtività",
                        "Inganno", "Intuizione", "Intimidire",
                        "Investigare", "Percezione", "Persuasione",
                        "Rapidità di Mano"],
        "features_lv1": ["Esperienza (2 abilità)", "Attacco Furtivo (1d6)",
                         "Gergo dei Ladri", "Maestria d'Armi (2)"],
        "weapon_mastery_count": 2, "spellcasting": False,
    },
    "Stregone": {
        "hit_die": 6, "primary": "CAR", "secondary": "COS",
        "saves": ["COS", "CAR"],
        "armor": [],
        "weapons": ["Semplici"],
        "skills_pick": 2,
        "skills_list": ["Arcano", "Inganno", "Intimidire",
                        "Persuasione", "Religione"],
        "features_lv1": ["Incantesimi", "Origine Stregonesca (innata)"],
        "weapon_mastery_count": 0, "spellcasting": True,
        "spells_known": 2, "cantrips_known": 4,
    },
    "Warlock": {
        "hit_die": 8, "primary": "CAR", "secondary": "COS",
        "saves": ["SAG", "CAR"],
        "armor": ["Leggere"],
        "weapons": ["Semplici"],
        "skills_pick": 2,
        "skills_list": ["Arcano", "Inganno", "Intimidire",
                        "Investigare", "Natura", "Religione", "Storia"],
        "features_lv1": ["Incantesimi del Patto (Carisma)",
                         "Patrono Ultraterreno"],
        "weapon_mastery_count": 0, "spellcasting": True,
        "spells_known": 2, "cantrips_known": 2,
    },
    "Mago": {
        "hit_die": 6, "primary": "INT", "secondary": "COS",
        "saves": ["INT", "SAG"],
        "armor": [],
        "weapons": ["Semplici"],
        "skills_pick": 2,
        "skills_list": ["Arcano", "Conoscenza", "Intuizione",
                        "Investigare", "Medicina", "Religione", "Storia"],
        "features_lv1": ["Incantesimi (libro)", "Ritualista",
                         "Recupero Arcano"],
        "weapon_mastery_count": 0, "spellcasting": True,
        "spells_prepared": 4, "cantrips_known": 3, "spellbook_known": 6,
    },
}


# ────────────────────────────────────────────────────────────────────────
# BACKGROUND 5.5e (con talento iniziale, formato 2024)
# ────────────────────────────────────────────────────────────────────────

BACKGROUNDS = {
    "Acolito":        {"abilities": ["Intuizione", "Religione"],
                       "feat": "Iniziato alla Magia (Chierico)",
                       "languages": 2, "tool": "—"},
    "Artigiano":      {"abilities": ["Investigare", "Persuasione"],
                       "feat": "Crafter",
                       "languages": 0, "tool": "Strumenti da artigiano (3)"},
    "Ciarlatano":     {"abilities": ["Inganno", "Rapidità di Mano"],
                       "feat": "Skilled",
                       "languages": 0, "tool": "Kit da travestimento"},
    "Criminale":      {"abilities": ["Furtività", "Rapidità di Mano"],
                       "feat": "Alert",
                       "languages": 0, "tool": "Arnesi da scasso"},
    "Intrattenitore": {"abilities": ["Acrobazia", "Spettacolo"],
                       "feat": "Musicista",
                       "languages": 0, "tool": "Strumenti musicali (1)"},
    "Contadino":      {"abilities": ["Addestrare Animali", "Natura"],
                       "feat": "Tough",
                       "languages": 0, "tool": "Strumenti da artigiano (1)"},
    "Guardia":        {"abilities": ["Atletica", "Percezione"],
                       "feat": "Alert",
                       "languages": 1, "tool": "Set da gioco (1)"},
    "Guida":          {"abilities": ["Furtività", "Sopravvivenza"],
                       "feat": "Magic Initiate (Druid)",
                       "languages": 1, "tool": "Kit da cartografo"},
    "Eremita":        {"abilities": ["Medicina", "Religione"],
                       "feat": "Healer",
                       "languages": 1, "tool": "Kit da erborista"},
    "Mercante":       {"abilities": ["Animali", "Persuasione"],
                       "feat": "Lucky",
                       "languages": 1, "tool": "Strumenti da navigazione"},
    "Nobile":         {"abilities": ["Storia", "Persuasione"],
                       "feat": "Skilled",
                       "languages": 1, "tool": "Set da gioco (1)"},
    "Saggio":         {"abilities": ["Arcano", "Storia"],
                       "feat": "Magic Initiate (Wizard)",
                       "languages": 2, "tool": "Strumenti da calligrafo"},
    "Marinaio":       {"abilities": ["Atletica", "Percezione"],
                       "feat": "Tavern Brawler",
                       "languages": 0, "tool": "Strumenti da navigazione"},
    "Scriba":         {"abilities": ["Investigare", "Religione"],
                       "feat": "Skilled",
                       "languages": 0, "tool": "Strumenti da calligrafo"},
    "Soldato":        {"abilities": ["Atletica", "Intimidire"],
                       "feat": "Savage Attacker",
                       "languages": 0, "tool": "Set da gioco (1)"},
    "Viandante":      {"abilities": ["Rapidità di Mano", "Furtività"],
                       "feat": "Lucky",
                       "languages": 2, "tool": "Strumenti del ladro"},
}


ALIGNMENTS = [
    "Legale Buono", "Neutrale Buono", "Caotico Buono",
    "Legale Neutrale", "Neutrale", "Caotico Neutrale",
    "Legale Malvagio", "Neutrale Malvagio", "Caotico Malvagio",
]


# ────────────────────────────────────────────────────────────────────────
# Sesso e caratteristiche fisiche
# ────────────────────────────────────────────────────────────────────────

GENDERS = ["Maschio", "Femmina"]

# Per specie: altezza maschile (cm min,max), peso (kg min,max), età adulta tipica.
# Le femmine ricevono uno scarto ridotto su altezza/peso (vedi _generate_physique).
SPECIES_PHYSIQUE = {
    "Umano":     {"h": (165, 188), "w": (60, 95),   "age": (18, 55)},
    "Elfo":      {"h": (165, 195), "w": (45, 75),   "age": (80, 350)},
    "Nano":      {"h": (120, 142), "w": (55, 90),   "age": (45, 250)},
    "Halfling":  {"h": (85, 102),  "w": (16, 24),   "age": (20, 90)},
    "Gnomo":     {"h": (90, 112),  "w": (15, 26),   "age": (40, 300)},
    "Tiefling":  {"h": (165, 192), "w": (55, 92),   "age": (18, 60)},
    "Draconico": {"h": (175, 205), "w": (90, 145),  "age": (15, 70)},
    "Goliath":   {"h": (195, 225), "w": (110, 165), "age": (18, 60)},
    "Aasimar":   {"h": (165, 192), "w": (55, 92),   "age": (18, 70)},
    "Orco":      {"h": (175, 208), "w": (85, 135),  "age": (14, 50)},
}

BUILDS = ["esile", "snella", "atletica", "robusta", "massiccia", "longilinea"]

# Pool aspetto: generico, con varianti per specie particolari.
_HAIR_GENERIC = ["neri", "castani", "biondi", "rossi", "grigi", "bianchi", "ramati"]
_EYES_GENERIC = ["marroni", "azzurri", "verdi", "grigi", "ambrati", "nocciola", "neri"]
_SKIN_GENERIC = ["chiara", "olivastra", "scura", "ambrata", "pallida", "abbronzata"]

SPECIES_LOOK = {
    "Tiefling":  {"hair": ["neri", "rosso scuro", "viola", "bianchi"],
                  "eyes": ["completamente rossi", "completamente neri", "dorati", "argentei"],
                  "skin": ["rossastra", "violacea", "bluastra", "cinerea"]},
    "Draconico": {"hair": ["assenti (cresta dorsale)"],
                  "eyes": ["dorati", "rossi", "verdi", "argentei"],
                  "skin": ["scaglie rosse", "scaglie blu", "scaglie verdi",
                           "scaglie dorate", "scaglie bianche", "scaglie nere"]},
    "Aasimar":   {"hair": ["biondi", "bianchi", "argentei", "ramati"],
                  "eyes": ["luminosi azzurri", "dorati", "grigio perla"],
                  "skin": ["dorata", "luminescente", "chiara", "ambrata"]},
    "Goliath":   {"hair": ["neri", "grigi", "assenti"],
                  "eyes": ["azzurri", "grigi", "ambrati"],
                  "skin": ["grigio-pietra", "grigio screziato", "grigio-azzurra"]},
    "Orco":      {"hair": ["neri", "castano scuro", "grigi"],
                  "eyes": ["rossi", "ambrati", "gialli", "neri"],
                  "skin": ["verde", "grigio-verde", "verde scuro"]},
}

# Tratti distintivi per specie + pool generico.
SPECIES_MARK = {
    "Tiefling":  ["corna ricurve", "coda prensile", "zoccoli al posto dei piedi"],
    "Draconico": ["cresta dorsale pronunciata", "scaglie iridescenti", "coda corta"],
    "Aasimar":   ["alone tenue attorno al capo", "occhi che brillano al buio"],
    "Goliath":   ["segni ancestrali sulla pelle", "corporatura imponente"],
    "Orco":      ["zanne sporgenti", "cicatrici tribali"],
    "Nano":      ["barba intrecciata"],
    "Elfo":      ["orecchie a punta affilate"],
}
_MARK_GENERIC = ["una cicatrice sul volto", "un tatuaggio sull'avambraccio",
                 "nessun segno particolare", "lentiggini diffuse",
                 "uno sguardo intenso", "un sorriso sghembo"]


# ────────────────────────────────────────────────────────────────────────
# Equipaggiamento iniziale (semplificato)
# ────────────────────────────────────────────────────────────────────────

# Denominazioni di moneta (italiano): platino, oro, argento, rame.
COIN_KINDS = ("mp", "po", "ma", "mr")

# Oro iniziale (in pezzi d'oro) per classe — borsa di partenza a Lv1.
CLASS_START_GOLD = {
    "Barbaro": 60,  "Bardo": 100, "Chierico": 100, "Druido": 60,
    "Guerriero": 120, "Monaco": 40, "Paladino": 120, "Ranger": 100,
    "Ladro": 80,    "Stregone": 60, "Warlock": 80,  "Mago": 80,
}


def empty_treasure(gold: int = 0) -> dict:
    """Borsa monete: pezzi di platino/oro/argento/rame. `gold` va nei po."""
    return {"mp": 0, "po": max(0, int(gold or 0)), "ma": 0, "mr": 0}


CLASS_GEAR = {
    "Barbaro":   ["Ascia bipenne", "2 asce da lancio", "Pacco da esploratore", "4 giavellotti"],
    "Bardo":     ["Spada lunga", "Pacco da intrattenitore", "Liuto", "Armatura di cuoio", "Pugnale"],
    "Chierico":  ["Mazza", "Cotta di maglia", "Scudo", "Pacco da chierico", "Simbolo sacro"],
    "Druido":    ["Bastone", "Armatura di cuoio", "Scudo di legno", "Pacco da esploratore", "Focus druidico"],
    "Guerriero": ["Spada lunga", "Scudo", "Cotta di maglia", "Balestra leggera + 20 quadrelli", "Pacco da dungeon"],
    "Monaco":    ["Bastone ferrato", "10 dardi", "Pacco da dungeon"],
    "Paladino":  ["Spada lunga", "Scudo", "Cotta di maglia", "5 giavellotti", "Simbolo sacro", "Pacco da chierico"],
    "Ranger":    ["Armatura scaglie", "2 spade corte", "Arco lungo + 20 frecce", "Pacco da esploratore"],
    "Ladro":     ["Stocco", "Arco corto + 20 frecce", "Armatura di cuoio", "Pacco da scassinatore", "Arnesi da scasso"],
    "Stregone":  ["Bastone", "2 pugnali", "Focus arcano", "Pacco da dungeon"],
    "Warlock":   ["Balestra leggera + 20 quadrelli", "2 pugnali", "Armatura di cuoio", "Focus arcano", "Pacco da studioso"],
    "Mago":      ["Bastone", "Pugnale", "Libro degli incantesimi", "Pacco da studioso", "Focus arcano"],
}


# ────────────────────────────────────────────────────────────────────────
# Spell list iniziali (Lv1, suggerimenti — il giocatore può modificare)
# ────────────────────────────────────────────────────────────────────────

INITIAL_SPELLS = {
    "Bardo":     {"cantrips": ["Vicious Mockery", "Light"],
                  "lv1": ["Charm Person", "Cure Wounds", "Faerie Fire", "Healing Word"]},
    "Chierico":  {"cantrips": ["Sacred Flame", "Guidance", "Light"],
                  "prepared": ["Bless", "Cure Wounds", "Guiding Bolt", "Shield of Faith"]},
    "Druido":    {"cantrips": ["Druidcraft", "Produce Flame"],
                  "prepared": ["Cure Wounds", "Entangle", "Faerie Fire", "Speak with Animals"]},
    "Ranger":    {"cantrips": [],
                  "prepared": ["Hunter's Mark", "Cure Wounds"]},
    "Stregone":  {"cantrips": ["Fire Bolt", "Prestidigitation", "Mage Hand", "Minor Illusion"],
                  "known": ["Magic Missile", "Shield"]},
    "Warlock":   {"cantrips": ["Eldritch Blast", "Mage Hand"],
                  "known": ["Hex", "Armor of Agathys"]},
    "Mago":      {"cantrips": ["Fire Bolt", "Mage Hand", "Light"],
                  "prepared": ["Magic Missile", "Shield", "Mage Armor", "Detect Magic"],
                  "spellbook": ["Magic Missile", "Shield", "Mage Armor", "Detect Magic",
                                "Burning Hands", "Sleep"]},
}


# ────────────────────────────────────────────────────────────────────────
# Incantatori: tipo di lancio e caratteristica
# ────────────────────────────────────────────────────────────────────────

# Tipo di incantatore → determina la tabella degli slot incantesimo.
CASTER_TYPE = {
    "Bardo": "full", "Chierico": "full", "Druido": "full",
    "Stregone": "full", "Mago": "full",
    "Ranger": "half",          # 5.5e: il Ranger ha incantesimi dal Lv1
    "Warlock": "pact",         # Pact Magic
}

# Caratteristica da incantatore per classe.
SPELL_ABILITY = {
    "Bardo": "CAR", "Stregone": "CAR", "Warlock": "CAR", "Paladino": "CAR",
    "Chierico": "SAG", "Druido": "SAG", "Ranger": "SAG",
    "Mago": "INT",
}


def _build_spells(cls: str, level: int, stats: dict,
                  raw: Optional[dict] = None) -> dict:
    """
    Costruisce il blocco incantesimi strutturato della scheda:
      ability, save_dc, attack_bonus, cantrips, known, spellbook, slots.
    `raw` è la sorgente delle liste (INITIAL_SPELLS o spells già salvati).
    Restituisce {} se la classe non è incantatrice.
    """
    cls_data = CLASSES.get(cls, {})
    if not cls_data.get("spellcasting"):
        return {}
    caster = CASTER_TYPE.get(cls, "full")
    ability = SPELL_ABILITY.get(cls, "INT")
    abil = stats.get(ability, {}) if isinstance(stats, dict) else {}
    mod = abil.get("mod", 0) if isinstance(abil, dict) else 0
    pb = proficiency_bonus(level)

    src = raw if isinstance(raw, dict) else INITIAL_SPELLS.get(cls, {})
    # le schede vecchie usano chiavi diverse (lv1/prepared/known); i nomi
    # vengono normalizzati in dict {name, level, desc}.
    cantrips = normalize_spell_list(src.get("cantrips", []), cantrip=True)
    known = normalize_spell_list(
        src.get("known") or src.get("prepared") or src.get("lv1") or [])
    spellbook = normalize_spell_list(src.get("spellbook", []))

    return {
        "ability":      ability,
        "save_dc":      8 + pb + mod,
        "attack_bonus": pb + mod,
        "cantrips":     cantrips,
        "known":        known,
        "spellbook":    spellbook,
        "slots":        spell_slots(caster, level),
    }


# ────────────────────────────────────────────────────────────────────────
# Assegnazione array standard
# ────────────────────────────────────────────────────────────────────────

def _assign_stats(class_name: str, species_hint: dict) -> dict:
    """
    Assegna l'array standard [15,14,13,12,10,8] ordinato per la classe,
    poi applica i bonus suggeriti dalla specie.
    """
    cdata = CLASSES[class_name]
    primary = cdata["primary"]
    secondary = cdata["secondary"]
    # ordine priorità: primaria, secondaria, COS sempre alta, poi resto
    priority = [primary, secondary]
    if "COS" not in priority:
        priority.append("COS")
    for ab in ABILITIES:
        if ab not in priority:
            priority.append(ab)

    scores = {}
    for ab, val in zip(priority, STANDARD_ARRAY):
        scores[ab] = val

    # bonus specie
    asi = species_hint.get("asi_hint", {})
    if "any" in asi:
        # Umano: +1 alle 3 priorità più alte (escluse COS già top)
        bumped = 0
        for ab in priority:
            if bumped >= 3:
                break
            scores[ab] = scores.get(ab, 10) + 1
            bumped += 1
    else:
        for ab, bonus in asi.items():
            if ab in scores:
                scores[ab] += bonus

    return scores


# ────────────────────────────────────────────────────────────────────────
# Generatore scheda
# ────────────────────────────────────────────────────────────────────────

def _seed(rng: Optional[random.Random] = None) -> str:
    rng = rng or random
    return datetime.now().strftime("%Y%m%d-%H%M%S-") + f"{rng.randint(0, 0xFFFF):04X}"


def _generate_physique(species: str, gender: str, rng: random.Random) -> dict:
    """Genera età, statura, peso, corporatura e aspetto coerenti con la specie."""
    phys = SPECIES_PHYSIQUE.get(species, SPECIES_PHYSIQUE["Umano"])
    h_lo, h_hi = phys["h"]
    w_lo, w_hi = phys["w"]
    a_lo, a_hi = phys["age"]

    height = rng.randint(h_lo, h_hi)
    weight = rng.randint(w_lo, w_hi)
    if gender == "Femmina":
        height = max(h_lo - 6, height - rng.randint(5, 12))
        weight = max(w_lo - 8, int(weight * rng.uniform(0.80, 0.92)))

    look = SPECIES_LOOK.get(species, {})
    marks = list(SPECIES_MARK.get(species, [])) + _MARK_GENERIC

    return {
        "gender":      gender,
        "age":         rng.randint(a_lo, a_hi),
        "height_cm":   height,
        "weight_kg":   weight,
        "build":       rng.choice(BUILDS),
        "hair":        rng.choice(look.get("hair", _HAIR_GENERIC)),
        "eyes":        rng.choice(look.get("eyes", _EYES_GENERIC)),
        "skin":        rng.choice(look.get("skin", _SKIN_GENERIC)),
        "distinctive": rng.choice(marks),
    }


def generate_sheet(
    name: str,
    species: str,
    cls: str,
    level: int = 1,
    background: str = "Soldato",
    alignment: str = "Neutrale",
    *,
    player_type: str = "human",  # "human" | "ai"
    gender: str = "",            # "Maschio" | "Femmina" | "" → casuale
    rng: Optional[random.Random] = None,
) -> dict:
    """
    Genera una scheda completa per il personaggio.
    Lancia ValueError se specie/classe/background sono sconosciuti.
    """
    rng = rng or random
    level = max(1, min(20, int(level)))

    if gender not in GENDERS:
        gender = rng.choice(GENDERS)

    if species not in SPECIES:
        raise ValueError(f"Specie sconosciuta: {species!r}. Disponibili: {list(SPECIES)}")
    if cls not in CLASSES:
        raise ValueError(f"Classe sconosciuta: {cls!r}. Disponibili: {list(CLASSES)}")
    if background not in BACKGROUNDS:
        raise ValueError(f"Background sconosciuto: {background!r}. Disponibili: {list(BACKGROUNDS)}")

    sp_data = SPECIES[species]
    cls_data = CLASSES[cls]
    bg_data = BACKGROUNDS[background]

    # Caratteristiche
    scores = _assign_stats(cls, sp_data)
    stats = {ab: {"score": scores[ab], "mod": ability_mod(scores[ab])}
             for ab in ABILITIES}

    # HP iniziali (Lv1 = max dado classe + mod COS)
    hp_max = cls_data["hit_die"] + stats["COS"]["mod"]

    # CA — calcolo semplificato: nessuna armatura = 10+DES, altrimenti
    # ipotizziamo armatura tipica della classe
    armor_set = set(cls_data["armor"])
    if "Tutte" in armor_set:
        ca = armor_class(scores["DES"], armor_base=16, armor_dex_cap=0, shield=2)  # cotta+scudo
        armor_desc = "Cotta di maglia + scudo (CA 18 base)"
    elif "Medie" in armor_set:
        ca = armor_class(scores["DES"], armor_base=14, armor_dex_cap=2,
                         shield=2 if "Scudi" in armor_set else 0)
        armor_desc = "Armatura media" + (" + scudo" if "Scudi" in armor_set else "")
    elif "Leggere" in armor_set:
        ca = armor_class(scores["DES"], armor_base=11, armor_dex_cap=None,
                         shield=2 if "Scudi" in armor_set else 0)
        armor_desc = "Armatura di cuoio" + (" + scudo" if "Scudi" in armor_set else "")
    elif cls == "Barbaro":
        ca = 10 + stats["DES"]["mod"] + stats["COS"]["mod"]
        armor_desc = "Difesa senza armatura (10+DES+COS)"
    elif cls == "Monaco":
        ca = 10 + stats["DES"]["mod"] + stats["SAG"]["mod"]
        armor_desc = "Difesa senza armatura (10+DES+SAG)"
    else:
        ca = armor_class(scores["DES"], armor_base=10)
        armor_desc = "Nessuna armatura (10+DES)"

    init = initiative_mod(scores["DES"], level=level)
    pb = proficiency_bonus(level)

    # Abilità: pick di classe (random) + 2 dal background
    skill_choices = list(cls_data["skills_list"])
    n_pick = cls_data["skills_pick"]
    if skill_choices == ["qualsiasi (3 a scelta)"]:
        skills = rng.sample(["Acrobazia", "Atletica", "Persuasione",
                             "Inganno", "Investigare", "Intimidire",
                             "Percezione", "Storia", "Arcano"], n_pick)
    else:
        skills = rng.sample(skill_choices, min(n_pick, len(skill_choices)))
    for sk in bg_data["abilities"]:
        if sk not in skills:
            skills.append(sk)

    # Linguaggi: comune + lingua specie + lingue background
    languages = list(sp_data["languages"])
    bg_langs = bg_data.get("languages", 0)
    for i in range(bg_langs):
        languages.append(f"linguaggio extra #{i+1}")

    # Equipaggiamento
    equipment = CLASS_GEAR.get(cls, []) + [bg_data["tool"]]

    # Weapon mastery (solo classi marziali)
    masteries_n = cls_data.get("weapon_mastery_count", 0)
    masteries = []
    if masteries_n > 0:
        all_masteries = ["cleave", "graze", "nick", "push", "sap",
                         "slow", "topple", "vex", "flex"]
        masteries = rng.sample(all_masteries, masteries_n)

    # Incantesimi e slot
    caster_type = (CASTER_TYPE.get(cls, "full")
                   if cls_data.get("spellcasting") else "none")
    spells = _build_spells(cls, level, stats)

    sheet = {
        "name":        name,
        "species":     species,
        "race":        species,  # alias retro-compatibile
        "class":       cls,
        "level":       level,
        "xp":          0,
        "background":  background,
        "feat_origin": bg_data["feat"],
        "alignment":   alignment,
        "player_type": player_type,
        "gender":      gender,
        "physical":    _generate_physique(species, gender, rng),
        "seed":        _seed(rng),

        "stats":       stats,
        "hp":          {"current": hp_max, "max": hp_max, "temp": 0},
        "ac":          ca,
        "armor":       armor_desc,
        "initiative":  init,
        "speed":       sp_data["speed"],
        "size":        sp_data["size"],

        "proficiency_bonus": pb,
        "saving_throws":     cls_data["saves"],
        "skills":            skills,
        "languages":         languages,
        "equipment":         equipment,
        "treasure":          empty_treasure(CLASS_START_GOLD.get(cls, 50)),
        "weapon_masteries":  masteries,

        "traits":         sp_data["traits"],
        "class_features": cls_data["features_lv1"],
        "caster_type":    caster_type,
        "spells":         spells,

        "death_saves":   {"successes": 0, "failures": 0, "stable": False, "dead": False},
        "status":        "alive",
        "inspiration":   False,
        "conditions":    [],
    }
    return sheet


# ────────────────────────────────────────────────────────────────────────
# Modifica manuale e migrazione schede
# ────────────────────────────────────────────────────────────────────────

def recompute_derived(sheet: dict) -> None:
    """
    Ricalcola in-place i valori derivati dopo una modifica manuale della
    scheda: modificatori di caratteristica e CD/bonus degli incantesimi.
    Da chiamare dopo un merge di modifiche dall'editor scheda.
    """
    if not isinstance(sheet, dict):
        return
    stats = sheet.get("stats")
    if isinstance(stats, dict):
        for v in stats.values():
            if isinstance(v, dict) and "score" in v:
                try:
                    v["mod"] = ability_mod(int(v["score"]))
                except (TypeError, ValueError):
                    pass
    spells = sheet.get("spells")
    if isinstance(spells, dict) and spells.get("ability"):
        try:
            level = int(sheet.get("level", 1) or 1)
        except (TypeError, ValueError):
            level = 1
        pb = proficiency_bonus(level)
        ab = spells.get("ability")
        st = stats.get(ab, {}) if isinstance(stats, dict) else {}
        mod = st.get("mod", 0) if isinstance(st, dict) else 0
        spells["save_dc"] = 8 + pb + mod
        spells["attack_bonus"] = pb + mod
        # normalizza le liste in dict {name, level, desc}: l'editor scheda
        # invia stringhe "Nome | descrizione", da ricondurre a struttura.
        spells["cantrips"] = normalize_spell_list(
            spells.get("cantrips", []), cantrip=True)
        spells["known"] = normalize_spell_list(spells.get("known", []))
        spells["spellbook"] = normalize_spell_list(spells.get("spellbook", []))
        # mantieni gli slot usati entro il massimo
        slots = spells.get("slots")
        if isinstance(slots, dict):
            for sl in slots.values():
                if isinstance(sl, dict):
                    mx = max(0, int(sl.get("max", 0) or 0))
                    us = max(0, min(mx, int(sl.get("used", 0) or 0)))
                    sl["max"], sl["used"] = mx, us


def upgrade_sheet(sheet: dict) -> dict:
    """
    Migrazione idempotente: porta una scheda salvata al formato corrente
    aggiungendo `caster_type` e il blocco incantesimi strutturato con slot.
    Conserva gli slot già usati. Sicura da chiamare a ogni caricamento.
    """
    if not isinstance(sheet, dict):
        return sheet
    cls = sheet.get("class")
    cls_data = CLASSES.get(cls, {})
    is_caster = bool(cls_data.get("spellcasting"))
    sheet["caster_type"] = (CASTER_TYPE.get(cls, "full")
                            if is_caster else "none")

    # borsa monete: la aggiunge alle schede vecchie e completa le
    # denominazioni mancanti senza toccare i valori già presenti.
    tre = sheet.get("treasure")
    if not isinstance(tre, dict):
        tre = empty_treasure(CLASS_START_GOLD.get(cls, 50))
        sheet["treasure"] = tre
    else:
        for k in COIN_KINDS:
            try:
                tre[k] = max(0, int(tre.get(k, 0) or 0))
            except (TypeError, ValueError):
                tre[k] = 0

    if not is_caster:
        return sheet

    try:
        level = int(sheet.get("level", 1) or 1)
    except (TypeError, ValueError):
        level = 1

    old = sheet.get("spells")
    old = old if isinstance(old, dict) else {}
    if "ability" not in old:
        # formato vecchio (liste sciolte) → ricostruisci strutturato
        sheet["spells"] = _build_spells(cls, level, sheet.get("stats", {}),
                                        raw=old)
    elif not isinstance(old.get("slots"), dict) or not old["slots"]:
        old["slots"] = spell_slots(CASTER_TYPE.get(cls, "full"), level)

    # normalizza gli incantesimi in dict {name, level, desc} (schede salvate
    # col formato vecchio a soli nomi vengono arricchite con la descrizione)
    sp = sheet.get("spells")
    if isinstance(sp, dict):
        sp["cantrips"] = normalize_spell_list(sp.get("cantrips", []), cantrip=True)
        sp["known"] = normalize_spell_list(sp.get("known", []))
        sp["spellbook"] = normalize_spell_list(sp.get("spellbook", []))
    return sheet


def species_list() -> list[str]:
    return list(SPECIES.keys())


def class_list() -> list[str]:
    return list(CLASSES.keys())


def background_list() -> list[str]:
    return list(BACKGROUNDS.keys())


def alignment_list() -> list[str]:
    return list(ALIGNMENTS)


def gender_list() -> list[str]:
    return list(GENDERS)


__all__ = [
    "SPECIES", "CLASSES", "BACKGROUNDS", "ALIGNMENTS", "GENDERS",
    "STANDARD_ARRAY", "ABILITIES", "CASTER_TYPE", "SPELL_ABILITY",
    "COIN_KINDS", "CLASS_START_GOLD", "empty_treasure",
    "generate_sheet", "recompute_derived", "upgrade_sheet",
    "species_list", "class_list",
    "background_list", "alignment_list", "gender_list",
]
