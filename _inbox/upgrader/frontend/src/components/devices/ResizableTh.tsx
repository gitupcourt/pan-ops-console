import { type ReactNode, useRef } from "react";
import { MIN_COL_WIDTH, type ColumnKey } from "./useColumnWidths";

/**
 * A table header cell with a drag-to-resize handle on its right edge.
 *
 * The handle is an absolutely-positioned 6px strip that takes the full
 * height of the cell. It captures pointer events at the document level
 * during drag, so the cursor stays in "col-resize" mode even when the
 * user drags past the handle (a common UX papercut with naive
 * implementations).
 *
 * Width source of truth lives in the parent's column-widths state. When
 * the user starts a drag we read the th's current rendered width via
 * the ref — works whether or not a custom width was already set —
 * then apply deltas as the cursor moves and finally commit on mouseup.
 *
 * `onResizeStart` is called once at the start of the drag so the parent
 * can pin every other column to its current rendered width before this
 * one starts changing. Without that, growing column A would cause every
 * other column to silently shrink as the browser redistributes space —
 * jarring and impossible to predict.
 */
export function ResizableTh({
  colKey,
  width,
  align = "left",
  className = "",
  children,
  onResize,
  onResizeStart,
}: {
  colKey: ColumnKey;
  width: number | undefined;
  align?: "left" | "right";
  className?: string;
  children?: ReactNode;
  onResize: (key: ColumnKey, width: number) => void;
  onResizeStart?: () => void;
}) {
  const thRef = useRef<HTMLTableCellElement>(null);

  const handleMouseDown = (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    const th = thRef.current;
    if (!th) return;

    // Snapshot rendered width at drag start. If a width was already set
    // explicitly we'd get that value; if not, we get whatever the browser
    // chose for auto-layout. Either way, it's the right starting point.
    const startX = e.clientX;
    const startWidth = th.getBoundingClientRect().width;

    onResizeStart?.();

    const handleMove = (ev: MouseEvent) => {
      const next = Math.max(MIN_COL_WIDTH, startWidth + (ev.clientX - startX));
      onResize(colKey, next);
    };
    const handleUp = () => {
      document.removeEventListener("mousemove", handleMove);
      document.removeEventListener("mouseup", handleUp);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };
    document.addEventListener("mousemove", handleMove);
    document.addEventListener("mouseup", handleUp);
    // Keep the resize cursor everywhere during the drag so the operator
    // doesn't see it flicker if they wander outside the handle. Also kill
    // text selection so dragging doesn't accidentally select cell text.
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
  };

  return (
    <th
      ref={thRef}
      // data-col-key lets the parent measure all headers in one querySelectorAll
      // pass when Auto-fit is clicked, without holding a separate ref per
      // column.
      data-col-key={colKey}
      className={`relative px-3 py-2 ${align === "right" ? "text-right" : "text-left"} ${className}`}
      style={width != null ? { width, minWidth: width } : undefined}
    >
      {children}
      <span
        onMouseDown={handleMouseDown}
        title="Drag to resize column"
        // Make the handle visible on hover so it's discoverable without
        // dominating the header bar in the default state.
        className="absolute right-0 top-0 z-10 h-full w-1.5 cursor-col-resize select-none bg-transparent transition-colors hover:bg-indigo-500/60 active:bg-indigo-500"
      />
    </th>
  );
}
