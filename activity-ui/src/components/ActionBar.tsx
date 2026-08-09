import { useMemo, useState } from "react";
import type { ActionBarData, BarAction } from "../lib/types";

/** What this character can do right now.
 *
 *  Everything here is the SERVER's answer: which acts exist, what they reach,
 *  what they cost and whether the economy still has it to spend. The bar
 *  greys out what it knows is unavailable so a player isn't invited to spend
 *  an Action they have already spent — but the engine re-checks every one of
 *  them, so a stale bar produces a refusal with a reason, never a cheat.
 *
 *  Picking an act does NOT send it. An act that needs aiming arms the board
 *  (see `VttOverlay`'s targeting mode) and waits for the click; only then is
 *  anything requested. That is the whole point: you get to see what a spell
 *  would reach, and who it would catch, before it costs you the slot. */

const COST_PIP: Record<string, string> = {
  action: "◆", bonus: "◇", reaction: "↺", free: "·",
};

const COST_LABEL: Record<string, string> = {
  action: "Action", bonus: "Bonus action", reaction: "Reaction",
  free: "Free",
};

const GROUPS: { key: BarAction["kind"]; label: string }[] = [
  { key: "attack", label: "Attacks" },
  { key: "cast", label: "Spells" },
  { key: "verb", label: "Actions" },
];

export interface ActionBarProps {
  data: ActionBarData | null;
  /** The act currently armed and waiting for the board to be clicked. */
  armed: BarAction | null;
  /** Which slot level an armed spell will be cast with. */
  slot: number | null;
  onArm: (a: BarAction | null) => void;
  onSlot: (n: number) => void;
  /** An act that needs no aiming — sent straight off. */
  onTake: (a: BarAction) => void;
  disabled?: boolean;
}

/** Can the economy still pay for this? The server has already said what the
 *  act costs and what is left; this is the arithmetic between the two. */
function affordable(a: BarAction, d: ActionBarData): string {
  const e = d.economy;
  if (!e.in_combat) return "";        // no turns, no economy to spend
  if (!e.my_turn) return `It's ${e.whose_turn || "someone else"}'s turn`;
  if (a.cost === "action" && e.action === false) {
    // An Attack action allows several swings; having spent the action is not
    // the same as having spent the attacks it bought.
    const more = (e.attacks_made ?? 0) < (e.attacks_per_action ?? 1);
    if (!(a.kind === "attack" && more)) return "Action already spent";
  }
  if (a.cost === "bonus" && e.bonus === false) return "Bonus action already spent";
  if (a.cost === "reaction" && e.reaction === false) return "Reaction already spent";
  return "";
}

function rangeLabel(a: BarAction): string {
  if (a.targeting === "none") return "self";
  if (a.range_ft == null) return "";
  if (a.range_ft <= 5) return "reach";
  return `${a.range_ft} ft`;
}

export function ActionBar(p: ActionBarProps) {
  const [openGroup, setOpenGroup] = useState<BarAction["kind"] | null>("attack");
  const d = p.data;

  const grouped = useMemo(() => {
    const m = new Map<BarAction["kind"], BarAction[]>();
    for (const a of d?.actions ?? []) {
      const list = m.get(a.kind) ?? [];
      list.push(a);
      m.set(a.kind, list);
    }
    return m;
  }, [d]);

  if (!d || !d.actions.length) return null;
  const e = d.economy;

  const pick = (a: BarAction, why: string) => {
    if (why || !a.enabled) return;
    if (p.armed?.id === a.id) { p.onArm(null); return; }   // click again to cancel
    // Anything that needs a click on the board is ARMED, not taken. Anything
    // that doesn't (Dash, Dodge, a self spell) has nothing to wait for.
    if (a.targeting === "none" || a.targeting === "dm") p.onTake(a);
    else p.onArm(a);
  };

  return (
    <div className={`abar${p.disabled ? " busy" : ""}`}>
      <div className="abar-econ">
        {e.in_combat ? (
          <>
            <span className={`abar-pip${e.action === false ? " spent" : ""}`}
              title={e.action === false ? "Action spent" : "Action available"}>◆</span>
            <span className={`abar-pip${e.bonus === false ? " spent" : ""}`}
              title={e.bonus === false ? "Bonus action spent" : "Bonus action available"}>◇</span>
            <span className={`abar-pip${e.reaction === false ? " spent" : ""}`}
              title={e.reaction === false ? "Reaction spent" : "Reaction available"}>↺</span>
            {e.move_left_ft != null && (
              <span className="abar-move">
                {e.move_left_ft} / {e.speed_ft ?? 0} ft
              </span>
            )}
            {!e.my_turn && (
              <span className="abar-wait">{e.whose_turn || "…"}'s turn</span>
            )}
          </>
        ) : (
          <span className="abar-wait">out of combat</span>
        )}
      </div>

      <div className="abar-groups">
        {GROUPS.map(({ key, label }) => {
          const list = grouped.get(key) ?? [];
          if (!list.length) return null;
          const open = openGroup === key;
          return (
            <div key={key} className={`abar-group${open ? " open" : ""}`}>
              <button className="abar-tab"
                onClick={() => setOpenGroup(open ? null : key)}>
                {label} <em>{list.length}</em>
              </button>
              {open && (
                <div className="abar-list">
                  {list.map((a) => {
                    const why = affordable(a, d);
                    const off = !a.enabled || !!why;
                    const armed = p.armed?.id === a.id;
                    const rng = rangeLabel(a);
                    return (
                      <button
                        key={a.id}
                        className={[
                          "abar-act", `cost-${a.cost}`,
                          armed ? "armed" : "", off ? "off" : "",
                          a.stowed ? "stowed" : "",
                        ].filter(Boolean).join(" ")}
                        disabled={off || p.disabled}
                        title={[
                          a.detail,
                          a.disabled_reason || why,
                          a.note,
                          COST_LABEL[a.cost],
                        ].filter(Boolean).join(" · ")}
                        onClick={() => pick(a, why)}
                      >
                        <span className="abar-cost">{COST_PIP[a.cost] ?? "·"}</span>
                        <span className="abar-name">{a.name}</span>
                        {rng && <span className="abar-range">{rng}</span>}
                        {a.targeting === "area" && a.shape && (
                          <span className="abar-shape">
                            {a.radius_ft || a.length_ft}ft {a.shape}
                          </span>
                        )}
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* An armed act explains itself: what it is waiting for, and — for a
          leveled spell — which slot is about to be spent. Upcasting from
          here is the only way to do it deliberately; describing an upcast in
          prose and hoping the DM reads it is not the same thing. */}
      {p.armed && (
        <div className="abar-armed">
          <span className="abar-armed-name">{p.armed.name}</span>
          <span className="abar-armed-hint">
            {p.armed.targeting === "area"
              ? "click the board to place it"
              : "click a ringed target"}
          </span>
          {(p.armed.slots?.length ?? 0) > 1 && (
            <span className="abar-slots">
              slot
              {p.armed.slots!.map((n) => (
                <button key={n}
                  className={n === (p.slot ?? p.armed!.slots![0]) ? "on" : ""}
                  title={n > (p.armed!.level ?? 0) ? `Upcast at level ${n}` : `Level ${n}`}
                  onClick={() => p.onSlot(n)}>{n}</button>
              ))}
            </span>
          )}
          <button className="abar-cancel" onClick={() => p.onArm(null)}>cancel</button>
        </div>
      )}
    </div>
  );
}
