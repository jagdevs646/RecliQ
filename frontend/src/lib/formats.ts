/**
 * File types the upload accepts. Mirrors the server's list (GET /api/files/formats),
 * which checks every file's content: spreadsheets and text tables, PDF/Word tables,
 * MT940 (.sta/.mt940/.940, or .txt), CAMT.053 (.xml) and BAI2 (.bai/.bai2, or .txt)
 * bank statements, and GSTR-2B returns (.json).
 */
export const UPLOAD_ACCEPT = ".xlsx,.xls,.csv,.tsv,.txt,.pdf,.docx,.sta,.mt940,.940,.xml,.bai,.bai2,.json";

export const UPLOAD_FORMATS_HINT = "Excel, CSV/TSV, PDF or Word tables, MT940, CAMT.053 or BAI2 bank statements, and GSTR-2B JSON";

// ── Record values on screen ─────────────────────────────────────────────
const PERCENT_COLUMN = "Difference %";
const TWO_DECIMALS: Intl.NumberFormatOptions = { minimumFractionDigits: 2, maximumFractionDigits: 2 };
// A plain decimal with more than two places, optionally a percentage. Codes ("007.1234", "1.2.3") do not qualify.
const LONG_DECIMAL_TEXT = /^-?(0|[1-9]\d*)\.\d{3,}%?$/;

function hasFraction(value: unknown): boolean {
  return typeof value === "number" && Number.isFinite(value) && !Number.isInteger(value);
}

/** Columns holding at least one fractional number: all their numbers are shown to 2 decimals. Whole-number columns (keys, row numbers) are left alone. */
export function decimalColumns(rows: Array<Record<string, unknown>>): Set<string> {
  const columns = new Set<string>();
  for (const row of rows) for (const [column, value] of Object.entries(row)) if (hasFraction(value)) columns.add(column);
  return columns;
}

/**
 * A record value as shown in the results table: numbers to 2 decimal places.
 * Display only: the stored value and the downloaded workbook keep full precision.
 */
export function formatRecordValue(value: unknown, column: string, decimals?: Set<string>): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "number" && Number.isFinite(value)) {
    // Stored as a fraction of the destination value (0.1235 = 12.35%).
    if (column === PERCENT_COLUMN) return `${(value * 100).toLocaleString(undefined, TWO_DECIMALS)}%`;
    return decimals?.has(column) || hasFraction(value) ? value.toLocaleString(undefined, TWO_DECIMALS) : String(value);
  }
  if (typeof value === "string" && LONG_DECIMAL_TEXT.test(value)) {
    const percent = value.endsWith("%");
    return `${Number(percent ? value.slice(0, -1) : value).toFixed(2)}${percent ? "%" : ""}`;
  }
  return String(value);
}

/**
 * Reads a timestamp from the server. The API sends UTC with an offset, but a value
 * without one ("2026-09-30T19:19:50.472435") is still UTC: `new Date` alone would
 * read it as local time and shift it by the viewer's UTC offset.
 */
export function parseServerTime(value: string): Date {
  const text = value.trim().replace(" ", "T").replace(/(\.\d{3})\d+/, "$1");
  const hasTime = /T\d{2}:\d{2}/.test(text);
  const hasZone = /(Z|[+-]\d{2}:?\d{2})$/i.test(text);
  return new Date(hasTime && !hasZone ? `${text}Z` : text);
}

/** A server timestamp in the viewer's time zone and locale; `fallback` when there is none. */
export function formatServerTime(value: string | null | undefined, options?: Intl.DateTimeFormatOptions, fallback = ""): string {
  if (!value) return fallback;
  const date = parseServerTime(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString(undefined, options);
}
