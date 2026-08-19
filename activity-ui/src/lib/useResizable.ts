import { useCallback, useEffect, useRef } from "react";

interface Opts {
  minW?: number;
  minH?: number;
  /** "y" drags height only — for panels whose width is set by the column they
   *  sit in, like the tactical board. */
  axis?: "both" | "y";
}

const KEY_PREFIX = "oracle.panel.";

/** The signal a reset sends. Every live panel listens and clears its own
 *  inline size, which is the whole of what a reset has to do. */
const RESET_EVENT = "oracle:panels-reset";

/** Forget one panel's persisted size — for panels that stop being resizable,
 *  whose stale stored height would otherwise pin the new layout. */
export function dropPanel(id: string) {
  try { localStorage.removeItem(KEY_PREFIX + id); } catch { /* ignore */ }
}

/** Drag-to-resize a panel via a corner grip. Size persists per `id` in
 * localStorage and is restored on mount. Returns a ref for the panel, a
 * pointer-down handler for the grip element, and a reset(). */
export function useResizable(id: string, opts: Opts = {}) {
  const { minW = 240, minH = 150, axis = "both" } = opts;
  // Mutable so a caller can share one element between this and its own ref
  // (the tactical board needs both).
  const ref = useRef<HTMLDivElement | null>(null);
  const storeKey = KEY_PREFIX + id;

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    try {
      const raw = localStorage.getItem(storeKey);
      if (!raw) return;
      const { w, h } = JSON.parse(raw) as { w?: number; h?: number };
      if (w) el.style.width = `${w}px`;
      if (h) el.style.height = `${h}px`;
    } catch { /* ignore malformed */ }
  }, [storeKey]);

  const onGripDown = useCallback((e: React.PointerEvent) => {
    const el = ref.current;
    if (!el) return;
    e.preventDefault();
    e.stopPropagation();
    document.body.classList.add("rez-active");
    const sx = e.clientX, sy = e.clientY, sw = el.offsetWidth, sh = el.offsetHeight;
    const move = (ev: PointerEvent) => {
      if (axis !== "y") el.style.width = `${Math.max(minW, sw + ev.clientX - sx)}px`;
      el.style.height = `${Math.max(minH, sh + ev.clientY - sy)}px`;
    };
    const up = () => {
      document.body.classList.remove("rez-active");
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      try {
        localStorage.setItem(storeKey, JSON.stringify(
          axis === "y" ? { h: el.offsetHeight }
                       : { w: el.offsetWidth, h: el.offsetHeight }));
      } catch { /* quota */ }
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }, [minW, minH, storeKey, axis]);

  const reset = useCallback(() => {
    const el = ref.current;
    if (el) { el.style.width = ""; el.style.height = ""; }
    try { localStorage.removeItem(storeKey); } catch { /* ignore */ }
  }, [storeKey]);

  // A global reset clears this panel too, without a reload — see resetAllPanels.
  useEffect(() => {
    const onReset = () => {
      const el = ref.current;
      if (el) { el.style.width = ""; el.style.height = ""; }
    };
    window.addEventListener(RESET_EVENT, onReset);
    return () => window.removeEventListener(RESET_EVENT, onReset);
  }, []);

  return { ref, onGripDown, reset };
}

/** Clear every persisted panel size and put the panels back to their defaults.
 *
 *  It used to call `location.reload()`, which is a much bigger hammer than the
 *  button says: reloading the Activity drops the socket, and the fresh one is
 *  bound to no session — so pressing "Reset Layout" in the middle of a fight
 *  put the player back on the landing with a bout still running behind them.
 *  Nothing about resetting a panel's HEIGHT requires throwing the table away. */
export function resetAllPanels() {
  try {
    for (let i = localStorage.length - 1; i >= 0; i--) {
      const k = localStorage.key(i);
      if (k && k.startsWith(KEY_PREFIX)) localStorage.removeItem(k);
    }
  } catch { /* ignore */ }
  try {
    window.dispatchEvent(new CustomEvent(RESET_EVENT));
  } catch { /* ignore */ }
}
