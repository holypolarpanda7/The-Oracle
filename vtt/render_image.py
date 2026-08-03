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

    # ---- wreckage ----
    # Painted over whatever is beneath it, on the square that was broken. When
    # no sprite has been drawn yet the square is still correct underneath — the
    # tile already changed — so this is decoration arriving late, never state.
    for deb in state.get("debris") or []:
        x, y = int(deb.get("x", -1)), int(deb.get("y", -1))
        if not (0 <= x < w_sq and 0 <= y < h_sq):
            continue
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
            # Feathered, not pasted flat. A diffusion sprite arrives with its
            # own background, so a hard square reads as a picture stuck on the
            # map; fading it toward the edges makes it debris LYING on the
            # floor. Cheap, and it needs no background-removal dependency.
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

    # ---- fog ----
    fog = state.get("fog")
    if fog:
        for y in range(h_sq):
            row = fog[y] if y < len(fog) else ""
            for x in range(w_sq):
                if (row[x] if x < len(row) else "0") != "1":
                    d.rectangle([sx(x), sy(y), sx(x) + cell, sy(y) + cell],
                                fill=(4, 6, 12, 225))

    # ---- coordinate ruler ----
    if show_coords and cell >= 18:
        step = 1 if cell >= 26 else 2
        for x in range(0, w_sq, step):
            d.text((sx(x) + 2, oy - 11), str(x), font=f_small, fill=_DIM)
        for y in range(0, h_sq, step):
            d.text((ox - margin + 3, sy(y) + 2), str(y), font=f_small, fill=_DIM)

    # ---- tokens ----
    current = state.get("current_token_id")
    for t in state.get("tokens") or []:
        if t.get("hidden"):
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
            if t.get("hidden"):
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
