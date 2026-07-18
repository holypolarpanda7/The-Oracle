import type { ReactNode, Ref, PointerEvent } from "react";

export const CORNER = "/assets/frames/corner.webp";

export interface PanelHandle {
  ref: Ref<HTMLDivElement>;
  onGripDown: (e: PointerEvent) => void;
}

/** The ornate panel frame: four tiled edge bands + four L-shaped corner pieces
 * that overhang the edges. Pass `panel` to make it drag-resizable. */
export function Frame({ className = "", panel, children }: {
  className?: string;
  panel?: PanelHandle;
  children: ReactNode;
}) {
  return (
    <div className={`cframe ${className}`} ref={panel?.ref}>
      <span className="e top" /><span className="e bot" />
      <span className="e lft" /><span className="e rgt" />
      <img className="cc tl" src={CORNER} alt="" />
      <img className="cc tr" src={CORNER} alt="" />
      <img className="cc bl" src={CORNER} alt="" />
      <img className="cc br" src={CORNER} alt="" />
      {children}
      {panel && <div className="grip" title="Drag to resize" onPointerDown={panel.onGripDown} />}
    </div>
  );
}
