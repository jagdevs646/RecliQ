/**
 * File types the upload accepts. Mirrors the server's list (GET /api/files/formats),
 * which checks every file's content: spreadsheets and text tables, PDF/Word tables,
 * MT940 (.sta/.mt940/.940, or .txt), CAMT.053 (.xml) and BAI2 (.bai/.bai2, or .txt)
 * bank statements, and GSTR-2B returns (.json).
 */
export const UPLOAD_ACCEPT = ".xlsx,.xls,.csv,.tsv,.txt,.pdf,.docx,.sta,.mt940,.940,.xml,.bai,.bai2,.json";

export const UPLOAD_FORMATS_HINT = "Excel, CSV/TSV, PDF or Word tables, MT940, CAMT.053 or BAI2 bank statements, and GSTR-2B JSON";

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
