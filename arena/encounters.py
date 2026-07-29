"""Rostering fights for the Proving Grounds.

The Grounds are a *test harness for combat*, so the roster is built by code, not
by the model: given a level, a difficulty and an environment, this picks a set
of real stat blocks whose adjusted XP lands on the budget, and shapes it into
something worth fighting (a lone brute, a pack, a captain with mooks).

The DM still narrates the fight — it just doesn't get to invent the numbers.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from dm_guide.encounter import count_multiplier, estimate_encounter, party_budgets

from .environments import Environment

#: Roster shapes, as (label, weight). A shape is the *silhouette* of a fight —
#: how many things the player has to track at once.
_SHAPES: tuple[tuple[str, float], ...] = (
    ("solo", 1.0),        # one big thing
    ("duo", 1.2),         # two of a kind
    ("pack", 1.4),        # a handful of the same
    ("mob", 0.8),         # a crowd of weak things
    ("captain", 1.1),     # one leader plus mooks
)

_SHAPE_COUNTS = {"solo": (1, 1), "duo": (2, 2), "pack": (3, 5), "mob": (6, 8)}

#: Number words for roster names — reads better than "5x Goblin".
_WORDS = ("", "a lone", "a pair of", "three", "four", "five", "six", "seven",
          "eight", "nine", "ten")


@dataclass(frozen=True)
class MonsterCard:
    """The little we need to know about a stat block to roster it."""
    slug: str
    name: str
    type: str
    cr: float
    xp: int
    speeds: frozenset            # {"walk", "swim", "fly", "burrow", "climb"}
    size: str = "Medium"

    def moves(self, how: str) -> bool:
        return how in self.speeds


@dataclass
class ArenaRoster:
    """A built fight: what to seat, and how hard it came out."""
    title: str
    entries: list[tuple[str, int]] = field(default_factory=list)   # (slug, count)
    cards: list[tuple[MonsterCard, int]] = field(default_factory=list)
    raw_xp: int = 0
    adjusted_xp: int = 0
    xp_budget: int = 0
    target_difficulty: str = "medium"
    difficulty: str = "medium"
    #: True when the environment's own creatures couldn't fill the budget and
    #: the Grounds reached outside it. The DM is told, so the fiction can cover
    #: it ("the wards drag something in that does not belong here").
    conjured: bool = False

    @property
    def count(self) -> int:
        return sum(n for _, n in self.entries)

    def summary(self) -> str:
        parts = [f"{n}x {c.name} (CR {_cr_str(c.cr)}, {c.xp} XP)"
                 for c, n in self.cards]
        return (f"{self.title}: {', '.join(parts)} — {self.adjusted_xp} adjusted XP "
                f"against a {self.target_difficulty} budget of {self.xp_budget} "
                f"(reads as {self.difficulty})")


def _cr_str(cr: float) -> str:
    if cr and cr < 1:
        return {0.125: "1/8", 0.25: "1/4", 0.5: "1/2"}.get(round(cr, 3), str(cr))
    return str(int(cr)) if float(cr).is_integer() else str(cr)


# ------------------------------------------------------------------ loading

def load_cards(engine: Any) -> list[MonsterCard]:
    """Every rosterable stat block in the rules database.

    A stat block with no XP can't be budgeted against, so it never appears in
    the Grounds — it can still be summoned by hand at a real table.
    """
    from sqlmodel import Session, select

    from rules.models import Monster

    out: list[MonsterCard] = []
    with Session(engine) as s:
        for m in s.exec(select(Monster)).all():
            xp = int(m.xp or 0)
            if xp <= 0 or not m.index_slug:
                continue
            speeds = {str(k).lower() for k, v in (m.speed or {}).items() if v}
            out.append(MonsterCard(
                slug=m.index_slug, name=m.name, type=(m.type or "").lower(),
                cr=float(m.challenge_rating or 0), xp=xp,
                speeds=frozenset(speeds), size=(m.size or "Medium")))
    return out


# ----------------------------------------------------------------- filtering

def suits_environment(card: MonsterCard, env: Environment) -> bool:
    """Can this creature fight *here* under its own power?"""
    if env.requires_speed:
        return card.moves(env.requires_speed)
    # A ground board: anything that can walk. A shark on a sand ring is absurd.
    return card.moves("walk")


def candidates_for(env: Environment, cards: Iterable[MonsterCard], level: int,
                   ) -> tuple[list[MonsterCard], bool]:
    """The stat blocks worth considering for this board and level.

    Returns ``(cards, conjured)`` — ``conjured`` is True when the environment's
    own creatures were too thin to field a fight and the filter was dropped.
    """
    cap = _cr_cap(level)
    native = [c for c in cards if suits_environment(c, env) and c.cr <= cap]
    # Three is enough to build a fight from. Below that the rosters would be the
    # same two creatures forever, and a conjured stranger is the better bout.
    if len(native) >= 3:
        return native, False
    anything = [c for c in cards if c.cr <= cap]
    return (anything, True) if anything else (native, False)


def _cr_cap(level: int) -> float:
    """The hardest single creature the Grounds will field at this level.

    A little above the party's level — high enough that a solo fight can bite,
    low enough that one unlucky round isn't simply lethal.
    """
    return max(1.0, float(level) + 2.0)


# ------------------------------------------------------------------ building

def build_roster(env: Environment, level: int, cards: Iterable[MonsterCard], *,
                 difficulty: str = "medium", party_size: int = 1,
                 rng: Optional[random.Random] = None) -> Optional[ArenaRoster]:
    """Pick a fight for ``level`` in ``env`` that spends the ``difficulty`` budget.

    Returns ``None`` only when there are no usable stat blocks at all (an
    un-ingested rules database).
    """
    rng = rng or random
    pool, conjured = candidates_for(env, list(cards), level)
    if not pool:
        return None

    budgets = party_budgets([max(1, level)] * max(1, party_size))
    tier = difficulty if difficulty in budgets else "medium"
    budget = budgets[tier]

    shape = _pick_shape(rng, pool, budget)
    if shape == "captain":
        picks = _build_captain(pool, budget, rng)
    else:
        lo, hi = _SHAPE_COUNTS[shape]
        n = rng.randint(lo, hi)
        lead = _pick_for_share(pool, budget, n, rng)
        picks = [(lead, n)] if lead else []

    if not picks:
        return None

    picks = _fit_budget(picks, budget, party_size, level)
    xps = [c.xp for c, n in picks for _ in range(n)]
    est = estimate_encounter([max(1, level)] * max(1, party_size), xps)
    return ArenaRoster(
        title=_title(picks, env),
        entries=[(c.slug, n) for c, n in picks],
        cards=picks,
        raw_xp=est["raw_xp"], adjusted_xp=est["adjusted_xp"],
        xp_budget=budget, target_difficulty=tier, difficulty=est["difficulty"],
        conjured=conjured,
    )


def _fit_budget(picks: list[tuple[MonsterCard, int]], budget: int,
                party_size: int, level: int) -> list[tuple[MonsterCard, int]]:
    """Nudge the count until the fight actually spends its budget.

    Picking the closest stat block leaves the roster short more often than not
    (XP values are coarse and the count multiplier bites), which is how you end
    up testing "deadly" against three rats. One creature at a time, add or drop
    from the cheapest entry until the adjusted XP is in range.
    """
    def adjusted(rows: list[tuple[MonsterCard, int]]) -> int:
        xps = [c.xp for c, n in rows for _ in range(n)]
        return estimate_encounter([max(1, level)] * max(1, party_size), xps)["adjusted_xp"]

    rows = [list(p) for p in picks]
    cheapest = min(range(len(rows)), key=lambda i: rows[i][0].xp)
    for _ in range(12):
        total = adjusted(rows)  # type: ignore[arg-type]
        n_all = sum(int(r[1]) for r in rows)
        if total < budget * 0.8 and n_all < 8:
            rows[cheapest][1] = int(rows[cheapest][1]) + 1
        elif total > budget * 1.45 and int(rows[cheapest][1]) > 1:
            rows[cheapest][1] = int(rows[cheapest][1]) - 1
        elif total > budget * 1.45 and len(rows) > 1:
            rows.pop(cheapest)
            cheapest = min(range(len(rows)), key=lambda i: rows[i][0].xp)
        else:
            break
    return [(c, int(n)) for c, n in rows]  # type: ignore[misc]


def _pick_shape(rng: random.Random, pool: list[MonsterCard], budget: int) -> str:
    """Choose a silhouette, dropping shapes the pool can't actually fill."""
    cheapest = min(c.xp for c in pool)
    shapes = []
    for name, weight in _SHAPES:
        lo = _SHAPE_COUNTS.get(name, (2, 2))[0]
        # A mob of the cheapest thing available must still fit the budget,
        # or the shape is a lie.
        if cheapest * lo * count_multiplier(lo) > budget * 1.6:
            continue
        shapes.append((name, weight))
    if not shapes:
        return "solo"
    names = [n for n, _ in shapes]
    return rng.choices(names, weights=[w for _, w in shapes])[0]


def _pick_for_share(pool: list[MonsterCard], budget: int, n: int,
                    rng: random.Random, share: float = 1.0
                    ) -> Optional[MonsterCard]:
    """The creature whose XP, ``n`` of them, best spends ``share`` of the budget."""
    if n <= 0:
        return None
    target = (budget * share) / (n * count_multiplier(n))
    ranked = sorted(pool, key=lambda c: abs(c.xp - target))
    # Some slack at the top so the same level doesn't always draw the same foe.
    return rng.choice(ranked[:max(1, min(5, len(ranked)))])


def _build_captain(pool: list[MonsterCard], budget: int, rng: random.Random
                   ) -> list[tuple[MonsterCard, int]]:
    """One leader worth about half the budget, with mooks for the rest."""
    n_mooks = rng.randint(2, 4)
    lead = _pick_for_share(pool, budget, 1, rng, share=0.55)
    mook_pool = [c for c in pool if lead is None or c.xp < lead.xp] or pool
    mook = _pick_for_share(mook_pool, budget, n_mooks, rng, share=0.45)
    if lead is None or mook is None:
        return [(lead or mook, 1)] if (lead or mook) else []
    if mook.slug == lead.slug:
        return [(lead, 1 + n_mooks)]
    return [(lead, 1), (mook, n_mooks)]


def _title(picks: list[tuple[MonsterCard, int]], env: Environment) -> str:
    """A readable name for the fight, from what's in it."""
    def phrase(card: MonsterCard, n: int) -> str:
        word = _WORDS[n] if n < len(_WORDS) else str(n)
        name = card.name if n == 1 else _plural(card.name)
        return f"{word} {name}".strip()

    if len(picks) == 1:
        return phrase(*picks[0]).title()
    lead, mooks = picks[0], picks[1]
    tail = (f"one {mooks[0].name}" if mooks[1] == 1 else phrase(*mooks))
    return f"{lead[0].name} and {tail}".title()


def _plural(name: str) -> str:
    if name.endswith(("s", "x", "z", "ch", "sh")):
        return name + "es"
    if name.endswith("y") and name[-2:-1].lower() not in "aeiou":
        return name[:-1] + "ies"
    return name + "s"
