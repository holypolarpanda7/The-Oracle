# The tactical board — rules & generation

What the board MEANS mechanically: picking, cover, the cutaway, the camera,
fog/sight/light, elevation, upper floors, awareness, movement and forced
movement, board sizing, layout generation and connectivity, and the art
conditioning that keeps the picture from contradicting the grid. Split out of
`CLAUDE.md`; read this before touching `vtt/geometry.py`, `vtt/mapgen.py`,
`vtt/scene.py`, `vtt/triggers.py`, `vtt/bridge.py` or `vtt/art.py`.

- **A click lands on the square you are LOOKING at, not on the ground plane
  under it.** `squareAt` unprojected the pixel onto the storey's floor, which
  answers "which square would be here if the board were flat" — and it has not
  been flat since elevation went in. On the demo board's ten-foot dais the
  plane was wrong by **two squares** on every probe, and the error grows with
  the height, which is exactly backwards: the whole point of high ground is
  that people stand on it. A player reported it as "I need to click on the 2d
  mesh location". `boardView.squareUnderRay` is the `occludedAt` march run the
  other way — every point of the form `(gx + rayX*u, u*rayRise, gz + rayZ*u)`
  projects to the same pixel, so walking `u` down from above and asking each
  square how tall it is DRAWN finds the first surface the ray meets. Drawn, not
  solid: **you pick what you can see**, so a click on what looks like the top
  of a wall selects that wall, and a wall the cutaway took down stops
  swallowing clicks meant for the floor behind it. Two traps, both measured: a
  SUNKEN square is reached at a NEGATIVE `u` (beyond the ground plane, not
  short of it), so the march is bounded by the board's deepest floor as well as
  its tallest thing; and it must step a little PAST that bound, because a
  surface sitting exactly on it is only ever approached and never crossed.
  Turning the camera pivots about `groundAt` — the CONTINUOUS ground point, never
  a square — because the square under the middle of the frame legitimately
  changes as the camera comes round, and pivoting about a moving target means a
  whole turn does not come back to where it started.
- **Cover is REPORTED while you are choosing where to stand.** It has been
  computed exactly and applied correctly since the board went 3D, and the only
  place a player ever saw the word was on a foe's own line after the fact —
  reported as "cover is not obvious". `VttEngine.cover_preview` rides in
  `path_preview`, so the answer for a hovered square arrives with what the move
  costs and who it provokes. **Per ENEMY, because cover is a relationship and
  not a property of a square**: the crate that screens you from the archer on
  your left does nothing about the one on your right, and one number for the
  square would be a comfortable lie. `best`/`worst` are for a caller with one
  line to spend, and the line NAMES the foes while there are few enough to name.
  Silent when there is nobody to take cover from, and silent when the answer is
  "none" — printing that on every square teaches a player to stop reading the
  line. **A preview says WHICH square it is about** (`x`/`y`), because it
  arrives after a debounce and the pointer has usually moved on; without that
  the panel shows the cover of a square the player has already left. It went
  missing once on the way through `App.tsx`, which copies the preview field by
  field — the same shape of bug as `_cc_request`.
- **The near walls are CUT AWAY, and the rule is one sentence.** A room is a
  box and an isometric camera looks into it over a corner, so the two walls
  nearest the lens stand between the viewer and the fight, and a quarter turn
  puts what used to be the far wall across the front of the board.
  `boardView.cutAwayAt` takes them down — always, now. The rule used to be
  "exactly when you are looking at the geometry rather than at a painting of
  the room", because under a painting the wall was a thing in the picture and
  not drawing the geometry removed nothing anybody could see. There is no
  painting any more, so the geometry IS the picture at every angle, and the
  walls come down to a STUB, never to nothing: a floor with no edge at all
  looks like it is hanging in space. **The consequence, and the harnesses
  assert it: a near wall no longer hides anybody.** What occludes is furniture,
  raised ground, upper storeys and landmark meshes.
  **And "in the way" is about to change meaning.** At a fixed angle it is the
  two walls nearest the lens; with a camera the player can swing to any pitch
  it becomes whatever stands between the lens and the thing being looked at,
  which is a different question wearing the same name.
  **STRUCTURE only**, which turns out to be the same thing as "never vary a
  height the rules quote" arrived at from the other side — a crate, a low wall,
  a table and an altar are OBJECTS, and every one of them has a quoted cover
  height. A rock MASS deeper than `CUTAWAY_DEPTH` is left alone: that is the
  edge of the world, not a wall in front of the room, and slicing the top off
  it reads as a mountain someone has been at with a bread knife. The depth
  exists at all because generated walls are commonly two squares thick, so a
  one-square test cuts the inner course and leaves the outer one standing.
  **`drawnTopFt` applies the identical reduction**, so the board's account of
  who is HIDDEN follows what it drew — otherwise the cutaway reveals a creature
  the board still calls occluded, which is the picture-versus-grid
  disagreement the occlusion march exists to prevent, arriving from the other
  side. The consequence, stated rather than discovered: after a cutaway what
  hides a creature is the FURNITURE and the ground, never the room's own walls.
  Nothing about the RULES moves — a cut wall is still total cover and still
  impassable. **Everything that reads terrain and can be asked about an upper
  STOREY goes through `rowsOf`**: `scene.terrain` is the ground floor and always
  has been (`state()` repeats each floor's own inside `levels[]`), so reading it
  for a gallery cuts that gallery to the plan of the hall underneath — walls
  missing where it has them, walls standing where it does not. Survivable while
  terrain reads were only the occlusion march, which documented itself as
  ground-floor; not survivable once the cutaway started deciding what to DRAW
  from it. `drawnTopFt`, `cutAwayAt`, `occludedAt` and `squareUnderRay` all take
  the level now.
  **A ROOF COMES OFF WHEN THE NEAR WALLS DO**, and it took a player to say
  so: cutting the walls away buys nothing while the lid is on. It began to
  matter when a town became somewhere you go IN — those houses have real
  floor, a doorway and stairs, so a fight happens inside one and nobody could
  watch it. Everything else on the board already agreed you could see in:
  occlusion has never counted a roof, so a creature under one is not marked
  hidden; `squareUnderRay` has never counted one, so a click on what looks
  like a roof already selects the floor beneath it; and the rules have never
  known roofs exist. Only the drawing said otherwise. To NOTHING rather than
  to a stub — the opposite of the wall rule and for the reason that rule
  gives: a wall is stubbed because a floor with no edge hangs in space, and
  nothing hangs when a roof goes, since the walls that make a room read as a
  room are still standing. An eaves FRINGE was tried and is worse twice over
  (at the eaves line, which is 0.70 of the wall's height, the terrace stands
  proud of its own roofs; at the wall head it is a tile band hanging over the
  stubs the cutaway just made). `hull.roofs` decides with `hollow`, asked of
  what the traced outline ENCLOSES and never of the region's own squares —
  without `footprints` a region is the wall RING, so a roof reporting on its
  own masonry would leave every lone hut with its lid on, and with footprints
  the two agree, which is why looking at a street would never have caught it.
  `awayDir` is one of EIGHT directions on purpose: the exact
  direction changes with every degree of a drag and the set of squares it picks
  out does not, so the mesh rebuilds eight times in a full turn instead of on
  every frame of one.
- **The camera TURNS now, and what that cost was one thing, not three.** The
  note here used to say the camera never rotates and that offering rotation
  would cost the closed-form inverse, pan-and-zoom being a translate-and-scale,
  and a painting staying aligned — all at once. Two of the three survive: yaw is
  a PARAMETER (`project(x,y,z,yawDeg)`, `basis(yaw)` memoised per angle), and
  for any fixed yaw the projection is still a plain affine map, so picking is
  still arithmetic and pan/zoom still mean what they meant. The geometry is real
  3D and never moves; only the lens does, so `vttScene3d` needed one basis
  swap and the shape tables needed nothing. `occludedAt` takes the yaw too —
  `rayRise` is tan(pitch) and does NOT depend on yaw, only which way the ray
  runs across the floor does — and it had to start testing the NEAR edges of the
  board, which it never did when the ray could only run one way.
  **The PAINTING was the real price, and it was not worth paying.** A picture
  baked against a depth map rasterized at one angle is a photograph of the room
  from one place, and no transform makes it a photograph from another — so for
  a while the server worked at exactly one angle and the client faded the
  painting out as it turned away. That is gone: a board you can walk round is
  worth more than a picture you can only look at from one chair, and the
  painted layer took a second implementation of the whole board in Python with
  it. Off-axis or on, you are looking at the geometry, which is exactly why the
  surfaces had to learn to answer to light before turning was worth offering —
  the two changes were one change in the right order.
  `camera-turn.mjs` holds the arithmetic with no browser (canonical projection
  unchanged to the bit, every basis orthonormal, the inverse exact at every
  angle, a full turn returning exactly); `turn-shot.mjs` holds the look in a
  real WebGL context. The flat canvas answers `canTurn: false` and shows no
  control — looking straight down there is nothing a rotation would reveal.
- **The isometric camera is ORTHOGRAPHIC, and that buys two things.** The
  projection is a plain affine map, so it inverts in closed form and picking is
  arithmetic; and pan and zoom are a translate-and-scale, so one `View`
  (`scale`/`ox`/`oy`) drives both browser renderers and the camera needs no
  state of its own. It used to buy a third — a painting baked at one framing
  stayed aligned at every other framing — and that one died with the painting.
  `activity-ui/src/lib/isocam.ts` is the only place the camera is defined, and
  it is now the ONLY place — there used to be a Python mirror of it so a depth
  map could be rasterized of the same view, and that file is gone. Winding is still
  load-bearing in the mesh builder for a related reason: normals are derived
  from vertex order, so a reversed face gets a normal pointing into the block
  and the light finds nothing to catch.
- **Fog is MEMORY; sight is LIVE. Both, or a door means nothing.**
  `TacticalMap.fog` records everywhere the party has ever seen and never dims
  — right for "have we been here", useless for "can we see it now".
  `VttEngine.sight()` is the second tier, recomputed per frame from real line
  of sight (which reads `blocks_sight` off the tile, so a closed door blocks
  and an open one doesn't, for free) and shipped as `state()["sight"]`. Never
  seen renders opaque black; seen-but-not-watched gets a cold veil; only live
  sight is clear. A foe standing where nobody can see is NOT DRAWN at all —
  in either view — because fog over a token you already drew hides nothing.
  `sight()` returns None when there is no fog, and both renderers treat that
  as "everything visible".
- **The painting may not invent terrain the rules don't have.** "The art is a
  texture, the grid is the truth" settles who WINS an argument; it doesn't stop
  the argument. A model given a dungeon floorplan will paint a pool across dry
  flagstone, and then a player asks how deep it is and the DM — who only ever
  sees the grid, never the picture — says there is no water there. Nobody is
  wrong and everybody is confused. So `Grid.absent_terrain_negative()` forbids
  water/lava/chasm/ice/sky to the render unless those tiles are actually
  present (a reef keeps its water, a dungeon doesn't get any), derived from the
  grid so it can't disagree with the art cache keyed on that same grid. Fire is
  deliberately NOT on the list — negating flame takes the torches out of every
  dungeon. For whatever still slips through, the DM board tells the model that
  unlisted scenery is decoration to be narrated as shallow/dry/harmless, which
  is true of every square the legend calls open floor. Terrain that IS on the
  grid needs none of this: `legend(rules=True)` already gives the DM
  "~ shallow water (difficult, costs double; swimmable)".
- **Light is a rule, and `survival/light.py` is the ONE place it is decided.**
  `perceives(level, distance_ft, senses)` answers "can this creature make that
  out" for everything — the board's `vision()`, the DM board's text, and the
  combat engine's advantage — so they cannot drift. Before it, `can_see` was
  pure geometry: two creatures in an unlit crypt saw each other perfectly and
  `lighting` was decoration. `VttEngine.light_map()` is the spatial half:
  ambient board lighting, raised by `kind="light"` effects (radius bright,
  2x radius dim — the 5e convention already in `_SOURCES`) cast as FIELD OF
  VIEW so a torch doesn't light through a wall, then lowered by `obscured`
  effects (which existed on `MapEffect` and had never been read, so a fog cloud
  blocked nothing). Senses live in `MapToken.senses` as `{"darkvision": 60}`
  and are looked up lazily from the bestiary (`monster_slug`) or the species
  (`character_id`) when absent, so a token nobody told about darkvision still
  gets it. **`parse_senses` must handle raw book lines** —
  `{"raw": "Darkvision60ft.;PassivePerception15"}` is how a large share of the
  ingested bestiary stores it, and reading only tidy rows silently costs the
  wolf its darkvision in the direction that changes fights. The combat payoff
  reaches `_attack_advantage` through `BoardSpatial.can_see` (a callback, like
  every other board→combat channel): can't see the target = disadvantage,
  target can't see you = advantage, and in a dark room both apply and cancel —
  which is correct, and is why darkvision is worth having.
- **Almost no board is flat any more, and height is the cheapest asymmetry
  there is.** Elevation had been folded into every distance, reach, cover check
  and spell area since the board went 3D, climbing had cost a foot per foot
  since then, and a drop of 10 ft or more had been reported as a fall — and
  FOURTEEN of the twenty-one archetypes still generated a perfectly flat board,
  including the one called `mountain-pass`. The rules were ready and the
  generators were not. `mapgen` now has a height vocabulary (`_raise`,
  `_terrace`, `_mound`, and `_storey`/`_stair` for real floors) and every
  archetype but `open-water` uses it: a dais or a sunken floor in a chamber,
  tiers around the arena's pit, a rock shelf in a cave, benches on the pass,
  a knoll on open ground, hummocks in the swamp, a standing floor over a fallen
  cellar in a ruin, walkways above a sewer's channel, a rampart round a camp,
  rooms a step apart in a complex — and two real STOREYS where the fiction has
  always had one: a tavern GALLERY and a street's ROOFTOPS, both reached by
  stairs the generator lays. Two heights only: a STEP (5 ft) is cheap to climb
  and free to come down, so it shapes a fight without punishing anyone; a LEDGE
  (10 ft) is the one you have to decide about, because stepping off it is a
  fall. A ramp is only worth laying on a ledge — half of a step is two feet,
  which is a number nobody needs. The DM board now states the high ground
  (`_elevation_summary`) whether or not anyone is standing on it: it used to
  mention height only on a creature's own line, which is no help at all to a DM
  deciding whether anybody *should* take it. The selftest guards it — a
  generator may be rewritten, but not back into a table top.
- **Elevation is PER STOREY, and it was the one such fact left out.** Terrain,
  fog, live sight and light are each stored level 0 on the row and upper floors
  inside `levels`, because they are the same kind of fact about what a storey
  looks like. Elevation was a single flat map belonging to the ground — so a
  gallery could not have a step, a rooftop could not have a ridge, a hold could
  not have a platform, and **the whole height vocabulary the ground floor grew
  (`_raise`, `_terrace`, `_mound`, `_plateaus`) stopped at the stairs.** Every
  upper storey in the game was a table top by construction, which is the same
  complaint the fourteen flat archetypes answered, one floor up.
  `elevation_of(row, level)` mirrors `fog_of` exactly, and the height
  primitives all take a `level`, so the vocabulary now works on any floor.
  **A level's `base_ft` is where its FLOOR sits; its elevation is what stands
  ON that floor.** Adding them is `token_height_ft`'s job and only its job — do
  it in two places and a one-foot step on a fifteen-foot gallery is sixteen
  feet in the air. Everything that reads a height takes a level now:
  `_height_at`, `_drop_ft` (a step is WITHIN one storey; the way between floors
  is a connector), `_effect_cost_fn` (climbing a gallery's step costs what that
  step is, not what the hall below happens to have at the same coordinates),
  the spell-area origin, the cover preview, and `bridge`'s band moves.
  Two deliberate uses, and the selftest pins both. A street's **rooftops** are
  raised by RING in from the eaves — a hipped roof, not a mesa with a rim,
  which is what "raise every interior square" gives on a block whose interior
  is most of it — with a second tier only where a block is deep enough to have
  a middle, and stepping off THAT is the ten-foot fall the rules make you
  decide about. A taproom's **gallery** gets a landing at the far end from its
  stair, which is the whole asymmetry a walkway can honestly carry. The other
  guard is the invariant that catches the classic mistake: **no storey may
  carry height on a square it has no floor on** — a primitive writing the
  ground's coordinates onto a storey that is mostly open air is height on
  nothing.
- **`_plateaus` stacks a whole board, and it climbs AWAY from the camera.**
  A ledge on a flat board and a board that IS stepped are different things: the
  primitive lays two to four tiers with impassable rock between them and two or
  three RAMPS, which is what makes high ground a position to be taken rather
  than a square anyone can scramble onto from anywhere. Boundaries wander,
  because a straight terrace is a retaining wall rather than a hillside, and the
  ramps are cut before the faces are drawn so no tier is ever sealed. The
  `terraces` archetype is built entirely from it (a DM asking for a mesa, a
  plateau, an escarpment or a quarry gets one), `mountain-pass` is now genuinely
  stepped, and `open`, `clearing` and `ruins` come out fully terraced about a
  third of the time — not always, or every board is the same board.
  **Which way it climbs is a drawing decision with a reason**: the isometric
  view looks along +x and +z, so a tier rising TOWARD the viewer hides its own
  riser behind the tier in front of it. The first terraced board measured as
  perfectly flat for exactly that reason — every height was present and not one
  face was visible. Ground rises away from the camera.
- **The LLM is not the only thing that moves a creature.** The engine decides a
  BAND (`melee with Gruk` / `near` / `far`) and `bridge.apply_band_move` turns
  it into a square — and that translation read flat distance alone, so on a
  board made of ledges and terraces every monster archer stood in the mud below
  them however loudly the DM prompt talked about high ground. It now prefers
  height: strongly for a creature holding a range band (a shooter belongs up),
  and as a tiebreak among the equally-close squares when closing to melee.
  Height counts only up to one LEDGE above the mover, so a terrace two tiers up
  is never chosen for being high — it is taken a tier per turn. That is a limit
  on the PREFERENCE and not on the move: a band move is already unbounded in
  distance (`free=True, enforce_speed=False`, because the engine charged it in
  its own coarse economy and the board must not bill it twice), and making band
  moves pay real feet is a change to the engine's abstraction rather than to
  this translation.
- **Play only uses what the prompt asks for.** The board grew height and the DM
  would have kept fighting on the floor: the active-board block explained how to
  CREATE a ledge and how to rule on cover, and never once said the ground was
  worth contesting. It now gives the tactical direction outright — shooters
  climb, melee shoves them off, a hurt creature drops out of sight and takes the
  fall as the cheaper price — and `vtt_smoke` pins that a generated chamber has
  high ground AND that the DM board states it with what it costs. The offline
  demo board carries a dais for the same reason: it is the only board a browser
  can draw with no backend, and it was flat while every real board grew height.
- **A thing that is ONE thing is GROWN, not speckled.** `mapgen._blob` throws
  N independent darts inside a radius, which is right for scattered rock and
  thin scrub and wrong for anything that is a single object. Measured, it was
  wrong in three places at once and each had been that way for as long as the
  generator existed: a bog's pools had a **median size of ONE SQUARE** —
  eighty-five puddles across a 46x34 board, no water anybody could see, wade or
  swim, and a foot of basin cut under every one of them; the reef's "coral
  heads standing in banks", which its own docstring has always promised, were
  forty-odd single squares; and a kelp bed was confetti. `_patch` grows from
  the frontier instead, so every square touches another and the outline still
  wanders. Same coverage, real bodies — median clump 6-12 squares with meres up
  to 130. `_blob` stays, documented as SPECKLE, for the things that really are
  scattered. The lesson generalises past water: when a generator's own prose
  says "bank", "bed", "pool" or "stand" and the picture says confetti, the
  primitive is the thing to look at.
  **`_drifts` is the same question one level down**, about DENSITY rather than
  shape. `_scatter` decides square by square, which is right for a thing that
  IS one square — a crate, a boulder, a fallen pillar, a patch of rubble — and
  wrong for anything that GROWS. At 15% decided per square a bog came back a
  checkerboard of reed and mire with no bank anywhere, and a wood an even
  stipple of bramble with no thicket in it. Same coverage, laid in stands.
  Deliberately for PASSABLE growth only, so it needs no connectivity guard of
  its own and REFUSES a blocking code at the call: a stand of reed walls
  nothing off, and anything that could belongs in `_scatter`, which checks.
- **A pool is brim-full ONLY if this code cut the hollow.** `water.surfaces`
  filled every basin to just under its bank, which is right for a pool `sink`
  made — the sink cuts it exactly deep enough — and wrong for a bed some
  generator had already dug for another reason. A forest's stream runs along
  the floor of a five-foot GULLY, and reading the bank alone put **4.6 ft of
  water in something the description calls shallow**, hiding the relief the
  gully exists to carry. The surface is capped by the deepest square's own tile
  depth above its own bed, so a stream is two feet deep in a five-foot cut and
  a sunk pond is still full to the brim.
- **Boards were coming out in fifty pieces, and nothing said so.**
  `_connect_regions` carved exactly ONE corridor per pass and gave up after
  twelve. A clearing's ring of trees leaves dozens of four- and five-square
  pockets between the trunks, and FOUR was the fill threshold, so every one of
  them qualified for a corridor and only twelve ever got one: **fifty to
  seventy-eight regions on a finished board**, most of them unreachable. The
  "did the generator collapse" guard counts WALKABLE squares, not connected
  ones, so it passed every time. Every outstanding region is carved in ONE pass
  now — carving only ever ADDS connectivity, so the main region a later carve
  aims at is still main — and the function RETURNS whether it managed.
  `POCKET_FLOOR` is 8: two hundred square feet, room for a creature and its
  reach, which is the smallest space where anything can happen. Absolute, for
  the `PLAYABLE_FLOOR` reason. Below it a "region" is a gap in the scenery and
  a corridor to it spends a real passage on somewhere nobody will ever stand.
- **A dead pocket is filled with SOLID, and most boards had none to hand.**
  `_dominant_blocker` took the commonest impassable code, and on most outdoor
  boards the commonest impassable thing is the MEDIUM: a bridge board filled
  its pockets with CHASM (a hole, and one a flier crosses, so it filled
  nothing), a ship's deck with DEEP WATER, an open field with CRATES, and the
  open sea with a stray dungeon wall. `FILL_CODES` is derived from the tile
  table rather than listed — a fill blocks every medium, it SCREENS (half cover
  is furniture standing in a gap, not the gap closed), and it is not an
  aperture — which leaves wall, rock, tree and pillar. The fallback is ROCK
  rather than wall, because a wall is something somebody BUILT and a board with
  no solid on it is open country or open sea.
- **A LANDMARK may not seal anything off either.** It is stamped AFTER the
  connectivity net — it has to be, or the net would carve a corridor straight
  through a colossus — and that means nothing was left to notice. Measured: a
  nine-square step pyramid landed flush against the right edge of a 56-wide
  board with its way in facing off the map, and its own thirty-five-square
  interior became unreachable. `fits` demands a clear margin all round and
  SKIPS the part of that ring which is out of bounds, so the board edge had
  been standing in for clear ground — the `_road_beyond` mistake again, in a
  different file. `setpieces_for(joins=)` re-checks after stamping and UNDOES
  the placement, then tries elsewhere: a set piece is optional scenery, so
  refusing is free. The standing guard is that EVERY archetype at three sizes
  comes out one region, which is a check nothing made before and which both of
  these were hiding behind.
- **...and `_scatter` re-flood-filled the whole grid after every crate.** The
  guard is right — a scattered impassable square must not cut the board in half
  — and it was asking the question the most expensive way there is: a full
  `_regions` traversal per square laid, ~42 whole-board flood fills per street.
  `_locally_joined` settles the easy case by asking whether the square's own
  open NEIGHBOURS can still reach each other without it, inside a small budget:
  if they can, nothing that used to route through it has lost its way, so no
  split is possible. **Sound in one direction and that is all it may be** — a
  yes skips the real check, so a yes must mean yes, and it is brute-forced
  against the full scan on dense random boards in the selftest, which is the
  only honest way to pin an approximation.
  **And it has to actually ANSWER — which for a while it did not.** Written
  with a stack it is a DEPTH-first search, and on open floor a DFS wanders a
  hundred squares across the board before it comes back to the neighbour
  standing right beside where it started; the budget ran out and a single crate
  dropped in an empty room came back "not joined". Sound, useless, and
  invisible, because every answer was still right — it just fell through to the
  full scan every time. It fired on FOUR of 193 placements. Breadth-first, and
  the selftest now asks how OFTEN the fast path fires as well as whether it
  lies. street 170 -> 29 ms, ruins 143 -> 48, open 127 -> 7, and 17 ms across
  the whole catalogue; the selftest 51 s -> 25 s.
  **A set piece's own passable squares are part of the question.**
  `_locally_joined_cells` asked only about the RING around a footprint, which
  is why the pyramid case survived it: flush against the board's edge, the
  outside stayed joined all the way round and the thirty-five squares inside
  were sealed. A landmark is not a solid block — a stepped pyramid has terraces
  you walk on.
  It changes two archetypes' layouts, and the change is the FIX: the old guard
  reverted a crate whenever the BOARD had more than one region, not when that
  crate was what split it — so on a tavern or a cave that momentarily had a
  pocket somewhere else, every impassable scatter square was refused and the
  room came back with less clutter than the generator asked for. Region counts
  are unchanged or better on all 22 archetypes.
- **A board costs 9 ms now, and it cost over a hundred.** The four things
  that were expensive, in the order they were found, and none of them was the
  layout: the rotated footprint rebuilt per candidate square (`_turned`, now
  cached by slug and quarter turn); a full board flood fill after every
  scattered crate (`_locally_joined`, a bounded BREADTH-first check); the
  landmark placer walking a hundred and twenty squares to be told no
  (`_prefix`/`_count`, two summed-area tables per piece, so a hopeless spot is
  eight lookups); and `_regions` asking `passable` -> `code_cost` -> `get` ->
  `in_bounds` per square AND per neighbour, when connectivity depends on the
  code and the medium and nothing else (`_connective_codes`, a frozen set).
  **Every one is pinned as an EQUIVALENCE, not as a speed**: byte-identical
  boards and byte-identical landmark placements across all 22 archetypes, the
  connective set compared square by square against `passable`, the prefilter
  compared against `fits` on real boards, and the local check brute-forced
  against the full scan. A faster answer that is a different answer is not an
  optimisation.
- **Landmark placement was 89% of the cost of generating a board.**
  `setpieces.fits` is asked about every square, for every piece and every
  quarter turn, and it called `_turned` each time — rebuilding the rotated
  footprint strings from scratch for an answer that depends only on the piece
  and the angle. Measured on a swamp: **6.0 s of 6.75 s** over six boards, none
  of it different from the time before. Cached by `(slug, quarter turn)` rather
  than by the piece, because `SetPiece` is frozen but carries a dict and so is
  not hashable; every caller treats the result as read-only. **362 ms to 102 ms
  per board**, byte-identical output with a warm cache and a cold one.
  `terrain.code_cost` is the same move for the inner loop — a square's cost
  depends on its code and the medium and nothing else.
- **A SKY ISLAND hangs at ONE height, and it is the whole island.** The height
  was stamped as a 7x7 BOX on the middle of a round island, so a stone hanging
  twenty feet up had a square mesa on it and a rim at zero — the picture flatly
  contradicting the shape, with the rules agreeing with the picture. `_island`
  returns its squares now and all of them are raised. A knoll on top is fine,
  because it RIDES on the island's own height; two base heights in one rock is
  not, and that is what the selftest asks. **Its top being broken is NOT the
  country's business** — this was the last outdoor archetype with no relief and
  the obvious move was to hang it off `_ruggedness`, which would be the camp's
  bank all over again: an island is a torn-off chunk of rock in open air, and
  what lies a thousand feet below it shaped neither its stone nor its top. What
  it DID want was `_for_area` (more sky, more islands) and a placement that
  STOPS when nothing is far enough from its neighbours — two that merge are one
  continent with a ten-foot cliff through the middle of what the board calls
  one rock. 2 merged in 345.
- **A liquid surface is LEVEL, and water lies in a DEPRESSION.** `~` and `W`
  were on `terrain.SOFT_GROUND`, so a pool's surface was averaged with the
  ground around it and given the ordinary ripple — a swamp pool with a hummock
  beside it ran visibly UPHILL into the bank, which is the one thing water
  never does. Off the list; the `seabed-*` skins keep `soft` instead, because a
  board fought UNDER the water has no surface in view and its floor is ordinary
  ground that should roll. The skin answers first, exactly as it does for scree
  and cobbles sharing a `.`. Level is not enough on its own, though: a pool
  flush with its bank is paint on a floor. `vtt/water.py` cuts the BED into a
  basin below its own shore — deeper the further from the bank, so the shallows
  are where anyone would expect them — as real elevation in whole feet that
  every rule reads, capped under a LEDGE so walking into water is never
  reported as a fall and wading out costs the foot-per-foot the SRD charges.
  It only ever lowers a square, so a generator that already dug its channel (a
  sewer's sludge run under its walkways) keeps what it dug. `surfaces()` is the
  other half — the sheet put back on top, or the depression reads as a hole —
  and it is traced on the SERVER and shipped in `state()` for the `hull.py`
  reason: a surface belongs to the whole POOL, no square can see one, and two
  languages tracing it separately is two answers. The shore is the LOWEST dry
  square a pool touches, which is the one it would spill over.
- **A town is somewhere you go IN.** A street was a block of solid `#` with a
  roof traced over it: scenery you fought around, never in. A house is not a
  different KIND of thing from a tent — `structures.townhouse` is
  :func:`shelter` with a party wall on each side and its door on a NAMED side —
  so everything a tent already earns comes with it: the inside is real squares,
  cover and sight read the walls, and the way in is a `/` doorway the engine
  already understands. `shelter` had to learn `door_side` for it: a door is
  onto the STREET, and one rolled onto the back wall of a terrace opens into
  the neighbour's masonry. The selftest checks that every open square on a town
  board is reachable from every other, which is the check that catches exactly
  that.
  **A TERRACE IS NOT ONE BUILDING**, and two things had to say so. The roof
  tracer groups by SKIN, and every house on a terrace wears the same plaster —
  so the whole row came back under one roof, which is a warehouse.
  `hull.roofs(footprints=…)` takes the generator's own list of houses
  (`GeneratedMap.buildings`, carried into `notes` so `state()` can trace it
  again), and each gets its own. And a terrace is not one HEIGHT: houses stand
  one or two storeys taller than their neighbours, which is what per-storey
  elevation on the rooftops level is for.
  **DEPTH IS SHARED between a block's two frontages**, and getting that wrong
  is how 175 pairs of houses across 120 boards came to be built on top of each
  other: both took `min(deep, block height)` independently, so any block
  between one and two houses deep had its terraces overlap — the second house
  overwriting the first's walls, and the first's roof left traced over squares
  that were no longer there. A block up to two houses deep is split down the
  middle, back to back; a deeper one gets a terrace at each road and a YARD
  between them. **An alley is DECIDED, never discovered**: a yard with no way
  through is a sealed block, and `_connect_regions` then carves its own hole in
  somebody's wall, which reads as nothing at all — exactly as it does when it
  punches through a cliff. So the frontage is PLANNED before anything is built
  (`_plan`), and where a yard exists the narrowest house on the run gives up a
  square to buy the alley — refused if that would take it under the smallest
  house a street has, which leaves a block with no yard rather than a terrace
  of sheds.
  **A LAID floor does not ripple.** The inside of a house took the archetype's
  default for `.`, which on a street is `cobbles` — `soft` on purpose, because
  a road follows the ground it is laid over. A floor does not: somebody
  levelled the plot and laid boards on it. `house-floor` shares the taproom's
  SUBSTANCE so it costs no second swatch, with words of its own; a ruin's
  inside gets `ruin-floor`, since nothing was scrubbing those boards.
  **Frontage cannot be found by scanning.** The first version looked for runs
  of wall with a road beside them, and one alley put a road square in every
  row, so every row read as facing a street and no terrace was ever laid. The
  BLOCKS are derived from the lane positions instead — the roads are laid by
  the generator, so it already knows where they are.
  **A road is LAID and it is not FLAT.** "Laid things are flat" is about a
  FLOOR — a dungeon's flagstones, a ship's deck — where the builder levelled
  the site, and nobody levels a hillside to put a street on it. `cobbles` is
  `soft`, and a street falls across the board in ONE-FOOT steps: the smallest
  the rules have, so climbing it costs the foot per foot the SRD charges and
  nothing on the roadway is ever a drop.
  **HOW steep is the COUNTRY's business, not a die's.** The fall was
  `rng.randint(3, 8)` whatever the board said it stood in, so a town on the
  plains and a town clinging to a mountain got the same slope off the same
  roll — the `_for_area` complaint arriving from another direction, a number
  DERIVED from something the board already knows being rolled instead. The
  answer lives in `placelore.RELIEF` (see the terrain rule below), handed DOWN
  as an input the way `style` and `wanted_rooms` are; `mapgen.terrain_of` reads
  the DM's prose only for a caller that has no place — a demo, the Grounds, the
  selftest. Two dials, because "steeper" alone is the
  wrong answer for a mountain — a mountain road is steeper AND it is not a
  ramp, so it climbs, saddles and climbs again and is CANTED across its width
  as well, which is most of what makes one read as cut into a hillside. Flat
  country wants the opposite of both, and the low end of its range is ZERO: a
  street on a plain is genuinely allowed to come back level.
  The profile is a WALK toward a target curve rather than a sampling of it,
  which is what enforces `ROAD_MAX_STEP_FT` by construction — no budget and no
  amount of waviness can put a drop in a street, and a road too steep for its
  country simply arrives at the far end still climbing. **A BUILDING is level
  INSIDE**, and the steeper the street the more that matters: a house six
  squares deep on a mountain road would otherwise have six feet of fall across
  its own floor. The plot is cut and filled to one height and the difference
  lands OUTSIDE, as the step up to the door that every hill town has — allowed
  to be a step, never allowed to be a fall.
  The same treatment reaches `ruins`: one standing building in three, with a
  doorway and sometimes a floor still up. A ruin drawn only as broken outlines
  is walls to run between and never anything to be inside, and the survivors
  are what make the outlines read as ruins.
- **A bigger board is MORE OF THE PLACE, not a bigger place.** Boards are
  roughly **four times the area** they were (combat 24x18 -> 46x34, which is
  230 by 170 feet), because a 120-ft board is enough to stand and trade blows
  on and not enough to skirmish, break off or kite: a longbow reaches 150 feet,
  so backing out of reach meant backing off the map. The area is the whole
  change — **nothing was stretched to fill it.**
  A square is five feet, which makes almost every dimension on a board a real
  measurement, and that is the thing to hold: a chamber is 20-45 ft, a great
  hall 90, a roadway 25, a block of houses 40 deep, a mountain track 10-30. Hold
  the FEATURES in feet and let the COUNTS grow (`_for_area`) and doubling a
  board doubles the number of rooms; write a feature as a fraction of the board
  and doubling it doubles every room. **Measured before any of this: between
  24x18 and 48x36 the dungeon complex grew its rooms 7.5x, the taproom 5.2x,
  the crypt 4.9x.** Six halls of seventy-five by sixty feet is not a dungeon,
  and a 190-ft taproom is a barn. `_bsp_cells` stopped at `depth >= 3` — a
  fixed EIGHT cells however big the rectangle — which is the bug in one line.
  What changed, and why each is the right shape: the complex and the crypt
  split until cells are ROOM-sized; the tavern caps its taproom and puts the
  rest of the inn behind it (`_inn_rooms`); `dungeon-room` caps its great hall
  and rings it with side chambers, **unless the margin is too small to hold
  one**, in which case the hall takes it — a board that was already one room
  must stay one room; the street lays a BLOCK GRID with a real roadway and
  cross streets, and blocks are two buildings deep so nothing is left solid;
  the sewer's bore is a bore and a bigger board holds more tunnels; the camp
  pitches more tents. Open country needs no rule at all: a meadow, a forest and
  a marsh SHOULD be one region four times the size, because that is what more
  of them is.
  **The board-collapsed floor was a fraction of the area, and that is a bug
  that only appears when boards grow.** "An eighth of the board must be
  walkable" is sensible at one size and wrong at another: the walkable content
  of a corridor-shaped place grows with its LENGTH, so a 48x36 mountain pass
  producing four times the track was condemned for not producing eight times —
  and silently replaced with a MEADOW, because the fallback is a real board.
  `PLAYABLE_FLOOR` is absolute. The guard is
  `selftest`'s "a big X is no WIDER than it was, only longer": the widest clear
  span of a built place, which is the one measure that works for a room and a
  roadway alike — a street is legitimately one long thin rectangle and a hall
  is not. Verified to fail by exactly that check with the old room sizing
  restored.
- **A board is sized by the FIGHT, not by one number.** `triggers.board_size_for`
  starts from the scene kind's default (`DEFAULT_SIZE`: combat 24x18, explore
  30x24, chase 34x14…) and grows it for room to STAND (total footprint on the
  board), room to MOVE (the fastest creature present — three moves to cross, so
  a charge is a decision rather than the whole encounter) and room to SHOOT
  (`VttConfig.outdoor_range_ft`, 150 by default). The last two apply OUTDOORS
  only: a tavern is the size of the tavern, and being outranged indoors is what
  a building is, not a sizing bug. `SCALES` (duel/skirmish/battle/pitched/
  mounted) let a DM force it via `[[VTT: open | … | scale=mounted]]` or
  `size=WxH`, for the charge that starts with two riders. Two rules were
  UNREACHABLE before this and are the reason it exists: a dashing warhorse
  crossed the whole 120-ft board in one turn, and a longbow's 150-ft normal
  range was longer than the battlefield, so long-range disadvantage could never
  fire. `_vtt_open` used to pass `default_width/height` unconditionally, which
  overrode the per-kind table and made every board 24x18 including exploration
  ones — pass width/height only when someone actually asked for a size.
  `bridge.roster_for` builds the (size, speed) list, because a Combatant row
  carries neither and the bridge is already where the rules library lives.
- **Upper floors are grids, and the only new rule is the CEILING.**
  `TacticalMap.levels` holds one terrain grid per storey with its own
  `base_ft`; level 0 is the board's own `terrain`, so a single-storey board
  carries nothing and every caller written before floors existed keeps working
  (`grid_of(row, level=0)` is defaulted, not required). This was cheap for one
  reason: **height was already folded into every distance, reach, cover check
  and spell area**, so `token_height_ft` adding the level's base was enough to
  make an archer on the gallery 20 ft away rather than standing on your head.
  What genuinely needed writing: `_occupied` is per floor (two creatures share
  an x,y on different storeys and are not in each other's way); a new level
  starts as ALL VOID because a gallery is the strip you build and everywhere
  else is open to the hall below; and a VOID square is what sight passes
  through — anywhere else there is a floor between you, which is the one thing
  an upper storey adds that height alone never expressed. Levels are otherwise
  sealed: `add_stairs` links a square to a square, both ways, and
  `take_stairs` is the only way across. Both renderers draw ONE floor (the PNG
  takes `level=`, the Activity follows your own token) because a gallery drawn
  over the hall it overlooks is unreadable. **Looking at a floor is not
  standing on it**: the Activity's floor strip lets a player peek upstairs
  before deciding to climb, marks which storey they are ON separately from
  which is DRAWN, and offers the one button that actually moves them —
  `vtt_stairs`, which the server gates exactly like a move and re-checks
  against the engine, since the client is not the authority. Connectors are
  painted on the board (▲/▼ plus the destination's name) because a player
  cannot choose a stair they cannot see. Anything belonging to a floor has to
  SAY so — `MapEffect.level` exists for that reason: peeking at the gallery is
  what made the hall's fireball burning on both storeys obvious.
  **Light, fog and live sight are per floor**, and each is stored the way
  terrain is (level 0 on the row, upper floors inside `levels`) because they
  are the same kind of fact and splitting them any other way would put two
  answers to "what does this storey look like" in two places. `light_map`,
  `light_at`, `sight` and `reveal` all take a `level`; a torch on the gallery
  does not light the hall, walking the hall does not reveal the gallery, and
  `reveal_from_party` lights the floor each creature is actually standing on.
  `state()` still ships the ground floor's flat, where it has always been, and
  repeats every floor's own inside `levels[]`.
- **A fight begins with everyone knowing something different.** Until
  `Combatant.awareness` every fight started squared up, so an ambush read
  exactly like a stand-up brawl. Three states, per CREATURE (half a camp waking
  is the interesting case): `alert` knows where you are and is the default;
  `suspicious` has heard something and, on its turn, SEARCHES rather than
  swinging at a square it has no reason to think you are in — which runs
  through the board's existing hide contest, Perception against the Stealth
  roll it already remembers, with `found_by` kept per creature; `unaware` is
  SURPRISED on round one, losing move, action and reaction entirely, and comes
  out of it **suspicious rather than blind**, because a creature that stays
  unaware after steel is drawn is a statue for the rest of the fight.
  `[[COMBAT: start | The Sleeping Camp | unaware]]` sets the opposition (the
  preset outlives the hook, since the roster arrives one `add` at a time), and
  `[[COMBAT: alert | Sentry | suspicious]]` is the one call every waking —
  a failed Stealth check, a shout, a slammed door, a spell — routes through.
  Escalation only ever goes UP: a creature that has seen you does not go back
  to wondering. **The DM sets the state and the board contests it**; the prompt
  says outright never to roll a creature's Perception by hand. "Roll
  initiative" from a player means start the fight and put the board out.
- **A jump is the one way over what you cannot walk.** Boards grew chasms,
  ten-foot channels, ledges and stacked terraces and there was no rule for
  going OVER any of it — a creature could climb at a foot per foot or walk
  round. `VttEngine.jump` uses the SRD's numbers as written: a running long
  jump clears the creature's STRENGTH SCORE in feet, a standing one half that,
  and "running" means it has already moved 10 ft this turn (checked, not
  assumed). **The squares crossed are not checked and the landing is
  everything** — that is what a jump IS — so the landing must be somewhere the
  creature could stand, empty, and no more than a high jump above the take-off:
  a ten-foot ledge is a CLIMB, and letting a hop reach it would quietly delete
  the climb rule. Landing lower reports the drop exactly as stepping off does,
  and the jump costs its own distance in movement, because a jump is movement
  and not a free way across difficult ground. Strength is read off the stat
  block or the sheet with the `senses` lazy-lookup pattern and degrades to 10.
- **A rider has no movement of their own.** `MapToken.mounted_on` names the
  mount (on the RIDER, the same shape as `grappled_by` and for the same reason:
  the carried one is whose movement stops being its own). They share the
  mount's space, so there is no second position to keep in step — `move_token`
  REFUSES a mounted creature and names the remedy, and moving the mount places
  the rider on the same square. Not the captive-dragging path: a captive is
  hauled to a square NEXT to its hauler, a rider is in the saddle. Mounting
  costs half Speed and needs an animal at least one size larger. A shoved mount
  or either of them knocked down puts the rider to a DC 10 Dex save the board
  rolls itself; failure lands them prone beside it. Placement bypasses go
  through `_place`, never `update_token` — that method refuses x/y on purpose
  so nothing sidesteps the movement rules by editing a position.
- **Squeezing is decided by the PATH, not the destination.** A Large creature
  crossing between two halls through one narrow door has a destination that
  fits perfectly; it is the way there that doesn't. So `move_token` tries the
  full-size path first and retries at one size smaller before giving up, marks
  `MapToken.squeezing`, and doubles the cost (stacking with crawling, because
  they are two separate extra feet). The flag is remembered rather than
  recomputed because `_attack_advantage` asks about it at a different moment —
  squeezing is disadvantage on your attacks and advantage on attacks against
  you, reached through `BoardSpatial.squeezing`.
- **Underwater combat is enforced, not requested.** The weapon rules used to
  live as a `dm_note` sentence in `arena/environments.py` telling the model to
  remember them by hand — the last place asking the LLM to apply a mechanic.
  `BoardSpatial.underwater()` (the board's medium is `swim`) and `swims(c)` now
  carry it to `_attack_advantage`: a melee weapon that is swung rather than
  thrust is at disadvantage for anyone WITHOUT a swimming speed, a ranged
  weapon that isn't a crossbow/net/thrown spear is at disadvantage for
  everyone, and past normal range it misses automatically (rolled and spent,
  not refused — that is what the rule says happens). Two traps: `movement_mode`
  is NOT "has a swimming speed" — on a swim board it is `swim` for everyone
  including the dwarf who is drowning, so `MapToken.swim_speed_ft` is looked up
  from the stat block the way senses are; and the weapon allowlists match by
  SUBSTRING, because the list is of weapon kinds and the table is full of
  "Trident of Warning". Spell attacks are untouched — the rule is about
  weapons, so passing no weapon skips it. **Fire resistance while immersed is
  NOT enforced**: the engine has no damage-type or resistance layer at all
  (weapons carry `"1d8"`, not a type), so the DM board states that one rule
  explicitly as the DM's to apply. Building damage typing is its prerequisite.
- **Cover is a question about HEIGHT, and the engine finally has a number for
  it.** `Tile.cover_height_ft` says how tall an obstacle SCREENS and
  `terrain.profile_height_ft(size, prone)` how tall a target presents; an
  obstacle at least as tall as the target grants TOTAL cover instead of its
  listed rating. That is not a house rule — the DMG defines total cover as
  "completely concealed by an obstacle", and lying flat behind a crate is how
  you get completely concealed by a crate. Two guards on it: the height is set
  ONLY on obstacles limited by height (crate 4 ft, low wall 3 ft, table 3 ft,
  altar 4 ft) — a pillar or tree is three-quarters because it is NARROW and
  lying down doesn't widen a trunk, a portcullis is bars and lying down doesn't
  make them opaque — and `attacker_height_advantage_ft` takes it away again, so
  you cannot lie behind a crate to dodge an archer on the gallery shooting down
  over it. Total cover then means what 5e says it means: `vision()` reports the
  target as unseeable and the combat engine already refuses the attack, so
  going prone behind low cover really does make you untargetable, and lets you
  Hide. `cover_between` with no height behaves exactly as before.
- **Hiding is a CONTEST, and it is personal.** `MapToken.hidden` used to be a
  bare bool a DM set, tied to nothing. Now the board decides ELIGIBILITY
  (`hide_eligibility`: every living enemy must either not perceive you at all,
  or be looking through three-quarters cover or better — dim light does NOT
  qualify, and an enemy with blindsight blocks the attempt no matter how much
  cover you have, because cover is protection from being *seen*), the CODE
  rolls the Stealth check (`hide`, DC 15, 2024 rules), and the result is KEPT
  as `stealth_dc` — without it every Search action is a check against nothing
  and the DM invents a DC each time. `found_by` tracks who has beaten it, so
  the guard who spotted you sees you while the rest of the room does not; one
  bool cannot say that, and `state()` filters each side's board by it. Hiding
  breaks on `unhide` (attacking, casting aloud) and by itself in `move_token`
  when the new square no longer qualifies. An enemy whose passive Perception
  already beats the roll is added to `found_by` at hide time — making them
  spend a Search action on something they could not have missed is wrong.
  Hooks: `[[VTT: hide|search|unhide]]` — **and they are in `_VTT_HOOK_ACTIONS`**.
- **A door belongs at the threshold the corridor made.** `_threshold_doors`
  hangs doors where a corridor breaches a room's wall ring; punching one into
  an arbitrary wall square leaves the corridor's own mouth gaping beside it,
  so the door guards nothing and closing it changes nothing. This only works
  because `mapgen._connective` treats a closed door as a way THROUGH for
  connectivity — judged by `passable` alone, a room behind a shut door reads
  as cut off and `_connect_regions` obligingly carves a second way in. The
  selftest asserts both halves: one connected region *granted the doors*, and
  that shutting them genuinely divides the warren.
- **The battlemap is CONDITIONED on the layout, not described to it.** A text
  prompt cannot say where a wall goes: told "a dungeon room with stone walls",
  the model paints a plausible room whose walls land nowhere near the grid's.
  That is not a tuning problem and no wording fixes it — the picture and the
  rules simply depicted different places. So `art.control_image` draws the
  grid as an architectural floorplan (white strokes on every wall FACE, gaps
  at doorways) and the render is conditioned on it through an SDXL scribble
  ControlNet (`imagery.map_controlnet`, model in ComfyUI/models/controlnet/).
  Empty config = off, and the graph is untouched. Verified by rendering the
  grid, the control image and the art side by side — see map-probe/alignment.
  Discrete objects are deliberately NOT in the control image: they are drawn
  as sprites, because a pillar that can be smashed has to be able to change.
- **Discrete objects are SPRITES, drawn from the grid on their own squares.**
  `terrain.OBJECT_SPRITES` (pillar, crate, furniture, tree, altar, low wall,
  door, open door, portcullis) — keyed by KIND, so eight pillars are one
  picture. Two reasons a painting can't do this: it cannot place anything on
  a named square, and a painted pillar cannot become rubble. Without them a
  player cannot find a door that mechanically exists, and wreckage appears
  from nowhere because the thing that broke was never visible. `objects_for`
  reads the TERRAIN each time rather than storing a list — the grid already
  says what stands where, and a broken square is no longer its object.
  Structure (walls, rock faces) is deliberately excluded: ControlNet puts
  that in the painting itself.
- **A painted board still draws the rules on top.** `render_board_png` given
  an `image_lookup` composites the battlemap and its debris — then OUTLINES
  the mechanically significant tiles, and washes only ground that stops or
  hurts you. A diffusion model cannot put a pillar on square 6,5, so the art
  and the grid disagree by design; the outline is what stops that
  disagreement reaching the players. Solid tints over every wall smother the
  picture — that was the first attempt and it was wrong.
- **SUPERSEDED — the art is NO LONGER pinned to the pristine layout.** Kept for
  the sprite economics below, which still hold. The reasoning was that the base
  art should be pinned to the
  layout as GENERATED (a pristine `layout_signature`), because the live grid
  hashes differently the moment anything breaks and would otherwise repaint
  the whole room over one square. That made two tables who painted different
  furniture into the same generated room share one picture, so the signature
  follows the CURRENT grid instead — see "Damage does NOT re-render the
  battlemap" in `docs/design/vtt-board-geometry.md`. Wreckage is a small sprite drawn on top,
  keyed by (what it became, material, board look) so it is SHARED across
  rooms. Measured warm on this rig: a 320px sprite is 6.1s against 13.5s for
  a full battlemap — ~2x cheaper, not proportional to pixels, because the
  per-call overhead dominates (256px measured WORSE than 320px). The real
  saving is the sharing, exactly as with the item-art catalogue.
- **The board is THREE-dimensional.** Elevation was stored (`elevation_ft` on
  tokens, a per-square elevation map) and measured by nothing, so a dragon
  hovering 100 ft overhead read as 10 ft away and a wyvern 60 ft up provoked
  opportunity attacks. `geo.token_distance_ft(..., dz_ft=)` folds height in
  as a third axis under the board's own diagonal rule, and
  `VttEngine.token_height_ft` / `height_gap_ft` are the ONE place height is
  decided. Every consumer passes it: measure, reach, grapple, opportunity
  attacks, spell areas (a flat template excludes what's far above its
  origin), the DM board's distances, and `bridge.BoardSpatial` — which is
  what gates the combat engine's weapon ranges. This matters most on the
  four archetypes fought off the ground (`sky-islands`, `skyship`, `reef`,
  `open-water`) and anywhere airships fly.
- **The AI never decides where a creature can be — the grid does.** The art is
  generated FROM the tile grid, so water on the picture IS water in the rules;
  `move_token` refuses a square the creature's medium forbids and there is no
  path for the model to overrule it. Two things make that legible rather than
  frustrating: the DM board's legend gives each present tile its RULE
  (`W deep water (swimmers only — a walker can't be here)`), and a refusal
  names the remedy (`…would have to swim ([[VTT: token | X | swim]])`). A
  square that DEMANDS a medium is adopted on arrival — one-way only, since
  adopting can unblock a creature but dropping one could strand a flier.
- **Battlemap art is reused until something really changed.** The bucket is
  (layout signature, biome + lighting + CONDITIONS). The signature hashes the
  tile grid, so painting one square regenerates the picture; the conditions
  carry season and precipitation, so the same room in snow earns its own art
  and the same room in the same weather reuses it. Interiors report a stable
  condition, so a taproom is never repainted for weather it can't feel — and
  time of day stays OUT of it, because `lighting` already carries it.
- **Speed 0 is a movement rule, so the BOARD enforces it.** A grappled or
  restrained token can't walk (`[[VTT: grapple/restrain]]`) — but it can still
  teleport, and it can still be shoved, because a grapple holds you rather
  than your magic. `grappled_by` names the holder, not a bare flag, because
  the grappler DRAGS their captive along at half speed. A grapple breaks by
  itself the moment the pair are out of reach, whichever of them moved.
  Prone doubles movement (crawling) and standing costs half Speed.
- **Forced movement is not movement.** `VttEngine.shove` (`[[VTT: push/pull]]`)
  ignores the target's speed, provokes NO opportunity attack, and travels a
  straight line stopping at the first obstacle — never `move_token`, which
  paths around things and charges the victim. 34 ingested features push or
  pull; before this there was no correct way to say so.
- **`_VTT_HOOK_ACTIONS` is an allowlist.** A handler with no entry there is
  silently dropped before the dispatcher ever sees it. Add both, always.
- **A creature LINK overrules the board on purpose.** `combat/bonds.py` is a
  generic three-lever link between creatures — bonus initiative dice, mutual
  sight/targeting *through* cover, and an emergency rescue — scoped to a table
  and an owner, replaced (never stacked) when re-granted. The Cartographer
  artificer's Adventurer's Atlas is one CONFIGURATION of it, not a special
  case in code; the label and the numbers come from the `[[BOND: grant]]` hook.
  Both overrides are callbacks (`VttEngine(linked=...)`,
  `roll_initiative(bonus_dice_for=...)`) so `vtt/` and `combat/` keep knowing
  nothing about any particular feature. `VttEngine.blink` is the matching
  movement: a short teleport whose long form is gated on the link, and which
  CHARGES movement (unlike `move_token(teleport=True)`, which is free).
- **"A creature you can see within range" is the board's answer, and the
  ACTION BAR is a third intent source.** `[[CAST]]` carried a name and a slot
  level and no target, so range and sight were enforced by nobody:
  `VttEngine.vision()` had been the complete "can A perceive B" answer since
  the light layer went in and no player-facing path ever called it.
  `targets_for` asks it together with range and returns EVERY creature — the
  illegal ones carrying a REASON, because a target greyed out with a stated
  cause is information and one missing from the list is indistinguishable from
  a bug. `area_preview` is the same answer for a template: the squares, who is
  caught (your own party included, before the slot is spent), and whether the
  origin is legal at all. `rules/targeting.py` reads what a spell targets out
  of the book's prose — derived, never stored, like `rules/components.py`.
  The bar (`_activity_actions` → `_board_action_plan` → `board_action`) emits
  the SAME intent dicts into the SAME `CombatEngine.resolve` that
  `_combat_preparse` and the extraction LLM do; `_combat_engine_turn(intents=)`
  just skips the parse. It is deliberately not a second resolution path — that
  would be a second set of rules to keep in step — and because skipping the
  parse is not skipping the RULES, intents arriving untrusted on the public
  `/chat` route can do no more than a typed sentence. **Enforcement is lenient
  where it cannot be honest**: no board, no token, or a name the board doesn't
  know means no refusal at all, the same direction `_material_check` errs in.
- **The tactical board is a spotlight, not a stage.** Play stays theater-of-the-
  mind; `vtt/` opens a grid only for moments where position decides the outcome,
  and closes it after. The LLM decides FICTION (a board opens here, the fireball
  lands on the altar); the code decides MECHANICS (layout, path, squares, cover).
  Never let the model author a layout or a distance. This does NOT contradict the
  "hex maps were dropped" rule below — that was a world-map render engine; this is
  an encounter-scale square grid.
- **A band change has to reach the BOARD, and a move you cannot finish still
  covers ground.** `combat/` thinks in bands and `vtt/` in squares, and
  `_do_move` used to set the band and stop — so with a spatial provider attached
  a monster that closed to melee had its swing measured from the square it
  started on and refused. Every archetype stopped resolving. It pushes the band
  to the board now (`BoardSpatial.move_to_band`), and a move too long to
  COMPLETE walks as far as it can (`advance_toward`) instead of being refused:
  opposed spawn zones are ninety feet apart, so all-or-nothing left every melee
  creature standing on its spawn square for the whole fight. The AI can also
  LEAP — `gap_between` is the cheap planner question, `jump_toward` takes the
  run-up and the leap, and the run-up is what makes it work at all (a standing
  jump clears one square, which lands you in the channel). `scripts/ai_arena.py`
  had never attached a board, so every number it ever printed was for a
  blindfolded engine.
