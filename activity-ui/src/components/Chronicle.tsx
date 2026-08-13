import { useState } from "react";
import type { BondRow, ChronicleData, CodexRow, JournalEntry, QuestRow,
              StandingRow, VentureRow } from "../lib/types";

/**
 * The Chronicle — the party's own record, and the people who have an opinion
 * of them. Both tabs are pure reads of state the world already keeps: the
 * append-only WorldEvent log, the QUEST scaffold, and the relationship
 * ledgers. Nothing is stored for the Chronicle's sake, so it can never
 * disagree with the world it reports on.
 */

type Tab = "journal" | "bonds" | "standing" | "codex";

const CODEX_GROUP: Record<string, string> = {
  deity: "Powers", place: "Places", faction: "Factions",
  npc: "People", lore: "Things learned",
};

function DayGroup({ day, rows }: { day: number; rows: JournalEntry[] }) {
  return (
    <div className="chr-day">
      <div className="chr-daymark">Day {day}</div>
      {rows.map((e, i) => (
        <p className="chr-entry" key={i}>
          {e.place && <span className="chr-where">{e.place} · </span>}
          {e.text}
        </p>
      ))}
    </div>
  );
}

function Quest({ q }: { q: QuestRow }) {
  const closed = q.state === "completed" || q.state === "failed";
  return (
    <div className={`chr-quest ${q.state} ${q.tier}`}>
      <div className="chr-qhead">
        <b>{q.name}</b>
        <em className={`chr-qtag ${q.tier}`}>{q.tier}</em>
        {closed && <em className={`chr-qtag ${q.state}`}>{q.state}</em>}
      </div>
      {q.conflict && <p className="chr-qline">{q.conflict}</p>}
      {q.stakes && <p className="chr-qline stakes">If ignored: {q.stakes}</p>}
      {(q.objectives ?? []).length > 0 && (
        <ul className="chr-objs">
          {q.objectives!.map((o, i) => <li key={i}>{o}</li>)}
        </ul>
      )}
      {(q.leads ?? []).length > 0 && (
        <p className="chr-qline leads">Leads: {q.leads!.join(" · ")}</p>
      )}
      <div className="chr-qfoot">
        {q.patron && <em>from {q.patron}</em>}
        {q.reward && <em>reward: {q.reward}</em>}
      </div>
    </div>
  );
}

/** Somebody else's road. Deliberately NOT rendered as a quest card: this is not
 *  work waiting for the party, it is a person getting on with their own life,
 *  and the only thing the party controls is whether they are walking it too. */
function Venture({ v, onInspect }: {
  v: VentureRow;
  onInspect: (n: string) => void;
}) {
  const closed = v.state === "completed" || v.state === "failed";
  const pct = Math.round((Math.min(v.step, v.steps) / Math.max(1, v.steps)) * 100);
  return (
    <div className={`chr-venture ${v.state}${v.with_you ? " riding" : ""}`
                    + (v.against_you ? " opposed" : "")}>
      <div className="chr-qhead">
        <button className="chr-vwho" onClick={() => onInspect(v.owner)}>
          {v.owner}
        </button>
        {v.with_you && <em className="chr-qtag comp">you ride with them</em>}
        {v.against_you && <em className="chr-qtag opp">you work against them</em>}
        {closed && <em className={`chr-qtag ${v.state}`}>{v.state}</em>}
      </div>
      <p className="chr-qline">wants to {v.goal}</p>
      {!closed && v.now && (
        <p className="chr-qline leads">
          Step {v.step} of {v.steps} — {v.now}
        </p>
      )}
      {!closed && v.steps > 1 && (
        <div className="chr-rbar"><span style={{ width: `${pct}%` }} /></div>
      )}
      {closed && v.outcome && <p className="chr-qline stakes">{v.outcome}</p>}
    </div>
  );
}

function Bond({ b, onInspect }: { b: BondRow; onInspect: (n: string) => void }) {
  return (
    <button className="chr-bond" onClick={() => onInspect(b.name)}>
      <span className="chr-bhead">
        <b>{b.name}</b>
        {b.companion && <em className="chr-btag comp">travels with you</em>}
        {b.status && <em className="chr-btag gone">{b.status}</em>}
      </span>
      <span className="chr-bmeta">
        {b.role && <em className="chr-brole">{b.role}</em>}
        {b.feeling && <em className={`chr-feel ${b.feeling}`}>{b.feeling}</em>}
        {b.attitude && !b.feeling && <em className="chr-feel">{b.attitude}</em>}
      </span>
      {b.reason && <span className="chr-why">“{b.reason}”</span>}
    </button>
  );
}

function Standing({ s }: { s: StandingRow }) {
  // Renown is only legible against the next rung, so the bar measures the gap
  // rather than an absolute the player has no scale for.
  const span = (s.needed ?? 0) + 1;
  const pct = s.next ? Math.max(6, Math.round(100 / span)) : 100;
  return (
    <div className="chr-standing">
      <div className="chr-qhead">
        <b>{s.faction}</b>
        {s.standing && <em className={`chr-qtag st-${s.standing}`}>{s.standing}</em>}
        <em className="chr-renown">{s.renown} renown</em>
      </div>
      {s.perks && <p className="chr-qline">{s.perks}</p>}
      {s.next && (
        <>
          <div className="chr-rbar"><span style={{ width: `${pct}%` }} /></div>
          <p className="chr-qline leads">
            {s.needed} more to be {s.next}.
          </p>
        </>
      )}
      {s.note && <p className="chr-qline leads">{s.note}</p>}
    </div>
  );
}

function Codex({ rows, onInspect }: {
  rows: CodexRow[];
  onInspect: (n: string) => void;
}) {
  const groups: { key: string; rows: CodexRow[] }[] = [];
  for (const r of rows) {
    const last = groups[groups.length - 1];
    if (last && last.key === r.kind) last.rows.push(r);
    else groups.push({ key: r.kind, rows: [r] });
  }
  return (
    <>
      {groups.map((g) => (
        <div key={g.key}>
          <div className="chr-head">{CODEX_GROUP[g.key] ?? g.key}</div>
          {g.rows.map((r) => (
            <button className="chr-codex" key={r.slug} onClick={() => onInspect(r.name)}>
              <span className="chr-bhead">
                <b className={r.script ? `script-${r.script}` : undefined}>{r.name}</b>
                {r.subtype && <em className="chr-brole">{r.subtype}</em>}
                {r.status && <em className="chr-btag gone">{r.status}</em>}
              </span>
              {r.group && <em className="chr-brole">{r.group}</em>}
              {r.note && <span className="chr-why">{r.note}</span>}
            </button>
          ))}
        </div>
      ))}
    </>
  );
}

export function Chronicle({ data, onClose, onInspect }: {
  data: ChronicleData | null;
  onClose: () => void;
  onInspect: (name: string) => void;
}) {
  const [tab, setTab] = useState<Tab>("journal");
  if (!data) return null;

  // Entries arrive newest-first; group them under their world day so a long
  // record reads as a chronicle rather than an undifferentiated list.
  const days: { day: number; rows: JournalEntry[] }[] = [];
  for (const e of data.entries) {
    const last = days[days.length - 1];
    if (last && last.day === e.day) last.rows.push(e);
    else days.push({ day: e.day, rows: [e] });
  }
  const live = data.quests.filter((q) => q.state === "offered" || q.state === "active");
  const closed = data.quests.filter((q) => q.state === "completed" || q.state === "failed");
  // Ventures are other people's, so they sort by whether the party is on them
  // first and by whether they are still running second — the one you are
  // walking is the one you need to see.
  const ventures = [...(data.ventures ?? [])].sort((a, b) =>
    Number(b.with_you || b.against_you) - Number(a.with_you || a.against_you)
    || Number(a.state !== "active") - Number(b.state !== "active"));

  return (
    <div className="chr-veil" onClick={onClose}>
      <div className="chr" onClick={(e) => e.stopPropagation()}>
        <button className="chr-close" onClick={onClose} aria-label="Close">✕</button>
        <div className="chr-title">The Chronicle</div>
        <div className="chr-tabs">
          <button className={`chr-tab${tab === "journal" ? " on" : ""}`}
                  onClick={() => setTab("journal")}>Journal</button>
          <button className={`chr-tab${tab === "bonds" ? " on" : ""}`}
                  onClick={() => setTab("bonds")}>
            Bonds{data.bonds.length ? ` · ${data.bonds.length}` : ""}
          </button>
          <button className={`chr-tab${tab === "standing" ? " on" : ""}`}
                  onClick={() => setTab("standing")}>
            Standing{data.standing.length ? ` · ${data.standing.length}` : ""}
          </button>
          <button className={`chr-tab${tab === "codex" ? " on" : ""}`}
                  onClick={() => setTab("codex")}>
            Codex{data.codex.length ? ` · ${data.codex.length}` : ""}
          </button>
        </div>

        {data.error && <p className="chr-none">{data.error}</p>}

        {tab === "journal" && (
          <div className="chr-body">
            {live.length > 0 && (
              <>
                <div className="chr-head">Open threads</div>
                {live.map((q) => <Quest q={q} key={q.name} />)}
              </>
            )}
            {closed.length > 0 && (
              <>
                <div className="chr-head">Settled</div>
                {closed.map((q) => <Quest q={q} key={q.name} />)}
              </>
            )}
            {ventures.length > 0 && (
              <>
                <div className="chr-head">Other people's roads</div>
                {ventures.map((v) => (
                  <Venture v={v} key={v.name} onInspect={onInspect} />
                ))}
              </>
            )}
            <div className="chr-head">What happened</div>
            {days.length === 0
              ? <p className="chr-none">Nothing is written here yet.</p>
              : days.map((d) => <DayGroup day={d.day} rows={d.rows} key={d.day} />)}
          </div>
        )}

        {tab === "bonds" && (
          <div className="chr-body">
            {data.bonds.length === 0
              ? <p className="chr-none">No one has formed an opinion of you yet.</p>
              : data.bonds.map((b) => <Bond b={b} key={b.slug} onInspect={onInspect} />)}
          </div>
        )}

        {tab === "standing" && (
          <div className="chr-body">
            {data.standing.length === 0
              ? <p className="chr-none">No faction has taken notice of you.</p>
              : data.standing.map((s) => <Standing s={s} key={s.slug} />)}
          </div>
        )}

        {tab === "codex" && (
          <div className="chr-body">
            {data.codex.length === 0
              ? <p className="chr-none">
                  You have learned nothing of this world yet. It fills as you go.
                </p>
              : <Codex rows={data.codex} onInspect={onInspect} />}
          </div>
        )}
      </div>
    </div>
  );
}
