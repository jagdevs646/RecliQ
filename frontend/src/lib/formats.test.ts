import { describe, expect, it } from "vitest";
import { formatServerTime, parseServerTime } from "./formats";

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
