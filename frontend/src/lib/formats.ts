/**
 * File types the upload accepts. Mirrors the server's list (GET /api/files/formats),
 * which checks every file's content: spreadsheets and text tables, PDF/Word tables,
 * MT940 (.sta/.mt940/.940, or .txt), CAMT.053 (.xml) and BAI2 (.bai/.bai2, or .txt)
 * bank statements, and GSTR-2B returns (.json).
 */
export const UPLOAD_ACCEPT = ".xlsx,.xls,.csv,.tsv,.txt,.pdf,.docx,.sta,.mt940,.940,.xml,.bai,.bai2,.json";

export const UPLOAD_FORMATS_HINT = "Excel, CSV/TSV, PDF or Word tables, MT940, CAMT.053 or BAI2 bank statements, and GSTR-2B JSON";
