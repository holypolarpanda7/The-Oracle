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
                fps[t.id], fps[o.id], row.square_ft,
                dz_ft=vtt.height_gap_ft(row, t, o)))
            d = geo.token_distance_ft(fps[t.id], fps[nearest.id], row.square_ft,
                                      dz_ft=vtt.height_gap_ft(row, t, nearest))
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


class BoardSpatial:
    """Exact distances for ``combat.CombatEngine``, taken off the board.

    The engine asks two questions — "how far apart are these two?" and "how far
    can this one reach?" — and falls back to its own band model whenever this
    answers ``None``. So attaching one of these upgrades reach checks from
    near/far to real feet without the engine knowing a grid exists, and
    detaching it puts the table straight back to theater of the mind.

        combat_engine.spatial = BoardSpatial(vtt, map_id)
    """

    def __init__(self, vtt: VttEngine, map_id: int):
        self.vtt = vtt
        self.map_id = map_id
        row = vtt.get_scene(map_id)
        self.square_ft = row.square_ft if row else 5
        self._tokens = {t.combatant_id: t for t in vtt.tokens(map_id)
                        if t.combatant_id}

    def _tok(self, c):
        return self._tokens.get(getattr(c, "id", None))

    def distance_ft(self, a, b) -> Optional[int]:
        ta, tb = self._tok(a), self._tok(b)
        if ta is None or tb is None:
            return None
        # Height counts: this is what gates the combat engine's reach checks
        # and weapon ranges, so a flier overhead has to read as far away.
        row = self.vtt.get_scene(self.map_id)
        dz = self.vtt.height_gap_ft(row, ta, tb) if row else 0
        return geo.token_distance_ft(
            geo.footprint(ta.x, ta.y, size_squares(ta.size)),
            geo.footprint(tb.x, tb.y, size_squares(tb.size)),
            self.square_ft, dz_ft=dz)

    def reach_ft(self, c) -> int:
        t = self._tok(c)
        return int(t.reach_ft or 5) if t is not None else 5

    def cover(self, attacker, target) -> Optional[str]:
        ta, tb = self._tok(attacker), self._tok(target)
        if ta is None or tb is None:
            return None
        return self.vtt.cover_for(self.map_id, ta.name, tb.name)

    def underwater(self) -> bool:
        """Is this fight being fought in the water?

        The board already knew — a swim-medium board is one whose layout is
        only connected to a swimmer — but the combat engine had no way to ask,
        so the underwater rules lived as a sentence of prose in the arena
        catalogue telling the DM to remember them by hand.
        """
        row = self.vtt.get_scene(self.map_id)
        return bool(row) and self.vtt.board_mode(row) == "swim"

    def swims(self, c) -> bool:
        """Has this creature a swimming speed? Not: is it currently swimming."""
        t = self._tok(c)
        return t is not None and self.vtt.swim_speed_ft(t) > 0

    def can_see(self, a, b) -> Optional[bool]:
        """Can ``a`` perceive ``b``? ``None`` when the board can't say.

        The board knows something the combat engine cannot: light. Whether an
        attacker can see their target decides advantage in both directions, and
        without this the engine reads it off CONDITIONS alone — so a fight in
        an unlit crypt rolled exactly like a fight at noon. Answering ``None``
        (either creature isn't on the board) leaves the engine on its own
        model, which is the same contract ``distance_ft`` and ``cover`` keep.
        """
        ta, tb = self._tok(a), self._tok(b)
        if ta is None or tb is None:
            return None
        return self.vtt.can_see(self.map_id, ta.name, tb.name)


def sync_cover(vtt: VttEngine, map_id: int, *, tracker: Any = None) -> None:
    """Write every creature's cover **as seen from whoever's turn it is**.

    Cover is only ever a fact about a target and a particular attacker, so the
    single value the tracker carries per creature is pinned to the one attacker
    that matters right now: the creature acting. On Gruk's turn, everyone else's
    cover is their cover *from Gruk* — which is exactly what the engine needs
    when Gruk swings, and what the DM needs when deciding whether the shot is
    worth taking.

    The acting creature's own cover is measured from its nearest enemy instead,
    since the only attacks it faces on its own turn are reactions from whoever
    it is standing next to.

    A cover value the DM set by hand (``[[COMBAT: cover | X | half]]`` — for
    something the terrain can't know, like a creature hunkered behind a
    barricade) is kept as a floor, so recomputing never erases their ruling.
    """
    tracker = tracker or vtt.tracker
    row = vtt.get_scene(map_id)
    if tracker is None or row is None:
        return
    toks = [t for t in vtt.tokens(map_id, include_defeated=False) if t.combatant_id]
    if not toks:
        return
    actor = None
    if row.encounter_id:
        try:
            cur = tracker.current_combatant(row.encounter_id)
            actor = next((t for t in toks if t.combatant_id == (cur.id if cur else None)),
                         None)
        except Exception:
            actor = None
    overrides = ((row.notes or {}).get("cover_override") or {})

    for t in toks:
        try:
            if actor is not None and t.id != actor.id:
                cover = vtt.cover_for(map_id, actor.name, t.name)
            else:
                foes = [o for o in toks if o.team != t.team]
                if not foes:
                    continue
                nearest = min(foes, key=lambda o: geo.token_distance_ft(
                    geo.footprint(t.x, t.y, size_squares(t.size)),
                    geo.footprint(o.x, o.y, size_squares(o.size)), row.square_ft))
                cover = vtt.cover_for(map_id, nearest.name, t.name)
            manual = overrides.get(str(t.combatant_id))
            if manual and geo.COVER_ORDER.get(manual, 0) > geo.COVER_ORDER.get(cover, 0):
                cover = manual
            tracker.set_cover(t.combatant_id, cover)
        except Exception as e:
            print(f"[vtt.bridge] cover sync failed for {t.name}: {e}")


def board_band(vtt: VttEngine, map_id: int, tok, *, square_ft: int = 5) -> str:
    """The band the *board* implies for a token — the inverse of a band move."""
    foes = [o for o in vtt.tokens(map_id, include_defeated=False)
            if o.team != tok.team and o.kind not in (TokenKind.MARKER,)]
    if not foes:
        return "near"
    fp = geo.footprint(tok.x, tok.y, size_squares(tok.size))
    nearest = min(foes, key=lambda o: geo.token_distance_ft(
        fp, geo.footprint(o.x, o.y, size_squares(o.size)), square_ft))
    d = geo.token_distance_ft(
        fp, geo.footprint(nearest.x, nearest.y, size_squares(nearest.size)),
        square_ft)
    engaged = d <= max(tok.reach_ft or 5, nearest.reach_ft or 5)
    return geo.band_for_distance(d, nearest.name if engaged else None)


def _band_rank(band: Optional[str]) -> tuple[int, str]:
    """(rank, engaged-with) — rank 0 melee, 1 near, 2 far."""
    b = (band or "near").strip().lower()
    m = _MELEE_RE.match(b)
    if m:
        return 0, m.group(1).strip().lower()
    return (2, "") if b == "far" else (1, "")


def reconcile_bands(vtt: VttEngine, map_id: int, *, tracker: Any = None) -> int:
    """Walk tokens to match bands the *engine* changed without touching the board.

    A monster's default AI closes to melee in the gridless model; nothing moved
    on the grid. Left alone, the next :func:`sync_bands` would simply overwrite
    that decision and the creature would teleport back to where its token stood.
    So before writing bands out, any combatant whose tracker band disagrees with
    its token's position gets its token moved to match the fiction.

    Returns how many tokens were repositioned.
    """
    tracker = tracker or vtt.tracker
    row = vtt.get_scene(map_id)
    if tracker is None or row is None or not row.encounter_id:
        return 0
    moved = 0
    try:
        combatants = {c.id: c for c in tracker.order(row.encounter_id)}
    except Exception:
        return 0
    for tok in vtt.tokens(map_id, include_defeated=False):
        c = combatants.get(tok.combatant_id or -1)
        if c is None or not c.position:
            continue
        want_rank, want_target = _band_rank(c.position)
        have_rank, have_target = _band_rank(
            board_band(vtt, map_id, tok, square_ft=row.square_ft))
        if want_rank == have_rank and want_target == have_target:
            continue
        if apply_band_move(vtt, map_id, c.id, c.position, tracker=tracker):
            moved += 1
    return moved


def sync_after_turn(vtt: VttEngine, map_id: int, *, tracker: Any = None) -> None:
    """One call to make the board agree with the tracker after a resolved turn."""
    mirror_from_tracker(vtt, map_id, tracker=tracker)
    # Fiction first (the engine may have moved someone by band), then write the
    # exact bands and cover back from the grid.
    reconcile_bands(vtt, map_id, tracker=tracker)
    sync_bands(vtt, map_id, tracker=tracker)
    sync_cover(vtt, map_id, tracker=tracker)
    vtt.recompute_auras(map_id)
