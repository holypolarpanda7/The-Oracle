import type { WorldShop } from "../lib/types";

/** A merchant's wares, in the WORLD — not the arena's Quartermaster.
 *
 *  Deliberately thin. Everything a shop actually decides happens on the server:
 *  what is in stock (rolled from the merchant, the settlement's scale and the
 *  world's week, so it is the same roll the DM's own context line carries),
 *  what it costs, and whether the purse covers it. A price computed here would
 *  be a second answer to a question the game has already answered — the same
 *  rule the Quartermaster follows, and the reason `onBuy` sends a NAME rather
 *  than a transaction.
 *
 *  Nothing is disabled for being unaffordable: a refusal comes back as a line
 *  of narration ("your purse comes up short"), which is the answer in the
 *  fiction's own voice, and greying it out would quietly hide what the world
 *  contains from a player who is merely poor today.
 */
export function Stall({ shop, onBuy, onClose }: {
  shop: WorldShop;
  onBuy: (item: string) => void;
  onClose: () => void;
}) {
  return (
    <div className="stall-wrap" onClick={onClose}>
      <div className="stall" onClick={(e) => e.stopPropagation()}>
        <div className="stall-head">
          <span className="stall-title">Wares</span>
          <span className="stall-purse">{shop.purse_text}</span>
          <button className="stall-x" onClick={onClose} aria-label="Close">✕</button>
        </div>
        {shop.stalls.map((s) => (
          <div className="stall-who" key={s.slug}>
            <div className="stall-name">
              {s.name}<span className="stall-role"> · {s.role}</span>
            </div>
            <ul className="stall-list">
              {s.stock.map((it) => (
                <li key={`${s.slug}:${it.name}`}>
                  <span className="stall-item">{it.name}</span>
                  <span className="stall-price">{formatGp(it.price_gp)}</span>
                  <button className="stall-buy" onClick={() => onBuy(it.name)}>
                    Buy
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </div>
  );
}

/** Prices carry fractions (a flask of oil is a tenth of a gold), so the coin
 *  they are quoted in changes with the price — 0.1 gp is a silver, not "0 gp". */
function formatGp(gp: number): string {
  if (gp >= 1) return `${Number(gp.toFixed(2))} gp`;
  if (gp >= 0.1) return `${Math.round(gp * 10)} sp`;
  return `${Math.max(1, Math.round(gp * 100))} cp`;
}
