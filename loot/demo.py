"""What the affix system actually produces. ``uv run python -m loot.demo``"""
from __future__ import annotations

from . import (affix_by_slug, describe_affixes, display_name,
               mechanical_bonuses, roll_affixes, slots_for_rarity,
               temper_cost_gp, temper_swap)

PIECES = [
    ("Longsword", "Martial", "weapon"),
    ("Chain Mail", "Armor", "armor"),
    ("Shield", "Armor", "shield"),
]
RARITIES = ("common", "uncommon", "rare", "very rare", "legendary")


def main() -> None:
    print("\033[1mLoot — rarity buys SLOTS, not bigger numbers\033[0m\n")
    for base, itype, cat in PIECES:
        print(f"\033[1m{base}\033[0m")
        for rar in RARITIES:
            slugs = roll_affixes(base, rar, item_type=itype, category=cat,
                                 seed=f"demo:{base}:{rar}")
            name = display_name(base, slugs) if slugs else base
            print(f"  {rar:<10} {slots_for_rarity(rar)} slot(s)  \033[33m{name}\033[0m")
            for p in describe_affixes(slugs):
                print(f"      · {p['name']} (tier {p['tier']}) — {p['text']}")
            bonuses = mechanical_bonuses(slugs)
            if bonuses:
                print(f"      \033[36m{bonuses}\033[0m")
        print()

    print("\033[1mThe forge — reroll one property, and it may come back worse\033[0m")
    slugs = roll_affixes("Longsword", "legendary", item_type="Martial",
                         category="weapon", seed="demo:forge")
    print(f"  before: {display_name('Longsword', slugs)}")
    for s in list(slugs):
        a = affix_by_slug(s)
        cost = temper_cost_gp("legendary", a.tier)
        after = temper_swap(list(slugs), s, item_name="Longsword",
                            rarity="legendary", item_type="Martial",
                            category="weapon", seed=f"demo:forge:{s}")
        print(f"    reforge {a.name:<22} {cost:>5} gp -> "
              f"{display_name('Longsword', after)}")


if __name__ == "__main__":
    main()
