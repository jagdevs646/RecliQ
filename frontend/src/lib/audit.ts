/** Plain-language names for the audit actions the server records. The code stays
 * available (filter value, tooltip, exports) for anyone matching logs by it. */
export const AUDIT_ACTIONS: Array<[code: string, label: string]> = [
  ["file.uploaded", "File uploaded"],
  ["file.rejected", "File rejected"],
  ["precheck.run", "Data check run"],
  ["job.created", "Reconciliation started"],
  ["job.completed", "Reconciliation completed"],
  ["job.failed", "Reconciliation failed"],
  ["job.cancelled", "Reconciliation cancelled"],
  ["job.deleted", "Reconciliation deleted"],
  ["job.pruned", "Old reconciliation removed"],
  ["report.downloaded", "Report downloaded"],
  ["report.customized", "Custom report created"],
  ["template.created", "Saved setup created"],
  ["template.updated", "Saved setup changed"],
  ["template.run", "Saved setup run"],
  ["template.archived", "Saved setup removed"],
  ["match.accepted", "Match confirmed"],
  ["match.rejected", "Match rejected"],
  ["match.reset", "Rejection withdrawn"],
  ["alias.created", "Name alias added"],
  ["alias.removed", "Name alias removed"],
  ["rule.created", "Auto-resolution rule created"],
  ["rule.updated", "Auto-resolution rule changed"],
  ["rule.archived", "Auto-resolution rule removed"],
  ["rule.reordered", "Auto-resolution rules reordered"],
  ["exceptions.auto_resolved", "Exceptions auto-resolved"],
  ["audit.exported", "Audit log exported"],
];

const LABELS = new Map(AUDIT_ACTIONS);

export function auditActionLabel(code: string): string {
  return LABELS.get(code) ?? code;
}
