import { describe, expect, it } from "vitest";
import {
  availableColumns,
  copyRuleSettings,
  derivedColumns,
  describePass,
  draftToSheetRule,
  emptyDraft,
  newPass,
  precheckBlocked,
  precheckNeedsAcknowledgement,
  precheckSummary,
  ruleIsReady,
  ruleStatus,
  transformationIsComplete,
} from "./plan";
import type { PrecheckResult, SheetRuleDraft } from "../types";

function draft(overrides: Partial<SheetRuleDraft> = {}): SheetRuleDraft {
  return { ...emptyDraft(["INVOICE", "VENDOR", "AMOUNT", "DATE", "DEBIT", "CREDIT"], ["INVOICE NO", "VENDOR", "AMOUNT", "DATE"], null), ...overrides };
}

describe("rule readiness", () => {
  it("requires a key, unless a complete amount/date pass is configured", () => {
    expect(ruleIsReady(draft())).toBe(false);
    expect(ruleIsReady(draft({ primaryKeySource: ["INVOICE"], primaryKeyDestination: ["INVOICE NO"] }))).toBe(true);

    const keyless = draft();
    keyless.matchingPasses = [newPass("amount_date", keyless)];
    expect(keyless.matchingPasses[0]).toMatchObject({ amount_source: "AMOUNT", date_source: "DATE", date_window_days: 3 });
    expect(ruleIsReady(keyless)).toBe(true);
    expect(ruleStatus(keyless)).toContain("No key (amount/date matching)");
  });

  it("blocks incomplete passes and preparation steps", () => {
    const base = draft({ primaryKeySource: ["INVOICE"], primaryKeyDestination: ["INVOICE NO"] });
    expect(ruleIsReady({ ...base, matchingPasses: [{ ...newPass("amount_date", base), date_source: "" }] })).toBe(false);
    expect(ruleIsReady({ ...base, transformations: [{ operation: "remove_prefix", side: "source", columns: ["INVOICE"], params: {} }] })).toBe(false);
    expect(ruleStatus({ ...base, transformations: [{ operation: "remove_prefix", side: "source", columns: ["INVOICE"], params: {} }] })).toBe("Finish the value preparation steps");
  });

  it("keeps the date-only guard for keyed rules", () => {
    const dateOnly = draft({ primaryKeySource: ["DATE"], primaryKeyDestination: ["DATE"] });
    expect(ruleIsReady(dateOnly)).toBe(false);
    expect(ruleIsReady({ ...dateOnly, dateOnlyOverride: true })).toBe(true);
  });
});

describe("transformations", () => {
  it("expose derived signed-amount columns on their side only", () => {
    const config = draft({ transformations: [{ operation: "debit_credit_to_signed", side: "source", columns: [], params: { debit_column: "DEBIT", credit_column: "CREDIT" }, output_column: "Signed Amount" }] });
    expect(derivedColumns(config, "source")).toEqual(["SIGNED AMOUNT"]);
    expect(availableColumns(config, "source")).toContain("SIGNED AMOUNT");
    expect(availableColumns(config, "destination")).not.toContain("SIGNED AMOUNT");
  });

  it("validate required parameters", () => {
    expect(transformationIsComplete({ operation: "trim", side: "both", columns: ["VENDOR"], params: {} })).toBe(true);
    expect(transformationIsComplete({ operation: "replace_text", side: "both", columns: ["VENDOR"], params: { find: "" } })).toBe(false);
    expect(transformationIsComplete({ operation: "debit_credit_to_signed", side: "both", columns: [], params: { debit_column: "DEBIT", credit_column: "CREDIT" } })).toBe(false);
  });
});

describe("plan building", () => {
  it("sends passes, normalization, preparation and the date format", () => {
    const config = draft({ primaryKeySource: ["INVOICE"], primaryKeyDestination: ["INVOICE NO"], dateFormat: "month_first" });
    config.matchingPasses = [newPass("amount_tolerance", config)];
    config.transformations = [{ operation: "invert_sign", side: "destination", columns: ["AMOUNT"], params: {} }];
    const rule = draftToSheetRule(config, { sheetRuleId: "rule-1-1", sourceSheet: "Books", destinationSheet: "Bank", label: "Books -> Bank" });
    expect(rule.date_format).toBe("month_first");
    expect(rule.matching_strategy.matching_passes[0]).toMatchObject({ type: "amount_tolerance", narrative_source: null });
    expect(rule.matching_strategy.normalization.legal_forms).toBe(true);
    expect(rule.transformations).toHaveLength(1);
  });

  it("sends empty keys for a keyless rule", () => {
    const config = draft();
    config.matchingPasses = [newPass("amount_date", config)];
    const rule = draftToSheetRule(config, { sheetRuleId: "r", sourceSheet: "A", destinationSheet: "B", label: "A -> B" });
    expect(rule.matching_strategy.primary_key_source).toEqual([]);
    expect(describePass(config.matchingPasses[0], 1)).toBe("Pass 1: Same amount and date within ±3 days");
  });

  it("copies settings between rules, dropping columns the target lacks", () => {
    const source = draft({ primaryKeySource: ["INVOICE"], primaryKeyDestination: ["INVOICE NO"], dateFormat: "month_first" });
    source.matchingPasses = [newPass("amount_date", source)];
    const target = emptyDraft(["INVOICE", "AMOUNT"], ["INVOICE NO", "AMOUNT"], null);
    const copied = copyRuleSettings(source, target);
    expect(copied.primaryKeySource).toEqual(["INVOICE"]);
    expect(copied.matchingPasses).toEqual([]); // No DATE column in the target.
    expect(copied.dateFormat).toBe("month_first");
  });
});

describe("pre-check gate", () => {
  const result = (blocker: number, warning: number): PrecheckResult => ({
    status: blocker ? "blockers" : warning ? "warnings" : "ok",
    summary: { blocker, warning, info: 1 },
    rules: [{ file_pair_id: "p", sheet_rule_id: "r", label: "R", date_format: "day_first", issues: [
      { severity: "warning", category: "blank_keys", side: "source", column: "INVOICE", message: "m", count: 3, examples: [], suggestion: null },
      { severity: "info", category: "key_overlap", side: null, column: null, message: "i", count: 9, examples: [], suggestion: null },
    ] }],
  });

  it("blocks on blockers and asks for confirmation on warnings", () => {
    expect(precheckBlocked(result(1, 0))).toBe(true);
    expect(precheckBlocked(result(0, 2))).toBe(false);
    expect(precheckNeedsAcknowledgement(result(0, 2))).toBe(true);
    expect(precheckNeedsAcknowledgement(result(0, 0))).toBe(false);
  });

  it("summarizes only actionable issues for the audit log", () => {
    const summary = precheckSummary(result(0, 1));
    expect(summary.issues).toEqual([{ rule: "r", severity: "warning", category: "blank_keys", count: 3 }]);
  });
});
