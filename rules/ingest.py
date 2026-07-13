"""
Seed the SRD rules tables from the open 5e-bits/5e-database dataset.

The dataset is Creative-Commons SRD content (CC-BY-4.0), so it's safe to store and
even redistribute with attribution — unlike the copyrighted rulebook PDFs. We pull
two bulk JSON files (monsters, spells), map them onto our structured tables, and
upsert by their stable ``index`` slug so re-running is idempotent.

    from rules.ingest import ingest_srd
    ingest_srd()                       # into the backend's oracle.db
    ingest_srd(database_url="sqlite:///./rules.db")
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

import requests
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine, select

from .models import (Monster, Spell, DndClass, Subclass, Item, Race, SrdEntry,
                     SRD_SOURCE, OWNED_SOURCE)

RAW_BASE = "https://raw.githubusercontent.com/5e-bits/5e-database/main/src/2014/en/"
MONSTERS_URL = RAW_BASE + "5e-SRD-Monsters.json"
SPELLS_URL = RAW_BASE + "5e-SRD-Spells.json"
EQUIPMENT_URL = RAW_BASE + "5e-SRD-Equipment.json"
MAGIC_ITEMS_URL = RAW_BASE + "5e-SRD-Magic-Items.json"

# The broad "mechanics sweep": SRD categories the DM brain should be able to look
# up by name. Stored generically in ``rules_srd_entry`` (name + desc + raw JSON).
SRD_REFERENCE_URLS: dict[str, str] = {
    "conditions": RAW_BASE + "5e-SRD-Conditions.json",
    "skills": RAW_BASE + "5e-SRD-Skills.json",
    "ability-scores": RAW_BASE + "5e-SRD-Ability-Scores.json",
    "damage-types": RAW_BASE + "5e-SRD-Damage-Types.json",
    "languages": RAW_BASE + "5e-SRD-Languages.json",
    "alignments": RAW_BASE + "5e-SRD-Alignments.json",
    "magic-schools": RAW_BASE + "5e-SRD-Magic-Schools.json",
    "weapon-properties": RAW_BASE + "5e-SRD-Weapon-Properties.json",
    "proficiencies": RAW_BASE + "5e-SRD-Proficiencies.json",
    "equipment-categories": RAW_BASE + "5e-SRD-Equipment-Categories.json",
    "feats": RAW_BASE + "5e-SRD-Feats.json",
    "backgrounds": RAW_BASE + "5e-SRD-Backgrounds.json",
    "races": RAW_BASE + "5e-SRD-Races.json",
    "subraces": RAW_BASE + "5e-SRD-Subraces.json",
    "traits": RAW_BASE + "5e-SRD-Traits.json",
    "features": RAW_BASE + "5e-SRD-Features.json",
    "rule-sections": RAW_BASE + "5e-SRD-Rule-Sections.json",
}

# SRD coin -> gold-piece conversion.
_COIN_TO_GP = {"cp": 0.01, "sp": 0.1, "ep": 0.5, "gp": 1.0, "pp": 10.0}


def get_engine(database_url: Optional[str] = None) -> Engine:
    """Default to the backend's ``oracle.db`` so rules live beside characters/world."""
    if database_url is None:
        database_url = os.getenv("DATABASE_URL")
    if database_url is None:
        backend_db = Path(__file__).resolve().parent.parent / "oracle-dm-backend" / "oracle.db"
        database_url = f"sqlite:///{backend_db}"
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    return create_engine(database_url, echo=False, connect_args=connect_args)


# ----- mapping helpers -----

def _fetch(url: str) -> list[dict]:
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    return resp.json()


def _join(value: Any) -> Optional[str]:
    """SRD descriptions come as list[str]; join into one string."""
    if value is None:
        return None
    if isinstance(value, list):
        return "\n\n".join(str(v) for v in value)
    return str(value)


def _parse_ac(armor_class: Any) -> tuple[Optional[int], Optional[str]]:
    """AC is a list like [{'type':'armor','value':15,'armor':[...]}] or an int."""
    if isinstance(armor_class, int):
        return armor_class, None
    if isinstance(armor_class, list) and armor_class:
        first = armor_class[0]
        if isinstance(first, dict):
            value = first.get("value")
            desc_bits = [str(a.get("type", "")) for a in armor_class if isinstance(a, dict)]
            desc = ", ".join(b for b in desc_bits if b) or None
            return value, desc
    return None, None


def _map_monster(m: dict) -> Monster:
    ac, ac_desc = _parse_ac(m.get("armor_class"))
    return Monster(
        index_slug=m["index"],
        name=m["name"],
        size=m.get("size"),
        type=m.get("type"),
        subtype=m.get("subtype"),
        alignment=m.get("alignment"),
        armor_class=ac,
        ac_desc=ac_desc,
        hit_points=m.get("hit_points"),
        hit_dice=m.get("hit_dice"),
        hit_points_roll=m.get("hit_points_roll"),
        strength=m.get("strength"),
        dexterity=m.get("dexterity"),
        constitution=m.get("constitution"),
        intelligence=m.get("intelligence"),
        wisdom=m.get("wisdom"),
        charisma=m.get("charisma"),
        challenge_rating=m.get("challenge_rating"),
        proficiency_bonus=m.get("proficiency_bonus"),
        xp=m.get("xp"),
        languages=m.get("languages"),
        speed=m.get("speed"),
        proficiencies=m.get("proficiencies"),
        senses=m.get("senses"),
        damage_vulnerabilities=m.get("damage_vulnerabilities"),
        damage_resistances=m.get("damage_resistances"),
        damage_immunities=m.get("damage_immunities"),
        condition_immunities=m.get("condition_immunities"),
        special_abilities=m.get("special_abilities"),
        actions=m.get("actions"),
        legendary_actions=m.get("legendary_actions"),
        raw=m,
    )


def _map_spell(sp: dict) -> Spell:
    school = sp.get("school") or {}
    dc = sp.get("dc") or {}
    dc_type = (dc.get("dc_type") or {}).get("name") if isinstance(dc, dict) else None
    classes = [c.get("name") for c in (sp.get("classes") or []) if isinstance(c, dict)]
    return Spell(
        index_slug=sp["index"],
        name=sp["name"],
        level=sp.get("level", 0),
        school=school.get("name") if isinstance(school, dict) else None,
        casting_time=sp.get("casting_time"),
        range=sp.get("range"),
        duration=sp.get("duration"),
        material=sp.get("material"),
        concentration=bool(sp.get("concentration", False)),
        ritual=bool(sp.get("ritual", False)),
        attack_type=sp.get("attack_type"),
        dc_type=dc_type,
        dc_success=dc.get("dc_success") if isinstance(dc, dict) else None,
        components=sp.get("components"),
        classes=classes,
        damage=sp.get("damage"),
        desc=_join(sp.get("desc")),
        higher_level=_join(sp.get("higher_level")),
        raw=sp,
    )


# ----- ingest -----

def _upsert(session: Session, model, index_slug: str, mapped) -> bool:
    """Insert or update by index_slug. Returns True if newly created."""
    existing = session.exec(select(model).where(model.index_slug == index_slug)).first()
    if existing:
        data = mapped.model_dump(exclude={"id", "created_at"})
        for k, v in data.items():
            setattr(existing, k, v)
        session.add(existing)
        return False
    session.add(mapped)
    return True


def _upsert_entry(session: Session, mapped: SrdEntry) -> bool:
    """Insert/update an SrdEntry by its composite ``entry_key``."""
    existing = session.exec(
        select(SrdEntry).where(SrdEntry.entry_key == mapped.entry_key)
    ).first()
    if existing:
        data = mapped.model_dump(exclude={"id", "created_at"})
        for k, v in data.items():
            setattr(existing, k, v)
        session.add(existing)
        return False
    session.add(mapped)
    return True


def _normalize_cost_gp(cost: Any) -> Optional[float]:
    """SRD cost {'quantity': N, 'unit': 'gp'} -> float gold pieces."""
    if not isinstance(cost, dict):
        return None
    qty = cost.get("quantity")
    unit = (cost.get("unit") or "gp").lower()
    if qty is None:
        return None
    return round(float(qty) * _COIN_TO_GP.get(unit, 1.0), 4)


def _map_item(e: dict) -> Item:
    cat = (e.get("equipment_category") or {}).get("index")
    dmg = e.get("damage") or {}
    two = e.get("two_handed_damage") or {}
    rng = e.get("range") or {}
    armor = e.get("armor_class") or {}
    item_type = (
        e.get("weapon_category")
        or e.get("armor_category")
        or (e.get("gear_category") or {}).get("name")
        or (e.get("tool_category"))
        or (e.get("vehicle_category"))
    )
    return Item(
        index_slug=e["index"],
        name=e["name"],
        category=cat,
        item_type=item_type,
        cost_gp=_normalize_cost_gp(e.get("cost")),
        weight=e.get("weight"),
        damage_dice=dmg.get("damage_dice"),
        damage_type=(dmg.get("damage_type") or {}).get("name"),
        two_handed_damage_dice=two.get("damage_dice"),
        range_normal=rng.get("normal"),
        range_long=rng.get("long"),
        properties=[p.get("name") for p in (e.get("properties") or [])] or None,
        armor_class_base=armor.get("base"),
        armor_dex_bonus=armor.get("dex_bonus"),
        armor_max_dex_bonus=armor.get("max_bonus"),
        str_minimum=e.get("str_minimum"),
        stealth_disadvantage=e.get("stealth_disadvantage"),
        desc=_join(e.get("desc")),
        source=SRD_SOURCE,
        raw=e,
    )


def _map_magic_item(e: dict) -> Item:
    desc = _join(e.get("desc")) or ""
    return Item(
        index_slug=e["index"],
        name=e["name"],
        category="magic-item",
        item_type=(e.get("equipment_category") or {}).get("name"),
        rarity=(e.get("rarity") or {}).get("name"),
        requires_attunement="attunement" in desc.lower(),
        desc=desc or None,
        source=SRD_SOURCE,
        raw=e,
    )


def ingest_items(
    engine: Optional[Engine] = None,
    database_url: Optional[str] = None,
    *,
    equipment: bool = True,
    magic_items: bool = True,
) -> dict:
    """Download and upsert SRD equipment + magic items. Returns counts."""
    engine = engine or get_engine(database_url)
    SQLModel.metadata.create_all(engine)
    result = {"items_new": 0, "items_total": 0}

    if equipment:
        data = _fetch(EQUIPMENT_URL)
        with Session(engine) as s:
            for e in data:
                if _upsert(s, Item, e["index"], _map_item(e)):
                    result["items_new"] += 1
            s.commit()
        result["items_total"] += len(data)

    if magic_items:
        data = _fetch(MAGIC_ITEMS_URL)
        with Session(engine) as s:
            for e in data:
                if _upsert(s, Item, e["index"], _map_magic_item(e)):
                    result["items_new"] += 1
            s.commit()
        result["items_total"] += len(data)

    return result


def ingest_reference(
    engine: Optional[Engine] = None,
    database_url: Optional[str] = None,
    *,
    categories: Optional[list[str]] = None,
) -> dict:
    """Sweep the broad SRD mechanics into ``rules_srd_entry`` (one flexible table).

    ``categories`` defaults to every entry in ``SRD_REFERENCE_URLS``.
    """
    engine = engine or get_engine(database_url)
    SQLModel.metadata.create_all(engine)
    cats = categories or list(SRD_REFERENCE_URLS.keys())
    result: dict[str, int] = {"entries_new": 0, "entries_total": 0}

    for cat in cats:
        url = SRD_REFERENCE_URLS.get(cat)
        if not url:
            continue
        try:
            data = _fetch(url)
        except Exception as e:  # pragma: no cover - one bad category shouldn't abort
            print(f"[ingest_reference] skip {cat}: {e}")
            continue
        with Session(engine) as s:
            for obj in data:
                slug = obj.get("index") or obj.get("name", "").lower().replace(" ", "-")
                entry = SrdEntry(
                    entry_key=f"{cat}:{slug}",
                    category=cat,
                    index_slug=slug,
                    name=obj.get("name", slug),
                    desc=_join(obj.get("desc")),
                    data=obj,
                    source=SRD_SOURCE,
                )
                if _upsert_entry(s, entry):
                    result["entries_new"] += 1
            s.commit()
        result["entries_total"] += len(data)
        result[cat] = len(data)

    return result


def ingest_srd(
    engine: Optional[Engine] = None,
    database_url: Optional[str] = None,
    *,
    monsters: bool = True,
    spells: bool = True,
) -> dict:
    """Download and upsert SRD monsters/spells. Returns counts."""
    engine = engine or get_engine(database_url)
    SQLModel.metadata.create_all(engine)

    result = {"monsters_new": 0, "monsters_total": 0, "spells_new": 0, "spells_total": 0}

    if monsters:
        data = _fetch(MONSTERS_URL)
        with Session(engine) as s:
            for m in data:
                if _upsert(s, Monster, m["index"], _map_monster(m)):
                    result["monsters_new"] += 1
            s.commit()
        result["monsters_total"] = len(data)

    if spells:
        data = _fetch(SPELLS_URL)
        with Session(engine) as s:
            for sp in data:
                if _upsert(s, Spell, sp["index"], _map_spell(sp)):
                    result["spells_new"] += 1
            s.commit()
        result["spells_total"] = len(data)

    return result


# ----- classes & subclasses (hand-authored, offline) -----
#
# The 5e SRD only includes ONE subclass per class. We seed the 12 core classes
# and their SRD subclass, PLUS non-SRD subclasses the player owns (e.g. the
# Bladesinger from Tasha's Cauldron of Everything). Owned content is stored as
# concise mechanical facts in our own words (feature name + level + one-line
# effect), never verbatim book prose, and tagged with ``OWNED_SOURCE``.

_CLASSES: list[dict] = [
    {"slug": "barbarian", "name": "Barbarian", "hit_die": 12, "primary_ability": "STR",
     "subclass_label": "Primal Path", "subclass_level": 3, "spellcasting_ability": None,
     "saving_throws": ["STR", "CON"]},
    {"slug": "bard", "name": "Bard", "hit_die": 8, "primary_ability": "CHA",
     "subclass_label": "Bard College", "subclass_level": 3, "spellcasting_ability": "CHA",
     "saving_throws": ["DEX", "CHA"]},
    {"slug": "cleric", "name": "Cleric", "hit_die": 8, "primary_ability": "WIS",
     "subclass_label": "Divine Domain", "subclass_level": 1, "spellcasting_ability": "WIS",
     "saving_throws": ["WIS", "CHA"]},
    {"slug": "druid", "name": "Druid", "hit_die": 8, "primary_ability": "WIS",
     "subclass_label": "Druid Circle", "subclass_level": 2, "spellcasting_ability": "WIS",
     "saving_throws": ["INT", "WIS"]},
    {"slug": "fighter", "name": "Fighter", "hit_die": 10, "primary_ability": "STR or DEX",
     "subclass_label": "Martial Archetype", "subclass_level": 3, "spellcasting_ability": None,
     "saving_throws": ["STR", "CON"]},
    {"slug": "monk", "name": "Monk", "hit_die": 8, "primary_ability": "DEX & WIS",
     "subclass_label": "Monastic Tradition", "subclass_level": 3, "spellcasting_ability": None,
     "saving_throws": ["STR", "DEX"]},
    {"slug": "paladin", "name": "Paladin", "hit_die": 10, "primary_ability": "STR & CHA",
     "subclass_label": "Sacred Oath", "subclass_level": 3, "spellcasting_ability": "CHA",
     "saving_throws": ["WIS", "CHA"]},
    {"slug": "ranger", "name": "Ranger", "hit_die": 10, "primary_ability": "DEX & WIS",
     "subclass_label": "Ranger Archetype", "subclass_level": 3, "spellcasting_ability": "WIS",
     "saving_throws": ["STR", "DEX"]},
    {"slug": "rogue", "name": "Rogue", "hit_die": 8, "primary_ability": "DEX",
     "subclass_label": "Roguish Archetype", "subclass_level": 3, "spellcasting_ability": None,
     "saving_throws": ["DEX", "INT"]},
    {"slug": "sorcerer", "name": "Sorcerer", "hit_die": 6, "primary_ability": "CHA",
     "subclass_label": "Sorcerous Origin", "subclass_level": 1, "spellcasting_ability": "CHA",
     "saving_throws": ["CON", "CHA"]},
    {"slug": "warlock", "name": "Warlock", "hit_die": 8, "primary_ability": "CHA",
     "subclass_label": "Otherworldly Patron", "subclass_level": 1, "spellcasting_ability": "CHA",
     "saving_throws": ["WIS", "CHA"]},
    {"slug": "wizard", "name": "Wizard", "hit_die": 6, "primary_ability": "INT",
     "subclass_label": "Arcane Tradition", "subclass_level": 2, "spellcasting_ability": "INT",
     "saving_throws": ["INT", "WIS"]},
]

# Level-1 skill proficiencies per class (SRD): choose N from the options.
_CLASS_SKILLS: dict[str, tuple[int, list[str]]] = {
    "barbarian": (2, ["Animal Handling", "Athletics", "Intimidation", "Nature",
                      "Perception", "Survival"]),
    "bard":      (3, ["Acrobatics", "Animal Handling", "Arcana", "Athletics", "Deception",
                      "History", "Insight", "Intimidation", "Investigation", "Medicine",
                      "Nature", "Perception", "Performance", "Persuasion", "Religion",
                      "Sleight of Hand", "Stealth", "Survival"]),
    "cleric":    (2, ["History", "Insight", "Medicine", "Persuasion", "Religion"]),
    "druid":     (2, ["Arcana", "Animal Handling", "Insight", "Medicine", "Nature",
                      "Perception", "Religion", "Survival"]),
    "fighter":   (2, ["Acrobatics", "Animal Handling", "Athletics", "History", "Insight",
                      "Intimidation", "Perception", "Survival"]),
    "monk":      (2, ["Acrobatics", "Athletics", "History", "Insight", "Religion",
                      "Stealth"]),
    "paladin":   (2, ["Athletics", "Insight", "Intimidation", "Medicine", "Persuasion",
                      "Religion"]),
    "ranger":    (3, ["Animal Handling", "Athletics", "Insight", "Investigation", "Nature",
                      "Perception", "Stealth", "Survival"]),
    "rogue":     (4, ["Acrobatics", "Athletics", "Deception", "Insight", "Intimidation",
                      "Investigation", "Perception", "Performance", "Persuasion",
                      "Sleight of Hand", "Stealth"]),
    "sorcerer":  (2, ["Arcana", "Deception", "Insight", "Intimidation", "Persuasion",
                      "Religion"]),
    "warlock":   (2, ["Arcana", "Deception", "History", "Intimidation", "Investigation",
                      "Nature", "Religion"]),
    "wizard":    (2, ["Arcana", "History", "Insight", "Investigation", "Medicine",
                      "Religion"]),
}

# Playable races (SRD 5.1 core versions), offline — mechanically exact bonuses
# so character creation is deterministic. Trait text is our own concise wording.
_RACES: list[dict] = [
    {"slug": "human", "name": "Human", "bonuses": {"str": 1, "dex": 1, "con": 1,
                                                    "int": 1, "wis": 1, "cha": 1},
     "speed": 30, "size": "Medium", "darkvision": False,
     "languages": "Common + one extra of your choice",
     "traits": ["Versatile: +1 to every ability score"]},
    {"slug": "dwarf", "name": "Dwarf (Hill)", "bonuses": {"con": 2, "wis": 1},
     "speed": 25, "size": "Medium", "darkvision": True,
     "languages": "Common, Dwarvish",
     "traits": ["Dwarven Resilience: advantage on saves vs poison; resistance to poison damage",
                "Dwarven Toughness: +1 HP per level",
                "Stonecunning: expertise on History checks about stonework"]},
    {"slug": "elf", "name": "Elf (High)", "bonuses": {"dex": 2, "int": 1},
     "speed": 30, "size": "Medium", "darkvision": True,
     "languages": "Common, Elvish + one extra",
     "traits": ["Fey Ancestry: advantage on saves vs charm; magic can't put you to sleep",
                "Keen Senses: proficiency in Perception",
                "Trance: 4-hour meditation replaces sleep",
                "Cantrip: one wizard cantrip (INT)"]},
    {"slug": "halfling", "name": "Halfling (Lightfoot)", "bonuses": {"dex": 2, "cha": 1},
     "speed": 25, "size": "Small", "darkvision": False,
     "languages": "Common, Halfling",
     "traits": ["Lucky: reroll natural 1s on d20 (must use new roll)",
                "Brave: advantage on saves vs frightened",
                "Naturally Stealthy: can hide behind bigger creatures"]},
    {"slug": "dragonborn", "name": "Dragonborn", "bonuses": {"str": 2, "cha": 1},
     "speed": 30, "size": "Medium", "darkvision": False,
     "languages": "Common, Draconic",
     "traits": ["Breath Weapon: exhale elemental damage (by ancestry; DC 8+CON+prof)",
                "Damage Resistance: your ancestry's damage type"]},
    {"slug": "gnome", "name": "Gnome (Rock)", "bonuses": {"int": 2, "con": 1},
     "speed": 25, "size": "Small", "darkvision": True,
     "languages": "Common, Gnomish",
     "traits": ["Gnome Cunning: advantage on INT/WIS/CHA saves vs magic",
                "Artificer's Lore: expertise on History about magic/tech items",
                "Tinker: build tiny clockwork devices"]},
    {"slug": "half-elf", "name": "Half-Elf",
     "bonuses": {"cha": 2}, "choose_bonus": [1, 1],
     "speed": 30, "size": "Medium", "darkvision": True,
     "languages": "Common, Elvish + one extra",
     "traits": ["Fey Ancestry: advantage on saves vs charm; magic can't put you to sleep",
                "Skill Versatility: proficiency in two skills of your choice (pick with class skills)",
                "+1 to two different abilities of your choice (besides CHA)"]},
    {"slug": "half-orc", "name": "Half-Orc", "bonuses": {"str": 2, "con": 1},
     "speed": 30, "size": "Medium", "darkvision": True,
     "languages": "Common, Orc",
     "traits": ["Menacing: proficiency in Intimidation",
                "Relentless Endurance: drop to 1 HP instead of 0 (once per long rest)",
                "Savage Attacks: extra weapon die on melee crits"]},
    {"slug": "tiefling", "name": "Tiefling", "bonuses": {"cha": 2, "int": 1},
     "speed": 30, "size": "Medium", "darkvision": True,
     "languages": "Common, Infernal",
     "traits": ["Hellish Resistance: resistance to fire damage",
                "Infernal Legacy: thaumaturgy cantrip (CHA); more spells at higher levels"]},
    {"slug": "custom-lineage", "name": "Custom Lineage",
     "bonuses": {}, "choose_bonus": [2, 1],
     "speed": 30, "size": "Medium", "darkvision": False,
     "languages": "Common + one extra of your choice",
     "traits": ["Describe your own people: +2 to one ability and +1 to another (your choice)",
                "One extra skill proficiency of your choice (picked with class skills)",
                "Darkvision OR one additional language (your choice)"],
     "source": "House rules (Custom Lineage variant)"},
]


def seed_races(
    engine: Optional[Engine] = None,
    database_url: Optional[str] = None,
) -> dict:
    """Seed playable races. Offline and idempotent (upsert by slug)."""
    engine = engine or get_engine(database_url)
    SQLModel.metadata.create_all(engine)
    result = {"races_new": 0, "races_total": len(_RACES)}
    with Session(engine) as s:
        for r in _RACES:
            mapped = Race(
                index_slug=r["slug"], name=r["name"],
                ability_bonuses=r.get("bonuses") or {},
                choose_bonus=r.get("choose_bonus"),
                speed=r.get("speed", 30), size=r.get("size", "Medium"),
                darkvision=bool(r.get("darkvision")),
                languages=r.get("languages"), traits=r.get("traits"),
                description=r.get("description"),
                source=r.get("source", SRD_SOURCE),
            )
            if _upsert(s, Race, r["slug"], mapped):
                result["races_new"] += 1
        s.commit()
    return result


# SRD subclass per class (one each), summarized in our own words.
_SUBCLASSES: list[dict] = [
    {"slug": "berserker", "name": "Path of the Berserker", "class_name": "Barbarian",
     "source": SRD_SOURCE,
     "description": "A barbarian who channels rage into unbridled, reckless violence.",
     "features": [
         {"level": 3, "name": "Frenzy", "summary": "While raging, make a bonus-action melee attack each turn; suffer exhaustion when the rage ends."},
         {"level": 6, "name": "Mindless Rage", "summary": "Can't be charmed or frightened while raging."},
         {"level": 10, "name": "Intimidating Presence", "summary": "Frighten a creature as an action (Wis save)."},
         {"level": 14, "name": "Retaliation", "summary": "Reaction melee attack against a creature that damages you."},
     ]},
    {"slug": "college-of-lore", "name": "College of Lore", "class_name": "Bard",
     "source": SRD_SOURCE,
     "description": "Bards who collect secrets and lore, wielding Cutting Words to undercut foes.",
     "features": [
         {"level": 3, "name": "Bonus Proficiencies", "summary": "Gain proficiency with three skills of your choice."},
         {"level": 3, "name": "Cutting Words", "summary": "Reaction: spend a Bardic Inspiration die to subtract from an enemy's roll."},
         {"level": 6, "name": "Additional Magical Secrets", "summary": "Learn two spells from any class's list."},
         {"level": 14, "name": "Peerless Skill", "summary": "Add a Bardic Inspiration die to your own ability check."},
     ]},
    {"slug": "life-domain", "name": "Life Domain", "class_name": "Cleric",
     "source": SRD_SOURCE,
     "description": "Clerics devoted to healing and the vitality of the living.",
     "features": [
         {"level": 1, "name": "Disciple of Life", "summary": "Healing spells restore extra HP (2 + spell level)."},
         {"level": 1, "name": "Bonus Proficiency", "summary": "Proficiency with heavy armor."},
         {"level": 2, "name": "Channel Divinity: Preserve Life", "summary": "Restore HP equal to 5x cleric level, split among creatures."},
         {"level": 6, "name": "Blessed Healer", "summary": "Healing others also heals you."},
         {"level": 8, "name": "Divine Strike", "summary": "Weapon attacks deal +1d8 radiant damage (2d8 at 14th)."},
         {"level": 17, "name": "Supreme Healing", "summary": "Healing dice are treated as their maximum value."},
     ]},
    {"slug": "circle-of-the-land", "name": "Circle of the Land", "class_name": "Druid",
     "source": SRD_SOURCE,
     "description": "Druids drawing power from a chosen terrain, gaining bonus spells.",
     "features": [
         {"level": 2, "name": "Natural Recovery", "summary": "Recover some spell slots on a short rest."},
         {"level": 3, "name": "Circle Spells", "summary": "Bonus always-prepared spells tied to your chosen land."},
         {"level": 6, "name": "Land's Stride", "summary": "Move through nonmagical difficult terrain freely; advantage vs. plant hazards."},
         {"level": 10, "name": "Nature's Ward", "summary": "Immune to charm/fright by elementals and fey; can't be poisoned/diseased."},
         {"level": 14, "name": "Nature's Sanctuary", "summary": "Beasts and plants must save to attack you."},
     ]},
    {"slug": "champion", "name": "Champion", "class_name": "Fighter",
     "source": SRD_SOURCE,
     "description": "A martial archetype focused on raw physical prowess and critical strikes.",
     "features": [
         {"level": 3, "name": "Improved Critical", "summary": "Weapon attacks crit on a 19-20."},
         {"level": 7, "name": "Remarkable Athlete", "summary": "Add half proficiency to Str/Dex/Con checks; longer running jumps."},
         {"level": 10, "name": "Additional Fighting Style", "summary": "Choose a second Fighting Style."},
         {"level": 15, "name": "Superior Critical", "summary": "Weapon attacks crit on a 18-20."},
         {"level": 18, "name": "Survivor", "summary": "Regain HP each turn while bloodied and above 0."},
     ]},
    {"slug": "way-of-the-open-hand", "name": "Way of the Open Hand", "class_name": "Monk",
     "source": SRD_SOURCE,
     "description": "Masters of unarmed combat who manipulate a foe's ki and body.",
     "features": [
         {"level": 3, "name": "Open Hand Technique", "summary": "Flurry of Blows can knock prone, push 15 ft, or deny reactions (save)."},
         {"level": 6, "name": "Wholeness of Body", "summary": "Action: heal yourself HP equal to 3x monk level, once per long rest."},
         {"level": 11, "name": "Tranquility", "summary": "Begin each day under a sanctuary-like effect until you attack."},
         {"level": 17, "name": "Quivering Palm", "summary": "Set lethal vibrations; later spend ki to force a devastating Con save."},
     ]},
    {"slug": "oath-of-devotion", "name": "Oath of Devotion", "class_name": "Paladin",
     "source": SRD_SOURCE,
     "description": "Paladins bound to the ideals of honor, virtue, and justice.",
     "features": [
         {"level": 3, "name": "Channel Divinity: Sacred Weapon / Turn the Unholy", "summary": "Bless a weapon with +Cha to hit and light, or turn fiends and undead."},
         {"level": 7, "name": "Aura of Devotion", "summary": "You and nearby allies can't be charmed."},
         {"level": 15, "name": "Purity of Spirit", "summary": "Always under a protection-from-evil-and-good effect."},
         {"level": 20, "name": "Holy Nimbus", "summary": "Emanate sunlight that damages fiends/undead and aids your saves."},
     ]},
    {"slug": "hunter", "name": "Hunter", "class_name": "Ranger",
     "source": SRD_SOURCE,
     "description": "A ranger archetype specialized in slaying dangerous prey.",
     "features": [
         {"level": 3, "name": "Hunter's Prey", "summary": "Choose Colossus Slayer, Giant Killer, or Horde Breaker."},
         {"level": 7, "name": "Defensive Tactics", "summary": "Choose Escape the Horde, Multiattack Defense, or Steel Will."},
         {"level": 11, "name": "Multiattack", "summary": "Choose Volley (AoE ranged) or Whirlwind Attack (AoE melee)."},
         {"level": 15, "name": "Superior Hunter's Defense", "summary": "Choose a powerful defensive reaction such as Evasion or Stand Against the Tide."},
     ]},
    {"slug": "thief", "name": "Thief", "class_name": "Rogue",
     "source": SRD_SOURCE,
     "description": "A roguish archetype of nimble burglars and daring climbers.",
     "features": [
         {"level": 3, "name": "Fast Hands", "summary": "Use Cunning Action to Sleight of Hand, use objects, or disarm traps."},
         {"level": 3, "name": "Second-Story Work", "summary": "Faster climbing; longer running jumps."},
         {"level": 9, "name": "Supreme Sneak", "summary": "Advantage on Stealth if you move no more than half speed."},
         {"level": 13, "name": "Use Magic Device", "summary": "Ignore class, race, and level requirements on magic items."},
         {"level": 17, "name": "Thief's Reflexes", "summary": "Take two turns during the first round of combat."},
     ]},
    {"slug": "draconic-bloodline", "name": "Draconic Bloodline", "class_name": "Sorcerer",
     "source": SRD_SOURCE,
     "description": "A sorcerer whose innate magic springs from draconic ancestry.",
     "features": [
         {"level": 1, "name": "Dragon Ancestor", "summary": "Choose a dragon type; gain doubled proficiency on Cha checks with dragons."},
         {"level": 1, "name": "Draconic Resilience", "summary": "+1 HP per level and unarmored AC 13 + Dex."},
         {"level": 6, "name": "Elemental Affinity", "summary": "Add Cha to one damage roll of your ancestry's element; optionally gain resistance."},
         {"level": 14, "name": "Dragon Wings", "summary": "Sprout wings and gain a flying speed."},
         {"level": 18, "name": "Draconic Presence", "summary": "Aura that charms or frightens nearby creatures (save)."},
     ]},
    {"slug": "the-fiend", "name": "The Fiend", "class_name": "Warlock",
     "source": SRD_SOURCE,
     "description": "A warlock pact with a fiend of the Lower Planes.",
     "features": [
         {"level": 1, "name": "Dark One's Blessing", "summary": "Gain temporary HP when you reduce an enemy to 0 HP."},
         {"level": 6, "name": "Dark One's Own Luck", "summary": "Add 1d10 to an ability check or save once per short rest."},
         {"level": 10, "name": "Fiendish Resilience", "summary": "Choose a damage type to resist after a rest."},
         {"level": 14, "name": "Hurl Through Hell", "summary": "Banish a hit creature through the Lower Planes for 10d10 psychic damage."},
     ]},
    {"slug": "school-of-evocation", "name": "School of Evocation", "class_name": "Wizard",
     "source": SRD_SOURCE,
     "description": "Wizards who shape raw elemental energy into devastating spells.",
     "features": [
         {"level": 2, "name": "Evocation Savant", "summary": "Copy evocation spells into your book at half time and cost."},
         {"level": 2, "name": "Sculpt Spells", "summary": "Carve safe pockets so allies avoid your area spells."},
         {"level": 6, "name": "Potent Cantrip", "summary": "Damage cantrips still deal half on a successful save."},
         {"level": 10, "name": "Empowered Evocation", "summary": "Add Int modifier to one damage roll of an evocation spell."},
         {"level": 14, "name": "Overchannel", "summary": "Deal maximum damage with a leveled spell, at the cost of backlash if overused."},
     ]},
    # ---- Owned, non-SRD ----
    {"slug": "bladesinger", "name": "Bladesinger", "class_name": "Wizard",
     "source": OWNED_SOURCE,
     "description": ("An elven Arcane Tradition of warrior-mages who blend swordplay and "
                     "spellcraft into a single graceful martial art. (Owned: Tasha's "
                     "Cauldron of Everything.)"),
     "features": [
         {"level": 2, "name": "Training in War and Song", "summary": "Gain proficiency with light armor, one one-handed melee weapon, and the Performance skill."},
         {"level": 2, "name": "Bladesong", "summary": "Bonus action to activate (prof-bonus uses/long rest, ~1 min): +Int to AC, +Int to Concentration saves, +10 ft speed, and advantage on Acrobatics while unarmored."},
         {"level": 6, "name": "Extra Attack", "summary": "Attack twice when taking the Attack action; may replace one attack with a cantrip."},
         {"level": 10, "name": "Song of Defense", "summary": "While Bladesong is active, expend a spell slot as a reaction to reduce damage by 5 per slot level."},
         {"level": 14, "name": "Song of Victory", "summary": "While Bladesong is active, add your Int modifier to melee weapon damage."},
     ]},
    {"slug": "way-of-the-long-death", "name": "Way of the Long Death", "class_name": "Monk",
     "source": OWNED_SOURCE,
     "description": ("A Monastic Tradition of monks obsessed with the mechanics of dying, "
                     "turning the study of death into a deadly fighting style. (Owned: "
                     "Sword Coast Adventurer's Guide.)"),
     "features": [
         {"level": 3, "name": "Touch of Death", "summary": "When you reduce a creature within 5 ft to 0 HP, gain temporary HP equal to Wis modifier + monk level (min 1)."},
         {"level": 6, "name": "Hour of Reaping", "summary": "Action: each creature within 30 ft that can see you must succeed on a Wisdom save or be frightened until the end of your next turn."},
         {"level": 11, "name": "Mastery of Death", "summary": "When reduced to 0 HP, expend 1 ki point (no action) to drop to 1 HP instead."},
         {"level": 17, "name": "Touch of the Long Death", "summary": "Action: touch a creature within 5 ft and spend 1-10 ki; it makes a Con save, taking 2d10 necrotic per ki spent (half on success)."},
     ]},
]


def seed_classes_and_subclasses(
    engine: Optional[Engine] = None,
    database_url: Optional[str] = None,
) -> dict:
    """Seed the core classes + their subclasses (incl. owned non-SRD ones).

    Offline and idempotent (upsert by ``index_slug``). Returns counts.
    """
    engine = engine or get_engine(database_url)
    SQLModel.metadata.create_all(engine)

    # Self-heal: create_all never ALTERs, so add the skill columns to
    # pre-existing rules_class tables before upserting rows that use them.
    with engine.connect() as conn:
        existing_cols = {row[1] for row in
                         conn.exec_driver_sql('PRAGMA table_info("rules_class")')}
        for col, ddl in [("skill_choices_n", "INTEGER DEFAULT 2"),
                         ("skill_options", "JSON")]:
            if existing_cols and col not in existing_cols:
                conn.exec_driver_sql(f'ALTER TABLE "rules_class" ADD COLUMN {col} {ddl}')
        conn.commit()

    result = {"classes_new": 0, "classes_total": len(_CLASSES),
              "subclasses_new": 0, "subclasses_total": len(_SUBCLASSES)}

    by_slug = {c["slug"]: c for c in _CLASSES}

    with Session(engine) as s:
        for c in _CLASSES:
            skills_n, skills = _CLASS_SKILLS.get(c["slug"], (2, []))
            mapped = DndClass(
                index_slug=c["slug"], name=c["name"], hit_die=c.get("hit_die"),
                primary_ability=c.get("primary_ability"),
                subclass_label=c.get("subclass_label"),
                subclass_level=c.get("subclass_level", 3),
                spellcasting_ability=c.get("spellcasting_ability"),
                skill_choices_n=skills_n, skill_options=skills,
                saving_throws=c.get("saving_throws"),
                description=c.get("description"),
                source=c.get("source", SRD_SOURCE),
            )
            if _upsert(s, DndClass, c["slug"], mapped):
                result["classes_new"] += 1

        for sub in _SUBCLASSES:
            parent = by_slug.get(sub["class_name"].lower())
            mapped = Subclass(
                index_slug=sub["slug"], name=sub["name"], class_name=sub["class_name"],
                class_slug=parent["slug"] if parent else None,
                features=sub.get("features"), description=sub.get("description"),
                source=sub.get("source", SRD_SOURCE),
            )
            if _upsert(s, Subclass, sub["slug"], mapped):
                result["subclasses_new"] += 1
        s.commit()

    return result


if __name__ == "__main__":
    print(ingest_srd())
    print(ingest_items())
    print(ingest_reference())
    print(seed_classes_and_subclasses())
