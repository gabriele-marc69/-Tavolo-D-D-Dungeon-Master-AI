"""
Loader e ricerca nel bestiario (bestiary.json).
Conservato file esistente. CR-filterable, ricerca per nome o id.
"""
from __future__ import annotations

import json
import os
from typing import Optional


BESTIARY_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "bestiary.json")

_cache: dict = {}


def _load() -> dict:
    if _cache:
        return _cache
    if not os.path.exists(BESTIARY_PATH):
        _cache.update({"meta": {}, "monsters": []})
        return _cache
    with open(BESTIARY_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    _cache.update(data)
    return _cache


def all_monsters() -> list[dict]:
    return _load().get("monsters", [])


def meta() -> dict:
    return _load().get("meta", {})


def find_by_id(monster_id: str) -> Optional[dict]:
    mid = (monster_id or "").strip().lower()
    for m in all_monsters():
        if m.get("id", "").lower() == mid:
            return m
    return None


def find_by_name(name: str) -> Optional[dict]:
    """Cerca per nome italiano o inglese, case-insensitive."""
    n = (name or "").strip().lower()
    if not n:
        return None
    for m in all_monsters():
        if m.get("name_it", "").lower() == n or m.get("name", "").lower() == n:
            return m
    # match parziale come fallback
    for m in all_monsters():
        if n in m.get("name_it", "").lower() or n in m.get("name", "").lower():
            return m
    return None


def _cr_to_float(cr) -> float:
    """CR può essere int, float, o stringa tipo "1/4", "1/8"."""
    if isinstance(cr, (int, float)):
        return float(cr)
    if isinstance(cr, str):
        cr = cr.strip()
        if "/" in cr:
            try:
                n, d = cr.split("/", 1)
                return float(n) / float(d)
            except ValueError:
                return 0.0
        try:
            return float(cr)
        except ValueError:
            return 0.0
    return 0.0


def filter_by_cr(cr_min: float = 0.0, cr_max: float = 30.0) -> list[dict]:
    return [m for m in all_monsters()
            if cr_min <= _cr_to_float(m.get("cr", 0)) <= cr_max]


def summary_for_encounter(cr_min: float, cr_max: float, limit: int = 10) -> list[dict]:
    """Versione compatta da iniettare nel prompt DM (no traits/actions full)."""
    monsters = filter_by_cr(cr_min, cr_max)[:limit]
    return [
        {
            "id":      m.get("id"),
            "name":    m.get("name_it") or m.get("name"),
            "cr":      m.get("cr"),
            "xp":      m.get("xp"),
            "ac":      m.get("ac"),
            "hp":      m.get("hp", {}).get("avg"),
            "habitat": m.get("habitat", []),
        }
        for m in monsters
    ]


def reload() -> dict:
    """Forza rilettura del file."""
    _cache.clear()
    return _load()


__all__ = [
    "all_monsters", "meta", "find_by_id", "find_by_name",
    "filter_by_cr", "summary_for_encounter", "reload",
]
