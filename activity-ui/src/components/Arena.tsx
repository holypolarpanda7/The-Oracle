import { useEffect, useMemo, useState } from "react";
import type { ArenaEnv, ArenaEquipLine, ArenaOutfitLine, ArenaSlot,
              ArenaState } from "../lib/types";
import { uiTick } from "../lib/sound";

const DOMAIN_LABEL: Record<string, string> = {
  land: "On land", sea: "At sea", air: "In the air",
};
const DOMAIN_ICON: Record<string, string> = { land: "⛰", sea: "🌊", air: "☁" };
const MODE_TAG: Record<string, string> = { swim: "swim", fly: "fly", walk: "" };

/** The Proving Grounds: pick a fighter, a place, and a level, then fight.
 *  Three slots, overwritable — practice characters are meant to be spent. */
export function Arena({ state, onCreate, onDelete, onBegin, onBack }: {
  state: ArenaState | null;
  onCreate: (slot: number) => void;
  onDelete: (slot: number) => void;
  onBegin: (opts: { slot: number; environment: string; level: number;
                    difficulty: string; reuse: boolean }) => void;
  onBack: () => void;
}) {
  const [slot, setSlot] = useState<number | null>(null);
  const [env, setEnv] = useState<string>("");
  const [level, setLevel] = useState(1);
  const [difficulty, setDifficulty] = useState("medium");
  const [reuse, setReuse] = useState(false);

  const slots = state?.slots ?? [];
  const chosen: ArenaSlot | undefined = slots.find((s) => s.slot === slot);
  const envs = state?.environments ?? [];
  const byDomain = envs.reduce<Record<string, ArenaEnv[]>>((acc, e) => {
    (acc[e.domain] ||= []).push(e);
    return acc;
  }, {});

  // A slot emptied under us (deleted, overwritten) must not stay selected.
  useEffect(() => {
    if (slot !== null && chosen && !chosen.character) setSlot(null);
  }, [slot, chosen]);

  // "Fight on" is only on offer while the leveled copy matches the chosen level.
  const canReuse = !!chosen?.leveled && chosen.leveled.level === level;
  useEffect(() => { if (!canReuse) setReuse(false); }, [canReuse]);

  if (!state) {
    return (
      <div className="arena">
        <div className="arena-head"><h2>The Proving Grounds</h2></div>
        <p className="arena-sub">Opening the gate…</p>
      </div>
    );
  }

  return (
    <div className="arena">
      <div className="arena-head">
        <h2>The Proving Grounds</h2>
        <p className="arena-sub">
          Practice bouts outside the world. Nothing here is remembered —
          fight, fall, and stand up whole.
        </p>
      </div>

      {/* ---- 1. who fights ---- */}
      <div className="arena-step">
        <div className="arena-step-label">① Your fighters</div>
        <div className="arena-slots">
          {slots.map((s) => (
            <div
              key={s.slot}
              className={`arena-slot${slot === s.slot ? " picked" : ""}${
                s.character ? "" : " empty"}`}
              onClick={() => { if (s.character) { uiTick(); setSlot(s.slot); } }}
            >
              <div className="as-num">Slot {s.slot}</div>
              {s.character ? (
                <>
                  <div className="as-name">{s.character.name}</div>
                  <div className="as-sub">
                    Level {s.character.level} {s.character.char_class}
                    {s.character.race ? ` · ${s.character.race}` : ""}
                  </div>
                  {s.leveled && (
                    <div className="as-leveled">
                      advanced copy: level {s.leveled.level}
                    </div>
                  )}
                  <div className="as-actions">
                    <button onClick={(e) => { e.stopPropagation(); uiTick(); onCreate(s.slot); }}>
                      Replace
                    </button>
                    <button onClick={(e) => { e.stopPropagation(); uiTick(); onDelete(s.slot); }}>
                      Clear
                    </button>
                  </div>
                </>
              ) : (
                <button className="as-forge"
                        onClick={() => { uiTick(); onCreate(s.slot); }}>
                  ⚒ Forge a fighter
                </button>
              )}
            </div>
          ))}
        </div>
      </div>

      {/* ---- 2. where ---- */}
      <div className={`arena-step${slot === null ? " dim" : ""}`}>
        <div className="arena-step-label">② Where you fight</div>
        {Object.entries(byDomain).map(([domain, list]) => (
          <div key={domain} className="arena-domain">
            <div className="ad-label">
              {DOMAIN_ICON[domain]} {DOMAIN_LABEL[domain] ?? domain}
            </div>
            <div className="arena-envs">
              {list.map((e) => (
                <button
                  key={e.slug}
                  className={`arena-env${env === e.slug ? " picked" : ""}`}
                  onClick={() => { uiTick(); setEnv(e.slug); }}
                >
                  <div className="ae-name">
                    {e.name}
                    {MODE_TAG[e.mode] && <span className="ae-mode">{MODE_TAG[e.mode]}</span>}
                  </div>
                  <div className="ae-blurb">{e.blurb}</div>
                </button>
              ))}
            </div>
          </div>
        ))}
      </div>

      {/* ---- 3. how hard ---- */}
      <div className={`arena-step${slot === null || !env ? " dim" : ""}`}>
        <div className="arena-step-label">③ At what strength</div>
        <div className="arena-dials">
          <label className="arena-dial">
            <span>Fight at level</span>
            <input
              type="range" min={1} max={state.max_level} value={level}
              onChange={(ev) => setLevel(Number(ev.target.value))}
            />
            <b>{level}</b>
          </label>
          <div className="arena-diffs">
            {state.difficulties.map((d) => (
              <button
                key={d}
                className={`arena-diff${difficulty === d ? " picked" : ""}`}
                onClick={() => { uiTick(); setDifficulty(d); }}
              >{d}</button>
            ))}
          </div>
        </div>
        <p className="arena-note">
          {level > 1 && !reuse
            ? `You'll make every level-up choice from 1 to ${level} before the gate opens.`
            : "The bout opens as soon as you step through."}
        </p>
        {canReuse && (
          <label className="arena-reuse">
            <input type="checkbox" checked={reuse}
                   onChange={(e) => setReuse(e.target.checked)} />
            <span>
              Fight on with the copy already advanced to level {level} —
              skip the level-up choices
            </span>
          </label>
        )}
      </div>

      <div className="arena-go">
        <button className="lu-confirm" onClick={onBack}>Back</button>
        <button
          className="lu-confirm"
          disabled={slot === null || !env}
          onClick={() => {
            uiTick();
            if (slot === null || !env) return;
            onBegin({ slot, environment: env, level, difficulty, reuse });
          }}
        >Step through the gate ➤</button>
      </div>
    </div>
  );
}

const RARITY_ORDER = ["common", "uncommon", "rare", "very rare", "legendary"];

/** The Quartermaster's stall: the stipend the Grounds hand out for the level
 *  being fought at, everything it buys, and what gets strapped on before the
 *  wards close. Prices and the rarity gate are the server's — this only asks. */
export function Quartermaster({ state, onOutfit }: {
  state: ArenaState;
  onOutfit: (cart: ArenaOutfitLine[], equip: ArenaEquipLine[]) => void;
}) {
  const shop = state.shop ?? null;
  // slug -> what's in the cart; name -> how owned gear is worn.
  const [cart, setCart] = useState<Record<string, { qty: number; equipped: boolean;
                                                    attuned: boolean }>>({});
  const [equip, setEquip] = useState<Record<string, { equipped: boolean;
                                                      attuned: boolean }>>({});
  const [filter, setFilter] = useState("");
  const [kind, setKind] = useState<"all" | "gear" | "magic">("all");
  const [sent, setSent] = useState(false);

  // Re-opening the stall between bouts restores the loadout you walked out with.
  useEffect(() => {
    if (!shop) return;
    setCart(Object.fromEntries((shop.cart ?? []).map((l) => [
      l.slug, { qty: l.quantity, equipped: l.equipped, attuned: l.attuned }])));
    setEquip(Object.fromEntries((shop.pack ?? []).map((p) => [
      p.name, { equipped: p.equipped, attuned: p.attuned }])));
    setSent(false);
  }, [shop?.purse, shop?.items.length, (shop?.cart ?? []).length]);

  const stock = shop?.items ?? [];
  const bySlug = useMemo(
    () => Object.fromEntries(stock.map((s) => [s.slug, s])), [stock]);

  const spent = Object.entries(cart).reduce(
    (sum, [slug, line]) => sum + (bySlug[slug]?.cost_gp ?? 0) * line.qty, 0);
  const purse = shop?.purse ?? 0;
  const remaining = purse - spent;
  const attuneLimit = shop?.attunement_limit ?? 3;
  const attuned = Object.values(cart).filter((l) => l.attuned).length
    + Object.values(equip).filter((e) => e.attuned).length;

  const q = filter.trim().toLowerCase();
  const shown = stock
    .filter((s) => kind === "all" || s.kind === kind)
    .filter((s) => !q || s.name.toLowerCase().includes(q))
    .slice(0, 100);

  if (!shop) return null;

  const setQty = (slug: string, qty: number) => {
    const it = bySlug[slug];
    if (!it) return;
    setCart((c) => {
      const next = { ...c };
      if (qty <= 0) delete next[slug];
      else next[slug] = {
        qty,
        // Nobody buys a shield to carry it in a sack, and a wondrous item that
        // does nothing unattuned may as well not have been bought.
        equipped: c[slug]?.equipped ?? it.equippable,
        attuned: c[slug]?.attuned ?? (!!it.attunement && attuned < attuneLimit),
      };
      return next;
    });
  };

  const step = (slug: string, by: number) =>
    setQty(slug, (cart[slug]?.qty ?? 0) + by);

  const toggleCart = (slug: string, key: "equipped" | "attuned") =>
    setCart((c) => (c[slug]
      ? { ...c, [slug]: { ...c[slug], [key]: !c[slug][key] } } : c));

  const togglePack = (name: string, key: "equipped" | "attuned") =>
    setEquip((e) => {
      const cur = e[name] ?? { equipped: false, attuned: false };
      return { ...e, [name]: { ...cur, [key]: !cur[key] } };
    });

  const walkOn = () => {
    if (sent) return;
    setSent(true);
    onOutfit(
      Object.entries(cart).map(([slug, line]) => ({
        slug, name: bySlug[slug]?.name ?? slug, quantity: line.qty,
        equipped: line.equipped, attuned: line.attuned })),
      Object.entries(equip).map(([name, e]) => ({
        name, equipped: e.equipped, attuned: e.attuned })));
  };

  return (
    <div className="levelup-veil">
      <div className="levelup quartermaster">
        <div className="levelup-head">
          <span className="lu-title">The Quartermaster</span>
          <span className="lu-arc">level {shop.level} stipend</span>
        </div>
        <p className="qm-blurb">
          Conjured coin for a conjured fight — spend it, wear it, and lose it
          when the run ends. A build is only half a build without the gear it's
          meant to be holding.
        </p>

        <div className="gear-budget">
          <span>Purse <b>{purse.toLocaleString()} gp</b></span>
          <span className={remaining < 0 ? "over" : ""}>
            Remaining <b>{remaining.toLocaleString()} gp</b>
          </span>
          <span className={attuned > attuneLimit ? "over" : ""}>
            Attuned <b>{attuned}/{attuneLimit}</b>
          </span>
        </div>

        {shop.pack.length > 0 && (
          <div className="qm-pack">
            <div className="qm-label">What you already carry</div>
            <div className="gear-list qm-packlist">
              {shop.pack.map((p) => {
                const st = equip[p.name] ?? { equipped: p.equipped, attuned: p.attuned };
                return (
                  <div key={p.name} className={`gear-row ${st.equipped || st.attuned ? "in" : ""}`}>
                    <span className="gear-name">
                      {p.name}{p.quantity > 1 ? ` ×${p.quantity}` : ""}
                    </span>
                    <div className="qm-flags">
                      <button className={`qm-flag ${st.equipped ? "on" : ""}`}
                              onClick={() => { uiTick(); togglePack(p.name, "equipped"); }}>
                        {st.equipped ? "worn" : "stowed"}
                      </button>
                      {p.attunement && (
                        <button className={`qm-flag ${st.attuned ? "on" : ""}`}
                                disabled={!st.attuned && attuned >= attuneLimit}
                                onClick={() => { uiTick(); togglePack(p.name, "attuned"); }}>
                          attuned
                        </button>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        <div className="qm-label">The stall</div>
        <div className="qm-filters">
          {(["all", "gear", "magic"] as const).map((k) => (
            <button key={k} className={`arena-diff${kind === k ? " picked" : ""}`}
                    onClick={() => { uiTick(); setKind(k); }}>{k}</button>
          ))}
          <input className="gear-search" placeholder="search the stall…"
                 value={filter} onChange={(e) => setFilter(e.target.value)} />
        </div>

        <div className="gear-list qm-stall">
          {shown.map((it) => {
            const line = cart[it.slug];
            const qty = line?.qty ?? 0;
            const canAdd = spent + it.cost_gp <= purse;
            return (
              <div key={it.slug} className={`gear-row ${qty ? "in" : ""}`}>
                <span className="gear-name">
                  {it.name}
                  {it.rarity && (
                    <span className={`qm-rarity r${RARITY_ORDER.indexOf(
                      it.rarity.toLowerCase())}`}>{it.rarity}</span>
                  )}
                  {it.attunement && <span className="qm-attune">attunement</span>}
                  {it.brief && <span className="qm-brief">{it.brief}</span>}
                </span>
                <span className="gear-cost">{it.cost_gp.toLocaleString()} gp</span>
                {qty > 0 && (
                  <div className="qm-flags">
                    {it.equippable && (
                      <button className={`qm-flag ${line?.equipped ? "on" : ""}`}
                              onClick={() => { uiTick(); toggleCart(it.slug, "equipped"); }}>
                        {line?.equipped ? "worn" : "stowed"}
                      </button>
                    )}
                    {it.attunement && (
                      <button className={`qm-flag ${line?.attuned ? "on" : ""}`}
                              disabled={!line?.attuned && attuned >= attuneLimit}
                              onClick={() => { uiTick(); toggleCart(it.slug, "attuned"); }}>
                        attuned
                      </button>
                    )}
                  </div>
                )}
                <div className="gear-qty">
                  <button disabled={qty <= 0}
                          onClick={() => { uiTick(); step(it.slug, -1); }}>−</button>
                  <span>{qty}</span>
                  <button disabled={!canAdd}
                          onClick={() => { uiTick(); step(it.slug, 1); }}>+</button>
                </div>
              </div>
            );
          })}
          {shown.length === 0 && (
            <p className="cf-hint">
              Nothing here matches — or the rules library has no priced items yet.
            </p>
          )}
        </div>
        {stock.length > shown.length && (
          <p className="cf-hint">
            Showing {shown.length} of {stock.length} — search to narrow.
          </p>
        )}

        {shop.rejected.length > 0 && (
          <p className="cf-hint qm-rejected">{shop.rejected.join(" · ")}</p>
        )}

        <div className="lu-actions" style={{ gap: 10 }}>
          <button className="lu-confirm" disabled={sent}
                  onClick={() => { uiTick(); setCart({}); }}>
            Buy nothing
          </button>
          <button className="lu-confirm" disabled={sent || remaining < 0}
                  onClick={() => { uiTick(); walkOn(); }}>
            {sent ? "The wards close…" : "Step through the gate ➤"}
          </button>
        </div>
      </div>
    </div>
  );
}

/** Shown over the play surface once a bout is decided: fight on, or walk out. */
export function ArenaResult({ state, onAgain, onElsewhere, onOutfit, onLeave }: {
  state: ArenaState;
  onAgain: () => void;
  onElsewhere: (environment: string) => void;
  onOutfit: () => void;
  onLeave: () => void;
}) {
  const run = state.run;
  const [picking, setPicking] = useState(false);
  if (!run || run.phase !== "resolved") return null;
  const here = state.environments.find((e) => e.slug === run.environment);
  const won = run.result === "victory";

  return (
    <div className="levelup-veil">
      <div className="levelup arena-result">
        <div className="levelup-head">
          <span className="lu-title">{won ? "The Ward Dims" : "The Ward Catches You"}</span>
          <span className="lu-arc">{run.roster}</span>
        </div>
        <p className="arena-tally">
          {won ? "Every one of them is down." : "You went down first."}
          {" "}Bout {run.fights} in {here?.name ?? "the Grounds"} —
          {" "}{run.wins ?? 0} won of {run.fights ?? 0}.
        </p>

        {picking ? (
          <div className="arena-envs">
            {state.environments.map((e) => (
              <button key={e.slug} className="arena-env"
                      onClick={() => { uiTick(); onElsewhere(e.slug); }}>
                <div className="ae-name">{e.name}</div>
                <div className="ae-blurb">{e.blurb}</div>
              </button>
            ))}
          </div>
        ) : (
          <div className="lu-actions" style={{ gap: 10 }}>
            <button className="lu-confirm" onClick={() => { uiTick(); onAgain(); }}>
              Again, here
            </button>
            <button className="lu-confirm" onClick={() => { uiTick(); setPicking(true); }}>
              Somewhere else
            </button>
            <button className="lu-confirm" onClick={() => { uiTick(); onOutfit(); }}>
              Back to the stall
            </button>
            <button className="lu-confirm" onClick={() => { uiTick(); onLeave(); }}>
              Leave the Grounds
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
