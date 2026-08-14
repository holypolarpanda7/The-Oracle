"""A stall you can look at, selling what the DM already knows is for sale.

Merchants have had real stock at real prices for a long time — rolled from the
role tables as a pure function of (merchant, settlement scale, world week),
never stored — and the only way to buy any of it was to type a sentence and
hope the DM emitted `[[TRADE: buy]]`. The Activity's only shop was the arena's
Quartermaster, which is outside the world entirely.

This pins the browsable stall, and the property that matters most about it:

1. it shows the merchants the DM's OWN context slice has standing here, with
   the SAME weekly roll — a panel with its own stock would let a player buy
   something the DM never saw for sale;
2. buying goes through `process_trade_hooks`, the path a narrated deal takes,
   so there is one set of commerce rules rather than two;
3. the coin and the item really move, and a purse that cannot cover it is
   refused rather than allowed to go negative;
4. and the stock rotates on the world's week, not on the request.

    uv run python scripts/shop_smoke.py
"""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

db = os.path.join(tempfile.gettempdir(), "oracle_shop_smoke.db")
if os.path.exists(db):
    os.remove(db)
os.environ["DATABASE_URL"] = f"sqlite:///{db}"

spec = importlib.util.spec_from_file_location(
    "fastapi_dm", str(ROOT / "oracle-dm-backend" / "fastapi-dm.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)                                        # noqa: E402

from sqlmodel import Session, SQLModel                             # noqa: E402

from eight_card_system import shops                                # noqa: E402
from eight_card_system.models import EntityType, RelationType      # noqa: E402
from eight_card_system.seed import seed_minimal_world              # noqa: E402

SQLModel.metadata.create_all(m.engine)

OK, BAD, OFF, DIM = "\033[32m", "\033[31m", "\033[0m", "\033[2m"
_fails = 0


def check(cond: bool, what: str, detail: str = "") -> None:
    global _fails
    print(f"  {OK}OK{OFF}  {what}" if cond else f"  {BAD}FAIL{OFF}  {what}")
    if detail:
        print(f"      {DIM}{detail}{OFF}")
    if not cond:
        _fails += 1


seed_minimal_world(m.world)

SESSION, USER = "shop:table", "shop-user"
with Session(m.engine) as s:
    char = m.Character(
        discord_user_id=USER, name="Bryn", race="Human", char_class="Fighter",
        level=3, stats={"STR": 14, "DEX": 12, "CON": 13,
                        "INT": 10, "WIS": 11, "CHA": 9},
        gp=40, sp=0, cp=0, ep=0, pp=0, inventory=[])
    s.add(char)
    s.commit()
    s.refresh(char)
    char_id = char.id

from eight_card_system.seed import place_pc                        # noqa: E402
pc = place_pc(m.world, "Bryn", discord_user_id=USER)
where = m.world.location_of(pc.slug)

# A smith standing where the PC is. Merchants are ordinary NPCs with a role —
# that role is the whole of what makes a stall.
smith = m.world.upsert_entity("Hurin Ashfall", EntityType.NPC,
                              attributes={"role": "blacksmith"})
m.world.add_relation(smith.slug, RelationType.LOCATED_IN, where.slug)

m._set_session_meta(SESSION, {"character_id": char_id, "pc_slug": pc.slug})

print("\n\033[1m1. the stall\033[0m")
shop = m._activity_shop(SESSION, USER)
check(shop is not None, "there is a stall where a merchant is standing")
stalls = (shop or {}).get("stalls") or []
check(any(st_["name"] == "Hurin Ashfall" for st_ in stalls),
      "and it is the merchant the world slice has here",
      f"{[s_['name'] for s_ in stalls]}")
stock = stalls[0]["stock"] if stalls else []
check(bool(stock) and all(i.get("price_gp") for i in stock),
      "everything on it is priced", f"{[i['name'] for i in stock][:4]}")
check((shop or {}).get("purse_text", "").strip() != "",
      "and the purse is shown beside it", (shop or {}).get("purse_text"))

print("\n\033[1m2. the same roll the DM is told about\033[0m")
day = m.world.current_day()
ctx = m.world.get_world_context(pc.slug, "looks around")
ent = next((e for e in ctx.entities if e.slug == smith.slug), None)
direct = shops.roll_stock(smith.slug, "blacksmith", ctx.merchant_scale(ent), day)
check([i["name"] for i in direct] == [i["name"] for i in stock],
      "the panel's stock IS the DM's stock — one roll, not two",
      "a panel with its own would sell what the DM never saw for sale")

print("\n\033[1m3. buying moves real coin\033[0m")
want = stock[0]["name"]
price = stock[0]["price_gp"]
notes = m._activity_shop_buy(SESSION, USER, want)
with Session(m.engine) as s:
    after = s.get(m.Character, char_id)
    purse_gp = after.gp
    names = [str(i.get("name", "")) for i in (after.inventory or [])]
check(bool(notes), "the deal reports back", f"{notes[:1]}")
check(any(want.lower() in n.lower() for n in names),
      f"{want} is in the pack now", f"{names}")
check(purse_gp < 40, "and the purse is lighter",
      f"40 gp -> {purse_gp} gp for a {price:g} gp piece")

print("\n\033[1m4. what it refuses\033[0m")
check(m._activity_shop_buy(SESSION, USER, "Staff of the Magi") == [],
      "something nobody here stocks is refused outright")
with Session(m.engine) as s:
    broke = s.get(m.Character, char_id)
    broke.gp, broke.sp, broke.cp, broke.ep, broke.pp = 0, 0, 0, 0, 0
    s.add(broke)
    s.commit()
dear = max(stock, key=lambda i: i["price_gp"])
notes = m._activity_shop_buy(SESSION, USER, dear["name"])
with Session(m.engine) as s:
    still = s.get(m.Character, char_id)
    owns = [str(i.get("name", "")) for i in (still.inventory or [])]
check(any("short" in n.lower() for n in notes),
      "an empty purse is told it is short", f"{notes}")
check(dear["name"] not in owns or dear["name"] == want,
      "and buys nothing", f"{owns}")

print("\n\033[1m5. stock is the WEEK's, not the request's\033[0m")
again = m._activity_shop(SESSION, USER)
check([i["name"] for i in again["stalls"][0]["stock"]] ==
      [i["name"] for i in stock],
      "asking twice on the same day gives the same stall")
later = shops.roll_stock(smith.slug, "blacksmith", ctx.merchant_scale(ent), day + 7)
check([i["name"] for i in later] != [i["name"] for i in stock]
      or len(later) != len(stock),
      "and a week on it has rotated",
      f"was {[i['name'] for i in stock][:3]} … now {[i['name'] for i in later][:3]}")

print()
if _fails:
    print(f"{BAD}{_fails} FAILED{OFF}")
    sys.exit(1)
print(f"{OK}what is on the shelf is what the DM said was on the shelf{OFF}")
