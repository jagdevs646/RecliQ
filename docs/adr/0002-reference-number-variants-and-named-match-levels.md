# ADR-0002: Match differently written reference numbers with named rules, and replace percentage boxes with named match levels

**Status:** Accepted  **Date:** 2026-11-04  **Deciders:** Jagdev Singh (owner)

Words used here. *Primary key*: the column(s) that identify the same record in both files, such as an invoice number. *Normalized*: the form RecliQ compares after removing spaces, case and punctuation, so `INV/005` and `INV-005` both become `inv005`. *Fuzzy score*: a 0-100% similarity between two texts (the `rapidfuzz` library). *Match Review*: the report tab and dashboard list of pairings a person must confirm. *Threshold*: the minimum fuzzy score that counts as similar. *Tolerance*: an accepted difference on a compared field after records are paired (it never decides pairing). Code references are `file:line` from the repository root, as of commit `6a8b906`.

## Context

**The user's problem.** The same invoice is often written differently in two systems: `INV-0105` in the books and `105` on the GST portal, or `INV/2026-27/105` against `105`. Users want these paired without retyping keys in Excel.

**What RecliQ does today.**
- Keys are compared in normalized form only. `normalized_identity_value` (`backend/app/reconciliation_engine/matching/indexed_matcher.py:284`) removes punctuation, so `INV/005` = `INV-005` already pairs.
- Beyond that, reference numbers are all-or-nothing. `_compare_identifier` (`indexed_matcher.py:381-393`) returns 100% (identical), 97% (same after normalization) or **0%** for anything else. No "close" score exists for invoice, GSTIN, PAN or identifier columns.
- The similar-key step (Pass 1b, `backend/app/reconciliation_engine/engine.py:1460-1534`) runs only when the user has set secondary ("must also match") columns. It scores keys with `_primary_similarity_result` (`engine.py:587`) against a threshold of 95% for references or 75% for text (`indexed_matcher.py:278-281`). Because reference numbers score 0 or ≥97, this step can never pair `INV-0105` with `INV-105`.
- Percentages are typed as raw numbers in four places: "How similar (%)" for keys (`frontend/src/pages/UploadPage.tsx:1194`, stored as `SimilarityPolicy.threshold`, `backend/app/schemas/reconciliation.py:41-43`); "Reference similarity (%)" in matching passes (`frontend/src/components/RuleExtras.tsx:160`); the text tolerance "similarity" limit (`frontend/src/lib/plan.ts:319-325`); and amount "± %" ranges (`RuleExtras.tsx:159`).

**The proposal under review.** The owner proposed adding fuzzy matching to primary keys, with five named percentage levels (for example "100%", "≥90%", "≥70%") chosen by name instead of typed, offered wherever a tolerance is entered.

**Measured evidence.** Fuzzy scores (`rapidfuzz.fuzz.ratio`, the score RecliQ already uses) on normalized reference numbers:

| Pair | Score | Truth |
|---|---|---|
| `INV-0105` / `INV-105` | 92% | Same invoice |
| `GST/0105` / `0105` | 73% | Same invoice |
| `INV/2026-27/105` / `105` | 40% | Same invoice |
| `INV-1054` / `INV-1045` (typing slip) | 86% | Same invoice |
| `INV-1O5` / `INV-105` (letter O) | 83% | Same invoice |
| `INV-1001` / `INV-1002` | 86% | Different invoices |
| `INV-2026-0451` / `INV-2026-0457` | 91% | Different invoices |
| `INV-10` / `INV-100` | 91% | Different invoices |
| `TXN202609150001` / `TXN202609150002` | 93% | Different invoices |

Different invoices score 86-93%, while the same invoice scores 40-92%. No single percentage separates them: at ≥90% RecliQ would pair `0451` with `0457` and still miss the year-prefix case, and at ≥70% it would pair neighbouring invoices throughout a series. In a reconciliation, a wrong pairing is worse than a missed one, because a real difference disappears into "matched".

**Forces.**
- Correctness: never silently pair two different invoices.
- Coverage: pair the common real-world variants (leading zeros, prefixes, fiscal-year segments, look-alike characters).
- Speed: fuzzy comparison of every key against every key is 2.5 billion comparisons for 50,000 × 50,000 rows; the nightly speed test must not regress.
- Simplicity: users should pick a meaningful level, not guess a number.

## Decision

1. **Do not apply a fuzzy percentage threshold to reference-number keys.** A percentage cannot distinguish a format difference from a different invoice.
2. **Add named, deterministic reference-variant rules**, tried in order only when the normalized key does not match:
   - **Leading zeros** in number groups (`0105` = `105`): counted as matched, labelled "key format differs".
   - **Known prefixes, suffixes and fiscal-year segments** (`INV/2026-27/105` = `105`): sent to Match Review.
   - **Look-alike characters inside digit runs** (O↔0, I/L↔1, S↔5): sent to Match Review.

   A rule applies only when it yields exactly one candidate on each side. Each pairing records the rule used, shown in the report and dashboard.
3. **Add fuzzy key suggestions as a last step, never as matches.** A suggestion requires the amount to agree (within tolerance), and the date or party too when those are mapped. The best candidate must lead the second by at least 5 points. Suggestions always go to Match Review. Candidates are narrowed through an n-gram index, never compared against every row.
4. **Replace the raw percentage and tolerance boxes with named levels, defined per kind of data.** The underlying number is stored with the level name, and "Custom" stays under Advanced:

| Data | Levels |
|---|---|
| Names and narratives (text) | Exact 100% · Very close ≥90% · Close ≥80% · Similar ≥70% · Loose ≥60% (shows a warning) |
| References / IDs | Exact · Formatting only · Variants (rules in point 2) · Variants + suggestions |
| Amounts | Exact · Rounding ±0.05 · ±1 · ±0.5% · ±1% |
| Dates | Same day · ±1 day · ±3 days · ±7 days |

5. **Keep saved plans working.** A saved numeric threshold loads as the nearest level when it equals that level's value, otherwise as "Custom" with the same number.

## Options Considered

### Option A: Fuzzy percentage on primary keys with five named percentage levels (as proposed)
| Dimension | Assessment |
|-----------|------------|
| Complexity | Low (reuse `rapidfuzz` and the threshold field) |
| Cost | Low to build; high in wrong pairings |
| Scalability | Poor without indexing (every key against every key) |
| Team familiarity | High |

**Pros:** Simple to explain; one control everywhere; helps on names.
**Cons:** The measurements show it pairs different invoices (86-93%) and misses real variants (40-73%). One percentage scale is meaningless for amounts and dates. Wrong pairings hide differences, which harms audit trust.

### Option B: Named reference-variant rules + review-only fuzzy suggestions + named levels per data kind (chosen)
| Dimension | Assessment |
|-----------|------------|
| Complexity | Medium (variant rules, candidate index, level mapping in UI and schema) |
| Cost | About 1-2 weeks including tests |
| Scalability | Good: variant rules are exact lookups; suggestions use a bounded index |
| Team familiarity | High (extends `IndexedCandidateMatcher` and existing passes) |

**Pros:** Pairs the common real variants; never auto-pairs a one-digit difference; each pairing says why; levels mean something per data kind.
**Cons:** Prefix and year patterns need a curated list; more review items at first; four level sets to explain instead of one.

### Option C: Leave matching as is; improve only the UI with named levels
| Dimension | Assessment |
|-----------|------------|
| Complexity | Low |
| Cost | A few days |
| Scalability | Unchanged |
| Team familiarity | High |

**Pros:** No change to matching risk.
**Cons:** Does not solve the stated problem: `INV-0105` and `105` stay unmatched.

### Option D: Let users write their own clean-up formulas (regular expressions) for keys
| Dimension | Assessment |
|-----------|------------|
| Complexity | Medium |
| Cost | Moderate; high support cost |
| Scalability | Good |
| Team familiarity | Medium |

**Pros:** Handles any house format.
**Cons:** Finance users cannot be expected to write regular expressions; mistakes silently mis-pair. Better kept as a later "Advanced" addition to Option B.

## Trade-off Analysis

The deciding trade-off is **false pairings versus missed pairings**. A missed pairing shows up as two "only in" records a person can see and fix. A false pairing hides a genuine difference inside "matched", which is the worst outcome for a reconciliation tool. Option A's own numbers show it would produce false pairings on ordinary invoice series. Option B gets most of Option A's coverage, because the real variants are format patterns, not random typos. It reaches that coverage through exact rules whose meaning can be explained. Anything uncertain goes to a person.

Named levels are kept from the proposal because they are a clear usability gain. They must be defined per data kind, because "≥90%" has no meaning for an amount or a date.

## Consequences

- **Easier:** pairing books against portals that write invoice numbers differently; choosing settings without guessing numbers; explaining each pairing in the report ("matched after removing leading zeros").
- **Harder:** the engine gains a variant step and a suggestion index to maintain; Match Review will hold more items at first; documentation must explain four level sets.
- **Revisit:**
  - The prefix and fiscal-year pattern list, after a month of real files.
  - Whether "Formatting only" should also cover the leading-zero rule.
  - Whether accepted suggestions should feed the existing learning store (`backend/app/reconciliation_engine/learning.py`), so that a confirmed variant pairs automatically next time.
  - Whether the GST engine should adopt the same variant step (see ADR-0001).

## Action Items

1. [x] Owner: level names confirmed; a leading-zero match counts as matched (2026-11-04).
2. [x] Variant rules: `matching/reference_variants.py`, run by `_run_reference_variant_passes` after the exact key pass and before Pass 1b. The rule name is recorded as the pairing's match method. They live beside the engine, not inside `IndexedCandidateMatcher`, because they pair whole key sets with mutual uniqueness rather than scoring one candidate.
3. [x] Review-only suggestions: `ReferenceSuggester` (trigram index) and `_run_reference_suggestions`.
4. [x] `SimilarityPolicy.reference_matching` stores the reference level. Text, amount and date levels are not stored: they are named from the saved numbers (`match_levels.py`, `frontend/src/lib/matchLevels.ts`), and any other number shows as "Custom". Saved plans without a level stay on "Formatting only"; new rules start on "Variants".
5. [x] Level pickers (`LevelSelect.tsx`) in the upload page, rule extras and mapping tolerances, with "Custom…" keeping the number inputs.
6. [x] The rule or level appears in the report's reason column, in Match Review and in the data check.
7. [x] Tests: `tests/backend/test_reference_variants.py`, `frontend/src/lib/matchLevels.test.ts`; the existing suites are unchanged.
8. [x] Speed, 100,000 rows (engine only, same machine, on mains): keyed 12.18 s, variants 12.67 s, suggestions 13.04 s, with identical outcome counts and peak memory about 705 MB. Suggestions proposed nothing there because the benchmark's "mistyped" references swap two digits and so spell another row's reference, which pairs exactly.
