"""
Relationship dynamics — evolving, alignment-weighted bonds between entities.

The lore layer (graph.record_lore) captures a single durable "why" on a
relationship. This module makes that relationship *live*:

  * ACCUMULATE — instead of one reason, an edge carries a small, bounded LEDGER
    of deeds ({day, tag, valence, text}). One short list, capped, so memory stays
    flat no matter how long two powers feud.
  * DECAY — a deed's weight fades toward zero over world-time (reusing the house
    entropy style). Old grudges lose their sting; a fresh, strong deed outweighs
    them. Grave deeds (murder, betrayal, honour) fade ~10x slower.
  * ALIGN — every entity has numeric alignment AXES (good<->evil, lawful<->
    chaotic). The perceiver's axes reshape how much each deed weighs: a lawful
    soul takes an oath-breaking harder; an evil one scorns mercy and admires
    cruelty (third-party judgement). A wrong done TO the perceiver always stays a
    wrong — you resent betrayal whatever your alignment.
  * DRIFT — the deeds you COMMIT reshape who you are: enough vengeance drags a
    good god toward evil; a chaotic power who keeps his word drifts lawful. The
    ``alignment`` string every power carries becomes a DERIVED label over the
    axes, kept in sync.
  * FLIP — when the decayed, alignment-weighted NET crosses a band, the typed
    edge (hostile_to / knows / allied_with) is closed and the opposite opened,
    append-only: "were enemies days 0-400, allied since." Nothing is lost.

Everything here is memory-cheap and OPT-IN per relationship: a static seeded feud
keeps just its one reason string until a deed actually happens between the pair;
decay and the alignment lens are pure read-time functions that store nothing.
"""
from __future__ import annotations

from typing import Optional

from sqlmodel import Session, select

from .graph import WorldGraph
from .models import Entity, Relation, RelationType

# --- tuning -----------------------------------------------------------------
LEDGER_CAP = 6                 # max deeds kept per relationship (least-impactful drop)
DECAY_GRACE_DAYS = 30          # no fade inside this window
DECAY_LIFE_DAYS = 360          # a light deed fully fades ~1 world-year past grace
DECAY_LIFE_SLOW_DAYS = 3600    # grave deeds (murder/betrayal/honour) ~10x slower
AXIS_MIN, AXIS_MAX = -100, 100
AXIS_POLE = 34                 # |axis| >= this reads as a pole (good/evil, lawful/chaotic)
# Net sentiment band boundaries -> typed relationship category.
BAND_HOSTILE = -3.0            # net <= this => hostile_to (one betrayal reaches it)
BAND_ALLIED = 3.0             # net >= this => allied_with ; between => knows


# --- deed taxonomy ----------------------------------------------------------
# v      : base valence from a good/lawful baseline (-3..+3)
# drift  : (d_moral, d_ethic) nudge to the ACTOR's own axes per deed
# order  : is this deed about oaths/law/order? (law-axis scales the reaction)
# moral  : is this a virtue/vice the good-evil axis judges?
# opol   : order polarity (+1 pro-order, -1 pro-chaos, 0 none) for third-party judging
# slow   : grave deed — decays on the slow life
# drift is deliberately STICKY: ~6 consistent grave deeds move a soul a full pole
# (|axis| 34), so alignment shifts over an arc, never on a single act.
TAGS: dict[str, dict] = {
    "betrayal":   {"v": -3, "drift": (-6, -5), "order": True,  "moral": True,  "opol": -1, "slow": True},
    "murder":     {"v": -3, "drift": (-8,  0), "order": False, "moral": True,  "opol": 0,  "slow": True},
    "cruelty":    {"v": -2, "drift": (-5,  0), "order": False, "moral": True,  "opol": 0,  "slow": False},
    "theft":      {"v": -1, "drift": (-2, -2), "order": True,  "moral": True,  "opol": -1, "slow": False},
    "tyranny":    {"v": -1, "drift": (-3,  5), "order": True,  "moral": True,  "opol": 1,  "slow": False},
    "defiance":   {"v":  0, "drift": ( 0, -4), "order": True,  "moral": False, "opol": -1, "slow": False},
    "oath_broken":{"v": -2, "drift": (-3, -5), "order": True,  "moral": True,  "opol": -1, "slow": True},
    "mercy":      {"v":  2, "drift": ( 5,  0), "order": False, "moral": True,  "opol": 0,  "slow": False},
    "protection": {"v":  2, "drift": ( 4,  0), "order": False, "moral": True,  "opol": 0,  "slow": False},
    "rescue":     {"v":  2, "drift": ( 4,  0), "order": False, "moral": True,  "opol": 0,  "slow": False},
    "gift":       {"v":  1, "drift": ( 2,  0), "order": False, "moral": True,  "opol": 0,  "slow": False},
    "healing":    {"v":  1, "drift": ( 2,  0), "order": False, "moral": True,  "opol": 0,  "slow": False},
    "loyalty":    {"v":  2, "drift": ( 3,  5), "order": True,  "moral": True,  "opol": 1,  "slow": True},
    "oath_kept":  {"v":  2, "drift": ( 3,  5), "order": True,  "moral": True,  "opol": 1,  "slow": True},
    "honor":      {"v":  3, "drift": ( 5,  5), "order": True,  "moral": True,  "opol": 1,  "slow": True},
    "sacrifice":  {"v":  3, "drift": ( 7,  3), "order": False, "moral": True,  "opol": 0,  "slow": True},
}

# Loose words -> canonical tag, so the DM can write naturally.
_TAG_ALIASES = {
    "betray": "betrayal", "betrayed": "betrayal", "treachery": "betrayal",
    "murdered": "murder", "slew": "murder", "slain": "murder", "killed": "murder",
    "torture": "cruelty", "tortured": "cruelty", "tormented": "cruelty", "cruel": "cruelty",
    "stole": "theft", "robbed": "theft", "theft ": "theft",
    "tyrant": "tyranny", "enslaved": "tyranny", "dominated": "tyranny",
    "defied": "defiance", "rebelled": "defiance", "rebellion": "defiance", "defy": "defiance",
    "oathbreaking": "oath_broken", "broke_oath": "oath_broken", "forsworn": "oath_broken",
    "spared": "mercy", "spare": "mercy", "pardoned": "mercy",
    "protected": "protection", "defended": "protection", "shielded": "protection",
    "saved": "rescue", "rescued": "rescue",
    "aid": "gift", "aided": "gift", "gave": "gift", "gifted": "gift", "helped": "gift",
    "healed": "healing", "mended": "healing",
    "loyal": "loyalty", "stood_by": "loyalty",
    "kept_oath": "oath_kept", "swore": "oath_kept", "oath": "oath_kept", "vowed": "oath_kept",
    "honored": "honor", "honour": "honor", "honorable": "honor",
    "sacrificed": "sacrifice", "martyred": "sacrifice",
}


def normalize_tag(raw: str) -> Optional[str]:
    """Map a freeform tag word onto a canonical deed tag, or None if unknown."""
    key = (raw or "").strip().lower().replace(" ", "_").replace("-", "_")
    if key in TAGS:
        return key
    return _TAG_ALIASES.get(key)


# --- alignment axes ---------------------------------------------------------

def _clamp_axis(x: int) -> int:
    return max(AXIS_MIN, min(AXIS_MAX, int(round(x))))


def parse_alignment(text: str) -> dict:
    """A 'lawful good' / 'chaotic evil' / 'neutral' string -> axes {'m','e'}."""
    t = (text or "").strip().lower()
    m = 70 if "good" in t else -70 if "evil" in t else 0
    e = 70 if "lawful" in t else -70 if "chaotic" in t else 0
    return {"m": m, "e": e}


def axes_to_label(axes: dict) -> str:
    """Numeric axes -> the classic nine-box label."""
    m, e = int(axes.get("m", 0)), int(axes.get("e", 0))
    ethic = "lawful" if e >= AXIS_POLE else "chaotic" if e <= -AXIS_POLE else "neutral"
    moral = "good" if m >= AXIS_POLE else "evil" if m <= -AXIS_POLE else "neutral"
    if ethic == "neutral" and moral == "neutral":
        return "true neutral"
    return f"{ethic} {moral}"


def get_axes(ent: Entity) -> dict:
    """An entity's live alignment axes, seeded from its label on first read."""
    attrs = ent.attributes or {}
    ax = attrs.get("align_axes")
    if isinstance(ax, dict) and "m" in ax and "e" in ax:
        return {"m": int(ax["m"]), "e": int(ax["e"])}
    return parse_alignment(attrs.get("alignment", ""))


# --- perception + decay -----------------------------------------------------

def perceive(v: int, tag: str, axes: dict, personal: bool = True) -> float:
    """Alignment-weighted valence of one deed, from the perceiver's view.

    personal=True  : the deed was done TO the perceiver — sign is preserved
                     (harm is bad, boon is good) but the law-axis scales how hard
                     an order-deed (oath/betrayal) lands.
    personal=False : the perceiver is JUDGING a deed in the world — here alignment
                     can invert it: an evil soul scorns virtue and admires cruelty;
                     a lawful one prizes order, a chaotic one prizes defiance.
    """
    info = TAGS.get(tag)
    if info is None:
        return float(v)
    m = axes.get("m", 0) / 100.0
    e = axes.get("e", 0) / 100.0
    if personal:
        val = float(v)
        if info["order"]:
            val *= (1.0 + 0.5 * e)          # lawful: oaths/betrayals against me cut deeper
        return val
    # Third-party moral/order judgement — alignment can flip the sign.
    val = 0.0
    if info["moral"]:
        polarity = 1.0 if v > 0 else -1.0 if v < 0 else 0.0
        val += abs(v) * polarity * m        # evil scorns virtue, admires vice
    if info["opol"]:
        val += (abs(v) or 1) * info["opol"] * e  # lawful prizes order, chaotic prizes defiance
    return val


def decay_factor(day: int, today: int, slow: bool) -> float:
    """Linear fade toward 0 over world-time; 1.0 fresh, 0.0 fully faded."""
    gap = max(0, int(today) - int(day) - DECAY_GRACE_DAYS)
    life = DECAY_LIFE_SLOW_DAYS if slow else DECAY_LIFE_DAYS
    return max(0.0, 1.0 - gap / float(life))


def net_sentiment(ledger: list[dict], perceiver_axes: dict, today: int) -> float:
    """Decayed, alignment-weighted sum of a ledger, from the perceiver's view."""
    total = 0.0
    for entry in ledger or []:
        tag = entry.get("g")
        info = TAGS.get(tag)
        if info is None:
            continue
        w = perceive(entry.get("v", 0), tag, perceiver_axes,
                     personal=bool(entry.get("p", 1)))
        total += w * decay_factor(entry.get("d", today), today, info["slow"])
    return total


def band_category(net: float) -> str:
    """A net sentiment -> the typed relationship category it implies."""
    if net <= BAND_HOSTILE:
        return RelationType.HOSTILE_TO
    if net >= BAND_ALLIED:
        return RelationType.ALLIED_WITH
    return RelationType.KNOWS


def band_word(net: float) -> str:
    """A short human word for a net sentiment (for context rendering)."""
    if net <= -7:
        return "loathes"
    if net <= BAND_HOSTILE:
        return "hostile"
    if net < -1:
        return "wary"
    if net < 1:
        return "neutral"
    if net < BAND_ALLIED:
        return "warm"
    if net < 7:
        return "allied"
    return "devoted"


# --- writing deeds ----------------------------------------------------------

_CATEGORIES = (RelationType.HOSTILE_TO, RelationType.ALLIED_WITH, RelationType.KNOWS)


def _top_reason(ledger: list[dict], axes: dict, today: int) -> str:
    """The single most-impactful surviving deed's text (for the reason string)."""
    best, best_mag = "", 0.0
    for entry in ledger or []:
        info = TAGS.get(entry.get("g"))
        if info is None:
            continue
        mag = abs(perceive(entry.get("v", 0), entry["g"], axes,
                           personal=bool(entry.get("p", 1)))
                  ) * decay_factor(entry.get("d", today), today, info["slow"])
        if mag >= best_mag and entry.get("t"):
            best, best_mag = entry["t"], mag
    return best


def _drift_actor(graph: WorldGraph, actor_id: int, tag: str) -> Optional[str]:
    """Nudge the actor's alignment axes by the deed they committed. Returns the
    new label if it changed, else None."""
    info = TAGS.get(tag)
    if info is None:
        return None
    dm, de = info["drift"]
    if dm == 0 and de == 0:
        return None
    with Session(graph.engine) as s:
        ent = s.get(Entity, actor_id)
        if ent is None:
            return None
        cur = get_axes(ent)
        before = axes_to_label(cur)
        ax = {"m": _clamp_axis(cur["m"] + dm), "e": _clamp_axis(cur["e"] + de)}
        after = axes_to_label(ax)
        new_attrs = {**(ent.attributes or {}), "align_axes": ax,
                     "alignment": after}
        # Remember the soul's starting alignment the first time it ever drifts,
        # so the DM can see how far a power has fallen (or risen) from its origin.
        new_attrs.setdefault("align_origin", before)
        ent.attributes = new_attrs
        s.add(ent)
        s.commit()
    return after if after != before else None


def record_deed(
    graph: WorldGraph,
    actor,
    target,
    *,
    tag: str,
    text: str = "",
    personal: bool = True,
    session_id: Optional[str] = None,
) -> Optional[dict]:
    """Record a deed by ``actor`` affecting ``target``, driving the live model.

    Appends to the (bounded) ledger on the target->actor edge, recomputes the
    target's decayed alignment-weighted sentiment toward the actor, flips the
    typed edge if the net crosses a band, and drifts the ACTOR's own alignment.
    Returns a small summary dict, or None if either entity is unknown / tag bad.
    """
    canon = normalize_tag(tag)
    if canon is None:
        return None
    info = TAGS[canon]
    graph.create_tables()
    with Session(graph.engine) as s:
        actor_e = graph._resolve_entity(s, actor)
        target_e = graph._resolve_entity(s, target)
        if actor_e is None or target_e is None:
            return None
        # Capture plain values now — the entities detach when the session closes.
        actor_id, target_id = actor_e.id, target_e.id
        actor_slug, actor_name = actor_e.slug, actor_e.name
        target_slug, target_name = target_e.slug, target_e.name
        day = graph._day(s)

        # The relationship that HOLDS the ledger is the perceiver's stance:
        # target (who was acted upon) -> actor (who is judged).
        edge = next((r for r in s.exec(select(Relation).where(
            Relation.src_id == target_id, Relation.dst_id == actor_id,
            Relation.valid_to == None)).all()  # noqa: E711
            if r.rel_type in _CATEGORIES), None)
        if edge is None:
            edge = Relation(src_id=target_id, rel_type=RelationType.KNOWS,
                            dst_id=actor_id, attributes={}, valid_from=day)
            s.add(edge)
            s.commit()
            s.refresh(edge)

        ledger = list((edge.attributes or {}).get("ledger") or [])
        ledger.append({"d": day, "g": canon, "v": info["v"],
                       "t": (text or "").strip()[:120], "p": 1 if personal else 0})

        perceiver_axes = get_axes(target_e)
        # Bound the ledger: when over cap, drop the least-impactful (fully-decayed
        # or weakest) entry so memory stays flat and the strong deeds survive.
        if len(ledger) > LEDGER_CAP:
            def mag(entry):
                di = TAGS.get(entry.get("g"))
                if di is None:
                    return 0.0
                return abs(perceive(entry.get("v", 0), entry["g"], perceiver_axes,
                                    personal=bool(entry.get("p", 1)))
                           ) * decay_factor(entry.get("d", day), day, di["slow"])
            ledger.sort(key=mag, reverse=True)
            ledger = ledger[:LEDGER_CAP]

        net = net_sentiment(ledger, perceiver_axes, day)
        new_cat = band_category(net)
        reason = _top_reason(ledger, perceiver_axes, day)
        edge_attrs = {**(edge.attributes or {}), "ledger": ledger,
                      "sentiment": round(net, 1)}
        if reason:
            edge_attrs["reason"] = reason

        flipped = None
        if new_cat != edge.rel_type:
            # Append-only flip: close the old-sentiment edge, open the new one
            # carrying the same ledger forward.
            edge.valid_to = day
            s.add(edge)
            new_edge = Relation(src_id=target_id, rel_type=new_cat, dst_id=actor_id,
                                attributes=edge_attrs, valid_from=day)
            s.add(new_edge)
            flipped = (edge.rel_type, new_cat)
        else:
            edge.attributes = edge_attrs
            s.add(edge)
        s.commit()

    drift_label = _drift_actor(graph, actor_id, canon)

    if flipped:
        graph.add_event(
            f"{target_name}'s regard for {actor_name} shifts from "
            f"{flipped[0].replace('_', ' ')} to {flipped[1].replace('_', ' ')}"
            + (f" — {reason}" if reason else ""),
            involved=[target_slug, actor_slug], session_id=session_id)

    return {"actor": actor_slug, "target": target_slug, "tag": canon,
            "net": round(net, 1), "category": new_cat, "flipped": flipped,
            "actor_alignment": drift_label}


def current_alignment(graph: WorldGraph, ref) -> Optional[str]:
    """The entity's live alignment label (over its drifting axes)."""
    ent = graph.get_entity(ref)
    return axes_to_label(get_axes(ent)) if ent is not None else None
