import io, json, pathlib, sys
if isinstance(sys.stdout, io.TextIOWrapper):
    sys.stdout.reconfigure(encoding='utf-8')

PATH = pathlib.Path("bestiary.json")
data = json.loads(PATH.read_text(encoding="utf-8"))

existing_ids = {m["id"] for m in data["monsters"]}

def add(m):
    if m["id"] not in existing_ids:
        data["monsters"].append(m)
        existing_ids.add(m["id"])

# Batch 10: Final SRD monsters
batch10 = [
    {
        "id": "couatl",
        "name": "Couatl",
        "cr": 4,
        "xp": 1100,
        "size": "Medium",
        "type": "Celestial",
        "alignment": "lawful good",
        "ac": 19,
        "hp": 97,
        "hd": "13d8+39",
        "speed": "30 ft., fly 90 ft.",
        "stats": {"str": 16, "dex": 20, "con": 17, "int": 18, "wis": 20, "cha": 18},
        "saves": {"con": 5, "wis": 7, "cha": 6},
        "damage_immunities": ["psychic", "bludgeoning/piercing/slashing from nonmagical weapons"],
        "senses": "truesight 120 ft., passive Perception 15",
        "languages": "all, telepathy 120 ft.",
        "traits": [
            {"name": "Innate Spellcasting", "desc": "Wis-based (DC 15). At will: detect evil, detect thoughts, create food and water. 3/day: bless, cure wounds, lesser restoration, protection from poison. 1/day: dream, greater restoration, scrying."},
            {"name": "Magic Weapons", "desc": "Attacks are magical."},
            {"name": "Shielded Mind", "desc": "Immune to scrying and to divination."}
        ],
        "actions": [
            {"name": "Bite", "desc": "Melee: +8, reach 5 ft. 8 (1d6+5) piercing. DC 13 Con or poisoned 1 minute. Poisoned target is also unconscious."},
            {"name": "Constrict", "desc": "Melee: +6, reach 10 ft. 10 (2d6+3) bludgeoning. Grapple (escape DC 15). Restrained while grappled."},
            {"name": "Change Shape", "desc": "Polymorphs into humanoid or beast, or back to couatl form."}
        ],
        "loot": ["couatl feathers (holy component)", "celestial essence"],
        "environment": ["jungle", "ruins", "tropical highlands"],
        "tactics": "Subdues with poison-bite sleep, constricts threats. Uses magic to aid and protect the good."
    },
    {
        "id": "guardian_naga",
        "name": "Guardian Naga",
        "cr": 10,
        "xp": 5900,
        "size": "Large",
        "type": "Monstrosity",
        "alignment": "lawful good",
        "ac": 18,
        "hp": 127,
        "hd": "15d10+45",
        "speed": "40 ft.",
        "stats": {"str": 19, "dex": 18, "con": 16, "int": 16, "wis": 19, "cha": 18},
        "saves": {"dex": 8, "con": 7, "int": 7, "wis": 8, "cha": 8},
        "damage_immunities": ["poison"],
        "condition_immunities": ["charmed", "poisoned"],
        "senses": "darkvision 60 ft., passive Perception 14",
        "languages": "Celestial, Common",
        "traits": [
            {"name": "Rejuvenation", "desc": "If it dies, reincarnates in 1d6 days with full HP if sacred site intact."},
            {"name": "Spellcasting", "desc": "11th-level caster. Wis-based (DC 16, +8). Cleric spells prepared."}
        ],
        "actions": [
            {"name": "Bite", "desc": "Melee: +8, reach 10 ft. 8 (1d8+4) piercing. DC 15 Con or 45 (10d8) poison, half on save."},
            {"name": "Spit Poison", "desc": "Ranged: +8, 15/30 ft. DC 15 Con or 45 (10d8) poison."}
        ],
        "loot": ["guardian naga scale", "holy serpent gem", "temple offering"],
        "environment": ["temple", "sacred ruins", "holy site"],
        "tactics": "Protects sacred sites. Opens with cleric spells, uses spit poison at range."
    },
    {
        "id": "spirit_naga",
        "name": "Spirit Naga",
        "cr": 8,
        "xp": 3900,
        "size": "Large",
        "type": "Monstrosity",
        "alignment": "chaotic evil",
        "ac": 15,
        "hp": 75,
        "hd": "10d10+20",
        "speed": "40 ft.",
        "stats": {"str": 18, "dex": 17, "con": 14, "int": 16, "wis": 15, "cha": 16},
        "saves": {"dex": 6, "con": 5, "wis": 5, "cha": 6},
        "damage_immunities": ["poison"],
        "condition_immunities": ["charmed", "poisoned"],
        "senses": "darkvision 60 ft., passive Perception 12",
        "languages": "Abyssal, Common",
        "traits": [
            {"name": "Rejuvenation", "desc": "Killed naga reincarnates 1d6 days later with full HP."},
            {"name": "Spellcasting", "desc": "10th-level caster. Int-based (DC 14, +6). Wizard spells prepared."}
        ],
        "actions": [
            {"name": "Bite", "desc": "Melee: +7, reach 10 ft. 7 (1d6+4) piercing. DC 13 Con or 31 (7d8) poison, half on save."}
        ],
        "loot": ["spirit naga scale", "spellbook", "dark altar components"],
        "environment": ["swamp", "dark dungeon", "corrupted temple"],
        "tactics": "Relies on wizard spells (lightning bolt, hold person, sleep), uses bite only when cornered."
    },
    {
        "id": "succubus",
        "name": "Succubus/Incubus",
        "cr": 4,
        "xp": 1100,
        "size": "Medium",
        "type": "Fiend (Shapechanger)",
        "alignment": "neutral evil",
        "ac": 15,
        "hp": 66,
        "hd": "12d8+12",
        "speed": "30 ft., fly 60 ft.",
        "stats": {"str": 8, "dex": 17, "con": 13, "int": 15, "wis": 12, "cha": 20},
        "skills": {"deception": 9, "insight": 5, "perception": 5, "persuasion": 9, "stealth": 7},
        "damage_immunities": ["bludgeoning/piercing/slashing from nonmagical weapons"],
        "resistances": ["cold", "fire", "lightning", "poison"],
        "senses": "darkvision 60 ft., passive Perception 15",
        "languages": "Abyssal, Common, Infernal, telepathy 60 ft.",
        "traits": [
            {"name": "Telepathic Bond", "desc": "Ignores range to Charm effect target while on same plane."},
            {"name": "Shapechanger", "desc": "Can polymorph into any humanoid or back."}
        ],
        "actions": [
            {"name": "Claw (Fiend Form Only)", "desc": "Melee: +5, reach 5 ft. 6 (1d6+3) slashing + 7 (2d6) psychic."},
            {"name": "Charm", "desc": "One humanoid in 30 ft. DC 15 Wis or magically charmed 1 day. Charmed treats succubus as trusted friend and resists harming it. New save if harmed."},
            {"name": "Draining Kiss", "desc": "Charmed or willing: 32 (5d10+5) psychic. Max HP reduced by same until long rest. Creature dies if at 0."},
            {"name": "Etherealness", "desc": "Enters Ethereal Plane from Material Plane or back."}
        ],
        "loot": ["soul contract fragment", "enchanted jewelry"],
        "environment": ["city (disguised)", "noble court", "tavern", "lower planes"],
        "tactics": "Infiltrates as humanoid, charms over days, uses Draining Kiss to drain life force. Etherealness to escape."
    },
    {
        "id": "myconid_sovereign",
        "name": "Myconid Sovereign",
        "cr": 2,
        "xp": 450,
        "size": "Large",
        "type": "Plant",
        "alignment": "lawful neutral",
        "ac": 13,
        "hp": 60,
        "hd": "8d10+16",
        "speed": "30 ft.",
        "stats": {"str": 18, "dex": 10, "con": 14, "int": 13, "wis": 15, "cha": 10},
        "senses": "darkvision 120 ft., passive Perception 12",
        "languages": "understands Common but communicates via Rapport Spores",
        "traits": [
            {"name": "Distress Spores", "desc": "When at 0 HP, releases spores in 240 ft. All myconids aware."},
            {"name": "Sun Sickness", "desc": "Disadvantage on attacks, checks, and saves while in sunlight."}
        ],
        "actions": [
            {"name": "Multiattack", "desc": "Two fist attacks."},
            {"name": "Fist", "desc": "Melee: +6, reach 5 ft. 8 (1d8+4) bludgeoning + 7 (2d6) poison."},
            {"name": "Animating Spores (3/Day)", "desc": "Animates dead humanoid or beast within 5 ft. as zombie under sovereign's control."},
            {"name": "Hallucination Spores", "desc": "30 ft. DC 12 Con or incapacitated 1 minute (hallucinating)."},
            {"name": "Rapport Spores", "desc": "30 ft. radius. Creatures sharing spores can communicate telepathically."}
        ],
        "loot": ["sovereign spore cluster", "myconid cap (rare component)", "underground mushroom garden"],
        "environment": ["underdark", "underground cave", "fungal forest"],
        "tactics": "Hallucinates enemies, animates any fallen as zombies, rallies other myconids."
    },
    {
        "id": "ochre_jelly",
        "name": "Ochre Jelly",
        "cr": 2,
        "xp": 450,
        "size": "Large",
        "type": "Ooze",
        "alignment": "unaligned",
        "ac": 8,
        "hp": 45,
        "hd": "6d10+12",
        "speed": "10 ft., climb 10 ft.",
        "stats": {"str": 15, "dex": 6, "con": 14, "int": 2, "wis": 6, "cha": 1},
        "damage_immunities": ["lightning", "slashing"],
        "resistances": ["acid"],
        "condition_immunities": ["blinded", "charmed", "deafened", "exhaustion", "frightened", "prone"],
        "senses": "blindsight 60 ft., passive Perception 8",
        "languages": "",
        "traits": [
            {"name": "Amorphous", "desc": "Can move through 1 inch space."},
            {"name": "Spider Climb", "desc": "Climbs ceilings and walls."},
            {"name": "Split", "desc": "Subject to lightning or slashing: splits into two smaller jellies if 10+ HP."}
        ],
        "actions": [
            {"name": "Pseudopod", "desc": "Melee: +4, reach 5 ft. 9 (2d6+2) bludgeoning + 3 (1d6) acid."}
        ],
        "loot": ["acid gland (minor)"],
        "environment": ["dungeon", "cave", "underdark"],
        "tactics": "Drops from ceiling, splits if struck with lightning/slashing, multiplying threat."
    },
    {
        "id": "gelatinous_cube",
        "name": "Gelatinous Cube",
        "cr": 2,
        "xp": 450,
        "size": "Large",
        "type": "Ooze",
        "alignment": "unaligned",
        "ac": 6,
        "hp": 84,
        "hd": "8d10+40",
        "speed": "15 ft.",
        "stats": {"str": 14, "dex": 3, "con": 20, "int": 1, "wis": 6, "cha": 1},
        "resistances": ["acid"],
        "condition_immunities": ["blinded", "charmed", "deafened", "exhaustion", "frightened", "prone"],
        "senses": "blindsight 60 ft. (blind beyond), passive Perception 8",
        "languages": "",
        "traits": [
            {"name": "Ooze Cube", "desc": "Cube occupies entire 10x10 space. Other creatures can enter the space. Transparent: DC 15 Perception to spot if not moving."},
            {"name": "Transparent", "desc": "Difficult to see when still."}
        ],
        "actions": [
            {"name": "Pseudopod", "desc": "Melee: +4, reach 5 ft. 10 (3d6) acid."},
            {"name": "Engulf", "desc": "Moves up to speed and can enter Medium or smaller creature's space. DC 12 Dex or engulfed. Engulfed: blinded, restrained, total cover. 21 (6d6) acid/turn. DC 12 Str to escape."}
        ],
        "loot": ["dissolved items inside", "acid gland"],
        "environment": ["dungeon corridor", "cave"],
        "tactics": "Slides silently down corridors, engulfs entire party members at once."
    },
    {
        "id": "flumph",
        "name": "Flumph",
        "cr": "1/8",
        "xp": 25,
        "size": "Small",
        "type": "Aberration",
        "alignment": "lawful good",
        "ac": 12,
        "hp": 7,
        "hd": "2d6",
        "speed": "5 ft., fly 30 ft. (hover)",
        "stats": {"str": 6, "dex": 15, "con": 10, "int": 14, "wis": 14, "cha": 11},
        "skills": {"arcana": 4, "history": 4, "perception": 4, "religion": 4},
        "damage_vulnerabilities": ["acid"],
        "condition_immunities": ["prone"],
        "senses": "darkvision 60 ft., passive Perception 14",
        "languages": "understands Undercommon but communicates via telepathy",
        "traits": [
            {"name": "Advanced Telepathy", "desc": "Can perceive contents of telepathic communication within 60 ft."},
            {"name": "Prone Deficiency", "desc": "If prone, can't right itself until flies up."},
            {"name": "Telepathic Shroud", "desc": "Immune to divination magic and to effects that would sense its emotions."}
        ],
        "actions": [
            {"name": "Tendrils", "desc": "Melee: +4, reach 5 ft. 4 (1d4+2) piercing + 2 (1d4) acid."},
            {"name": "Stench Spray (1/Day)", "desc": "15 ft. cone. DC 10 Dex or covered in stench for 1d4 hours. Creatures within 5 ft. of stinky target disadvantaged on attacks."}
        ],
        "loot": [],
        "environment": ["underdark", "underground"],
        "tactics": "Non-violent by nature; uses stench spray defensively. Often an informant for good-aligned PCs."
    },
    {
        "id": "troglodyte",
        "name": "Troglodyte",
        "cr": "1/4",
        "xp": 50,
        "size": "Medium",
        "type": "Humanoid",
        "alignment": "chaotic evil",
        "ac": 11,
        "hp": 13,
        "hd": "2d8+4",
        "speed": "30 ft.",
        "stats": {"str": 14, "dex": 10, "con": 14, "int": 6, "wis": 10, "cha": 6},
        "skills": {"stealth": 2},
        "senses": "darkvision 60 ft., passive Perception 10",
        "languages": "Troglodyte",
        "traits": [
            {"name": "Chameleon Skin", "desc": "Advantage on Stealth checks."},
            {"name": "Stench", "desc": "Creatures within 5 ft. at start of turn: DC 12 Con or poisoned until start of next turn."},
            {"name": "Sunlight Sensitivity", "desc": "Disadvantage on attacks and Perception in sunlight."}
        ],
        "actions": [
            {"name": "Multiattack", "desc": "Three attacks: one bite, two claws."},
            {"name": "Bite", "desc": "Melee: +4, reach 5 ft. 4 (1d4+2) piercing."},
            {"name": "Claw", "desc": "Melee: +4, reach 5 ft. 4 (1d4+2) slashing."}
        ],
        "loot": ["crude weapons", "bones", "small shiny objects"],
        "environment": ["underground", "cave", "underdark"],
        "tactics": "Packs overwhelm with stench nauseating and multiattacks. Ambush from stealth."
    },
    {
        "id": "yuan_ti_pureblood",
        "name": "Yuan-ti Pureblood",
        "cr": 1,
        "xp": 200,
        "size": "Medium",
        "type": "Humanoid (Yuan-ti)",
        "alignment": "neutral evil",
        "ac": 11,
        "hp": 40,
        "hd": "9d8",
        "speed": "30 ft.",
        "stats": {"str": 11, "dex": 12, "con": 11, "int": 13, "wis": 12, "cha": 14},
        "skills": {"deception": 6, "perception": 3, "stealth": 3},
        "damage_immunities": ["poison"],
        "condition_immunities": ["poisoned"],
        "senses": "darkvision 60 ft., passive Perception 13",
        "languages": "Abyssal, Common, Draconic",
        "traits": [
            {"name": "Innate Spellcasting (Psionics)", "desc": "Cha-based (DC 12). At will: animal friendship (snakes), poison spray. 3/day: suggestion."},
            {"name": "Magic Resistance", "desc": "Advantage on saves against spells."},
            {"name": "Shapechanger", "desc": "Polymorph into Medium snake or back."}
        ],
        "actions": [
            {"name": "Multiattack", "desc": "Two melee attacks."},
            {"name": "Scimitar", "desc": "Melee: +3, reach 5 ft. 4 (1d6+1) slashing."},
            {"name": "Shortbow", "desc": "Ranged: +3, 80/320 ft. 4 (1d6+1) piercing + 7 (2d6) poison."}
        ],
        "loot": ["yuan-ti disguise kit", "poison vial", "serpent cult symbol"],
        "environment": ["city (infiltrating)", "jungle temple", "yuan-ti stronghold"],
        "tactics": "Infiltrates human society, uses suggestion to manipulate, poisons from range."
    },
    {
        "id": "doppelganger",
        "name": "Doppelganger",
        "cr": 3,
        "xp": 700,
        "size": "Medium",
        "type": "Monstrosity (Shapechanger)",
        "alignment": "neutral",
        "ac": 14,
        "hp": 52,
        "hd": "8d8+16",
        "speed": "30 ft.",
        "stats": {"str": 11, "dex": 18, "con": 14, "int": 11, "wis": 12, "cha": 14},
        "skills": {"deception": 6, "insight": 3},
        "condition_immunities": ["charmed"],
        "senses": "darkvision 60 ft., passive Perception 11",
        "languages": "Common",
        "traits": [
            {"name": "Shapechanger", "desc": "Can polymorph into Small or Medium humanoid, or back."},
            {"name": "Ambusher", "desc": "Advantage on attacks against surprised creatures."},
            {"name": "Surprise Attack", "desc": "Extra 10 (3d6) damage if has advantage on attack and hit."}
        ],
        "actions": [
            {"name": "Multiattack", "desc": "Two slam attacks."},
            {"name": "Slam", "desc": "Melee: +6, reach 5 ft. 7 (1d6+4) bludgeoning."},
            {"name": "Read Thoughts", "desc": "One creature in 60 ft. DC 13 Wis or doppelganger reads surface thoughts for 1 minute. Advantage on Charisma checks against that creature."}
        ],
        "loot": ["shapechanger essence", "stolen identity documents"],
        "environment": ["city", "dungeon", "any civilized area"],
        "tactics": "Takes the form of a trusted NPC, reads thoughts to impersonate perfectly, ambushes when trust established."
    },
    {
        "id": "werewolf",
        "name": "Werewolf",
        "cr": 3,
        "xp": 700,
        "size": "Medium",
        "type": "Humanoid (Human, Shapechanger)",
        "alignment": "chaotic evil",
        "ac": 11,
        "hp": 58,
        "hd": "9d8+18",
        "speed": "30 ft. (40 ft. in wolf form)",
        "stats": {"str": 15, "dex": 13, "con": 14, "int": 10, "wis": 11, "cha": 10},
        "skills": {"perception": 4, "stealth": 3},
        "damage_immunities": ["bludgeoning/piercing/slashing from nonmagical weapons not made with silver"],
        "senses": "passive Perception 14",
        "languages": "Common (can't speak in wolf form)",
        "traits": [
            {"name": "Shapechanger", "desc": "Can polymorph into wolf-humanoid hybrid, wolf, or back to human."}
        ],
        "actions": [
            {"name": "Multiattack (Hybrid Only)", "desc": "Two attacks: one bite, one claws."},
            {"name": "Bite (Wolf or Hybrid)", "desc": "Melee: +4, reach 5 ft. 6 (1d8+2) piercing. DC 12 Con or cursed with lycanthropy."},
            {"name": "Claws (Hybrid Only)", "desc": "Melee: +4, reach 5 ft. 7 (2d4+2) slashing."}
        ],
        "loot": ["silver dagger (ironic)", "family heirloom", "cursed amulet"],
        "environment": ["forest", "village", "any rural area"],
        "tactics": "Lurks in human form until night, transforms for ambush. Bite spreads lycanthropy."
    },
    {
        "id": "werebear",
        "name": "Werebear",
        "cr": 5,
        "xp": 1800,
        "size": "Medium",
        "type": "Humanoid (Human, Shapechanger)",
        "alignment": "neutral good",
        "ac": 10,
        "hp": 135,
        "hd": "18d8+54",
        "speed": "30 ft. (40 ft. in bear form)",
        "stats": {"str": 19, "dex": 10, "con": 17, "int": 11, "wis": 12, "cha": 12},
        "skills": {"perception": 7},
        "damage_immunities": ["bludgeoning/piercing/slashing from nonmagical weapons not made with silver"],
        "senses": "passive Perception 17",
        "languages": "Common (can't speak in bear form)",
        "traits": [
            {"name": "Shapechanger", "desc": "Can polymorph into Large bear-humanoid hybrid, Large brown bear, or back."}
        ],
        "actions": [
            {"name": "Multiattack (Human or Hybrid)", "desc": "Two greataxe attacks (human) or two claw attacks (hybrid)."},
            {"name": "Bite (Bear or Hybrid)", "desc": "Melee: +7, reach 5 ft. 15 (2d10+4) piercing. DC 14 Con or cursed with lycanthropy."},
            {"name": "Claw (Bear or Hybrid)", "desc": "Melee: +7, reach 5 ft. 13 (2d8+4) slashing."},
            {"name": "Greataxe (Human or Hybrid)", "desc": "Melee: +7, reach 5 ft. 10 (1d12+4) slashing."}
        ],
        "loot": ["greataxe", "bearskin cloak", "forest herbs"],
        "environment": ["forest", "mountain", "wilderness"],
        "tactics": "Protects wilderness. Transforms to bear/hybrid to fight overwhelming threats."
    }
]

before = len(data["monsters"])
for m in batch10:
    add(m)
after = len(data["monsters"])

PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"Batch 10: +{after - before} mostri")
print(f"Totale: {after}")

# Print summary by CR range
import collections
cr_counts = collections.Counter()
for m in data["monsters"]:
    cr = m.get("cr", "?")
    if isinstance(cr, str) and "/" in cr:
        cr_counts["CR 0-1"] += 1
    elif isinstance(cr, (int, float)):
        if cr == 0: cr_counts["CR 0-1"] += 1
        elif cr <= 5: cr_counts["CR 1-5"] += 1
        elif cr <= 10: cr_counts["CR 6-10"] += 1
        elif cr <= 15: cr_counts["CR 11-15"] += 1
        elif cr <= 20: cr_counts["CR 16-20"] += 1
        else: cr_counts["CR 21+"] += 1
    else:
        cr_counts["CR 0-1"] += 1

print("\nDistribuzione per CR:")
for k, v in sorted(cr_counts.items()):
    print(f"  {k}: {v} mostri")
