import { useMemo, useState } from "react";
import type { BastionPlan } from "../lib/types";

/** Raising a stronghold: what the rules allow, and what the player imagines.
 *
 *  Two halves, deliberately unlike each other. The LEFT is the game's — kind,
 *  facilities, a vessel, a running total against a real purse, and every
 *  refusal quoted back from the server, which is the only thing that decides.
 *  The RIGHT is the player's, and nothing on it is validated or refused: a
 *  name, what the place looks like, what it is known for. A builder that
 *  argues with somebody's description of their own home is one nobody uses
 *  twice.
 *
 *  The client prices NOTHING it acts on. It shows a running total so the
 *  choosing makes sense, and the server re-checks and charges — the
 *  Quartermaster's rule, for the same reason.
 */
export function BastionBuilder({ plan, onBuild, onClose, busy, error }: {
  plan: BastionPlan;
  onBuild: (choice: {
    kind: string; name: string; description: string; motif: string;
    facilities: string[]; vessel_slug: string; vehicle_kind: string;
  }) => void;
  onClose: () => void;
  busy?: boolean;
  error?: string | null;
}) {
  const first = plan.kinds.find((k) => k.available)?.slug ?? "keep";
  const [kind, setKind] = useState(first);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [motif, setMotif] = useState("");
  const [picked, setPicked] = useState<string[]>([]);
  const [vessel, setVessel] = useState("");
  const [vehicle, setVehicle] = useState("");

  const vesselCost = useMemo(
    () => plan.vessels.find((v) => v.slug === vessel)?.cost_gp ?? 0,
    [plan.vessels, vessel]);
  const total = picked.length * plan.cost_per_facility_gp
    + (kind === "airship" ? vesselCost : 0);
  const overspent = total > plan.purse_gp;
  const needsMover = (kind === "mobile" || kind === "airship")
    && !picked.some((s) => plan.facilities.find((f) => f.slug === s)?.propulsion);

  const toggle = (slug: string) =>
    setPicked((p) => p.includes(slug) ? p.filter((s) => s !== slug) : [...p, slug]);

  if (plan.existing) {
    return (
      <div className="bb-wrap" onClick={onClose}>
        <div className="bb" onClick={(e) => e.stopPropagation()}>
          <div className="bb-head">
            <span className="bb-title">{plan.existing.name}</span>
            <button className="bb-x" onClick={onClose}>✕</button>
          </div>
          <p className="bb-note">
            You already hold a bastion. It is {plan.existing.kind === "keep"
              ? "a fixed place" : plan.existing.kind === "airship"
              ? "a flying one" : "a travelling one"}, with{" "}
            {plan.existing.facilities.length} facilit
            {plan.existing.facilities.length === 1 ? "y" : "ies"}.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="bb-wrap" onClick={onClose}>
      <div className="bb" onClick={(e) => e.stopPropagation()}>
        <div className="bb-head">
          <span className="bb-title">Raise a Bastion</span>
          <span className="bb-purse">{plan.purse_gp.toLocaleString()} gp</span>
          <button className="bb-x" onClick={onClose} aria-label="Close">✕</button>
        </div>

        {!plan.can_own ? (
          <p className="bb-note">
            A bastion needs level {plan.min_level}; you are {plan.level}.
          </p>
        ) : (
          <div className="bb-body">
            {/* ---- the game's half ---- */}
            <div className="bb-col">
              <div className="bb-lbl">What is it</div>
              <div className="bb-kinds">
                {plan.kinds.map((k) => (
                  <button
                    key={k.slug}
                    className={`bb-kind${kind === k.slug ? " on" : ""}`}
                    disabled={!k.available}
                    title={k.why || k.blurb}
                    onClick={() => setKind(k.slug)}
                  >
                    <b>{k.name}</b>
                    <span>{k.available ? k.blurb : k.why}</span>
                  </button>
                ))}
              </div>

              {kind === "airship" && (
                <>
                  <div className="bb-lbl">Her hull</div>
                  <select className="bb-sel" value={vessel}
                          onChange={(e) => setVessel(e.target.value)}>
                    <option value="">choose a vessel…</option>
                    {plan.vessels.map((v) => (
                      <option key={v.slug} value={v.slug}>
                        {v.name} — crew {v.crew ?? "?"}, {v.cost_gp
                          ? `${v.cost_gp.toLocaleString()} gp` : "no price"}
                      </option>
                    ))}
                  </select>
                </>
              )}
              {kind === "mobile" && (
                <>
                  <div className="bb-lbl">What carries it</div>
                  <input className="bb-in" value={vehicle} maxLength={80}
                         placeholder="a barge, a walking hall on six legs…"
                         onChange={(e) => setVehicle(e.target.value)} />
                </>
              )}

              <div className="bb-lbl">
                Facilities
                <span className="bb-dim">
                  {" "}{plan.cost_per_facility_gp.toLocaleString()} gp each
                </span>
              </div>
              <ul className="bb-facs">
                {plan.facilities.map((f) => (
                  <li key={f.slug} className={picked.includes(f.slug) ? "on" : ""}>
                    <label>
                      <input type="checkbox" checked={picked.includes(f.slug)}
                             onChange={() => toggle(f.slug)} />
                      <span className="bb-fn">{f.name}</span>
                      {f.propulsion && <span className="bb-tag">moves it</span>}
                      {f.income_gp > 0 && (
                        <span className="bb-tag earn">+{f.income_gp} gp</span>
                      )}
                      <span className="bb-fd">{f.desc}</span>
                    </label>
                  </li>
                ))}
              </ul>
            </div>

            {/* ---- the player's half: nothing here is refused ---- */}
            <div className="bb-col">
              <div className="bb-lbl">Its name</div>
              <input className="bb-in" value={name} maxLength={80}
                     placeholder="The Gilded Sow"
                     onChange={(e) => setName(e.target.value)} />
              <div className="bb-lbl">What it looks like</div>
              <textarea className="bb-ta" value={description} maxLength={400}
                        rows={5}
                        placeholder="A brass hall slung beneath a whale-shaped envelope, lamps burning at every rail…"
                        onChange={(e) => setDescription(e.target.value)} />
              <div className="bb-lbl">What it is known for</div>
              <input className="bb-in" value={motif} maxLength={200}
                     placeholder="pigs in gold leaf, everywhere"
                     onChange={(e) => setMotif(e.target.value)} />
              <p className="bb-free">
                None of this is checked. It is what the world will say about
                your bastion, and what its picture will be drawn from.
              </p>
            </div>
          </div>
        )}

        {plan.can_own && (
          <div className="bb-foot">
            <div className={`bb-total${overspent ? " over" : ""}`}>
              {total.toLocaleString()} gp
              {overspent && <span className="bb-warn"> — more than you have</span>}
              {needsMover && (
                <span className="bb-warn">
                  {" "}— nothing aboard could move it
                </span>
              )}
            </div>
            {error && <div className="bb-err">{error}</div>}
            <button
              className="bb-go"
              disabled={!!busy || !name.trim() || overspent || needsMover
                        || (kind === "airship" && !vessel)}
              onClick={() => onBuild({
                kind, name, description, motif,
                facilities: picked, vessel_slug: vessel, vehicle_kind: vehicle,
              })}
            >{busy ? "Raising…" : "Raise it"}</button>
          </div>
        )}
      </div>
    </div>
  );
}
