/** The isometric camera — one definition, and the arithmetic that follows it.
 *
 *  ## Why this file is load-bearing
 *
 *  The board is drawn from geometry in the browser, and (once the painted layer
 *  lands) a matching depth map is rasterized on the SERVER so a depth ControlNet
 *  can paint a picture that lines up with that geometry. Those are two programs
 *  in two languages projecting the same room, and if their cameras disagree by
 *  a degree the painting no longer sits on the walls — every shadow lands beside
 *  the thing casting it.
 *
 *  So the camera is a handful of constants and a page of arithmetic, kept short
 *  enough to verify by eye, and `vtt/isocam.py` is its mirror. **Change one and
 *  you must change the other**, then re-run the alignment check.
 *
 *  ## Why it can be this simple
 *
 *  The camera is ORTHOGRAPHIC. That buys three things:
 *
 *  * the projection is a plain affine map — no perspective divide, so it
 *    inverts in closed form and picking is arithmetic rather than a ray march;
 *  * pan and zoom are a translate-and-scale of the projected image, which is
 *    exactly what `View`'s `scale`/`ox`/`oy` already mean;
 *  * a painting baked at one framing stays aligned at every other FRAMING.
 *
 *  ## Turning it
 *
 *  It used never to turn either, and the note here said that offering rotation
 *  would cost all three at once. Two of those three survive, and the third is
 *  the real price — worth stating exactly, because it is the whole design.
 *
 *  Yaw is now a PARAMETER (`project(x, y, z, yawDeg)`), defaulting to
 *  `YAW_DEG`. For any FIXED yaw the projection is still a plain affine map, so
 *  it still inverts in closed form and picking is still arithmetic; pan and
 *  zoom are still a translate-and-scale. Nothing about turning the camera costs
 *  those, because the camera is orthographic and yaw only chooses which affine
 *  map you are using. The bases are memoised per angle, so a frame at 137
 *  degrees is exactly as cheap as a frame at 45.
 *
 *  What rotation genuinely costs is the PAINTING. A picture baked against a
 *  depth map rasterized at one yaw is a photograph of the room from one place,
 *  and there is no transform that turns it into a photograph from another. So
 *  the server keeps working at exactly one angle — `YAW_DEG`, the CANONICAL
 *  yaw, which is what `vtt/isocam.py` mirrors and what the alignment gate
 *  compares — and the client fades the painting out as it turns away from it.
 *  Off-axis you are looking at the geometry, which is why the surfaces had to
 *  learn to answer to light before this was worth offering.
 *
 *  ## Space
 *
 *  World units are SQUARES: grid `(x, y)` is world `(x, ·, y)`, X running east
 *  and Z running south, Y up. Feet convert through the board's own `square_ft`,
 *  which is why height, elevation and storey bases all arrive as feet and
 *  become units here rather than at each call site. */

/** The CANONICAL rotation about the vertical axis, and the only one the server
 *  ever works at. 45° puts the board corner-on, so both wall faces of a corner
 *  are visible and neither runs parallel to the screen edge.
 *
 *  Every baked painting, every depth map and every alignment check is at this
 *  angle. A viewer may turn the camera anywhere; nothing that has to agree with
 *  the server may. */
export const YAW_DEG = 45;

/** How far from canonical the camera may be before the painted layer is gone.
 *
 *  A painting is a photograph of the room from ONE place and no transform makes
 *  it a photograph from another, so it has to go — but not abruptly, because a
 *  picture that vanishes at a threshold reads as a bug and a picture that fades
 *  reads as the room turning. Full strength within `PAINT_HOLD_DEG`, gone by
 *  `PAINT_FADE_DEG`. */
export const PAINT_HOLD_DEG = 3;
export const PAINT_FADE_DEG = 16;

/** How much a viewer may turn per notch of the control. A whole quarter is the
 *  useful unit on a square grid — it swaps which two faces of every corner you
 *  can see — and free rotation in between is what the arithmetic already
 *  supports. */
export const YAW_STEP_DEG = 15;

/** How far the camera tilts down. Higher shows more floor (positions are easier
 *  to read); lower shows more of the walls' faces (more dramatic, and more of
 *  the board hidden behind them). 40° favours the floor, because this is a
 *  board you have to be able to count squares on. */
export const PITCH_DEG = 40;

const PITCH = (PITCH_DEG * Math.PI) / 180;
const SIN_P = Math.sin(PITCH);
const COS_P = Math.cos(PITCH);

/** Everything that depends on the angle the camera is turned to. */
export interface Basis {
  yawDeg: number;
  sinY: number;
  cosY: number;
  /** Camera basis, world-space. Derived by pitching down about X then yawing
   *  about Y, which is the order that keeps the horizon level (no roll). */
  RIGHT: readonly [number, number, number];
  UP: readonly [number, number, number];
  FORWARD: readonly [number, number, number];
  /** The ray from any point back TOWARD the camera, per unit of HORIZONTAL
   *  travel: how far it moves across the floor in x and z, and how far it
   *  climbs. `rayRise` is tan(pitch) and so does not depend on yaw at all —
   *  turning the camera changes which way "toward the lens" points across the
   *  floor, and not how steeply it climbs. */
  rayX: number;
  rayZ: number;
  rayRise: number;
}

const _BASES = new Map<number, Basis>();

/** The basis for one yaw, memoised.
 *
 *  Memoised on the rounded angle rather than recomputed per call because
 *  `occludedAt` asks per token per frame and `project` asks per vertex of every
 *  decal; six trig calls each is real work for an answer that changes only when
 *  somebody turns the camera. A tenth of a degree is far finer than any control
 *  offers, so the key can never blow up. */
export function basis(yawDeg: number = YAW_DEG): Basis {
  const key = Math.round(yawDeg * 10) / 10;
  const got = _BASES.get(key);
  if (got) return got;
  const y = (key * Math.PI) / 180;
  const sinY = Math.sin(y);
  const cosY = Math.cos(y);
  const FORWARD: readonly [number, number, number] =
    [-sinY * COS_P, -SIN_P, -cosY * COS_P];
  const run = Math.hypot(FORWARD[0], FORWARD[2]);
  const made: Basis = {
    yawDeg: key, sinY, cosY,
    RIGHT: [cosY, 0, -sinY],
    UP: [-sinY * SIN_P, COS_P, -cosY * SIN_P],
    FORWARD,
    rayX: -FORWARD[0] / run,
    rayZ: -FORWARD[2] / run,
    rayRise: -FORWARD[1] / run,
  };
  _BASES.set(key, made);
  return made;
}

const CANON = basis(YAW_DEG);
const SIN_Y = CANON.sinY;
const COS_Y = CANON.cosY;

/** The canonical basis, for everything that must agree with the server. */
export const RIGHT = CANON.RIGHT;
export const UP = CANON.UP;
export const FORWARD = CANON.FORWARD;

/** The ray from any point back TOWARD the camera, per unit of HORIZONTAL
 *  travel: how far it moves across the floor in x and z, and how far it climbs.
 *
 *  A constant for any one angle, because the camera does not MOVE — which is
 *  what makes "is a wall standing in front of this creature" a march over the
 *  grid rather than a depth-buffer readback. Turning the camera picks a
 *  different constant (`basis(yaw).rayX/rayZ`), and the march is the same
 *  march; the climb, `rayRise`, is tan(pitch) and does not depend on yaw. `RAY_RISE` is tan(pitch): at 40 degrees
 *  the ray gains 0.84 units of height for every unit it crosses the floor, so a
 *  ten-foot wall hides a figure one square behind it and nothing two squares
 *  behind it. See `occludedAt` in boardView.ts, the only caller.
 *
 *  Not mirrored in vtt/isocam.py, and it should not be: the server rasterizes a
 *  depth map of the ROOM, and creatures are not in it. */
export const RAY_X = CANON.rayX;
export const RAY_Z = CANON.rayZ;
export const RAY_RISE = CANON.rayRise;

/** A world unit of HEIGHT is this many units of screen — cos(pitch).
 *
 *  The other half of the same foreshortening: a token's DOM box is as tall in
 *  pixels as its footprint is wide, so the creature it draws stands
 *  `1 / VERTICAL_SQUEEZE` world units tall. Anything asking how much of a
 *  figure a wall hides needs that number. */
export const VERTICAL_SQUEEZE = COS_P;

/** Breathing room around the board in the CANONICAL framing, in squares.
 *
 *  The painted layer is baked to the projected bounding box plus this margin,
 *  and the client lays it back over exactly that rectangle. Mirrored by
 *  `FRAME_PAD_SQUARES` in vtt/isocam.py — a disagreement here slides the whole
 *  painting off the geometry by a constant, which is the most convincing kind
 *  of wrong because everything still looks plausible. */
export const FRAME_PAD_SQUARES = 0.25;

/** A point on the projection plane, in world units, before zoom or pan.
 *  `y` grows DOWNWARD to match screen convention. */
export interface Projected { x: number; y: number; depth: number }

/** Project a world point. `depth` grows with distance from the camera and is a
 *  sort key only — orthographic projection makes the absolute value arbitrary,
 *  but the ordering is exact. */
export function project(wx: number, wy: number, wz: number,
                        yawDeg: number = YAW_DEG): Projected {
  const b = yawDeg === YAW_DEG ? CANON : basis(yawDeg);
  return {
    x: wx * b.cosY - wz * b.sinY,
    // Negated because UP is up and screens count downward.
    y: -(wx * b.UP[0] + wy * b.UP[1] + wz * b.UP[2]),
    depth: wx * b.FORWARD[0] + wy * b.FORWARD[1] + wz * b.FORWARD[2],
  };
}

/** Invert `project` onto a horizontal plane at height `wy`.
 *
 *  Two equations, two unknowns — solved with Cramer's rule, whose determinant
 *  is `sin(pitch)`. That is the whole reason a pitch of 0 is forbidden: looking
 *  along the ground, every square on a line projects to the same pixel and
 *  there is no answer to give. */
export function unproject(sx: number, sy: number, wy: number,
                          yawDeg: number = YAW_DEG): [number, number] {
  const b = yawDeg === YAW_DEG ? CANON : basis(yawDeg);
  const rhs = sy + wy * COS_P;      // fold the known height back out
  const det = SIN_P;
  const wx = (sx * b.cosY * SIN_P + b.sinY * rhs) / det;
  const wz = (b.cosY * rhs - b.sinY * SIN_P * sx) / det;
  return [wx, wz];
}

/** The projected bounding box of a board `w x h` squares whose tallest
 *  structure stands `tallest` units above its floor.
 *
 *  Every extreme of an axis-aligned box lands on one of its eight corners under
 *  an affine map, so checking the corners is exact rather than a safe guess. */
export function boundsOf(w: number, h: number, tallest: number, baseY = 0,
                         yawDeg: number = YAW_DEG):
    { minX: number; maxX: number; minY: number; maxY: number } {
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  for (const wx of [0, w]) {
    for (const wz of [0, h]) {
      for (const wy of [baseY, baseY + tallest]) {
        const p = project(wx, wy, wz, yawDeg);
        if (p.x < minX) minX = p.x;
        if (p.x > maxX) maxX = p.x;
        if (p.y < minY) minY = p.y;
        if (p.y > maxY) maxY = p.y;
      }
    }
  }
  return { minX, maxX, minY, maxY };
}


/** How much of the painted layer survives at this angle, 0..1.
 *
 *  The painting is baked against a depth map rasterized at `YAW_DEG` — a
 *  photograph of the room from one place — and no transform makes it a
 *  photograph from another, so turning away from canonical has to give it up.
 *  A fade rather than a switch: a picture that vanishes at a threshold reads as
 *  a bug, and one that dissolves reads as the room turning under you.
 *
 *  Symmetric about the canonical angle, and measured the SHORT way round, so
 *  359 degrees is one degree off canonical rather than 314. */
export function paintOpacity(yawDeg: number): number {
  // ((d % 360) + 540) % 360 - 180 is the SIGNED short way round; its magnitude
  // is how far off canonical we are, and 359 degrees is one degree off rather
  // than 314.
  const off = Math.abs((((yawDeg - YAW_DEG) % 360) + 540) % 360 - 180);
  if (off <= PAINT_HOLD_DEG) return 1;
  if (off >= PAINT_FADE_DEG) return 0;
  return 1 - (off - PAINT_HOLD_DEG) / (PAINT_FADE_DEG - PAINT_HOLD_DEG);
}

/** Normalize an angle to [0, 360). */
export function wrapYaw(yawDeg: number): number {
  return ((yawDeg % 360) + 360) % 360;
}
