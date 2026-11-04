/**
 * Named match levels (docs/adr/0002): people pick a level instead of typing a
 * number. Requests still carry the numbers, so saved plans keep working, and
 * a number that is not a level shows as "Custom". The backend names levels in
 * reports from the same tables (backend/app/reconciliation_engine/match_levels.py);
 * tests/backend/test_reference_variants.py checks the two stay in step.
 */

export interface LevelOption<V> {
  id: string;
  label: string;
  value: V;
  hint?: string;
  warning?: string;
}

export const CUSTOM_LEVEL = "custom";

/** Text and narratives: minimum similarity, in percent. */
export const TEXT_LEVELS: LevelOption<number>[] = [
  { id: "exact", label: "Exact", value: 100, hint: "Identical text only" },
  { id: "very_close", label: "Very close", value: 90, hint: "At least 90% similar" },
  { id: "close", label: "Close", value: 80, hint: "At least 80% similar" },
  { id: "similar", label: "Similar", value: 70, hint: "At least 70% similar" },
  { id: "loose", label: "Loose", value: 60, hint: "At least 60% similar", warning: "Loose can accept texts that only look alike. Check the results." },
];

export interface AmountLimits {
  amount: number;
  percent: number;
}

/** Amounts: an absolute or a percentage difference. Exact means no difference. */
export const AMOUNT_LEVELS: LevelOption<AmountLimits>[] = [
  { id: "exact", label: "Exact", value: { amount: 0, percent: 0 }, hint: "No difference" },
  { id: "rounding", label: "Rounding", value: { amount: 0.05, percent: 0 }, hint: "±0.05" },
  { id: "one", label: "±1", value: { amount: 1, percent: 0 }, hint: "Up to 1 either way" },
  { id: "half_percent", label: "±0.5%", value: { amount: 0, percent: 0.5 }, hint: "Of the larger amount" },
  { id: "one_percent", label: "±1%", value: { amount: 0, percent: 1 }, hint: "Of the larger amount" },
];

/** Dates: a window of days either way. */
export const DATE_LEVELS: LevelOption<number>[] = [
  { id: "same_day", label: "Same day", value: 0 },
  { id: "one_day", label: "±1 day", value: 1 },
  { id: "three_days", label: "±3 days", value: 3 },
  { id: "seven_days", label: "±7 days", value: 7 },
];

export type ReferenceLevel = "exact" | "formatting" | "variants" | "suggestions";

/** How strictly reference numbers (invoice numbers, codes) must agree. */
export const REFERENCE_LEVELS: LevelOption<ReferenceLevel>[] = [
  { id: "exact", label: "Exact", value: "exact", hint: "Only letter case and extra spaces are ignored." },
  { id: "formatting", label: "Formatting only", value: "formatting", hint: "Spaces, case and punctuation are ignored: INV/005 = INV-005." },
  { id: "variants", label: "Variants", value: "variants", hint: "Also leading zeros (INV-0105 = INV-105, matched). A dropped prefix or year (INV/2026-27/105 = 105) and look-alike characters (1O5 = 105) go to Match Review." },
  { id: "suggestions", label: "Variants + suggestions", value: "suggestions", hint: "Also proposes similar references (INV-1054 for INV-1045) when the amount agrees. Proposals always go to Match Review." },
];

/** Saved plans without a level keep the behaviour from before levels existed. */
export const DEFAULT_REFERENCE_LEVEL: ReferenceLevel = "formatting";
/** New sheet rules start here, so the same invoice written differently pairs. */
export const NEW_RULE_REFERENCE_LEVEL: ReferenceLevel = "variants";

const same = (left: number, right: number) => Math.abs(left - right) < 1e-9;

/** The text level a similarity is, "" when none is set, or "custom". */
export function textLevelId(similarity: number | null | undefined, options: LevelOption<number>[] = TEXT_LEVELS): string {
  if (similarity === null || similarity === undefined) return "";
  return options.find((option) => same(option.value, similarity))?.id ?? CUSTOM_LEVEL;
}

/** The amount level two limits are, "" when neither is set, or "custom". */
export function amountLevelId(amount: number | null | undefined, percent: number | null | undefined, options: LevelOption<AmountLimits>[] = AMOUNT_LEVELS): string {
  if ((amount === null || amount === undefined) && (percent === null || percent === undefined)) return "";
  const limits = { amount: amount ?? 0, percent: percent ?? 0 };
  return options.find((option) => same(option.value.amount, limits.amount) && same(option.value.percent, limits.percent))?.id ?? CUSTOM_LEVEL;
}

/** The date level a window is, "" when none is set, or "custom". */
export function dateLevelId(days: number | null | undefined, options: LevelOption<number>[] = DATE_LEVELS): string {
  if (days === null || days === undefined) return "";
  return options.find((option) => same(option.value, days))?.id ?? CUSTOM_LEVEL;
}

export function referenceLevelOf(level: string | undefined): ReferenceLevel {
  return REFERENCE_LEVELS.some((option) => option.value === level) ? level as ReferenceLevel : DEFAULT_REFERENCE_LEVEL;
}

/** Levels by id, keeping the table's order (a place offers a subset). */
export function pickLevels<V>(options: LevelOption<V>[], ids: string[]): LevelOption<V>[] {
  return options.filter((option) => ids.includes(option.id));
}

/** Absolute-only amount levels, for inputs that take no percentage. */
export const ABSOLUTE_AMOUNT_LEVELS = pickLevels(AMOUNT_LEVELS, ["exact", "rounding", "one"]);
/** Tolerances: "no tolerance" is the empty choice, so the exact level is not offered twice. */
export const TOLERANCE_AMOUNT_LEVELS = pickLevels(AMOUNT_LEVELS, ["rounding", "one", "half_percent", "one_percent"]);
export const TOLERANCE_TEXT_LEVELS = pickLevels(TEXT_LEVELS, ["very_close", "close", "similar", "loose"]);
export const TOLERANCE_DATE_LEVELS = pickLevels(DATE_LEVELS, ["one_day", "three_days", "seven_days"]);

/** "Close (≥80%)" style text for summaries. */
export function describeTextLevel(similarity: number | null | undefined): string {
  const id = textLevelId(similarity);
  if (!id) return "Automatic";
  const level = TEXT_LEVELS.find((option) => option.id === id);
  return level ? `${level.label} (${level.value === 100 ? "100%" : `≥${level.value}%`})` : `Custom (≥${similarity}%)`;
}
