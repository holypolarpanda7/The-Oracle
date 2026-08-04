/** Board sprites — the object and wreckage pictures the overlay draws.
 *
 *  Kept out of vttPaint.ts on purpose: that module is a pure draw pass, and
 *  loading images is neither pure nor cheap to repeat. Kept out of the
 *  component too, because a sprite is shared by KIND — every pillar in every
 *  room in every session is one picture — so the cache belongs to the module,
 *  not to whichever board happens to be open.
 *
 *  The bytes come from /imagery/sprite/<id>, which is the server matting the
 *  same sprite the Discord board composites. A browser cannot run rembg, and
 *  two views cutting their own pillars differently is exactly the kind of
 *  disagreement the grid-is-truth rule exists to prevent. */

const CACHE = new Map<number, HTMLImageElement>();

/** Every sprite fetched so far, for paint() to look up by id. */
export const SPRITES: ReadonlyMap<number, HTMLImageElement> = CACHE;

/** Start loading any sprite ids not already held. `onLoad` fires per arrival,
 *  so the caller can repaint — a board that opens before its art lands should
 *  fill in rather than wait. */
export function loadSprites(ids: Iterable<number>, onLoad: () => void): void {
  for (const id of ids) {
    if (!id || CACHE.has(id)) continue;
    const img = new Image();
    img.decoding = "async";
    img.addEventListener("load", onLoad);
    img.addEventListener("error", () => {
      // A sprite that won't load is not a broken board: the tile beneath it is
      // already correct, and paint() draws nothing rather than a broken image.
      CACHE.delete(id);
    });
    img.src = `/imagery/sprite/${id}`;
    CACHE.set(id, img);
  }
}
