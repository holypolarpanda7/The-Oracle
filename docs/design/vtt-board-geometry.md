# The tactical board — geometry & shapes

What a square IS and what shape gets drawn for it: tile codes, skins, walls,
prismatoids, roofs, hulls, structures you can go inside, furniture models and
landmark set pieces. Split out of `CLAUDE.md`; read this before touching
`vtt/boardshapes.py`, `vtt/skins.py`, `vtt/hull.py`, `vtt/structures.py`,
`vtt/setpieces.py`, `vtt/furniture.py` or `activity-ui/src/lib/boardView.ts`.

- **Furniture is breakable, and the tile is what changes.** A pillar, door,
  crate or wall carries AC/HP (`terrain._BREAKABLE`, the project's OWN
  tuning, not a copied table). `[[VTT: damage | x,y | n | type]]` applies it;
  at 0 the tile becomes what it leaves behind, so the cover it granted and
  the way it blocked vanish with it and NOTHING else has to be told — cover,
  sight and movement all read the tile. State follows the `doors` precedent:
  terrain holds what a square IS, a JSON column holds what happened to it.
  Objects are immune to poison and psychic; material decides the rest.
- **Damage does NOT re-render the battlemap** — because nothing calls
  `render_art` on a break, not because the cache key lies. (An earlier
  attempt pinned the art to the PRISTINE layout signature; that made two
  tables who painted different furniture into the same generated room share
  one picture. The signature follows the CURRENT grid, so the key always
  matches what was drawn.) Wreckage is a small sprite instead.
- **Wreckage sprites: render large, cut, store small, PRE-render.** SDXL is
  trained at 1024, so 512-and-downscale is sharper than asking for 320 (and
  256 measured WORSE than 320). rembg (`u2netp`, Windows venv) cuts the
  background so a sprite is debris lying on the floor rather than a picture
  stuck to it; without rembg it feathers instead and still works. The whole
  catalogue is ~190 sprites (9 object kinds + 10 wreckage kinds x 10 board
  looks) — run `scripts/debris_prerender.py --render` once and a mid-combat
  smash costs a cache lookup, not a render.
  **A matte is checked, never trusted.** Told to cut stone out of a picture of
  the same stone in the same light, rembg fails three ways, and all three put
  a wrong thing on the board: it keeps everything (a pale BOX round a pillar),
  keeps nothing (an empty square where the rules say a crate is), or keeps the
  right shape at half strength (a smudge). So `art.cutout` measures what the
  matte kept and falls back to the raw render outside 8–97%, hardens soft
  alpha with a contrast curve, re-fits the subject to its square from the
  alpha's own bounding box (the model draws a crate a sixth of the frame often
  enough to matter, and how big it draws things is not steerable), and eases
  the corners so a failed cut never lands as a rectangle. Sprite prompts name
  the VIEW before the thing — "the flat circular capital of a pillar directly
  below the viewer", not "a pillar, seen from above", because the model's
  prior for any object is its elevation and a trailing modifier loses to it.
  `art.SPRITE_REV` versions the ref slug: bump it when the FRAMING changes, or
  boards keep serving sprites drawn to a framing that no longer matches.
- **A door belongs to the wall it interrupts.** Two consequences, both in
  code: `mapgen._door_on_wall` punches ALL the way through — most generators
  fill the grid with wall and carve a room inside it, so setting one square of
  the room's own ring to a door left it opening onto solid rock, an alcove
  that read as a door standing in a wall for no reason. And a door is DRAWN as
  a thin panel lying along its wall run (`terrain.aperture_axis` reads the
  direction off the grid; `render_image._paste_panel` draws it, jamb ticks and
  all), never as a picture filling its square — square and centred it reads as
  furniture parked on the floor. The control image leaves an aperture's
  PASSAGE faces open and keeps its JAMBS, or a door at the board's edge gets
  sealed shut by the out-of-bounds rule.
- **Every object and every wreck is NAMED on the board.** The walls-overlay
  argument applied to sprites: the picture may be atmospheric, the word is not
  a guess, and "broken pillar" is the answer to wreckage that appears from
  nowhere. One chip per run, not per square — two crates side by side are one
  fact, and labelling both prints each over the other. Wreckage also gets a
  deterministic dark scuff under its sprite, because stone rubble on a
  flagstone floor is the low-contrast case a render can lose.
- **Every board draws the same room.** `render_image.py` (Discord PNG),
  `activity-ui/src/lib/vttScene3d.ts` (the isometric board) and
  `vttPaint.ts` (the flat canvas it is replacing) read the identical `state()`
  dict and must stay in step — objects, wreckage, panels, labels, fog tiers,
  and which tokens are visible. Sprites are matted ONCE by `art.sprite_png`
  and served to the browser over `/imagery/sprite/{id}`; a browser cannot run
  rembg, and two views cutting their own pillars differently is the same
  disagreement the grid-is-truth rule exists to prevent.
  The browser renderers meet at `lib/boardView.ts` — a `BoardView` is all
  `VttOverlay` knows, so the interaction shell, the action bar and the token
  layer are written once. **Tokens are DOM over the canvas, never painted into
  it**, which is what makes a camera-facing character free on the isometric
  board: an element over a canvas already faces the viewer, so a billboard
  needs a projection (`BoardView.screenOf`) and no character art at all.
  `vttPaint.ts` and `canvasBoardView.ts` are a deliberate fallback for a
  webview with no usable WebGL, and retire together once the isometric board
  reaches parity.
- **Being DOM is also why OCCLUSION has to be computed.** An element over a
  canvas is in front of the room by construction, so no depth test will ever put
  a creature behind a wall — "behind" is a thing somebody has to SAY.
  `boardView.occludedAt` marches the view ray over the grid: the camera is
  orthographic and never MOVES, so for any one angle the ray back to the lens
  is a fixed direction that climbs `RAY_RISE` (tan of the pitch, which no
  amount of turning changes) per foot it crosses the floor, and a square's drawn height is the same arithmetic the geometry is
  built from. Grid, not picture — the same rule as cover and sight, and a
  depth-buffer readback would stall the frame besides. The point tested is
  the creature's CHEST: a wall that hides the boots is not worth marking, and at
  this pitch a ten-foot wall one square in front leaves exactly the head
  showing. An occluded token is drawn HOLLOW — bright rim, quiet inside — never
  hidden, because a token that vanished would be indistinguishable from a bug.
  The flat canvas reports `false` and always will; nothing on it can stand in
  front of anything.
- **Python owns the board's SHAPES; the TypeScript is generated.** Every
  silhouette the board draws — how thick a wall is, how a tent's canvas leans,
  which of four crowns a tree wears — is DATA, authored in
  `vtt/boardshapes.py` and written into `boardShapes.generated.ts` by
  `scripts/gen_board_shapes.py`. One source, one direction, a file the browser
  never edits; `--check` fails if it is stale.
  It used to be a MIRROR rather than a generator's source, and that is worth
  remembering as the thing not to rebuild: the same shapes were also
  rasterized in Python, for a depth map a ControlNet was conditioned on, and
  `iso_alignment_check.py` existed purely to catch two implementations of one
  board drifting apart. The painted layer is gone (see "The camera TURNS now"
  in `docs/design/vtt-board-rules.md`) and so is the drift it could suffer.
- **A wall is a thin skin where solid meets open floor, not a five-foot cube.**
  Drawn as a full cube it presents an enormous top face; a ring of them is a
  rim, and a rim around a floor is a TRAY, which is what every enclosed room
  read as. The square is still fully solid in the rules — the floor strip beside
  the slab is a drawing. Keying the slab on which way the wall RUNS fails: mapgen
  walls are commonly two squares thick, so every square reads as a corner and a
  band of pluses notches at every seam. Draw the FACE, and let buried squares
  draw nothing.
- **Never vary a height the RULES quote.** Per-instance jitter gives walls,
  pillars and trees a little life, but `cover_height_ft > 0` marks the tiles
  whose height IS the answer — a crate screens four feet, a low wall three — and
  those are drawn exactly. A player deciding whether they can break line of
  sight reads it off the board, and against a stronger enemy that decision is
  most of the fight; a crate drawn shorter than its neighbour invents a
  difference the engine will not honour, exactly where being misled is expensive.
- **A tile code says what a square DOES; a SKIN says what it is MADE OF.**
  `vtt/skins.py` is the same split `placelore.py` makes between the land a
  place stands in and the surface it presents, one level down. Before it a
  `#` was the same dressed masonry in a sewer, on a mountainside and around a
  tent, so the pass read as a built corridor and the reef's columns stood
  pristine and carved on the seabed. More tile CODES is the wrong fix and the
  project already rules it out — a cliff and a wall are not different rules,
  they are different stuff. A skin carries a substance (one swatch shared by
  every coral thing everywhere), a silhouette, and what to tell the painter.
  It is invisible to every rule, and that is what makes the vocabulary safe to
  grow. Derived from the ARCHETYPE where it is uniform, recorded per square
  (`GeneratedMap.skins`, sparse, like elevation) where a generator built an
  exception. **A skin's silhouette wins over everything including the
  wall-face model** — that model exists to stop an enclosed ROOM reading as a
  tray, which is the wrong worry for a mountainside. **It may reshape a height
  the rules quote but never restate one**: `_check_heights` refuses at import,
  and caught three on the first run (a ship's rail is three feet whether it is
  timber, brass or grown chitin). A skin that only says what the model would
  paint anyway is not free — `masonry` on the dungeon boards bought nothing
  and was removed.
- **The prismatoid existed for a year and almost nothing used it.** Measured:
  **106 of 178 skin parts were still boxes**, every single piece of FURNITURE
  was boxes (altar 5, table 8, crate 8, low wall 2, zero prismatoids between
  them), and the pillar was `prism(cx, cz, 0.32)` written by hand in BOTH
  renderers and in neither table — the last shape in the project living where
  the generated gate could not see it, which is the exact arrangement the tree
  was moved out of for having drifted between the two languages. So the board
  read as stacked blocks not because the geometry could not do better but
  because nobody had filled the table in. Down to **25**, and the ones left are
  things that really are flat: a plank deck, a canvas plane, a rung.
  `skins.slab(...)` is the workhorse — a box with `chamfer` (top plan pulled
  in) and `batter` (bottom plan pulled in), both zero by default so it degrades
  to exactly the box it replaces. What it buys is that **the only thing that
  says "worked" rather than "block" at this camera is the line where two planes
  of slightly different size meet**: a crate's lid proud of the body it closes,
  a coping that sheds, a die that leans in as it rises, a cornice that throws
  out over it. A tower's batter is a fortieth of a square over sixteen feet —
  nothing you would measure, and the difference between masonry and a carton.
  Two cases where the WORDS had been describing something the silhouette flatly
  contradicted, which is the arrangement this whole table exists to prevent: a
  taproom post whose `words` have said "chamfered" since the day it was written
  and which was a plain box, and a chitin hull whose own comment promised "no
  straight line anywhere" and whose every part was a rectangle. Round things
  are round now — logs, masts, stanchions, column drums, pipes, ladder
  rungs — because a square top face is most of what this camera sees, and it
  says "sawn" however loudly the prompt says "split log". `ring`, `rect`,
  `inset` and `slab` live in `skins.py` beside `solid`, not in the renderer
  that happened to need them first: skins is imported BY boardshapes, so a skin
  cannot reach the other way.
- **A part may be a PRISMATOID, and that is what stops everything being a
  cube.** A part was six numbers — an axis-aligned box — so every silhouette
  was built by stacking boxes, and a slope came out as a flight of terraces.
  The second form is `(bottom polygon, top polygon, y0, y1)`, told apart in
  both languages by whether the first element is a number. It subsumes the box,
  so gaining it rewrote nothing, and it buys exactly three things: a narrower
  top is a **taper** (a tent's canvas drawn in to a ridge, a hipped roof, a
  hull with tumblehome), an OFFSET top is a **lean** (the raked legs of a
  timber watchtower, a ladder, a guy rope), and more than four vertices is a
  **cut corner**. **Winding is normalized in `skins.solid()`, never trusted** —
  a ring written the wrong way round points its normals into the solid, so the
  renderer culls every face you should see; the first watchtower roof simply
  did not appear and nothing in either program looked broken.
- **There are three orientation rules, and a tent needed the third.** `yaw_of`
  turns a boulder any way at all; `run_axis` lines a rail up with its run; and
  `out_axis` points a part at the OUTDOORS. Without the last, a tent's canvas
  could only lean the same amount toward both faces of its wall — which against
  a five-foot square and a seven-foot tent is not leaning at all, and is why
  three rewrites of the tent still came back as pens. A doorway counts as part
  of the wall it interrupts (the project's existing rule about doors), or a
  tent takes its own flap for the weather and pitches the roof at the way in.
  **An outward skin does not ROLL for its arrangement**: 0 is the plain run and
  1 is the CORNER (`out_corner`), because four of the twelve squares in a
  tent's ring face the weather on two sides at once and a shape aimed at one of
  them leaves the other a sheer face — two pitched sides and two cliffs.
- **A THIN wall is not the skin of a mass.** `wall_parts` draws a slab hugging
  each open side, which is right for the face of a thick band and wrong for a
  wall with two faces: hugging BOTH sides of a one-square bulkhead draws two
  slabs with a slot down the middle, which is how a ship's deckhouse came back
  with double walls and a corridor between them. Two or more open sides means a
  thin wall, and a thin wall is drawn on its CENTRELINE — a hub plus an arm
  toward each square it carries on into — so it is one wall, mitres its own
  corners, and stops cleanly at a stub end. It keeps less top face than a full
  cube, so the tray this rule exists to prevent stays prevented.
- **A MASS picks its shape from a coarse hash; an OBJECT picks per square —
  and a boulder is an OBJECT.** `variant_smooth` samples over two-square
  blocks. Only rock bordering open floor is drawn, so a one-square shell whose
  every square chose its own height has nothing left to connect it. Three more
  things the mountain pass needed, all measured: the shell must be judged on
  all EIGHT neighbours (`boardshapes.exposed`), because a wandering track steps
  diagonally as often as squarely and a square whose only open neighbour was a
  diagonal was drawn as buried, notching the face into separate towers; the
  blocks must overhang their squares by about a tenth, because two diagonal
  neighbours otherwise meet at a single point; and the pass must not sprinkle
  its chasm through the rock at random, because a few hundred holes leave every
  remaining square bordering one and the whole face shatters. **A cliff and a
  boulder are not one skin.** Both are granite and they want opposite
  silhouettes — a cliff fills its square so neighbours merge into a face, a
  boulder stands alone and needs an outline — so sharing one drew every fallen
  stone as a full-square fourteen-foot block.
- **A ROOF is bigger than a square too, and that is why a street looked like
  huts.** The `townhouse` skin carried a gable PER SQUARE, so a terrace of
  close-packed two-storey houses came out a sawtooth of one-square huts —
  twelve little ridges over one building. No amount of shape authoring inside a
  square fixes that: what is wrong is the SIZE OF THE UNIT, which is the
  `vtt/hull.py` argument arriving from the other side. `hull.roofs` traces each
  contiguous run of a roofed skin and puts one roof on it, computed on the
  server and shipped in `state()` for the same reason a hull is — an algorithm
  over the board is the one kind of geometry two languages cannot be trusted to
  agree about. A skin opts in with `roof_ft` (how far the ridge stands above
  the eaves) and `roof_at` (where the eaves sit in its drawn height).
  **The ridge is a uniform inward OFFSET of the footprint**, which is the
  straight skeleton for any rectangle and close enough for the rest, and two
  things about it are load-bearing. The corner factor is `d / |bisector|²`,
  not `d / |bisector|` — the average of two unit normals is shorter than either,
  so a corner has to travel `d / cos` to put both edges at `d`, and getting
  that wrong by one factor left a two-square terrace with a ridge half a square
  across, which is a flat-topped slab. And **a polygon collapsed to a line is
  the ANSWER, not a failure**: that is exactly what a ridge IS over a building
  narrower than twice the inset, and over a square one it collapses to a point,
  which is a pyramid. What must be rejected is an offset that has turned itself
  inside out, told apart by asking whether any edge now runs backwards — and
  then halved and retried rather than abandoned.
  **Winding is normalized, never trusted** (the `skins.solid` rule, and it
  bites twice as hard on shipped geometry): a loop traced the other way shades
  the near pitch as though it faced away and, in the browser, culls the roof
  outright — the building comes back with no top and neither program looks
  broken. Both renderers walk the SAME cycle: eaves i, eaves j, ridge j,
  ridge i.
  **A BATTER is a property of a mass, so it cannot be per square.** The
  townhouse and tower walls were given a lean as part of the same pass and a
  terrace came back with a bright hairline slot up the face at every square
  boundary — two neighbours both tapering in leave a wedge between them. Where
  a shape belongs to something bigger than a square, the square is the wrong
  place to put it, which is the same sentence the traced roof answers.
  `scripts/demo_textures.py --board <archetype>` came out of doing this and is
  how a silhouette gets looked at: it stages a REAL generated board over the
  offline demo, so the browser can be pointed at a street or a reef without a
  backend or a session.
- **A thing bigger than a square cannot be drawn a square at a time.** A
  vessel's deck is carved out of a grid, so its outline is a staircase, and
  cutting each step's outer corner within its own square joins the steps into a
  line — a one-square line. Joining the corners FARTHEST from the hull's middle
  needs the outline as a LOOP, and no square can see one. So `vtt/hull.py`
  traces it: the boundary of each vessel-bodied region, the notch vertices
  dropped where the step is short (`MAX_SMOOTH_RUN`, or a chord swings across
  the whole waist and hangs two square yards of hull over the water), the
  triangle each dropped notch gave up kept so the deck reaches its own hull,
  and the bottom mitred round the whole loop — which per-square geometry could
  never do, because the mitre has to reach ACROSS squares.
  **It is traced once on the server and SHIPPED in `state()`**, and that is the
  point: the shape tables are data and so can be generated, the camera is
  arithmetic and so has to be gated, but this is an algorithm over the board,
  and the only way to add one without giving two languages a chance to disagree
  is to have one of them do it. Two traps, both hit: a vessel's body is full of
  HOLES (the mast's square, the cabin's, every crate) so only the outer loop is
  the hull; and a boundary can PINCH, where the walker must always turn as
  tightly as it can back toward the region or the outline breaks into
  fragments that then draw little hulls inside the deck.
  **The taper is nearly nothing (`HULL_TAPER`, a tenth of a square) and that is
  measured**: the mitre closes a corner within one square and could not reach
  across to the next, so at 0.42 every step of the bow opened a three-foot
  wedge of sky. Same lesson as `SKIRT_INSET` board-wide: flush faces meet.
  (The per-square chamfer that came first was deleted, not left standing. A
  code path nothing reaches is a trap for whoever comes next.)
- **A skyship is not a boat that happens to be up.** They shared a hull plan
  and a deck skin and came back the same vessel. A sea hull is fine at the bow,
  full through the waist and flat at the transom, and sits IN the water; an
  airship is slender and fine at BOTH ends — nothing about air rewards a
  transom — and you see its whole keel, so it gets a side half again as deep.
  `_hull(plan=)` and two deck skins.
- **A ship has a CLASS, and from the inside it has ROOMS.** `_hull` took its
  length and beam from the BOARD (`width - 2` by `height - 2`), so a two-crew
  skiff and a forty-passenger cruiser were the same outline in the same frame
  and only the water underneath told them apart. `vtt/vessels.py` is the
  missing middle: length, beam, fineness and plan per class, DERIVED from a
  catalogued vessel's own crew/passengers/cargo (the fleet is gitignored data,
  so a hull table naming those vessels could not be committed) or rolled from
  the seed. **The silhouette is what the painter is conditioned on**, so two
  classes differing only in name would be two pictures of one ship, and a
  vessel that will not fit keeps its PROPORTIONS rather than being clamped into
  the board's own rectangle. The same complaint applies inboard: `_rig_ship`
  built one deckhouse aft whatever it was rigging. **How many compartments is
  the class's business** (`HullClass.compartments`), swept from the transom
  forward one square at a time so the narrowing beam rations them; **what they
  are CALLED can only come from the caller** (`generate_map(rooms=)`, the
  `landmarks=` bargain), and for a bastion that flies the backend's
  `_bastion_rooms` supplies the facilities its owner actually bought — read
  there, not in `vtt/`, because the tactical layer must not know what a bastion
  is. A trader is nine squares in the beam and holds ONE deckhouse, so the rest
  go BELOW, divided by bulkheads that are ordinary walls with ordinary
  doorways — and a hold must be built from the whole HULL, not from walkable
  deck, or it comes out full of holes with the floor under a cabin stranded as
  an island. A room is not a rule, so it lives in `notes`; it reaches
  `state()`, the DM board, and the PNG's label chip on the drawn floor only.
- **A timber watchtower is not a stone one in wood.** Drawn as a walled shelter
  in log cladding it came back a squat box with a door in it. It is four raked
  legs holding a platform up, open underneath — `structures._post_tower` — and
  it needed no new rules primitive either: the legs are `O` pillars, which is
  what a post IS to the engine, and the ground between them stays the ground it
  was. Its footprint is ODD (five squares) because the platform and roof are
  drawn from the MIDDLE square and reach out over the rest, and only an odd
  footprint has a middle whose own centre is the structure's centre — which is
  what keeps the roof where it belongs when the square takes its quarter turn.
  Drawing them from one square is not laziness: only one storey is ever drawn
  at a time, so from the ground the platform is something you look at.
- **A hillside was a flight of stairs, because elevation is stored per SQUARE.**
  Whole feet per square drawn at one height per square is a terrace, and the
  terracing is most of what makes an outdoor board read as stacked blocks — a
  meadow with a knoll on it came out a wedding cake. `boardshapes.corner_lift_ft`
  (mirrored BY HAND in `boardView.cornerLiftFt`, and nothing compares the two —
  the alignment gate went with the painted layer, so this is one of the last
  places two languages hold one answer ungated) bends the SURFACE between square centres by averaging the shared
  CORNERS. **A corner's height must be a property of the CORNER**: anything
  that reads the asking square gives the two squares sharing an edge two
  different answers there, and the ground tears along every seam.
  Two guards keep it a drawing rather than a lie. It applies to natural ground
  only — a floor, a road, a quay and a deck are LAID, and laid things are flat
  — and **the SKIN decides, because the tile code cannot**: `.` is scree on a
  mountain pass and cobbles on a street, which is exactly the distinction a
  skin exists to make (`Skin.soft`, with `terrain.SOFT_GROUND` as the fallback
  for a square wearing none). And it only joins a difference of one STEP: a
  LEDGE is the height the rules make you decide about, and ramping one draws a
  ramp where the board says there is a fall.
  **`GROUND_RIPPLE_FT` is the other half**, and it is the `HEIGHT_JITTER`
  precedent applied to the ground: outdoor relief is mostly built from LEDGES,
  which must stay hard, so everything between them was still a dead-flat plate.
  A wander of a foot and a bit, hashed from the corner so both squares agree,
  riding on the smoothed average so it appears only where the ground was
  already allowed to slope. No rule reads it and the occlusion march never
  sees it — a creature stands at its square's stated elevation and every
  distance, cover and area check reads the integer.
- **`elevation` is DRAWN now, and was not for a long time.** It is stored per
  square, shipped in `state()`, and folded into every distance, reach, cover
  and spell-area check since the board went 3D — and neither renderer read it.
  A mountain-pass ledge stood ten feet up in the rules and flat in the picture.
  Three consequences once it is drawn: a floor ends against a LOWER square as
  well as against a hole, so a ledge gets a face; a creature rides its square's
  elevation on top of its own (a wyvern over a ledge is above both); and every
  wash, path and marker rides it too, or the movement range on a ship's deck is
  a stain on the sea beside it. `SHIP_FREEBOARD_FT` is the first deliberate
  use — a caravel's deck is six real feet above the water, so her hull is
  visible and hauling somebody out of the sea is six feet of climb in the rules
  exactly as it is in the picture.
- **Things you get INSIDE are compositions of tiles that already existed.**
  `vtt/structures.py`. A camp's tents were 2x2 blocks of impassable furniture,
  so a creature could only ever be beside one and a token on the next square
  read as a soldier standing on the canvas. A tent is a walkable floor, a wall
  ring and a flap; a watchtower is that plus a real storey and a ladder, so an
  archer on top is fifteen feet up for every distance, cover and area check.
  Nothing new was needed — `add_level`/`add_stairs` already existed, and a
  hold is the same machinery at `base_ft: -8` (a level's height is an OFFSET,
  which is why the DM board signs it). **Scale is a rules question**: nothing
  is built with an interior under 10 ft, because a 5-ft interior holds one
  creature and is a box with a hole in it. Shelters demand clear ground around
  them — two built back to back seal a pocket, and the connectivity net then
  carves a corridor through somebody's tent to reach it.
- **A tile KIND may have a MODEL, and that is where furniture-sized meshes
  stopped being forbidden.** `vtt/furniture.py`. The shape tables draw a crate,
  a table, an altar and a column out of prismatoids and that goes a long way
  for a few numbers; what they cannot do is put a handle on a barrel or a
  moulding on an altar, because none of those is a shape a rule can describe.
  A model per KIND is affordable on the sprite economics — one crate model
  serves every crate on every board in every session, nine kinds rather than
  nine hundred squares. Three rules, and the second is the one that reverses
  the old prohibition: the TILE keeps every rule; **the model is scaled to the
  height the board would have DRAWN**, so it cannot restate a height the rules
  quote (a set piece stamps its own codes and one mesh at one scale cannot
  honour a per-square quoted height — here the code is already there and the
  scale is derived FROM it); and a missing model is never an error, it is the
  prismatoids every board drew before. The fit carries no height of its own —
  `unit_scale` takes the mesh to one unit tall and the caller multiplies — so a
  quoted height stays exact and a jittered one still jitters.
  **A model wider than its own square is REFUSED, not squashed.** Measured: a
  "stack of two crates" came back 1.00 x 0.45 x 0.58 and would have stood nine
  feet across a five-foot square. Scaling it to fit would draw a crate that
  screens four feet at two, and a player deciding whether they can break line
  of sight behind it would read the wrong number off the board — so
  `MAX_SPREAD` refuses it and `--audit` says why. Every subject phrase
  therefore describes ONE upright thing of roughly square footprint, which is a
  constraint and not a style.
  **Rendering and COMMITTING are two steps on purpose.**
  `scripts/furniture_meshes.py --render` gives this installation a model;
  `--collect` is the deliberate act of putting one in the repo for everyone.
  The code can tell you a model is ILLEGAL; it cannot tell you whether one is
  any GOOD — a pedestal that is a perfectly correct altar is the wrong thing in
  a crypt full of coffins, and no measurement catches that. The committed set
  is empty until a person has looked.
- **A LANDMARK MAY NOT BE TALLER THAN THE SPACE IT STANDS IN IS WIDE.**
  `setpieces.standing_room`. Reported about one board — a forty-foot gate
  tower standing free in the middle of a twenty-five-foot carriageway, which
  is a building nobody could have built — and fixed for all of them, because
  `fits` asked for one clear square all round and one clear square is what a
  road has. The margin is derived from the piece's HEIGHT instead, measured
  across the NARROW side of its footprint since that is where a thing is
  cramped. It is a rule about the SPACE rather than about the piece, which is
  exactly why it does not belong in a per-archetype pool: the same tower is
  right at a bridgehead, in a market square and on open moor, and absurd in a
  lane or a corridor — and the twenty-one archetypes nobody had looked at get
  it for free. It changes almost nothing for pieces that were already
  reasonable (a nine-square jungle giant wants two squares where it wanted
  one; a five-square fountain still wants one; the tower goes one to three),
  and every archetype still stands its landmarks. The gate tower went back
  into the street pool once the rule existed: offered on every town, placed on
  none, and standing in the market square for a DM who asks for it by name.
- **A landmark that GROWS belongs to a latitude.** Reported by a player: a
  temperate northern wood came back with a sixty-foot PALM standing in it.
  `SetPiece.on` says what GROUND a piece may stand on and has nothing to say
  about where in the world that ground is, so the jungle giant was in the pool
  for every forest, clearing and swamp on the planet. `SetPiece.climates` is
  the declaration and `setpieces.suits_climate` the gate — per PIECE rather
  than a table somewhere central, because masonry needs none of it (a ruined
  arch is a ruined arch in the snow) and only the things that grow care.
  `forest-giant` is the same tree one band north, and it needed no new asset —
  Kenney's Nature Kit has a broadleaf beside the palm, so it is a different
  preference order, a different name and forty-five feet instead of sixty,
  because an oak is not a kapok. Lenient in the direction every gate in that
  file errs in: a piece naming no band stands anywhere and a board told no
  climate places everything, since a landmark refused for a climate nobody
  stated never appears at all. The DM's own `landmark=` is NOT filtered — the
  pool is a default rather than a permission, and somebody who narrates a palm
  in the snow has narrated a palm in the snow. `climate` reaches the board as
  an INPUT (`generate_map(climate=)`, stored in `notes`) exactly as `relief`
  does, and for the same reason.
- **Four of the world's seven climate BANDS were silently coming out
  temperate.** `geo.climate_for` derives a band from LATITUDE and produces
  arctic, subarctic, cool temperate, temperate, warm temperate, subtropical and
  tropical; `survival.weather.CLIMATES` knew three of them, and
  `climate if climate in CLIMATES else "temperate"` never complains about a
  word it has not got — so the subarctic never froze and the subtropics were
  never warm, everywhere in the world, every day of the year. The same shape as
  `TERRAIN.get(name, TERRAIN["grassland"])` costing a sea crossing as a stroll,
  in the module next door, and found only by going looking for it.
  `desert`/`coastal`/`mountain` stay in that table and are NOT bands — they are
  the TERRAIN axis, which arrived because the module was written standalone,
  and what a desert or a summit does to the sky is `placelore.WEATHER_BIAS`
  applied on top of whatever the latitude gives. `routes_smoke` now fails if
  the world can produce a band the weather model has never heard of.
- **A LANDMARK may be somebody else's mesh, and it still owns no rules.**
  `vtt/setpieces.py`. Everything else the board draws is derived from (tile
  code, skin, x, z), which works because a wall, a cliff and a hull are things
  a rule can describe — and stops working at a colossal seated guardian with a
  human face, because no hash produces one. That is a capability limit, not an
  efficiency one. A set piece is a mesh plus a footprint of tile codes it
  STAMPS, and the tiles are its entire mechanical content, so cover, movement,
  sight and breakability read the board exactly as always. Three guards fire at
  import: a square the mesh FILLS must stamp an impassable code (a picture may
  not close a square the rules leave open — `skins.occludes_floor` again); a
  passable square must declare its `elevation` or a creature stands inside the
  model; and a set piece may not stamp a code whose height the rules QUOTE,
  since one mesh at one scale cannot honour a per-square quoted height — a
  market stall was written and deleted by that rule, and furniture-sized things
  stay tiles. **Not every set piece has a mesh**: a stepped pyramid is rock
  faces and floors at a stated height, geometry the board has drawn since
  elevation went in, so `source=None` and a model of one would only be a rival
  answer to a shape the rules already fix. Enterable buildings stay in
  `structures.py`; a set piece is fought AROUND or ON, never INTO. Sources are
  **CC0 or CC-BY only** — the operative question for a public repo is
  REDISTRIBUTION, not use, and "free for personal use" fails it. The packs are
  a register in code (`PACKS`), unzipped into the gitignored `assets_src/`;
  only meshes actually used are copied into
  `activity-ui/public/assets/setpieces/` and committed, with an
  ATTRIBUTION.md **generated** from that register (`scripts/setpiece_assets.py
  --list | --audit | --collect | --attribution`) so file and code cannot drift,
  exactly as the OFL fonts do. An entry's FILE half is a search key, not a
  path — pack contents get renamed between releases — and the audit resolves it
  and reports the mesh's bounding box against the height the entry declares.
  Scale is always DERIVED, never authored — a per-pack magic multiplier is a
  number nobody can check — and **the declared HEIGHT is what it is derived
  from, with the footprint giving way.** Fitting a mesh's width to its declared
  footprint (the first rule, now reversed) made every tall thing a dwarf: a
  60-ft jungle giant came out 21 ft, a 40-ft gate tower 3 ft. The height is the
  fiction and it frames the depth map; the footprint is a floor-level rules
  statement about which squares are stamped, and nothing stops it being wider.
  Scaling stays UNIFORM — stretching one axis to satisfy both numbers distorts
  anything organic. `--audit` reports the squares a mesh NEEDS at its declared
  height, which is how a wrong ENTRY is told from a wrong footprint: a gate
  tower wanting 27x27 had matched the Castle Kit's crenellated *cap*, because
  that kit is modular and sells no whole tower. A kit sold for assembling
  buildings is `structures.py` territory; a set piece wants a pack that sells
  whole objects.
  Wider footprints then forced **`KEEP` — a square a piece RESERVES and does
  not change.** A 9x9 tree is 80 squares of ground its canopy merely hangs
  over, and stamping them repaved a meadow into flagstones: the picture
  contradicting the grid in the one direction nobody checks, because the
  terrain was RIGHT before the landmark arrived. A reserved square is still
  checked by `fits` and still kept clear of scatter.
  **`triggers.board_size_for` now grows a board for its SCENERY**, reversing
  the note that a landmark needing a bigger board is one that mostly does not
  appear — true when a footprint was a mesh's width, false once footprints were
  measured honestly. `"open"` is deliberately absent from the archetype table:
  it is the FALLBACK archetype, so giving it landmarks grew the default board
  for scenery nobody asked for (the selftest caught exactly that).
  **The mesh must turn the way the TILES turn.** `_turned` sends a footprint
  square to `(-z, x)` at 90°; three.js `rotation.y` is the other handedness and
  sends it to `(z, -x)`. Applied naively the picture rotates and the cover does
  not, and both programs look correct in isolation — so the handedness lives in
  `setpieces.rotate_xz` / `boardView.setpieceRotate`. The FIT
  (`mesh_fit`: scale + pivot) is measured on
  the server and shipped in `state()`, never recomputed in the browser: it is a
  measurement of a FILE, so the only way two languages cannot disagree is for
  one of them to do it. Both renderers stay in step — the isometric board draws
  the mesh, and the Discord PNG draws no mesh at all but NAMES the landmark,
  since its stamped tiles were always on that board already.
- **A landmark has to be ASKED for, and a place is read in three tiers.**
  `[[VTT: open | … | landmark=a stepped ziggurat]]` is the channel between the
  fiction and the catalogue, and without it the model narrated a ziggurat the
  board had never heard of — the picture flatly contradicting the prose, which
  is the one direction the grid-is-truth rule does not cover.
  `setpieces.landmark_for` maps loose words onto a slug on WORD BOUNDARIES
  (unlike `mapgen`'s archetype table, because "arch" lives inside "archer" and
  a board full of archers is what a DM describes when a fight starts);
  `board_size_for` grows the board for what was asked; `_place_setpieces`
  stands it before the archetype's own pool and exempt from the coin-flip
  rationing. The DM still chooses only WHAT — the footprint, the place and the
  fit stay the code's. The prompt lists the catalogue GENERATED from
  `landmark_vocabulary()`: a model cannot ask for a ziggurat nobody told it
  exists. Where nothing is named, the place text is read for one, so a DM who
  never learned the parameter still gets the fountain they described.
  **`archetype_for` reads medium, then architecture, then country.** A flat
  first-match list put `jungle` above `temple`, so "an overgrown temple in the
  jungle" came back a plain patch of forest — the jungle already reaches the
  render through the biome and the skins, and nothing else in the chain can put
  terraces on the board. The MEDIUM outranks both, because it decides who can
  be there at all: an underwater ruin is fought swimming whatever it is built
  of, and reading the sea first is also what keeps a "shipwreck" off a ship's
  deck (matching is by substring).
  **Placement TRIES every square in a seeded order** — forty random darts
  measured badly, since a 9x9 piece wants an 11x11 clearing, wooded boards have
  few, and a miss is indistinguishable from a board with no room. Two forests
  in three refused a pyramid that fitted; six boards in ten stood nothing at
  all. Scanning inner ground first keeps a landmark off the rim, where it fits
  most easily and is least worth having. `fits` judges clearance in the board's
  own MEDIUM (the `_connect_regions` rule), or deep water reads as "something
  already standing here" and a wreck may not lie in the sea — and an `on` list
  that forgets the archetype's actual floor is the same bug quietly: rubble
  kept every temple piece off the RUINS boards.
