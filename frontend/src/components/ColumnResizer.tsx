import { useCallback, useEffect, useState, type KeyboardEvent, type PointerEvent } from "react";

const STORAGE_KEY = "recliq.review-table.layout";
const MIN_WIDTH = 60;
const MAX_WIDTH = 1200;
const FIT_LIMIT = 900; // Fitting a very long comment stops here; wrap text shows the rest.
const KEY_STEP = 20;

interface Layout { widths: Record<string, number>; wrap: boolean }

function load(): Layout {
  try {
    const stored = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "{}");
    const widths = Object.fromEntries(Object.entries(stored.widths ?? {}).filter(([, width]) => typeof width === "number"));
    return { widths: widths as Record<string, number>, wrap: stored.wrap === true };
  } catch {
    return { widths: {}, wrap: false };
  }
}

const clamp = (width: number) => Math.round(Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, width)));

/**
 * Column widths and text wrapping chosen by the reviewer, remembered in this
 * browser. Columns are identified by name, so a width set on COMMENT applies
 * wherever that column appears.
 */
export function useColumnLayout() {
  const [layout, setLayout] = useState<Layout>(load);
  useEffect(() => {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(layout)); } catch { /* Storage unavailable: keep it for this visit. */ }
  }, [layout]);

  const setWidth = useCallback((column: string, width: number) => setLayout((current) => ({ ...current, widths: { ...current.widths, [column]: clamp(width) } })), []);
  const toggleWrap = useCallback(() => setLayout((current) => ({ ...current, wrap: !current.wrap })), []);
  const resetWidths = useCallback(() => setLayout((current) => ({ ...current, widths: {} })), []);
  const widthStyle = useCallback((column: string) => {
    const width = layout.widths[column];
    return width ? { width, maxWidth: width } : undefined;
  }, [layout.widths]);

  return { widths: layout.widths, wrap: layout.wrap, setWidth, toggleWrap, resetWidths, widthStyle, customized: Object.keys(layout.widths).length > 0 };
}

/** Width of the column's content box (the cell without its padding). */
function contentWidth(handle: HTMLElement): number {
  const cell = handle.closest("th");
  if (!cell) return MIN_WIDTH;
  const style = getComputedStyle(cell);
  return cell.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
}

/** Width that shows the longest value in the column on one line. */
function fittedWidth(handle: HTMLElement): number {
  const header = handle.closest("th");
  const table = handle.closest("table");
  if (!header || !table) return MIN_WIDTH;
  table.classList.add("is-measuring");
  let widest = 0;
  for (const row of Array.from(table.rows)) {
    const cell = row.cells[header.cellIndex];
    if (!cell || cell.colSpan > 1) continue;
    const text = cell.querySelector<HTMLElement>(".cell-text");
    if (text) widest = Math.max(widest, text.getBoundingClientRect().width);
  }
  table.classList.remove("is-measuring");
  return Math.min(FIT_LIMIT, Math.ceil(widest) + 2);
}

interface Props { column: string; width?: number; onResize: (width: number) => void }

/** Drag handle on a header's right edge. Double-click (or Enter) fits the column to its text. */
export function ColumnResizer({ column, width, onResize }: Props) {
  const [dragging, setDragging] = useState(false);

  function startDrag(event: PointerEvent<HTMLSpanElement>) {
    if (event.button !== 0) return;
    event.preventDefault();
    const handle = event.currentTarget;
    const startX = event.clientX;
    const startWidth = contentWidth(handle);
    try { handle.setPointerCapture(event.pointerId); } catch { /* The pointer is already gone. */ }
    setDragging(true);
    document.body.classList.add("is-resizing-column");
    const move = (moveEvent: globalThis.PointerEvent) => onResize(startWidth + moveEvent.clientX - startX);
    const end = () => {
      handle.removeEventListener("pointermove", move);
      handle.removeEventListener("pointerup", end);
      handle.removeEventListener("pointercancel", end);
      document.body.classList.remove("is-resizing-column");
      setDragging(false);
    };
    handle.addEventListener("pointermove", move);
    handle.addEventListener("pointerup", end);
    handle.addEventListener("pointercancel", end);
  }

  function onKeyDown(event: KeyboardEvent<HTMLSpanElement>) {
    const step = event.shiftKey ? KEY_STEP * 4 : KEY_STEP;
    if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
      event.preventDefault();
      onResize(contentWidth(event.currentTarget) + (event.key === "ArrowRight" ? step : -step));
    } else if (event.key === "Enter") {
      event.preventDefault();
      onResize(fittedWidth(event.currentTarget));
    }
  }

  return <span
    className={`column-resizer${dragging ? " is-dragging" : ""}`}
    role="separator"
    aria-orientation="vertical"
    aria-label={`Resize ${column} column`}
    aria-valuemin={MIN_WIDTH}
    aria-valuemax={MAX_WIDTH}
    aria-valuenow={width}
    tabIndex={0}
    title="Drag to resize · double-click to fit the text"
    onPointerDown={startDrag}
    onDoubleClick={(event) => onResize(fittedWidth(event.currentTarget))}
    onKeyDown={onKeyDown}
  />;
}
