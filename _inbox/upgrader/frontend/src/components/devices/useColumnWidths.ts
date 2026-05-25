import { useCallback, useEffect, useState } from "react";

/**
 * Per-column width state for the Devices table, persisted to localStorage.
 *
 * Three modes the table can be in:
 *   1. Empty widths object → no custom widths. Table uses table-layout:auto
 *      and the browser sizes columns to their content. This is the default
 *      experience for first-time visitors.
 *   2. Some widths set → that column is pinned at its specified width. Other
 *      columns share whatever space is left. The table switches to
 *      table-layout:fixed to make explicit widths actually take effect.
 *   3. All widths set (via Auto-fit) → every column is pinned. Pre-condition
 *      for predictable drag-to-resize: when you grow column A, column B
 *      shrinks predictably instead of every column shifting like a slinky.
 *
 * localStorage (not URL): widths can be different on different operator
 * machines (resolutions, monitors). Bookmarks shouldn't impose the bookmark
 * author's monitor on you.
 */

const STORAGE_KEY = "devices-col-widths-v1";

export type ColumnKey =
  | "select"
  | "expand"
  | "name"
  | "model"
  | "panos"
  | "ha"
  | "dg"
  | "ts"
  | "status"
  | "precheck"
  | "actions";

export type ColumnWidths = Partial<Record<ColumnKey, number>>;

// Minimum width any column can be dragged to. 40px lets the drag handle stay
// reachable even at the narrowest reasonable setting; anything smaller and
// the handle would be on top of the column content of the next column.
export const MIN_COL_WIDTH = 40;

export function useColumnWidths() {
  const [widths, setWidths] = useState<ColumnWidths>(() => {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      return raw ? (JSON.parse(raw) as ColumnWidths) : {};
    } catch {
      return {};
    }
  });

  useEffect(() => {
    try {
      if (Object.keys(widths).length === 0) {
        localStorage.removeItem(STORAGE_KEY);
      } else {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(widths));
      }
    } catch {
      // localStorage can throw in private browsing / quota-exceeded — fine
      // to ignore; the widths still live in React state for the session.
    }
  }, [widths]);

  // Stable callbacks so they don't churn dependent memoisations downstream.
  const setWidth = useCallback((key: ColumnKey, width: number) => {
    setWidths((prev) => ({
      ...prev,
      [key]: Math.max(MIN_COL_WIDTH, Math.round(width)),
    }));
  }, []);

  const setAll = useCallback((all: ColumnWidths) => {
    setWidths(all);
  }, []);

  const reset = useCallback(() => {
    setWidths({});
  }, []);

  return { widths, setWidth, setAll, reset };
}
