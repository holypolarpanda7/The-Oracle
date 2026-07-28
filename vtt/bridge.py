"""
Board <-> tracker bridge.

``combat/`` already runs fights: it owns initiative, HP, conditions, the action
economy, and a deliberately gridless spacing model ("melee with Gruk", "near",
"far"). The VTT does *not* replace that. It adds exact position underneath it
and keeps both descriptions of the same fight true at once:

* every ``combat_combatant`` gets a token, hydrated with its real size, speed
  and reach;
* after anything moves, each combatant's ``position`` band is recomputed from
  actual distance, so ``combat/engine.py`` keeps validating reach exactly as it
  did before the board existed;
* when the *engine* moves someone by band (a monster's default AI closing to
  melee), the token is walked to a matching square, so the picture doesn't
  drift from the fiction.

The rule of thumb: **the grid is the truth when a board is out, the bands are
the interface**. Nothing in ``combat/`` needs to know the VTT exists.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from . import geometry as geo
from .models import Team, TokenKind, size_squares
from .scene import VttEngine

_MELEE_RE = re.compile(r"melee\s+with\s+(.+)", re.I)

#: Distance bands the gridless engine understands, in feet.
NEAR_FT = 30


def _size_for(monster_row: Any) -> str:
    size = getattr(monster_row, "size", None)
    return str(size).lower() if size else "medium"


def _speed_for(monster_row: Any, default: int = 30) -> int:
    """Walking speed in feet from an SRD monster row's speed blob."""
    raw = getattr(monster_row, "speed", None)
    if isinstance(raw, dict):
        walk = raw.get("walk") or raw.get("Walk")
        if isinstance(walk, str):
            m = re.search(r"(\d+)", walk)
            if m:
                return int(m.group(1))
        if isinstance(walk, int):
            return walk
    if isinstance(raw, str):
        m = re.search(r"(\d+)", raw)
        if m:
            return int(m.group(1))
    return default


def _reach_for(monster_row: Any, size: str) -> int:
    """Large-and-up creatures usually threaten 10 ft; everyone else 5."""
    return 10 if size_squares(size) >= 2 else 5


def seat_encounter(vtt: VttEngine, map_id: int, encounter_id: int, *,
                   tracker: Any = None, rules_lib: Any = None,
                   portrait_lookup: Any = None) -> list:
    """Give every combatant in a fight a token (idempotent).

    ``rules_lib`` (a ``rules.RulesLibrary``) is used to look up a monster's size
    and speed; ``portrait_lookup(combatant) -> image_id`` supplies token art.
    Both are optional — without them tokens are medium, 30 ft, and drawn as
    coloured discs.
    """
    tracker = tracker or vtt.tracker
    if tracker is None:
        return []
    created = []
    existing = {t.combatant_id for t in vtt.tokens(map_id) if t.combatant_id}
    for c in tracker.order(encounter_id):
        if c.id in existing:
            continue
        is_pc = (c.kind == "pc")
        size, speed, reach = "medium", 30, 5
        if c.monster_slug and rules_lib is not None:
            try:
                mon = rules_lib.get_monster(c.monster_slug)
                if mon is not None:
                    size = _size_for(mon)
                    speed = _speed_for(mon)
                    reach = _reach_for(mon, size)
            except Exception:
                pass
        image_id = None
        if portrait_lookup is not None:
            try:
                image_id = portrait_lookup(c)
            except Exception:
                image_id = None
        tok = vtt.add_token(
            map_id, c.name,
            kind=(TokenKind.PC if is_pc else
                  TokenKind.NPC if c.kind == "npc" else TokenKind.MONSTER),
            team=(Team.PARTY if is_pc else Team.FOE),
            size=size, speed_ft=speed, reach_ft=reach,
            combatant_id=c.id, character_id=c.character_id,
            monster_slug=c.monster_slug, image_id=image_id,
        )
        if tok:
            created.append(tok)
    if created:
        sync_bands(vtt, map_id, tracker=tracker)
    return created


def sync_bands(vtt: VttEngine, map_id: int, *, tracker: Any = None) -> None:
    """Rewrite every combatant's spacing band from where its token now stands.

    This is what lets the untouched combat engine keep making correct reach
    calls while the players push miniatures around a grid.
    """
    tracker = tracker or vtt.tracker
    if tracker is None:
        return
    row = vtt.get_scene(map_id)
    if row is None:
        return
    toks = [t for t in vtt.tokens(map_id) if t.combatant_id]
    if not toks:
        return
    fps = {t.id: geo.footprint(t.x, t.y, size_squares(t.size)) for t in toks}
    for t in toks:
        foes = [o for o in toks if o.team != t.team and not o.defeated]
        if not foes:
            band = "near"
        else:
            nearest = min(foes, key=lambda o: geo.token_distance_ft(
                fps[t.id], fps[o.id], row.square_ft))
            d = geo.token_distance_ft(fps[t.id], fps[nearest.id], row.square_ft)
            # "Engaged" means either creature can reach the other.
            engaged = d <= max(t.reach_ft or 5, nearest.reach_ft or 5)
            band = geo.band_for_distance(d, nearest.name if engaged else None)
        try:
            tracker.set_position(t.combatant_id, band)
        except Exception as e:
            print(f"[vtt.bridge] band sync failed for {t.name}: {e}")


def mirror_from_tracker(vtt: VttEngine, map_id: int, *, tracker: Any = None) -> None:
    """Pull tracker truth onto the board: who is down, who is prone/hidden."""
    tracker = tracker or vtt.tracker
    row = vtt.get_scene(map_id)
    if tracker is None or row is None or not row.encounter_id:
        return
    by_id = {c.id: c for c in tracker.order(row.encounter_id)}
    for t in vtt.tokens(map_id):
        c = by_id.get(t.combatant_id or -1)
        if c is None:
            continue
        conds = {str(x).lower() for x in (c.conditions or [])}
        changes = {}
        if bool(c.defeated) != bool(t.defeated):
            changes["defeated"] = bool(c.defeated)
        prone = "prone" in conds
        if prone != bool(t.prone):
            changes["prone"] = prone
        hidden = "invisible" in conds
        if hidden != bool(t.hidden) and t.team != Team.PARTY:
            changes["hidden"] = hidden
        if changes:
            vtt.update_token(t.id, **changes)


def apply_band_move(vtt: VttEngine, map_id: int, combatant_id: int,
                    band: Optional[str], *, tracker: Any = None) -> Optional[dict]:
    """Walk a token to match a band the *engine* decided ("melee with Gruk").

    Used when a fight advances without anyone touching the board — monster AI,
    or a player who typed "I charge the ogre" instead of clicking a square. The
    token takes a real path (so it can be blocked, and so it triggers the same
    opportunity attacks the engine already resolved through its own model).
    """
    if not band:
        return None
    tok = vtt.token_for_combatant(map_id, combatant_id)
    row = vtt.get_scene(map_id)
    if tok is None or row is None:
        return None
    grid = vtt.grid_of(row)
    band_l = band.strip().lower()

    target_sq = None
    m = _MELEE_RE.match(band_l)
    if m:
        other = vtt.find_token(map_id, m.group(1).strip())
        if other is None:
            return None
        # The nearest square that puts the mover inside its own reach.
        want = max(1, (tok.reach_ft or 5) // row.square_ft)
        best, best_d = None, 1 << 30
        for dx in range(-want - 1, want + 2):
            for dy in range(-want - 1, want + 2):
                cand = (other.x + dx, other.y + dy)
                if not grid.in_bounds(*cand):
                    continue
                d = geo.distance_squares((tok.x, tok.y), cand)
                if geo.token_distance_ft(
                        geo.footprint(cand[0], cand[1], size_squares(tok.size)),
                        geo.footprint(other.x, other.y, size_squares(other.size)),
                        row.square_ft) <= (tok.reach_ft or 5) and d < best_d:
                    best, best_d = cand, d
        target_sq = best
    elif band_l in ("near", "far"):
        foes = [t for t in vtt.tokens(map_id, include_defeated=False)
                if t.team != tok.team]
        if not foes:
            return None
        anchor = min(foes, key=lambda o: geo.distance_squares((tok.x, tok.y), (o.x, o.y)))
        want_ft = 15 if band_l == "near" else 60
        best, best_err = None, 1 << 30
        for x, y in grid.squares():
            if not grid.passable(x, y):
                continue
            d = geo.distance_ft((x, y), (anchor.x, anchor.y), row.square_ft)
            err = abs(d - want_ft) + geo.distance_squares((tok.x, tok.y), (x, y))
            if err < best_err:
                best, best_err = (x, y), err
        target_sq = best

    if target_sq is None:
        return None
    # ``free`` — the engine already charged this creature's movement in its own
    # economy; charging again on the board would double-bill the turn.
    return vtt.move_token(tok.id, target_sq[0], target_sq[1], free=True,
                          enforce_speed=False)


def sync_after_turn(vtt: VttEngine, map_id: int, *, tracker: Any = None) -> None:
    """One call to make the board agree with the tracker after a resolved turn."""
    mirror_from_tracker(vtt, map_id, tracker=tracker)
    sync_bands(vtt, map_id, tracker=tracker)
    vtt.recompute_auras(map_id)
