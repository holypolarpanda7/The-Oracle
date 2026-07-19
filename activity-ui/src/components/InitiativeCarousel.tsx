import { useEffect, useRef } from "react";
import type { CombatState, CombatantView } from "../lib/types";

/** BG3-style initiative carousel — slides into its own strip at the top of the
    play surface while a fight is live. Cards run left→right in initiative
    order; the creature whose turn it is stands taller and burns brighter. */

function hpMood(hp: number, max: number): string {
  const f = hp / Math.max(1, max);
  return f <= 0.25 ? "dire" : f <= 0.6 ? "hurt" : "";
}

/** Two-letter monogram: "Goblin 2" -> "G2", "Bandit Captain" -> "BC". */
function monogram(name: string): string {
  const words = name.trim().split(/\s+/);
  const tail = words[words.length - 1];
  if (words.length > 1 && /^\d+$/.test(tail)) return words[0][0].toUpperCase() + tail;
  if (words.length > 1) return (words[0][0] + tail[0]).toUpperCase();
  return name.slice(0, 2).toUpperCase();
}

const COVER_LABEL: Record<string, string> = {
  half: "½ cover", "three-quarters": "¾ cover", total: "total cover",
};

function Card({ c, active }: { c: CombatantView; active: boolean }) {
  const max = Math.max(1, c.max_hp);
  const hpPct = Math.min(100, (100 * c.current_hp) / max);
  const tempPct = (100 * (c.temp_hp || 0)) / max;
  const cover = COVER_LABEL[c.cover ?? "none"];
  const conds = [
    ...(c.conditions || []),
    ...(c.concentration ? [`⟟ ${c.concentration}`] : []),
  ];
  return (
    <div
      className={[
        "cs-card", `kind-${c.kind}`,
        active ? "active" : "",
        c.defeated ? "down" : "",
      ].join(" ").trim()}
      title={`${c.name} — ${c.current_hp}/${c.max_hp} HP` +
        (c.temp_hp ? ` (+${c.temp_hp} temp)` : "") +
        (c.armor_class != null ? ` · AC ${c.armor_class}` : "")}
    >
      <span className="cs-init">{c.initiative}</span>
      <div className="cs-face">
        <span className="cs-mono">{c.defeated ? "☠" : monogram(c.name)}</span>
      </div>
      <div className="cs-nm">{c.name}</div>
      {c.position && <div className="cs-pos" title={c.position}>{c.position}</div>}
      {active && !c.defeated && (
        <div className="cs-econ" title="This turn: action · bonus · movement">
          <span className={`ce a ${c.action_used ? "spent" : ""}`}
                title={c.action_used ? "Action spent" : "Action available"}>A</span>
          <span className={`ce b ${c.bonus_used ? "spent" : ""}`}
                title={c.bonus_used ? "Bonus action spent" : "Bonus action available"}>B</span>
          <span className={`ce m ${(c.move_left ?? 1) <= 0 ? "spent" : ""}`}
                title={`Movement: ${c.move_left ?? 1} step(s) left`}>M</span>
        </div>
      )}
      <div className={`cs-bar ${hpMood(c.current_hp, c.max_hp)} ${c.temp_hp > 0 ? "has-temp" : ""}`}>
        <span className="hp-fill" style={{ width: `${hpPct}%` }} />
        {c.temp_hp > 0 && (
          <span className="hp-temp" style={{ left: `${hpPct}%`, width: `${tempPct}%` }} />
        )}
      </div>
      {(conds.length > 0 || cover) && (
        <div className="cs-conds">
          {cover && <span className="cs-cond cs-cover" key="cover">🛡 {cover}</span>}
          {conds.map((x) => <span className="cs-cond" key={x}>{x}</span>)}
        </div>
      )}
    </div>
  );
}

export function InitiativeCarousel({ combat }: { combat: CombatState }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    // A fight just opened. The play surface may be scrolled down (and Chrome's
    // scroll anchoring would keep it there) — bring the carousel into view.
    ref.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, []);
  return (
    <div className="combat-strip" ref={ref}>
      <div className="cs-head">
        <span className="cs-round">⚔ Round {combat.round}</span>
        <span className="cs-title">{combat.name}</span>
      </div>
      <div className="cs-cards">
        {combat.combatants.map((c) => (
          <Card key={c.id} c={c} active={c.id === combat.current_combatant_id} />
        ))}
      </div>
    </div>
  );
}
