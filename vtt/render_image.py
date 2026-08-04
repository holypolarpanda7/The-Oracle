"""
Draw the board as a picture, for tables that aren't in the Activity.

The web overlay is the good seat: live, interactive, costed. But most play
happens in a Discord channel where the only thing the Oracle can hand a player
is a message and an attachment. A fight without a board there is exactly the
theater-of-the-mind experience the grid was supposed to fix, so this module
renders the same scene state to a PNG the bot can post.

    from vtt.render_image import render_board_png
    png = render_board_png(vtt_engine.state(map_id))

It draws from :meth:`vtt.scene.VttEngine.state` — the identical dict the web
overlay consumes — so the two views can never disagree about where anyone is.
Coordinates are printed along the edges, because a player looking at this
picture needs to be able to say "I move to 9,5" and have it mean something.
"""
from __future__ import annotations

import io
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

# Palette mirrors activity-ui/src/lib/vttPaint.ts. Keep them in step: the same
# room should look like the same room in Discord and in the browser.
_TILE_COLORS: dict[str, tuple[int, int, int]] = {
    ".": (36, 42, 66), "g": (43, 63, 49), "s": (69, 59, 40), "b": (74, 55, 34),
    "=": (51, 56, 74), ",": (58, 58, 66), '"': (47, 68, 51), "~": (29, 66, 87),
    "m": (58, 49, 38), "i": (59, 85, 102), "%": (74, 74, 82), "u": (59, 64, 92),
    "#": (16, 20, 31), "R": (29, 31, 42), "T": (34, 55, 31), "O": (58, 66, 112),
    "o": (74, 56, 35), "n": (66, 49, 31), "w": (36, 42, 61), "W": (16, 41, 59),
    "^": (20, 28, 51), "x": (6, 8, 14), "l": (90, 28, 12), "f": (74, 36, 16),
    "A": (58, 49, 89),
    "+": (75, 58, 32), "/": (58, 63, 87), "p": (42, 47, 66), " ": (5, 7, 13),
}
_EDGE_COLORS: dict[str, tuple[int, int, int]] = {
    "#": (70, 82, 123), "R": (60, 63, 82), "T": (79, 125, 74), "O": (125, 140, 200),
    "o": (138, 106, 51), "n": (122, 92, 48), "w": (74, 84, 120), "x": (56, 64, 94),
    "l": (255, 122, 51), "f": (255, 154, 74), "A": (136, 120, 192),
    "+": (160, 124, 60), "/": (160, 124, 60), "p": (106, 114, 144),
}

#: Tiles that get a light WASH over the painted art, not merely an outline:
#: ground that stops a creature or hurts it. A painting can show a puddle that
#: is really deep water, or ice that reads as stone — those must be unmissable.
#: Ordinary difficult terrain (rubble, undergrowth) is deliberately NOT here:
#: the art shows it perfectly well and washing it all buries the picture.
_WASHED = frozenset({"~", "W", "^", "x", "l", "f", "i", "m", "%"})

#: Cut-out sprites, keyed by stored image id. Matting the SAME rubble on every
#: board redraw would be pure waste — the picture never changes once stored.
_CUTOUTS: dict = {}


def _cutout_cached(image_id, raw_bytes):
    """RGBA sprite with its background removed, or None if that isn't possible."""
    if image_id is None or not raw_bytes:
        return None
    if image_id in _CUTOUTS:
        return _CUTOUTS[image_id]
    try:
        # Through art.sprite_png, not cutout directly: the Activity fetches the
        # matted sprite over HTTP from that same function, and the two boards
        # have to be looking at the same picture.
        from .art import sprite_png
        cut = sprite_png(image_id, raw_bytes)
        out = Image.open(io.BytesIO(cut)).convert("RGBA") if cut else None
    except Exception as e:
        print(f"[vtt.png] cutout failed: {e}")
        out = None
    _CUTOUTS[image_id] = out
    return out


#: How much of a square a door panel fills ACROSS the wall it sits in. A door
#: is a plank in a wall, not a thing standing in a room — drawn square and
#: centred it reads as furniture parked in the middle of the floor, which is
#: exactly how the first pass looked.
_PANEL_THICKNESS = 0.46


def _paste_panel(img, d, sprite, px: int, py: int, cell: int, axis: str,
                 edge=None) -> None:
    """Draw an aperture as a panel lying IN its wall, not filling its square.

    ``axis`` is the direction the wall runs: ``"ew"`` puts the panel across the
    square horizontally, ``"ns"`` vertically. The jamb ticks at either end are
    drawn by us, never by the model — they are what tie the door to the wall,
    and they stay right even when the painting behind them wanders.
    """
    thick = max(3, int(round(cell * _PANEL_THICKNESS)))
    inset = (cell - thick) // 2
    if axis == "ns":
        box = (px + inset, py, px + inset + thick, py + cell)
        size = (thick, cell)
    else:
        box = (px, py + inset, px + cell, py + inset + thick)
        size = (cell, thick)

    if sprite is not None:
        sp = sprite
        if axis == "ns":
            # The sprite is drawn as a horizontal panel; stand it on end rather
            # than squashing it sideways, or the planks run the wrong way.
            sp = sp.rotate(90, expand=True)
        sp = sp.resize(size, Image.LANCZOS)
        img.paste(sp, (box[0], box[1]), sp)
    else:
        d.rectangle(box, fill=(96, 74, 41))

    color = edge or (160, 124, 60)
    d.rectangle(box, outline=(*color, 230), width=2)
    # Jambs: a short tick across the wall at each end of the panel.
    tick = max(2, cell // 8)
    if axis == "ns":
        for yy in (py, py + cell - 1):
            d.line([px + inset - tick, yy, px + inset + thick + tick, yy],
                   fill=(*color, 255), width=2)
    else:
        for xx in (px, px + cell - 1):
            d.line([xx, py + inset - tick, xx, py + inset + thick + tick],
                   fill=(*color, 255), width=2)


def _draw_labels(img, chips, font) -> None:
    """Name every object and every wreck, in small dark chips on the board.

    Composited in ONE overlay rather than drawn square by square, because the
    chips are translucent and the board beneath them is a painting — a solid
    box would punch a hole in the art for the sake of one word, and a per-chip
    composite would pay for the whole canvas each time.

    This is the walls-overlay argument applied to objects: the picture is
    allowed to be atmospheric, and the word is not a guess. A player who can
    read "door" on a square never has to wonder whether the model drew one.
    """
    if not chips:
        return
    pad = 3
    tmp = Image.new("RGBA", img.size, (0, 0, 0, 0))
    td = ImageDraw.Draw(tmp)
    for cx, base_y, text in chips:
        try:
            l, t, r, b = td.textbbox((0, 0), text, font=font)
        except Exception:                       # very old Pillow
            l = t = 0
            r, b = len(text) * 6, 10
        w, h = r - l, b - t
        x0 = int(cx - w / 2) - pad
        y0 = int(base_y) - h - 2 * pad
        td.rectangle((x0, y0, x0 + w + 2 * pad, y0 + h + 2 * pad),
                     fill=(9, 12, 21, 200), outline=(*_GOLD, 130), width=1)
        td.text((x0 + pad - l, y0 + pad - t), text, font=font,
                fill=(*_TEXT, 255))
    img.paste(Image.alpha_composite(img.convert("RGBA"), tmp).convert("RGB"),
              (0, 0))


_TEAM_COLORS = {
    "party": (79, 163, 255),
    "foe": (255, 90, 90),
    "neutral": (217, 178, 90),
}

_BG = (9, 12, 21)
_GRID = (128, 145, 166)
_TEXT = (219, 231, 232)
_DIM = (128, 145, 166)
_GOLD = (230, 188, 100)


def _font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:      # very old Pillow: unsized bitmap font
        return ImageFont.load_default()


def _monogram(name: str) -> str:
    words = (name or "?").split()
    if len(words) > 1 and words[-1].isdigit():
        return (words[0][:1] + words[-1]).upper()
    if len(words) > 1:
        return (words[0][:1] + words[-1][:1]).upper()
    return name[:2].upper()


def render_board_png(state: dict, *, cell: int = 46, margin: int = 22,
                     show_coords: bool = True, max_px: int = 1600,
                     image_lookup=None) -> bytes:
    """Render a scene ``state()`` dict to PNG bytes.

    ``cell`` is shrunk automatically so a very large board still fits inside
    ``max_px`` — Discord scales attachments down anyway, and an unreadable
    2500-px map helps nobody.

    ``image_lookup(image_id) -> bytes | None`` opts this renderer into the
    painted art: the battlemap is laid under the grid and any wreckage sprite
    is dropped on the square it belongs to. Without it — or with the GPU cold —
    the flat tile colours are drawn exactly as before, which is why the board
    is never broken by missing pictures.
    """
    w_sq = max(1, int(state.get("width", 1)))
    h_sq = max(1, int(state.get("height", 1)))
    head = 34
    cell = max(14, min(cell, (max_px - margin * 2) // max(w_sq, h_sq)))

    W = w_sq * cell + margin * 2
    H = h_sq * cell + margin * 2 + head
    img = Image.new("RGB", (W, H), _BG)
    d = ImageDraw.Draw(img, "RGBA")

    f_small = _font(max(9, cell // 4))
    f_tok = _font(max(10, cell // 3))
    f_head = _font(16)

    # ---- header ----
    name = str(state.get("name") or "Tactical Scene")
    rnd = state.get("round")
    kind = str(state.get("kind") or "").upper()
    d.text((margin, 9), name, font=f_head, fill=_GOLD)
    right = []
    if state.get("encounter_id") and rnd:
        right.append(f"ROUND {rnd}")
    right.append(f"{w_sq}x{h_sq} squares · {state.get('square_ft', 5)} ft each")
    if kind:
        right.insert(0, kind)
    d.text((margin, 24), "  ·  ".join(right), font=f_small, fill=_DIM)

    ox, oy = margin, margin + head

    def sx(x: int) -> int:
        return ox + x * cell

    def sy(y: int) -> int:
        return oy + y * cell

    # ---- terrain ----
    # The painted map goes UNDER everything when we have it; the flat tile
    # colours are the fallback, and they stay authoritative in the sense that
    # they are what the rules read. The picture is only ever a texture.
    painted = None
    if image_lookup is not None and state.get("background_image_id"):
        try:
            raw = image_lookup(state["background_image_id"])
            if raw:
                painted = Image.open(io.BytesIO(raw)).convert("RGB").resize(
                    (w_sq * cell, h_sq * cell), Image.LANCZOS)
        except Exception as e:
            print(f"[vtt.png] background unavailable: {e}")
    if painted is not None:
        img.paste(painted, (ox, oy))
        # The art is aligned to the grid, but a diffusion model cannot be told
        # to put a pillar on square 6,5 — that is exactly why the tile grid is
        # authoritative. So the mechanically significant tiles are TINTED back
        # over the picture: a wall the rules enforce has to be a wall the
        # players can see, even when the painting put it somewhere else. This
        # is the "walls-overlay" vtt/art.py's own docstring promises.
        # OUTLINES, not fills. The picture was generated FROM this grid, so it
        # already shows the room; a solid tint over every wall smothers the art
        # it is meant to annotate. What players need is a visible edge on the
        # squares the rules treat specially — and a light wash only where the
        # ground itself would stop or hurt them, which a painting can easily
        # fail to make obvious.
        terrain = state.get("terrain") or []
        for y in range(h_sq):
            row = terrain[y] if y < len(terrain) else ""
            for x in range(w_sq):
                code = row[x] if x < len(row) else "."
                box = [sx(x), sy(y), sx(x) + cell, sy(y) + cell]
                if code in _WASHED:
                    color = _TILE_COLORS.get(code, _TILE_COLORS["."])
                    d.rectangle(box, fill=(*color, 70))
                edge = _EDGE_COLORS.get(code)
                if edge:
                    d.rectangle(box, outline=(*edge, 150), width=1)
    else:
        terrain = state.get("terrain") or []
        for y in range(h_sq):
            row = terrain[y] if y < len(terrain) else ""
            for x in range(w_sq):
                code = row[x] if x < len(row) else "."
                color = _TILE_COLORS.get(code, _TILE_COLORS["."])
                d.rectangle([sx(x), sy(y), sx(x) + cell, sy(y) + cell], fill=color)
                edge = _EDGE_COLORS.get(code)
                if edge:
                    d.rectangle([sx(x), sy(y), sx(x) + cell, sy(y) + cell],
                                outline=edge, width=1)

    # ---- objects ----
    # Drawn from the GRID, on their own squares. This is the half that makes a
    # board legible: a pillar you can point at, and later recognise as the
    # rubble beside your feet. The painting cannot do it — a prompt cannot
    # place anything — so the sprites do.
    labels: dict[tuple[int, int], str] = {}
    for obj in state.get("objects") or []:
        x, y = int(obj.get("x", -1)), int(obj.get("y", -1))
        if not (0 <= x < w_sq and 0 <= y < h_sq):
            continue
        if obj.get("label"):
            labels[(x, y)] = str(obj["label"])
        axis = str(obj.get("axis") or "")
        if image_lookup is not None and obj.get("image_id"):
            try:
                raw = image_lookup(obj["image_id"])
            except Exception:
                raw = None
            cut = _cutout_cached(obj["image_id"], raw) if raw else None
            if cut is not None:
                if axis:
                    _paste_panel(img, d, cut, sx(x), sy(y), cell, axis,
                                 _EDGE_COLORS.get(str(obj.get("code") or "")))
                else:
                    sp = cut.resize((cell, cell), Image.LANCZOS)
                    img.paste(sp, (sx(x), sy(y)), sp)
                continue
        if axis:
            # No sprite yet, but a door is worth drawing without one: a bar
            # across its opening says more than the square's colour does.
            _paste_panel(img, d, None, sx(x), sy(y), cell, axis,
                         _EDGE_COLORS.get(str(obj.get("code") or "")))
        # Otherwise the tile colour and edge already drew it — nothing to do.

    # ---- wreckage ----
    # Painted over whatever is beneath it, on the square that was broken. When
    # no sprite has been drawn yet the square is still correct underneath — the
    # tile already changed — so this is decoration arriving late, never state.
    for deb in state.get("debris") or []:
        x, y = int(deb.get("x", -1)), int(deb.get("y", -1))
        if not (0 <= x < w_sq and 0 <= y < h_sq):
            continue
        if deb.get("label"):
            labels[(x, y)] = str(deb["label"])
        # Scuff the square before anything is drawn on it. Stone rubble on a
        # flagstone floor is the low-contrast case a sprite can lose — same
        # material, same light — and the square has to read as CHANGED whether
        # or not the picture cooperated. Deterministic, so it can't fail.
        scuff = img.crop((sx(x), sy(y), sx(x) + cell, sy(y) + cell))
        img.paste(Image.blend(scuff, Image.new("RGB", scuff.size, (38, 30, 22)),
                              0.3), (sx(x), sy(y)))
        sprite = None
        if image_lookup is not None and deb.get("image_id"):
            try:
                raw = image_lookup(deb["image_id"])
                if raw:
                    sprite = Image.open(io.BytesIO(raw)).convert("RGBA").resize(
                        (cell, cell), Image.LANCZOS)
            except Exception as e:
                print(f"[vtt.png] debris sprite unavailable: {e}")
        if sprite is not None:
            cut = _cutout_cached(deb.get("image_id"), raw)
            if cut is not None:
                img.paste(cut.resize((cell, cell), Image.LANCZOS),
                          (sx(x), sy(y)),
                          cut.resize((cell, cell), Image.LANCZOS))
            else:
                # No background removal available: feather the square instead.
                # A diffusion sprite arrives with its own background, and a hard
                # paste reads as a picture stuck on the map rather than debris
                # lying on it. Softer than a cut-out, but never broken.
                mask = Image.new("L", (cell, cell), 0)
                md = ImageDraw.Draw(mask)
                steps = max(6, cell // 4)
                for i in range(steps):
                    t = i / float(steps)
                    inset = int(t * cell * 0.5)
                    md.ellipse([inset, inset, cell - inset, cell - inset],
                               fill=int(255 * min(1.0, (t + 0.15) * 1.5)))
                img.paste(sprite.convert("RGB"), (sx(x), sy(y)), mask)
        else:
            # No picture: mark the square as wreckage so the change still reads.
            d.rectangle([sx(x), sy(y), sx(x) + cell, sy(y) + cell],
                        fill=(90, 78, 62, 190))
        d.rectangle([sx(x), sy(y), sx(x) + cell, sy(y) + cell],
                    outline=(210, 150, 90, 200), width=2)

    # ---- labels ----
    # Under the tokens, over everything else. A sprite the model drew is only
    # as legible as the model was clear; the word is not a guess. This is the
    # same argument as the walls-overlay — the code says what a square IS, and
    # the picture is welcome to be atmospheric about it.
    # One chip per RUN, not per square. Two crates side by side are two squares
    # and one fact; labelling both prints "broken crates" over "broken crat…"
    # and costs the player the word it was there to give them. A label is
    # dropped only when the square west or north of it already says the same
    # thing, so an isolated object is never left unnamed.
    chips = []
    for (x, y), text in sorted(labels.items()):
        if labels.get((x - 1, y)) == text or labels.get((x, y - 1)) == text:
            continue
        # A chip is wider than the square it names, so two DIFFERENT labels
        # side by side overlap even after the run-dedupe above. Stagger them:
        # a labelled neighbour to the west pushes this one to the top of its
        # square instead of the bottom.
        top = (x - 1, y) in labels
        chips.append((sx(x) + cell // 2,
                      sy(y) + (12 if top else cell - 2), text))
    _draw_labels(img, chips, _font(max(9, cell // 5)))

    # ---- effects ----
    for eff in state.get("effects") or []:
        col = _hex(eff.get("color")) or (168, 107, 255)
        soft = eff.get("kind") in ("aura", "light")
        # A marker is an annotation — outline it, never paint over the ground.
        marker = eff.get("kind") == "marker"
        alpha = 0 if marker else int(
            255 * min(0.5, max(0.06, float(eff.get("opacity", 0.35))
                               * (0.5 if soft else 1.0))))
        for sq in (eff.get("squares") or []) if alpha else []:
            x, y = int(sq[0]), int(sq[1])
            if 0 <= x < w_sq and 0 <= y < h_sq:
                d.rectangle([sx(x), sy(y), sx(x) + cell, sy(y) + cell],
                            fill=col + (alpha,))
        # Outline the footprint so overlapping areas stay readable.
        inside = {(int(a), int(b)) for a, b in (eff.get("squares") or [])}
        for x, y in inside:
            if (x, y - 1) not in inside:
                d.line([sx(x), sy(y), sx(x) + cell, sy(y)], fill=col + (220,), width=2)
            if (x, y + 1) not in inside:
                d.line([sx(x), sy(y) + cell, sx(x) + cell, sy(y) + cell],
                       fill=col + (220,), width=2)
            if (x - 1, y) not in inside:
                d.line([sx(x), sy(y), sx(x), sy(y) + cell], fill=col + (220,), width=2)
            if (x + 1, y) not in inside:
                d.line([sx(x) + cell, sy(y), sx(x) + cell, sy(y) + cell],
                       fill=col + (220,), width=2)

    # ---- grid ----
    for x in range(w_sq + 1):
        d.line([sx(x), oy, sx(x), oy + h_sq * cell], fill=_GRID + (70,), width=1)
    for y in range(h_sq + 1):
        d.line([ox, sy(y), ox + w_sq * cell, sy(y)], fill=_GRID + (70,), width=1)

    # ---- fog, in two tiers ----
    # Never seen is black. Seen once but not under anyone's eye right now is a
    # cold veil — you remember the room, you are not watching it. Only what is
    # in live line of sight is left clear. One tier alone gets a door wrong in
    # both directions: with memory only, closing a door behind you changes
    # nothing; with sight only, the party forgets the map every time they turn
    # around.
    fog = state.get("fog")
    sight = state.get("sight")
    if not fog:
        # No fog: light still decides what can be fought, and nothing else on
        # the board shows it. Drawn only in this branch — with fog on, the veil
        # above already carries it, and stacking the two buries the art.
        for y, lrow in enumerate(state.get("light") or []):
            if y >= h_sq:
                break
            for x, lv in enumerate(lrow[:w_sq]):
                if lv == "b":
                    continue
                box = (sx(x), sy(y), sx(x) + cell, sy(y) + cell)
                shade = img.crop(box)
                img.paste(Image.blend(shade,
                                      Image.new("RGB", shade.size, (6, 9, 20)),
                                      0.32 if lv == "d" else 0.66),
                          (sx(x), sy(y)))
    if fog:
        for y in range(h_sq):
            row = fog[y] if y < len(fog) else ""
            lit_row = (sight[y] if sight and y < len(sight) else None)
            for x in range(w_sq):
                seen = (row[x] if x < len(row) else "0") == "1"
                lit = lit_row is not None and x < len(lit_row) and lit_row[x] == "1"
                box = (sx(x), sy(y), sx(x) + cell, sy(y) + cell)
                if not seen:
                    # Opaque, not merely dark. At 88% the walls of an unexplored
                    # room showed faintly through, which hands the party the
                    # shape of the dungeon they haven't walked yet.
                    d.rectangle(list(box), fill=(4, 6, 12, 255))
                elif not lit:
                    veil = img.crop(box)
                    img.paste(Image.blend(veil, Image.new("RGB", veil.size,
                                                          (7, 11, 22)), 0.62),
                              (sx(x), sy(y)))

    # ---- coordinate ruler ----
    if show_coords and cell >= 18:
        step = 1 if cell >= 26 else 2
        for x in range(0, w_sq, step):
            d.text((sx(x) + 2, oy - 11), str(x), font=f_small, fill=_DIM)
        for y in range(0, h_sq, step):
            d.text((ox - margin + 3, sy(y) + 2), str(y), font=f_small, fill=_DIM)

    # ---- tokens ----
    # A creature standing in the dark is not on the board. Drawing an ogre in a
    # room nobody can see would give the picture away more completely than any
    # amount of fog hides it — the party's own tokens are exempt, since they
    # are what the sight is measured FROM.
    def in_sight(t) -> bool:
        if not sight or t.get("team") == "party":
            return True
        ty, tx = int(t.get("y", -1)), int(t.get("x", -1))
        n = max(1, int(t.get("squares", 1)))
        for yy in range(ty, ty + n):
            for xx in range(tx, tx + n):
                r = sight[yy] if 0 <= yy < len(sight) else ""
                if xx < len(r) and r[xx] == "1":
                    return True
        return False

    current = state.get("current_token_id")
    for t in state.get("tokens") or []:
        if t.get("hidden") or not in_sight(t):
            continue
        n = max(1, int(t.get("squares", 1)))
        x0, y0 = sx(int(t["x"])), sy(int(t["y"]))
        span = cell * n
        pad = max(2, span // 10)
        box = [x0 + pad, y0 + pad, x0 + span - pad, y0 + span - pad]
        team = _TEAM_COLORS.get(t.get("team", "foe"), _TEAM_COLORS["foe"])
        if t.get("defeated"):
            team = (110, 110, 118)
        d.ellipse(box, fill=(18, 24, 42), outline=team,
                  width=max(2, span // 16))
        if current is not None and t.get("id") == current:
            # Whose turn it is gets a gold halo.
            d.ellipse([box[0] - 3, box[1] - 3, box[2] + 3, box[3] + 3],
                      outline=_GOLD, width=2)
        label = "X" if t.get("defeated") else _monogram(str(t.get("name", "?")))
        tw = d.textlength(label, font=f_tok)
        d.text((x0 + span / 2 - tw / 2, y0 + span / 2 - cell // 5),
               label, font=f_tok, fill=_TEXT)

    # ---- names under the tokens (only when there's room) ----
    if cell >= 26:
        for t in state.get("tokens") or []:
            if t.get("hidden") or not in_sight(t):
                continue
            n = max(1, int(t.get("squares", 1)))
            nm = str(t.get("name", ""))[:12]
            tw = d.textlength(nm, font=f_small)
            x0 = sx(int(t["x"])) + (cell * n) / 2 - tw / 2
            y0 = sy(int(t["y"])) + cell * n - 9
            d.rectangle([x0 - 2, y0 - 1, x0 + tw + 2, y0 + 10], fill=(6, 9, 17, 210))
            d.text((x0, y0), nm, font=f_small,
                   fill=_TEXT if not t.get("defeated") else _DIM)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _hex(value: Optional[str]) -> Optional[tuple[int, int, int]]:
    if not value or not isinstance(value, str) or not value.startswith("#"):
        return None
    v = value.lstrip("#")
    if len(v) == 3:
        v = "".join(c * 2 for c in v)
    if len(v) != 6:
        return None
    try:
        return (int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16))
    except ValueError:
        return None
