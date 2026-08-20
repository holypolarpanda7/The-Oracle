/** The flat top-down board, behind the `BoardView` interface.
 *
 *  A thin adapter over `vttPaint.ts`, which already had every piece this
 *  contract asks for — `fitView`, `toSquare`, `toScreen` and `paint` — just as
 *  loose functions the component called directly. Nothing about how the flat
 *  board draws changes here; it only stops being the only thing `VttOverlay`
 *  knows how to hold.
 *
 *  Two answers are constant for a board with no third dimension: nothing is
 *  ever in front of anything (`occluded: false`), and every square is the same
 *  distance away (`depth: 0`). The isometric renderer is where those become
 *  real questions.
 *
 *  Retired together with `vttPaint.ts` once the isometric board reaches parity. */
import type { VttScene } from "./types";
import {
  CELL, type BoardView, type PaintState, type TokenPlacement, type View,
} from "./boardView";
import { fitView, paint, toScreen, toSquare } from "./vttPaint";

export function createCanvasBoardView(canvas: HTMLCanvasElement): BoardView {
  return {
    // Looking straight down there is nothing a rotation would reveal, so this
    // one never turns — and says so rather than turning into a board that
    // silently ignores the control.
    canTurn: false,
    fit(scene: VttScene, w: number, h: number): View {
      return fitView(scene, w, h);
    },

    squareAt(view: View, _scene: VttScene, px: number, py: number,
             _level: number): [number, number] | null {
      // Deliberately unclamped, exactly as before: the flat board has always
      // reported the square the pointer is over even when that is past the
      // edge, and the draw pass and the server both already cope. Adding a
      // bounds check here would be a behaviour change wearing a tidy-up.
      return toSquare(view, px, py);
    },

    screenOf(view: View, _scene: VttScene, x: number, y: number, squares: number,
             _level: number, _elevationFt: number): TokenPlacement {
      const [left, top] = toScreen(view, x, y);
      return { left, top, size: CELL * view.scale * squares, depth: 0, occluded: false };
    },

    zoomAt(view: View, px: number, py: number, factor: number): View {
      const scale = Math.max(0.2, Math.min(3, view.scale * factor));
      const k = scale / view.scale;
      // Zoom about the cursor so the square under it stays put.
      return { scale, ox: px - (px - view.ox) * k, oy: py - (py - view.oy) * k };
    },

    backdropRect(): null {
      // The flat board draws its own art inside paint(); there is no separate
      // layer to place.
      return null;
    },

    draw(st: PaintState, w: number, h: number): void {
      if (w === 0 || h === 0) return;
      const dpr = Math.min(2, window.devicePixelRatio || 1);
      if (canvas.width !== w * dpr || canvas.height !== h * dpr) {
        canvas.width = w * dpr;
        canvas.height = h * dpr;
        canvas.style.width = `${w}px`;
        canvas.style.height = `${h}px`;
      }
      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      paint(ctx, w, h, st);
    },

    dispose(): void {
      // A 2D context owns nothing the collector won't take.
    },
  };
}
