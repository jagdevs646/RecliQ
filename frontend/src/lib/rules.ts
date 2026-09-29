import type { RuleCategory, RuleCondition, RuleDraft } from "../types";

/** Conditions whose value is a number (the server validates it again). */
export const NUMBER_VALUE_OPERATORS = new Set([
  "abs_lte", "abs_gte", "lte", "gte", "between", "difference_abs_lte", "difference_pct_lte", "confidence_gte", "previously_confirmed"
]);
export const NO_VALUE_OPERATORS = new Set(["is_blank", "not_blank"]);
/** Operator groups that test a column of the record. */
export const COLUMN_GROUPS = new Set(["text", "number"]);

export const EMPTY_RULE: RuleDraft = {
  name: "",
  category: "only_in_destination",
  conditions: [{ column: "", operator: "contains", value: "" }],
  action: { resolution: "", reason_code: "", gl_account: "", note: "" }
};

/** Starting conditions that already satisfy each exception type's safety limits:
 * a difference rule limits the size of the difference, a confirmation rule
 * requires earlier reviewer confirmation. */
export function defaultConditions(category: RuleCategory): RuleCondition[] {
  if (category === "field_difference") return [{ operator: "field_is", value: "" }, { operator: "difference_abs_lte", value: 1 }];
  if (category === "to_confirm") return [{ operator: "previously_confirmed", value: 2 }];
  return [{ column: "", operator: "contains", value: "" }];
}

/** Form values are strings; send numbers for numeric conditions. Blank stays blank
 * so the server can explain what is missing. */
export function cleanDraft(draft: RuleDraft): RuleDraft {
  return {
    ...draft,
    conditions: draft.conditions.map((condition) => {
      const numeric = NUMBER_VALUE_OPERATORS.has(condition.operator);
      const toValue = (value: RuleCondition["value"]) => (numeric && value !== "" && value !== null && value !== undefined ? Number(value) : value);
      return { ...condition, value: toValue(condition.value), value_2: toValue(condition.value_2) };
    })
  };
}
