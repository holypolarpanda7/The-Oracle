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
from .models import Feat, Monster, Spell, Subclass, Item, SrdEntry, Puzzle


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

    def legal_spells_for(self, cls: str, *, max_level: int,
                         include_cantrips: bool = True) -> list[Spell]:
        """Spells a class may legally know/prepare up to ``max_level`` spell
        level — the legality check behind any spell-picking UI. Sorted by
        level then name."""
        cls_l = cls.strip().lower()
        with Session(self.engine) as s:
            stmt = select(Spell).where(Spell.level <= max_level)
            rows = s.exec(stmt).all()
        out = [sp for sp in rows
               if any(cls_l == c.lower() for c in (sp.classes or []))
               and (include_cantrips or sp.level > 0)]
        return sorted(out, key=lambda sp: (sp.level, sp.name))

    def count(self) -> dict:
        with Session(self.engine) as s:
            m = len(s.exec(select(Monster.id)).all())
            sp = len(s.exec(select(Spell.id)).all())
            it = len(s.exec(select(Item.id)).all())
            ref = len(s.exec(select(SrdEntry.id)).all())
            ft = len(s.exec(select(Feat.id)).all())
            sc = len(s.exec(select(Subclass.id)).all())
        return {"monsters": m, "spells": sp, "items": it, "reference": ref,
                "feats": ft, "subclasses": sc}

    # ----- feats -----

    def get_feat(self, ref: str) -> Optional[Feat]:
        with Session(self.engine) as s:
            f = s.exec(select(Feat).where(Feat.index_slug == ref)).first()
            if f:
                return f
            ref_l = ref.strip().lower()
            return s.exec(select(Feat).where(Feat.name.ilike(ref_l))).first()  # type: ignore[attr-defined]

    def search_feats(
        self,
        query: str = "",
        *,
        category: Optional[str] = None,
        max_level: Optional[int] = None,
        limit: int = 20,
    ) -> list[Feat]:
        with Session(self.engine) as s:
            stmt = select(Feat)
            if query:
                stmt = stmt.where(Feat.name.ilike(f"%{query}%"))  # type: ignore[attr-defined]
            if category:
                stmt = stmt.where(Feat.category == category)
            if max_level is not None:
                stmt = stmt.where(Feat.min_level <= max_level)
            stmt = stmt.order_by(Feat.name).limit(limit)  # type: ignore[attr-defined]
            return list(s.exec(stmt).all())

    # ----- subclasses -----

    def get_subclass(self, ref: str) -> Optional[Subclass]:
        with Session(self.engine) as s:
            sc = s.exec(select(Subclass).where(Subclass.index_slug == ref)).first()
            if sc:
                return sc
            ref_l = ref.strip().lower()
            return s.exec(select(Subclass).where(Subclass.name.ilike(ref_l))).first()  # type: ignore[attr-defined]

    def subclasses_for_class(self, class_name: str) -> list[Subclass]:
        """All subclasses of a class (e.g. the level-3 subclass choice menu)."""
        with Session(self.engine) as s:
            stmt = select(Subclass).where(
                Subclass.class_name.ilike(class_name.strip())  # type: ignore[attr-defined]
            ).order_by(Subclass.name)  # type: ignore[attr-defined]
            return list(s.exec(stmt).all())

    def subclass_features_at(self, ref: str, level: int) -> list[dict]:
        """The features a subclass grants AT a given level (for level-up)."""
        sc = self.get_subclass(ref)
        if sc is None:
            return []
        return [f for f in (sc.features or []) if f.get("level") == level]

    def class_features_at(self, cls: str, level: int) -> list[dict]:
        """Core class features gained AT a given level, as
        [{'class','level','name','summary'}] — pair with
        ``subclass_features_at`` for the full level-up picture."""
        cls_l = cls.strip().lower()
        with Session(self.engine) as s:
            rows = s.exec(select(SrdEntry).where(
                SrdEntry.category == "class-feature")).all()
        out = []
        for e in rows:
            d = e.data or {}
            if d.get("level") == level and (d.get("class") or "").lower() == cls_l:
                out.append({**d, "summary": e.desc})
        return sorted(out, key=lambda f: f["name"])

    def class_features_up_to(self, cls: str, level: int) -> list[dict]:
        """All core class features a character of ``level`` has (for the DM prompt)."""
        cls_l = cls.strip().lower()
        with Session(self.engine) as s:
            rows = s.exec(select(SrdEntry).where(
                SrdEntry.category == "class-feature")).all()
        out = [{**(e.data or {}), "summary": e.desc} for e in rows
               if ((e.data or {}).get("level") or 1) <= level
               and ((e.data or {}).get("class") or "").lower() == cls_l]
        return sorted(out, key=lambda f: ((f.get("level") or 1), f["name"]))

    def _race_feature_rows(self, race: str) -> list[dict]:
        race_l = (race or "").strip().lower()
        with Session(self.engine) as s:
            rows = s.exec(select(SrdEntry).where(
                SrdEntry.category == "race-feature")).all()
        return [{**(e.data or {}), "summary": e.desc}
                for e in rows if ((e.data or {}).get("race") or "").lower() == race_l]

    def race_features_at(self, race: str, level: int) -> list[dict]:
        """Species features GAINED at exactly ``level`` (for the level-up flow)."""
        return sorted((f for f in self._race_feature_rows(race)
                       if f.get("level") == level), key=lambda f: f["name"])

    def race_features_up_to(self, race: str, level: int) -> list[dict]:
        """All species features a character of ``level`` has (for the DM prompt)."""
        return sorted((f for f in self._race_feature_rows(race)
                       if (f.get("level") or 1) <= level),
                      key=lambda f: (f.get("level") or 1, f["name"]))

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

    # ----- puzzles (the DM brain's ready-made puzzle/riddle/trap library) -----

    def get_puzzle(self, ref: str) -> Optional[Puzzle]:
        with Session(self.engine) as s:
            p = s.exec(select(Puzzle).where(Puzzle.index_slug == ref)).first()
            if p:
                return p
            ref_l = ref.strip().lower()
            return s.exec(select(Puzzle).where(Puzzle.name.ilike(ref_l))).first()  # type: ignore[attr-defined]

    def search_puzzles(
        self,
        tags: Optional[list[str]] = None,
        *,
        puzzle_type: Optional[str] = None,
        limit: int = 3,
    ) -> list[Puzzle]:
        """Puzzles whose ``setting_tags`` (or ``puzzle_type``) overlap ``tags``.

        Tags and the stored setting tags are both exploded on ``-``/whitespace so
        a scene token like ``tomb`` matches a ``crypt-tomb`` style tag. With no
        tags, returns the first ``limit`` puzzles (optionally type-filtered).
        """
        tagset: set[str] = set()
        for t in (tags or []):
            tagset.update(x for x in re.split(r"[-\s]+", str(t).lower()) if x)
        with Session(self.engine) as s:
            stmt = select(Puzzle)
            if puzzle_type:
                stmt = stmt.where(Puzzle.puzzle_type == puzzle_type)
            rows = list(s.exec(stmt).all())
        if not tagset:
            return rows[:limit]
        scored: list[tuple[int, Puzzle]] = []
        for p in rows:
            toks: set[str] = set()
            for t in (p.setting_tags or []):
                toks.update(x for x in re.split(r"[-\s]+", str(t).lower()) if x)
            if p.puzzle_type:
                toks.add(str(p.puzzle_type).lower())
            overlap = len(toks & tagset)
            if overlap:
                scored.append((overlap, p))
        scored.sort(key=lambda sp: (-sp[0], sp[1].name))
        return [p for _, p in scored[:limit]]

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
        # Trailing s? so plurals ("goblin warriors") hit the full name instead
        # of falling back to a shorter fragment.
        self._mention_re = re.compile(
            r"\b(" + "|".join(re.escape(n) for n in names) + r")s?\b", re.IGNORECASE)

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

class _AttrView:
    """Read-only attribute access over a dict, for renderers that expect a model."""

    __slots__ = ("_d",)

    def __init__(self, d: dict):
        self._d = d

    def __getattr__(self, name: str):
        return self._d.get(name)


def format_monster_brief(m: Monster) -> str:
    """Concise stat line for the DM/roller context.

    Accepts a ``Monster`` row or a plain dict (e.g. a scaled variant) with the
    same field names.
    """
    if isinstance(m, dict):
        m = _AttrView(m)

    def score(v: Optional[int]) -> str:
        return f"{v} ({ability_modifier(v):+d})" if v is not None else "?"

    hit_dice = m.hit_dice or m.hit_points_roll
    lines = [
        f"**{m.name}** ({m.size} {m.type}, CR {m.challenge_rating})",
        f"AC {m.armor_class}"
        + (f" ({m.ac_desc})" if m.ac_desc else "")
        + f" | HP {m.hit_points}" + (f" ({hit_dice})" if hit_dice else "")
        + (f" | PB +{m.proficiency_bonus}" if m.proficiency_bonus else ""),
        (
            f"STR {score(m.strength)} DEX {score(m.dexterity)} "
            f"CON {score(m.constitution)} INT {score(m.intelligence)} "
            f"WIS {score(m.wisdom)} CHA {score(m.charisma)}"
        ),
    ]
    for a in (m.actions or []):
        bonus = a.get("attack_bonus")
        dmg = ", ".join(
            f"{d.get('damage_dice','')} {(d.get('damage_type') or {}).get('name','').lower()}".strip()
            for d in (a.get("damage") or [])
        )
        if bonus is not None:
            atk = f" (+{bonus} to hit" + (f", {dmg}" if dmg else "") + ")"
        else:
            # Book-ingested actions carry their math in prose; keep the part
            # with the numbers.
            desc = (a.get("desc") or "").strip()
            atk = f": {desc[:160]}" if desc else ""
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
    elif sp.desc:
        # Book-ingested 2024 spells carry mechanics in prose, not JSON.
        desc = sp.desc.strip()
        if len(desc) > 300:
            desc = desc[:300].rsplit(" ", 1)[0] + "…"
        bits.append(desc)
    return "\n".join(bits)


def format_puzzle_available(puzzles: list[Puzzle]) -> str:
    """Offer block: ready-made puzzles that fit the current scene, with slugs."""
    lines = [
        "# Puzzles available here (optional)",
        "This location fits one or more ready-made puzzles. If the scene calls for "
        "it, set one up by emitting `[[PUZZLE: start | <slug>]]` — the game then "
        "presents it, holds the solution, and doles out hints on request. You are "
        "never required to use one. To make a chamber a lasting puzzle site (so "
        "fitting puzzles keep surfacing here), emit "
        "`[[PUZZLE: site | tag, tag]]`.",
    ]
    for p in puzzles:
        prem = " ".join((p.premise or "").split())
        if len(prem) > 160:
            prem = prem[:160].rsplit(" ", 1)[0] + "…"
        diff = f", {p.difficulty}" if p.difficulty else ""
        lines.append(
            f"- `{p.index_slug}`: **{p.name}** ({p.puzzle_type or 'puzzle'}{diff})"
            + (f" — {prem}" if prem else "")
        )
    return "\n".join(lines)


def format_feat_brief(f: Feat) -> str:
    head = f"**{f.name}** ({f.category} feat"
    if f.min_level > 1:
        head += f", level {f.min_level}+"
    head += ")"
    bits = [head]
    if f.prerequisite:
        bits.append(f"Prerequisite: {f.prerequisite}")
    benefit = (f.benefit or "").strip()
    if benefit:
        if len(benefit) > 400:
            benefit = benefit[:400].rsplit(" ", 1)[0] + "…"
        bits.append(benefit)
    return "\n".join(bits)


def format_subclass_brief(sc: Subclass, *, max_level: Optional[int] = None) -> str:
    """Concise subclass summary; cap features at ``max_level`` (e.g. the PC's
    current level) so the DM isn't fed abilities the PC doesn't have yet."""
    bits = [f"**{sc.name}** ({sc.class_name} subclass)"]
    for f in (sc.features or []):
        if max_level is not None and f.get("level", 0) > max_level:
            continue
        summary = (f.get("summary") or "").strip()
        if len(summary) > 220:
            summary = summary[:220].rsplit(" ", 1)[0] + "…"
        bits.append(f"- L{f.get('level')} {f.get('name')}: {summary}")
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
