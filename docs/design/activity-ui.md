# The Activity UI — the battle page and the socket

The fight's own page, why a "frozen panel" is a dead socket, and the schema
drift underneath it. Split out of `CLAUDE.md`; read this before touching
`activity-ui/src/BattleSurface.tsx` or the Activity WebSocket loop.

- **A fight gets its own PAGE, because the board was a fifth of the screen.**
  `BattleSurface.tsx` replaces the play surface whenever a board is out. The old
  layout spent its height on a status bar, an initiative carousel of CARDS, a
  "here & now" rail, a narration column and a permanent character sheet, so the
  one thing that decides the outcome was a small panel in the middle of them.
  **The board IS the page and everything else floats ON it.** The first cut
  gave the board a grid CELL with the log in a column beside it, which is
  better than a panel in a scrolling stage and still not what a fight wants: a
  third of the width went to a narration column nobody reads mid-turn, and the
  prose was squeezed into it. Now the strip, the turn line and the log are
  overlays — they cost the map nothing when you are not reading them, the log
  folds to a tab, and on a phone it is a bottom drawer that starts SHUT because
  open it covers the action bar. The page never scrolls: the wheel over the
  board is the zoom and only the zoom, which needs a NON-PASSIVE native
  listener, since React attaches wheel passively and `preventDefault` there is
  a no-op. The page has three things: one strip (round, the whole order as a
  tight rail, your own HP/AC, the way out), a line saying WHOSE TURN it is in
  words, and the board with its action bar filling the rest. The sheet is a thing you look
  UP — it does not change between turns — and the log folds away entirely.
  Two mechanical notes. `vtt.css` and the shared narration/prompt styles are
  re-scoped `:is(.play, .battle)` rather than duplicated, and the log is
  PARCHMENT for that reason: every narration style (a name, an item, damage, a
  whisper) is inked for parchment, so a dark log would need a second palette
  for the same words. And the board takes a `fill` prop that stops it owning a
  height of its own — on the play surface it trades height with the narration
  and the player drags the split, and that persisted height would otherwise
  clamp the battle page's board. **Sizing the board CELL as a fraction of the
  viewport is the bug this page exists to fix, one breakpoint down**: the panel
  carries a title bar, a floor strip, a movement line and the action bar, and
  those fixed costs ate a 46vh cell down to a sliver of map on a phone. The MAP
  gets the floor, and the page scrolls.
- **A fight whose first initiative belonged to a MONSTER simply sat there.**
  Monsters only ever moved inside `_combat_engine_turn`, which runs on a
  player's MESSAGE — so the board said "Cultist 1's turn", the cultist did
  nothing, and the only thing that could have moved it was the player acting
  out of a turn they had just been told was not theirs.
  `_combat_npc_catchup` plays out whoever is up until it is a PC's turn again,
  and the Grounds run it when a bout opens. It stops at a PC, at a pending
  reaction (a question only a player can answer) and when one side is wiped.
  The other half of that hang was in `CombatEngine.render_report`: **a move
  does NOT always land on a band.** Three paths emit one without — a creature
  that covered as far as its turn allowed, a failed leap, and a jump (that one
  deliberately, since a `to` would send the caller to `apply_band_move` and
  undo the leap it was just told about) — and the renderer indexed `e['to']`
  directly, so the whole exchange raised. Each event is rendered inside its own
  guard now, because the tracker has ALREADY applied everything in a report and
  a rendering failure that raises throws away the record of damage that really
  happened.
- **A frozen panel is a DEAD SOCKET, and it was three bugs stacked.** Reported
  as "the equipment screen just froze". None of the three was in that screen.
  **`create_all` never ALTERs an existing table**, so a column added to a model
  never reached a database that already had the table — `combat_combatant.
  awareness` and `vtt_map.setpieces` had been missing for months, which means
  NO FIGHT COULD START AT ALL, in the world or in the Grounds. Nothing complains
  at import; it fails at the INSERT, deep inside a feature. The startup
  self-heal used to hand-list columns per table, which is exactly how two went
  missing, so the last pass is now DERIVED: any column a model declares and its
  table lacks is added, always nullable (SQLite cannot add NOT NULL to a table
  with rows, and the models apply their own defaults on write).
  **The Activity WebSocket loop caught only `WebSocketDisconnect`**, so that
  exception tore down the whole connection — and to a player a dead socket is
  not an error, it is the screen they were holding refusing to respond. Each
  message is now handled inside its own `try`, which reports the failure where
  the player is looking and puts the busy spinner down. A turn may fail; the
  table should not have to be rebuilt.
  **And the client said nothing about it**: `onclose` did nothing once the
  socket had opened, and every later `send` silently no-op'd on a closed one.
  `connect()` reconnects with backoff and reports a `ConnStatus`; the surface
  shows a banner, and re-entering on reconnect goes through the same
  `pendingEnterRef` the seal already uses (a fresh socket is bound to no
  session). Only a frame carrying a `t` counts as having been ANSWERED — a dev
  server's HMR socket accepts any upgrade and sends its own JSON down it, and
  counting that would make a page with no backend look like a live table.
  **"Reset Layout" was `location.reload()`** — a far bigger hammer than the
  button says, since reloading drops the socket, so pressing it mid-fight put
  the player on the landing with a bout still running behind them. Panels clear
  their own inline size on a broadcast event now; nothing about resetting a
  HEIGHT requires throwing the table away. **A long wait is said out loud**:
  opening a bout rosters an encounter, lays out a board and may draw it, which
  is tens of seconds behind a screen that otherwise just stops responding. The
  veil appears only after 250 ms, so a fast answer never flashes one (offline,
  where the Grounds answer synchronously, it never appears at all).
  (Related, and worth knowing when a demo-fed harness suddenly fails: `vite
  preview` PROXIES `/ws` to the backend, so the offline demo feed only engages
  when the backend is actually down. Serve `dist` with a plain static server to
  exercise it while the backend is up.)
