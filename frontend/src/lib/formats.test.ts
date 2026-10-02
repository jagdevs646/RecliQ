import { describe, expect, it } from "vitest";
import { decimalColumns, formatRecordValue, formatServerTime, parseServerTime } from "./formats";

describe("record values on screen", () => {
  const two = (value: number) => value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });

  it("show numbers to 2 decimal places", () => {
    expect(formatRecordValue(125.678945, "Difference")).toBe(two(125.68));
    expect(formatRecordValue("125.678945", "File 1 Value")).toBe("125.68");
    expect(formatRecordValue("12.3456789123%", "Similarity")).toBe("12.35%");
  });

  it("show the difference percentage, stored as a fraction, as a percentage", () => {
    expect(formatRecordValue(0.123456789123, "Difference %")).toBe(`${two(12.35)}%`);
  });

  it("leave whole-number columns, codes and text alone", () => {
    expect(formatRecordValue(100234, "MATCH KEY")).toBe("100234");
    expect(formatRecordValue("007.1234", "MATCH KEY")).toBe("007.1234");
    expect(formatRecordValue("INV-1.2.3", "MATCH KEY")).toBe("INV-1.2.3");
    expect(formatRecordValue(null, "Comment")).toBe("—");
  });

  it("show every number of a column with decimals the same way", () => {
    const rows = [{ KEY: 1, AMOUNT: 100 }, { KEY: 2, AMOUNT: 125.678 }];
    const decimals = decimalColumns(rows);
    expect([...decimals]).toEqual(["AMOUNT"]);
    expect(formatRecordValue(100, "AMOUNT", decimals)).toBe(two(100));
    expect(formatRecordValue(1, "KEY", decimals)).toBe("1");
  });
});

const INSTANT = Date.UTC(2026, 8, 30, 19, 19, 50, 472);

describe("server timestamps", () => {
  it("read a value without an offset as UTC, not local time", () => {
    expect(parseServerTime("2026-09-30T19:19:50.472435").getTime()).toBe(INSTANT);
    expect(parseServerTime("2026-09-30 19:19:50.472").getTime()).toBe(INSTANT);
  });

  it("respect an explicit offset", () => {
    expect(parseServerTime("2026-09-30T19:19:50.472435Z").getTime()).toBe(INSTANT);
    expect(parseServerTime("2026-09-30T19:19:50.472435+00:00").getTime()).toBe(INSTANT);
    expect(parseServerTime("2026-10-01T00:49:50.472+05:30").getTime()).toBe(INSTANT);
  });

  it("format in the viewer's time zone, with a fallback for missing values", () => {
    const options: Intl.DateTimeFormatOptions = { dateStyle: "medium", timeStyle: "short" };
    expect(formatServerTime("2026-09-30T19:19:50.472435", options)).toBe(new Date(INSTANT).toLocaleString(undefined, options));
    expect(formatServerTime(null, undefined, "Never")).toBe("Never");
    expect(formatServerTime("not a date")).toBe("not a date");
  });
});
