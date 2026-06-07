"""
Generatore schede personaggio(SRD 5.2 — Playtest 2024).

Tabelle: SPECIES (specie), CLASSES (classi), BACKGROUNDS (background, con
talento iniziale), ALIGNMENTS.

`generate_sheet(...)` produce una scheda completa Lv1 con array standard
[15,14,13,12,10,8] assegnato ottimalmente per classe + bonus specie.
"""
from __future__ import annotations

import random
import re
from datetime import datetime
from typing import Optional

from .rules import (ability_mod, armor_class, initiative_mod, level_for_xp,
                    proficiency_bonus, spell_slots, xp_for_level)
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
# Armi: database dado di danno / proprietà. Serve a costruire le voci
# STRUTTURATE in sheet["weapons"] (dado, tipo danno, bonus d'attacco già
# calcolato) così il sistema può generare il ROLL_REQ dei danni in
# automatico dopo un colpo a segno, senza che il DM debba indovinare il dado.
#
# Ogni entry:
#   match    → token (minuscoli) cercati come sottostringa nell'equipaggiamento
#   die      → dado di danno base (es. "1d8")
#   type     → tipo di danno (tagliente/perforante/contundente)
#   abil     → "for" | "des" | "finesse" (accurata: usa il migliore tra FOR/DES)
#   category → "Semplice" | "Marziale"
#   versatile→ dado a due mani (None se non versatile)
#   ranged   → arma a distanza (bonus di tiro = DES)
#   props    → proprietà testuali (informative)
# ────────────────────────────────────────────────────────────────────────
WEAPON_DB = [
    {"name": "Ascia bipenne",  "match": ["ascia bipenne"], "die": "1d12", "type": "tagliente",   "abil": "for",     "category": "Marziale", "versatile": None,  "ranged": False, "props": ["pesante", "due mani"]},
    {"name": "Ascia da lancio","match": ["ascia da lancio", "asce da lancio"], "die": "1d6", "type": "tagliente", "abil": "for", "category": "Marziale", "versatile": None, "ranged": False, "props": ["leggera", "lancio"]},
    {"name": "Spada lunga",    "match": ["spada lunga"],   "die": "1d8",  "type": "tagliente",   "abil": "for",     "category": "Marziale", "versatile": "1d10","ranged": False, "props": ["versatile"]},
    {"name": "Spada corta",    "match": ["spada corta", "spade corte"], "die": "1d6", "type": "perforante", "abil": "finesse", "category": "Marziale", "versatile": None, "ranged": False, "props": ["leggera", "accurata"]},
    {"name": "Stocco",         "match": ["stocco"],        "die": "1d8",  "type": "perforante",  "abil": "finesse", "category": "Marziale", "versatile": None,  "ranged": False, "props": ["accurata"]},
    {"name": "Pugnale",        "match": ["pugnale", "pugnali"], "die": "1d4", "type": "perforante", "abil": "finesse", "category": "Semplice", "versatile": None, "ranged": False, "props": ["leggera", "accurata", "lancio"]},
    {"name": "Mazza",          "match": ["mazza"],         "die": "1d6",  "type": "contundente", "abil": "for",     "category": "Semplice", "versatile": None,  "ranged": False, "props": []},
    {"name": "Bastone ferrato","match": ["bastone ferrato"], "die": "1d6", "type": "contundente", "abil": "for",    "category": "Semplice", "versatile": "1d8", "ranged": False, "props": ["versatile"]},
    {"name": "Bastone",        "match": ["bastone"],       "die": "1d6",  "type": "contundente", "abil": "for",     "category": "Semplice", "versatile": "1d8", "ranged": False, "props": ["versatile"]},
    {"name": "Giavellotto",    "match": ["giavellott"],    "die": "1d6",  "type": "perforante",  "abil": "for",     "category": "Semplice", "versatile": None,  "ranged": False, "props": ["lancio"]},
    {"name": "Dardo",          "match": ["dardi", "dardo"],"die": "1d4",  "type": "perforante",  "abil": "finesse", "category": "Semplice", "versatile": None,  "ranged": True,  "props": ["lancio"]},
    {"name": "Arco lungo",     "match": ["arco lungo"],    "die": "1d8",  "type": "perforante",  "abil": "des",     "category": "Marziale", "versatile": None,  "ranged": True,  "props": ["munizioni", "due mani", "pesante"]},
    {"name": "Arco corto",     "match": ["arco corto"],    "die": "1d6",  "type": "perforante",  "abil": "des",     "category": "Semplice", "versatile": None,  "ranged": True,  "props": ["munizioni", "due mani"]},
    {"name": "Balestra leggera","match": ["balestra leggera"], "die": "1d8", "type": "perforante", "abil": "des", "category": "Semplice", "versatile": None, "ranged": True, "props": ["munizioni", "due mani"]},
]


def _dmg_roll(die: str, mod: int) -> str:
    """'1d8' + mod → '1d8+3' / '1d8-1' / '1d8' (mod 0)."""
    if mod > 0:
        return f"{die}+{mod}"
    if mod < 0:
        return f"{die}{mod}"
    return die


def _build_weapons(equipment: list, stats: dict, pb: int) -> list:
    """Scansiona l'equipaggiamento e costruisce le voci STRUTTURATE delle
    armi: dado di danno, tipo, bonus d'attacco e tiro danni già calcolati
    (mod abilità + competenza). I PG sono competenti con l'equipaggiamento
    iniziale della classe, quindi il bonus competenza è sempre incluso."""
    def _mod(ab: str) -> int:
        v = (stats.get(ab) or {})
        return int(v.get("mod", 0) or 0)

    for_mod, dex_mod = _mod("FOR"), _mod("DES")
    weapons: list = []
    seen: set = set()
    for raw in (equipment or []):
        low = str(raw).lower()
        for w in WEAPON_DB:
            if any(tok in low for tok in w["match"]):
                if w["name"] in seen:
                    break
                seen.add(w["name"])
                if w["abil"] == "des":
                    abil, amod = "DES", dex_mod
                elif w["abil"] == "finesse":
                    abil, amod = ("DES", dex_mod) if dex_mod >= for_mod else ("FOR", for_mod)
                else:
                    abil, amod = "FOR", for_mod
                atk = amod + pb
                weapons.append({
                    "name":         w["name"],
                    "category":     w["category"],
                    "ability":      abil,
                    "damage":       w["die"],
                    "damage_type":  w["type"],
                    "damage_mod":   amod,
                    "damage_roll":  _dmg_roll(w["die"], amod),
                    "attack_bonus": atk,
                    "attack_roll":  f"1d20+{atk}" if atk >= 0 else f"1d20{atk}",
                    "versatile":    (_dmg_roll(w["versatile"], amod) if w["versatile"] else None),
                    "ranged":       w["ranged"],
                    "properties":   list(w["props"]),
                })
                break
    return weapons


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

    # Caratteristiche. `score_base` è il punteggio "nudo" (senza oggetti
    # magici): permette di ricalcolare `score` come base + bonus item ogni
    # volta che cambia l'inventario sintonizzato.
    scores = _assign_stats(cls, sp_data)
    stats = {ab: {"score": scores[ab],
                  "score_base": scores[ab],
                  "mod": ability_mod(scores[ab])}
             for ab in ABILITIES}

    # HP: Lv1 = max dado classe + mod COS. Ogni livello successivo aggiunge
    # la media del dado (regola 5e per promozione a tavolo) + mod COS — stessa
    # formula di apply_level_progression, così un PG creato a Lv N ha gli HP
    # corretti invece di restare ai PF di Lv1.
    hit_die = cls_data["hit_die"]
    hp_max = hit_die + stats["COS"]["mod"]
    if level > 1:
        per_level_hp = max(1, hit_die // 2 + 1 + stats["COS"]["mod"])
        hp_max += per_level_hp * (level - 1)

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
        "xp":          xp_for_level(level),
        "background":  background,
        "feat_origin": bg_data["feat"],
        "alignment":   alignment,
        "player_type": player_type,
        "gender":      gender,
        "physical":    _generate_physique(species, gender, rng),
        "seed":        _seed(rng),

        "stats":       stats,
        "hp":          {"current": hp_max, "max": hp_max, "temp": 0},
        "hp_max_base": hp_max,        # HP max "nudi" — bonus oggetti separati
        "ac":          ca,
        "ac_base":     ca,            # CA senza magic_items: ricalcolata dai bonus
        "armor":       armor_desc,
        "initiative":  init,
        "initiative_base": init,
        "speed":       sp_data["speed"],
        "speed_base":  sp_data["speed"],
        "size":        sp_data["size"],

        "proficiency_bonus": pb,
        "saving_throws":     cls_data["saves"],
        "skills":            skills,
        "languages":         languages,
        "equipment":         equipment,
        # Armi STRUTTURATE: dado di danno + bonus d'attacco già calcolati,
        # estratti dall'equipaggiamento. Il sistema le usa per costruire il
        # ROLL_REQ dei danni dopo un colpo a segno.
        "weapons":           _build_weapons(equipment, stats, pb),
        "magic_items":       [],   # artefatti magici / oggetti meravigliosi
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

_ITEM_MOD_KEYS = (
    "ac", "initiative", "speed", "hp_max",
    "save_dc", "attack_bonus", "save_all", "proficiency_bonus",
)
_ITEM_SAVE_ABILITIES = ("FOR", "DES", "COS", "INT", "SAG", "CAR")

# ────────────────────────────────────────────────────────────────────────
# Parser bonus da descrizione naturale
# ────────────────────────────────────────────────────────────────────────
# Riconosce stringhe come "+2 CA", "+1 a tutti i TS", "+2 FOR", "+5 HP max",
# "-1 iniziativa" ecc. dentro la descrizione di un oggetto magico. Mappa la
# label naturale alla chiave di `modifiers` usata da `_aggregate_item_modifiers`.
_DESC_LABEL_TO_KEY = {
    # CA
    "ca": ("scalar", "ac"),
    "ac": ("scalar", "ac"),
    # HP
    "hp": ("scalar", "hp_max"),
    "hp max": ("scalar", "hp_max"),
    "hp massimi": ("scalar", "hp_max"),
    "pf": ("scalar", "hp_max"),
    "pf max": ("scalar", "hp_max"),
    "pf massimi": ("scalar", "hp_max"),
    "punti ferita": ("scalar", "hp_max"),
    # iniziativa
    "iniziativa": ("scalar", "initiative"),
    "init": ("scalar", "initiative"),
    # velocità
    "velocità": ("scalar", "speed"),
    "velocita": ("scalar", "speed"),
    "speed": ("scalar", "speed"),
    "movimento": ("scalar", "speed"),
    # CD incantesimi
    "cd": ("scalar", "save_dc"),
    "cd incantesimi": ("scalar", "save_dc"),
    "save dc": ("scalar", "save_dc"),
    # Bonus attacco
    "tpc": ("scalar", "attack_bonus"),
    "attacco": ("scalar", "attack_bonus"),
    "attacchi": ("scalar", "attack_bonus"),
    "bonus attacco": ("scalar", "attack_bonus"),
    # TS generale
    "tutti i ts": ("scalar", "save_all"),
    "ts generale": ("scalar", "save_all"),
    "ts generali": ("scalar", "save_all"),
    "salvezze": ("scalar", "save_all"),
    "tiri salvezza": ("scalar", "save_all"),
    # competenza
    "competenza": ("scalar", "proficiency_bonus"),
}

# Caratteristiche full-name → sigla.
_DESC_ABILITY_ALIASES = {
    "for": "FOR", "forza": "FOR",
    "des": "DES", "destrezza": "DES",
    "cos": "COS", "costituzione": "COS",
    "int": "INT", "intelligenza": "INT",
    "sag": "SAG", "saggezza": "SAG",
    "car": "CAR", "carisma": "CAR",
}

# Token "[+/-]N" seguito (eventualmente da preposizione) da una label.
# La label viene catturata in modo greedy fino al prossimo separatore così
# match anche "tutti i TS" o "TS FOR".
_DESC_BONUS_RE = re.compile(
    r"""
    ([+-]?\d+)                         # bonus numerico (con segno opzionale)
    \s*
    (?:a(?:lla|lle|i|gli|l)?\s+|al\s+|alla\s+)?  # "a / al / alla / ai" facoltativo
    (
        ts\s+(?:for|des|cos|int|sag|car|forza|destrezza|costituzione|intelligenza|saggezza|carisma)
      | tutti\s+i\s+ts | ts\s+general[ei] | tiri\s+salvezza | salvezze
      | cd\s+incantesimi | save\s+dc
      | hp\s+(?:max|massimi)? | pf\s+(?:max|massimi)? | punti\s+ferita
      | bonus\s+attacco
      | velocit[àa] | iniziativa | movimento | speed | init
      | competenza
      | ca | ac
      | tpc | attacco | attacchi
      | for(?:za)? | des(?:trezza)? | cos(?:tituzione)? | int(?:elligenza)?
      | sag(?:gezza)? | car(?:isma)?
    )\b
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _extract_desc_modifiers(desc: str) -> dict:
    """Scansiona la descrizione testuale di un oggetto magico e ricava un
    dict `modifiers` compatibile con `_aggregate_item_modifiers`.

    Es: "Anello di protezione · +2 CA"   → {"ac": 2}
        "+1 FOR e +1 a tutti i TS"      → {"stat": {"FOR": 1}, "save_all": 1}
        "+1 TS SAG"                      → {"save": {"SAG": 1}}

    Restituisce {} se la stringa non contiene bonus riconoscibili. Non
    lancia eccezioni: in caso di token ambiguo viene saltato.
    """
    if not isinstance(desc, str) or not desc.strip():
        return {}
    out: dict = {}
    for m in _DESC_BONUS_RE.finditer(desc):
        try:
            val = int(m.group(1))
        except (TypeError, ValueError):
            continue
        if val == 0:
            continue
        label = m.group(2).lower()
        label = re.sub(r"\s+", " ", label).strip()

        # TS singola caratteristica: "ts SAG"
        if label.startswith("ts "):
            ab_raw = label[3:].strip()
            ab = _DESC_ABILITY_ALIASES.get(ab_raw)
            if ab:
                save = out.setdefault("save", {})
                save[ab] = save.get(ab, 0) + val
            continue

        # Label diretta (tipo CA, HP, iniziativa, …)
        mapping = _DESC_LABEL_TO_KEY.get(label)
        if mapping:
            kind, key = mapping
            if kind == "scalar":
                out[key] = out.get(key, 0) + val
            continue

        # Bonus a caratteristica (FOR/DES/...)
        ab = _DESC_ABILITY_ALIASES.get(label)
        if ab:
            stat = out.setdefault("stat", {})
            stat[ab] = stat.get(ab, 0) + val
    return out


def _normalize_magic_item(it):
    """Porta una voce di `magic_items` al formato strutturato e auto-rileva
    i bonus dichiarati in linguaggio naturale nella descrizione.

    • Voci stringa "Nome | desc | flag" vengono ignorate (gestite a UI).
      Convertiamo qui solo i dict, lasciando le stringhe intatte: l'editor
      JS le riserializza in dict al salvataggio.
    • Se `modifiers` è assente o vuoto e `desc` contiene pattern come
      "+2 CA", lo riempiamo con `_extract_desc_modifiers(desc)`.
    • Se almeno un bonus meccanico esiste (esplicito o estratto) e
      `attuned` non è già True, forziamo `attuned=True`. In 5.5e un anello
      di protezione che dichiara "+2 CA" deve poter agire sulla CA del PG
      anche se l'utente non ha spuntato il flag a mano.
    """
    if not isinstance(it, dict):
        return it
    mods = it.get("modifiers")
    has_explicit = isinstance(mods, dict) and any(
        (k in _ITEM_MOD_KEYS and v) or (k == "stat" and v) or (k == "save" and v)
        for k, v in mods.items()
    )
    if not has_explicit:
        parsed = _extract_desc_modifiers(it.get("desc") or "")
        if parsed:
            # Conserva eventuali sotto-dict già presenti (es. modifiers vuoto
            # ma con chiave "stat" preimpostata): merge superficiale.
            existing = mods if isinstance(mods, dict) else {}
            merged = dict(existing)
            for k, v in parsed.items():
                if k in ("stat", "save") and isinstance(v, dict):
                    sub = dict(merged.get(k) or {})
                    for ab, n in v.items():
                        sub[ab] = (sub.get(ab, 0) or 0) + n
                    merged[k] = sub
                else:
                    merged[k] = (merged.get(k, 0) or 0) + v
            it["modifiers"] = merged
            mods = merged

    # Auto-sintonizzazione quando ci sono bonus reali da applicare.
    if isinstance(mods, dict):
        has_any = any(
            (k in _ITEM_MOD_KEYS and v)
            or (k in ("stat", "save") and isinstance(v, dict) and any(v.values()))
            for k, v in mods.items()
        )
        if has_any and not it.get("attuned"):
            it["attuned"] = True
    return it


def normalize_magic_items(items: list) -> list:
    """Applica `_normalize_magic_item` in-place a ogni voce della lista.

    Idempotente: chiamarla più volte non altera ulteriormente la scheda.
    """
    if not isinstance(items, list):
        return items
    for i, it in enumerate(items):
        items[i] = _normalize_magic_item(it)
    return items


def _aggregate_item_modifiers(items: list) -> dict:
    """Somma i bonus dei magic_items SINTONIZZATI in un unico dict.

    Schema atteso per ogni voce attiva:
        {"name": "...", "attuned": True,
         "modifiers": {"ac": 1, "save_all": 1, "stat": {"DES": 2}, "save": {"SAG": 1}, ...}}

    Solo gli oggetti con `attuned: True` contribuiscono (in 5.5e quasi ogni
    artefatto richiede sintonizzazione). Voci stringa o senza `modifiers`
    sono ignorate. Bonus stats vengono sommati al PUNTEGGIO (score), non
    al modificatore: il `mod` viene poi ricalcolato.
    """
    out = {k: 0 for k in _ITEM_MOD_KEYS}
    out["stat"] = {ab: 0 for ab in _ITEM_SAVE_ABILITIES}
    out["save"] = {ab: 0 for ab in _ITEM_SAVE_ABILITIES}
    if not isinstance(items, list):
        return out
    for it in items:
        if not isinstance(it, dict):
            continue
        if not it.get("attuned"):
            continue
        mods = it.get("modifiers")
        if not isinstance(mods, dict):
            continue
        for k in _ITEM_MOD_KEYS:
            try:
                out[k] += int(mods.get(k, 0) or 0)
            except (TypeError, ValueError):
                pass
        stat_bonus = mods.get("stat")
        if isinstance(stat_bonus, dict):
            for ab, v in stat_bonus.items():
                ab_up = str(ab).upper()
                if ab_up in out["stat"]:
                    try:
                        out["stat"][ab_up] += int(v or 0)
                    except (TypeError, ValueError):
                        pass
        save_bonus = mods.get("save")
        if isinstance(save_bonus, dict):
            for ab, v in save_bonus.items():
                ab_up = str(ab).upper()
                if ab_up in out["save"]:
                    try:
                        out["save"][ab_up] += int(v or 0)
                    except (TypeError, ValueError):
                        pass
    return out


def apply_item_modifiers(sheet: dict) -> None:
    """Applica in-place i bonus degli oggetti magici sintonizzati alla scheda.

    Il flusso è dichiarativo:
      • `sheet["ac_base"]` = CA naturale (armatura + DES, calcolata in
        `generate_sheet`). Sopravvive ai turni e all'aggiunta/rimozione di
        oggetti.
      • Ad ogni `apply_item_modifiers` la CA visibile (`sheet["ac"]`) viene
        ricalcolata come `ac_base + bonus_oggetti.ac`.
      • Stesso pattern per iniziativa, velocità, HP max, CD incantesimi,
        bonus attacco, e per i punteggi delle caratteristiche.
      • I bonus `save_all` e `save` non rientrano nei numeri base della
        scheda (in 5.5e i TS sono `d20 + mod + pb`): vengono memorizzati
        in `sheet["save_bonuses"]` (`{all:1, SAG:2, ...}`) così il
        riquadro dadi può sommarli, e l'editor può visualizzarli.

    Sicura da chiamare più volte: si rilegge sempre dal *_base.
    """
    if not isinstance(sheet, dict):
        return
    items = sheet.get("magic_items") or []
    # Prima dell'aggregazione: estrai bonus dichiarati in linguaggio naturale
    # dalla descrizione (es. "Anello di protezione · +2 CA") e auto-sintonizza
    # gli oggetti che dichiarano effetti meccanici, così la CA del PG riflette
    # davvero i bonus dichiarati nella scheda.
    normalize_magic_items(items)
    agg = _aggregate_item_modifiers(items)

    # Caratteristiche: aggiungi bonus oggetti al score_base, poi ricalcola mod.
    stats = sheet.get("stats")
    if isinstance(stats, dict):
        for ab, v in stats.items():
            if not isinstance(v, dict):
                continue
            base = v.get("score_base")
            if base is None:
                # prima passata su scheda vecchia: lo score corrente È il base
                base = v.get("score", 10)
                v["score_base"] = int(base)
            try:
                base_int = int(base)
            except (TypeError, ValueError):
                base_int = 10
            v["score"] = base_int + int(agg["stat"].get(ab, 0) or 0)
            try:
                v["mod"] = ability_mod(int(v["score"]))
            except (TypeError, ValueError):
                v["mod"] = 0

    # CA: ac_base = senza oggetti; ac = ac_base + bonus
    ac_base = sheet.get("ac_base")
    if ac_base is None:
        ac_base = sheet.get("ac", 10)
        sheet["ac_base"] = int(ac_base or 10)
    try:
        sheet["ac"] = int(sheet["ac_base"]) + int(agg.get("ac", 0) or 0)
    except (TypeError, ValueError):
        pass

    # Iniziativa, velocità, HP max: stesso pattern dichiarativo.
    for field in ("initiative", "speed", "hp_max"):
        base_key = field + "_base"
        if field == "hp_max":
            base_key = "hp_max_base"
            current_hp = sheet.get("hp")
            if not isinstance(current_hp, dict):
                continue
            if base_key not in sheet:
                sheet[base_key] = int(current_hp.get("max", 0) or 0)
            try:
                current_hp["max"] = int(sheet[base_key]) + int(agg.get("hp_max", 0) or 0)
                # HP correnti non possono superare il nuovo max
                cur = int(current_hp.get("current", 0) or 0)
                if cur > current_hp["max"]:
                    current_hp["current"] = current_hp["max"]
            except (TypeError, ValueError):
                pass
        else:
            if base_key not in sheet:
                sheet[base_key] = sheet.get(field, 0)
            try:
                sheet[field] = (float(sheet[base_key]) if field == "speed"
                                else int(sheet[base_key])) \
                               + int(agg.get(field, 0) or 0)
            except (TypeError, ValueError):
                pass

    # Bonus ai TS: memorizzati separatamente. Format:
    #   {"all": N, "FOR": N, ..., "CAR": N}
    save_bon = {"all": int(agg.get("save_all", 0) or 0)}
    for ab in _ITEM_SAVE_ABILITIES:
        save_bon[ab] = int(agg["save"].get(ab, 0) or 0)
    if any(v for v in save_bon.values()):
        sheet["save_bonuses"] = save_bon
    elif "save_bonuses" in sheet:
        # niente più bonus → rimuovi il campo per non sporcare la scheda
        sheet.pop("save_bonuses", None)


def sync_bases_from_update(sheet: dict, update: dict) -> None:
    """Allinea i campi `*_base` ai valori top-level appena scritti dal DM.

    Senza questa sincronizzazione un CHAR_UPDATE con `{"ac": 18}` viene
    sovrascritto da `apply_item_modifiers` (che ricalcola `ac` come
    `ac_base + bonus_oggetti`). Qui interpretiamo il valore scritto dal DM
    come TOTALE desiderato: ridefiniamo `ac_base = ac - bonus_oggetti` così
    il totale ricalcolato torna a coincidere col valore voluto. Stesso
    pattern per iniziativa, velocità, HP max e punteggi caratteristica.
    """
    if not isinstance(sheet, dict) or not isinstance(update, dict):
        return
    # Normalizza prima così i bonus desc-parsed contano per il ribaltamento
    # del *_base partendo dai valori totali scritti dal DM.
    items = sheet.get("magic_items") or []
    normalize_magic_items(items)
    bonus = _aggregate_item_modifiers(items)

    if "ac" in update:
        try:
            sheet["ac_base"] = int(update["ac"]) - int(bonus.get("ac", 0) or 0)
        except (TypeError, ValueError):
            pass
    if "initiative" in update:
        try:
            sheet["initiative_base"] = int(update["initiative"]) - int(bonus.get("initiative", 0) or 0)
        except (TypeError, ValueError):
            pass
    if "speed" in update:
        try:
            sheet["speed_base"] = float(update["speed"]) - int(bonus.get("speed", 0) or 0)
        except (TypeError, ValueError):
            pass
    hp_upd = update.get("hp")
    if isinstance(hp_upd, dict) and "max" in hp_upd:
        try:
            sheet["hp_max_base"] = int(hp_upd["max"]) - int(bonus.get("hp_max", 0) or 0)
        except (TypeError, ValueError):
            pass
    stats_upd = update.get("stats")
    if isinstance(stats_upd, dict):
        stats = sheet.setdefault("stats", {})
        for ab, val in stats_upd.items():
            ab_up = str(ab).upper()
            if ab_up not in ABILITIES or not isinstance(val, dict):
                continue
            if "score" not in val:
                continue
            target = stats.setdefault(ab_up, {})
            try:
                target["score_base"] = int(val["score"]) - int(bonus["stat"].get(ab_up, 0) or 0)
            except (TypeError, ValueError):
                pass


def apply_level_progression(sheet: dict) -> None:
    """Auto level-up basato sugli XP cumulati della scheda.

    Quando un CHAR_UPDATE alza gli XP oltre la soglia del livello successivo
    aggiorniamo qui: `level`, gli HP max (media del dado classe + mod COS
    per ogni nuovo livello, regola 5e per promozione a tavolo), il bonus di
    competenza, e — per gli incantatori — gli slot incantesimo
    (rigenerati col nuovo livello, preservando lo `used` dove la chiave
    esiste ancora). Senza questo passo i PG salirebbero di XP ma non di
    livello e gli slot resterebbero quelli del grado precedente.
    """
    if not isinstance(sheet, dict):
        return
    try:
        xp = int(sheet.get("xp", 0) or 0)
    except (TypeError, ValueError):
        xp = 0
    try:
        old_lv = int(sheet.get("level", 1) or 1)
    except (TypeError, ValueError):
        old_lv = 1
    new_lv = max(old_lv, level_for_xp(xp))
    cls = sheet.get("class")
    cls_data = CLASSES.get(cls, {})
    if new_lv > old_lv:
        hit_die = int(cls_data.get("hit_die", 8) or 8)
        stats = sheet.get("stats") or {}
        cos = stats.get("COS", {}) if isinstance(stats, dict) else {}
        cos_mod = int(cos.get("mod", 0) or 0) if isinstance(cos, dict) else 0
        per_level_hp = max(1, hit_die // 2 + 1 + cos_mod)
        delta = new_lv - old_lv
        hp_max_base = int(sheet.get("hp_max_base", 0) or 0)
        sheet["hp_max_base"] = hp_max_base + per_level_hp * delta
        sheet["level"] = new_lv
        hp = sheet.setdefault("hp", {"current": 0, "max": 0, "temp": 0})
        if isinstance(hp, dict):
            try:
                cur = int(hp.get("current", 0) or 0)
                hp["current"] = cur + per_level_hp * delta
            except (TypeError, ValueError):
                pass
    sheet["proficiency_bonus"] = proficiency_bonus(max(1, int(sheet.get("level", 1) or 1)))
    caster = sheet.get("caster_type") or CASTER_TYPE.get(cls, "none")
    if caster and caster != "none":
        new_slots = spell_slots(caster, int(sheet.get("level", 1) or 1))
        spells = sheet.setdefault("spells", {})
        old_slots = spells.get("slots") if isinstance(spells, dict) else {}
        if isinstance(old_slots, dict):
            for k, v in new_slots.items():
                old_sl = old_slots.get(k)
                if isinstance(old_sl, dict):
                    try:
                        v["used"] = max(0, min(int(v["max"]),
                                               int(old_sl.get("used", 0) or 0)))
                    except (TypeError, ValueError):
                        v["used"] = 0
        spells["slots"] = new_slots


def recompute_derived(sheet: dict) -> None:
    """
    Ricalcola in-place i valori derivati dopo una modifica manuale della
    scheda: modificatori di caratteristica, CD/bonus degli incantesimi, e
    bonus degli oggetti magici sintonizzati (CA, iniziativa, HP max,
    statistiche, TS).
    Da chiamare dopo un merge di modifiche dall'editor scheda o dopo un
    CHAR_UPDATE del DM (es. nuovo oggetto magico trovato).
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
        # Salva i valori "base" SE non già salvati: serve per ricalcolare
        # quando un magic_item con save_dc/attack_bonus viene tolto.
        if "_save_dc_base" not in spells:
            spells["_save_dc_base"] = 8 + pb + mod
        if "_attack_bonus_base" not in spells:
            spells["_attack_bonus_base"] = pb + mod
        spells["_save_dc_base"] = 8 + pb + mod
        spells["_attack_bonus_base"] = pb + mod
        spells["save_dc"] = spells["_save_dc_base"]
        spells["attack_bonus"] = spells["_attack_bonus_base"]
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

    # ULTIMO step: applica i bonus degli oggetti magici. Va DOPO il
    # ricalcolo base di stats/spells così sovrascrive ac, hp_max,
    # save_dc, attack_bonus con i valori complessivi (base + oggetti).
    apply_item_modifiers(sheet)

    # Riallinea save_dc/attack_bonus con i bonus oggetti se l'oggetto li
    # tocca direttamente (oltre allo stat). _aggregate_item_modifiers li
    # raccoglie ma _apply_item_modifiers non li ha applicati a spells:
    # lo facciamo qui perché ora abbiamo `_save_dc_base` certo.
    if isinstance(spells, dict) and spells.get("ability"):
        agg = _aggregate_item_modifiers(sheet.get("magic_items") or [])
        # se le stats sono cambiate per bonus item, ricalcola da capo
        ab2 = spells.get("ability")
        st2 = stats.get(ab2, {}) if isinstance(stats, dict) else {}
        mod2 = st2.get("mod", 0) if isinstance(st2, dict) else 0
        try:
            lvl = int(sheet.get("level", 1) or 1)
        except (TypeError, ValueError):
            lvl = 1
        pb2 = proficiency_bonus(lvl)
        spells["_save_dc_base"] = 8 + pb2 + mod2
        spells["_attack_bonus_base"] = pb2 + mod2
        spells["save_dc"] = spells["_save_dc_base"] + int(agg.get("save_dc", 0) or 0)
        spells["attack_bonus"] = spells["_attack_bonus_base"] + int(agg.get("attack_bonus", 0) or 0)


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

    # Inventario esteso: armi tenute (separate dall'equipaggiamento generico)
    # e oggetti magici / artefatti. Schede vecchie senza questi campi vengono
    # arricchite con liste vuote. Ogni voce può essere stringa o dict
    # {name, desc, slot, attuned, modifiers: {...}}.
    if not isinstance(sheet.get("weapons"), list):
        sheet["weapons"] = []
    # Backfill armi STRUTTURATE su schede vecchie: se `weapons` è vuoto (o
    # contiene solo stringhe del vecchio formato) ricostruiscilo dal dado di
    # danno a partire dall'equipaggiamento, così il sistema può tirare i danni.
    _w = sheet.get("weapons") or []
    _needs_weapons = (not _w) or all(not isinstance(x, dict) or "damage_roll" not in x for x in _w)
    if _needs_weapons:
        _stats = sheet.get("stats") if isinstance(sheet.get("stats"), dict) else {}
        try:
            _pb = int(sheet.get("proficiency_bonus") or proficiency_bonus(int(sheet.get("level", 1) or 1)))
        except (TypeError, ValueError):
            _pb = 2
        _built = _build_weapons(sheet.get("equipment") or [], _stats, _pb)
        if _built:
            sheet["weapons"] = _built
    if not isinstance(sheet.get("magic_items"), list):
        sheet["magic_items"] = []

    # Migrazione per il sistema modifiers: se mancano i campi `*_base`,
    # inizializzali ai valori correnti (così la prima `apply_item_modifiers`
    # non altera nulla — fino a che non viene equipaggiato un artefatto).
    if "ac_base" not in sheet:
        sheet["ac_base"] = sheet.get("ac", 10)
    if "initiative_base" not in sheet:
        sheet["initiative_base"] = sheet.get("initiative", 0)
    if "speed_base" not in sheet:
        sheet["speed_base"] = sheet.get("speed", 9)
    hp = sheet.get("hp")
    if isinstance(hp, dict) and "hp_max_base" not in sheet:
        sheet["hp_max_base"] = int(hp.get("max", 0) or 0)
    stats = sheet.get("stats")
    if isinstance(stats, dict):
        for v in stats.values():
            if isinstance(v, dict) and "score" in v and "score_base" not in v:
                try:
                    v["score_base"] = int(v["score"])
                except (TypeError, ValueError):
                    v["score_base"] = 10

    # Riallinea i valori esposti applicando i bonus degli oggetti magici
    # sintonizzati (idempotente: legge dai *_base).
    apply_item_modifiers(sheet)

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


def reset_combat_state(sheet: dict) -> dict:
    """Riporta una scheda allo stato 'fresco' di inizio partita: HP al
    MASSIMO, HP temporanei a 0, tutti gli slot incantesimo ripristinati,
    death-saves azzerati (il PG viene RESUSCITATO anche se era morto),
    condizioni e ispirazione ripulite, status 'alive'.

    NON tocca la progressione (XP, livello, oggetti, tesoro, magic_items):
    resetta solo i parametri di gioco riportandoli 'al massimo'. Usato dal
    tasto "Nuova" così la nuova partita riparte coi PG in piena forma.
    Modifica in place e restituisce la scheda."""
    if not isinstance(sheet, dict):
        return sheet

    # HP → pieni, temporanei azzerati, book-keeping anti-drift ripulito.
    hp = sheet.setdefault("hp", {})
    if isinstance(hp, dict):
        try:
            mx = int(hp.get("max"))
        except (TypeError, ValueError):
            mx = None
        if mx is not None and mx > 0:
            hp["current"] = mx
        hp["temp"] = 0
        for k in ("_last_msg_id", "_last_damage", "_last_heal", "_last_after"):
            hp.pop(k, None)

    # Death saves → azzerati e PG vivo (resuscita i morti permanenti).
    ds = sheet.get("death_saves")
    if not isinstance(ds, dict):
        ds = {}
        sheet["death_saves"] = ds
    ds["successes"] = 0
    ds["failures"]  = 0
    ds["stable"]    = False
    ds["dead"]      = False

    sheet["status"]      = "alive"
    sheet["conditions"]  = []
    sheet["inspiration"] = False

    # Slot incantesimo → tutti gli "used" a 0.
    spells = sheet.get("spells")
    if isinstance(spells, dict):
        slots = spells.get("slots")
        if isinstance(slots, dict):
            for sl in slots.values():
                if isinstance(sl, dict):
                    sl["used"] = 0

    return sheet


__all__ = [
    "SPECIES", "CLASSES", "BACKGROUNDS", "ALIGNMENTS", "GENDERS",
    "STANDARD_ARRAY", "ABILITIES", "CASTER_TYPE", "SPELL_ABILITY",
    "COIN_KINDS", "CLASS_START_GOLD", "empty_treasure",
    "generate_sheet", "recompute_derived", "upgrade_sheet",
    "reset_combat_state",
    "apply_item_modifiers", "sync_bases_from_update",
    "apply_level_progression", "normalize_magic_items",
    "species_list", "class_list",
    "background_list", "alignment_list", "gender_list",
]
