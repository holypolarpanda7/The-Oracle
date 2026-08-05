import { useEffect, useRef, useState } from "react";
import { Frame } from "./Frame";
import { CharacterSheet } from "./CharacterSheet";
import { IconDefs } from "./icons";
import { RevealedSpans, isTyped, type Block } from "./Narration";
import { useResizable, resetAllPanels, dropPanel } from "../lib/useResizable";
import { InitiativeCarousel } from "./InitiativeCarousel";
import { VttOverlay } from "./VttOverlay";
import type {
  Ally, CombatState, Locale, RollResult, RouteRow, SheetData, VttOptions,
  VttScene,
} from "../lib/types";

function hpMood(hp: number, max: number): string {
  const f = hp / Math.max(1, max);
  return f <= 0.25 ? "dire" : f <= 0.6 ? "hurt" : "";
}

/** Break "d20:14 +5 vs DC 15" into chips, so the maths is legible at a glance
 *  instead of being a mono string the eye slides off. */
function rollParts(r: RollResult): { die: string | null; mods: string[] } {
  const detail = r.detail ?? r.expr ?? "";
  const die = detail.match(/d\d+\s*:\s*(\d+)/i)?.[1] ?? null;
  const mods = [...detail.matchAll(/([+-]\s*\d+)/g)].map((m) => m[1].replace(/\s+/g, ""));
  return { die, mods };
}

/** The natural d20 — a 20 or a 1 is the loudest thing that can happen on a
 *  turn, so it gets its own treatment rather than a colour on the border. */
function critOf(r: RollResult): "crit" | "fumble" | null {
  const nat = rollParts(r).die;
  if (!nat || !/d20/i.test(r.detail ?? r.expr ?? "")) return null;
  return nat === "20" ? "crit" : nat === "1" ? "fumble" : null;
}

function RollCard({ roll }: { roll: RollResult }) {
  const { die, mods } = rollParts(roll);
  const crit = critOf(roll);
  const state = roll.success === undefined ? "" : roll.success ? "ok" : "failure";
  return (
    <div className={`rollcard ${state} ${crit ?? ""}`}>
      <span className="rc-die">
        {roll.total}
        {crit && <em className="rc-crit">{crit === "crit" ? "nat 20" : "nat 1"}</em>}
      </span>
      <span className="rc-body">
        <b className="rc-label">{roll.label ?? "Roll"}</b>
        <span className="rc-chips">
          {die && <em className="rc-chip nat">d20 {die}</em>}
          {mods.map((m, i) => <em className="rc-chip" key={i}>{m}</em>)}
          {roll.dc !== undefined && <em className="rc-chip dc">DC {roll.dc}</em>}
        </span>
      </span>
      {roll.success !== undefined && (
        <span className="rc-stamp">{roll.success ? "success" : "failure"}</span>
      )}
    </div>
  );
}

function renderBlock(b: Block, i: number, onBlockDone: (i: number) => void) {
  if (b.kind === "player") {
    return (
      <p className={`player${b.secret ? " secret" : ""}`} key={i}>
        {b.secret && <span className="secret-tag">🔒 </span>}
        {b.who && <span className="hl-name">{b.who} · </span>}{b.text}
      </p>
    );
  }
  if (b.kind === "whisper") {
    return (
      <p className="whisper" key={i}>
        <span className="secret-tag">🤫 </span>{b.text}
      </p>
    );
  }
  if (b.kind === "roll") {
    return <RollCard roll={b.roll} key={i} />;
  }
  if (b.kind === "speech") {
    return (
      <div className={`speech${b.secret ? " secret" : ""}`} key={i}>
        {b.portrait
          ? <img className="sp-face" src={b.portrait} alt="" />
          : b.who ? <span className="sp-face empty">{b.who.slice(0, 1)}</span> : null}
        <div className="sp-body">
          {b.who && (
            <div className={`sp-who${b.script ? ` script-${b.script}` : ""}`}>
              {b.who}
            </div>
          )}
          <p>
            <RevealedSpans spans={b.spans} done={b.done} onDone={() => onBlockDone(i)} />
          </p>
        </div>
      </div>
    );
  }
  return (
    <p key={i} className={b.secret ? "secret" : undefined}>
      {b.secret && <span className="secret-tag">🔒 to you · </span>}
      <RevealedSpans spans={b.spans} done={b.done} onDone={() => onBlockDone(i)} />
    </p>
  );
}

/** The always-on world header: who you are, where you stand, and what hour it
 *  is in the world. The clock and the place are the two facts that make a
 *  persistent world feel persistent, and they were previously invisible. */
function StatusBar({ sheet, locale }: { sheet: SheetData | null; locale: Locale | null }) {
  if (!sheet && !locale) return null;
  const hp = sheet ? sheet.hp : 0;
  const max = sheet ? Math.max(1, sheet.hp_max) : 1;
  const where = [locale?.place, locale?.region].filter(Boolean).join(" · ");
  const when = [locale?.time_of_day, locale?.date].filter(Boolean).join(" · ");
  return (
    <div className="statusbar">
      {sheet?.portrait
        ? <img className="sb-face" src={sheet.portrait} alt="" />
        : <span className="sb-face empty" />}
      <span className="sb-id">
        <b className={`sb-name${sheet?.script ? ` script-${sheet.script}` : ""}`}>
          {sheet?.name ?? "—"}
        </b>
        {sheet?.subtitle && <em className="sb-sub">{sheet.subtitle}</em>}
      </span>
      <span className="sb-world">
        {where && <b className="sb-where">{where}</b>}
        {when && <em className="sb-when">{when}</em>}
      </span>
      {sheet && (
        <span className="sb-vitals">
          <span className={`sb-hp ${hpMood(hp, max)}`}>
            <span className="sb-hp-fill" style={{ width: `${(100 * hp) / max}%` }} />
            <b>{hp} / {sheet.hp_max}</b>
          </span>
          <span className="sb-ac">AC {sheet.ac}</span>
        </span>
      )}
    </div>
  );
}

/** "Here and now" — what your character can tell by standing there and
 *  looking around. Deliberately NOT a map: a map is an in-game artifact you
 *  draft or buy (see eight_card_system/mapmaker.py), never a UI freebie. */
function LocaleRail({ locale, onInspect }: {
  locale: Locale | null;
  onInspect: (name: string) => void;
}) {
  if (!locale) return null;
  const present = locale.present ?? [];
  return (
    <div className="locale">
      <div className="lc-head">Here &amp; Now</div>
      {locale.place && (
        <div className="lc-place">
          <b>{locale.place}</b>
          {locale.place_kind && <em>{locale.place_kind}</em>}
        </div>
      )}
      {locale.weather && <p className="lc-sky">{locale.weather}</p>}
      {(locale.hazards ?? []).length > 0 && (
        <div className="lc-haz">
          {locale.hazards!.map((h) => <span className="lc-tag" key={h}>{h}</span>)}
        </div>
      )}
      <div className="lc-head">Present</div>
      {present.length === 0
        ? <p className="lc-none">No one but you.</p>
        : present.map((who) => (
            <button className="lc-who" key={who.name} onClick={() => onInspect(who.name)}>
              <b>{who.name}</b>
              {who.role && <em className="lc-role">{who.role}</em>}
              {who.attitude && <em className={`lc-att ${who.attitude}`}>{who.attitude}</em>}
            </button>
          ))}
    </div>
  );
}

export interface PlayProps {
  blocks: Block[];
  sheet: SheetData | null;
  /** Place / world clock / weather / who's here — null until the first push. */
  locale: Locale | null;
  sceneUrl: string | null;
  party: Ally[];
  combat: CombatState | null;
  /** The tactical board — non-null only while the Oracle has one out. */
  vtt: VttScene | null;
  vttOptions: VttOptions | null;
  vttPing: { x: number; y: number; label?: string; at: number } | null;
  vttPreview: { token_id: number; ok: boolean; cost_ft?: number;
                opportunity?: string[] } | null;
  vttError: string | null;
  onVttOptions: (tokenId: number, dash: boolean) => void;
  onVttPreview: (tokenId: number, x: number, y: number) => void;
  onVttMove: (tokenId: number, x: number, y: number) => void;
  onVttPing: (x: number, y: number) => void;
  onVttStairs: () => void;
  onVttDismissError: () => void;
  /** Things the Oracle says you could do now — a nudge, never a menu. */
  suggestions: string[];
  /** Ways of getting somewhere, when the Oracle offered to set out. */
  routes: RouteRow[];
  input: string;
  setInput: (v: string) => void;
  submit: (secret?: boolean, text?: string) => void;
  onChronicle: () => void;
  busy: boolean;
  rateWait: number;
  onSkip: () => void;
  onBlockDone: (i: number) => void;
  onMainMenu: () => void;
  onInspect: (name: string) => void;
  onItemAction: (name: string, action: string) => void;
  onPortrait: (action: "regear" | "select" | "delete",
               opts?: { context?: string; replace_context?: string; detail?: string }) => void;
  onSetDnr: (dnr: boolean) => void;
  onReprepare: () => void;
}

export function PlaySurface(p: PlayProps) {
  const scene = useResizable("scene", { minW: 280, minH: 160 });
  const sheetR = useResizable("sheet", { minW: 260, minH: 320 });
  const txtRef = useRef<HTMLDivElement>(null);
  // The narration column now fills the stage instead of being a fixed-size
  // prop, so a height persisted by the old drag-grip would pin it short.
  useEffect(() => { dropPanel("scroll"); }, []);
  // "Secret" input: the action + the Oracle's answer stay private to you — your
  // tablemates never see it (cheat, lie, a hidden roll).
  const [secret, setSecret] = useState(false);
  const send = (text?: string) => { p.submit(secret, text); setSecret(false); };

  // A reply now arrives as several blocks (prose, dialogue, roll cards), and
  // they must take their turn: without this every block mounts at once and
  // types simultaneously. Everything up to and including the first unfinished
  // block is shown; the rest appear as each one completes (or all at once when
  // the player clicks to skip).
  const pending = p.blocks.findIndex((b) => isTyped(b) && !b.done);
  const revealed = pending === -1 ? p.blocks : p.blocks.slice(0, pending + 1);

  useEffect(() => {
    const el = txtRef.current;
    if (!el) return;
    // On desktop the narration column is its own scroller; on a phone the
    // scroll grows with the text and the DOCUMENT scrolls, so follow the
    // newest line instead of nudging a scrollTop that cannot move.
    if (el.scrollHeight > el.clientHeight + 4) el.scrollTop = el.scrollHeight;
    else el.lastElementChild?.scrollIntoView({ block: "nearest" });
  });

  return (
    <div className={`play${p.vtt ? " boarded" : ""}`}>
      <IconDefs />
      <StatusBar sheet={p.sheet} locale={p.locale} />
      {p.combat && <InitiativeCarousel combat={p.combat} />}
      {/* The rail is absent in the Proving Grounds (no world, no clock) and
          before the first state push, and an empty column track would shove
          the stage into the rail's width — so the grid is told either way. */}
      <div className={`play-surface${p.locale ? "" : " no-rail"}`}>
        <LocaleRail locale={p.locale} onInspect={p.onInspect} />
        <div className="stage">
          {/* While a board is out it IS the picture of the moment; the rendered
              scene art returns the instant the Oracle puts the grid away. An
              empty frame is NOT rendered at all — a picture-shaped hole ate
              nearly half the surface, and the header already says where you
              are, so the narration takes that room instead. */}
          {p.vtt ? (
            <VttOverlay
              scene={p.vtt}
              combat={p.combat}
              myCharacterId={p.sheet?.character_id ?? null}
              options={p.vttOptions}
              preview={p.vttPreview}
              ping={p.vttPing}
              error={p.vttError}
              onRequestOptions={p.onVttOptions}
              onPreviewPath={p.onVttPreview}
              onMove={p.onVttMove}
              onPing={p.onVttPing}
              onTakeStairs={p.onVttStairs}
            onDismissError={p.onVttDismissError}
            />
          ) : p.sceneUrl ? (
            <Frame className="scene" panel={scene} bare>
              <div className="in"><img src={p.sceneUrl} alt="Scene" /></div>
              <span className="tag">Scene · rendered</span>
            </Frame>
          ) : null}

          <div className="scroll">
            <div className="txt" ref={txtRef} onClick={p.onSkip} title="Click to reveal instantly">
              <div className="who">The Oracle Speaks</div>
              {p.blocks.length
                ? revealed.map((b, i) => renderBlock(b, i, p.onBlockDone))
                : <p className="awaiting">The tale awaits your first deed…</p>}
            </div>
          </div>

          {p.party.length > 0 && !p.combat && (
            <div className="party">
              {p.party.map((a) => (
                <div className="ally" key={a.name}>
                  <div className="nm">{a.name}</div>
                  <div className={`abar ${hpMood(a.hp, a.hp_max)}`}>
                    <span style={{ width: `${(100 * a.hp) / Math.max(1, a.hp_max)}%` }} />
                  </div>
                  {a.condition && <div className="cond">{a.condition}</div>}
                </div>
              ))}
            </div>
          )}

          {/* Setting out is a decision. The code costed these from the world's
              real geography; picking one sends it as your action. Note what is
              NOT here: no map, no coordinates — only what a traveller could
              tell you (see eight_card_system/mapmaker.py). */}
          {p.routes.length > 0 && !p.busy && (
            <div className="routes">
              <div className="rt-head">
                The road to {p.routes[0].destination}
              </div>
              <div className="rt-row">
                {p.routes.map((r) => (
                  <button
                    className={`route danger-${r.danger}`}
                    key={r.id}
                    disabled={p.rateWait > 0}
                    onClick={() => send(`We take ${r.label} to ${r.destination}.`)}
                  >
                    <b>{r.label}</b>
                    <em className="rt-cost">
                      {r.miles} mi · {r.days} {r.days === 1 ? "day" : "days"}
                    </em>
                    <em className={`rt-danger ${r.danger}`}>{r.danger} danger</em>
                    <span className="rt-blurb">{r.blurb}</span>
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* Openings the Oracle named in the scene it just narrated. One tap
              sends the phrase as your action; the box below still takes
              anything at all. */}
          {p.suggestions.length > 0 && !p.busy && (
            <div className="suggests">
              {p.suggestions.map((s) => (
                <button className="sugg" key={s} disabled={p.rateWait > 0}
                        onClick={() => send(s)}>{s}</button>
              ))}
            </div>
          )}

          {/* Last in the column so the sticky prompt bar has nothing beneath
              it to sit in front of. */}
          <div className={`promptbar${secret ? " secret" : ""}`}>
            <button
              className={`psecret${secret ? " on" : ""}`}
              onClick={() => setSecret((s) => !s)}
              title={secret ? "Secret: only you will see this" : "Act in secret (hidden from your table)"}
              aria-pressed={secret}
            >{secret ? "🔒" : "🎭"}</button>
            <input
              value={p.input}
              onChange={(e) => p.setInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && send()}
              placeholder={
                p.rateWait > 0 ? `the table needs a breath — ${p.rateWait}s…`
                  : secret ? "whisper a secret deed — only you will see the Oracle's reply…"
                  : p.busy ? "the Oracle is weaving…"
                  : "Speak your deed, and the Oracle shall answer…"
              }
              disabled={p.busy || p.rateWait > 0}
            />
            <button className="psend" onClick={() => send()} disabled={p.busy || !p.input.trim()} aria-label="Send">➤</button>
          </div>
        </div>

        <aside>
          <CharacterSheet sheet={p.sheet} panel={sheetR} onInspect={p.onInspect}
                          onItemAction={p.onItemAction}
                          onPortrait={p.onPortrait} onSetDnr={p.onSetDnr}
                          onReprepare={p.onReprepare} />
          <div className="menu">
            <button className="mbtn wide" onClick={p.onChronicle}>📖 The Chronicle</button>
            <button className="mbtn" onClick={resetAllPanels}>⟲ Reset Layout</button>
            <button className="mbtn" onClick={p.onMainMenu}>☰ Main Menu</button>
          </div>
        </aside>
      </div>
    </div>
  );
}
