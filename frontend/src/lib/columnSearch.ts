// Finding a column by name in a long header list. Every word typed must
// appear in the name, in any order; case and separators (_ - / .) are
// ignored, so "amt signed" finds "SIGNED_AMT".

/** Lowercase with separators as spaces, keeping each character's position. */
function normalize(text: string): string {
  const lowered = text.toLowerCase();
  return (lowered.length === text.length ? lowered : text).replace(/[_\-/.]/g, " ");
}

export function searchTerms(query: string): string[] {
  return normalize(query).split(/\s+/).filter(Boolean);
}

/** Where each term appears in the column name, merged; null when a term is missing. */
export function matchRanges(column: string, terms: string[]): Array<[number, number]> | null {
  const name = normalize(column).toLowerCase();
  const ranges: Array<[number, number]> = [];
  for (const term of terms) {
    const start = name.indexOf(term);
    if (start < 0) return null;
    ranges.push([start, start + term.length]);
  }
  ranges.sort((a, b) => a[0] - b[0]);
  return ranges.reduce<Array<[number, number]>>((merged, range) => {
    const last = merged[merged.length - 1];
    if (last && range[0] <= last[1]) last[1] = Math.max(last[1], range[1]);
    else merged.push([...range]);
    return merged;
  }, []);
}

export function filterColumns(columns: string[], query: string): string[] {
  const terms = searchTerms(query);
  return terms.length ? columns.filter((column) => matchRanges(column, terms)) : columns;
}
