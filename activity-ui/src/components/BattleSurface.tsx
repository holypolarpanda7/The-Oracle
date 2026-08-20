import { useEffect, useRef, useState } from "react";
import { IconDefs } from "./icons";
import { isTyped } from "./Narration";
import { VttOverlay } from "./VttOverlay";
import { ActionBar } from "./ActionBar";
import { renderBlock, type PlayProps } from "./PlaySurface";
import { CharacterSheet } from "./CharacterSheet";
import { useResizable } from "../lib/useResizable";
import { uiTick } from "../lib/sound";

/** The fight, as its own page.
 *
 *  Combat used to be a panel inside the play surface: the board shared a column
 *  with the narration, under a status bar, an initiative strip, a "here & now"
 *  rail and a full character sheet — so the one thing that decides the outcome
 *  was a fifth of the screen. Everything in here is either the board, the acts
 *  you can take on it, or who goes next; the rest of the table is one tap away
 *  and not on screen.
 *
 *  It is deliberately a separate component rather than a mode flag threaded
 *  through `PlaySurface`: the two layouts share no structure, and the shared
 *  half (what a block looks like, what the bar does) is shared as components.
 */
/** How the engine's own lines are coloured. The text is the ENGINE's, never a
 *  model's, so the vocabulary is small and fixed and worth reading at a
 *  glance — a HIT and a MISS should not look the same in a scrolling column. */
function logTone(line: string): string {
  if (/^(CRITICAL HIT|.*: CRITICAL HIT)/.test(line)) return "crit";
  if (/\bCRITICAL HIT\b/.test(line)) return "crit";
  if (/\bgoes DOWN\b|ALL FOES DOWN|THE PARTY IS DOWN/.test(line)) return "down";
  if (/\bHIT\b|FAILED SAVE/.test(line)) return "hit";
  if (/\bMISS\b|\bSAVED\b/.test(line)) return "miss";
  if (/^REFUSED:/.test(line)) return "refused";
  if (/^NOW:/.test(line)) return "now";
  if (/^(MOVE|DASH|DODGE|DISENGAGE):/.test(line)) return "move";
  return "";
}

export function BattleSurface(p: PlayProps) {
  const logRef = useRef<HTMLDivElement>(null);
  const engRef = useRef<HTMLDivElement>(null);
  const [secret, setSecret] = useState(false);
  // Open beside the board on a desktop, SHUT on a phone. There the log is a
  // drawer along the bottom, and open by default it covers the action bar —
  // the one thing on the page you act with.
  const [logOpen, setLogOpen] = useState(
    () => typeof window === "undefined" || window.innerWidth > 900);
  // The sheet is a REFERENCE during a fight, not a fixture: it took a third of
  // the width to say things that do not change between turns. It opens over
  // the board when you want it.
  const [sheetOpen, setSheetOpen] = useState(false);
  const sheetR = useResizable("battle-sheet", { minW: 260, minH: 300 });
  const send = (text?: string) => { p.submit(secret, text); setSecret(false); };

  const pending = p.blocks.findIndex((b) => isTyped(b) && !b.done);
  const revealed = pending === -1 ? p.blocks : p.blocks.slice(0, pending + 1);

  useEffect(() => {
    const el = logRef.current;
    if (el) el.scrollTop = el.scrollHeight;
    const eg = engRef.current;
    if (eg) eg.scrollTop = eg.scrollHeight;
  });

  const combat = p.combat;
  const up = combat?.combatants.find(
    (c) => c.id === combat.current_combatant_id) ?? null;
  const mine = p.sheet?.character_id ?? null;
  const myTurn = !!up && up.kind === "pc" && !!mine
    && up.character_id === mine;
  const me = combat?.combatants.find((c) => c.character_id === mine) ?? null;

  return (
    <div className={`battle${logOpen ? "" : " log-shut"}`}>
      <IconDefs />

      {/* One strip, and everything in it earns its height: the round, who is
          up, the whole order, your own numbers, and the way out. The old
          layout spent three separate bars on this. */}
      <header className="bt-top">
        <div className="bt-id">
          <span className="bt-round">⚔ {combat ? `Round ${combat.round}` : "Battle"}</span>
          <span className="bt-name">{combat?.name ?? p.vtt?.name ?? "the field"}</span>
        </div>

        <div className="bt-order">
          {(combat?.combatants ?? []).map((c) => {
            const dead = c.defeated || c.current_hp <= 0;
            const frac = Math.max(0, Math.min(1,
              c.current_hp / Math.max(1, c.max_hp)));
            return (
              <div
                key={c.id}
                className={`bt-pip${c.id === combat?.current_combatant_id ? " on" : ""}`
                  + (c.kind === "pc" ? " pc" : "")
                  + (dead ? " down" : "")}
                title={`${c.name} · initiative ${c.initiative} · ${c.current_hp}/${c.max_hp} HP`}
              >
                <span className="bt-init">{c.initiative}</span>
                <span className="bt-who">{c.name}</span>
                <span className="bt-hp"><i style={{ width: `${frac * 100}%` }} /></span>
              </div>
            );
          })}
        </div>

        <div className="bt-me">
          {me && (
            <span className="bt-mehp" title="your hit points">
              {me.current_hp}/{me.max_hp}
            </span>
          )}
          {p.sheet?.ac != null && <span className="bt-ac">AC {p.sheet.ac}</span>}
          <button
            className={`bt-icon${p.narrateCombat ? " on" : ""}`}
            title={p.narrateCombat
              ? "The Oracle is describing the fight — click for the engine's pace alone"
              : "Fighting without narration — click to have the Oracle describe it again"}
            onClick={() => { uiTick(); p.onNarrateCombat(!p.narrateCombat); }}
          >✒</button>
          <button className={`bt-icon${sheetOpen ? " on" : ""}`} title="Your sheet"
                  onClick={() => { uiTick(); setSheetOpen((v) => !v); }}>📜</button>
          <button className="bt-icon" title="The Chronicle"
                  onClick={() => { uiTick(); p.onChronicle(); }}>📖</button>
          <button className="bt-icon" title="Leave the fight / main menu"
                  onClick={() => { uiTick(); p.onMainMenu(); }}>☰</button>
        </div>
      </header>

      {/* Whose turn it is, said once and loudly. The fight tells you it is
          "Cultist 1's turn" in six small places and never once says what YOU
          are supposed to do about it. */}
      <div className={`bt-turn${myTurn ? " mine" : ""}${p.busy ? " waiting" : ""}`}>
        {p.busy
          ? <><span className="bt-spin" /> the Oracle is resolving…</>
          : myTurn
            ? <>◆ <b>Your turn</b> — arm an act below, then click the board</>
            : up
              ? <><span className="bt-spin" /> {up.name} is acting…</>
              : "the fight is being set out…"}
      </div>

      <main className="bt-board">
        {p.vtt ? (
          <VttOverlay
            scene={p.vtt}
            combat={p.combat}
            myCharacterId={mine}
            options={p.vttOptions}
            preview={p.vttPreview}
            armed={p.armed}
            targets={p.vttTargets}
            area={p.vttArea}
            ping={p.vttPing}
            error={p.vttError}
            fill
            onRequestOptions={p.onVttOptions}
            onPreviewPath={p.onVttPreview}
            onMove={p.onVttMove}
            onRequestTargets={p.onVttTargets}
            onPreviewArea={p.onVttArea}
            onTakeAimed={(a, aim) => p.onTakeAction(a, aim)}
            onPing={p.onVttPing}
            onTakeStairs={p.onVttStairs}
            onDismissError={p.onVttDismissError}
          >
            <ActionBar
              data={p.actions}
              armed={p.armed}
              slot={p.armedSlot}
              onArm={p.onArm}
              onSlot={p.onArmedSlot}
              onTake={(a) => p.onTakeAction(a)}
              disabled={p.busy}
            />
          </VttOverlay>
        ) : (
          <div className="bt-noboard">
            The board is being drawn — the fight is running either way; say what
            you do below.
          </div>
        )}
      </main>

      {sheetOpen && (
        <div className="bt-sheet" onClick={() => setSheetOpen(false)}>
          <div className="bt-sheet-in" onClick={(e) => e.stopPropagation()}>
            <CharacterSheet sheet={p.sheet} panel={sheetR} onInspect={p.onInspect}
                            onItemAction={p.onItemAction}
                            onPortrait={p.onPortrait} onSetDnr={p.onSetDnr}
                            onReprepare={p.onReprepare} />
          </div>
        </div>
      )}

      <aside className={`bt-log${logOpen ? "" : " shut"}`}>
        <button className="bt-logtab" onClick={() => { uiTick(); setLogOpen((o) => !o); }}>
          {logOpen ? "› log" : "‹ log"}
        </button>

        {/* THE ENGINE, on its own. Every line here is the rules engine's
            certified record — no model wrote any of it, and it lands the
            moment the turn resolves rather than when the prose is finished. */}
        <div className="bt-eng" ref={engRef}>
          <div className="bt-eng-head">
            the fight, as the rules had it
            {!p.narrateCombat && <span className="bt-eng-mute"> · prose off</span>}
          </div>
          {p.combatLog.length === 0 && (
            <p className="bt-eng-empty">nothing has been resolved yet.</p>
          )}
          {p.combatLog.map((e, i) => (
            <div className={`bt-turnlog k-${e.kind}`} key={i}>
              <div className="bt-turnlog-head">
                <span className="bt-tl-who">{e.actor}</span>
                <span className="bt-tl-round">r{e.round}</span>
              </div>
              {e.text.split("\n").filter((l) => l.trim()).map((line, j) => (
                <div className={`bt-tl-line ${logTone(line)}`} key={j}>{line}</div>
              ))}
            </div>
          ))}
        </div>

        <div className="bt-lines" ref={logRef} onClick={p.onSkip}>
          {revealed.map((b, i) => renderBlock(b, i, p.onBlockDone))}
          {p.draft ? <p className="drafting">{p.draft}</p> : null}
        </div>

        {p.suggestions.length > 0 && !p.busy && (
          <div className="bt-suggests">
            {p.suggestions.map((s) => (
              <button className="sugg" key={s} disabled={p.rateWait > 0}
                      onClick={() => send(s)}>{s}</button>
            ))}
          </div>
        )}

        {/* Typing what you do still works, and always will — the bar is the
            fast path, not the only one. */}
        <div className={`promptbar${secret ? " secret" : ""}`}>
          <button
            className={`psecret${secret ? " on" : ""}`}
            onClick={() => setSecret((v) => !v)}
            title={secret ? "Secret: only you will see this" : "Act in secret"}
            aria-pressed={secret}
          >{secret ? "🔒" : "🎭"}</button>
          <input
            value={p.input}
            onChange={(e) => p.setInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && send()}
            placeholder={
              p.rateWait > 0 ? `a breath — ${p.rateWait}s…`
                : p.busy ? "resolving…"
                : myTurn ? "say what you do…"
                : "waiting on the others…"
            }
            disabled={p.busy || p.rateWait > 0}
          />
          <button className="psend" onClick={() => send()}
                  disabled={p.busy || !p.input.trim()} aria-label="Send">➤</button>
        </div>
      </aside>
    </div>
  );
}
