import { useMemo, useState } from "react";
import type { BastionPlan } from "../lib/types";

/** Raising a stronghold, and going on building it.
 *
 *  Two halves, deliberately unlike each other. The LEFT is the game's — kind,
 *  facilities, a vessel, a running total against a real purse, and every
 *  refusal quoted back from the server, which is the only thing that decides.
 *  The RIGHT is the player's, and nothing on it is validated or refused: a
 *  name, what the place looks like, what it is known for, and the ordinary
 *  rooms under whatever they call them. A builder that argues with somebody's
 *  description of their own home is one nobody uses twice.
 *
 *  HOW MANY facilities is a level entitlement rather than a purchase — the
 *  rules hand out another at 9, 13 and 17 — so the screen counts SLOTS, and a
 *  bastion that already exists opens here again to add to it rather than as a
 *  card saying you have one.
 *
 *  The client prices NOTHING it acts on. It shows a running total so the
 *  choosing makes sense, and the server re-checks and charges — the
 *  Quartermaster's rule, for the same reason.
 */
export function BastionBuilder({ plan, onBuild, onEnlarge, onClose, busy, error }: {
  plan: BastionPlan;
  onBuild: (choice: {
    kind: string; name: string; description: string; motif: string;
    facilities: string[];
    rooms: { slug: string; name: string; description: string }[];
    vessel_slug: string; vehicle_kind: string;
  }) => void;
  /** Order the building work on one facility. Paid now, finished on a turn. */
  onEnlarge: (facilityId: number) => void;
  onClose: () => void;
  busy?: boolean;
  error?: string | null;
}) {
  const held = plan.existing ?? null;
  const first = plan.kinds.find((k) => k.available)?.slug ?? "keep";
  const [kind, setKind] = useState(held?.kind ?? first);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [motif, setMotif] = useState("");
  const [picked, setPicked] = useState<string[]>([]);
  const [rooms, setRooms] = useState<
    { slug: string; name: string; description: string }[]>([]);
  const [vessel, setVessel] = useState("");
  const [vehicle, setVehicle] = useState("");

  const vesselCost = useMemo(
    () => plan.vessels.find((v) => v.slug === vessel)?.cost_gp ?? 0,
    [plan.vessels, vessel]);
  const total = picked.length * plan.cost_per_facility_gp
    + rooms.length * plan.cost_per_room_gp
    + (!held && kind === "airship" ? vesselCost : 0);
  const overspent = total > plan.purse_gp;
  const specialLeft = plan.special_slots - plan.special_used - picked.length;
  const roomLeft = plan.basic_slots - plan.basic_used - rooms.length;
  const needsMover = !held && (kind === "mobile" || kind === "airship")
    && !picked.some((s) => plan.facilities.find((f) => f.slug === s)?.propulsion);
  const unnamedRoom = rooms.some((r) => !r.name.trim());

  const toggle = (slug: string) => {
    if (!picked.includes(slug) && specialLeft <= 0) return;
    setPicked((p) => p.includes(slug) ? p.filter((s) => s !== slug) : [...p, slug]);
  };
  const addRoom = (slug: string) => {
    if (roomLeft <= 0) return;
    setRooms((r) => [...r, { slug, name: "", description: "" }]);
  };
  const setRoom = (i: number, patch: Partial<{ name: string; description: string }>) =>
    setRooms((r) => r.map((row, j) => j === i ? { ...row, ...patch } : row));

  const nothingChosen = picked.length === 0 && rooms.length === 0;

  return (
    <div className="bb-wrap" onClick={onClose}>
      <div className="bb" onClick={(e) => e.stopPropagation()}>
        <div className="bb-head">
          <span className="bb-title">
            {held ? held.name : "Raise a Bastion"}
          </span>
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
              {held ? (
                <p className="bb-note">
                  {held.notes || "Yours already."} Another special facility
                  comes at levels 9, 13 and 17.
                </p>
              ) : (
                <>
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
                </>
              )}

              {held?.installed?.length ? (
                <>
                  <div className="bb-lbl">
                    What stands here
                    <span className="bb-dim"> · enlarging takes bastion turns</span>
                  </div>
                  <ul className="bb-built">
                    {held.installed.map((f) => (
                      <li key={f.id}>
                        <span className="bb-fn">{f.name}</span>
                        <span className="bb-size">
                          {f.space} · holds {f.holds}
                        </span>
                        {f.enlarging_to ? (
                          <span className="bb-tag works">
                            building → {f.enlarging_to}
                          </span>
                        ) : f.can_enlarge ? (
                          <button className="bb-add" disabled={!!busy}
                                  title={`Then holds ${f.then_holds}, produces `
                                         + `x${f.then_output} — ${f.turns} bastion `
                                         + `turn${f.turns === 1 ? "" : "s"}`}
                                  onClick={() => onEnlarge(f.id)}>
                            → {f.to_space} · {f.cost_gp.toLocaleString()} gp
                          </button>
                        ) : (
                          <span className="bb-why">{f.why}</span>
                        )}
                      </li>
                    ))}
                  </ul>
                </>
              ) : null}

              <div className="bb-lbl">
                Special facilities
                <span className="bb-dim">
                  {" "}{plan.special_used + picked.length} of {plan.special_slots}
                  {" · "}{plan.cost_per_facility_gp.toLocaleString()} gp each
                </span>
              </div>
              <ul className="bb-facs">
                {plan.facilities.map((f) => {
                  const owned = held?.facilities.includes(f.slug);
                  return (
                    <li key={f.slug}
                        className={picked.includes(f.slug) ? "on"
                                   : owned ? "owned" : ""}>
                      <label>
                        <input type="checkbox" checked={!!picked.includes(f.slug)}
                               disabled={owned
                                 || (specialLeft <= 0 && !picked.includes(f.slug))}
                               onChange={() => toggle(f.slug)} />
                        <span className="bb-fn">{f.name}</span>
                        {owned && <span className="bb-tag">built</span>}
                        {f.propulsion && <span className="bb-tag">moves it</span>}
                        {f.income_gp > 0 && (
                          <span className="bb-tag earn">+{f.income_gp} gp</span>
                        )}
                        <span className="bb-fd">{f.desc}</span>
                      </label>
                    </li>
                  );
                })}
              </ul>
            </div>

            {/* ---- the player's half: nothing here is refused ---- */}
            <div className="bb-col">
              {!held && (
                <>
                  <div className="bb-lbl">Its name</div>
                  <input className="bb-in" value={name} maxLength={80}
                         placeholder="The Gilded Sow"
                         onChange={(e) => setName(e.target.value)} />
                  <div className="bb-lbl">What it looks like</div>
                  <textarea className="bb-ta" value={description} maxLength={400}
                            rows={4}
                            placeholder="A brass hall slung beneath a whale-shaped envelope, lamps burning at every rail…"
                            onChange={(e) => setDescription(e.target.value)} />
                  <div className="bb-lbl">What it is known for</div>
                  <input className="bb-in" value={motif} maxLength={200}
                         placeholder="pigs in gold leaf, everywhere"
                         onChange={(e) => setMotif(e.target.value)} />
                </>
              )}

              <div className="bb-lbl">
                Rooms
                <span className="bb-dim">
                  {" "}{plan.basic_used + rooms.length} of {plan.basic_slots}
                  {" · "}{plan.cost_per_room_gp.toLocaleString()} gp each
                </span>
              </div>
              <div className="bb-adds">
                {plan.basics.map((b) => (
                  <button key={b.slug} className="bb-add" disabled={roomLeft <= 0}
                          title={b.desc} onClick={() => addRoom(b.slug)}>
                    + {b.name}
                  </button>
                ))}
              </div>
              {held?.rooms?.length ? (
                <p className="bb-free">
                  Already yours: {held.rooms.map((r) => r.name).join(", ")}
                </p>
              ) : null}
              <ul className="bb-rooms">
                {rooms.map((r, i) => (
                  <li key={i}>
                    <span className="bb-kindtag">
                      {plan.basics.find((b) => b.slug === r.slug)?.name ?? r.slug}
                    </span>
                    <input className="bb-in" value={r.name} maxLength={80}
                           placeholder="call it whatever it is"
                           onChange={(e) => setRoom(i, { name: e.target.value })} />
                    <input className="bb-in bb-sub" value={r.description}
                           maxLength={200} placeholder="and what it's like, if you like"
                           onChange={(e) => setRoom(i, { description: e.target.value })} />
                    <button className="bb-x" aria-label="Remove"
                            onClick={() => setRooms((all) =>
                              all.filter((_, j) => j !== i))}>✕</button>
                  </li>
                ))}
              </ul>
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
                <span className="bb-warn"> — nothing aboard could move it</span>
              )}
              {unnamedRoom && (
                <span className="bb-warn"> — every room needs a name</span>
              )}
            </div>
            {error && <div className="bb-err">{error}</div>}
            <button
              className="bb-go"
              disabled={!!busy || overspent || needsMover || unnamedRoom
                        || (held ? nothingChosen : !name.trim())
                        || (!held && kind === "airship" && !vessel)}
              onClick={() => onBuild({
                kind, name, description, motif,
                facilities: picked, rooms,
                vessel_slug: vessel, vehicle_kind: vehicle,
              })}
            >{busy ? (held ? "Building…" : "Raising…")
                   : (held ? "Build it" : "Raise it")}</button>
          </div>
        )}
      </div>
    </div>
  );
}
