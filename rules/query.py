"""
Lookups over the SRD rules tables — the read side the DM brain and dice roller use.

Everything here returns exact, structured values (AC, HP, attack bonus, damage dice,
save DC) so combat and checks are grounded in real numbers rather than guessed.
"""
from __future__ import annotations

import re
from typing import Optional

from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from .ingest import get_engine
from .models import Monster, Spell, Item, SrdEntry


def ability_modifier(score: Optional[int]) -> int:
    if score is None:
        return 0
    return (score - 10) // 2


class RulesLibrary:
    def __init__(self, engine: Optional[Engine] = None, database_url: Optional[str] = None):
        self.engine = engine or get_engine(database_url)
        self._mention_re: Optional[re.Pattern] = None
        self._name_map: dict[str, tuple[str, str]] = {}

    # ----- monsters -----

    def get_monster(self, ref: str) -> Optional[Monster]:
        with Session(self.engine) as s:
            m = s.exec(select(Monster).where(Monster.index_slug == ref)).first()
            if m:
                return m
            ref_l = ref.strip().lower()
            return s.exec(select(Monster).where(Monster.name.ilike(ref_l))).first()  # type: ignore[attr-defined]

    def search_monsters(
        self,
        query: str = "",
        *,
        cr_min: Optional[float] = None,
        cr_max: Optional[float] = None,
        type: Optional[str] = None,
        limit: int = 20,
    ) -> list[Monster]:
        with Session(self.engine) as s:
            stmt = select(Monster)
            if query:
                stmt = stmt.where(Monster.name.ilike(f"%{query}%"))  # type: ignore[attr-defined]
            if cr_min is not None:
                stmt = stmt.where(Monster.challenge_rating >= cr_min)
            if cr_max is not None:
                stmt = stmt.where(Monster.challenge_rating <= cr_max)
            if type:
                stmt = stmt.where(Monster.type == type)
            stmt = stmt.order_by(Monster.challenge_rating).limit(limit)  # type: ignore[attr-defined]
            return list(s.exec(stmt).all())

    # ----- spells -----

    def get_spell(self, ref: str) -> Optional[Spell]:
        with Session(self.engine) as s:
            sp = s.exec(select(Spell).where(Spell.index_slug == ref)).first()
            if sp:
                return sp
            ref_l = ref.strip().lower()
            return s.exec(select(Spell).where(Spell.name.ilike(ref_l))).first()  # type: ignore[attr-defined]

    def search_spells(
        self,
        query: str = "",
        *,
        level: Optional[int] = None,
        cls: Optional[str] = None,
        limit: int = 20,
    ) -> list[Spell]:
        with Session(self.engine) as s:
            stmt = select(Spell)
            if query:
                stmt = stmt.where(Spell.name.ilike(f"%{query}%"))  # type: ignore[attr-defined]
            if level is not None:
                stmt = stmt.where(Spell.level == level)
            results = list(s.exec(stmt.order_by(Spell.level, Spell.name).limit(200)).all())  # type: ignore[attr-defined]
        if cls:
            cls_l = cls.lower()
            results = [sp for sp in results if any(cls_l == c.lower() for c in (sp.classes or []))]
        return results[:limit]

    def count(self) -> dict:
        with Session(self.engine) as s:
            m = len(s.exec(select(Monster.id)).all())
            sp = len(s.exec(select(Spell.id)).all())
            it = len(s.exec(select(Item.id)).all())
            ref = len(s.exec(select(SrdEntry.id)).all())
        return {"monsters": m, "spells": sp, "items": it, "reference": ref}

    # ----- items (equipment + magic items) -----

    def get_item(self, ref: str) -> Optional[Item]:
        with Session(self.engine) as s:
            it = s.exec(select(Item).where(Item.index_slug == ref)).first()
            if it:
                return it
            ref_l = ref.strip().lower()
            return s.exec(select(Item).where(Item.name.ilike(ref_l))).first()  # type: ignore[attr-defined]

    def search_items(
        self,
        query: str = "",
        *,
        category: Optional[str] = None,
        max_cost_gp: Optional[float] = None,
        rarity: Optional[str] = None,
        limit: int = 20,
    ) -> list[Item]:
        with Session(self.engine) as s:
            stmt = select(Item)
            if query:
                stmt = stmt.where(Item.name.ilike(f"%{query}%"))  # type: ignore[attr-defined]
            if category:
                stmt = stmt.where(Item.category == category)
            if max_cost_gp is not None:
                stmt = stmt.where(Item.cost_gp <= max_cost_gp)
            if rarity:
                stmt = stmt.where(Item.rarity == rarity)
            stmt = stmt.order_by(Item.name).limit(limit)  # type: ignore[attr-defined]
            return list(s.exec(stmt).all())

    # ----- generic SRD reference (conditions, skills, feats, races, ...) -----

    def get_reference(self, category: str, ref: str) -> Optional[SrdEntry]:
        with Session(self.engine) as s:
            e = s.exec(
                select(SrdEntry).where(SrdEntry.entry_key == f"{category}:{ref}")
            ).first()
            if e:
                return e
            ref_l = ref.strip().lower()
            return s.exec(
                select(SrdEntry).where(
                    SrdEntry.category == category, SrdEntry.name.ilike(ref_l)  # type: ignore[attr-defined]
                )
            ).first()

    def search_reference(
        self, query: str = "", *, category: Optional[str] = None, limit: int = 20
    ) -> list[SrdEntry]:
        with Session(self.engine) as s:
            stmt = select(SrdEntry)
            if category:
                stmt = stmt.where(SrdEntry.category == category)
            if query:
                stmt = stmt.where(SrdEntry.name.ilike(f"%{query}%"))  # type: ignore[attr-defined]
            stmt = stmt.order_by(SrdEntry.name).limit(limit)  # type: ignore[attr-defined]
            return list(s.exec(stmt).all())

    # ----- mention scanning (for auto-injecting stats the DM referenced) -----

    def _ensure_name_index(self) -> None:
        if self._mention_re is not None:
            return
        with Session(self.engine) as s:
            monsters = s.exec(select(Monster.name, Monster.index_slug)).all()
            spells = s.exec(select(Spell.name, Spell.index_slug)).all()
        name_map: dict[str, tuple[str, str]] = {}
        for name, slug in monsters:
            name_map[name.lower()] = ("monster", slug)
        for name, slug in spells:
            name_map[name.lower()] = ("spell", slug)
        self._name_map = name_map
        # Names >=4 chars avoid noisy substring hits (bat/cat/elf/orc, etc.).
        # Longest-first so multi-word names win over their fragments.
        names = sorted((n for n in name_map if len(n) >= 4), key=len, reverse=True)
        if not names:
            self._mention_re = re.compile(r"(?!x)x")  # matches nothing
            return
        self._mention_re = re.compile(r"\b(" + "|".join(re.escape(n) for n in names) + r")\b", re.IGNORECASE)

    def refresh_index(self) -> None:
        """Drop the cached mention index (call after a fresh ingest)."""
        self._mention_re = None
        self._name_map = {}

    def find_mentions(self, text: str, limit: int = 6) -> list[tuple[str, object]]:
        """Return [(kind, Monster|Spell)] for entities named in ``text``."""
        if not text or not text.strip():
            return []
        self._ensure_name_index()
        found: list[tuple[str, object]] = []
        seen_slugs: set[str] = set()
        for m in self._mention_re.finditer(text):  # type: ignore[union-attr]
            entry = self._name_map.get(m.group(1).lower())
            if not entry:
                continue
            kind, slug = entry
            if slug in seen_slugs:
                continue
            seen_slugs.add(slug)
            obj = self.get_monster(slug) if kind == "monster" else self.get_spell(slug)
            if obj is not None:
                found.append((kind, obj))
            if len(found) >= limit:
                break
        return found


# ----- compact renderers for DM prompt injection -----

def format_monster_brief(m: Monster) -> str:
    """Concise stat line for the DM/roller context."""
    lines = [
        f"**{m.name}** ({m.size} {m.type}, CR {m.challenge_rating})",
        f"AC {m.armor_class}"
        + (f" ({m.ac_desc})" if m.ac_desc else "")
        + f" | HP {m.hit_points} ({m.hit_dice})"
        + (f" | PB +{m.proficiency_bonus}" if m.proficiency_bonus else ""),
        (
            f"STR {m.strength} ({ability_modifier(m.strength):+d}) "
            f"DEX {m.dexterity} ({ability_modifier(m.dexterity):+d}) "
            f"CON {m.constitution} ({ability_modifier(m.constitution):+d}) "
            f"INT {m.intelligence} ({ability_modifier(m.intelligence):+d}) "
            f"WIS {m.wisdom} ({ability_modifier(m.wisdom):+d}) "
            f"CHA {m.charisma} ({ability_modifier(m.charisma):+d})"
        ),
    ]
    for a in (m.actions or []):
        bonus = a.get("attack_bonus")
        dmg = ", ".join(
            f"{d.get('damage_dice','')} {(d.get('damage_type') or {}).get('name','').lower()}".strip()
            for d in (a.get("damage") or [])
        )
        atk = f" (+{bonus} to hit" + (f", {dmg}" if dmg else "") + ")" if bonus is not None else ""
        lines.append(f"- {a.get('name','Action')}{atk}")
    return "\n".join(lines)


def format_spell_brief(sp: Spell) -> str:
    lvl = "Cantrip" if sp.level == 0 else f"Level {sp.level}"
    header = f"**{sp.name}** ({lvl} {sp.school})"
    meta = f"{sp.casting_time} | {sp.range} | {sp.duration}"
    if sp.concentration:
        meta += " | Concentration"
    bits = [header, meta]
    if sp.dc_type:
        bits.append(f"Save: {sp.dc_type}" + (f" ({sp.dc_success} on success)" if sp.dc_success else ""))
    if sp.attack_type:
        bits.append(f"Attack: {sp.attack_type}")
    if sp.damage and isinstance(sp.damage, dict):
        dtype = (sp.damage.get("damage_type") or {}).get("name", "")
        slots = sp.damage.get("damage_at_slot_level") or sp.damage.get("damage_at_character_level") or {}
        if slots:
            base = slots.get(str(sp.level)) or next(iter(slots.values()))
            bits.append(f"Damage: {base} {dtype}".strip())
    return "\n".join(bits)


def _gp_str(cost_gp: Optional[float]) -> str:
    if cost_gp is None:
        return "—"
    if cost_gp == int(cost_gp):
        return f"{int(cost_gp)} gp"
    return f"{cost_gp:g} gp"


def format_item_brief(it: Item) -> str:
    """Concise item line for DM/economy context."""
    head = f"**{it.name}**"
    tags = [t for t in (it.category, it.item_type, it.rarity) if t]
    if tags:
        head += f" ({', '.join(tags)})"
    bits = [head]
    line2 = [f"Cost {_gp_str(it.cost_gp)}"]
    if it.weight:
        line2.append(f"{it.weight:g} lb")
    if it.requires_attunement:
        line2.append("requires attunement")
    bits.append(" | ".join(line2))
    if it.damage_dice:
        dmg = f"Damage {it.damage_dice} {it.damage_type or ''}".rstrip()
        if it.two_handed_damage_dice:
            dmg += f" ({it.two_handed_damage_dice} two-handed)"
        if it.properties:
            dmg += f" [{', '.join(it.properties)}]"
        bits.append(dmg)
    if it.armor_class_base is not None:
        ac = f"AC {it.armor_class_base}"
        if it.armor_dex_bonus:
            ac += " + Dex" + (f" (max {it.armor_max_dex_bonus})" if it.armor_max_dex_bonus else "")
        if it.str_minimum:
            ac += f", Str {it.str_minimum}+"
        if it.stealth_disadvantage:
            ac += ", Stealth disadvantage"
        bits.append(ac)
    return "\n".join(bits)


def format_reference_brief(e: SrdEntry) -> str:
    """Concise reference entry (condition, skill, feat, ...) for DM context."""
    head = f"**{e.name}** ({e.category})"
    desc = (e.desc or "").strip()
    if len(desc) > 600:
        desc = desc[:600].rsplit(" ", 1)[0] + "…"
    return f"{head}\n{desc}" if desc else head
