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

/** How much a tile's DRAWN height may wander, as a fraction.
 *  Never applied where the RULES quote a height — see heightScale. */
export const HEIGHT_JITTER = 0.12;

/** How tall each tile SCREENS a creature, in feet, per the rules.
 *  Non-zero means the height is an ANSWER a player reads off the
 *  board, so it must be drawn exactly and never jittered. */
export const COVER_HEIGHT_FT: Record<string, number> = {
  A: 4,
  n: 3,
  o: 4,
  w: 3,
};

/** How thick a platform is, in feet, where its floor meets a hole.
 *  Without it an island is a paper cut-out hanging in nothing. */
export const SKIRT_FT = 8;

/** Codes that are a HOLE, not ground — nothing is drawn on them.
 *  Open sky is air and a chasm is the absence of floor; drawing
 *  either as a surface invents ground the rules say you fall
 *  through. */
export const HOLE_CODES: ReadonlySet<string> = new Set([" ", "^", "x"]);

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

/** What each object is SHAPED like: one or more ARRANGEMENTS, each a
 *  list of parts [x0, x1, z0, z1, yFrom, yTo] — fractions of the
 *  square for x/z, of the tile's standing height for y. Which one a
 *  square uses comes from variantOf, so the same square always picks
 *  the same arrangement on both sides of the wire. */
export const OBJECT_VARIANTS: Record<string, readonly (readonly (readonly [
  number, number, number, number, number, number])[])[]> = {
  A: [
    [[0.1, 0.9, 0.3, 0.7, 0, 0.72], [0.06, 0.94, 0.26, 0.74, 0.72, 1]],
    [[0.14, 0.86, 0.28, 0.72, 0, 0.62], [0.1, 0.62, 0.24, 0.76, 0.62, 0.94], [0.66, 0.96, 0.3, 0.7, 0.62, 0.82]]],
  n: [
    [[0.12, 0.22, 0.16, 0.26, 0, 0.72], [0.78, 0.88, 0.16, 0.26, 0, 0.72], [0.12, 0.22, 0.74, 0.84, 0, 0.72], [0.78, 0.88, 0.74, 0.84, 0, 0.72], [0.06, 0.94, 0.1, 0.9, 0.72, 1]],
    [[0.1, 0.24, 0.12, 0.88, 0, 1], [0.24, 0.34, 0.2, 0.3, 0.62, 0.78], [0.24, 0.34, 0.7, 0.8, 0.62, 0.78]]],
  o: [
    [[0.08, 0.58, 0.1, 0.62, 0, 0.62], [0.46, 0.92, 0.36, 0.9, 0, 0.48], [0.16, 0.56, 0.18, 0.58, 0.62, 1]],
    [[0.12, 0.66, 0.14, 0.7, 0, 1], [0.62, 0.94, 0.52, 0.92, 0, 0.54]],
    [[0.1, 0.52, 0.2, 0.64, 0, 0.7], [0.54, 0.9, 0.14, 0.56, 0, 0.86], [0.34, 0.74, 0.6, 0.94, 0, 0.44]]],
  w: [
    [[0.18, 0.82, 0, 1, 0, 0.8], [0.1, 0.9, 0, 1, 0.8, 1]]],
};

/** Scenery: drawn by every view, honoured by none of the rules.
 *  kind -> [heightFt, parts]. Capped below the lowest cover height
 *  in the tile table, so nothing decorative can ever be mistaken for
 *  something to crouch behind — see vtt/decor.py. */
export const MAX_DECOR_HEIGHT_FT = 2;
export const DECOR_KINDS: Record<string, readonly [number,
  readonly (readonly [number, number, number, number, number, number])[]]> = {
  bones: [0.5, [[0.3, 0.7, 0.42, 0.52, 0, 0.5], [0.38, 0.48, 0.3, 0.7, 0, 0.4], [0.55, 0.72, 0.55, 0.68, 0, 0.7]]],
  brazier: [1.9, [[0.42, 0.58, 0.42, 0.58, 0, 0.62], [0.32, 0.68, 0.32, 0.68, 0.62, 1]]],
  roots: [0.6, [[0.05, 0.95, 0.4, 0.52, 0, 0.5], [0.36, 0.48, 0.05, 0.95, 0, 0.7]]],
  rug: [0.08, [[0.1, 0.9, 0.18, 0.82, 0, 1]]],
  sack: [1.6, [[0.3, 0.66, 0.32, 0.68, 0, 0.78], [0.34, 0.6, 0.36, 0.62, 0.78, 1]]],
  shards: [0.4, [[0.32, 0.52, 0.36, 0.56, 0, 1], [0.54, 0.68, 0.52, 0.66, 0, 0.6]]],
};
