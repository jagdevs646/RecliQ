import { describe, expect, it } from "vitest";
import {
  ABSOLUTE_AMOUNT_LEVELS,
  CUSTOM_LEVEL,
  TOLERANCE_AMOUNT_LEVELS,
  amountLevelId,
  dateLevelId,
  describeTextLevel,
  referenceLevelOf,
  textLevelId,
} from "./matchLevels";

describe("named match levels", () => {
  it("names a number that is a level, and calls any other number custom", () => {
    expect(textLevelId(80)).toBe("close");
    expect(textLevelId(85)).toBe(CUSTOM_LEVEL);
    expect(textLevelId(undefined)).toBe("");
    expect(dateLevelId(3)).toBe("three_days");
    expect(dateLevelId(5)).toBe(CUSTOM_LEVEL);
  });

  it("treats an amount level as the pair of limits it sets", () => {
    expect(amountLevelId(0.05, 0)).toBe("rounding");
    expect(amountLevelId(0, 1)).toBe("one_percent");
    expect(amountLevelId(1, 1)).toBe(CUSTOM_LEVEL); // Both limits: not one level.
    expect(amountLevelId(undefined, undefined)).toBe("");
    // Inputs that take no percentage offer absolute levels only.
    expect(ABSOLUTE_AMOUNT_LEVELS.map((level) => level.id)).toEqual(["exact", "rounding", "one"]);
    // A tolerance's empty choice is "Exact (no tolerance)", so Exact is not listed twice.
    expect(TOLERANCE_AMOUNT_LEVELS.some((level) => level.id === "exact")).toBe(false);
  });

  it("keeps saved plans without a reference level on formatting only", () => {
    expect(referenceLevelOf(undefined)).toBe("formatting");
    expect(referenceLevelOf("variants")).toBe("variants");
    expect(referenceLevelOf("fuzzy")).toBe("formatting");
  });

  it("describes a similarity for the review summary", () => {
    expect(describeTextLevel(undefined)).toBe("Automatic");
    expect(describeTextLevel(90)).toBe("Very close (≥90%)");
    expect(describeTextLevel(100)).toBe("Exact (100%)");
    expect(describeTextLevel(87)).toBe("Custom (≥87%)");
  });
});
