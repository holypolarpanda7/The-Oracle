# Ventures — other people's quests

An NPC steps out of their role and goes after something they want. The party may
travel with them, help, hinder, or leave them to it. It progresses on world-time
either way.

Code: `eight_card_system/ventures.py`. Wiring: `[[VENTURE:]]` hooks and the
prompt block in `oracle-dm-backend/fastapi-dm.py`. Test:
`uv run python scripts/ventures_smoke.py`.

## Why it exists

The world had NPCs who *are* something — a role, a disposition, a hook from the
census table — and quests the *party* takes. It had nothing in between. Nobody
in it ever wanted anything badly enough to act on it, so an NPC was furniture
between the moments a player addressed them, and the only stories in the world
were the ones the players were currently interested in.

A venture is that missing middle: the blacksmith who has been saying his brother
never came back from the hills packs a hammer and goes, and whether he comes home
is a fact about the world rather than a fact about the party's attention.

## The four properties

**They are not companions.** A companion (`travels_with`, `CompanionControl`) is
a body the party directs. A venturer LEADS. The party may `accompany` them
(`ACCOMPANIES`, pc → npc — deliberately the mirror of the companion edge, so
nothing can confuse the two) and may stop at any moment. Accompanying opens no
companion relation, gives the party no control, and does not move the NPC's
location around with the party.

**They progress unwatched.** `advance_ventures` runs on the entropy cadence and
rolls each *unaccompanied* venture forward one stage attempt per `STEP_DAYS`
world-days: d20 + the NPC's competence against the stage DC. An **accompanied**
venture is skipped and its clock stamped forward, because resolving it behind the
party's back would make walking beside somebody the one way to stop mattering to
their story. It moves at the table instead, through `[[VENTURE: step]]`.

`step_venture` is the ONE place a stage moves, whichever side called it, so a
watched and an unwatched venture can never advance by different rules.

**They have DEPTH, 1 to 3.** A venture is `steps[:depth-1] + [climax]`, so the
climax always happens last and a depth-1 venture is a whole small story rather
than a truncated big one. Depth is a real dial on the odds:

| depth | setbacks allowed | measured success, unwatched |
|-------|------------------|-----------------------------|
| 1     | 3                | 79% (146/184) |
| 2     | 4                | 61% (94/155)  |
| 3     | 5                | 41% (38/92)   |

(431 ventures over 24 simulated world-years; 65% overall.) A setback also raises the
current stage's DC by `SETBACK_DC_STEP` — the surprise is spent and whatever
stands in the way is awake now. The gradient is the point: a small errand usually
comes off, a three-stage saga usually needs somebody. That is the strongest
argument the system can make for walking beside a person.

**They mutate the world when they land.** Every mutation goes through a primitive
that already existed, so a venture can never change the world in a way nothing
else can:

| `outcome_kind` | success | failure |
|---|---|---|
| `safer` | `shift_place_danger(-1)` | `shift_place_danger(+1)` |
| `standing` | the NPC's `role` is promoted | the role is ruined |
| `wealth` | their purse and the town's population rise | both fall |
| `relic` | a real ITEM entity they `OWNS` | the place keeps whatever they disturbed |
| `peril` | they come home | `claim_peril_npc` — they die, and `spawn_successor` fills the post |

A failure out in the country also raises that place's danger: somebody went out
there and stirred something.

## Working against one

A party may want somebody to fail. `OPPOSES` (pc → npc) is the stance —
deliberately not `HOSTILE_TO`, because you can want the smith's guild seat to go
to someone else without hating the smith, and this edge ends when the venture
does where hostility outlives it.

**Opposition is not the mirror of accompanying, and that is the whole point.** An
accompanied venture *pauses* the offline roll, because the party is standing in
it. An opposed one keeps rolling — harder. Sabotage is a thing you do and then
walk away from, and a declared enemy who has to stand there watching to matter is
not an enemy, it is an escort.

`effective_dc` adds `OPPOSED_DC` (+3) at roll time and never writes it back: a
setback is permanent and lives in the stage, opposition lasts only while somebody
is actually set against them. Two different kinds of fact, stored differently.

Three verbs:

* **`hinder`** — one concrete act of sabotage. It is `step_venture(failure)` with
  a name on it, and the distinction is not cosmetic: a hindrance is **counted**,
  and the count is what the venturer has to trace back.
* **`thwart`** — the race, not a roll. The party took the prize, won the seat,
  got there first; the goal is simply gone, and the venture resolves FAILED.
* **`relent`** — back off, and they get their own DC back.

**A hindered venture fails the way any venture fails.** Sabotage the ranger who
was going to make the road safe and the road stays unsafe. That the cost lands on
somebody else is the design, not an oversight — there is no consequence-free way
to wreck somebody's work.

### Do they ever learn it was you?

Rolled **once, at resolution**, not on a clock through the operation: the
question only matters at the end, and a covert party that got away with it should
get away with it cleanly. Chance is `DISCOVERY_BASE + DISCOVERY_PER_ACT ×
hindrances`, capped — the more you actually interfered, the more there is to
trace. An **open** enemy skips the roll entirely; they were never hiding.

On discovery a deed lands in `relationships.record_deed`, and the tag is chosen
by what the party actually did: **`betrayal`** if they were *accompanying* the
venture while working against it — which is exactly what betrayal is, and the
ledger already prices it at the slow-decay maximum — otherwise `theft`. An
exposed saboteur also gets no companion trust for the journey, however it ended.

Opposing while accompanying is allowed and is not a bug. It is the saboteur
inside the camp: presence still pauses the offline roll, opposition still lifts
the DC of every step settled at the table.

## Reusing the quest table

A venture IS a `QUEST` entity with `tier: "venture"`, which buys three things for
nothing: the Chronicle reads it, `_quests_touching` pulls it into the DM's world
slice whenever its owner or its home place is in view, and entropy's main-cast
protection already refuses to age out a quest-involved NPC — which is exactly the
person whose errand is half-finished.

What it must never take is a party **stakes clock**. Those escalate on the
party's *neglect*, and a venture's whole point is that neglect is not what
decides it. `entropy.advance_quest_clocks` skips tier `venture`;
`_format_quests_block` and the Chronicle's quest list exclude them too, or they
read as work waiting for the party.

Ventures are created with `create_entity`, never `upsert_entity`: a person may
set out twice in a long life and the second time is a NEW thread. Upserting by
slug would reopen the finished one.

## Rationing, and who is eligible

`MAX_LIVE` (4) caps concurrency; `BIRTH_CHANCE` is rolled **once per pass**, not
once per candidate — per candidate it is not a rate limit at all, since a town of
ten people gets ten chances and something is born nearly every time.
`COOLDOWN_DAYS` (90) keeps the same three villagers off the road permanently.
Measured: ~18 ventures a world-year in one active town, averaging **0.99 live**
at any moment, so the DM's block usually has something and never has a crowd.

The filter that matters most is the last one in `_candidates`: **somebody has to
be able to hear about it.** The NPC must either be known to a PC or live
somewhere the party has actually been. A venture by a stranger in a town nobody
has entered is a dice roll no one will ever see, so it is not rolled at all.

## Two bugs worth remembering

**A venturer has to come HOME.** The climax moves them to the wild place the
venture was aimed at. Without a homecoming on resolution they simply stand there
for the rest of the world's life: the town loses its smith, the census keeps a
post nobody fills, and — because eligibility keys on living somewhere visited —
the pool of people who could ever set out drains to zero. In the first long
simulation ventures stopped appearing entirely after 126 days for exactly this
reason.

**A successor is born where `spawn_successor` finds the body.** Kill a venturer
at the climax and the heir to their post is spawned in the wilds their
predecessor never came back from. They died out there, which is the truth; the
successor takes the post at *home*, which is also the truth, and the ventures
code has to say so.

## Hooks

```
[[VENTURE: open    | <npc> | <goal (optional)> | <depth 1-3 (optional)>]]
[[VENTURE: step    | <npc> | success|setback | <what happened>]]
[[VENTURE: follow  | <npc>]]
[[VENTURE: leave   | <npc>]]
[[VENTURE: resolve | <npc> | success|failure | <outcome>]]
[[VENTURE: abandon | <npc> | <why>]]

[[VENTURE: oppose  | <npc> | <why> | covert]]
[[VENTURE: hinder  | <npc> | <what the party did>]]
[[VENTURE: thwart  | <npc> | <how the goal is now gone>]]
[[VENTURE: relent  | <npc>]]
```

With no goal given, the code rolls one from the NPC's trade — `family_for` maps a
role onto one of eight families (martial, devout, mercantile, craft, learned,
underworld, rustic, common), each with three archetypes. The DM names WHO and
optionally WHAT; the code owns the stages, the DCs, the places and the roll. The
same division of labour the tactical board keeps.

## What the players see

* The DM prompt gets `render_block`, in three lists because they are three
  different situations: ventures the party is ON (the scene, with the current
  step and an explicit reminder that the NPC leads), ventures they are set
  AGAINST (with whether their hand is hidden, and how much interference has
  already happened), and ventures merely underway nearby (colour, and an
  opening).
* The Chronicle's journal tab gets **Other people's roads** — a distinct card,
  not a quest card, showing whose road it is, what they want, which step they are
  on, and whether the party is riding with them or working against them. Only the
  party's OWN stance is shown: whether the venturer has worked out who is behind
  it is the venturer's business, and the Chronicle is not where a covert
  operation gets blown.
* A returning player is told, in the away-time block, how a thread they once
  walked ended (`catch_up_lines`) — they earned hearing it without having to ask.
