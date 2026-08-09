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
  CELL, STRUCTURE_CODES, tileHeightFt, tileStyle,
  type BoardView, type PaintState, type TokenPlacement, type View,
} from "./boardView";
import { FORWARD, RIGHT, UP, boundsOf, project, unproject } from "./isocam";

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

/** Depth is a sort key for `z-index`, not a distance. Scaled up so that two
 *  creatures a single square apart still land on different integers. */
const DEPTH_STEPS = 8;

/** Accumulates triangles for one merged mesh.
 *
 *  Merging matters: a 40x30 board is 1200 floor tiles, and 1200 meshes is 1200
 *  draw calls, which is the difference between a board that runs on a phone
 *  inside a Discord webview and one that does not. */
class MeshBuilder {
  private pos: number[] = [];
  private norm: number[] = [];
  private col: number[] = [];

  get empty(): boolean { return this.pos.length === 0; }

  /** One quad, wound counter-clockwise as seen from the side the normal faces. */
  quad(a: THREE.Vector3Like, b: THREE.Vector3Like, c: THREE.Vector3Like,
       d: THREE.Vector3Like, color: THREE.Color): void {
    const nx = (b.y - a.y) * (c.z - a.z) - (b.z - a.z) * (c.y - a.y);
    const ny = (b.z - a.z) * (c.x - a.x) - (b.x - a.x) * (c.z - a.z);
    const nz = (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x);
    const len = Math.hypot(nx, ny, nz) || 1;
    for (const [p, q, r] of [[a, b, c], [a, c, d]] as const) {
      for (const v of [p, q, r]) {
        this.pos.push(v.x, v.y, v.z);
        this.norm.push(nx / len, ny / len, nz / len);
        this.col.push(color.r, color.g, color.b);
      }
    }
  }

  build(): THREE.BufferGeometry {
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(this.pos, 3));
    g.setAttribute("normal", new THREE.Float32BufferAttribute(this.norm, 3));
    g.setAttribute("color", new THREE.Float32BufferAttribute(this.col, 3));
    return g;
  }
}

const v3 = (x: number, y: number, z: number) => new THREE.Vector3(x, y, z);

/** A block with a chamfered top, emitting only the side faces that show.
 *
 *  Skipping buried faces is not only cheaper: a long wall run drawn as
 *  independent boxes has a bright chamfer line at every internal seam, which
 *  reads as a row of separate pillars rather than one wall. */
function block(mb: MeshBuilder, x: number, z: number, y0: number, y1: number,
               color: THREE.Color, exposed: (dx: number, dz: number) => boolean): void {
  const top = y1;
  const rim = Math.max(y0, y1 - BEVEL);
  const b = BEVEL;
  const [x0, x1, z0, z1] = [x, x + 1, z, z + 1];
  const [ix0, ix1, iz0, iz1] = [x0 + b, x1 - b, z0 + b, z1 - b];

  // Top face, inset.
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

  /** The terrain mesh is expensive and the board redraws on every pointer move,
   *  so it is rebuilt only when the ROOM changes. Keying on `scene.revision`
   *  would rebuild on every step anyone takes; keying on the tile rows means a
   *  smashed pillar rebuilds and a walk does not. */
  let terrainKey = "";

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

    const floor = new MeshBuilder();
    const structure = new MeshBuilder();
    const gridPts: number[] = [];

    for (let z = 0; z < rows.length; z++) {
      for (let x = 0; x < rows[z].length; x++) {
        const code = rows[z][x];
        // A void square is a hole, not dark ground: on an upper storey it is
        // open air you can see and fall through, and drawing a floor there
        // would hide the hall below.
        if (code === " ") continue;

        const style = tileStyle(code);
        const color = new THREE.Color(style.fill);
        const h = tileHeightFt(code);

        if (h > 0) {
          const top = base + heightUnits(scene, h);
          // Structure hides its shared faces from its neighbours; a discrete
          // object standing on its own square shows all four.
          const solid = STRUCTURE_CODES.has(code);
          structure.quad(v3(x, base, z), v3(x, base, z + 1),
                         v3(x + 1, base, z + 1), v3(x + 1, base, z), color);
          block(structure, x, z, base, top, color, (dx, dz) => {
            if (!solid) return true;
            const n = at(x + dx, z + dz);
            return n === null || tileHeightFt(n) < h;
          });
        } else {
          floor.quad(v3(x, base, z), v3(x, base, z + 1),
                     v3(x + 1, base, z + 1), v3(x + 1, base, z), color);
          if (showGrid) {
            gridPts.push(x, base, z, x + 1, base, z);
            gridPts.push(x, base, z, x, base, z + 1);
          }
        }
      }
    }

    const mat = () => new THREE.MeshLambertMaterial({ vertexColors: true });
    if (!floor.empty) terrainGroup.add(new THREE.Mesh(floor.build(), mat()));
    if (!structure.empty) terrainGroup.add(new THREE.Mesh(structure.build(), mat()));
    if (gridPts.length) {
      const g = new THREE.BufferGeometry();
      g.setAttribute("position", new THREE.Float32BufferAttribute(gridPts, 3));
      terrainGroup.add(new THREE.LineSegments(g, new THREE.LineBasicMaterial({
        color: 0x8fa2d8, transparent: true, opacity: 0.16,
        polygonOffset: true, polygonOffsetFactor: -1,
      })));
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
      for (let dx = 0; dx < t.squares; dx++) {
        for (let dz = 0; dz < t.squares; dz++) bases.push([t.x + dx, t.y + dz]);
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

    draw(st: PaintState, w: number, h: number): void {
      if (w === 0 || h === 0) return;
      const { scene, view } = st;
      const level = st.level ?? 0;

      renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
      renderer.setSize(w, h, false);
      canvas.style.width = `${w}px`;
      canvas.style.height = `${h}px`;

      const key = `${scene.id}|${level}|${st.show.grid}|${(scene.terrain ?? []).join("|")}`;
      if (key !== terrainKey) {
        buildTerrain(scene, level, st.show.grid);
        terrainKey = key;
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

      renderer.render(scene3, camera);
    },

    dispose(): void {
      disposeTree(scene3);
      renderer.dispose();
    },
  };
}
