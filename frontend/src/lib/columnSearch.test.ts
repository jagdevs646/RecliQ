import { describe, expect, it } from "vitest";
import { filterColumns, matchRanges, searchTerms } from "./columnSearch";

const columns = ["SIGNED_AMT", "Signed Amount", "NARRATION", "Value Date", "BANK-DATE", "Cheque No."];

describe("column search", () => {
  it("shows every column for an empty query", () => {
    expect(filterColumns(columns, "  ")).toEqual(columns);
  });

  it("ignores case and separators, and needs every word in any order", () => {
    expect(filterColumns(columns, "amt signed")).toEqual(["SIGNED_AMT"]);
    expect(filterColumns(columns, "signed")).toEqual(["SIGNED_AMT", "Signed Amount"]);
    expect(filterColumns(columns, "bank date")).toEqual(["BANK-DATE"]);
    expect(filterColumns(columns, "date")).toEqual(["Value Date", "BANK-DATE"]);
    expect(filterColumns(columns, "cheque no.")).toEqual(["Cheque No."]);
    expect(filterColumns(columns, "gst")).toEqual([]);
  });

  it("returns the matched parts for highlighting, merged", () => {
    expect(matchRanges("Signed Amount", searchTerms("amount sig"))).toEqual([[0, 3], [7, 13]]);
    expect(matchRanges("NARRATION", searchTerms("narr rat"))).toEqual([[0, 6]]);
    expect(matchRanges("NARRATION", searchTerms("date"))).toBeNull();
  });
});
