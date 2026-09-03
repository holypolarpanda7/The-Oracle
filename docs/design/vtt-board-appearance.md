# The tactical board — light, materials & ground

How the board LOOKS once its shapes are right: swatches and world uvs, shadows
and tone, the room's own ambient light, PBR channels derived from the albedo,
the ground under an object, water, scenery and re-shading in place. Split out
of `CLAUDE.md`; read this before touching `vtt/surface.py`, `vtt/decor.py`,
`vtt/water.py`, `imagery/landmark3d.py` or `activity-ui/src/lib/vttScene3d.ts`.

- **A probe that quietly shows something other than the app is worse than no
  probe.** Two of these, found by finally pointing a browser at a real
  generated board rather than at the demo's one mill room. `demo_textures`
  staged its swatches keyed by TILE CODE, and the renderer looks a material up
  by SLOT (`materialSlot(code, skin)` is `#@townhouse` wherever a skin is on) —
  so on any board with skins every square missed and fell back to its flat tile
  colour. A street came back a field of untextured dark grey and a swamp looked
  fine, purely because a swamp's codes mostly wear no skin. And the seam did
  not carry `water` at all, so a staged bog was a set of empty sunken basins:
  the geometry as it looks before the water goes back on top, which is exactly
  the thing the seam exists to let somebody look at. `board-look.mjs` is the
  harness; run `--clear` afterwards, because every other harness reads the same
  seam and a staged street has no gallery for `floors-shot` to climb.
  **Four more of them, all found by pointing that harness at the other twenty
  archetypes.** A slug is not a swatch: `material-v2-floor` holds TWELVE rows,
  one per look, and picking by slug alone staged the SKY's floor on a cave —
  resolved now the way `scene.materials_for` resolves it, and the look is
  printed. A skin is not always the archetype's, so a camp's tents (`#` wearing
  canvas inside a palisade that is `#` wearing logs) found no swatch and came
  back as dark holes; the board is asked for its own slots. `demoTileCost` read
  the mill's floor plan whatever was on screen, so the reachable wash hung in
  the OPEN SKY between two floating islands. And the mill's own people, spell
  areas, wreckage and landmark stayed at the mill's coordinates on every staged
  board — five creatures standing in mid-air off a skyship's bow. Every one of
  them reads as a bug in the BOARD rather than in the seam, which is how a probe
  wastes somebody's morning.
- **Every quad needs WORLD uvs: one unit is one square.** First found on a
  roof — the unit square is right for a floor tile, one square one repeat, and
  stretches the whole swatch across a pitch six squares long, so every roof on a
  street was a set of nested bands with the geometry correct the whole time. It
  is the same everywhere else, because "this face, whatever its size, shows
  exactly one copy" makes a sixteen-foot tower face and a four-foot crate face
  four times each other's scale and squeezes a whole swatch into the BEVEL
  strip round every block top. A flat face is read off the floor plan, an
  upright one along its own run and up its own height, so two coplanar
  neighbours continue each other.
  **And a square no longer TURNS its swatch.** `tileUVs` rotated and mirrored
  per square — eight arrangements, so the eye would not read the repeat — and
  what it bought was that nothing on the board was continuous: a tiling swatch
  is seamless with itself in ONE orientation, so a turned square meets its four
  neighbours along mismatched edges and the grid shows through as a lattice of
  chevrons over grass, mire and stone alike. On anything with a GRAIN it was
  ruinous: a caravel's deck came back as basketwork and a taproom's boarded
  walls and floor as a maze of nested outlines, which I twice mistook for
  aliasing before zooming in far enough to see the planks pointing four ways.
  The variety it gave was variety of ORIENTATION, on materials that have one.
  The cost is honest and worth stating: the repeat is now visible as a rhythm at
  five feet, where before it was visible as a lattice.
- **A board that reads as a TILE SET is a rendering problem, not a swatch
  problem.** Reported in four words — "8-bit Mario bad" — and it was three
  things, none of them the pictures.
  **Nothing cast a shadow.** A directional light with `castShadow` off draws a
  diagram of a room: nothing is attached to the ground it stands on, and every
  block is lit purely by which way it faces. The trap on the way in is worth
  more than the fix: for a FrontSide material three renders BACK faces into the
  shadow map — right for closed solids, silently wrong for everything here,
  because this board is built out of open single-sided SHEETS and the face a
  sheet turns to the sun is exactly the one that gets culled. `shadowSide =
  DoubleSide`. It is nearly free at this camera: the sun never moves and the
  board is static, so the map is rendered once per REBUILD, and panning,
  zooming and turning cost nothing. Fill light came down from 1.25 to 0.55 to
  let the shadows read, and `ACESFilmicToneMapping` went on — without it every
  colour is its raw sRGB value clipped at white, which is why lit stone read as
  paper.
  **And the picture repeated at the pitch of the grid, which is the definition
  of a tile.** A swatch is a photograph of a surface at some SCALE, and the
  scale is a fact about the picture rather than about the square:
  `surface.SURFACE_TILE_FT` says how many feet one repeat covers. A plank
  swatch shows eight boards, so five feet is a seven-inch board and right; a
  dungeon floor shows five stones across, so five feet was a ONE-FOOT
  flagstone and a great hall came back tiled in bathroom tile. Broad ground
  fifteen feet, rock in the mass twelve, a floor twelve, made things five.
  `macroAt` varies the albedo slowly over about seven squares on top, because
  real ground is not the same brightness everywhere and the eye finds a perfect
  repeat instantly. Two things measurement changed my mind about: a PATCH is
  not ground (a stand of undergrowth is three to nine squares, so a fifteen-foot
  repeat shows an arbitrary crop of a swatch with big structure in it, and the
  meadow came back strewn with pale smears), and the same substance answers
  both ways — granite is a cliff face AND a field stone — so `Skin.standalone`
  marks the skins that clothe one thing standing on one square.
  **Contact shading is the half a cast shadow cannot give you**: the inside of
  a corner is dark because most of the SKY is blocked from it, whichever way
  the sun points. An upright face darkens toward its own bottom edge (always
  true of a real wall, and it needs nothing but the quad); a floor darkens by
  how boxed-in it is, sampled at the four squares meeting at the nearest grid
  CORNER so two squares sharing an edge agree — the `corner_lift_ft` rule.
  **No standing guard for the shadows, and that is measured too**: a dark-tail
  histogram reads 16% with the sun casting and 27% with it OFF, because
  switching casting off also brightens every lit face and moves the median. It
  cannot tell a cast shadow from diffuse shading, and a check that cannot fail
  is worse than none. What DOES measure one is an A/B — shoot the board twice,
  once with `sun.castShadow` off, and difference the two — and it is written
  down in `board-look.mjs` rather than standing, because it costs a source edit
  and two builds.
- **The shadows had SHIPPED, and nobody could see them.** The A/B above is what
  found it: they were being cast, correctly, over 3% of the board at a 28%
  drop, which is a shadow you find with a difference image and not with your
  eye. Three causes, and the one usually blamed is only the third.
  **The sun was too HIGH.** At 52 degrees a ten-foot wall throws seven and a
  half feet: a band a square and a half wide lying against the wall's own foot,
  which is exactly where an isometric camera looks most steeply and sees least
  of it. At 34 it throws fifteen — three squares out across the floor, where
  the eye is. `SUN_DIR` is ONE constant now; it was written twice, once to
  point the light and once to place it, and two spellings of one direction is
  how a light ends up shading one thing and shadowing another.
  **The FILL was too strong for a shadow to read, and the display's own gamma
  makes that worse than it looks**: sRGB encoding halves every ratio, so 3:1 of
  radiance arrives on screen as 1.6:1. Hemisphere 0.55 -> 0.28 with the sun up
  to 2.9, which leaves a lit floor at almost exactly the brightness it had —
  the board is not darker, the shadows are deeper. Measured after: 8.4% of the
  board at a 32% drop.
  **And `PCFSoftShadowMap` has been a NO-OP since three 0.185**, which quietly
  downgrades it to `PCFShadowMap` with a console warning nobody reads. PCF
  takes its taps in TEXEL space, so the penumbra is a few texels wide however
  far the shadow has run — about an inch at this map density, which reads as a
  sticker cut out with scissors. VSM blurs the depth distribution, so `radius`
  is a real dial; it wants no negative depth bias of its own, since pulling the
  occluder toward the light eats the near end of every shadow.
- **The board says what the ROOM is lit like, and the renderer never asked.**
  `TacticalMap.lighting` is the AMBIENT level — what the room is like with
  nobody in it. `light_map` reads it as the floor every square starts from,
  `state()` has shipped it since boards were opened, and `vttScene3d.ts` had
  never touched the field: a crypt whose ambient is `dark` was drawn as a
  sunlit hall with a blue-grey filter over it. It only began to matter when the
  sun did — while the board was flat tinting the sun was a shading convention
  and the light MAP was the whole statement about how lit a room is. `KEY_LIGHT`
  is the table, and a key light survives at every level ON PURPOSE: form is a
  drawing convention here, like the camera, and a board lit only by flat
  ambient is the coloured cardboard this whole pass exists to get away from.
  What changes is how much and what colour — and dropping it also drops the
  board down the tone curve, where a torch's bright core stops being squeezed
  against the dim room around it in the shoulder.
  **`DIM` went 0.72 -> 0.55, and it is ONE number for two tiers because the
  RULES pair them.** A tenth of a step on screen is a bright core you find by
  knowing where the torch is rather than by looking, and dim light is a rules
  line (lightly obscured: disadvantage on anything done by sight), so drawing
  it faintly is a board declining to say something the rules say. Darkvision
  lets you treat darkness as dim light, in greyscale — so a dim square and a
  dark square seen by darkvision are the same brightness by the book, and the
  only honest difference between them is the colour.
  **The per-square STEP is not a defect to smooth away**: every boundary
  `reshade` draws is a rules line — never-seen against remembered, remembered
  against watched, bright against dim — and a 20-ft torch really is a 9x9
  SQUARE under the 5-5-5 diagonal rule. The answer to a step that reads badly
  is to make it honest and legible, never to blur it.
  **Fog, live sight and the light map are the three per-square facts a
  GENERATED board does not carry**, so every browser harness had looked at
  boards with all three off and `reshade` had never been in front of a browser.
  `demo_textures.py --dark` opens the same archetype, seed and size in a real
  engine on a scratch database, unlit and fogged, and stages what `state()`
  says — the engine's answer, not a second implementation of it in a probe.
- **A MESH cannot wear a swatch, so it takes the colour of the stuff it is made
  of.** Everything out of a FILE — a landmark, a furniture model — has no uvs,
  because the OBJ readers this project ships take `v` and `f` and nothing else.
  Drawn in the tile's flat palette colour instead (that palette is for a dark 2D
  board) a ruins board's great stone stag and its ruined arch both came back
  VIOLET, standing on the pale sandstone they were cut from; and on a TEXTURED
  square the tile colour is white, because it multiplies a picture, so a crate
  model would have been drawn as a white crate. One pixel per swatch, sampled
  once, the browser doing the averaging — and an average is exactly right, since
  a textured surface beside it lights as its own average times the same light.
- **A roof is not made of the wall.** `Skin.roof_skin`. The traced roof wore the
  building's own material so as not to invent a colour of its own, and a
  declared material is not an invented colour: a street of lime-plastered houses
  came back white walls under white roofs, one pale mass with a road through it,
  while the townhouse skin's own words have said "steep tiled roofs above" since
  the day it was written. Empty keeps the old behaviour, which is right for a
  ruin whose tiles are gone. Two notes: `roofs()` had carried an unfilled `slot`
  field since roofs were traced (the hole was already the right shape), and
  `materials_for` walks SQUARES — a roof's material belongs to no square, so
  without a pass for it the slot resolves to nothing and falls back to flat.
- **A board fought INSIDE the water gets the water back.** The geometry used to
  draw a dry seabed — open water came back a corrugated beige plain — because
  the water column was put back by the PAINTING, which is gone. It is fog,
  because a water column is what fog IS, ranged off the four corners' depths
  along the view axis so it follows the camera wherever it is turned. `state()`
  had shipped the board's MEDIUM since boards gained one and the client never
  declared it. The depth must use all THREE components of FORWARD — the lens
  looks down as well as along, and dropping the y term loses a constant the size
  of the camera's own height and draws the board as one flat slab of sea.
- **A cliff is a PRISMATOID and a tree is a crown on a trunk.** Both were
  found the long way round, arguing with a painter that kept drawing a snowy
  village on a mountain pass: four attempts from the painter's side all
  measured WORSE, and the fault was the SHAPE both times. A cliff square was a
  full-square box at one of six heights, and a field of flat-topped boxes at
  varied heights is a hill town; battered and canted with no flat top or right
  angle in plan, it is rock. Only the buried bottoms stay square, which keeps
  the merging rule that stops a rock face breaking into towers. A tree
  (`boardshapes.OBJECT_VARIANTS["T"]`, four crowns) stands 18 ft rather than 12
  because a crown needs room above head height, and its swatch is FOLIAGE not
  bark — one swatch colours the whole square, and what a square of tree shows
  an overhead camera is leaves. Painted brown they came back violet; painted
  green the forest is a forest instead of a field of sawn-off stumps.
- **A landmark may be one the DM INVENTED, and the name is its identity.** The
  catalogue is a fixed list of meshes so a model cannot ask for one nobody
  shipped — a guarantee about MESHES, which says nothing about a thing the board
  can already draw. A `landmark=` phrase the catalogue does not know becomes a
  piece with `source=None` stamping `A` (a worked object, half cover, four feet,
  breakable) at 2x2, so the DM's own gilded sow stands in the tavern named for
  it. Four rules keep it honest: a description of the ROOM is refused (the same
  resolver is fed loose place text, and invention is enabled only for an
  explicit `landmark=`); a SPECIFIC description beats a loose catalogue keyword,
  judged by how much of the phrase the matched names cover, or "a life-size
  statue of a pig" resolves to the colossal seated guardian; the placed record
  carries the NAME, because the ad-hoc register is in memory and a board
  outlives the process that drew it; and two squares rather than one, because a
  single square of a 24x18 board is a four-hundredth of the frame. `SetPiece.
  words` now actually reaches the prompt — it claimed to for months and nothing
  joined it — and a landmark gets a REGION of its own squares, which is the one
  case regional prompting is good at: a contiguous block fighting no strong
  wrong prior.
- **A landmark the DM ASKED for sweeps the scatter under it.** Only
  `terrain.DECOR_CODES` — the set a generator scatters *without* touching
  connectivity — so a sweep can never open a way through anything and a wall is
  never moved. Without it the channel failed on exactly the boards most worth a
  landmark: a 9x9 piece wants an 11x11 clearing and a ruin strewn with broken
  pillars has none. A landmark nobody asked for still takes the board as it
  finds it. Found by making `vtt_smoke` DETERMINISTIC (`random.seed`) — it drew
  a fresh layout every run, and a landmark check is a check about whether one
  fits, so it failed intermittently and would have been blamed on whatever
  change somebody was holding at the time.
- **An invented landmark can now be given a SHAPE, and the mesher is only ever
  as good as the picture.** `imagery/landmark3d.py` + `imagery/mesh_client.py`.
  The catalogue exists so a model cannot ask for a mesh nobody shipped, and a
  catalogue is a fixed list — the DM's own gilded sow was standing on the board
  as a 2x2 stamped box: mechanically exact and visually nothing. The missing
  step was never geometry; a PICTURE of the thing is something the project
  already makes for every catalogue item and every wreck, and nothing turned one
  into a shape. TRELLIS.2 does, and what comes back is a mesh like any other —
  fitted by `setpieces.mesh_fit` on the server, drawn by the isometric board,
  carrying NO mechanical content. The tiles the piece stamps stay its entire
  rules meaning. OBJ, because the readers already speak it (the browser's
  `OBJLoader`, `_obj_bounds`); a GLB would need new readers to buy nothing. Off by default
  (`ORACLE_LANDMARK_MESH`) — minutes of GPU on a card already shared with SDXL
  and the local LLM — and every failure leaves the box exactly where it was.
  **Two roots, searched COLLECTED FIRST** (`MESH_ROOT`, then `GENERATED_ROOT`,
  gitignored): a pack mesh is a modeller's answer to what the thing is, a
  generated one is a diffusion model's guess, so a catalogue entry can never be
  displaced by something this machine invented under the same slug. They serve
  over different URLs, because vite serves only `public/`.
  **The reference render is its own ImageKind, and that cost a real run to
  learn.** Drawn as a `MAP` — which is what the first version did — the gilded
  sow came back a flat heraldic emblem, because a kind carries LoRAs and
  SDXL-Battlemaps + HadesLevel@0.9 do exactly what they are for; no wording
  survives a LoRA at that strength. TRELLIS then faithfully produced a 2D emblem
  in relief: **1.00 x 0.02 x 0.95**, correct work on the wrong input, and not one
  thing in the pipeline complained. `ImageKind.MESHREF` renders it with no house
  style at all, because nobody ever LOOKS at this picture — it is an instrument
  reading, and dramatic light bakes a shadow into the geometry. `_too_flat`
  refuses a mesh whose thinnest side is under 8% of its widest, since a sheet
  standing on its edge is worse than the box it replaces. With a real
  photograph the same phrase came back a **sow on a plinth**, 1.00 x 0.44 x
  1.00, in **76 s warm** (30 s picture + 46 s mesh).
  **TRELLIS.2 is Z-UP and the board is Y-up**, and nothing in the project
  rotates anything — `SetPiece.up` is read by `mesh_fit` and by NO renderer, so
  an unrotated file arrives lying on its side, correctly scaled and silently
  wrong. `_normalize_obj` stands it up at WRITE time ((x,y,z) -> (x,z,-y), a
  proper rotation so winding survives) and strips the file to `v` and `f` —
  all three readers want nothing else, it halves the bytes, and it drops a
  `mtllib` line naming a file no route serves.
  **For an INVENTED landmark the footprint binds and the height gives way** —
  the exact reverse of the catalogue's rule, and deliberately. There, height is
  a stated fact about the fiction and fitting width to the footprint made every
  tall thing a dwarf; here BOTH numbers are defaults nobody chose for this
  thing, and the sow at nine feet tall would spill five feet onto every square
  around it, which is the picture contradicting the grid in the direction the
  `KEEP` rule exists to prevent.
  Three more things that would each have been silent: the fit measurement is
  cached but its MISSES are not — an invented piece is registered when its
  phrase is first seen, which in a fresh process is after something has already
  asked about the slug, and a remembered `None` would leave the landmark flat
  for the life of the run (`forget_mesh` drops it when a mesh lands mid-session); the file is written ATOMICALLY, because
  `_obj_bounds` measures whatever is on disk and a half-written OBJ measures,
  fits and stands the landmark at a confidently wrong size. **`Trellis2ExportTrimesh` reports `outputs: {}`** —
  measured: the file was written, the job reported success, and the history said
  nothing — so `_poll` returning None is normal and `_locate` finds the file
  under the prefix we chose.
- **A swatch is albedo, and albedo alone is a picture of stone laid FLAT on a
  shape.** `vtt/surface.py`. Every face of every block returned the same light
  for its orientation, so mortar courses, grain and pitting were painted ON the
  surface instead of being surface, and the geometry read as coloured cardboard
  however good the swatch was. Two halves, from different places, and that split
  is the design. **The RELIEF is already in the picture**: recovered by a
  HIGH-PASS, never by plain luminance — an albedo render contains the lighting it
  was made under, so luminance-as-height bakes somebody else's sun into the
  geometry. Measurable, and pinned: after the pass, detail beats low-frequency
  energy by 11x on a swatch with a gradient across it, where luminance alone
  fails the same check. **The SHINE is not in the picture at all** — contrast
  says nothing about whether stone is wet — so roughness and metalness are
  declared per SUBSTANCE, which is what a skin already is. Metalness is a switch
  and not a dial, and nothing is a mirror, because everything on a battlefield is
  dirty. **Every filter WRAPS** (`numpy.roll`): the swatch is tiled with
  `RepeatWrapping`, so a blur that clamps at the edges leaves a seam every five
  feet, in a grid, over the whole board. Derived, never stored beside the
  swatch — the `rules/components.py` doctrine one layer down — and derived on the
  SERVER for the `mesh_fit` reason. Client-side: `MeshStandardMaterial`, a
  hemisphere fill so the underside of every ledge stops going flat black, and a
  small procedural environment because **a metal with nothing to reflect renders
  BLACK**. The derived channels load as `NoColorSpace` deliberately: a normal map
  read through the sRGB curve lights as the material being subtly the wrong
  colour, which nobody can point at and everybody can see.
- **The GROUND is where a board stops making sense, and three different things
  were wrong with it.** All three read as "the painter is being silly" and none
  of them was the painter. **Scenery had no idea where it was**: `decor_for`
  drew from one pool, so a meadow got rugs, sacks and braziers — now a kind
  declares its SETTING (`built` / `paved` / `wild` / `under`; a cave has no
  furniture and no vegetation, a street is open to the sky and nobody lays a rug
  on it) and outdoor kinds exist at all (tussock, bush, deadfall, stones,
  stump). **A tile's LOOK is a skin's job, and the rules code is often right
  when the picture is wrong**: `_gen_open` scatters twenty-one `o` squares for
  cover, and `o` is a crate — mechanically exactly right, and a field strewn
  with crates. The `field-stone` skin leaves every rule alone (half cover, four
  feet, breakable) and makes them boulders. Same fix for a street's `#`
  (`townhouse`: buildings with ROOFS at 24 ft, where a 10-ft wall slab beside a
  paved strip had come back as a garden fence) and a ruin's `,` and `O`.
  **And a shape is a shape at every size**: the first outdoor scenery was built
  out of BOXES, and a meadow came back strewn with purple crates — the same
  lesson as the cliff and the crypt-of-dice, in the one place small enough to
  think it did not matter. Everything wild is a prismatoid now.
  **A swatch prompt is a POSITIVE prompt, so what a material must not be needs
  a negative** — `Skin.negative`, on top of `art.MATERIAL_NEGATIVE`. Written
  into the positive it does the opposite of what it says: the seabed swatch
  asked for "no water surface in view, no plants" and came back an aerial
  photograph of a BEACH, surf and palm fronds included.
  **An ACHROMATIC swatch is an invitation to invent a hue.** "Dressed grey
  stone" is a request for a colourless image and the model obliges; every stone
  swatch averaged to a dead, faintly COOL grey, and the painter — free to pick
  a hue where the init has none — came back with BLUE columns in a ruin and
  violet crowns on the trees before their swatch went green. Real limestone has
  a colour, so the prompt names one, and the drift stops. The lesson generalises
  past stone: a swatch that says only what a thing is made of and nothing about
  what colour it is has left the decision to the sampler.
  (`material_prerender.py --redraw <substance>` exists for this: the swatch
  PROMPT is the thing you iterate on, and before it the only way to see a
  changed one was to rename the substance in the code forever.)
  **And the rule was broken four more times in the table that states it**, one
  of them found by a player: "the tables and crates are basically the same
  colour as the road" — measured, thirteen out of 255 between a square that
  gives half cover and the roadway under it, because `wood` named its grain
  and named no colour and the sampler chose grey-green. `iron` came back steel
  BLUE, `granite` asked for "raw GREY granite" (a request for nothing), and
  `m` had no entry at all, fell through to the tile's own two words and came
  back a green mud. Wood is PINE and deliberately not oak, since
  `taproom-boards` is already a dark waxed oak floor and two woods described
  as one wood come back the same colour; foliage is darker and bluer than
  turf, since a canopy and a lawn both asked for as "green" landed eight
  apart, which is a tree granting THREE-QUARTERS cover reading as grass.
  **THE GROUND UNDER AN OBJECT IS NOT MADE OF THE OBJECT**, and that was the
  next report once the crates could be seen at all: one builder per square,
  chosen from the square's own code, with the floor fan drawn into it — so
  every crate came with a square yard of pine floor around it and every pillar
  stood on a disc of its own granite. The tile code says a crate stands here
  and says NOTHING about what it stands on, so `boardView.groundSlot` asks the
  board in two tiers. NEIGHBOURS first, because the local truth beats the
  average: a crate on a road through a meadow is on the road. Then the
  STOREY'S OWN commonest floor — and that tier is not a nicety. Measured over
  every archetype at two seeds, **28.6% of object squares have no floor
  touching them at all and 796 of those 861 are on a CLEARING**: a tree in the
  middle of a stand is ringed by trees, so every tree in every wood in the
  game was drawn standing on a square of its own foliage. With both tiers, 0
  of 3,011 fall through. Things that FILL their square are left alone — a wall
  covers its own ground, so there is nothing visible to get wrong. It lives in
  `boardView.ts` because that is the shared answer both renderers meet at, and
  being pure grid arithmetic is what makes `ground-check.mjs` possible.
  **A swatch prompt that draws a SURFACE is fragile, and the colour is the
  safest thing to change about it.** Granite took three goes: "warm pink-grey
  speckled with black mica and quartz" put the colour on the BACKGROUND and
  turned one fractured face into separate stones with gaps (a cave full of
  pink boulders), and "an unbroken granite surface filling the whole frame …
  flecked all over" came back as white terrazzo flakes at (187,188,188). What
  worked was the MINIMAL edit — keep the sentence that was already producing a
  surface and append a colour to it, nothing else.
  **`material_prerender.py --contrast` is the standing guard**: it generates
  every archetype and compares only the cover/floor pairs that ACTUALLY MEET
  on a board — crossing the catalogue with itself gives 1,176 pairs against 97
  real ones and buries them. Under 30 is worth looking at; under 14 is a
  prompt at fault, and that line sits in a measured gap between the reported
  bug at 12.7 and the closest legitimately-similar pair (a pale limestone
  pillar on sand) at 14.8. Same-substance pairs are listed apart and never
  fail — a wooden crate on a wooden deck IS one material, and geometry and
  shading are what tell those apart.
  Two more traps worth remembering: a material swatch is a sample of a SURFACE
  and whatever it averages to is where the painter starts, so asking for "weeds
  forcing up through the joints" made a green swatch and the ruin stayed a lawn;
  and `R` (rock face) is in `STRUCTURE_CODES`, so an unskinned one is drawn as a
  thin WALL panel — `DEFAULT_SKINS` gives it `boulder` everywhere, which the
  pass and the cave had been hiding by overriding it for other reasons.
- **Underwater is a COLOUR GRADE, and it is applied rather than requested.**
  Every other thing on a board is decided per square — a tile, a skin, a
  swatch — and the one thing that makes a reef read as a reef is a property of
  none of them: the water column in front of all of them. Four per-square
  levers were measured and none reached it. The `~` swatch was a picture of a
  pond SURFACE with lily leaves (right for a stream through a meadow,
  catastrophic on the 73% of a reef that wears it) and is now a genuine sea
  floor; the coral was thin vertical boxes, which is a stand of REEDS, and is
  now domes, plates and thick antlers; `_void_reads_as` was asserting "its
  surface catching the light" on a board fought INSIDE the water, and now takes
  the medium; the prompt says in as many words that the whole scene is
  submerged. The board still came back a green pond, because ninety percent of
  it is flat, and a flat green expanse in an isometric fantasy diorama is a
  pond. So `_underwater_grade` puts the water back after the render — tinted
  toward the sea's own colour, stronger and DARKER with distance (water
  absorbs; a grade that pales with distance reads as mist, which is a thing
  that happens in air). Deterministic, no GPU, and it lands on every swim board.
  **The residue was the GENERATOR, and fixing it is what actually worked.**
  `_gen_reef` laid 73% featureless shallow water and 2% coral: no cover for the
  rules, and nothing for the depth map to carry, on the archetype whose own
  description promises that sight lines die at twenty feet. It is now a sand
  SHELF (about 55%) with silt and weed over it, coral heads standing in banks
  (6-8%), and two or three channels cut clean across it at
  `REEF_CHANNEL_FT` = **-10 ft of real elevation** (17-28%). The relief is the
  point on both sides of the wire — cover and a climb to the rules, and the only
  thing a depth map can say to a painter. Channels are carved as a random WALK
  from edge to edge, because a channel is a thing water cut and a swimmer can
  follow it; blobs made ponds, which was the shape the whole reef had. Coral is
  laid FIRST and the channels cut through it. Swept 117 boards: one connected
  region for a swimmer and two spawn zones on every one.
- **`vtt/decor.py` is scenery: in the room, not in the rules.** Bones, a rug, a
  brazier — drawn by the geometry and by the depth map, invisible to movement,
  cover and sight. It exists because the visual vocabulary was capped by the
  tile taxonomy, and every new code costs rules meaning that a rug should not
  have to pay. **Nothing decorative may reach cover height**; the cap is
  asserted at import, and anything that deserves to be cover is a TILE. Placement
  is DERIVED from layout + seed (the `objects_for` precedent), the server ships
  it in `state()`, and the DM board names it as having no mechanical effect —
  unnamed, it is scenery the picture has and the board denies.
- **...and re-tinting from ONE base painted all the scenery white.** `reshade`
  rewrites every vertex colour as `base x shade`, which is right for terrain
  merged per material slot — every vertex of it is the same swatch — and wrong
  for the one builder that carries a colour PER PIECE. Bushes, tussocks,
  deadfall, stumps and stones all go in with their own tints and were
  registered with a base of WHITE, so the first shading pass painted the lot of
  it. **It had looked right for exactly one frame since fog shading went in**,
  and every board in the game was strewn with identical white blobs. A
  shadeTarget may now carry `tints`, a copy of the colours as built, and the
  shade is applied as a FACTOR instead of a replacement. Nothing was ever going
  to notice this from the inside, which is why the guard in `board-look.mjs` is
  a pixel count: near-white must stay under 0.15% of the board (it was 0.49%).
- **Fog, sight and light are re-tinted in place, never rebuilt.** They used to
  sit in the terrain cache key, so every step anyone took threw away the whole
  mesh because a torch had moved. Each vertex records its SQUARE and `reshade`
  writes colours straight into the attribute; shading only ever takes nine
  values (three visibility tiers by three light levels), so it is a table lookup
  per vertex. Instancing was considered and rejected — meshes are already merged
  per tile code, so a board is ~10 draw calls and rebuild FREQUENCY was the cost.
- **A creature walks the route the server walked.** `move_token` paths around
  walls and records the route in the event log for monsters as well as players;
  `state()["last_move"]` carries the newest one and the client animates along
  it. A straight lerp between two squares draws a creature strolling through
  masonry, which was tolerable when walls were flat shading and is not now.
