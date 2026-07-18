import { useCallback, useEffect, useRef } from "react";

interface Opts {
  minW?: number;
  minH?: number;
  /** Stretch the panel's <img> to fill on resize (for the scroll parchment). */
  fillImg?: boolean;
}

const KEY_PREFIX = "oracle.panel.";

/** Drag-to-resize a panel via a corner grip. Size persists per `id` in
 * localStorage and is restored on mount. Returns a ref for the panel, a
 * pointer-down handler for the grip element, and a reset(). */
export function useResizable(id: string, opts: Opts = {}) {
  const { minW = 240, minH = 150, fillImg = false } = opts;
  const ref = useRef<HTMLDivElement>(null);
  const storeKey = KEY_PREFIX + id;

  const applyFill = useCallback(() => {
    if (!fillImg || !ref.current) return;
    const im = ref.current.querySelector("img");
    if (im) { im.style.height = "100%"; im.style.objectFit = "fill"; }
  }, [fillImg]);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    try {
      const raw = localStorage.getItem(storeKey);
      if (!raw) return;
      const { w, h } = JSON.parse(raw) as { w?: number; h?: number };
      if (w) el.style.width = `${w}px`;
      if (h) el.style.height = `${h}px`;
      applyFill();
    } catch { /* ignore malformed */ }
  }, [storeKey, applyFill]);

  const onGripDown = useCallback((e: React.PointerEvent) => {
    const el = ref.current;
    if (!el) return;
    e.preventDefault();
    e.stopPropagation();
    document.body.classList.add("rez-active");
    const sx = e.clientX, sy = e.clientY, sw = el.offsetWidth, sh = el.offsetHeight;
    applyFill();
    const move = (ev: PointerEvent) => {
      el.style.width = `${Math.max(minW, sw + ev.clientX - sx)}px`;
      el.style.height = `${Math.max(minH, sh + ev.clientY - sy)}px`;
    };
    const up = () => {
      document.body.classList.remove("rez-active");
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      try {
        localStorage.setItem(storeKey, JSON.stringify({ w: el.offsetWidth, h: el.offsetHeight }));
      } catch { /* quota */ }
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }, [minW, minH, storeKey, applyFill]);

  const reset = useCallback(() => {
    const el = ref.current;
    if (el) { el.style.width = ""; el.style.height = ""; }
    try { localStorage.removeItem(storeKey); } catch { /* ignore */ }
  }, [storeKey]);

  return { ref, onGripDown, reset };
}

/** Clear every persisted panel size and reload to the default layout. */
export function resetAllPanels() {
  try {
    for (let i = localStorage.length - 1; i >= 0; i--) {
      const k = localStorage.key(i);
      if (k && k.startsWith(KEY_PREFIX)) localStorage.removeItem(k);
    }
  } catch { /* ignore */ }
  location.reload();
}
