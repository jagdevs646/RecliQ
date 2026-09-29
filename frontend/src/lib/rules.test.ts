import { describe, expect, it } from "vitest";
import { UPLOAD_ACCEPT } from "./formats";
import { cleanDraft, defaultConditions } from "./rules";
import type { RuleDraft } from "../types";

describe("auto-resolution rule drafts", () => {
  it("start with conditions that meet each exception type's safety limits", () => {
    expect(defaultConditions("field_difference").map((condition) => condition.operator)).toContain("difference_abs_lte");
    expect(defaultConditions("to_confirm")[0]).toEqual({ operator: "previously_confirmed", value: 2 });
    expect(defaultConditions("only_in_source")[0].operator).toBe("contains");
  });

  it("send numbers for numeric conditions and keep text and blanks as typed", () => {
    const draft: RuleDraft = {
      name: "Bank charges",
      category: "only_in_destination",
      conditions: [
        { column: "Narrative", operator: "contains", value: "100" },
        { column: "Amount", operator: "between", value: "0", value_2: "12.5" },
        { column: "Amount", operator: "abs_lte", value: "" },
      ],
      action: { resolution: "Bank charges" },
    };
    const clean = cleanDraft(draft).conditions;
    expect(clean[0].value).toBe("100");
    expect(clean[1]).toMatchObject({ value: 0, value_2: 12.5 });
    expect(clean[2].value).toBe("");
  });
});

describe("upload formats", () => {
  it("offer bank statements and GST returns alongside spreadsheets", () => {
    const accepted = UPLOAD_ACCEPT.split(",");
    for (const extension of [".xlsx", ".csv", ".sta", ".mt940", ".xml", ".bai2", ".json"]) expect(accepted).toContain(extension);
    expect(accepted).not.toContain(".doc");
  });
});
