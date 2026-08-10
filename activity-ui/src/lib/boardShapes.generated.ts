/** GENERATED FILE — do not edit.
 *
 *  Written by scripts/gen_board_shapes.py from the Python side, which is
 *  authoritative. `scripts/iso_alignment_check.py` fails if this file is stale.
 *
 *  These are the shapes the isometric board is built from, and they matter on
 *  both sides of the wire: vtt/isocam.py rasterizes them into the depth map the
 *  painted layer is conditioned on, and vttScene3d.ts builds them as the
 *  geometry the player looks at. If the two disagree, the painting lands on
 *  furniture that is not there — and nothing in either program looks wrong.
 */


/** How thick a wall is DRAWN, as a fraction of its square.
 *  The square stays fully solid in the RULES; this only stops the
 *  wall's top face swallowing the room at this camera angle. */
export const WALL_THICKNESS = 0.34;

/** Radius of a pillar or tree trunk, as a fraction of its square. */
export const PILLAR_RADIUS = 0.32;

/** Codes that are the BUILDING rather than something standing in it. */
export const STRUCTURE_CODES: ReadonlySet<string> = new Set(["#", "R"]);

/** How tall each tile stands, in feet. 0 is floor you walk on. */
export const TILE_HEIGHT_FT: Record<string, number> = {
  "#": 10,
  "+": 8,
  A: 4,
  O: 10,
  R: 10,
  T: 12,
  n: 3,
  o: 4,
  p: 8,
  w: 3,
};

/** What each object is SHAPED like, as parts of its square:
 *  [x0, x1, z0, z1, yFrom, yTo] — fractions of the square for x/z,
 *  of the tile's standing height for y. */
export const OBJECT_PARTS: Record<string, readonly (readonly [
  number, number, number, number, number, number])[]> = {
  A: [
      [0.1, 0.9, 0.3, 0.7, 0, 0.72],
      [0.06, 0.94, 0.26, 0.74, 0.72, 1]],
  n: [
      [0.12, 0.22, 0.16, 0.26, 0, 0.72],
      [0.78, 0.88, 0.16, 0.26, 0, 0.72],
      [0.12, 0.22, 0.74, 0.84, 0, 0.72],
      [0.78, 0.88, 0.74, 0.84, 0, 0.72],
      [0.06, 0.94, 0.1, 0.9, 0.72, 1]],
  o: [
      [0.08, 0.58, 0.1, 0.62, 0, 0.62],
      [0.46, 0.92, 0.36, 0.9, 0, 0.48],
      [0.16, 0.56, 0.18, 0.58, 0.62, 1]],
  w: [
      [0.18, 0.82, 0, 1, 0, 0.8],
      [0.1, 0.9, 0, 1, 0.8, 1]],
};
