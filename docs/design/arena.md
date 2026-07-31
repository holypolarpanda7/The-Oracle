# The Proving Grounds (`arena/`)

A practice mode that stands **outside the living world**: three level-1 character
slots, a place to fight, a level to fight at, and encounters the code rosters
against a real XP budget. Nothing that happens here is remembered.

## Why it exists

Two reasons, and they turn out to be the same reason twice.

For a **player**, it's the only way to answer "what does this build actually feel
like at level 9?" without spending a real character and a real month of world
days finding out.

For **us**, it's a test harness for the three systems that have to be right on
day one — character creation, level-up, and combat on the tactical board. Every
run walks the same code paths a real table does. There is no arena-flavoured
combat resolver, no shortcut level-up, no fake board. If a bout works, the game
works; if a bout is broken, the game is broken and we found out in thirty
seconds instead of mid-session.

## The loop

```
landing → Proving Grounds
  ① pick a slot (or forge a level-1 character into one — the real CC flow)
  ② pick where you fight (land / sea / air)
  ③ pick a level (1–20) and a difficulty
  → the climb: every level-up choice from 1 to N, in the real level-up overlay
  → the stall: a stipend for that level, spent on gear, and strapped on
  → the bout: a rostered encounter, seated on a real board, played in prose
  → the result: again here · somewhere else · back to the stall · leave
```

## What's a slot, and what's a run

Two characters per slot, and the distinction is the whole trick:

| `Character.arena_slot` | what it is |
| --- | --- |
| `n` (1..3) | the **saved** character — always level 1, never modified |
| `-n` | the **run copy** — cloned from the saved one and levelled to the target |

Levelling a copy is what lets "fight at level 12" replay the entire level-up
flow from level 1 every time, without ever spending the saved character. A run
copy that already sits at the level you asked for is offered back ("fight on"),
so the eleventh identical climb is optional rather than mandatory.

Both kinds are excluded from the world roster and from session character
binding: a practice character is never a person in the world, never enters the
world graph, and never shows up on the landing page.

## Environments

`arena/environments.py` is a hand-picked catalog, not an exhaustive list of
boards. Each entry is a *distinct tactical problem*: open ground, a corridor, a
room full of cover, water you can wade, water you can only swim, air with
footing, air with none.

Each names a `vtt.mapgen` archetype and the **medium** it's fought in:

- **land** (`walk`) — the sand ring, blackroot wood, a cave, ruins, a crypt, a
  cliff track, a sewer, open field
- **sea** — a ship's deck (walk), a coral reef and open water (`swim`), a mire
- **air** — floating islands and a skyship deck (`fly`), a rope bridge (walk)

Sea and sky boards needed the board layer to grow a notion of medium, so
`GeneratedMap.mode` now rides along with every layout, and connectivity, spawn
zones and the "did this generator collapse?" check are judged **in that medium**.
An open-water board is one connected space to a swimmer and nothing at all to a
walker; before this it would have been thrown away as unplayable. Tokens seated
on such a board default to its medium — the Grounds grant the fighter the
movement the place demands, because a landbound PC dropped in open water is a
test of drowning, not of combat.

## Rostering

`arena/encounters.py` builds the fight, and the model gets no say in it:

1. take the XP budget for (level, difficulty) from `dm_guide.encounter`
2. filter the bestiary to creatures that can move in this medium
3. pick a **silhouette** — solo, duo, pack, mob, or a captain with mooks
4. choose the stat block whose XP best spends the budget at that count
5. nudge the count until the adjusted XP actually lands in range

Step 5 matters more than it sounds: XP values are coarse and the count
multiplier bites, so "closest single pick" reliably under-spends — which is how
you end up testing "deadly" against three rats.

If an environment's own creatures can't fill a fight, the filter drops and the
roster is marked `conjured`, and the framing says so out loud rather than
pretending a shark belongs on a sand floor.

## The Quartermaster

A build is only half a build without the gear it's meant to be holding: a
level-12 paladin who never bought plate is a test of the wrong character, and
character creation grants a kit but **equips nothing**, so without a stall in
the way a level-12 fighter walks into the sand with their chain mail still in
the sack. So between the gate and the sand stands `arena/loadout.py`.

**The stipend is the Grounds', not the world's.** Conjured coin for a conjured
fight: it never touches the character's purse, and it's gone when the run ends.
Level 1 is paid the class's own starting gold so a level-1 bout matches what
creation would have given you; above that, `PURSE_BY_LEVEL` is the Grounds' own
curve, tuned so each tier can afford the kind of gear that tier is expected to
be holding — not a table lifted out of any book.

**What's on the board is gated by the level being fought at.** The gate is
rarity (`RARITY_MIN_LEVEL`: uncommon at 2, rare at 5, very rare at 11, legendary
at 17); an artifact is never for sale. The catalog rarely prices magic at all,
so `MAGIC_PRICE_BY_RARITY` is the stall's own conjuring fee — a practice mode
that can't sell a Cloak of Protection can't test a build that has one. Mundane
gear is priced from `rules_item` itself, and anything unpriced is never
silently given away.

**The client asks; the server prices.** `price_cart` is the only thing that
decides what was actually bought: an item that isn't on the board, a cart bigger
than the purse, or a fourth attunement is dropped **with a reason** rather than
granted, because a loadout the client invented would test a character that can't
exist.

**Re-outfitting is a clean slate.** Everything the stall sold is flagged in the
inventory, so walking back to it takes the whole loadout back and hands the
whole stipend over again — the character's own kit is never touched. Between
bouts the loadout persists; the result overlay's "back to the stall" reopens it.

The stall is also where gear gets **worn**: bought armour and weapons default to
equipped, anything wanting attunement is attuned up to the limit of three, and
the fighter's existing pack is listed with the same toggles. All of it runs
through the same inventory flags the live sheet uses, so `_compute_ac` and the
DM's equipment guardrails see exactly what a real table would.

## What the DM is and isn't told

A bout injects `# THE PROVING GROUNDS` into the prompt in place of the world
slice: this is a conjured practice fight, nothing is remembered, don't invent
quests or towns, narrate the combat and nothing else. It still gets the
initiative board, the tactical board, and the full hook vocabulary — that's the
point.

A practice session is exempted from three things the world gets: the world
clock, entropy/quest clocks, and change extraction. No days pass in the Grounds
and nothing is written back to the graph.

## Ending a bout

The **Grounds** call the fight, not the narration: after every turn, if every
foe is down it's a victory, if the fighter is down it's a defeat. The encounter
closes, the fighter is restored to full (HP, hit dice, slots, resources, death
saves, exhaustion, conditions), and the result overlay offers the next bout.
The board stays up as an aftermath until the next one replaces it.

## Wiring

| piece | where |
| --- | --- |
| catalog + roster building | `arena/` |
| stipend curve, stall stock, cart pricing | `arena/loadout.py` |
| board medium (`swim`/`fly`) | `vtt/mapgen.py`, `vtt/terrain.py` (`^` open sky), `vtt/scene.py` |
| slots, runs, bouts, outcomes, the stall | `_arena_*` in `oracle-dm-backend/fastapi-dm.py` |
| socket protocol | `arena_state` / `arena_create` / `arena_delete` / `arena_begin` / `arena_shop` / `arena_outfit` / `arena_fight` / `arena_leave` → `{t:"arena", state}` (the stall's board rides on `state.shop`, only while it's open) |
| the screens | `activity-ui/src/components/Arena.tsx` (`Arena`, `Quartermaster`, `ArenaResult`) |

## Checking it

```bash
uv run python -m arena.demo 12 deadly      # what would it field?
uv run python scripts/arena_smoke.py       # the whole loop, LLM stubbed
uv run python -m vtt.selftest              # board maths, incl. the new media
```

`scripts/arena_smoke.py` drives the engine directly *and* replays the same
sequence over the real Activity WebSocket handler, so the glue is covered too.
