"""
Motore regole — dadi, modificatori, CA, iniziativa, XP, morte.

Il DM (IA) narra l'azione e richiede tiri via tag <ROLL_REQ>. Il codice
esegue i tiri in modo deterministico e iniettabile.
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from typing import List, Optional


# ────────────────────────────────────────────────────────────────────────
# Dadi
# ────────────────────────────────────────────────────────────────────────

# Un'espressione di dadi è una sequenza di termini con segno: gruppi di
# dadi "NdM" e modificatori piatti. Es: "2d6+3", "1d20-1", "1d20+4+1d4".
_DICE_TERM_RE = re.compile(r"([+-]?)(\d*)d(\d+)|([+-]?)(\d+)", re.IGNORECASE)


@dataclass
class RollResult:
    expr: str                 # espressione originale es "1d20+5"
    rolls: List[int]          # singoli risultati dei dadi
    modifier: int             # bonus/malus statico
    total: int                # somma rolls + modifier
    advantage: bool = False
    disadvantage: bool = False
    reason: str = ""
    is_crit: bool = False     # True se naturale 20 su un d20
    is_fumble: bool = False   # True se naturale 1 su un d20

    def to_dict(self) -> dict:
        return {
            "expr":         self.expr,
            "rolls":        self.rolls,
            "modifier":     self.modifier,
            "total":        self.total,
            "advantage":    self.advantage,
            "disadvantage": self.disadvantage,
            "reason":       self.reason,
            "is_crit":      self.is_crit,
            "is_fumble":    self.is_fumble,
        }

    def pretty(self) -> str:
        tag = " VANT" if self.advantage else (" SVANT" if self.disadvantage else "")
        crit = " ✦CRIT" if self.is_crit else (" ✗FUMBLE" if self.is_fumble else "")
        mod_s = f"{self.modifier:+d}" if self.modifier else ""
        rolls_s = "+".join(str(r) for r in self.rolls)
        reason = f" ({self.reason})" if self.reason else ""
        return f"🎲 {self.expr}{tag} → [{rolls_s}]{mod_s} = {self.total}{crit}{reason}"


def roll(expr: str, *, advantage: bool = False, disadvantage: bool = False,
         reason: str = "", rng: Optional[random.Random] = None) -> RollResult:
    """
    Tira un'espressione di dadi. Supporta PIÙ gruppi di dadi e modificatori
    nella stessa espressione: "2d6+3", "1d20-1", "d8", "4d6",
    "1d20+4+1d4" (tira 1d20, +4, poi 1d4), "1d8+1d6".
    Se advantage+disadvantage entrambi True si annullano.
    Vantaggio/svantaggio si applicano al PRIMO d20 singolo dell'espressione.
    """
    if advantage and disadvantage:
        advantage = disadvantage = False

    rng = rng or random
    expr_s = expr.strip()
    compact = expr_s.replace(" ", "")
    if not compact:
        raise ValueError(f"Espressione dadi non valida: {expr!r}")

    # Tokenizza l'espressione in termini: (segno, n, facce) per i dadi,
    # somma con segno per i modificatori piatti.
    dice_terms: list[tuple[int, int, int]] = []
    flat = 0
    pos = 0
    while pos < len(compact):
        m = _DICE_TERM_RE.match(compact, pos)
        if m is None or m.start() != pos:
            raise ValueError(f"Espressione dadi non valida: {expr!r}")
        if m.group(3) is not None:                 # gruppo di dadi "NdM"
            sign = -1 if m.group(1) == "-" else 1
            n = int(m.group(2)) if m.group(2) else 1
            faces = int(m.group(3))
            if n < 1 or n > 100:
                raise ValueError(f"Numero dadi fuori range (1-100): {n}")
            if faces < 2 or faces > 1000:
                raise ValueError(f"Facce dadi fuori range (2-1000): {faces}")
            dice_terms.append((sign, n, faces))
        else:                                       # modificatore piatto
            sign = -1 if m.group(4) == "-" else 1
            flat += sign * int(m.group(5))
        pos = m.end()

    if not dice_terms:
        raise ValueError(f"Espressione dadi non valida: {expr!r}")

    all_rolls: list[int] = []
    total = flat
    primary_d20: Optional[int] = None     # indice del d20 principale in all_rolls
    for sign, n, faces in dice_terms:
        group = [rng.randint(1, faces) for _ in range(n)]
        # Vantaggio/svantaggio: solo sul PRIMO d20 singolo positivo (5.5e)
        if faces == 20 and n == 1 and sign > 0 and primary_d20 is None:
            if advantage or disadvantage:
                second = rng.randint(1, faces)
                group = [max(group[0], second)] if advantage else [min(group[0], second)]
            primary_d20 = len(all_rolls)
        all_rolls.extend(group)
        total += sign * sum(group)

    # Crit/fumble: sul d20 principale
    natural = all_rolls[primary_d20] if primary_d20 is not None else None
    is_crit = natural == 20
    is_fumble = natural == 1

    return RollResult(
        expr=expr_s, rolls=all_rolls, modifier=flat, total=total,
        advantage=advantage, disadvantage=disadvantage, reason=reason,
        is_crit=is_crit, is_fumble=is_fumble,
    )


# ────────────────────────────────────────────────────────────────────────
# Modificatori e CA
# ────────────────────────────────────────────────────────────────────────

def ability_mod(score: int) -> int:
    """Modificatore standard (score - 10) // 2."""
    return (score - 10) // 2


def proficiency_bonus(level: int) -> int:
    """Bonus competenza per livello (5.5e: +2 a Lv1-4, +3 a Lv5-8, …, +6 a Lv17-20)."""
    return 2 + max(0, (max(1, level) - 1) // 4)


def passive_score(ability_score: int, proficient: bool, level: int,
                  bonus: int = 0) -> int:
    """Punteggio passivo (Percezione passiva ecc.): 10 + mod + comp + bonus."""
    return 10 + ability_mod(ability_score) + (proficiency_bonus(level) if proficient else 0) + bonus


def armor_class(dex_score: int, armor_base: int = 10, armor_dex_cap: Optional[int] = None,
                shield: int = 0, misc: int = 0) -> int:
    """CA: armor_base + min(mod_DES, armor_dex_cap) + shield + misc."""
    dex = ability_mod(dex_score)
    if armor_dex_cap is not None:
        dex = min(dex, armor_dex_cap)
    return armor_base + dex + shield + misc


def initiative_mod(dex_score: int, level: int = 1, proficient: bool = False) -> int:
    """Mod iniziativa: mod DES + (bonus comp se classe lo concede)."""
    return ability_mod(dex_score) + (proficiency_bonus(level) if proficient else 0)


# ────────────────────────────────────────────────────────────────────────
# XP e progressione (5.5e identico a 5e per soglie)
# ────────────────────────────────────────────────────────────────────────

XP_TABLE = {
     1: 0,       2: 300,    3: 900,    4: 2700,   5: 6500,
     6: 14000,   7: 23000,  8: 34000,  9: 48000, 10: 64000,
    11: 85000,  12: 100000,13: 120000,14: 140000,15: 165000,
    16: 195000, 17: 225000,18: 265000,19: 305000,20: 355000,
}


def level_for_xp(xp: int) -> int:
    """Restituisce il livello corrente dato l'XP totale."""
    xp = max(0, int(xp))
    lv = 1
    for level, threshold in XP_TABLE.items():
        if xp >= threshold:
            lv = level
    return lv


def xp_for_level(level: int) -> int:
    """XP totali richiesti per raggiungere un livello."""
    return XP_TABLE.get(max(1, min(20, level)), 0)


def xp_to_next(xp: int) -> Optional[int]:
    """XP mancanti per il prossimo livello. None se Lv 20."""
    lv = level_for_xp(xp)
    if lv >= 20:
        return None
    return xp_for_level(lv + 1) - xp


# ────────────────────────────────────────────────────────────────────────
# Morte e ferite
# ────────────────────────────────────────────────────────────────────────

@dataclass
class DeathSaves:
    successes: int = 0
    failures: int = 0
    stable: bool = False
    dead: bool = False


def apply_damage(hp_current: int, hp_max: int, damage: int,
                 death_saves: Optional[DeathSaves] = None) -> tuple[int, DeathSaves]:
    """
    Applica danni. Se HP scende a 0 e c'è già 0 HP, ogni colpo è 1 fallimento
    al TS morte (2 se critico). Se danno >= HP max, morte istantanea.
    Restituisce (nuovo_hp, death_saves).
    """
    ds = death_saves or DeathSaves()
    if ds.dead:
        return 0, ds

    if damage >= hp_max and hp_current <= damage:
        ds.dead = True
        return 0, ds

    new_hp = hp_current - damage
    if new_hp <= 0:
        if hp_current <= 0:
            # già a terra: 1 fallimento aggiuntivo
            ds.failures = min(3, ds.failures + 1)
            if ds.failures >= 3:
                ds.dead = True
        new_hp = 0
    return new_hp, ds


def death_save(ds: DeathSaves, rng: Optional[random.Random] = None) -> tuple[RollResult, DeathSaves]:
    """Tiro salvezza contro morte (d20, ≥10 successo, 1 = 2 fallimenti, 20 = stabile +1HP)."""
    rng = rng or random
    r = roll("1d20", reason="TS morte", rng=rng)
    nat = r.rolls[0]
    if nat == 20:
        ds.successes = 0
        ds.failures = 0
        ds.stable = True
    elif nat == 1:
        ds.failures = min(3, ds.failures + 2)
    elif nat >= 10:
        ds.successes = min(3, ds.successes + 1)
        if ds.successes >= 3:
            ds.stable = True
            ds.successes = 0
            ds.failures = 0
    else:
        ds.failures = min(3, ds.failures + 1)
    if ds.failures >= 3:
        ds.dead = True
    return r, ds


# ────────────────────────────────────────────────────────────────────────
# Weapon mastery (5.5e — 9 proprietà per classi marziali)
# ────────────────────────────────────────────────────────────────────────

WEAPON_MASTERIES = {
    "cleave":  "Cleave — un colpo extra a un nemico adiacente con stessi modificatori (no bonus danno).",
    "graze":   "Graze — se manchi, il bersaglio subisce comunque danni pari al modificatore di caratteristica.",
    "nick":    "Nick — l'attacco off-hand non costa un'azione bonus.",
    "push":    "Push — su colpo, sposti il bersaglio Grande o minore di 3m.",
    "sap":     "Sap — il bersaglio ha svantaggio al prossimo tiro per colpire fino al tuo prossimo turno.",
    "slow":    "Slow — riduci la velocità del bersaglio di 3m fino al tuo prossimo turno.",
    "topple":  "Topple — il bersaglio fa un TS COS (CD 8+comp+mod) o cade prono.",
    "vex":     "Vex — se colpisci, hai vantaggio al prossimo attacco contro lo stesso bersaglio.",
    "flex":    "Flex — l'arma può essere usata a una o due mani con dado danno aumentato.",
}


# ────────────────────────────────────────────────────────────────────────
# Slot incantesimo (5.5e — identico a 5e)
# ────────────────────────────────────────────────────────────────────────

# Incantatori "pieni" (Bardo, Chierico, Druido, Mago, Stregone).
# Livello PG 1-20 → lista numero slot per livello incantesimo (1-9).
FULL_CASTER_SLOTS = {
     1: [2],
     2: [3],
     3: [4, 2],
     4: [4, 3],
     5: [4, 3, 2],
     6: [4, 3, 3],
     7: [4, 3, 3, 1],
     8: [4, 3, 3, 2],
     9: [4, 3, 3, 3, 1],
    10: [4, 3, 3, 3, 2],
    11: [4, 3, 3, 3, 2, 1],
    12: [4, 3, 3, 3, 2, 1],
    13: [4, 3, 3, 3, 2, 1, 1],
    14: [4, 3, 3, 3, 2, 1, 1],
    15: [4, 3, 3, 3, 2, 1, 1, 1],
    16: [4, 3, 3, 3, 2, 1, 1, 1],
    17: [4, 3, 3, 3, 2, 1, 1, 1, 1],
    18: [4, 3, 3, 3, 3, 1, 1, 1, 1],
    19: [4, 3, 3, 3, 3, 2, 1, 1, 1],
    20: [4, 3, 3, 3, 3, 2, 2, 1, 1],
}

# Semi-incantatori (Ranger, Paladino). In 5.5e il Ranger ha incantesimi al Lv1.
HALF_CASTER_SLOTS = {
     1: [2],
     2: [2],
     3: [3],
     4: [3],
     5: [4, 2],
     6: [4, 2],
     7: [4, 3],
     8: [4, 3],
     9: [4, 3, 2],
    10: [4, 3, 2],
    11: [4, 3, 3],
    12: [4, 3, 3],
    13: [4, 3, 3, 1],
    14: [4, 3, 3, 1],
    15: [4, 3, 3, 2],
    16: [4, 3, 3, 2],
    17: [4, 3, 3, 3, 1],
    18: [4, 3, 3, 3, 1],
    19: [4, 3, 3, 3, 2],
    20: [4, 3, 3, 3, 2],
}

# Pact Magic del Warlock: (numero slot, livello degli slot).
WARLOCK_SLOTS = {
     1: (1, 1),  2: (2, 1),  3: (2, 2),  4: (2, 2),  5: (2, 3),
     6: (2, 3),  7: (2, 4),  8: (2, 4),  9: (2, 5), 10: (2, 5),
    11: (3, 5), 12: (3, 5), 13: (3, 5), 14: (3, 5), 15: (3, 5),
    16: (3, 5), 17: (4, 5), 18: (4, 5), 19: (4, 5), 20: (4, 5),
}


def spell_slots(caster_type: str, level: int) -> dict:
    """
    Slot incantesimo per tipo di incantatore e livello.
    Restituisce {liv_incantesimo: {"max": N, "used": 0}}.
      caster_type: "full" | "half" | "pact" | "none"
    """
    level = max(1, min(20, int(level)))
    out: dict[str, dict] = {}
    if caster_type == "full":
        table = FULL_CASTER_SLOTS.get(level, [])
        for i, n in enumerate(table, start=1):
            if n > 0:
                out[str(i)] = {"max": n, "used": 0}
    elif caster_type == "half":
        table = HALF_CASTER_SLOTS.get(level, [])
        for i, n in enumerate(table, start=1):
            if n > 0:
                out[str(i)] = {"max": n, "used": 0}
    elif caster_type == "pact":
        n, slot_lv = WARLOCK_SLOTS.get(level, (0, 1))
        if n > 0:
            out[str(slot_lv)] = {"max": n, "used": 0}
    return out


# ────────────────────────────────────────────────────────────────────────
# Helper alto livello: parser ROLL_REQ
# ────────────────────────────────────────────────────────────────────────

def parse_roll_request(payload: dict, rng: Optional[random.Random] = None) -> RollResult:
    """
    Esegue una ROLL_REQ del DM. Payload accetta:
      {"dice":"1d20+5", "advantage":true, "disadvantage":false, "reason":"attacco"}
    """
    dice = payload.get("dice") or payload.get("expr") or "1d20"
    adv = bool(payload.get("advantage", False))
    dis = bool(payload.get("disadvantage", False))
    reason = payload.get("reason", "")
    return roll(dice, advantage=adv, disadvantage=dis, reason=reason, rng=rng)


__all__ = [
    "RollResult", "roll", "ability_mod", "proficiency_bonus",
    "passive_score", "armor_class", "initiative_mod",
    "XP_TABLE", "level_for_xp", "xp_for_level", "xp_to_next",
    "DeathSaves", "apply_damage", "death_save",
    "WEAPON_MASTERIES", "parse_roll_request",
    "FULL_CASTER_SLOTS", "HALF_CASTER_SLOTS", "WARLOCK_SLOTS", "spell_slots",
]
