# ADR-0001: Make GST a ready-made setup of the general engine, not a separate reconciliation type

**Status:** Proposed  **Date:** 2026-10-12  **Deciders:** Jagdev Singh (owner)

Words used here. *Engine*: the part of RecliQ that pairs records and compares them. *Preset*: a ready-made setup (keys, columns, tolerances) that fills in the general wizard; the user can still change it. *Parity*: two ways of working give the same results on the same files. *Route*: an address on the server that the screen calls. *Report builder*: the shared step that turns an engine's results into the lines of the downloaded workbook and of the stored results. *Stored results*: the copy of a run's results that RecliQ keeps for its results page. *Branch, commit, merge, push*: a branch is a separate line of code changes; a commit is one saved change; merging brings a branch into the main code; pushing sends it to the shared copy on GitHub. Code references are `file:line` from the repository root, as of commit `6b67fdb`. The local main branch now also holds both bug fixes below (`9789e09` and `4a78fa9`, then `9c4cf16`); of the cited files, they shift lines only in `tolerance.py`, `universal_mapper.py`, `indexed_matcher.py` and `precheck_service.py`.

## Context

**What the user sees today.** Step 1 of the wizard asks "Reconciliation type: General | GST invoices" (`frontend/src/pages/UploadPage.tsx:835`). Choosing GST sends the run to a separate engine, `run_gst_reconciliation` (`backend/app/reconciliation_engine/engine.py:1594`), through its own route `POST /reconciliation/gst` (`backend/app/api/routes/reconciliation.py:63`).

**Two problems in today's GST runs.** Both were found while preparing this record. Both affect GST users now, whatever is decided here.
- **Missing differences.** A GST run counts mismatched invoices but lists none of them. The GST engine names its difference columns like "IGST (File1)" and "IGST DIFF" (`engine.py:1708-1728`). The shared report builder reads only columns named like "IGST (FILE 1)" that also have a status column (`backend/app/reconciliation_engine/universal_mapper.py:53-69`). So a GST report counts a mismatched invoice ("1 item(s) need attention"), but its Differences tab says "No differences were found". The stored results have no difference lines either (`backend/app/services/reconciliation_service.py:642-647`, `:775`). The user learns how many invoices disagree, but not which ones, on which field, or by how much. The naming mismatch dates from 19 August, when the report builder was added (commit `8e4fc6d`), so it is not one of the 12 October changes. No test checks it.
- **Skipped sheets.** A GST run reads only the first workbook on each side and the first selected sheet of it. The screen sends only the first workbook (`UploadPage.tsx:605-606`, `:618-619`). The server then builds a single work item from the first selected sheet (`reconciliation_service.py:510-512`, `:536-537`). Other selected sheets are ignored, and nothing warns the user. A user who selects three monthly sheets gets a report on the first of them only.

**What the GST engine does.**
- It needs ten exact headings in both files: GSTR, NAME OF TRADER/FIRM/COMPANY, INVOICE NO., INVOICE DATE, TAXABLE VALUE, IGST, CGST, SGST, CESS, INVOICE VALUE (`engine.py:64-75`). The screen checks them by exact name, ignoring capitals (`UploadPage.tsx:36-40`), and will not run until all ten are present (`UploadPage.tsx:126`, `:910`). The engine itself refuses files without them (`engine.py:1627-1634`).
- It adds up invoice lines that share GSTIN, name, invoice number and date (`engine.py:78`, `:1636-1637`).
- It looks for each invoice number only among rows with the same GSTIN in the other file (`engine.py:1644-1647`, `:1665-1676`), using a tolerant invoice-number comparison.
- It compares the name (under 85% similar is a difference; 85 to 99% goes to review) (`engine.py:79`, `:1700-1712`), the date (`:1714-1719`) and six amounts (`:1721-1728`). Amounts have no tolerance: they must agree to as many decimal places as the more precise of the two values has, so any difference is reported (`backend/app/reconciliation_engine/matching/indexed_matcher.py:294-306`; `backend/app/reconciliation_engine/cache/cache_manager.py:194-201`).
- An amount it cannot read becomes 0 without a warning (`backend/app/reconciliation_engine/preprocessing/vector_preprocessing.py:65`). The general engine shows the unreadable value as a difference (`engine.py:282-314`; `indexed_matcher.py:409-427`). It stops and names the row only when it has to add that line to others with the same key (`backend/app/reconciliation_engine/matching/advanced_matcher.py:54-64`).

**The GSTR-2B reader.** A GSTR-2B or 2A JSON upload becomes one sheet named "GSTR-2B" (`backend/app/reconciliation_engine/ingestion/finance_formats.py:56`, `:113-114`). Its first ten columns are the GST template headings, and 13 more follow, such as section, document type and ITC available (`finance_formats.py:67-72`). So it contains the template columns rather than exactly them. Credit notes come out as negative amounts (`finance_formats.py:713-715`).

**What the GST path lacks today.**

| Capability | General run | GST run |
|---|---|---|
| Differences listed in the report | Yes | No: counted but not listed (see "Missing differences") |
| Tolerances, preparation steps, extra matching passes | Part of the request (`backend/app/schemas/reconciliation.py:163-198`) | Request holds only files, orientation and the name threshold (`schemas/reconciliation.py:451-457`) |
| Learning, saved aliases, auto-resolution | Loaded (`reconciliation_service.py:434-443`) | Not loaded (same lines) |
| Saved setups | Yes | Refused (`backend/app/services/template_service.py:92-93`) |
| Change and run again | `GET /jobs/{id}/plan` | Refused there (`backend/app/services/job_service.py:98-99`); has its own `GET /jobs/{id}/gst-plan` (`backend/app/api/routes/jobs.py:123-158`) |
| Several sheet rules or file pairs per run | Yes (`schemas/reconciliation.py:201-282`) | One rule and one sheet per side. The screen sends only the first workbook on each side (`UploadPage.tsx:605-606`, `:618-619`), and the server reads only the first selected sheet of it (`reconciliation_service.py:510-512`, `:536-537`). Other selected sheets are ignored without a warning. |
| Data-quality pre-check before running | Yes | Skipped (`UploadPage.tsx:903`, `:910`) |
| Speed measured in the scaling plan | Yes | No (`docs/performance-scaling-plan.md:83`) |
| Dedicated tests | Many | Three (`tests/backend/test_gst_engine.py:27`, `:43`; `tests/backend/test_finance_formats.py:95`) plus one speed test (`tests/backend/test_performance_large_dataset.py:62`). None checks which differences the report lists. |

**What is already shared.** A GST run writes its workbook with the same report writer as a general run (`reconciliation_service.py:605-617`, `:749-750`). But the report builder does not read the GST engine's difference columns, so neither the workbook nor the stored results list GST differences (see "Missing differences"). The older GST-only writer, `write_gst_output` (`backend/app/reconciliation_engine/report_generator/excel_reporter.py:100`), is reached only through a wrapper that nothing calls (`backend/app/reconciliation/gst.py:190-207`). Results of both types are stored the same way (`reconciliation_service.py:772-781`).

**What the general engine already offers a GST setup.**
- Keys made of several columns (`schemas/reconciliation.py:164-165`; `engine.py:982-986`).
- "Must also match" columns (`schemas/reconciliation.py:25-36`; `engine.py:997-1013`). Proposals for similar keys (Pass 1b) run only when such a column exists (`engine.py:1037-1038`).
- Combining rows that share a key, with amounts summed and the combined rows listed (`engine.py:847-869`).
- Tolerances per field: an amount, a percentage, days or text similarity (`schemas/reconciliation.py:113-135`; `backend/app/reconciliation_engine/tolerance.py:89-128`). The first tolerance that fits a field is the one used (`tolerance.py:101`).
- A stated type per field ("number", "date" and so on), which the engine obeys (`schemas/reconciliation.py:11-14`; `engine.py:719-721`).
- Difference columns named the way the report builder reads them, so differences are listed (`engine.py:298-314`).

**Parity check on made-up data.** Seven book rows (six invoices, one split over two lines) against six GSTR-2B rows. Re-run on 12 October on `6b67fdb`. The script sits in the session scratchpad, not in the repository.
- GST engine: 5 invoices paired, 4 agree fully, 1 has a difference (INV-004: IGST 90 vs 95, and so invoice value 590 vs 595), 1 left unpaired on each side. Its results do not name the differing invoice (see "Missing differences"), so on the GST side only the counts could be compared; INV-004 is the one invoice the data was built to differ.
- General engine set up with key GSTR + INVOICE NO., GSTR as "must also match", name accepted at 85% similarity, date and six amounts mapped: the same 5 pairs and the same 1 + 1 unpaired. This includes INV/003 vs INV-003, and INV-001 used by two suppliers. One extra difference: the split invoice showed INVOICE VALUE 708 vs 1180, because its two lines were not summed.
- Cause: the "Invoice amounts" bug below. Only number fields are summed (`engine.py:856-861`), and INVOICE VALUE was not treated as a number. This is a prerequisite, not a reason against the decision.
- The same run with the six amounts stated as numbers matched the GST engine on every count (5 paired, 4 agree, 1 difference, 1 and 1 unpaired).
- Re-run on 13 October with both bug fixes below merged (`9c4cf16`), without stating any types: the general engine now matches the GST engine on every count (5 paired, 4 agree, 1 difference, 1 and 1 unpaired), and INV-004's IGST and invoice value show numeric differences of 5. The real-file parity test (action item 7) is still needed.

**Two engine bugs found while preparing this record, and their status.**
- **Percentage tolerances.** A "± %" tolerance was measured against the File 2 value only, so swapping the files could change the result (`tolerance.py:108-110`). Fixed on branch `fix/percent-tolerance-basis` (commit `9789e09`, on top of `6b67fdb`): it now measures against the larger of the two values. It has been merged into the local main branch, followed by a matching wording change on screen (`4a78fa9`); neither is pushed yet.
- **Invoice amounts.** A number column whose heading contains "invoice", or the separate word "inv", such as INVOICE VALUE, is treated as an invoice number, not an amount (`indexed_matcher.py:140-141`), even when its values are numbers (`indexed_matcher.py:200-201`). So it is not added up when lines are combined. Fixed in commit `9c4cf16`, merged into the local main branch, not pushed yet: a heading with the invoice word and an amount word (value, amount, total, taxable and so on) is an amount, while headings that name the invoice itself ("Invoice No.", "Tax Invoice #", "Credit Invoice", "Invoice/Credit Note Ref") stay invoice numbers. The pre-check and the key suggester now type columns the same way as the run.

**Headings in real files.** Purchase registers exported from accounting software usually carry their own headings, such as "Supplier GSTIN", "Invoice Number" or "Integrated Tax". This is a judgement from experience, not a measurement. If it holds, detection by exact template headings would rarely fire. The current heading synonyms also put IGST, CGST and SGST in one "gst" group and do not know "Integrated Tax" (`backend/app/reconciliation_engine/schema/semantic_mapper.py:11`).

## Decision

Adopt option C. Make GST a preset of the general engine, not a separate engine and screen. Offer it next to today's switch first; once the parity check on real GST files passes, remove the step-1 switch and retire the separate GST engine. Detect GST files by their content: in both files, a column whose values look like GSTINs, plus tax columns. A GSTR-2B JSON upload is a certain signal. When detected, ask one question: "These look like GST invoice files. Use the GST setup?" Also list the GST preset openly on the upload step, so users can find it without detection. Never switch silently.

Separately, and whatever is decided, fix the two problems in today's GST runs now (action items 2 and 3). Current GST users depend on the GST engine until it is retired.

## Options Considered

### Option A: Keep as is

| Dimension | Assessment |
|---|---|
| Complexity | Low: nothing changes |
| Cost | Low now; ongoing cost of keeping two engines in step |
| Scalability | Weak: one rule per run, speed not measured |
| Team familiarity | High |

**Pros:** No work and no risk now. Results users already know, although GST reports today do not list which invoices differ, or on which fields.
**Cons:** Two engines to fix and test. GST runs miss tolerances, learning, saved setups, the pre-check and multi-sheet runs. GST reports do not list the differences they count. Extra sheets selected for a GST run are silently skipped. Users must rename headings to the template. The type is asked before RecliQ has seen the files.

### Option B: Hide the switch, detect the template headings, ask, then open the GST screen (owner's proposal)

| Dimension | Assessment |
|---|---|
| Complexity | Low to medium: reuses today's heading check (`UploadPage.tsx:36-40`) and the existing GST screens; adds a question and hides the switch |
| Cost | Low now, plus option A's ongoing cost |
| Scalability | Same as option A |
| Team familiarity | High |

**Pros:** A cleaner step 1. Cheapest to build. Exact template headings never misfire on a non-GST file. GST results stay exactly as today, so there is nothing to re-test. Users of the GST template get the "it noticed" moment.
**Cons:** Detection would use today's exact-heading rule (`UploadPage.tsx:36-40`), so on real registers it would rarely fire (judgement above). A user who is not prompted can reach GST only through the dashboard shortcut (`frontend/src/pages/DashboardPage.tsx:80`), if it is kept; the upload step gives no hint. Better detection would not help, because the GST engine itself refuses files without the ten headings (`engine.py:1627-1634`). B's narrowness is the engine's narrowness. A hidden feature that few people find does not raise the standard. All of option A's gaps remain, and so do two engines.

### Option C: GST as a preset of the general engine (recommended)

| Dimension | Assessment |
|---|---|
| Complexity | Medium to high: preset, detection, question, parity test, then removal |
| Cost | Medium once; lower afterwards, with one engine |
| Scalability | Good: inherits the general engine's speed work and multi-sheet runs |
| Team familiarity | Medium: the engine is known, presets are new |

**Pros:** One engine to fix, test and speed up. GST users gain tolerances, learning, aliases, auto-resolution, saved setups, a fuller run again (change keys, fields and tolerances, not only files and the name threshold), the pre-check and several sheets per run. Their reports list each difference. Works with any headings, because detection reads the values. The question gives the "it noticed" moment the owner wants, and the open listing keeps GST findable. The user can see and change every rule.
**Cons:** Needs the "Invoice amounts" fix first. Behaviour changes to settle: names 85 to 99% similar become "matched, with a note" instead of "review" (`tolerance.py:10-13`); unreadable amounts show as differences, or stop the run when lines are being combined, instead of silently becoming 0; amount differences up to ±1 rupee are accepted with a note, where the GST engine reports any difference; the preset combines lines by GSTIN + invoice number, while the GST engine also requires the same name and date (`engine.py:78`). More work than option B.

### Option D: Drop GST support

| Dimension | Assessment |
|---|---|
| Complexity | Low |
| Cost | Low |
| Scalability | Not applicable |
| Team familiarity | High |

**Pros:** Least code.
**Cons:** Drops a use case the product leads with (`README.md:3`; `docs/NON_TECHNICAL_GUIDE.md:54`, `:86`). The GSTR-2B reader loses its main purpose. Users would have to build a GST setup by hand (once, then save it), with no guidance.

## Trade-off Analysis

- **Headings or content.** A heading says what a column is called; the values say what it holds. A GSTIN has a fixed 15-character pattern that RecliQ already recognises in values (`indexed_matcher.py:111-113`, `:196-197`). Content detection works on any export. Heading detection works only on the template.
- **Hidden or visible.** A suggestion plus an open listing gives option B's pleasant surprise without the risk that the feature is never found.
- **Silent or confirmed.** A finance user must know which rules produced a report. One question costs a click; a silent switch can cost trust.
- **One engine or two.** Today's accuracy and speed work went into the general engine, and the GST path was outside the scaling plan (`docs/performance-scaling-plan.md:83`, `:648`). Every month with two engines widens the gap. The two problems above show the cost: the GST path fell behind the shared report builder and the multi-sheet work, and nothing caught it.
- **Risk of option C.** Results could shift for GST users. The parity test on real files, run before anything is removed, contains this risk.
- **Tolerances.** The preset uses a rupee amount, not a percentage. The "Percentage tolerances" fix makes "± %" measure against the larger of the two values instead of File 2 only. It does not affect the preset as proposed.

## Consequences

**Needed now, whatever is decided.** Today's GST users get reports that count differences but do not list them, and runs that skip extra sheets without a word. Both must be fixed in the current GST engine (action items 2 and 3). Under option C that engine stays until the real-file parity test passes, which waits on files from the owner, so these fixes cannot wait for it.

**Easier.** One engine to maintain and test. GST runs get every general feature. Any heading layout works. A GST setup can be saved, changed and run again like any other.

**Harder.**
- The preset must be kept in step with the GSTR-2B reader if the portal format changes.
- Detection can misfire. The confirming question and the open listing limit the damage.
- The workspace switch `gst_reconciliation` (`backend/app/services/policy_service.py:18`), which restricted users lose (`policy_service.py:94-96`), needs a new meaning, for example "show the GST preset". Workspaces with general runs switched off but GST on (`frontend/src/App.tsx:192-193`; `reconciliation_service.py:428`) would lose GST, because preset runs are general runs. Decide whether such a workspace may run only the GST preset.
- The RecliQ Desktop app calls the GST routes (`../RecliQ Desktop/recliq_desktop/api.py:218-245`, a sibling folder outside this repository). It calls these routes through its own copy of the backend, so it must change before that copy is next refreshed from this repository.
- Old GST runs need a new "Run again" path; today it opens the GST screen (`frontend/src/App.tsx:271`). Their stored results have counts and unpaired invoices but no difference lines; only running them again can add those.

**Revisit if** the real-file parity test shows differences that settings cannot close; users often decline the suggestion; the GSTR-2B format changes; or GST needs a rule the general engine cannot express (for example, pairing credit notes with the invoices they correct).

## Action Items

1. [ ] Push the two bug fixes, already merged into the local main branch: "Percentage tolerances" (`9789e09`, `4a78fa9`) and "Invoice amounts" (`9c4cf16`, which includes a test with one invoice split over two lines).
2. [x] *Done in `89574a0` (differences listed, pairs to confirm on Match Review) and the commit after it (the report step's tab names).* Make GST reports list their differences now, because current GST users need this whatever is decided: give the GST engine's difference columns the names the report builder reads, with a status column for each field (`engine.py:1708-1728`; `universal_mapper.py:53-69`), and add a test that a differing invoice appears on the Differences tab. Also make the GST report step name the tabs the report really has (`UploadPage.tsx:31`).
3. [x] *Done in `89574a0`: every selected sheet is read and stacked; additional workbooks are refused with a message.* Stop GST runs skipping sheets now: either read every selected sheet on each side, stacked into one table (the server can already stack sheets: `backend/app/api/routes/analysis.py:76-84`), or refuse to start, with a clear message, when more than one workbook or sheet is selected on a side (`UploadPage.tsx:605-606`, `:618-619`; `reconciliation_service.py:510-512`, `:536-537`). Add a test with two selected sheets.
4. [ ] Define the preset. Key: GSTIN + invoice number. Must also match: GSTIN, same text. Compare: supplier name, accepted at 85% similarity; invoice date, exact, day first; taxable value, IGST, CGST, SGST, cess and invoice value, each stated as a number, with ±1 rupee per field. Combine lines that share GSTIN + invoice number and add up their amounts. GSTIN is repeated as a "must also match" column on purpose: the general engine looks for near-identical invoice numbers (INV-03 vs INV-003) only when such a column exists (`engine.py:1037-1038`), and this keeps those proposals within the same supplier, as the GST engine does today (`engine.py:1644-1647`). Decide two points: whether document type joins the key, so a credit note never pairs with an invoice of the same number (`finance_formats.py:708-715`); and whether near names go to review, as today, or to matched with a note.
5. [ ] Write the detection rules. Certain: a file read as GSTR-2B (sheet "GSTR-2B"). Likely: in each file, one column where at least 80% of filled values look like GSTINs, plus at least one tax column (IGST, CGST or SGST, or Integrated, Central or State tax), a taxable value column and an invoice number column. Never on headings alone. Add the GSTIN check digit, since the pattern today checks format only (`indexed_matcher.py:113`). Teach the heading synonyms each tax head separately (`semantic_mapper.py:11`).
6. [ ] Add the confirming question and an open "Ready-made setups" list on the upload step, including "GST: purchase register vs GSTR-2B". Yes fills in the setup and shows it for review; No keeps the general flow. Keep the GST sample template (`api/routes/reconciliation.py:33-50`), moving its column list out of `backend/app/reconciliation/gst.py`. Ship this next to the current switch at first.
7. [ ] Run the parity test on a set of real, anonymised GST files that the owner provides: a purchase register and the matching GSTR-2B JSON, ideally for several months. Compare every invoice's outcome between the GST engine and the preset. Today the GST engine's results do not say which invoices differ, so do item 2 first, or read the GST side from the engine's own internal lists of paired, differing and unpaired invoices. Run the preset first with no amount tolerance, which is true parity, then with ±1 rupee, and list what the tolerance accepts. Once both bug fixes are merged, re-run the made-up check, add to its data a near-identical invoice number that the engine does not already treat as the same (such as INV-03 vs INV-003), and add it to `tests/backend/`. Pass means the same pairs and the same differences, or each difference explained and accepted by the owner.
8. [ ] Until parity holds, keep the GST engine and its routes: `POST /reconciliation/gst`, `GET /reconciliation/gst/config` (`api/routes/reconciliation.py:22-30`) and `GET /jobs/{id}/gst-plan`.
9. [ ] After parity: first redefine the `gst_reconciliation` switch (`policy_service.py:18`, `:94-96`) and settle the general-off, GST-on case. Then remove the step-1 switch and the GST steps (`UploadPage.tsx:835`, `:935-936`, `:1103`); point the dashboard GST shortcut at the preset (`DashboardPage.tsx:80`); remove `run_gst_reconciliation` (`engine.py:1594-1802`), `backend/app/reconciliation/gst.py`, the unused `write_gst_output` and the GST routes; update the desktop app before its backend copy is next refreshed.
10. [ ] Keep old GST runs readable: keep the "GST" labels (`frontend/src/pages/HistoryPage.tsx:95`, `frontend/src/pages/ResultsPage.tsx:215`, `frontend/src/pages/StatusPage.tsx:174`) and their stored results. Old GST runs' stored results have counts and unpaired invoices but no difference lines; say so on their results page. "Run again" on an old GST run opens the general wizard with the GST preset and the same files.
11. [ ] Update the guides: `README.md:36`; `docs/TECHNICAL_ARCHITECTURE_GUIDE.md:119-121`, `:139`; `docs/api.md:47-53`, `:113-117`, `:127`; `docs/operations-and-security.md:141-143`, `:192-194`; `docs/architecture.md:28`; and the GST use cases in `docs/NON_TECHNICAL_GUIDE.md`.
