import { describe, expect, it } from "vitest";
import {
  applyReadyMadeSetup,
  readyMadeSetupApplied,
  readyMadeSetupRelevant,
  availableColumns,
  copyRuleSettings,
  derivedColumns,
  describePass,
  describeTolerance,
  draftToSheetRule,
  emptyDraft,
  limitsFromPasses,
  mappingIsReady,
  mappingSetupStatus,
  matchingSetupStatus,
  newPass,
  precheckBlocked,
  precheckNeedsAcknowledgement,
  precheckSummary,
  pruneTolerances,
  ruleIsReady,
  clearTolerance,
  dateAmountMappingIsReady,
  shouldSuggestDateAmount,
  guessToleranceKind,
  setToleranceLimit,
  toleranceFor,
  toleranceKind,
  toleranceLimit,
  ruleStatus,
  sheetRuleToDraft,
  suggestSheetPair,
  transformationIsComplete,
} from "./plan";
import type { PrecheckResult, ReadyMadeSetup, SheetRuleDraft } from "../types";

function draft(overrides: Partial<SheetRuleDraft> = {}): SheetRuleDraft {
  return { ...emptyDraft(["INVOICE", "VENDOR", "AMOUNT", "DATE", "DEBIT", "CREDIT"], ["INVOICE NO", "VENDOR", "AMOUNT", "DATE"], null), ...overrides };
}

describe("tolerance bands", () => {
  const mapped = () => draft({
    primaryKeySource: ["INVOICE"], primaryKeyDestination: ["INVOICE NO"],
    rules: [{ file_1_fields: ["DEBIT"], file_2_fields: ["AMOUNT"] }, { file_1_fields: ["DATE"], file_2_fields: ["DATE"] }],
  });

  it("need a compared field and a limit above 0 before the mapping step is complete", () => {
    const config = mapped();
    expect(mappingIsReady(config)).toBe(true);
    expect(mappingIsReady({ ...config, tolerances: [{ field: "DEBIT", amount: null, percent: null, days: null }] })).toBe(false);
    expect(mappingIsReady({ ...config, tolerances: [{ field: "DEBIT", amount: 0.05 }] })).toBe(true);
    expect(mappingIsReady({ ...config, tolerances: [{ field: "*", days: 3 }] })).toBe(true);
    expect(mappingIsReady({ ...config, tolerances: [{ field: "CREDIT", amount: 1 }] })).toBe(false); // Not compared.
  });

  it("are sent with the sheet rule, empty limits as null", () => {
    const config = { ...mapped(), tolerances: [{ field: "DEBIT", amount: 0.05, percent: 0, days: null }] };
    const rule = draftToSheetRule(config, { sheetRuleId: "r", sourceSheet: "a", destinationSheet: "b", label: "L" });
    expect(rule.tolerances).toEqual([{ field: "DEBIT", amount: 0.05, percent: null, days: null, similarity: null }]);
  });

  it("are copied only for fields the target rule still compares", () => {
    const source = { ...mapped(), tolerances: [{ field: "DEBIT", amount: 1 }, { field: "*", days: 2 }] };
    const target = draft({ file1Columns: ["INVOICE", "DATE"], file2Columns: ["INVOICE NO", "DATE"] });
    expect(copyRuleSettings(source, target).tolerances).toEqual([{ field: "*", days: 2 }]);
  });

  it("can be taken from a step-3 pass, comparing the pass's date columns if needed", () => {
    const config = mapped();
    config.rules = [{ file_1_fields: ["DEBIT"], file_2_fields: ["AMOUNT"] }, { file_1_fields: ["VENDOR"], file_2_fields: ["VENDOR"] }];
    config.matchingPasses = [{
      ...newPass("amount_tolerance", config), amount_source: "DEBIT", amount_destination: "AMOUNT", amount_tolerance: 5, amount_tolerance_percent: 10,
      date_source: "DATE", date_destination: "DATE", date_window_days: 3, narrative_source: "VENDOR", narrative_destination: "VENDOR", narrative_threshold: 75,
    }];
    const { tolerances, rules } = limitsFromPasses(config);
    expect(tolerances).toEqual([
      { field: "DEBIT", amount: 5, percent: 10 },
      { field: "DATE", days: 3 },
      { field: "VENDOR", similarity: 75 },
    ]);
    expect(rules).toContainEqual({ file_1_fields: ["DATE"], file_2_fields: ["DATE"] });
    expect(mappingIsReady({ ...config, rules, tolerances })).toBe(true);
    // Once applied, nothing is suggested again.
    expect(limitsFromPasses({ ...config, rules, tolerances }).tolerances).toEqual([]);
  });

  it("are set on a mapped field one limit at a time, amount and % together", () => {
    const amount = setToleranceLimit([], "DEBIT", "amount", "amount", toleranceLimit("amount", "0.05"));
    expect(amount).toEqual([{ field: "DEBIT", amount: 0.05 }]);
    const both = setToleranceLimit([...amount, { field: "DATE", days: 3 }], "DEBIT", "amount", "percent", 1);
    expect(both).toEqual([{ field: "DEBIT", amount: 0.05, percent: 1 }, { field: "DATE", days: 3 }]);
    expect(toleranceKind(toleranceFor(both, "DEBIT"))).toBe("amount");
    expect(mappingIsReady({ ...mapped(), tolerances: both })).toBe(true);
    // Emptying the last box leaves the field with no tolerance.
    const onlyPercent = setToleranceLimit(both, "DEBIT", "amount", "amount", null);
    expect(onlyPercent[0]).toEqual({ field: "DEBIT", percent: 1 });
    expect(setToleranceLimit(onlyPercent, "DEBIT", "amount", "percent", null)).toEqual([{ field: "DATE", days: 3 }]);
    // Limits of another kind do not survive a change of kind.
    expect(setToleranceLimit(both, "DEBIT", "text", "similarity", 80)[0]).toEqual({ field: "DEBIT", similarity: 80 });
    expect(clearTolerance(both, "DEBIT")).toEqual([{ field: "DATE", days: 3 }]);
    expect(toleranceKind({ field: "X", amount: null, percent: null, days: 2, similarity: null })).toBe("date");
  });

  it("guess the kind of data a mapping compares", () => {
    const analysis = { recommended_keys_1: [], recommended_keys_2: [], key_confidence: 0, is_composite_key: false, recommended_mappings: [], date_columns_1: ["POSTED"], date_columns_2: [] };
    expect(guessToleranceKind({ file_1_fields: ["POSTED"], file_2_fields: ["BOOKED ON"] }, analysis)).toBe("date");
    expect(guessToleranceKind({ file_1_fields: ["VALUE DATE"], file_2_fields: ["DATE"] }, null)).toBe("date");
    expect(guessToleranceKind({ file_1_fields: ["DEBIT"], file_2_fields: ["AMOUNT"] }, null)).toBe("amount");
    expect(guessToleranceKind({ file_1_fields: ["BASIC", "HRA"], file_2_fields: ["PAY"] }, null)).toBe("amount");
    expect(guessToleranceKind({ file_1_fields: ["NARRATION"], file_2_fields: ["DESCRIPTION"] }, null)).toBe("text");
  });

  it("read typed limits: whole days and similarity, nothing for an empty box", () => {
    expect(toleranceLimit("amount", "")).toBeNull();
    expect(toleranceLimit("amount", "0.05")).toBe(0.05);
    expect(toleranceLimit("days", "2.6")).toBe(3);
    expect(toleranceLimit("similarity", "140")).toBe(100);
    expect(toleranceLimit("percent", "-4")).toBe(0);
  });

  it("go when their mapping is removed", () => {
    const tolerances = [{ field: "DEBIT", amount: 1 }, { field: "DATE", days: 2 }, { field: "*", days: 1 }];
    expect(pruneTolerances(tolerances, [{ file_1_fields: ["DATE"], file_2_fields: ["DATE"] }])).toEqual([{ field: "DATE", days: 2 }, { field: "*", days: 1 }]);
  });

  it("are described in words", () => {
    expect(describeTolerance({ field: "NARRATION", similarity: 75 })).toBe("NARRATION: text at least 75% similar");
    expect(describeTolerance({ field: "DEBIT", amount: 0.05, percent: 1 })).toBe("DEBIT: ±0.05 or ±1%");
    expect(describeTolerance({ field: "*", days: 1 })).toBe("Every compared field: ±1 day");
  });
});

describe("one workbook per sheet", () => {
  const sheets = (...names: string[]) => names.map((name) => ({ id: name, name }));

  it("pairs a single-sheet workbook with the sheet named like it", () => {
    const many = sheets("North", "South", "East");
    expect(suggestSheetPair(many, sheets("Sheet1"), "Regions.xlsx", "South.xlsx")).toEqual({ sheet1: many[1], sheet2: { id: "Sheet1", name: "Sheet1" } });
    expect(suggestSheetPair(many, sheets("east"), "Regions.xlsx", "export.csv")?.sheet1.name).toBe("East");
    expect(suggestSheetPair(sheets("Data"), many, "Sales North 2026.xlsx", "Regions.xlsx")?.sheet2.name).toBe("North");
  });

  it("suggests nothing when no sheet, or more than one, fits", () => {
    expect(suggestSheetPair(sheets("North", "South"), sheets("Sheet1"), "Regions.xlsx", "West.xlsx")).toBeNull();
    expect(suggestSheetPair(sheets("North", "North 2"), sheets("Sheet1"), "Regions.xlsx", "North 2 final.xlsx")).toBeNull();
    expect(suggestSheetPair(sheets("A", "B"), sheets("C", "D"), "x.xlsx", "y.xlsx")).toBeNull();
  });
});

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

  it("reports matching setup states using the same readiness requirements", () => {
    expect(matchingSetupStatus(undefined)).toBe("not-configured");
    expect(matchingSetupStatus(draft())).toBe("not-configured");
    expect(matchingSetupStatus(draft({ primaryKeySource: ["INVOICE"], primaryKeyDestination: [""] }))).toBe("error");
    expect(matchingSetupStatus(draft({ secondaryConditions: [{ source_column: "VENDOR", destination_column: "", comparison_method: "exact_text" }] }))).toBe("error");
    expect(matchingSetupStatus(draft({ secondaryConditions: [{ source_column: "VENDOR", destination_column: "VENDOR", comparison_method: "exact_text" }] }))).toBe("partial");
    expect(matchingSetupStatus(draft({ primaryKeySource: ["INVOICE"], primaryKeyDestination: ["INVOICE NO"] }))).toBe("configured");
  });

  it("reports map-stage completion and malformed mappings separately", () => {
    expect(mappingSetupStatus(undefined)).toBe("not-configured");
    expect(mappingSetupStatus(draft({ primaryKeySource: ["INVOICE"], primaryKeyDestination: ["INVOICE NO"] }))).toBe("not-configured");
    const ready = draft({ primaryKeySource: ["INVOICE"], primaryKeyDestination: ["INVOICE NO"], rules: [{ file_1_fields: ["AMOUNT"], file_2_fields: ["AMOUNT"] }] });
    expect(mappingSetupStatus(ready)).toBe("configured");
    expect(mappingSetupStatus({ ...ready, rules: [{ file_1_fields: ["AMOUNT"], file_2_fields: [] }] })).toBe("error");
    expect(mappingSetupStatus({ ...ready, tolerances: [{ field: "AMOUNT", amount: 0 }] })).toBe("error");
  });
});

describe("Date & Amount setup", () => {
  it("suggests date and debit/credit columns without changing the default method", () => {
    const config = emptyDraft(["POSTED DATE", "Debit", "Credit"], ["Date", "Dr", "Cr"], null);
    expect(config.method).toBe("standard");
    expect(config.dateAmount).toMatchObject({
      dateSource: "POSTED DATE", dateDestination: "Date",
      amountModeSource: "debit_credit", amountModeDestination: "debit_credit",
      debitSource: "Debit", creditSource: "Credit", debitDestination: "Dr", creditDestination: "Cr",
    });
  });

  it("recommends Date & Amount only when both files have detected dates and amount fields", () => {
    const analysis = {
      recommended_keys_1: [], recommended_keys_2: [], key_confidence: 0,
      is_composite_key: false, date_columns_1: ["POSTED DATE"], date_columns_2: ["DATE"],
      recommended_mappings: [],
    };
    const config = emptyDraft(["POSTED DATE", "AMOUNT"], ["DATE", "TOTAL"], analysis);
    expect(shouldSuggestDateAmount(config)).toBe(true);
    expect(shouldSuggestDateAmount({ ...config, analysis: { ...analysis, date_columns_2: [] } })).toBe(false);
    expect(shouldSuggestDateAmount({ ...config, dateAmount: { ...config.dateAmount, amountDestination: "" } })).toBe(false);
  });

  it("uses Date & Amount as the step-3 method and validates all required step-4 fields", () => {
    const baseDraft = draft({ method: "date_amount" });
    const base = { ...baseDraft, dateAmount: { ...baseDraft.dateAmount, dateSource: "", dateDestination: "", amountSource: "", amountDestination: "" } };
    expect(ruleIsReady(base)).toBe(true);
    expect(matchingSetupStatus(base)).toBe("configured");
    expect(mappingIsReady(base)).toBe(false);
    const ready = {
      ...base,
      dateAmount: {
        ...base.dateAmount,
        dateSource: "DATE", dateDestination: "DATE",
        amountSource: "AMOUNT", amountDestination: "AMOUNT",
        textSource: "VENDOR", textDestination: "VENDOR",
      },
    };
    expect(dateAmountMappingIsReady(ready)).toBe(true);
    expect(mappingSetupStatus(ready)).toBe("configured");
    expect(mappingIsReady({ ...ready, dateAmount: { ...ready.dateAmount, textDestination: "" } })).toBe(false);
    expect(mappingIsReady({ ...ready, dateAmount: { ...ready.dateAmount, amountTolerance: "0.0000001" } })).toBe(false);
  });

  it("serializes to the canonical date_amount contract and restores the editor state", () => {
    const base = draft({ method: "date_amount" });
    const config = {
      ...base,
      dateAmount: {
        ...base.dateAmount,
        dateSource: "DATE", dateDestination: "DATE",
        amountModeSource: "debit_credit" as const, amountModeDestination: "single" as const,
        debitSource: "DEBIT", creditSource: "CREDIT", amountDestination: "AMOUNT",
        amountTolerance: "0.05", dateToleranceDays: 2, verifyTransactions: false,
      },
    };
    const payload = draftToSheetRule(config, { sheetRuleId: "rule", sourceSheet: "A", destinationSheet: "B", label: "Ledger" });
    expect(payload.matching_strategy.method).toBe("date_amount");
    expect(payload.matching_strategy.primary_key_source).toEqual([]);
    expect(payload.reconciliation_mapping).toEqual([]);
    expect(payload.date_amount).toMatchObject({
      date_source: "DATE", amount_mode_source: "debit_credit", debit_source: "DEBIT",
      credit_source: "CREDIT", amount_destination: "AMOUNT", amount_tolerance: "0.05",
      date_tolerance_days: 2, verify_transactions: false,
    });
    expect(sheetRuleToDraft(payload, base.file1Columns, base.file2Columns, null)).toMatchObject({
      method: "date_amount", dateAmount: { dateSource: "DATE", amountModeSource: "debit_credit", verifyTransactions: false },
    });
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

describe("changing a finished run", () => {
  it("puts a saved sheet rule back in the editor unchanged", () => {
    const config = draft({
      primaryKeySource: ["INVOICE"], primaryKeyDestination: ["INVOICE NO"], dateFormat: "month_first",
      rules: [{ file_1_fields: ["DEBIT"], file_2_fields: ["AMOUNT"] }],
      tolerances: [{ field: "DEBIT", amount: 5, percent: null, days: null, similarity: null }],
      secondaryConditions: [{ source_column: "VENDOR", destination_column: "VENDOR", comparison_method: "exact_text" }],
      transformations: [{ operation: "trim", side: "both", columns: ["VENDOR"], params: {} }],
    });
    config.matchingPasses = [newPass("amount_date", config)];
    const ids = { sheetRuleId: "rule-1-1", sourceSheet: "Books", destinationSheet: "Bank", label: "Books -> Bank" };
    const saved = draftToSheetRule(config, ids);
    const reopened = sheetRuleToDraft(saved, config.file1Columns, config.file2Columns, null);
    expect(reopened).toMatchObject({ primaryKeySource: ["INVOICE"], dateFormat: "month_first", rules: config.rules, transformations: config.transformations });
    expect(reopened.matchingPasses[0].narrative_source).toBe("");
    expect(draftToSheetRule(reopened, ids)).toEqual(saved);
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

describe("ready-made setups", () => {
  const gst: ReadyMadeSetup = {
    id: "gst", label: "GST: purchase register vs GSTR-2B", status: "certain", missing_1: [], missing_2: [], notes: [],
    rule: {
      matching_strategy: {
        primary_key_source: ["SUPPLIER GSTIN", "INVOICE NUMBER"],
        primary_key_destination: ["GSTR", "INVOICE NO."],
        secondary_conditions: [{ source_column: "SUPPLIER GSTIN", destination_column: "GSTR", comparison_method: "exact_text" }],
        matching_passes: [{
          type: "amount_tolerance", name: "Same supplier and taxable value", amount_source: "TAXABLE VALUE", amount_destination: "TAXABLE",
          date_window_days: 0, amount_tolerance: 1, amount_tolerance_percent: 0, narrative_threshold: 90, respect_secondary_keys: true,
        }],
      },
      reconciliation_mapping: [{ file_1_fields: ["IGST AMOUNT"], file_2_fields: ["IGST"], comparison_type: "numeric" }],
      tolerances: [{ field: "IGST AMOUNT", amount: 1 }],
      date_format: "day_first",
    },
  };

  it("fills keys, conditions, fields and tolerances, and keeps the rest", () => {
    const before = { ...emptyDraft(["SUPPLIER GSTIN", "INVOICE NUMBER", "IGST AMOUNT"], ["GSTR", "INVOICE NO.", "IGST"], null),
      transformations: [{ operation: "trim" as const, side: "both" as const, columns: ["INVOICE NUMBER"], params: {} }], dateFormat: "month_first" as const };
    expect(readyMadeSetupApplied(before, gst)).toBe(false);
    const after = applyReadyMadeSetup(before, gst);
    expect(after.primaryKeySource).toEqual(["SUPPLIER GSTIN", "INVOICE NUMBER"]);
    expect(after.secondaryConditions).toHaveLength(1);
    expect(after.matchingPasses.map((item) => item.name)).toEqual(["Same supplier and taxable value"]);
    expect(after.rules[0].comparison_type).toBe("numeric");
    expect(after.tolerances).toEqual([{ field: "IGST AMOUNT", amount: 1 }]);
    expect(after.dateFormat).toBe("day_first");
    expect(after.transformations).toEqual(before.transformations);
    expect(readyMadeSetupApplied(after, gst)).toBe(true);
    after.rules[0].file_1_fields.push("CHANGED");  // An edit never changes the setup itself.
    expect(gst.rule!.reconciliation_mapping[0].file_1_fields).toEqual(["IGST AMOUNT"]);
    after.matchingPasses[0].amount_tolerance = 5;
    expect(gst.rule!.matching_strategy.matching_passes![0].amount_tolerance).toBe(1);
  });

  it("is shown only when it applies or a file has a GSTIN", () => {
    expect(readyMadeSetupRelevant(gst)).toBe(true);
    const unrelated = { ...gst, status: "unavailable" as const, rule: null,
      missing_1: ["gstin", "invoice_number", "taxable_value"], missing_2: ["gstin", "taxable_value"] };
    expect(readyMadeSetupRelevant(unrelated)).toBe(false);
    expect(readyMadeSetupRelevant({ ...unrelated, missing_2: ["taxable_value"] })).toBe(true);
  });

  it("leaves the rule alone when the setup cannot be filled in", () => {
    const draft = emptyDraft(["A"], ["B"], null);
    expect(applyReadyMadeSetup(draft, { ...gst, status: "unavailable", rule: null })).toBe(draft);
  });
});
