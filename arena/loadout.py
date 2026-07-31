"""The Quartermaster — the gear stall that stands between the gate and the sand.

A practice fighter is only half a build without the gear that build is meant to
be holding: a level-12 paladin who never bought plate is testing the wrong
character. So the Grounds hand out a **stipend** scaled to the level you asked
to fight at, and let you spend it on the rules catalog before the wards close.

Three rules, and they exist for the same reason the rest of the Grounds does —
so what you learn here transfers to the world:

* **The stipend is the Grounds', not the world's.** It is conjured coin for a
  conjured fight; it never touches the character's purse, and it evaporates the
  moment the run ends. Nothing bought here is remembered.
* **What you may buy is gated by the level you fight at.** A level-2 bout can't
  stock a rare wand. The gate is rarity, and it moves with tier.
* **Prices for magic are the Grounds' own fee**, not a world economy — the
  catalog rarely prices magic at all, and a practice mode that can't sell a
  Cloak of Protection can't test a build that has one.

The module is pure catalog-and-arithmetic: it reads the rules item table and
prices a cart. Granting the items to a character (and equipping them) is the
backend's job — see ``_arena_*`` in ``oracle-dm-backend/fastapi-dm.py``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

#: What the Grounds hand a fighter to outfit with, by the level they fight at.
#: These are the GROUNDS' numbers, tuned so that each tier can afford the kind
#: of gear that tier is expected to be holding — a rare item and change at 11,
#: a legendary at the very top — not a table lifted out of any book. Level 1
#: falls back to the class's own starting gold so a level-1 bout matches what
#: character creation would have given you.
PURSE_BY_LEVEL: Dict[int, int] = {
    1: 0,
    2: 250, 3: 400, 4: 600,
    5: 1_200, 6: 1_700, 7: 2_300, 8: 3_000, 9: 3_800, 10: 4_800,
    11: 9_000, 12: 11_000, 13: 13_000, 14: 16_000, 15: 19_000, 16: 23_000,
    17: 60_000, 18: 80_000, 19: 100_000, 20: 130_000,
}

#: The Grounds' conjuring fee for magic, by rarity. The rules catalog prices
#: mundane gear and almost never prices magic, so without this the stall would
#: sell rope and nothing else.
MAGIC_PRICE_BY_RARITY: Dict[str, float] = {
    "common": 100.0,
    "uncommon": 600.0,
    "rare": 6_000.0,
    "very rare": 45_000.0,
    "legendary": 120_000.0,
}

#: The level a rarity unlocks at. An artifact is never for sale at any level.
RARITY_MIN_LEVEL: Dict[str, int] = {
    "common": 1, "uncommon": 2, "rare": 5, "very rare": 11, "legendary": 17,
}

#: How many items anyone may be attuned to at once (the rules' own limit).
ATTUNEMENT_LIMIT = 3

# Item types that sit on a body rather than in a pack. Anything here can be
# carried into the fight already worn or in hand; everything else is baggage.
_WEARABLE_WORDS = (
    "armor", "shield", "weapon", "ring", "cloak", "boots", "gloves", "gauntlet",
    "helm", "hat", "belt", "bracers", "amulet", "necklace", "goggles", "mantle",
    "robe", "wand", "staff", "rod", "instrument",
)

# The same question asked of a bare NAME, for gear the catalog never heard of —
# a kit item, a book weapon, anything imported. Coarse on purpose: guessing
# "wearable" wrong only offers a pointless toggle, while guessing it away hides
# the armor someone is about to fight without.
_WEARABLE_NAME_WORDS = _WEARABLE_WORDS + (
    "mail", "plate", "leather", "breastplate", "scale", "hide", "padded",
    "splint", "studded", "sword", "axe", "bow", "hammer", "spear", "dagger",
    "mace", "flail", "glaive", "halberd", "pike", "club", "javelin", "sling",
    "rapier", "scimitar", "whip", "trident", "lance", "morningstar", "quarter",
    "pistol", "revolver", "musket", "firearm",
)


def equippable_name(name: Optional[str]) -> bool:
    """Does this NAME read as something worn or wielded? (catalog-free guess)"""
    n = _norm(name)
    return any(w in n for w in _WEARABLE_NAME_WORDS)


def _norm(text: Optional[str]) -> str:
    return (text or "").strip().lower()


def purse_for(level: int, *, class_gold: Optional[int] = None) -> int:
    """The stipend for a bout at ``level``.

    Level 1 uses the class's own starting gold when the caller knows it, so the
    Grounds and character creation agree about what a level-1 fighter can buy.
    """
    lv = max(1, min(20, int(level or 1)))
    if lv == 1:
        return int(class_gold if class_gold is not None else 125)
    return int(PURSE_BY_LEVEL.get(lv, 0))


def rarity_allowed(rarity: Optional[str], level: int) -> bool:
    """May a fighter at ``level`` buy something of this rarity?"""
    r = _norm(rarity)
    if not r or r not in RARITY_MIN_LEVEL:
        return False              # artifacts, unrated oddities: not for sale
    return int(level or 1) >= RARITY_MIN_LEVEL[r]


def magic_price(row: Any) -> Optional[float]:
    """What the stall charges for a magic item, or None if it won't sell it."""
    priced = getattr(row, "cost_gp", None)
    if priced:                    # the catalog priced it: honour that
        return float(priced)
    return MAGIC_PRICE_BY_RARITY.get(_norm(getattr(row, "rarity", None)))


def is_magic(row: Any) -> bool:
    return (_norm(getattr(row, "category", None)) == "magic-item"
            or bool(_norm(getattr(row, "rarity", None))))


def is_equippable(row: Any) -> bool:
    """True for gear that is worn or wielded rather than stowed."""
    cat = _norm(getattr(row, "category", None))
    if cat in ("weapon", "armor"):
        return True
    blob = f"{_norm(getattr(row, 'item_type', None))} {_norm(getattr(row, 'name', None))}"
    return any(w in blob for w in _WEARABLE_WORDS)


@dataclass(frozen=True)
class StockItem:
    """One line on the Quartermaster's board."""
    slug: str
    name: str
    cost_gp: float
    kind: str                       # "gear" | "magic"
    category: Optional[str] = None
    item_type: Optional[str] = None
    rarity: Optional[str] = None
    equippable: bool = False
    attunement: bool = False
    brief: Optional[str] = None

    def payload(self) -> Dict[str, Any]:
        out = {"slug": self.slug, "name": self.name, "cost_gp": self.cost_gp,
               "kind": self.kind, "category": self.category,
               "item_type": self.item_type, "equippable": self.equippable}
        if self.rarity:
            out["rarity"] = self.rarity
        if self.attunement:
            out["attunement"] = True
        if self.brief:
            out["brief"] = self.brief
        return out


def stock_from_rows(rows: Iterable[Any], level: int) -> List[StockItem]:
    """Turn rules item rows into what the stall will actually sell at ``level``."""
    out: List[StockItem] = []
    seen: set[str] = set()
    for row in rows:
        slug = getattr(row, "index_slug", None)
        name = getattr(row, "name", None)
        if not slug or not name or slug in seen:
            continue
        magic = is_magic(row)
        if magic:
            if not rarity_allowed(getattr(row, "rarity", None), level):
                continue
            price = magic_price(row)
        else:
            price = getattr(row, "cost_gp", None)
            price = float(price) if price is not None else None
        if price is None or price <= 0:
            continue              # unpriced gear is never silently free
        seen.add(slug)
        out.append(StockItem(
            slug=slug, name=name, cost_gp=round(float(price), 2),
            kind="magic" if magic else "gear",
            category=getattr(row, "category", None),
            item_type=getattr(row, "item_type", None),
            rarity=getattr(row, "rarity", None),
            equippable=is_equippable(row),
            attunement=bool(getattr(row, "requires_attunement", False)),
            brief=((getattr(row, "desc", None) or "")[:140] or None) if magic else None,
        ))
    out.sort(key=lambda s: (s.kind != "gear", s.cost_gp, s.name))
    return out


def build_stock(session: Any, level: int) -> List[StockItem]:
    """Everything on sale for a bout at ``level``, read from the rules tables."""
    from sqlmodel import select

    from rules.models import Item as _Item
    rows = session.exec(select(_Item).order_by(_Item.name)).all()
    return stock_from_rows(rows, level)


@dataclass
class PricedCart:
    """A validated cart: what is actually bought, at what cost, and why not."""
    lines: List[Dict[str, Any]] = field(default_factory=list)
    spent: float = 0.0
    purse: int = 0
    rejected: List[str] = field(default_factory=list)

    @property
    def remaining(self) -> float:
        return round(self.purse - self.spent, 2)

    def payload(self) -> Dict[str, Any]:
        return {"lines": self.lines, "spent": round(self.spent, 2),
                "purse": self.purse, "remaining": self.remaining,
                "rejected": self.rejected}


def price_cart(cart: Sequence[Dict[str, Any]], stock: Sequence[StockItem],
               purse: int, *, attuned_already: int = 0) -> PricedCart:
    """Price a cart against the stall's own board — the server's prices win.

    Anything unknown, unaffordable, or over the attunement limit is dropped with
    a reason rather than silently granted: a practice loadout the client made up
    would test a character that can't exist.
    """
    by_slug = {s.slug: s for s in stock}
    by_name = {_norm(s.name): s for s in stock}
    priced = PricedCart(purse=int(purse))
    attuned = int(attuned_already)
    for entry in cart or []:
        if not isinstance(entry, dict):
            continue
        item = (by_slug.get(str(entry.get("slug") or ""))
                or by_name.get(_norm(entry.get("name"))))
        if item is None:
            name = entry.get("name") or entry.get("slug") or "something"
            priced.rejected.append(f"{name} is not on the board")
            continue
        try:
            qty = max(1, int(entry.get("quantity", 1) or 1))
        except (TypeError, ValueError):
            qty = 1
        qty = min(qty, 99)
        line_cost = round(item.cost_gp * qty, 2)
        if priced.spent + line_cost > purse + 1e-6:
            priced.rejected.append(f"{item.name} costs more than is left")
            continue
        equipped = bool(entry.get("equipped")) and item.equippable
        attune = bool(entry.get("attuned")) and item.attunement
        if attune:
            if attuned >= ATTUNEMENT_LIMIT:
                attune = False
                priced.rejected.append(
                    f"{item.name} is bought but not attuned — three is the limit")
            else:
                attuned += 1
        priced.spent = round(priced.spent + line_cost, 2)
        priced.lines.append({
            "slug": item.slug, "name": item.name, "quantity": qty,
            "cost_gp": item.cost_gp, "line_gp": line_cost,
            "equipped": equipped, "attuned": attune,
            "kind": item.kind, "rarity": item.rarity,
            "attunement": item.attunement, "equippable": item.equippable,
        })
    return priced


def default_flags(item: StockItem) -> Tuple[bool, bool]:
    """How a freshly bought item walks into the ring: (equipped, attuned).

    Gear you wear or wield is worn — nobody buys a shield to carry it in a sack
    — and anything that needs attunement is attuned, subject to the limit the
    pricing pass enforces.
    """
    return item.equippable, item.attunement
