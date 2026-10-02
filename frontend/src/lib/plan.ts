/**
 * Pure helpers for building and validating a reconciliation plan in the
 * browser. Kept free of React so they can be unit-tested.
 */
import type {
  AnalysisResponse,
  DateFormat,
  MatchingPass,
  NormalizationSettings,
  PrecheckIssue,
  PrecheckResult,
  SecondaryMatchCondition,
  SheetRuleDraft,
  RuleMapping,
  SheetRulePayload,
  ToleranceBand,
  TransformationStep,
} from "../types";

export const DEFAULT_NORMALIZATION: NormalizationSettings = {
  legal_forms: true,
  abbreviations: true,
  ignore_prefixes: true,
  join_initials: true,
  word_order: true,
  use_saved_aliases: true,
  synonyms: [],
  aliases: [],
};

export function emptyDraft(file1Columns: string[], file2Columns: string[], analysis: AnalysisResponse | null): SheetRuleDraft {
  return {
    file1Columns,
    file2Columns,
    primaryKeySource: [],
    primaryKeyDestination: [],
    secondaryConditions: [],
    similarityPolicy: {},
    dateOnlyOverride: false,
    rules: [],
    includeFile1: [],
    includeFile2: [],
    analysis,
    transformations: [],
    matchingPasses: [],
    normalization: { ...DEFAULT_NORMALIZATION },
    dateFormat: "day_first",
    tolerances: [],
  };
}

const DATE_COLUMN_HINT = /date|\bdt\b/i;

/** Column names are compared the way the server normalizes headers. */
export function normalizeHeader(value: string): string {
  return value.replace(/[\r\n]/g, "").trim().toUpperCase();
}

/** Columns created by transformations (e.g. a signed amount), per side. */
export function derivedColumns(config: Pick<SheetRuleDraft, "transformations">, side: "source" | "destination"): string[] {
  return config.transformations
    .filter((step) => step.operation === "debit_credit_to_signed" && step.side === side)
    .map((step) => normalizeHeader(step.output_column || "Signed Amount"));
}

/** File columns plus derived columns, for mapping and pass selection. */
export function availableColumns(config: SheetRuleDraft, side: "source" | "destination"): string[] {
  const base = side === "source" ? config.file1Columns : config.file2Columns;
  return [...base, ...derivedColumns(config, side).filter((column) => !base.includes(column))];
}

/** A one-column key on a date column (values decide first, names are a fallback). */
export function isDateOnlyKey(config: SheetRuleDraft): boolean {
  if (config.primaryKeySource.length !== 1 || config.primaryKeyDestination.length !== 1) return false;
  const [source] = config.primaryKeySource;
  const [destination] = config.primaryKeyDestination;
  const dateValued = Boolean(config.analysis?.date_columns_1?.includes(source) || config.analysis?.date_columns_2?.includes(destination));
  return dateValued || DATE_COLUMN_HINT.test(source) || DATE_COLUMN_HINT.test(destination);
}

/**
 * When one workbook has a single sheet and the other several (one workbook
 * per sheet), the sheet it belongs to: the only one whose name matches the
 * single sheet's name or its workbook's file name. Null when unclear.
 */
export function suggestSheetPair<Sheet extends { id: string; name: string }>(
  sourceSheets: Sheet[], destinationSheets: Sheet[], sourceFilename: string, destinationFilename: string,
): { sheet1: Sheet; sheet2: Sheet } | null {
  const plain = (text: string) => text.replace(/\.[a-z0-9]+$/i, "").toLowerCase().replace(/[^a-z0-9]/g, "");
  const alike = (left: string, right: string) => left.length >= 3 && right.length >= 3 && (left.includes(right) || right.includes(left));
  const pick = (many: Sheet[], single: Sheet, filename: string) => {
    const names = [plain(single.name), plain(filename)];
    const exact = many.filter((sheet) => names.includes(plain(sheet.name)));
    const close = exact.length ? exact : many.filter((sheet) => names.some((name) => alike(plain(sheet.name), name)));
    return close.length === 1 ? close[0] : null;
  };
  if (destinationSheets.length === 1 && sourceSheets.length > 1) {
    const sheet1 = pick(sourceSheets, destinationSheets[0], destinationFilename);
    return sheet1 ? { sheet1, sheet2: destinationSheets[0] } : null;
  }
  if (sourceSheets.length === 1 && destinationSheets.length > 1) {
    const sheet2 = pick(destinationSheets, sourceSheets[0], sourceFilename);
    return sheet2 ? { sheet1: sourceSheets[0], sheet2 } : null;
  }
  return null;
}

export function passIsComplete(item: MatchingPass): boolean {
  if (!item.amount_source || !item.amount_destination) return false;
  if (item.type === "amount_date" && (!item.date_source || !item.date_destination)) return false;
  if (Boolean(item.date_source) !== Boolean(item.date_destination)) return false;
  if (Boolean(item.narrative_source) !== Boolean(item.narrative_destination)) return false;
  return true;
}

const PARAMETER_REQUIRED: Partial<Record<TransformationStep["operation"], string>> = {
  replace_text: "find",
  remove_prefix: "text",
  remove_suffix: "text",
  remove_characters: "characters",
  multiply: "factor",
};

export function transformationIsComplete(step: TransformationStep): boolean {
  if (step.operation === "debit_credit_to_signed") {
    return step.side !== "both" && Boolean(step.params.debit_column) && Boolean(step.params.credit_column);
  }
  if (!step.columns.length || step.columns.some((column) => !column)) return false;
  const required = PARAMETER_REQUIRED[step.operation];
  return !required || (step.params[required] !== undefined && step.params[required] !== "");
}

export function hasKeys(config: SheetRuleDraft): boolean {
  return config.primaryKeySource.length > 0 && [...config.primaryKeySource, ...config.primaryKeyDestination].some(Boolean);
}

/** Matching setup is complete (step 3). Keys are optional when a keyless pass exists. */
export function ruleIsReady(config: SheetRuleDraft | undefined): boolean {
  if (!config) return false;
  const passesReady = config.matchingPasses.every(passIsComplete);
  const keyed = hasKeys(config);
  const keysComplete = config.primaryKeySource.length === config.primaryKeyDestination.length
    && [...config.primaryKeySource, ...config.primaryKeyDestination].every(Boolean);
  return Boolean(
    (keyed ? keysComplete : config.matchingPasses.length > 0)
    && passesReady
    && config.transformations.every(transformationIsComplete)
    && config.secondaryConditions.every((condition) => condition.source_column && condition.destination_column)
    && (!keyed || !isDateOnlyKey(config) || config.dateOnlyOverride),
  );
}

export function ruleStatus(config: SheetRuleDraft): string {
  const keyed = hasKeys(config);
  if (!keyed && !config.matchingPasses.length) return "Choose the key columns";
  if (keyed && ![...config.primaryKeySource, ...config.primaryKeyDestination].every(Boolean)) return "Choose the key columns";
  if (config.secondaryConditions.some((condition) => !condition.source_column || !condition.destination_column)) return "Finish the \"must also match\" columns";
  if (!config.matchingPasses.every(passIsComplete)) return "Finish the extra matching passes";
  if (!config.transformations.every(transformationIsComplete)) return "Finish the value preparation steps";
  if (keyed && isDateOnlyKey(config) && !config.dateOnlyOverride) return "Date-only key needs attention";
  const parts = [
    keyed ? config.primaryKeySource.join(" + ") : "No key (amount/date matching)",
    `${config.secondaryConditions.length} must-also-match column${config.secondaryConditions.length === 1 ? "" : "s"}`,
  ];
  if (config.matchingPasses.length) parts.push(`${config.matchingPasses.length} extra pass${config.matchingPasses.length === 1 ? "" : "es"}`);
  if (config.transformations.length) parts.push(`${config.transformations.length} preparation step${config.transformations.length === 1 ? "" : "s"}`);
  parts.push(`${config.rules.length} mapping${config.rules.length === 1 ? "" : "s"}`);
  return parts.join(" · ");
}

/** Deep-copies another rule's settings, keeping only columns that exist in the target sheets. */
export function copyRuleSettings(source: SheetRuleDraft, target: SheetRuleDraft): SheetRuleDraft {
  const copy = structuredClone(source);
  const transformations = copy.transformations.filter((step) => {
    const columns = step.side === "destination" ? target.file2Columns : step.side === "source" ? target.file1Columns : [...target.file1Columns].filter((column) => target.file2Columns.includes(column));
    const needed = step.operation === "debit_credit_to_signed" ? [String(step.params.debit_column), String(step.params.credit_column)] : step.columns;
    return needed.every((column) => columns.includes(column));
  });
  const withSteps = { ...target, transformations };
  const has1 = new Set(availableColumns(withSteps, "source"));
  const has2 = new Set(availableColumns(withSteps, "destination"));
  const keyPairs = copy.primaryKeySource.map((column, index) => [column, copy.primaryKeyDestination[index]] as const)
    .filter(([left, right]) => has1.has(left) && right !== undefined && has2.has(right));
  const passColumnsExist = (item: MatchingPass) => [item.amount_source, item.date_source, item.narrative_source].every((column) => !column || has1.has(column))
    && [item.amount_destination, item.date_destination, item.narrative_destination].every((column) => !column || has2.has(column));
  const rules = copy.rules.filter((rule) => rule.file_1_fields.every((field) => has1.has(field)) && rule.file_2_fields.every((field) => has2.has(field)));
  const fields = new Set(rules.map(comparedFieldLabel));
  return {
    ...withSteps,
    primaryKeySource: keyPairs.map(([left]) => left),
    primaryKeyDestination: keyPairs.map(([, right]) => right),
    secondaryConditions: copy.secondaryConditions.filter((condition: SecondaryMatchCondition) => has1.has(condition.source_column) && has2.has(condition.destination_column)),
    similarityPolicy: copy.similarityPolicy,
    dateOnlyOverride: false,
    rules,
    includeFile1: copy.includeFile1.filter((column) => has1.has(column)),
    includeFile2: copy.includeFile2.filter((column) => has2.has(column)),
    matchingPasses: copy.matchingPasses.filter(passColumnsExist),
    normalization: copy.normalization,
    dateFormat: copy.dateFormat,
    tolerances: copy.tolerances.filter((band) => band.field === ALL_FIELDS || fields.has(band.field)),
  };
}

// ── Tolerance bands ─────────────────────────────────────────────────────
export const ALL_FIELDS = "*";

/** How a compared field is named: its source column(s), as the server labels it. */
export function comparedFieldLabel(rule: RuleMapping): string {
  return rule.file_1_fields.join(",");
}

export function comparedFields(config: Pick<SheetRuleDraft, "rules">): string[] {
  return [...new Set(config.rules.filter((rule) => rule.file_1_fields.length && rule.file_2_fields.length).map(comparedFieldLabel))];
}

/** A band names a compared field (or all of them) and has at least one limit above 0. */
export function toleranceIsComplete(band: ToleranceBand, config: Pick<SheetRuleDraft, "rules">): boolean {
  const fieldExists = band.field === ALL_FIELDS || comparedFields(config).includes(band.field);
  return fieldExists && [band.amount, band.percent, band.days, band.similarity].some((limit) => (limit ?? 0) > 0);
}

export type ToleranceKind = "amount" | "date" | "text";
export type ToleranceLimit = "amount" | "percent" | "days" | "similarity";

/** The limit boxes each kind of data offers, in the order they are shown. */
export const TOLERANCE_KINDS: Array<{ value: ToleranceKind; label: string; limits: ToleranceLimit[] }> = [
  { value: "amount", label: "Amount", limits: ["amount", "percent"] },
  { value: "date", label: "Date", limits: ["days"] },
  { value: "text", label: "Text", limits: ["similarity"] },
];

export const TOLERANCE_LIMITS: Record<ToleranceLimit, { label: string; placeholder: string; step: string; max?: number; whole?: boolean }> = {
  amount: { label: "± Amount", placeholder: "± amount", step: "0.01" },
  percent: { label: "± %", placeholder: "± %", step: "0.1", max: 100 },
  days: { label: "± Days", placeholder: "± days", step: "1", max: 366, whole: true },
  similarity: { label: "Text similar ≥ %", placeholder: "similar ≥ %", step: "1", max: 100, whole: true },
};

const AMOUNT_COLUMN_HINT = /amount|amt|value|debit|credit|total|balance|price|rate|qty|quantity|tax|gst|salary|gross|\bnet\b|paid|payment|fee|cost|charge|\bdr\b|\bcr\b/i;

/** The tolerance set on one mapped field. */
export function toleranceFor(tolerances: ToleranceBand[], field: string): ToleranceBand | undefined {
  return tolerances.find((band) => band.field === field);
}

/** The kind of data a band's limits are for, from the limits it holds. */
export function toleranceKind(band: ToleranceBand | undefined): ToleranceKind | null {
  if (!band) return null;
  const holds = (test: (value: unknown) => boolean) => TOLERANCE_KINDS.find((kind) => kind.limits.some((limit) => test(band[limit])))?.value;
  return holds((value) => Number(value ?? 0) > 0) ?? holds((value) => typeof value === "number") ?? null;
}

/**
 * The kind of data a mapping compares, so its tolerance boxes fit without
 * asking: several columns added together are an amount, date columns are
 * known by their values or name, amounts by name, anything else is text.
 */
export function guessToleranceKind(rule: RuleMapping, analysis: AnalysisResponse | null | undefined): ToleranceKind {
  if (rule.file_1_fields.length > 1 || rule.file_2_fields.length > 1) return "amount";
  const dateValued = rule.file_1_fields.some((column) => analysis?.date_columns_1?.includes(column))
    || rule.file_2_fields.some((column) => analysis?.date_columns_2?.includes(column));
  if (dateValued) return "date";
  const columns = [...rule.file_1_fields, ...rule.file_2_fields];
  // Dates first: "Value date" is a date, not a value.
  if (columns.some((column) => DATE_COLUMN_HINT.test(column))) return "date";
  return columns.some((column) => AMOUNT_COLUMN_HINT.test(column)) ? "amount" : "text";
}

/** What was typed as a limit: empty is no limit, days and similarity are whole numbers. */
export function toleranceLimit(limit: ToleranceLimit, text: string): number | null {
  if (text.trim() === "" || Number.isNaN(Number(text))) return null;
  const { max, whole } = TOLERANCE_LIMITS[limit];
  const value = Math.max(0, Number(text));
  const capped = max === undefined ? value : Math.min(max, value);
  return whole ? Math.round(capped) : capped;
}

/**
 * Sets one limit of a mapped field's tolerance, keeping the other limits of
 * the same kind (amount and % go together). With no limit left the field has
 * no tolerance.
 */
export function setToleranceLimit(tolerances: ToleranceBand[], field: string, kind: ToleranceKind, limit: ToleranceLimit, value: number | null): ToleranceBand[] {
  const at = tolerances.findIndex((item) => item.field === field);
  const band: ToleranceBand = { field };
  for (const key of TOLERANCE_KINDS.find((item) => item.value === kind)?.limits ?? []) {
    const next = key === limit ? value : tolerances[at]?.[key];
    if (typeof next === "number") band[key] = next;
  }
  const others = tolerances.filter((item) => item.field !== field);
  if (Object.keys(band).length === 1) return others;
  return at < 0 ? [...others, band] : [...others.slice(0, at), band, ...others.slice(at)];
}

/** Removes one mapped field's tolerance. */
export function clearTolerance(tolerances: ToleranceBand[], field: string): ToleranceBand[] {
  return tolerances.filter((item) => item.field !== field);
}

/** Drops tolerances whose field is no longer mapped, so removing a mapping removes its tolerance. */
export function pruneTolerances(tolerances: ToleranceBand[], rules: RuleMapping[]): ToleranceBand[] {
  const fields = new Set(comparedFields({ rules }));
  return tolerances.filter((band) => band.field === ALL_FIELDS || fields.has(band.field));
}

/** Step 4 is complete: fields are mapped and every tolerance band is usable. */
export function mappingIsReady(config: SheetRuleDraft | undefined): boolean {
  if (!config?.rules.length) return false;
  return config.tolerances.every((band) => toleranceIsComplete(band, config));
}

/**
 * The limits typed into step-3 matching passes, as tolerance bands. Pass
 * limits only pair records the key did not find; people often expect them to
 * accept differences on matched records too. Date limits need the dates to
 * be compared, so their columns are added to the compared fields.
 */
export function limitsFromPasses(config: SheetRuleDraft): { tolerances: ToleranceBand[]; rules: RuleMapping[] } {
  const rules = [...config.rules];
  const tolerances: ToleranceBand[] = [];
  const compare = (source: string, destination: string) => {
    if (!rules.some((rule) => comparedFieldLabel(rule) === source)) rules.push({ file_1_fields: [source], file_2_fields: [destination] });
  };
  const add = (band: ToleranceBand) => {
    const current = tolerances.find((item) => item.field === band.field);
    if (current) Object.assign(current, Object.fromEntries(Object.entries(band).filter(([, value]) => value)));
    else tolerances.push(band);
  };
  for (const item of config.matchingPasses) {
    if (item.type === "amount_tolerance" && (item.amount_tolerance > 0 || item.amount_tolerance_percent > 0) && item.amount_source && item.amount_destination) {
      compare(item.amount_source, item.amount_destination);
      add({ field: item.amount_source, amount: item.amount_tolerance || null, percent: item.amount_tolerance_percent || null });
    }
    if (item.date_source && item.date_destination && item.date_window_days > 0) {
      compare(item.date_source, item.date_destination);
      add({ field: item.date_source, days: item.date_window_days });
    }
    if (item.narrative_source && item.narrative_destination && item.narrative_threshold > 0) {
      compare(item.narrative_source, item.narrative_destination);
      add({ field: item.narrative_source, similarity: item.narrative_threshold });
    }
  }
  const limits = ["amount", "percent", "days", "similarity"] as const;
  const covered = (band: ToleranceBand) => config.tolerances.some((existing) => existing.field === band.field
    && limits.every((key) => !band[key] || existing[key] === band[key]));
  return { tolerances: tolerances.filter((band) => !covered(band)), rules };
}

export function describeTolerance(band: ToleranceBand): string {
  const limits = [
    band.amount ? `±${band.amount}` : "",
    band.percent ? `±${band.percent}%` : "",
    band.days ? `±${band.days} day${band.days === 1 ? "" : "s"}` : "",
    band.similarity ? `text at least ${band.similarity}% similar` : "",
  ].filter(Boolean);
  return `${band.field === ALL_FIELDS ? "Every compared field" : band.field}: ${limits.join(" or ") || "no limit set"}`;
}

/** One sheet rule of the canonical plan sent to the API. */
export function draftToSheetRule(config: SheetRuleDraft, ids: { sheetRuleId: string; sourceSheet: string; destinationSheet: string; label: string }): SheetRulePayload {
  const keyed = hasKeys(config);
  return {
    sheet_rule_id: ids.sheetRuleId,
    source_sheets: [ids.sourceSheet],
    destination_sheets: [ids.destinationSheet],
    matching_strategy: {
      primary_key_source: keyed ? config.primaryKeySource : [],
      primary_key_destination: keyed ? config.primaryKeyDestination : [],
      secondary_conditions: config.secondaryConditions,
      similarity_policy: config.similarityPolicy,
      date_only_override: keyed && isDateOnlyKey(config) && config.dateOnlyOverride,
      matching_passes: config.matchingPasses.map((item) => ({
        ...item,
        date_source: item.date_source || null,
        date_destination: item.date_destination || null,
        narrative_source: item.narrative_source || null,
        narrative_destination: item.narrative_destination || null,
      })),
      normalization: config.normalization,
    },
    reconciliation_mapping: config.rules,
    include_columns_file_1: config.includeFile1,
    include_columns_file_2: config.includeFile2,
    transformations: config.transformations,
    date_format: config.dateFormat,
    tolerances: config.tolerances.map((band) => ({
      field: band.field,
      amount: band.amount || null,
      percent: band.percent || null,
      days: band.days || null,
      similarity: band.similarity || null,
    })),
    report_label: ids.label,
  };
}

/** A saved sheet rule back in the editor, to change it and run again. The reverse of draftToSheetRule. */
export function sheetRuleToDraft(rule: SheetRulePayload, file1Columns: string[], file2Columns: string[], analysis: AnalysisResponse | null): SheetRuleDraft {
  const strategy = rule.matching_strategy;
  return {
    ...emptyDraft(file1Columns, file2Columns, analysis),
    primaryKeySource: strategy.primary_key_source ?? [],
    primaryKeyDestination: strategy.primary_key_destination ?? [],
    secondaryConditions: strategy.secondary_conditions ?? [],
    similarityPolicy: strategy.similarity_policy ?? {},
    dateOnlyOverride: Boolean(strategy.date_only_override),
    matchingPasses: (strategy.matching_passes ?? []).map((item) => ({
      ...item,
      date_source: item.date_source ?? "",
      date_destination: item.date_destination ?? "",
      narrative_source: item.narrative_source ?? "",
      narrative_destination: item.narrative_destination ?? "",
    })),
    normalization: { ...DEFAULT_NORMALIZATION, ...strategy.normalization },
    rules: rule.reconciliation_mapping ?? [],
    includeFile1: rule.include_columns_file_1 ?? [],
    includeFile2: rule.include_columns_file_2 ?? [],
    transformations: rule.transformations ?? [],
    dateFormat: rule.date_format ?? "day_first",
    tolerances: rule.tolerances ?? [],
  };
}

export function newPass(type: MatchingPass["type"], config: SheetRuleDraft): MatchingPass {
  const guess = (columns: string[], pattern: RegExp) => columns.find((column) => pattern.test(column)) ?? "";
  const source = availableColumns(config, "source");
  const destination = availableColumns(config, "destination");
  return {
    type,
    amount_source: guess(source, /amount|amt|value|debit|credit|total/i),
    amount_destination: guess(destination, /amount|amt|value|debit|credit|total/i),
    date_source: guess(source, /date/i),
    date_destination: guess(destination, /date/i),
    date_window_days: type === "amount_date" ? 3 : 0,
    amount_tolerance: type === "amount_tolerance" ? 1 : 0,
    amount_tolerance_percent: 0,
    narrative_source: "",
    narrative_destination: "",
    narrative_threshold: 85,
    respect_secondary_keys: true,
  };
}

export function describePass(item: MatchingPass, number: number): string {
  const base = item.type === "amount_date" ? "Same amount" : `Amount within ${[item.amount_tolerance ? `±${item.amount_tolerance}` : "", item.amount_tolerance_percent ? `±${item.amount_tolerance_percent}%` : ""].filter(Boolean).join(" / ") || "±0"}`;
  const date = item.date_source ? ` and date within ±${item.date_window_days} day${item.date_window_days === 1 ? "" : "s"}` : "";
  const reference = item.narrative_source ? `, reference ≥ ${item.narrative_threshold}% similar` : "";
  return `Pass ${number}: ${base}${date}${reference}`;
}

// ── Pre-check ───────────────────────────────────────────────────────────
export function precheckBlocked(result: PrecheckResult | null): boolean {
  return Boolean(result && result.summary.blocker > 0);
}

export function precheckNeedsAcknowledgement(result: PrecheckResult | null): boolean {
  return Boolean(result && result.summary.warning > 0);
}

export function issuesBySeverity(issues: PrecheckIssue[]): Record<PrecheckIssue["severity"], PrecheckIssue[]> {
  return {
    blocker: issues.filter((issue) => issue.severity === "blocker"),
    warning: issues.filter((issue) => issue.severity === "warning"),
    info: issues.filter((issue) => issue.severity === "info"),
  };
}

/** The compact summary stored with the job when the user confirms warnings. */
export function precheckSummary(result: PrecheckResult): Record<string, unknown> {
  return {
    status: result.status,
    ...result.summary,
    issues: result.rules.flatMap((rule) => rule.issues.filter((issue) => issue.severity !== "info").map((issue) => ({ rule: rule.sheet_rule_id, severity: issue.severity, category: issue.category, count: issue.count }))).slice(0, 100),
  };
}

export function dateFormatLabel(format: DateFormat): string {
  return format === "month_first" ? "Month first (MM/DD/YYYY)" : "Day first (DD/MM/YYYY)";
}
