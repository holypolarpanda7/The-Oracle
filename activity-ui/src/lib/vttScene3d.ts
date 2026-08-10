/** The isometric tactical board.
 *
 *  A third reader of `VttEngine.state()`, beside the server's PNG and the flat
 *  canvas. It draws the same room those two draw, from the same payload, under
 *  the same rule: **the grid is the truth and the picture is a texture.** Every
 *  wall here stands where a tile code says it stands; nothing is invented, and
 *  nothing the model or the mesh does can move a square.
 *
 *  ## What is 3D and what is not
 *
 *  The floor and the structure are geometry. The creatures are not — they stay
 *  DOM elements over the canvas, positioned by `screenOf`. That is the whole
 *  billboard trick: an element over a canvas already faces the viewer, so a
 *  camera-facing character costs a projection and no character art. It also
 *  means every existing token affordance — portraits, HP fill, condition pips,
 *  cover badges, targeting states, click handling — arrives here working, with
 *  no second implementation to keep in step.
 *
 *  ## What is deliberately not here yet
 *
 *  Props, fog, light tint, effects and stair markers are the next phase; the
 *  painted overlay is the one after. The board is playable without any of them,
 *  which is the point of building it in this order — geometry first, and
 *  everything else layered onto a thing that already works offline.
 *
 *  Lighting is form only. One directional light and some ambient exist so a
 *  wall reads as solid; the board's *mechanical* light level comes from
 *  `scene.light`, computed by the server, and will be a tint. Real lights here
 *  would let the picture disagree with the grid about who can see whom. */
import * as THREE from "three";
import type { VttScene } from "./types";
import {
  CELL, DECOR_KINDS, HOLE_CODES, OBJECT_VARIANTS, SKIRT_FT, STRUCTURE_CODES,
  heightScale,
  rotatePart,
  tileHeightFt,
  tileStyle, variantOf, wallParts, yawOf,
  type BoardView, type PaintState, type TokenPlacement, type View,
} from "./boardView";
import {
  FORWARD, FRAME_PAD_SQUARES, RIGHT, UP, boundsOf, project, unproject,
} from "./isocam";

/** How far back the camera sits. Orthographic, so this changes nothing about
 *  the image — it only has to clear the tallest thing on the board. */
const CAMERA_DISTANCE = 500;

/** Chamfer on the top edge of every extruded block, in world units (squares).
 *
 *  Small, and the single biggest reason the pre-painting board does not read as
 *  Minecraft. An unbevelled box meets the light along a mathematically perfect
 *  edge and catches no highlight, so it reads as a flat-shaded solid; a chamfer
 *  a few centimetres wide gives every edge a bright line and the eye reads a
 *  built thing. Cheap: four quads and a smaller top face per block. */
const BEVEL = 0.06;

/** Whether the painted layer is laid over the board yet.
 *
 *  OFF, and deliberately. The server side works — vtt/isocam.py rasterizes the
 *  depth map, the depth ControlNet paints a room that lands on the geometry,
 *  and scripts/iso_gallery.py shows the results. What is not proven is this
 *  file's half: the screen-space backdrop pass draws nothing in the browser,
 *  and because a board WITH a painting switches its geometry to depth-only, the
 *  failure is not "no picture" but "no board" — which is far worse than never
 *  having wired it.
 *
 *  Turning it off restores the geometry board, which is good, terrain-accurate
 *  and offline-safe. Suspects for whoever picks this up, in order: the
 *  screen-space ortho camera (frustum is left=0/right=w/top=0/bottom=h, an
 *  intentionally inverted Y, which is easy to get wrong), then the stencil mask
 *  that clips the painting to the board silhouette. The stencil was ruled out
 *  by disabling it — the painting did not appear either way — so the camera or
 *  the quad placement is the likelier fault.
 *
 *  It needs a browser and a devtools frame capture, not another guess. */
const PAINTED_BACKDROP = true;

/** Depth is a sort key for `z-index`, not a distance. Scaled up so that two
 *  creatures a single square apart still land on different integers. */
const DEPTH_STEPS = 8;

/** Accumulates triangles for one merged mesh.
 *
 *  Merging matters: a 40x30 board is 1200 floor tiles, and 1200 meshes is 1200
 *  draw calls, which is the difference between a board that runs on a phone
 *  inside a Discord webview and one that does not. */
/** The four corner UVs of a quad, in the vertex order `quad` takes them. */
const UV_SQUARE: readonly [number, number][] = [[0, 0], [0, 1], [1, 1], [1, 0]];

/** One square's UVs, turned and flipped by its own coordinates.
 *
 *  Every tile of a kind gets the same swatch, so a plain mapping tiles the room
 *  with one picture repeated in lockstep and the eye reads the repeat instantly.
 *  Rotating and mirroring per square gives eight arrangements from one texture
 *  for free, and doing it from the coordinates rather than at random keeps a
 *  room looking the same every time it is drawn. */
function tileUVs(x: number, z: number): readonly [number, number][] {
  const h = ((x * 73856093) ^ (z * 19349663)) >>> 0;
  const turns = h & 3;
  const flip = (h >>> 2) & 1;
  const out = UV_SQUARE.map((_, i) => UV_SQUARE[(i + turns) % 4]);
  return flip ? out.map(([u, v]) => [1 - u, v] as [number, number]) : out;
}

class MeshBuilder {
  private pos: number[] = [];
  private norm: number[] = [];
  private col: number[] = [];
  private uv: number[] = [];
  /** Which SQUARE each vertex belongs to, so shading can be rewritten in place
   *  without rebuilding the mesh. See `reshade`. */
  private owner: number[] = [];
  /** The square subsequent geometry belongs to. */
  at = 0;

  get empty(): boolean { return this.pos.length === 0; }

  /** One quad, wound counter-clockwise as seen from the side the normal faces. */
  quad(a: THREE.Vector3Like, b: THREE.Vector3Like, c: THREE.Vector3Like,
       d: THREE.Vector3Like, color: THREE.Color,
       uvs: readonly [number, number][] = UV_SQUARE): void {
    const nx = (b.y - a.y) * (c.z - a.z) - (b.z - a.z) * (c.y - a.y);
    const ny = (b.z - a.z) * (c.x - a.x) - (b.x - a.x) * (c.z - a.z);
    const nz = (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x);
    const len = Math.hypot(nx, ny, nz) || 1;
    for (const [i, j, k] of [[0, 1, 2], [0, 2, 3]] as const) {
      for (const idx of [i, j, k]) {
        const v = [a, b, c, d][idx];
        this.pos.push(v.x, v.y, v.z);
        this.norm.push(nx / len, ny / len, nz / len);
        this.col.push(color.r, color.g, color.b);
        this.uv.push(uvs[idx][0], uvs[idx][1]);
        this.owner.push(this.at);
      }
    }
  }

  build(): THREE.BufferGeometry {
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(this.pos, 3));
    g.setAttribute("normal", new THREE.Float32BufferAttribute(this.norm, 3));
    g.setAttribute("color", new THREE.Float32BufferAttribute(this.col, 3));
    g.setAttribute("uv", new THREE.Float32BufferAttribute(this.uv, 2));
    return g;
  }

  owners(): Uint32Array { return Uint32Array.from(this.owner); }
}

/** Catalogue swatches by stored image id.
 *
 *  Module-level, like `boardSprites`, because that is what the thing being
 *  cached is: one swatch of dungeon flagstone serves every board in every
 *  session. A texture is created immediately and fills in when the bytes land,
 *  so the mesh never has to be rebuilt on arrival — only redrawn. */
const TEXTURES = new Map<number, THREE.Texture | null | undefined>();

/** Fetch a swatch, and tell the caller when its fate is decided.
 *
 *  Deliberately does NOT touch a material. Vertex colour carries fog, sight and
 *  light (see `shade`), so a mesh must know whether it is textured at BUILD
 *  time: textured, the vertex colour has to be a pure multiplier over white, or
 *  the tile's own colour tints the picture; untextured, it has to be that tile
 *  colour, or the board goes grey. Switching a live material from one to the
 *  other is what silently erased the fog tiers the first time.
 *
 *  So a texture arriving invalidates the terrain instead, and the next frame
 *  rebuilds knowing the answer. It resolves once per board and a rebuild is
 *  cheap, which makes this the honest trade rather than the clever one. */
function requestTexture(id: number, settled: () => void): void {
  if (TEXTURES.has(id)) return;                // resolved, or already in flight
  TEXTURES.set(id, undefined);
  new THREE.TextureLoader().load(
    `/imagery/image/${id}`,
    (t) => {
      t.wrapS = THREE.RepeatWrapping;
      t.wrapT = THREE.RepeatWrapping;
      t.colorSpace = THREE.SRGBColorSpace;
      t.anisotropy = 4;
      TEXTURES.set(id, t);
      settled();
    },
    undefined,
    // No imagery server (the offline demo) or a pruned id. Remembered as bad so
    // it is asked for once, and the board simply stays flat-coloured.
    () => { TEXTURES.set(id, null); settled(); },
  );
}

const v3 = (x: number, y: number, z: number) => new THREE.Vector3(x, y, z);

/** Scenery colours. Muted on purpose: decoration that draws the eye competes
 *  with the things a player actually has to read — cover, hazards, creatures. */
const DECOR_TINT: Record<string, string> = {
  rug: "#5a3238", bones: "#c9c2ac", shards: "#8a7a63",
  roots: "#4a5a34", sack: "#7a6a4a", brazier: "#6a5b46",
};

/** How much of a square a door panel fills ACROSS its wall. Mirrors
 *  vtt/render_image.py's _PANEL_THICKNESS and the flat renderer's: a door is a
 *  plank in a wall, and drawn square and centred it reads as furniture parked
 *  on the floor. */
const PANEL_THICKNESS = 0.46;

/** Apertures — a gap in a wall rather than a block filling a square. */
const APERTURES: ReadonlySet<string> = new Set(["+", "/", "p"]);

/** What the party can see of a square, right now.
 *
 *  Two tiers, and both are needed or a door means nothing: `fog` is MEMORY and
 *  never dims, `sight` is recomputed per frame from real line of sight. With
 *  memory only, closing a door behind you changes nothing; with sight only, the
 *  party forgets the map every time it turns around. */
const enum Seen { Never = 0, Remembered = 1, Watched = 2 }

/** Dim and tint a tile for what can be seen of it and how lit it is.
 *
 *  Baked into the mesh rather than laid over it, because an overlay quad on the
 *  floor cannot darken a ten-foot wall standing on that square — the fog would
 *  stop at the skirting board. That is why fog, sight and light are part of the
 *  terrain cache key: they change on a move, not on a pointer flick. */
function shade(base: THREE.Color, seen: Seen, light: string): THREE.Color {
  const c = base.clone();
  if (seen === Seen.Never) return c.multiplyScalar(0.05);
  if (seen === Seen.Remembered) {
    // A cold veil: you remember the room, you are not watching it.
    c.multiplyScalar(0.4);
    return c.lerp(new THREE.Color(0x2b3c5e), 0.35);
  }
  if (light === "x") {
    // Watched but unlit — someone is seeing this by darkvision, and the rule
    // is that darkvision is greyscale. Rendering it merely dim would claim a
    // colour nobody in the room can actually make out.
    const g = c.r * 0.299 + c.g * 0.587 + c.b * 0.114;
    return c.setRGB(g, g, g).multiplyScalar(0.55);
  }
  return light === "d" ? c.multiplyScalar(0.72) : c;
}

/** A block with a chamfered top, emitting only the side faces that show.
 *
 *  Skipping buried faces is not only cheaper: a long wall run drawn as
 *  independent boxes has a bright chamfer line at every internal seam, which
 *  reads as a row of separate pillars rather than one wall. */
function block(mb: MeshBuilder, x: number, z: number, y0: number, y1: number,
               color: THREE.Color, exposed: (dx: number, dz: number) => boolean): void {
  boxFaces(mb, x, x + 1, z, z + 1, y0, y1, color, exposed);
}

/** A box over an arbitrary footprint, all four sides drawn.
 *
 *  For the things that do not fill their square: a door panel lying along its
 *  wall run, and a heap of wreckage. Nothing abuts them, so there are no shared
 *  faces to cull. */
function panelBlock(mb: MeshBuilder, x0: number, x1: number, z0: number,
                    z1: number, y0: number, y1: number, color: THREE.Color): void {
  boxFaces(mb, x0, x1, z0, z1, y0, y1, color, () => true);
}

/** An upright n-sided prism, chamfered at the top.
 *
 *  What stops a pillar being a cube. Structure is genuinely box-shaped and
 *  should stay that way — a wall IS a rectangular run — but a pillar, a tree
 *  and an altar are not, and drawing them as full-square blocks is most of what
 *  reads as voxel. Eight sides is plenty at this camera distance and keeps the
 *  triangle count near a box's.
 *
 *  This is not only cosmetic. The painted layer is conditioned on a depth map
 *  rendered FROM this geometry, so a cube-shaped pillar hands the model a
 *  cube-shaped silhouette and it paints a cube. Shape here is what the painting
 *  inherits. */
function prism(mb: MeshBuilder, cx: number, cz: number, r: number,
               y0: number, y1: number, sides: number, color: THREE.Color): void {
  const b = Math.min(BEVEL, r / 3);
  const top = y1;
  const rim = Math.max(y0, y1 - b);
  const pt = (i: number, rad: number) => {
    const a = (i / sides) * Math.PI * 2 + Math.PI / sides;
    return [cx + Math.cos(a) * rad, cz + Math.sin(a) * rad] as const;
  };
  // Top cap as a fan of quads from the centre (two triangles each, one
  // degenerate — cheaper than a separate triangle path in MeshBuilder).
  for (let i = 0; i < sides; i++) {
    const [ax, az] = pt(i, r - b);
    const [bx, bz] = pt(i + 1, r - b);
    mb.quad(v3(cx, top, cz), v3(ax, top, az), v3(bx, top, bz), v3(cx, top, cz), color);
  }
  for (let i = 0; i < sides; i++) {
    const [ax, az] = pt(i, r);
    const [bx, bz] = pt(i + 1, r);
    const [iax, iaz] = pt(i, r - b);
    const [ibx, ibz] = pt(i + 1, r - b);
    mb.quad(v3(ax, y0, az), v3(ax, rim, az), v3(bx, rim, bz), v3(bx, y0, bz), color);
    mb.quad(v3(ax, rim, az), v3(iax, top, iaz), v3(ibx, top, ibz), v3(bx, rim, bz), color);
  }
}

function boxFaces(mb: MeshBuilder, x0: number, x1: number, z0: number, z1: number,
                  y0: number, y1: number, color: THREE.Color,
                  exposed: (dx: number, dz: number) => boolean): void {
  const top = y1;
  const rim = Math.max(y0, y1 - BEVEL);
  // A chamfer can never eat more than half of the thing it is chamfering — a
  // door panel is thinner than two bevels, and left unclamped its top face
  // turns inside out.
  const b = Math.min(BEVEL, (x1 - x0) / 3, (z1 - z0) / 3);

  // The top is inset ONLY where a side is actually exposed. Insetting all four
  // regardless left a bevel-wide slot down every seam of a wall run — two
  // neighbours each pulling their top face back from a shared edge that has no
  // side quad to close it — so a plain wall read as a row of separate blocks
  // with grooves between them. That was a large part of why this looked like
  // Minecraft, and it was a bug rather than a consequence of using boxes.
  const ix0 = exposed(-1, 0) ? x0 + b : x0;
  const ix1 = exposed(1, 0) ? x1 - b : x1;
  const iz0 = exposed(0, -1) ? z0 + b : z0;
  const iz1 = exposed(0, 1) ? z1 - b : z1;

  // Top face.
  mb.quad(v3(ix0, top, iz0), v3(ix0, top, iz1), v3(ix1, top, iz1), v3(ix1, top, iz0), color);

  // North (-z), south (+z), west (-x), east (+x). Each gets a wall face up to
  // the rim and a chamfer from the rim to the inset top.
  //
  // Winding is load-bearing here, not a detail: `quad` derives the normal from
  // the vertex order, so a reversed face does not merely cull — it gets a
  // normal pointing INTO the block, and the light then finds nothing to catch.
  // Every run below is ordered so (b-a) x (c-a) points out of the block.
  const faces: [number, number, THREE.Vector3[], THREE.Vector3[]][] = [
    [0, -1, [v3(x0, y0, z0), v3(x0, rim, z0), v3(x1, rim, z0), v3(x1, y0, z0)],
            [v3(x0, rim, z0), v3(ix0, top, iz0), v3(ix1, top, iz0), v3(x1, rim, z0)]],
    [0, 1, [v3(x1, y0, z1), v3(x1, rim, z1), v3(x0, rim, z1), v3(x0, y0, z1)],
           [v3(x1, rim, z1), v3(ix1, top, iz1), v3(ix0, top, iz1), v3(x0, rim, z1)]],
    [-1, 0, [v3(x0, y0, z1), v3(x0, rim, z1), v3(x0, rim, z0), v3(x0, y0, z0)],
            [v3(x0, rim, z1), v3(ix0, top, iz1), v3(ix0, top, iz0), v3(x0, rim, z0)]],
    [1, 0, [v3(x1, y0, z0), v3(x1, rim, z0), v3(x1, rim, z1), v3(x1, y0, z1)],
           [v3(x1, rim, z0), v3(ix1, top, iz0), v3(ix1, top, iz1), v3(x1, rim, z1)]],
  ];
  for (const [dx, dz, side, chamfer] of faces) {
    if (!exposed(dx, dz)) continue;
    mb.quad(side[0], side[1], side[2], side[3], color);
    mb.quad(chamfer[0], chamfer[1], chamfer[2], chamfer[3], color);
  }
}

/** Squares -> flat quads just above the floor. Used for every wash and marker. */
function decal(squares: Iterable<[number, number]>, y: number,
               color: string, opacity: number): THREE.Mesh | null {
  const pos: number[] = [];
  for (const [x, z] of squares) {
    pos.push(x, y, z, x, y, z + 1, x + 1, y, z + 1,
             x, y, z, x + 1, y, z + 1, x + 1, y, z);
  }
  if (!pos.length) return null;
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
  const m = new THREE.MeshBasicMaterial({
    color: new THREE.Color(color), transparent: true, opacity,
    depthWrite: false, side: THREE.DoubleSide,
    polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -2,
  });
  return new THREE.Mesh(g, m);
}

function disposeTree(obj: THREE.Object3D): void {
  obj.traverse((o) => {
    const m = o as THREE.Mesh | THREE.LineSegments;
    m.geometry?.dispose?.();
    const mat = m.material;
    if (Array.isArray(mat)) mat.forEach((x) => x.dispose());
    else mat?.dispose?.();
  });
}

export function createIsoBoardView(canvas: HTMLCanvasElement): BoardView {
  // `alpha` matters: the painted layer is a DOM image BEHIND this canvas, and
  // an opaque canvas would hide it.
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
  renderer.setClearColor(0x000000, 0);

  const scene3 = new THREE.Scene();
  const camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.1, CAMERA_DISTANCE * 2);

  // Form only — see the header. Angled off the camera axis so the two visible
  // faces of every corner differ, which is what makes a block read as a block.
  const sun = new THREE.DirectionalLight(0xfff2dc, 1.5);
  sun.position.set(-0.4, 1, 0.65);
  scene3.add(sun);
  scene3.add(new THREE.AmbientLight(0x8899cc, 1.1));

  const terrainGroup = new THREE.Group();
  const decalGroup = new THREE.Group();
  scene3.add(terrainGroup, decalGroup);

  renderer.autoClear = false;

  /** The terrain mesh is expensive and the board redraws on every pointer move,
   *  so it is rebuilt only when the ROOM changes. Keying on `scene.revision`
   *  would rebuild on every step anyone takes; keying on the tile rows means a
   *  smashed pillar rebuilds and a walk does not. */
  let terrainKey = "";

  /** The last frame's arguments, so a swatch arriving late can repaint without
   *  the component being told. A texture fills into a material that is already
   *  on the mesh, so this is a redraw and never a rebuild. */
  let lastFrame: { st: PaintState; w: number; h: number } | null = null;
  let pending = 0;
  const redraw = () => {
    if (pending || !lastFrame) return;
    pending = requestAnimationFrame(() => {
      pending = 0;
      if (lastFrame) paintFrame(lastFrame.st, lastFrame.w, lastFrame.h);
    });
  };

  /** A swatch's fate has been decided: rebuild, because whether a code is
   *  textured changes what its vertex colours have to mean. */
  const invalidate = () => { terrainKey = ""; redraw(); };

  const heightUnits = (scene: VttScene, ft: number) => ft / (scene.square_ft || 5);
  const baseUnits = (scene: VttScene, level: number) =>
    heightUnits(scene, scene.levels?.[level]?.base_ft ?? 0);

  /** Tallest thing on the board, for framing and for the camera's far plane. */
  function tallestUnits(scene: VttScene): number {
    let ft = 0;
    for (const row of scene.terrain ?? []) {
      for (const ch of row) ft = Math.max(ft, tileHeightFt(ch));
    }
    return heightUnits(scene, ft);
  }

  /** Is a painting being drawn behind the geometry this frame? */
  let backdrop = false;

  /** Per code-mesh: which square each vertex belongs to, and the untinted
   *  colour that shading multiplies. Kept so fog, sight and light can be
   *  rewritten straight into the colour attribute.
   *
   *  Rebuilding the whole terrain for a shading change was the old behaviour
   *  and it happened on every step anyone took — geometry, normals and UVs all
   *  thrown away and remade because a torch moved. The positions did not
   *  change; only the tint did. */
  let shadeTargets: { geom: THREE.BufferGeometry; owners: Uint32Array;
                      base: THREE.Color }[] = [];
  let shadeKey = "";

  function buildTerrain(scene: VttScene, level: number, showGrid: boolean): void {
    disposeTree(terrainGroup);
    terrainGroup.clear();

    const rows = scene.terrain ?? [];
    const base = baseUnits(scene, level);
    const at = (x: number, z: number): string | null => {
      if (z < 0 || z >= rows.length) return null;
      const r = rows[z];
      if (x < 0 || x >= r.length) return null;
      return r[x];
    };

    // One builder per TILE CODE, not one for the whole floor: each code carries
    // its own swatch, and a mesh has one material. Still a handful of draw
    // calls for a whole board — a code, not a square.
    const byCode = new Map<string, MeshBuilder>();
    const builderFor = (code: string) => {
      let b = byCode.get(code);
      if (!b) { b = new MeshBuilder(); byCode.set(code, b); }
      return b;
    };
    const gridPts: number[] = [];
    const swatches = scene.materials ?? {};

    // Which way the wall runs through each aperture. Read off the server's own
    // objects list rather than re-derived here: `aperture_axis` already
    // answered it once, from the grid, for every view.
    const axes = new Map<string, string>();
    for (const o of scene.objects ?? []) {
      if (o.axis) axes.set(`${o.x},${o.y}`, o.axis);
    }
    const wrecked = new Set((scene.debris ?? []).map((d) => `${d.x},${d.y}`));

    // Which codes will actually be drawn with a picture on this pass. Anything
    // still in flight counts as untextured for now and the mesh is rebuilt when
    // it lands — see `requestTexture`.
    const textured = new Set<string>();
    for (const [code, id] of Object.entries(swatches)) {
      const t = TEXTURES.get(id);
      if (t instanceof THREE.Texture) textured.add(code);
      else if (t === undefined) requestTexture(id, invalidate);
    }

    /** Floor a creature could stand on — not structure, not off the board.
     *  Void counts as closed: on an upper storey it is open air, and a wall
     *  should not grow a face onto a hole. Mirrors is_open in vtt/isocam.py. */
    const isOpen = (x: number, z: number): boolean => {
      const c = at(x, z);
      return c !== null && !HOLE_CODES.has(c) && !STRUCTURE_CODES.has(c);
    };

    const seenAt = (x: number, z: number): Seen => {
      if (!scene.fog) return Seen.Watched;          // no fog = all visible
      if (scene.sight?.[z]?.[x] === "1") return Seen.Watched;
      return scene.fog[z]?.[x] === "1" ? Seen.Remembered : Seen.Never;
    };
    const lightAt = (x: number, z: number): string =>
      scene.light?.[z]?.[x] ?? "b";

    for (let z = 0; z < rows.length; z++) {
      for (let x = 0; x < rows[z].length; x++) {
        const code = rows[z][x];
        // A HOLE, not dark ground. Void is open air on an upper storey; open
        // sky IS air; a chasm is the absence of floor. Drawing any of them as a
        // surface is the geometry inventing ground the rules say you fall
        // through — a sky-islands board is mostly open sky, and paved over it
        // came back a flat plane instead of stones floating in nothing.
        if (HOLE_CODES.has(code)) continue;

        // The UNTINTED colour. Fog, sight and light are applied afterwards by
        // `reshade`, straight into the colour attribute, so a torch moving does
        // not throw away the geometry it is lighting.
        // NB: not `base` — that is the storey height, a few lines up.
        const tint = textured.has(code) ? "#ffffff" : tileStyle(code).fill;
        const color = new THREE.Color(tint);
        // Heights get a little life, except where the rules quote one.
        const h = tileHeightFt(code) * heightScale(code, x, z);
        const mb = builderFor(code);
        // Everything emitted from here belongs to this square, so `reshade` can
        // find its vertices again without rebuilding anything.
        mb.at = z * scene.width + x;

        // Floor under everything: an object stands ON a square, and without
        // this a pillar's base is a hole in the ground.
        mb.quad(v3(x, base, z), v3(x, base, z + 1),
                v3(x + 1, base, z + 1), v3(x + 1, base, z), color, tileUVs(x, z));
        // A skirt wherever the floor ends at a hole, so a platform has
        // substance rather than being a sheet of paper hanging in nothing.
        // The same "draw the FACE where two things meet" rule as the walls,
        // pointed downward.
        const drop = base - heightUnits(scene, SKIRT_FT);
        const holeAt = (hx: number, hz: number) => {
          const c = at(hx, hz);
          return c === null || HOLE_CODES.has(c);
        };
        if (holeAt(x, z + 1)) {
          mb.quad(v3(x + 1, drop, z + 1), v3(x + 1, base, z + 1),
                  v3(x, base, z + 1), v3(x, drop, z + 1), color);
        }
        if (holeAt(x + 1, z)) {
          mb.quad(v3(x + 1, drop, z), v3(x + 1, base, z),
                  v3(x + 1, base, z + 1), v3(x + 1, drop, z + 1), color);
        }
        if (holeAt(x, z - 1)) {
          mb.quad(v3(x, drop, z), v3(x, base, z),
                  v3(x + 1, base, z), v3(x + 1, drop, z), color);
        }
        if (holeAt(x - 1, z)) {
          mb.quad(v3(x, drop, z + 1), v3(x, base, z + 1),
                  v3(x, base, z), v3(x, drop, z), color);
        }

        if (showGrid && seenAt(x, z) !== Seen.Never) {
          gridPts.push(x, base, z, x + 1, base, z);
          gridPts.push(x, base, z, x, base, z + 1);
        }

        if (h > 0) {
          const top = base + heightUnits(scene, h);
          const axis = axes.get(`${x},${z}`);
          if (APERTURES.has(code) && axis) {
            // A door belongs to the wall it interrupts: a thin panel lying
            // along that wall's run, never a block filling its square.
            const t = PANEL_THICKNESS / 2;
            const [x0, x1] = axis === "ew" ? [x, x + 1] : [x + 0.5 - t, x + 0.5 + t];
            const [z0, z1] = axis === "ew" ? [z + 0.5 - t, z + 0.5 + t] : [z, z + 1];
            panelBlock(mb, x0, x1, z0, z1, base, top, color);
          } else if (STRUCTURE_CODES.has(code)) {
            // A thin skin where the solid region meets open floor, never a full
            // five-foot cube. The square stays solid in the RULES — this only
            // stops the wall's top face swallowing the room at this camera
            // angle. See wallParts.
            for (const [wx0, wx1, wz0, wz1] of wallParts(isOpen, x, z)) {
              panelBlock(mb, x + wx0, x + wx1, z + wz0, z + wz1, base, top, color);
            }
          } else {
            // Everything else is a THING standing on a square, and things are
            // not cubes. Each gets a silhouette of roughly its real footprint —
            // a 5-ft square is a big place, and a pillar filling one is the
            // single loudest voxel tell on the board.
            const cx = x + 0.5, cz = z + 0.5;
            if (code === "O") {
              prism(mb, cx, cz, 0.32, base, top, 8, color);
            } else if (code === "T") {
              prism(mb, cx, cz, 0.13, base, top * 0.55 + base * 0.45, 6, color);
              prism(mb, cx, cz, 0.46, base + (top - base) * 0.4, top, 8, color);
            } else if (OBJECT_VARIANTS[code]) {
              // A built silhouette, in one of several arrangements chosen by the
              // square itself — shared with the depth map the painted layer is
              // conditioned on, so what stands here is what gets painted here.
              const vs = OBJECT_VARIANTS[code];
              const parts = vs[variantOf(x, z, vs.length)];
              const turns = yawOf(x, z);
              const h2 = top - base;
              for (const raw of parts) {
                const [px0, px1, pz0, pz1, py0, py1] = rotatePart(raw, turns);
                panelBlock(mb, x + px0, x + px1, z + pz0, z + pz1,
                           base + h2 * py0, base + h2 * py1, color);
              }
            } else {
              block(mb, x, z, base, top, color, () => true);
            }
          }
        } else if (wrecked.has(`${x},${z}`)) {
          // Something stood here and was broken. The square's terrain already
          // changed to what it left, so the floor is right — but wreckage that
          // is only a change of colour appears from nowhere. A low heap says a
          // thing came down here.
          panelBlock(mb, x + 0.12, x + 0.88, z + 0.12, z + 0.88,
                     base, base + 0.22, color);
        }
      }
    }

    // Scenery, into its own builder so it shares the plain untextured material
    // and never inherits a wall's swatch. It stands ON the floor and occludes
    // nothing the rules care about, so it needs no ordering.
    const decorMb = new MeshBuilder();
    for (const d of scene.decor ?? []) {
      const spec = DECOR_KINDS[d.kind];
      if (!spec) continue;
      const [ft, parts] = spec;
      const dTop = base + heightUnits(scene, ft);
      const turns = yawOf(d.x, d.y);
      decorMb.at = d.y * scene.width + d.x;
      const col = new THREE.Color(DECOR_TINT[d.kind] ?? "#6b6255");
      for (const raw of parts) {
        const [px0, px1, pz0, pz1, py0, py1] = rotatePart(raw, turns);
        panelBlock(decorMb, d.x + px0, d.x + px1, d.y + pz0, d.y + pz1,
                   base + (dTop - base) * py0, base + (dTop - base) * py1, col);
      }
    }

    shadeTargets = [];
    if (!decorMb.empty) {
      const geom = decorMb.build();
      shadeTargets.push({ geom, owners: decorMb.owners(),
                          base: new THREE.Color("#ffffff") });
      terrainGroup.add(new THREE.Mesh(geom, new THREE.MeshLambertMaterial({
        vertexColors: true, ...(backdrop ? { colorWrite: false } : {}),
      })));
    }
    for (const [code, mb] of byCode) {
      if (mb.empty) continue;
      const tex = textured.has(code) ? TEXTURES.get(swatches[code]) : null;
      const geom = mb.build();
      shadeTargets.push({
        geom, owners: mb.owners(),
        base: new THREE.Color(textured.has(code) ? "#ffffff" : tileStyle(code).fill),
      });
      terrainGroup.add(new THREE.Mesh(geom, new THREE.MeshLambertMaterial({
        vertexColors: true,
        ...(tex instanceof THREE.Texture ? { map: tex } : {}),
        // With a painting behind it the geometry stops drawing and becomes a
        // depth-only proxy — invisible, but still occluding the decals and
        // answering "is a wall in front of this creature". That is precisely
        // what Baldur's Gate shipped beside each of its backgrounds, and it is
        // why the geometry is not thrown away once the picture arrives.
        ...(backdrop ? { colorWrite: false } : {}),
      })));
    }
    shadeKey = "";      // force a tint pass over the new geometry
    if (gridPts.length) {
      const g = new THREE.BufferGeometry();
      g.setAttribute("position", new THREE.Float32BufferAttribute(gridPts, 3));
      terrainGroup.add(new THREE.LineSegments(g, new THREE.LineBasicMaterial({
        color: 0x8fa2d8, transparent: true, opacity: 0.16,
        polygonOffset: true, polygonOffsetFactor: -1,
      })));
    }
  }

  /** Rewrite fog, sight and light into the colour attribute, in place.
   *
   *  Cheap for a reason worth stating: shading only ever takes nine values —
   *  three visibility tiers by three light levels — so the colours are computed
   *  nine times per mesh and the per-vertex loop is a table lookup and three
   *  writes. No allocation, no geometry, nothing for the collector.
   */
  function reshade(scene: VttScene, level: number, showFog: boolean): void {
    const fog = showFog ? scene.fog : null;
    const sight = showFog ? scene.sight : null;
    const light = scene.light;
    const w = scene.width;

    for (const { geom, owners, base } of shadeTargets) {
      const attr = geom.getAttribute("color") as THREE.BufferAttribute;
      const arr = attr.array as Float32Array;
      // Memoised by (tier, light) — nine possibilities, whatever the board size.
      const cache = new Map<number, THREE.Color>();
      let lastTile = -1, r = 0, g = 0, b = 0;
      for (let v = 0; v < owners.length; v++) {
        const tile = owners[v];
        if (tile !== lastTile) {
          const z = (tile / w) | 0;
          const x = tile - z * w;
          const seen: Seen = !fog ? Seen.Watched
            : sight?.[z]?.[x] === "1" ? Seen.Watched
            : fog[z]?.[x] === "1" ? Seen.Remembered : Seen.Never;
          const lv = light?.[z]?.[x] ?? "b";
          const key = seen * 4 + (lv === "x" ? 2 : lv === "d" ? 1 : 0);
          let c = cache.get(key);
          if (!c) { c = shade(base, seen, lv); cache.set(key, c); }
          r = c.r; g = c.g; b = c.b;
          lastTile = tile;
        }
        const i = v * 3;
        arr[i] = r; arr[i + 1] = g; arr[i + 2] = b;
      }
      attr.needsUpdate = true;
    }
  }

  function buildDecals(st: PaintState, level: number): void {
    disposeTree(decalGroup);
    decalGroup.clear();
    const { scene } = st;
    const y = baseUnits(scene, level) + 0.012;
    const keys = (m: Iterable<string>) =>
      [...m].map((k) => k.split(",").map(Number) as [number, number]);

    const add = (mesh: THREE.Mesh | null) => { if (mesh) decalGroup.add(mesh); };

    // Spell areas, zones, auras and lingering hazards. Their `squares` are the
    // SERVER's — already clipped by line of effect — so this only paints what
    // it is handed and never works out a footprint of its own. Drawn first, so
    // the things a player is deciding right now sit above them.
    if (st.show.effects) {
      for (const e of scene.effects ?? []) {
        if ((e.level ?? 0) !== level || !e.squares?.length) continue;
        add(decal(e.squares, y + 0.001, e.color || "#a86bff",
                  Math.min(0.7, Math.max(0.12, e.opacity || 0.3))));
      }
    }

    // A way off this floor is a feature of the room, and a player cannot choose
    // a stair they cannot see. The destination's NAME is a DOM label; this is
    // the mark on the board underneath it.
    for (const s of st.stairs ?? []) {
      add(decal([[s.x, s.y]], y + 0.009, "#e6bc64", 0.42));
    }

    // Threatened ground goes UNDER the movement wash, so a player sees the
    // danger while choosing rather than after the pointer has settled.
    if (st.threatened) add(decal(keys(st.threatened), y, "#ff6b57", 0.2));
    if (st.reach) add(decal(keys(st.reach.keys()), y + 0.002, "#5aa9e6", 0.22));
    if (st.area) {
      add(decal(st.area, y + 0.006, st.areaLegal === false ? "#ff6b57" : "#c07bff", 0.34));
    }
    if (st.path) add(decal(st.path, y + 0.004, st.pathProvokes ? "#ffb347" : "#8fd6ff", 0.4));
    if (st.hover) add(decal([st.hover], y + 0.008, "#e6bc64", 0.3));

    // A base under each creature. Without it a standee floats: the disc is
    // drawn at head height and nothing says which square it belongs to.
    const bases: [number, number][] = [];
    for (const t of scene.tokens) {
      if ((t.level ?? 0) !== level || t.defeated) continue;
      // Mid-walk the base follows the creature; left at t.x/t.y it would sit
      // at the destination while the figure was still crossing the room.
      const wk = st.walking && st.walking.tokenId === t.id ? st.walking : null;
      const bx = wk ? wk.x : t.x;
      const bz = wk ? wk.y : t.y;
      for (let dx = 0; dx < t.squares; dx++) {
        for (let dz = 0; dz < t.squares; dz++) bases.push([bx + dx, bz + dz]);
      }
    }
    add(decal(bases, y + 0.01, "#0a0d16", 0.35));
  }

  return {
    fit(scene: VttScene, w: number, h: number): View {
      const pad = 18;
      const b = boundsOf(scene.width, scene.height, tallestUnits(scene));
      const spanX = Math.max(1e-6, b.maxX - b.minX);
      const spanY = Math.max(1e-6, b.maxY - b.minY);
      const scale = Math.max(0.28, Math.min(
        (w - pad * 2) / (CELL * spanX),
        (h - pad * 2) / (CELL * spanY),
        1.6,
      ));
      return {
        scale,
        ox: w / 2 - CELL * scale * (b.minX + b.maxX) / 2,
        oy: h / 2 - CELL * scale * (b.minY + b.maxY) / 2,
      };
    },

    squareAt(view: View, scene: VttScene, px: number, py: number,
             level: number): [number, number] | null {
      const k = CELL * view.scale;
      const [wx, wz] = unproject((px - view.ox) / k, (py - view.oy) / k,
                                 baseUnits(scene, level));
      const x = Math.floor(wx);
      const y = Math.floor(wz);
      // Unlike the flat board this CAN miss: the viewport is a rectangle and
      // the board inside it is a diamond, so a good part of the frame is not
      // over the board at all. Reporting a square there would let a click on
      // empty space walk someone off the map.
      if (x < 0 || y < 0 || x >= scene.width || y >= scene.height) return null;
      return [x, y];
    },

    screenOf(view: View, scene: VttScene, x: number, y: number, squares: number,
             level: number, elevationFt: number): TokenPlacement {
      const k = CELL * view.scale;
      const wy = baseUnits(scene, level) + heightUnits(scene, elevationFt);
      const p = project(x + squares / 2, wy, y + squares / 2);
      const size = k * squares;
      return {
        left: view.ox + k * p.x - size / 2,
        // The token's FOOT sits on the square, not its middle — that is what
        // makes a flat disc read as a figure standing on the board rather than
        // a counter lying on it.
        top: view.oy + k * p.y - size,
        size,
        depth: p.depth * DEPTH_STEPS,
        occluded: false,   // phase 5
      };
    },

    zoomAt(view: View, px: number, py: number, factor: number): View {
      const scale = Math.max(0.2, Math.min(3, view.scale * factor));
      const k = scale / view.scale;
      return { scale, ox: px - (px - view.ox) * k, oy: py - (py - view.oy) * k };
    },

    backdropRect(view: View, scene: VttScene) {
      if (!PAINTED_BACKDROP || !scene.iso_image_id) return null;
      const k = CELL * view.scale;
      const b = boundsOf(scene.width, scene.height, tallestUnits(scene));
      const p = FRAME_PAD_SQUARES;
      return {
        left: view.ox + k * (b.minX - p),
        top: view.oy + k * (b.minY - p),
        width: k * (b.maxX - b.minX + p * 2),
        height: k * (b.maxY - b.minY + p * 2),
      };
    },

    draw(st: PaintState, w: number, h: number): void {
      lastFrame = { st, w, h };
      paintFrame(st, w, h);
    },

    dispose(): void {
      if (pending) cancelAnimationFrame(pending);
      lastFrame = null;
      disposeTree(scene3);
      renderer.dispose();
    },
  };

  function paintFrame(st: PaintState, w: number, h: number): void {
      if (w === 0 || h === 0) return;
      const { scene, view } = st;
      const level = st.level ?? 0;

      renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
      renderer.setSize(w, h, false);
      canvas.style.width = `${w}px`;
      canvas.style.height = `${h}px`;

      // The painted layer. Only the ground floor has one — an upper storey is
      // its own room and would need its own painting, so peeking upstairs falls
      // back to geometry rather than showing the hall's picture on a gallery.
      const isoId = level === 0 ? scene.iso_image_id ?? 0 : 0;
      const isoTex = isoId ? TEXTURES.get(isoId) : null;
      if (isoId && isoTex === undefined) requestTexture(isoId, invalidate);
      backdrop = PAINTED_BACKDROP && isoTex instanceof THREE.Texture;

      // Fog, live sight and light are baked into the mesh (see `shade`), so
      // they belong in the key. They change on a MOVE, not on a pointer flick,
      // so this rebuilds when someone walks and stays cached while they aim.
      // Geometry depends on the ROOM. Shading does not, and used to be baked
      // into the same key — so every step anyone took rebuilt the whole mesh
      // because the fog had moved. They are two keys now: the room is rare, the
      // tint is every frame that matters.
      const key = [
        scene.id, level, st.show.grid, st.show.terrain, backdrop,
        (scene.terrain ?? []).join(""),
        (scene.debris ?? []).map((d) => `${d.x},${d.y}`).join(";"),
      ].join("|");
      if (key !== terrainKey) {
        buildTerrain(scene, level, st.show.grid);
        terrainKey = key;
      }
      const tint = [
        st.show.fog, (scene.fog ?? []).join(""), (scene.sight ?? []).join(""),
        (scene.light ?? []).join(""),
      ].join("|");
      if (tint !== shadeKey) {
        reshade(scene, level, st.show.fog);
        shadeKey = tint;
      }
      buildDecals(st, level);

      // Drive the orthographic camera from `View`, so pan and zoom mean exactly
      // what they mean on the flat board and `screenOf` and the rendered image
      // cannot drift apart.
      const k = CELL * view.scale;
      const halfW = w / 2 / k;
      const halfH = h / 2 / k;
      const cx = (w / 2 - view.ox) / k;
      const cyUp = (view.oy - h / 2) / k;
      const target = new THREE.Vector3(
        RIGHT[0] * cx + UP[0] * cyUp,
        RIGHT[1] * cx + UP[1] * cyUp,
        RIGHT[2] * cx + UP[2] * cyUp,
      );
      camera.left = -halfW;
      camera.right = halfW;
      camera.top = halfH;
      camera.bottom = -halfH;
      camera.near = 0.1;
      camera.far = CAMERA_DISTANCE * 2;
      camera.position.set(
        target.x - FORWARD[0] * CAMERA_DISTANCE,
        target.y - FORWARD[1] * CAMERA_DISTANCE,
        target.z - FORWARD[2] * CAMERA_DISTANCE,
      );
      camera.up.set(UP[0], UP[1], UP[2]);
      camera.lookAt(target);
      camera.updateProjectionMatrix();

      // One pass. The painting is a DOM layer BEHIND this canvas (see
      // `backdropRect`), which is why the canvas is alpha and why the geometry
      // stops drawing colour when a painting exists — it stays as a depth-only
      // proxy so the decals still occlude correctly.
      //
      // It was a second WebGL camera rendering a screen-space quad, and that
      // never drew: the pass was reached with a correct rectangle and a loaded
      // texture, and a plain red quad with no stencil did not appear either.
      // A DOM image needs none of that machinery, and the painting already
      // carries its own alpha, so the corners are handled without a stencil.
      renderer.clear(true, true, true);
      renderer.render(scene3, camera);
  }
}
