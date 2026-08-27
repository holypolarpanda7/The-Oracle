/** The board's stylised RESPONSE — ink, rim and grade, over the lit geometry.
 *
 *  Everything below this file is about getting the light physically right:
 *  relief high-passed out of a swatch, roughness declared per substance, a sun
 *  that casts, a hemisphere that fills, ACES rolling the highlights off. That
 *  is a *photographic* response, and it is the right substrate — but on its own
 *  it aims at a look nobody asked for. The house art direction has been Hades-
 *  adjacent since the LoRA stack was chosen (`game_config`: bold ink outlines,
 *  saturated jewel tones, dramatic rim light), and every rendered PICTURE in
 *  the game already reads that way. The board was the one surface still
 *  chasing a photograph.
 *
 *  So this is the layer that turns lit geometry into a drawing:
 *
 *    * **Ink** where the geometry breaks — a silhouette against something
 *      behind it, or a crease where two planes meet.
 *    * **Rim** on the faces turned away from the key, which is what separates
 *      a wall from the wall behind it once the palette gets saturated.
 *    * **Grade** — chroma, and a split-tone pushing shadows cool and lights
 *      warm, which is most of what a Supergiant palette is doing.
 *
 *  **Post-process rather than inverted hulls, and that is forced.** The usual
 *  cheap outline is a second copy of the mesh, scaled out, back faces drawn
 *  dark. It needs a closed SOLID, and this board is built out of single-sided
 *  SHEETS — a wall is a slab hugging one face, a floor is a quad, a roof is two
 *  pitches. A shell around a sheet is a sheet. It also draws silhouettes only,
 *  and half of what makes a room read is the crease where its own wall meets
 *  its own floor.
 *
 *  **Depth AND normals, because each misses what the other catches.** Depth
 *  alone finds a silhouette and is blind to the crease of a corner, where both
 *  planes are the same distance away. Normals alone find the corner and are
 *  blind to a wall standing in front of another wall parallel to it. The two
 *  buffers together are the whole answer, and the board pays almost nothing for
 *  the second one: terrain is merged per material slot, so the normal pass is
 *  about ten more draw calls of geometry that is already on the card.
 *
 *  **The depth threshold is RELATIVE, and it is divided by how edge-on the
 *  surface is.** An absolute threshold inks every pixel of the near ground and
 *  nothing at the back of the board. Worse, a floor running away from the lens
 *  has a large depth gradient EVERYWHERE — measured against a flat tolerance it
 *  is one solid edge, and the first version of this drew the entire floor
 *  black. What tells a receding surface from a real step is the surface's own
 *  obliquity, which the normal buffer is already carrying.
 *
 *  Grading happens in LINEAR and tone mapping happens at the very end, and
 *  that ordering is the point: three disables tone mapping when it renders
 *  into a target (`WebGLPrograms`, `currentRenderTarget === null`), so the
 *  colour buffer holds light values rather than screen values — which is the
 *  only place a saturation or a split-tone means anything. This pass therefore
 *  finishes the job an `OutputPass` would otherwise do, applying ACES and the
 *  sRGB transfer itself.
 *
 *  **`EffectComposer` was tried first and cannot do this.** It ping-pongs two
 *  render targets, so a pass that samples the DEPTH of the buffer it is
 *  reading is sampling a texture that is also the active framebuffer — WebGL
 *  says `GL_INVALID_OPERATION: Feedback loop formed between Framebuffer and
 *  active Texture` and the board comes back BLANK. Owning the targets outright
 *  is both the fix and simpler: one scene render, one normal render, one
 *  full-screen pass, and no buffer whose identity changes underneath us.
 */
import * as THREE from "three";
import { FullScreenQuad } from "three/examples/jsm/postprocessing/Pass.js";

/** Every dial the look has, in one place.
 *
 *  Numbers, not code, for the reason the shape tables are data: a look is
 *  argued about by changing values and looking, and a value that has to be
 *  found inside a shader is a value nobody tunes. */
export type BoardStyle = {
  /** Off entirely — the board is the lit geometry and nothing else. */
  enabled: boolean;
  /** How much of the ink colour an edge takes. 0 = no lines. */
  ink: number;
  /** What the ink IS. Deliberately not black: a pure black line over
   *  saturated paint reads as a sticker, and every ink this style is imitating
   *  is a very dark version of the picture's own darks. */
  inkColour: THREE.Color;
  /** Line width, in pixels of the sampled cross. Above ~1.5 the line stops
   *  being a line and becomes a bevel. */
  inkWidth: number;
  /** How big a depth break, as a fraction of the distance to it, counts. */
  depthEdge: number;
  /** How much harder a break must be to count when the two surfaces face the
   *  SAME way. 1 = no allowance, and a meadow comes back as graph paper. */
  coplanar: number;
  /** How far two normals must diverge to count as a crease. 1 - cos. */
  normalEdge: number;
  /** How much of the ink a CREASE earns, against a silhouette's full share.
   *
   *  **Zero, and that is a measurement rather than a preference.** A crease
   *  detector cannot tell "a wall meets its floor" from "a floor plate meets
   *  the skirt of the next floor plate" — both are ninety degrees — and the
   *  board's ground is a field of plates with a skirt at every seam (the
   *  `SKIRT_INSET` rule: flush faces meet). Turned on at any threshold that
   *  catches a real corner, every 5-ft square on the board gets an outline and
   *  a street comes back as graph paper. What it would have bought is the
   *  inside corner of a room, and at this camera a wall's base is already a
   *  DEPTH break, so almost nothing is lost. Kept as a dial because the answer
   *  is about this geometry, not about the idea. */
  crease: number;
  /** Strength of the backlight on faces turned away from the key. */
  rim: number;
  rimColour: THREE.Color;
  /** How tightly the rim hugs the silhouette. Higher = thinner. */
  rimPower: number;
  /** Chroma. 1 = the render's own. */
  saturation: number;
  /** Split tone: what the darks are multiplied by... */
  shadowTint: THREE.Color;
  /** ...and what the lights are. */
  lightTint: THREE.Color;
  /** Show a BUFFER instead of the board. 0 off, 1 normals, 2 distance,
   *  3 the edge mask alone.
   *
   *  Not a leftover: every one of the dials above is judged against something
   *  you cannot see, and the first version of this pass drew no lines at all
   *  while looking, from the outside, exactly like a pass that was working.
   *  A buffer nobody can look at is a buffer nobody can tune. */
  debug: number;
};

/** The shipped look.
 *
 *  Tuned against a generated board rather than against a test scene, because
 *  the thing this has to survive is 22 archetypes of wildly different value —
 *  a sunlit meadow and an unlit crypt are graded by the same numbers. */
export const BOARD_STYLE: BoardStyle = {
  enabled: true,
  ink: 0.80,
  inkColour: new THREE.Color(0x1a1410),
  inkWidth: 1.15,
  // Swept against a real generated street. At 0.018 — a plausible-looking
  // first guess — the depth test fired almost nowhere, because the tolerance
  // is a fraction of the DISTANCE and this camera stands sixty squares off:
  // it was asking for a ten-foot step before it would draw a line. 0.0008 inks
  // the seams between floor plates; 0.006 starts dropping real structure.
  depthEdge: 0.002,
  coplanar: 9.0,
  normalEdge: 0.90,
  crease: 0.0,
  rim: 0.42,
  rimColour: new THREE.Color(0xbcd2ff),
  rimPower: 3.0,
  saturation: 1.20,
  // Gentler than the first pass, and the reason is where a split tone LANDS.
  // It is a multiply, so its effect is proportional — and it is aimed at the
  // darks, which on this board is most of the ground. At (0.86, 0.90, 1.10) a
  // dungeon floor went from grey-green to a vivid cyan and the movement wash
  // over it turned garish: correct arithmetic, far too much of it. The move is
  // worth making and worth making quietly.
  shadowTint: new THREE.Color(0.93, 0.96, 1.06),
  lightTint: new THREE.Color(1.04, 1.00, 0.95),
  debug: 0,
};

/** Where the rules layer is put so the grade cannot reach it. */
const DECAL_LAYER = 1;

const INK_SHADER = {
  uniforms: {
    tDiffuse: { value: null as THREE.Texture | null },
    tDepth: { value: null as THREE.Texture | null },
    tNormal: { value: null as THREE.Texture | null },
    tDecal: { value: null as THREE.Texture | null },
    uTexel: { value: new THREE.Vector2() },
    uNear: { value: 0.5 },
    uFar: { value: 100.0 },
    uExposure: { value: 1.0 },
    uInk: { value: 0.55 },
    uInkColour: { value: new THREE.Color(0x1a1410) },
    uInkWidth: { value: 1.0 },
    uDepthEdge: { value: 0.002 },
    uCoplanar: { value: 9.0 },
    uNormalEdge: { value: 0.90 },
    uCrease: { value: 0.0 },
    uRim: { value: 0.3 },
    uRimColour: { value: new THREE.Color(0xbcd2ff) },
    uRimPower: { value: 3.0 },
    uRimDir: { value: new THREE.Vector3(-0.4, 0.35, -0.85) },
    uSat: { value: 1.28 },
    uShadowTint: { value: new THREE.Color(0.86, 0.9, 1.1) },
    uLightTint: { value: new THREE.Color(1.06, 1.0, 0.92) },
    uDebug: { value: 0 },
  },
  vertexShader: /* glsl */ `
    varying vec2 vUv;
    void main() {
      vUv = uv;
      gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
    }
  `,
  fragmentShader: /* glsl */ `
    #include <common>
    #include <packing>
    // Tone mapping only. colorspace_pars_fragment is injected into every
    // ShaderMaterial unconditionally (WebGLProgram's prefixFragment), so
    // including it here redefines LinearTransferOETF and the shader fails to
    // compile -- which is why three's own OutputPass is a RawShaderMaterial.
    // The tone-mapping chunk is injected only when the material IS tone
    // mapped, and this one deliberately is not, so that half must be asked for.
    #include <tonemapping_pars_fragment>
    uniform sampler2D tDiffuse;
    uniform sampler2D tDepth;
    uniform sampler2D tNormal;
    uniform sampler2D tDecal;
    uniform vec2 uTexel;
    uniform float uNear;
    uniform float uFar;
    uniform float uExposure;
    uniform float uInk;
    uniform vec3  uInkColour;
    uniform float uInkWidth;
    uniform float uDepthEdge;
    uniform float uCoplanar;
    uniform float uNormalEdge;
    uniform float uCrease;
    uniform float uRim;
    uniform vec3  uRimColour;
    uniform float uRimPower;
    uniform vec3  uRimDir;
    uniform float uSat;
    uniform vec3  uShadowTint;
    uniform vec3  uLightTint;
    uniform float uDebug;
    varying vec2 vUv;

    // Distance from the lens in world units, positive and growing away.
    float dist(vec2 uv) {
      float d = texture2D(tDepth, uv).x;
      return -perspectiveDepthToViewZ(d, uNear, uFar);
    }

    vec3 nrm(vec2 uv) {
      return texture2D(tNormal, uv).xyz * 2.0 - 1.0;
    }

    // ACES and the sRGB transfer, which have not happened yet: three turns
    // tone mapping off for anything it renders into a target.
    vec3 present(vec3 c) {
      c *= uExposure;
      #ifdef ACES_FILMIC_TONE_MAPPING
        c = ACESFilmicToneMapping(c);
      #endif
      #ifdef SRGB_TRANSFER
        c = sRGBTransferOETF(vec4(c, 1.0)).rgb;
      #endif
      return c;
    }

    void main() {
      vec4 src = texture2D(tDiffuse, vUv);
      // The RULES LAYER — movement range, spell areas, the aiming path. It is
      // drawn into its own buffer and composited here, AFTER everything the
      // grade does, because it is not scenery: its colours were chosen to be
      // read, and saturating them is as wrong as saturating a label. It also
      // has to be blended where it always was. The old path tone mapped each
      // material as it drew it and blended in DISPLAY space; a linear pipeline
      // blends first and maps after, which is more correct and made a
      // half-transparent blue wash come back twice as strong.
      vec4 dec = texture2D(tDecal, vUv);

      // Nothing was drawn here. The canvas is composited over the page, so the
      // background is left exactly alone — including by the edge test, or the
      // board would wear a halo where it meets the page.
      if (src.a < 0.004 && dec.a < 0.004) { gl_FragColor = vec4(0.0); return; }

      vec3 col = src.rgb;
      vec2 o = uTexel * uInkWidth;
      float d0 = dist(vUv);
      vec3  n0 = nrm(vUv);

      // Four neighbours, and each is judged on BOTH buffers at once.
      //
      // The rule that makes this work outdoors: a depth break across a surface
      // whose ORIENTATION has not changed is not a silhouette, it is the seam
      // between two plates of the same ground. The board's floor is exactly
      // that — a field of per-square plates with a skirt at every joint — so a
      // depth test alone inks a meadow into graph paper. Where the two samples
      // face the same way the jump has to be uCoplanar times bigger before
      // it counts, which leaves a real step (a ledge, a wall in front of a
      // parallel wall) inked and a five-foot seam alone.
      vec2 offs[4];
      offs[0] = vec2(-o.x, 0.0); offs[1] = vec2(o.x, 0.0);
      offs[2] = vec2(0.0, -o.y); offs[3] = vec2(0.0, o.y);

      float facing = clamp(abs(n0.z), 0.06, 1.0);
      float tol = uDepthEdge * d0 / facing;
      float de = 0.0;
      float ne = 0.0;
      for (int i = 0; i < 4; i++) {
        vec2 uv = vUv + offs[i];
        float di = dist(uv);
        vec3  ni = nrm(uv);
        float turn = 1.0 - dot(n0, ni);          // 0 same way, 2 opposed
        // Same-facing pairs need a far bigger step before they count.
        float t = tol * mix(uCoplanar, 1.0, clamp(turn, 0.0, 1.0));
        de = max(de, smoothstep(t, t * 2.2, abs(d0 - di)));
        ne = max(ne, turn);
      }
      ne = smoothstep(uNormalEdge, uNormalEdge * 1.8, ne);

      float edge = clamp(max(de, ne * uCrease), 0.0, 1.0);

      if (uDebug > 0.5) {
        vec3 dbg = vec3(0.0);
        if (uDebug < 1.5) dbg = n0 * 0.5 + 0.5;
        else if (uDebug < 2.5) dbg = vec3(fract(d0 * 0.05));
        else dbg = vec3(1.0 - edge);
        gl_FragColor = vec4(dbg, 1.0);
        return;
      }

      // RIM. Strongest at the silhouette, where the surface turns away from
      // the lens, and only on the side facing away from the key — so it reads
      // as a backlight separating one mass from the next rather than as a
      // uniform glow round everything.
      float fres = pow(1.0 - clamp(abs(n0.z), 0.0, 1.0), uRimPower);
      float back = clamp(dot(normalize(n0), normalize(uRimDir)), 0.0, 1.0);
      col += uRimColour * (fres * back * uRim);

      // GRADE, in LINEAR — the tone curve is below, not above.
      float l = dot(col, vec3(0.2126, 0.7152, 0.0722));
      col = mix(vec3(l), col, uSat);
      col *= mix(uShadowTint, uLightTint, smoothstep(0.0, 0.55, l));
      col = mix(col, uInkColour, edge * uInk);

      vec3 out3 = present(col);
      // The decal buffer is PREMULTIPLIED — it was blended over a target
      // cleared to zero — so it is undone before the curve and re-applied by
      // the blend itself.
      if (dec.a > 0.004) {
        vec3 d = present(dec.rgb / max(dec.a, 1e-4));
        out3 = mix(out3, d, dec.a);
      }
      gl_FragColor = vec4(out3, max(src.a, dec.a));
    }
  `,
};

/** What the renderer holds on to. */
export type InkPipeline = {
  render(scene: THREE.Scene, camera: THREE.Camera): void;
  setSize(w: number, h: number, pixelRatio: number): void;
  /** Objects excluded from the NORMAL pass — see `makeInkPipeline`. */
  omit: THREE.Object3D[];
  style: BoardStyle;
  apply(style: Partial<BoardStyle>): void;
  dispose(): void;
};

/** Build the chain, or return null if this context cannot support it.
 *
 *  `omit` is the escape hatch and it has one real user: the DECALS — movement
 *  washes, spell areas, the aiming path. They are transparent planes lying a
 *  hair above the floor with `depthWrite: false`, so they never reach the
 *  depth buffer and cannot make a depth edge. The normal pass has no such
 *  courtesy: an override material makes every one of them an opaque surface
 *  facing straight up, and a movement range would come back as a black outline
 *  drawn around the squares you can walk to. */
export function makeInkPipeline(renderer: THREE.WebGLRenderer,
                                style: BoardStyle = BOARD_STYLE): InkPipeline | null {
  // A look is argued about by changing numbers and LOOKING, and every dial
  // here is one somebody has to see to judge. `__ORACLE_BOARD_STYLE` lets a
  // harness set them before the page runs — the same seam
  // `__ORACLE_DEMO_SURFACES` already opens for the board's own data — so an
  // A/B is two screenshots rather than two builds. Colours arrive as anything
  // THREE.Color accepts.
  const over = (globalThis as Record<string, unknown>).__ORACLE_BOARD_STYLE;
  if (over && typeof over === "object") {
    const o = over as Record<string, unknown>;
    style = { ...style };
    for (const [k, v] of Object.entries(o)) {
      const cur = (style as unknown as Record<string, unknown>)[k];
      if (cur instanceof THREE.Color) (cur as THREE.Color).set(v as THREE.ColorRepresentation);
      else if (cur !== undefined) (style as unknown as Record<string, unknown>)[k] = v;
    }
  }
  let colour: THREE.WebGLRenderTarget;
  let normals: THREE.WebGLRenderTarget;
  let decals: THREE.WebGLRenderTarget;
  let quad: FullScreenQuad;
  let material: THREE.ShaderMaterial;
  try {
    const depth = new THREE.DepthTexture(1, 1);
    depth.type = THREE.UnsignedIntType;
    colour = new THREE.WebGLRenderTarget(1, 1, {
      depthTexture: depth, depthBuffer: true,
      // HALF FLOAT, and it is not a nicety: the sun is 2.9 and the grade
      // multiplies on top, so an 8-bit target clips every lit surface to white
      // BEFORE the tone curve ever sees it — the exact failure ACES was added
      // to fix, reintroduced one buffer earlier.
      type: THREE.HalfFloatType,
    });
    normals = new THREE.WebGLRenderTarget(1, 1);
    // Shares the scenery's DEPTH, so a movement wash is still hidden behind
    // the wall it runs behind — the decals write no depth of their own and
    // never did.
    decals = new THREE.WebGLRenderTarget(1, 1, {
      depthTexture: depth, depthBuffer: true, type: THREE.HalfFloatType });
    material = new THREE.ShaderMaterial({
      uniforms: THREE.UniformsUtils.clone(INK_SHADER.uniforms),
      vertexShader: INK_SHADER.vertexShader,
      fragmentShader: INK_SHADER.fragmentShader,
      transparent: true,
      depthTest: false,
      depthWrite: false,
      // We apply the curve ourselves, from the define below. Left on, three
      // would inject its own dispatch and the board would be tone mapped
      // twice — which reads as a washed-out board nobody can account for.
      toneMapped: false,
    });
    quad = new FullScreenQuad(material);
  } catch {
    return null;
  }

  const normalMat = new THREE.MeshNormalMaterial({ side: THREE.DoubleSide });
  const u = material.uniforms;
  u.tDiffuse.value = colour.texture;
  u.tDepth.value = colour.depthTexture;
  u.tNormal.value = normals.texture;
  u.tDecal.value = decals.texture;

  const pipe: InkPipeline = {
    omit: [],
    // Colours CLONED, not shared: `apply` copies into these, and aliasing the
    // module-level default would make one board's tuning everyone's.
    style: { ...style, inkColour: style.inkColour.clone(),
             rimColour: style.rimColour.clone(),
             shadowTint: style.shadowTint.clone(),
             lightTint: style.lightTint.clone() },
    apply(next: Partial<BoardStyle>): void {
      const s = pipe.style as unknown as Record<string, unknown>;
      // A colour arrives from a harness as whatever THREE.Color accepts, so it
      // is SET into the instance we already hold rather than replacing it —
      // which also keeps every uniform pointing at a live object.
      for (const [k, v] of Object.entries(next as Record<string, unknown>)) {
        const cur = s[k];
        if (cur instanceof THREE.Color) cur.set(v as THREE.ColorRepresentation);
        else s[k] = v;
      }
      const st = pipe.style;
      u.uInk.value = st.ink;
      (u.uInkColour.value as THREE.Color).copy(st.inkColour);
      u.uInkWidth.value = st.inkWidth;
      u.uDepthEdge.value = st.depthEdge;
      u.uCoplanar.value = st.coplanar;
      u.uNormalEdge.value = st.normalEdge;
      u.uCrease.value = st.crease;
      u.uRim.value = st.rim;
      (u.uRimColour.value as THREE.Color).copy(st.rimColour);
      u.uRimPower.value = st.rimPower;
      u.uSat.value = st.saturation;
      (u.uShadowTint.value as THREE.Color).copy(st.shadowTint);
      (u.uLightTint.value as THREE.Color).copy(st.lightTint);
      u.uDebug.value = st.debug;
    },
    setSize(w: number, h: number, pixelRatio: number): void {
      const pw = Math.max(1, Math.ceil(w * pixelRatio));
      const ph = Math.max(1, Math.ceil(h * pixelRatio));
      colour.setSize(pw, ph);
      normals.setSize(pw, ph);
      decals.setSize(pw, ph);
      // The cross is sampled in DEVICE pixels, so a line one texel wide is
      // half a CSS pixel on a 2x display — the same line thins as the screen
      // gets better, which is the opposite of what anyone wants. The width is
      // in CSS pixels and the ratio is applied here.
      (u.uTexel.value as THREE.Vector2).set(pixelRatio / pw, pixelRatio / ph);
    },
    render(scene: THREE.Scene, camera: THREE.Camera): void {
      if (!pipe.style.enabled) {
        // Everything, including anything a previous styled frame moved onto
        // the decal layer.
        for (const o of pipe.omit) o.traverse((c) => c.layers.set(0));
        (camera as THREE.Object3D).layers.enableAll();
        renderer.setRenderTarget(null);
        renderer.render(scene, camera);
        return;
      }
      const cam = camera as THREE.PerspectiveCamera;
      u.uNear.value = cam.near ?? 0.5;
      u.uFar.value = cam.far ?? 100;
      u.uExposure.value = renderer.toneMappingExposure;
      // The curve is the RENDERER's choice, read each frame rather than
      // hard-coded, so changing it in one place still changes it everywhere.
      const wantAces = renderer.toneMapping === THREE.ACESFilmicToneMapping;
      const hasAces = "ACES_FILMIC_TONE_MAPPING" in (material.defines ?? {});
      const wantSrgb = renderer.outputColorSpace === THREE.SRGBColorSpace;
      const hasSrgb = "SRGB_TRANSFER" in (material.defines ?? {});
      if (wantAces !== hasAces || wantSrgb !== hasSrgb) {
        material.defines = {};
        if (wantAces) material.defines.ACES_FILMIC_TONE_MAPPING = "";
        if (wantSrgb) material.defines.SRGB_TRANSFER = "";
        material.needsUpdate = true;
      }

      // The rules layer is separated by LAYER rather than by visibility: it is
      // rebuilt every frame, so the flag is stamped on whatever is in it now.
      // A group does not gate its children in three — the test is per
      // object — so this has to walk.
      for (const o of pipe.omit) o.traverse((c) => c.layers.set(DECAL_LAYER));
      const camLayers = (camera as THREE.Object3D).layers.mask;
      (camera as THREE.Object3D).layers.set(0);

      const prevFog = scene.fog;
      const prevOverride = scene.overrideMaterial;
      // Fog would tint the packed normals, which are not a colour.
      scene.fog = null;
      scene.overrideMaterial = normalMat;
      renderer.setRenderTarget(normals);
      renderer.setClearColor(0x8080ff, 1);        // a normal facing the lens
      renderer.clear(true, true, false);
      renderer.render(scene, camera);
      scene.overrideMaterial = prevOverride;
      scene.fog = prevFog;

      // The lit board, in linear light and with alpha, into our own buffer.
      renderer.setClearColor(0x000000, 0);
      renderer.setRenderTarget(colour);
      renderer.clear(true, true, false);
      renderer.render(scene, camera);

      // The rules layer, into its own buffer against the SAME depth — so it
      // is still occluded by the room — with the depth left exactly as the
      // scenery wrote it. Colour only: clearing depth here would let a
      // movement wash show through the wall in front of it.
      (camera as THREE.Object3D).layers.set(DECAL_LAYER);
      renderer.setRenderTarget(decals);
      const autoClear = renderer.autoClear;
      renderer.autoClear = false;
      renderer.clear(true, false, false);
      renderer.render(scene, camera);
      renderer.autoClear = autoClear;
      (camera as THREE.Object3D).layers.mask = camLayers;

      renderer.setRenderTarget(null);
      quad.render(renderer);
    },
    dispose(): void {
      quad.dispose();
      material.dispose();
      colour.dispose();
      normals.dispose();
      decals.dispose();
      normalMat.dispose();
    },
  };
  pipe.apply(style);
  return pipe;
}
