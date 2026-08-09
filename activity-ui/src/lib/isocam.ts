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
 *  The camera is ORTHOGRAPHIC and never rotates. That buys three things:
 *
 *  * the projection is a plain affine map — no perspective divide, so it
 *    inverts in closed form and picking is arithmetic rather than a ray march;
 *  * pan and zoom are a translate-and-scale of the projected image, which is
 *    exactly what `View`'s `scale`/`ox`/`oy` already mean, so the camera needs
 *    no state of its own;
 *  * a painting baked at one framing stays aligned at every other framing.
 *
 *  Offering rotation would cost all three at once.
 *
 *  ## Space
 *
 *  World units are SQUARES: grid `(x, y)` is world `(x, ·, y)`, X running east
 *  and Z running south, Y up. Feet convert through the board's own `square_ft`,
 *  which is why height, elevation and storey bases all arrive as feet and
 *  become units here rather than at each call site. */

/** Rotation about the vertical axis. 45° puts the board corner-on, so both wall
 *  faces of a corner are visible and neither runs parallel to the screen edge. */
export const YAW_DEG = 45;

/** How far the camera tilts down. Higher shows more floor (positions are easier
 *  to read); lower shows more of the walls' faces (more dramatic, and more of
 *  the board hidden behind them). 40° favours the floor, because this is a
 *  board you have to be able to count squares on. */
export const PITCH_DEG = 40;

const YAW = (YAW_DEG * Math.PI) / 180;
const PITCH = (PITCH_DEG * Math.PI) / 180;

const SIN_Y = Math.sin(YAW);
const COS_Y = Math.cos(YAW);
const SIN_P = Math.sin(PITCH);
const COS_P = Math.cos(PITCH);

/** Camera basis, world-space. Derived by pitching down about X then yawing
 *  about Y, which is the order that keeps the horizon level (no roll). */
export const RIGHT: readonly [number, number, number] = [COS_Y, 0, -SIN_Y];
export const UP: readonly [number, number, number] = [-SIN_Y * SIN_P, COS_P, -COS_Y * SIN_P];
export const FORWARD: readonly [number, number, number] = [-SIN_Y * COS_P, -SIN_P, -COS_Y * COS_P];

/** A point on the projection plane, in world units, before zoom or pan.
 *  `y` grows DOWNWARD to match screen convention. */
export interface Projected { x: number; y: number; depth: number }

/** Project a world point. `depth` grows with distance from the camera and is a
 *  sort key only — orthographic projection makes the absolute value arbitrary,
 *  but the ordering is exact. */
export function project(wx: number, wy: number, wz: number): Projected {
  return {
    x: wx * COS_Y - wz * SIN_Y,
    // Negated because UP is up and screens count downward.
    y: -(wx * UP[0] + wy * UP[1] + wz * UP[2]),
    depth: wx * FORWARD[0] + wy * FORWARD[1] + wz * FORWARD[2],
  };
}

/** Invert `project` onto a horizontal plane at height `wy`.
 *
 *  Two equations, two unknowns — solved with Cramer's rule, whose determinant
 *  is `sin(pitch)`. That is the whole reason a pitch of 0 is forbidden: looking
 *  along the ground, every square on a line projects to the same pixel and
 *  there is no answer to give. */
export function unproject(sx: number, sy: number, wy: number): [number, number] {
  const rhs = sy + wy * COS_P;      // fold the known height back out
  const det = SIN_P;
  const wx = (sx * COS_Y * SIN_P + SIN_Y * rhs) / det;
  const wz = (COS_Y * rhs - SIN_Y * SIN_P * sx) / det;
  return [wx, wz];
}

/** The projected bounding box of a board `w x h` squares whose tallest
 *  structure stands `tallest` units above its floor.
 *
 *  Every extreme of an axis-aligned box lands on one of its eight corners under
 *  an affine map, so checking the corners is exact rather than a safe guess. */
export function boundsOf(w: number, h: number, tallest: number, baseY = 0):
    { minX: number; maxX: number; minY: number; maxY: number } {
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  for (const wx of [0, w]) {
    for (const wz of [0, h]) {
      for (const wy of [baseY, baseY + tallest]) {
        const p = project(wx, wy, wz);
        if (p.x < minX) minX = p.x;
        if (p.x > maxX) maxX = p.x;
        if (p.y < minY) minY = p.y;
        if (p.y > maxY) maxY = p.y;
      }
    }
  }
  return { minX, maxX, minY, maxY };
}
