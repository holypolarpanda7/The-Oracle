"""
Shop inventories — merchants have real stock at real prices, rolled not stored.

A merchant's stock is a PURE FUNCTION of (merchant slug, world-week): rolled
from role tables, never persisted, rotating weekly for free. The DM sees a
one-line "stock (this week): ..." under each merchant in context and treats
prices as ground truth; the [[TRADE]] hook makes the coin and items actually
move. Settlement scale sets depth: a village smith carries a few mundane
pieces, a city smith a wall of them.

Prices are SRD-ish, in gold pieces (fractions fine — the purse handles cp).
"""
from __future__ import annotations

import random
from typing import Optional

# Roles whose context line includes rolled stock.
MERCHANT_ROLES = {
    "blacksmith", "merchant", "pawnbroker", "map-maker", "herbalist",
    "innkeeper", "brewer", "tanner", "weaver", "cooper", "fence",
    "grain factor", "den keeper", "tattoo artist", "stablemaster",
    "horse trader",
}

# (item, price_gp, weight) — weight biases the weekly roll.
_TABLES: dict[str, list[tuple[str, float, int]]] = {
    "blacksmith": [
        ("Dagger", 2, 5), ("Handaxe", 5, 4), ("Mace", 5, 3), ("Longsword", 15, 3),
        ("Battleaxe", 10, 3), ("Warhammer", 15, 2), ("Greatsword", 50, 1),
        ("Chain Shirt", 50, 2), ("Scale Mail", 50, 2), ("Chain Mail", 75, 1),
        ("Shield", 10, 4), ("Crossbow Bolts", 1, 5), ("Arrows", 1, 5),
        ("Caltrops (bag of 20)", 1, 2), ("Iron Spikes (10)", 1, 3),
    ],
    "merchant": [
        ("Backpack", 2, 4), ("Bedroll", 1, 5), ("Rope, Hempen (50 ft)", 1, 5),
        ("Torch (5)", 0.25, 5), ("Lantern, Hooded", 5, 3), ("Oil (flask)", 0.1, 4),
        ("Rations (1 day)", 0.5, 6), ("Waterskin", 0.2, 5), ("Tinderbox", 0.5, 4),
        ("Grappling Hook", 2, 2), ("Crowbar", 2, 3), ("Mirror, Steel", 5, 2),
        ("Fine Clothes", 15, 1), ("Tent, Two-Person", 2, 3), ("Healer's Kit", 5, 2),
    ],
    "herbalist": [
        ("Potion of Healing", 50, 3), ("Antitoxin", 50, 2), ("Healer's Kit", 5, 4),
        ("Herbalism Kit", 5, 2), ("Alchemist's Fire", 50, 1), ("Acid (vial)", 25, 1),
        ("Soothing Salve", 2, 4), ("Smelling Salts", 1, 3), ("Dried Willowbark", 0.5, 4),
    ],
    "pawnbroker": [
        ("Dented Helm", 3, 3), ("Tarnished Silver Locket", 8, 2),
        ("Second-hand Shortsword", 7, 3), ("Cracked Spyglass", 40, 1),
        ("Sailor's Dice (bone)", 1, 3), ("Old Signal Whistle", 0.5, 3),
        ("Moth-eaten Cloak", 1, 3), ("Unlabeled Key", 2, 2),
        ("Battered Lute", 12, 2), ("Chipped Hand Mirror", 2, 3),
    ],
    "map-maker": [
        ("Regional Map", 25, 6), ("Cartographer's Tools", 15, 4),
        ("Blank Vellum (5 sheets)", 2, 5), ("Ink and Quills", 1, 5),
        ("Surveyor's Chain", 8, 2), ("Compass, Brass", 25, 2),
    ],
    "innkeeper": [
        ("Hot Meal", 0.3, 6), ("Ale (mug)", 0.04, 6), ("Wine (pitcher)", 0.2, 4),
        ("Room for the Night", 0.5, 6), ("Stabling (1 day)", 0.5, 3),
        ("Traveler's Bread (3 days)", 0.6, 4),
    ],
    "brewer": [
        ("Ale (cask)", 2, 5), ("Strong Mead (bottle)", 0.5, 4),
        ("Winter Stout (cask)", 4, 2), ("Small Beer (cask)", 1, 5),
    ],
    "tanner": [
        ("Leather Armor", 10, 3), ("Hide Armor", 10, 2), ("Belt Pouch", 0.5, 5),
        ("Leather Backpack", 2, 4), ("Waterskin", 0.2, 4), ("Whetstone Strop", 1, 3),
    ],
    "weaver": [
        ("Traveler's Clothes", 2, 5), ("Common Clothes", 0.5, 6),
        ("Winter Blanket", 0.5, 5), ("Fine Cloak", 5, 2), ("Sack (5)", 0.05, 4),
    ],
    "cooper": [
        ("Barrel", 2, 5), ("Bucket", 0.05, 6), ("Cask, Sealed", 3, 3),
        ("Water Butt", 4, 2),
    ],
    "fence": [
        ("A Sword with the Crest Filed Off", 8, 3), ("Lockpicks (crude)", 15, 2),
        ("Somebody's Signet Ring", 20, 1), ("Dark Lantern", 8, 2),
        ("A Bill of Lading, Almost Convincing", 5, 2), ("Weighted Dice", 3, 3),
    ],
    "grain factor": [
        ("Rations (1 day)", 0.4, 6), ("Sack of Flour", 0.3, 5),
        ("Seed Grain (bushel)", 1, 3), ("Feed (10 days)", 0.5, 4),
    ],
    "tattoo artist": [
        ("Small Tattoo (a name, a symbol)", 2, 5),
        ("Elaborate Tattoo (a scene, a story)", 10, 3),
        ("Guild Mark Touch-up", 1, 3), ("Lucky Anchor Tattoo", 5, 3),
    ],
    "den keeper": [
        ("Seat at the Dice Table (stake 1-50 gp)", 0, 6),
        ("Round of Knucklebones (stake 1-20 gp)", 0, 4),
        ("Private Card Table (hourly)", 1, 2),
    ],
    # SRD mounts, tack, and vehicles at book prices.
    "stablemaster": [
        ("Riding Horse", 75, 5), ("Pony", 30, 4), ("Mule", 8, 5),
        ("Draft Horse", 50, 3), ("Camel", 50, 2), ("Mastiff", 25, 3),
        ("Saddle, Riding", 10, 5), ("Saddle, Pack", 5, 4),
        ("Saddle, Military", 20, 2), ("Bit and Bridle", 2, 5),
        ("Saddlebags", 4, 4), ("Animal Feed (10 days)", 0.5, 5),
        ("Stabling (1 day)", 0.5, 6), ("Cart", 15, 3), ("Wagon", 35, 2),
    ],
}

# How many lines a merchant shows, by enclosing settlement scale.
_DEPTH = {"village": 3, "town": 5, "settlement": 5, "city": 8, "district": 5}

# City shops may carry ONE special at a markup (heartland economies reach
# further up the supply chain). Keyed by role; 35% chance per week.
_CITY_SPECIALS: dict[str, list[tuple[str, float]]] = {
    "merchant": [
        ("Potion of Greater Healing", 150), ("Bag of Holding", 400),
        ("Driftglobe", 200), ("Immovable Rod", 450),
    ],
    "blacksmith": [
        ("Cloak of Protection", 350), ("+1 Weapon (roll the type)", 500),
        ("Adamantine Chain Shirt", 550),
    ],
    "stablemaster": [
        ("Warhorse", 400), ("Elephant (special order)", 200),
        ("Barding: Chain Shirt", 200),
    ],
    # Original magic tattoo work (house designs — inked over hours, permanent
    # until magically removed; each is a wondrous item worn in the skin).
    "tattoo artist": [
        ("Emberward Ink (1/day: resistance to fire for 1 min)", 300),
        ("Skinwrit Spell (holds one 1st-level spell, single use)", 150),
        ("Ironhide Brand (unarmored AC floor 13)", 400),
        ("Wolfsblood Mark (1/day bonus action: +1d4 melee damage, 1 min)", 350),
        ("Ghostgait Sigil (1/day: step through one wall up to 5 ft thick)", 500),
    ],
}
_TABLES["horse trader"] = _TABLES["stablemaster"]
_CITY_SPECIALS["horse trader"] = _CITY_SPECIALS["stablemaster"]


def roll_stock(merchant_slug: str, role: str, settlement_scale: str,
               world_day: int) -> list[dict]:
    """This week's stock for a merchant. Deterministic; rotates every 7 days."""
    table = _TABLES.get((role or "").strip().lower())
    if not table:
        return []
    week = world_day // 7
    rng = random.Random(f"stock:{merchant_slug}:{week}")
    depth = _DEPTH.get((settlement_scale or "").strip().lower(), 4)
    names = [t[0] for t in table]
    weights = [t[2] for t in table]
    picked: list[str] = []
    pool = list(zip(names, weights))
    while pool and len(picked) < depth:
        total = sum(w for _, w in pool)
        r = rng.uniform(0, total)
        acc = 0.0
        for i, (n, w) in enumerate(pool):
            acc += w
            if r <= acc:
                picked.append(n)
                pool.pop(i)
                break
    prices = {t[0]: t[1] for t in table}
    stock = [{"name": n, "price_gp": prices[n]} for n in picked]
    role_l = (role or "").strip().lower()
    specials = _CITY_SPECIALS.get(role_l)
    if specials and (settlement_scale or "").lower() == "city" and rng.random() < 0.35:
        name, price = rng.choice(specials)
        stock.append({"name": name, "price_gp": price})
    return stock


def stock_line(stock: list[dict], *, limit: int = 6) -> str:
    """Terse context line: 'longsword 15gp, shield 10gp, ...'."""
    def fmt(p: float) -> str:
        if p >= 1:
            return f"{p:g}gp"
        return f"{int(round(p * 10))}sp" if p >= 0.1 else f"{int(round(p * 100))}cp"
    return ", ".join(f"{s['name']} {fmt(s['price_gp'])}" for s in stock[:limit])


def find_in_stock(stock: list[dict], item_name: str) -> Optional[dict]:
    low = (item_name or "").strip().lower()
    for s in stock:
        if s["name"].lower() == low or low in s["name"].lower():
            return s
    return None
