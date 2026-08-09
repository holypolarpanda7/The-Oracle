"""What a character is actually WEARING and HOLDING — the one place a loadout
is decided.

Before this, ``equipped`` was a single boolean on an inventory row and that was
the whole model. It answered "is this strapped on", which is enough for armour
class and enough for a portrait, and it cannot answer the question the casting
rules actually ask: *is a hand free?* A shield and a greatsword were two rows
both flagged ``equipped`` and nothing said they were fighting over the same two
hands — so the free-hand rule for Somatic and Material components was left to
the DM as a note on their board, the last mechanic in the game asked for by
prose rather than enforced.

The model is deliberately narrow. Only two things eat hands: **weapons and
shields** (plus the short list of gear that is obviously held — a torch, a
wand, a staff), which is exactly the pair the rule names. Everything else a
character straps on is *worn*: it occupies a body, not a grip, and it can never
cause a refusal. That asymmetry is on purpose — a wrong guess that eats a hand
stops a spell, and a wrong guess that doesn't merely makes the game generous,
which is the same direction :func:`_material_check` already errs in.

Three facts a caller can ask for:

* :func:`read_loadout` — what is worn, what is held, and in which hand, with
  grips INFERRED for any older row that was flagged ``equipped`` before grips
  existed. Inference is deterministic (two-handed → both, shield → off, weapon
  → main) so an imported sheet reads the same way twice.
* :func:`plan_equip` / :func:`plan_stow` — a validated change to that state.
  Equipping something the hands can't hold does not fail; it DISPLACES what was
  there and says so, because that is what swapping a weapon is.
* :func:`casting_hands` — whether the Somatic and Material components can be
  performed, and if not, the one item to stow so they can. A refusal that names
  its remedy is a turn's free object interaction, not a dead end.

Nothing in here touches the database. It reads inventory dicts and a catalogue
lookup and returns plain data, so the backend, the arena's Quartermaster and
the smoke tests all get the same answer from the same code.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .components import SpellComponents, is_focus, is_worn_focus

#: A grip is one of these. ``both`` is a two-handed weapon (or a versatile one
#: being swung in earnest) and occupies main and off together.
MAIN, OFF, BOTH = "main", "off", "both"
GRIPS = (MAIN, OFF, BOTH)

#: How many hands a body has. A column rather than a literal 2 because a
#: caller may one day have a creature with more of them.
DEFAULT_HANDS = 2

#: Gear that is HELD when strapped on even though it is neither weapon nor
#: shield. A wand or a staff is the point — it is a spellcasting focus, so it
#: both costs a hand and pays for the material component with the same hand.
_HELD_GEAR = (
    "wand", "rod", "staff", "quarterstaff", "orb", "crystal", "sceptre",
    "scepter", "torch", "lantern", "lamp", "spellbook", "shield",
)

#: Worn, always: these never cost a hand however they are flagged. Checked
#: before the held list so a "Ring of the Ram" is a ring, not a rod.
_WORN_GEAR = (
    "ring", "cloak", "cape", "mantle", "boots", "shoes", "slippers", "gloves",
    "gauntlets", "bracers", "belt", "girdle", "helm", "helmet", "hat", "crown",
    "circlet", "diadem", "mask", "goggles", "lenses", "eyes of", "amulet",
    "necklace", "periapt", "brooch", "medallion", "pendant", "robe", "cape",
    "armor", "armour", "mail", "plate", "brigandine", "cuirass", "tabard",
)

#: Item types the catalogue uses for armour. A shield IS armour to the
#: catalogue ("armor / Shield") and is emphatically not worn, so it is pulled
#: out first everywhere below.
_ARMOR_TYPES = ("light", "medium", "heavy", "light armor", "medium armor",
                "heavy armor")


# ---------------------------------------------------------------------------
# What a thing is
# ---------------------------------------------------------------------------

def _low(v: Any) -> str:
    return str(v or "").strip().lower()


def is_shield(row: Any, name: str) -> bool:
    """A shield: held in a hand, and the reason the free-hand rule bites."""
    return "shield" in _low(name) or "shield" in _low(getattr(row, "item_type", None))


def is_armor(row: Any, name: str) -> bool:
    """Body armour — worn, exclusive with other armour, never a shield."""
    if is_shield(row, name):
        return False
    if getattr(row, "armor_class_base", None) is not None:
        return True
    cat = _low(getattr(row, "category", None))
    it = _low(getattr(row, "item_type", None))
    if cat == "armor" or it in _ARMOR_TYPES or "armor" in it:
        return True
    n = _low(name)
    return bool(re.search(r"\b(armou?r|chain mail|scale mail|ring mail|"
                          r"splint|breastplate|half plate|plate)\b", n))


def is_weapon(row: Any, name: str) -> bool:
    cat = _low(getattr(row, "category", None))
    it = _low(getattr(row, "item_type", None))
    if cat == "weapon" or "weapon" in it or it in ("simple", "martial"):
        return True
    return bool(getattr(row, "damage_dice", None))


def is_two_handed(row: Any, name: str) -> bool:
    props = {_low(p) for p in (getattr(row, "properties", None) or [])}
    if "two-handed" in props or "two handed" in props:
        return True
    return "two-handed" in _low(getattr(row, "desc", None))[:200]


def is_versatile(row: Any, name: str) -> bool:
    """A longsword: one hand or two, and the sheet has to record which."""
    if getattr(row, "two_handed_damage_dice", None):
        return True
    props = {_low(p) for p in (getattr(row, "properties", None) or [])}
    return "versatile" in props


@dataclass
class WeaponProps:
    """The weapon properties that decide how a thing is HELD and swung.

    Read once, here, because three callers were each parsing the same
    ``properties`` list their own way: the combat profile builder, the grip
    model, and the two-weapon rule.
    """
    light: bool = False
    heavy: bool = False
    two_handed: bool = False
    versatile: bool = False
    finesse: bool = False
    thrown: bool = False
    reach: bool = False
    ranged: bool = False
    #: The die a versatile weapon does in two hands ("1d10"), else None.
    versatile_damage: Optional[str] = None
    #: How far it flies when thrown (20/60 for a dagger), else None. Kept apart
    #: from the weapon's REACH, which for a thrown melee weapon is 5 ft.
    throw_normal: Optional[int] = None
    throw_long: Optional[int] = None

    @property
    def melee(self) -> bool:
        return not self.ranged


def weapon_props(row: Any, name: str) -> WeaponProps:
    props = {_low(p) for p in (getattr(row, "properties", None) or [])}
    it = _low(getattr(row, "item_type", None))
    # An SRD melee weapon still carries range.normal = 5, so "has a range" is
    # not "is ranged" — trust the type label first, then a range beyond reach.
    ranged = "ranged" in it or (int(getattr(row, "range_normal", None) or 0) > 10)
    return WeaponProps(
        light="light" in props,
        heavy="heavy" in props,
        two_handed=is_two_handed(row, name),
        versatile=is_versatile(row, name),
        finesse="finesse" in props,
        thrown="thrown" in props,
        reach="reach" in props,
        ranged=ranged,
        versatile_damage=getattr(row, "two_handed_damage_dice", None) or None,
        throw_normal=getattr(row, "throw_range_normal", None) or None,
        throw_long=getattr(row, "throw_range_long", None) or None,
    )


def two_weapon_pair(loadout: "Loadout",
                    get_item: Optional[Callable[[str], Any]] = None,
                    *, dual_wielder: bool = False
                    ) -> Optional[Tuple[Held, Held]]:
    """``(main, off)`` when the hands qualify for the extra attack, else None.

    The 2024 Light property: when you attack with a Light weapon, you may make
    one extra attack as a Bonus Action with a *different* Light weapon in your
    other hand. Dual Wielder relaxes the OFF hand only — it still wants Light
    in the main hand, and the off weapon may then be any melee weapon lacking
    Two-Handed.

    Two weapons in two hands is the whole condition, and it is a fact about the
    LOADOUT, which is why nothing could answer it before grips existed: two
    shortswords in a pack and two shortswords in two hands were the same row.
    """
    main = loadout.at(MAIN)
    off = loadout.at(OFF)
    if main is None or off is None or main is off:
        return None
    mp = weapon_props(_row_for(get_item, main.name), main.name)
    op = weapon_props(_row_for(get_item, off.name), off.name)
    if not is_weapon(_row_for(get_item, main.name), main.name):
        return None
    if not is_weapon(_row_for(get_item, off.name), off.name):
        return None
    if not mp.light:
        return None
    # The Light property says nothing about melee, and a hand crossbow is Light
    # — two of them is a real build, so ranged is not excluded here. Dual
    # Wielder is the branch that IS melee-only, because its own text says so.
    if op.light:
        return (main, off)
    if dual_wielder and op.melee and not op.two_handed:
        return (main, off)
    return None


def _row_for(get_item: Optional[Callable[[str], Any]], name: str) -> Any:
    if get_item is None:
        return None
    try:
        return get_item(name)
    except Exception:
        return None


def hands_needed(row: Any, name: str) -> int:
    """How many hands wielding this costs — 0 if it is worn or stowed gear.

    The permissive direction is 0: an unrecognised thing does not eat a hand,
    so an item the catalogue has never heard of can never refuse a spell.
    """
    n = _low(name)
    if is_shield(row, name):
        return 1
    if is_armor(row, name):
        return 0
    if is_weapon(row, name):
        return 2 if is_two_handed(row, name) else 1
    if any(w in n for w in _WORN_GEAR):
        return 0
    it = _low(getattr(row, "item_type", None))
    if any(w in n or w in it for w in _HELD_GEAR):
        return 1
    return 0


def wear_slot(row: Any, name: str) -> Optional[str]:
    """``"hand"``, ``"armor"``, ``"ring"`` or ``"worn"`` — None if unwearable.

    Only ``armor`` is exclusive by itself (one suit at a time); hands are
    exclusive by counting, and everything else stacks, because deciding that a
    character may not wear two cloaks is a ruling and this is a schema.
    """
    if hands_needed(row, name) > 0:
        return "hand"
    if is_armor(row, name):
        return "armor"
    n = _low(name)
    if n.startswith("ring") or " ring of" in n or _low(getattr(row, "item_type", None)) == "ring":
        return "ring"
    if any(w in n for w in _WORN_GEAR):
        return "worn"
    # A holy symbol or a component pouch is worn, not held — and it has to be
    # equippable, because a worn focus is the whole reason a cleric with a
    # shield can cast at all.
    if is_worn_focus(n) or is_focus(n):
        return "worn"
    if getattr(row, "requires_attunement", False):
        return "worn"
    it = _low(getattr(row, "item_type", None))
    if "wondrous" in it:
        return "worn"
    return None


def equippable(row: Any, name: str) -> bool:
    return wear_slot(row, name) is not None


# ---------------------------------------------------------------------------
# The loadout
# ---------------------------------------------------------------------------

@dataclass
class Held:
    """One thing in one or both hands."""
    name: str
    grip: str                 #: MAIN / OFF / BOTH
    hands: int                #: 1, or 2 for BOTH
    index: int = -1           #: where it sits in the inventory list
    shield: bool = False
    focus: bool = False       #: stands in for a costless material component
    inferred: bool = False    #: the grip was derived, not recorded

    @property
    def stowable(self) -> bool:
        return True


@dataclass
class Loadout:
    """Everything strapped on, sorted by what it occupies."""
    held: List[Held] = field(default_factory=list)
    armor: Optional[str] = None
    rings: List[str] = field(default_factory=list)
    worn: List[str] = field(default_factory=list)
    hands: int = DEFAULT_HANDS
    #: Flagged ``equipped`` and there was no hand left for it. A body cannot be
    #: in this state; it is what an older sheet or a bulk outfitter produces,
    #: and :func:`normalize` is what puts it right.
    overflow: List[Held] = field(default_factory=list)
    #: A second suit of armour nobody can be wearing, same reason.
    spare_armor: List[str] = field(default_factory=list)

    @property
    def used_hands(self) -> int:
        return sum(h.hands for h in self.held)

    @property
    def free_hands(self) -> int:
        return max(0, self.hands - self.used_hands)

    @property
    def two_handing(self) -> bool:
        return any(h.grip == BOTH for h in self.held)

    @property
    def can_free_a_hand(self) -> bool:
        """True when a hand can be freed without putting anything down.

        Exactly one case: both hands are on a single object. Taking one off it
        and putting it back costs nothing, because the thing is never released
        — which is why a greatsword is not the same problem as a sword and a
        shield, where freeing a hand means actually stowing something.
        """
        return len(self.held) == 1 and self.held[0].grip == BOTH

    def at(self, grip: str) -> Optional[Held]:
        """What is in one hand — a two-handed weapon answers for both."""
        for h in self.held:
            if h.grip == grip or h.grip == BOTH:
                return h
        return None

    def find(self, name: str) -> Optional[Held]:
        n = _norm(name)
        for h in self.held:
            if _norm(h.name) == n:
                return h
        for h in self.held:
            if n and (n in _norm(h.name) or _norm(h.name) in n):
                return h
        return None

    def describe_hands(self) -> str:
        """"main hand: Longsword; off hand: Shield; 0 hands free"." """
        if not self.held:
            return f"both hands empty ({self.hands} free)"
        parts: List[str] = []
        for h in sorted(self.held, key=lambda x: GRIPS.index(x.grip)):
            where = ("both hands" if h.grip == BOTH else
                     "main hand" if h.grip == MAIN else "off hand")
            parts.append(f"{where}: {h.name}")
        free = self.free_hands
        parts.append(f"{free} hand{'s' if free != 1 else ''} free")
        return "; ".join(parts)

    def describe(self) -> str:
        """The whole loadout in one line, for the DM's board."""
        bits = [self.describe_hands()]
        if self.armor:
            bits.append(f"wearing {self.armor}")
        if self.rings:
            bits.append("rings: " + ", ".join(self.rings))
        return "; ".join(bits)


def _norm(name: Any) -> str:
    return re.sub(r"\s+", " ", str(name or "").strip().lower())


def _quantity(entry: Dict[str, Any]) -> int:
    try:
        return max(1, int(entry.get("quantity", 1) or 1))
    except (TypeError, ValueError):
        return 1


def _lookup(get_item: Optional[Callable[[str], Any]], entry: Dict[str, Any]) -> Any:
    """The catalogue row behind an entry — by its BASE name.

    Affixes and player renames both change the display name, and every
    mechanical lookup in this project has to go through ``base`` or the piece
    silently loses its stats (see ``_compute_ac``'s history).
    """
    if get_item is None:
        return None
    name = str(entry.get("base") or entry.get("name") or "")
    try:
        return get_item(name)
    except Exception:
        return None


def _infer_grip(row: Any, name: str, hands: int, taken: Dict[str, bool]) -> str:
    """Which hand an older ``equipped`` row must have meant.

    Deterministic on purpose: two rows read in the same order always land in
    the same hands, so a sheet that has never heard of grips still reads the
    same way every time it is loaded.
    """
    if hands >= 2:
        return BOTH
    if is_shield(row, name):
        return OFF if not taken.get(OFF) else MAIN
    if not taken.get(MAIN):
        return MAIN
    return OFF


def read_loadout(items: Sequence[Dict[str, Any]],
                 get_item: Optional[Callable[[str], Any]] = None,
                 *, hands: int = DEFAULT_HANDS) -> Loadout:
    """What this inventory adds up to on a body.

    An entry counts only if it is ``equipped``. Nothing else does: a pack full
    of swords is a pack full of swords, and a character who has never equipped
    anything has two empty hands — which is why turning this rule on cannot
    break an imported sheet that carries no equip flags at all.
    """
    out = Loadout(hands=hands)
    taken: Dict[str, bool] = {}
    pending: List[Tuple[int, Dict[str, Any], Any, str, int]] = []
    for idx, it in enumerate(items):
        if not it.get("equipped"):
            continue
        name = str(it.get("name") or "")
        if not name:
            continue
        row = _lookup(get_item, it)
        need = int(it.get("hands") if str(it.get("hands") or "").isdigit()
                   else hands_needed(row, name))
        slot = wear_slot(row, name)
        if need <= 0:
            if slot == "armor":
                if out.armor is None:
                    out.armor = name
                else:
                    out.spare_armor.append(name)
            elif slot == "ring":
                out.rings.append(name)
            else:
                out.worn.append(name)
            continue
        pending.append((idx, it, row, name, need))

    # Recorded grips are placed first so an inferred one never steals a hand
    # something explicitly claimed.
    ordered = sorted(pending, key=lambda p: 0 if _low(p[1].get("grip")) in GRIPS else 1)
    for idx, it, row, name, need in ordered:
        grip = _low(it.get("grip"))
        inferred = grip not in GRIPS
        if inferred:
            grip = _infer_grip(row, name, need, taken)
        if grip == BOTH or need >= 2:
            grip, need = BOTH, 2
        wants = {MAIN, OFF} if grip == BOTH else {grip}
        held = Held(name=name, grip=grip, hands=need, index=idx,
                    shield=is_shield(row, name),
                    focus=is_focus(str(it.get("base") or name)),
                    inferred=inferred)
        if any(taken.get(w) for w in wants):
            # Two things claiming one hand. Neither is dropped silently — the
            # later one is reported as overflow, and `normalize` stows it.
            out.overflow.append(held)
            continue
        for w in wants:
            taken[w] = True
        out.held.append(held)
    return out


def normalize(items: List[Dict[str, Any]],
              get_item: Optional[Callable[[str], Any]] = None,
              *, hands: int = DEFAULT_HANDS) -> List[str]:
    """Put right a body that is wearing more than it has room for.

    A bulk outfitter — the arena's Quartermaster, an imported sheet, a kit
    granted at creation — sets ``equipped`` on a list of rows without ever
    asking whether they fit together, so a fighter can end up flagged as
    holding a greatsword AND a shield. Rather than let a physically impossible
    loadout reach the free-hand rule (where it would read as *fewer* free hands
    than a body has, which is the wrong kind of strict), the excess is stowed
    and named. Returns what was put away.
    """
    load = read_loadout(items, get_item, hands=hands)
    stowed: List[str] = []
    for h in load.overflow:
        if 0 <= h.index < len(items):
            _patch(items[h.index], _stow_patch())
            stowed.append(h.name)
    for name in load.spare_armor:
        idx = _index_of(items, name)
        if idx is not None:
            _patch(items[idx], _stow_patch())
            stowed.append(name)
    # A grip that was only ever inferred is written down now, so the sheet says
    # what it means and the next read doesn't have to guess again.
    for h in load.held:
        if h.inferred and 0 <= h.index < len(items):
            items[h.index]["grip"] = h.grip
            items[h.index]["hands"] = h.hands
    return stowed


# ---------------------------------------------------------------------------
# Changing it
# ---------------------------------------------------------------------------

@dataclass
class EquipPlan:
    """The result of asking for a change: what moves, and what got displaced."""
    ok: bool = True
    error: str = ""
    #: ``{inventory index: {field: value}}`` — ``grip: None`` clears it.
    changes: Dict[int, Dict[str, Any]] = field(default_factory=dict)
    #: Names put away to make room, in the order they were displaced.
    displaced: List[str] = field(default_factory=list)
    #: New rows to append. A stack has ONE grip, so dual-wielding two identical
    #: shortswords means splitting one off the pile into its own row — without
    #: it "2x Shortsword" can be in the main hand or the off hand and never in
    #: both, which is the commonest two-weapon build there is.
    additions: List[Dict[str, Any]] = field(default_factory=list)
    note: str = ""

    def apply(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Write the plan onto the inventory list (mutating its dicts)."""
        for idx, patch in self.changes.items():
            if 0 <= idx < len(items):
                _patch(items[idx], patch)
        items.extend(self.additions)
        return items


def _stow_patch() -> Dict[str, Any]:
    return {"equipped": False, "grip": None, "hands": None}


def _patch(entry: Dict[str, Any], patch: Dict[str, Any]) -> None:
    """Write a patch onto one entry — ``None`` REMOVES the key rather than
    storing a null, so a stowed row reads exactly like one that never had a
    grip and the JSON doesn't accumulate dead fields."""
    for k, v in patch.items():
        if v is None:
            entry.pop(k, None)
        else:
            entry[k] = v


def plan_stow(items: Sequence[Dict[str, Any]], name: str,
              get_item: Optional[Callable[[str], Any]] = None) -> EquipPlan:
    """Put one thing away — a free object interaction, and the usual remedy."""
    idx = _index_of(items, name)
    if idx is None:
        return EquipPlan(ok=False, error=f"{name} isn't in the pack.")
    if not items[idx].get("equipped"):
        return EquipPlan(ok=True, note=f"{items[idx].get('name')} was already stowed.",
                         changes={})
    plan = EquipPlan(changes={idx: _stow_patch()})
    plan.note = f"{items[idx].get('name')} is stowed."
    return plan


def plan_equip(items: Sequence[Dict[str, Any]], name: str,
               get_item: Optional[Callable[[str], Any]] = None,
               *, grip: Optional[str] = None,
               hands: int = DEFAULT_HANDS) -> EquipPlan:
    """Strap something on, displacing whatever it can't share a body with.

    A request for a hand that is full is not an error — it is a swap, which is
    what picking up a different weapon has always been. Armour is the one
    exclusive worn slot, and a second suit displaces the first for the same
    reason: ``_compute_ac`` reads whichever it meets last, and two suits worn
    at once is a state no character can be in.
    """
    idx = _index_of(items, name, grip=_low(grip) or None)
    if idx is None:
        return EquipPlan(ok=False, error=f"{name} isn't in the pack.")
    entry = items[idx]
    disp_name = str(entry.get("name") or name)
    row = _lookup(get_item, entry)
    slot = wear_slot(row, disp_name)
    if slot is None:
        # The catalogue has never heard of it. A book weapon, a homebrew relic
        # and a coil of rope all land here, so refusing outright would make a
        # whole class of gear unwieldable. Naming a hand is the DM (or the
        # player) saying it is held; saying nothing leaves it worn, which is
        # the same permissive default `hands_needed` takes.
        slot = "hand" if _low(grip) in GRIPS else "worn"

    plan = EquipPlan()
    if slot != "hand":
        if slot == "armor":
            current = read_loadout(items, get_item, hands=hands).armor
            if current and _norm(current) != _norm(disp_name):
                other = _index_of(items, current)
                if other is not None and other != idx:
                    plan.changes[other] = _stow_patch()
                    plan.displaced.append(current)
        plan.changes[idx] = {"equipped": True, "grip": None}
        plan.note = f"{disp_name} is worn."
        return plan

    need = hands_needed(row, disp_name)
    want = _low(grip) if _low(grip) in GRIPS else None
    if need >= 2:
        want = BOTH
    elif want == BOTH and not is_versatile(row, disp_name):
        # Two hands on a weapon that gains nothing by it is a fiction the
        # sheet shouldn't record — it would eat a hand for no benefit.
        want = MAIN
    if want == BOTH:
        need = 2

    # A STACK has one grip. Two scimitars filed as "2x Scimitar" can be in the
    # main hand or the off hand and never in both, so asking for the other hand
    # splits one off the pile into a row of its own. Only ever for an explicit
    # request: "equip my scimitar" with no hand named means the one already in
    # a hand, not a second one drawn from the same stack.
    split = False
    if (want is not None and entry.get("equipped")
            and _low(entry.get("grip")) in GRIPS
            and _low(entry.get("grip")) != want
            and _quantity(entry) > 1 and need < 2):
        split = True

    # Read the loadout as it will be WITHOUT this item, so re-gripping
    # something already in hand doesn't fight itself for its own hand.
    others = [dict(it) for it in items]
    if not split:
        others[idx] = {**others[idx], "equipped": False}
    current = read_loadout(others, get_item, hands=hands)

    if want is None:
        want = _preferred_grip(current, row, disp_name, need)
        if need >= 2:
            want = BOTH

    wanted_hands = {MAIN, OFF} if want == BOTH else {want}
    # Overflow counts as in the way: it is already flagged equipped, so leaving
    # it alone would keep an impossible loadout alive one change longer.
    for h in list(current.held) + list(current.overflow):
        occupies = {MAIN, OFF} if h.grip == BOTH else {h.grip}
        if occupies & wanted_hands:
            if h.index >= 0:
                plan.changes[h.index] = _stow_patch()
            plan.displaced.append(h.name)

    if split:
        plan.changes[idx] = {"quantity": _quantity(entry) - 1}
        fresh = {k: v for k, v in entry.items()
                 if k not in ("quantity", "grip", "hands", "equipped")}
        fresh.update({"name": disp_name, "quantity": 1, "equipped": True,
                      "grip": want, "hands": 1})
        plan.additions.append(fresh)
    else:
        plan.changes[idx] = {"equipped": True, "grip": want,
                             "hands": 2 if want == BOTH else 1}
    where = ("both hands" if want == BOTH else
             "main hand" if want == MAIN else "off hand")
    plan.note = (f"{'a second ' if split else ''}{disp_name} is held in "
                 f"{'the ' if want != BOTH else ''}{where}.")
    if plan.displaced:
        plan.note += " " + _and(plan.displaced) + \
            (" is" if len(plan.displaced) == 1 else " are") + " stowed to make room."
    return plan


def _preferred_grip(current: Loadout, row: Any, name: str, need: int) -> str:
    """Where a thing goes when nobody said: shields off, weapons main."""
    free_main = current.at(MAIN) is None
    free_off = current.at(OFF) is None
    if is_shield(row, name):
        return OFF if free_off else MAIN
    if is_weapon(row, name):
        return MAIN if free_main else OFF
    return OFF if free_off else MAIN


def _index_of(items: Sequence[Dict[str, Any]], name: str,
              *, grip: Optional[str] = None) -> Optional[int]:
    """Which row a name refers to. Exact match beats containment.

    ``grip`` disambiguates the two-weapon case: a ranger carrying two
    scimitars has two rows (or one stack) called the same thing, and "put a
    scimitar in the off hand" must not re-grip the one already in the main
    hand. A row already held in the wanted hand wins, then one that is free,
    then the first match — so the request lands on a scimitar that can take it.
    """
    n = _norm(name)
    if not n:
        return None
    exact = [i for i, it in enumerate(items) if _norm(it.get("name")) == n]
    loose = [i for i, it in enumerate(items)
             if _norm(it.get("name")) and _norm(it.get("name")) != n
             and (n in _norm(it.get("name")) or _norm(it.get("name")) in n)]
    for pool in (exact, loose):
        if not pool:
            continue
        if grip in GRIPS:
            for i in pool:
                if _low(items[i].get("grip")) == grip:
                    return i
            for i in pool:
                if not items[i].get("equipped"):
                    return i
        return pool[0]
    return None


def _and(names: Sequence[str]) -> str:
    names = list(names)
    if len(names) <= 1:
        return names[0] if names else ""
    return ", ".join(names[:-1]) + " and " + names[-1]


# ---------------------------------------------------------------------------
# The rule this whole module exists for
# ---------------------------------------------------------------------------

@dataclass
class HandsRuling:
    """Whether the components can be performed, and what to do if not."""
    ok: bool = True
    reason: str = ""
    #: The item to put away — the DM's one-move fix, and a free interaction.
    stow: Optional[str] = None

    @property
    def remedy(self) -> str:
        if self.ok or not self.stow:
            return ""
        return f"stowing {self.stow} would free a hand"


def casting_hands(loadout: Loadout, comps: SpellComponents, *,
                  somatic_waived: bool = False,
                  component_name: Optional[str] = None) -> HandsRuling:
    """Can this caster perform the Somatic and Material components?

    The rules, in the order they matter:

    * A free hand does both. One is all any spell ever needs, because the hand
      that produces the material component is allowed to perform the somatic
      one for the SAME spell.
    * With no free hand, a spell that has a Material component can still be
      cast if the component (or a focus standing in for it) is already in a
      hand — that hand does the gesturing too.
    * A focus that is *worn* — a holy symbol on a shield or an amulet at the
      throat — pays for the Material component without a hand, which is the
      whole reason clerics can fight with a shield. It does NOT perform the
      somatic component: nothing is gesturing.
    * War Caster (and anything else whose benefit says so) waives the somatic
      requirement outright.

    A spell with neither S nor M is never touched, and a caster with a hand
    free is never touched, so the check is invisible until a character is
    genuinely holding a weapon in each hand.
    """
    if not comps.somatic and not comps.material:
        return HandsRuling()
    if loadout.free_hands >= 1:
        return HandsRuling()
    if loadout.can_free_a_hand:
        # Both hands are on ONE haft. Letting go with one, gesturing and taking
        # hold again is a movement, not an object interaction — you never let
        # go of the weapon, so there is nothing to draw or stow. This is the
        # ruling every table plays and the reason a greatsword paladin can cast
        # at all; a sword AND a shield is the case it deliberately excludes,
        # because putting the shield away is a real interaction.
        return HandsRuling()

    held_component = None
    for h in loadout.held:
        if component_name and _norm(component_name) == _norm(h.name):
            held_component = h
            break
        if comps.focus_ok and h.focus:
            held_component = h
            break
    worn_focus = any(is_worn_focus(w) for w in loadout.worn) or \
        (comps.focus_ok and any(is_worn_focus(w) for w in loadout.rings))
    # A holy symbol borne on a shield is worn, and the shield is in a hand.
    if comps.focus_ok and not worn_focus:
        worn_focus = any(h.shield and is_worn_focus(h.name) for h in loadout.held)

    stow = _stow_candidate(loadout)

    if comps.somatic and not somatic_waived and held_component is None:
        return HandsRuling(
            ok=False, stow=stow,
            reason=("has no hand free for the somatic component — "
                    + loadout.describe_hands()))
    if comps.material and held_component is None and not worn_focus:
        want = component_name or ("the material component"
                                  if not comps.focus_ok else
                                  "a spellcasting focus")
        return HandsRuling(
            ok=False, stow=stow,
            reason=(f"has no hand free to produce {want} — "
                    + loadout.describe_hands()))
    return HandsRuling()


def _stow_candidate(loadout: Loadout) -> Optional[str]:
    """The thing to put away first: the off hand before the main one.

    A character drops what they are guarding with sooner than what they are
    fighting with, and the two-handed case has only one answer anyway.
    """
    for want in (OFF, MAIN, BOTH):
        for h in loadout.held:
            if h.grip == want and h.stowable:
                return h.name
    return loadout.held[0].name if loadout.held else None
