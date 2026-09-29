# Operations, security and controls

This page covers how RecliQ keeps secrets out of the code, how jobs survive
restarts, how it is monitored, and the controls added for finance use:
saved reconciliations, the audit log, the data-quality pre-check, keyless
matching, transformations and name normalization.

## Secrets

- No secret is committed. Configuration comes from environment variables
  (`backend/.env` locally, which git ignores; a secret store in production:
  Azure Key Vault references in Container Apps, GitHub Actions secrets in CI).
- `backend/.env.example` lists every setting with safe placeholders only.
- CI runs [gitleaks](https://github.com/gitleaks/gitleaks) over the full
  history on every push and pull request (`.gitleaks.toml`). A finding fails
  the build.

### Rotating a leaked credential

A secret that was ever pushed must be treated as public, even after the
history is rewritten: forks, clones and caches may keep it.

1. Rotate first: change the password / regenerate the key at its source
   (Azure portal: reset the user's password and enable MFA; regenerate
   storage account keys; create a new database password; generate a new
   `SECRET_KEY`), then update the secret store.
2. Check the provider's sign-in and activity logs for use of the old value.
3. Purge it from history (see below) and force-push; ask everyone with a
   clone to re-clone.
4. Ask GitHub support to clear cached views of the removed commits if the
   repository was ever public.

### Purging a file from history

```bash
pip install git-filter-repo
git clone --no-local <repo> purge && cd purge
git filter-repo --invert-paths --path "AzureCloud - info.txt"
git remote add origin https://github.com/<owner>/<repo>.git
git push --force --all origin && git push --force --tags origin
```

## Continuous integration (`.github/workflows/ci.yml`)

| Job | What it checks |
| --- | --- |
| Secret scan | gitleaks over the whole history |
| Backend tests | `pytest tests/backend`; Alembic upgrade/downgrade on PostgreSQL; audit log refuses UPDATE/DELETE/TRUNCATE |
| Frontend | `pnpm typecheck`, `pnpm test` (Vitest), `pnpm build` |
| Dependency audit | `pip-audit` on `backend/requirements.txt`, `pnpm audit` |
| Docker | builds all four images (`docker/*.Dockerfile`) |

## Observability

- Logs are one JSON object per line (`LOG_FORMAT=json`, default) with
  `request_id`, `job_id` and `session_id`, so one request or one
  reconciliation can be followed across the API and the workers.
- Every response carries `X-Request-ID` (an incoming one is reused). An
  unhandled error returns `{"detail": "Internal server error", "request_id": …}`
  and is logged with its stack trace.
- Set `SENTRY_DSN` to send errors to Sentry (FastAPI and RQ integrations;
  personal data is not sent). Job errors are tagged with the job ID, type
  and attempt.
- `GET /health` is liveness; `GET /health/ready` checks the database and
  (with the Redis backend) the queue, returning 503 when either is down.

## Uploads

- Accepted formats (`GET /api/files/formats`): `.xlsx`, `.xls`, `.csv`,
  `.tsv`, `.txt`, `.pdf` (tables) and `.docx` (tables), plus bank
  statements and GST returns (below). `.doc` and `.md` were listed before
  but never worked, so they are refused.
- Content is checked against the extension (ZIP/OOXML, OLE2, `%PDF`, no
  binary in text files); password-protected workbooks and ZIP bombs are
  refused with a clear message.
- `MAX_UPLOAD_MB` (default 50) is enforced while the request streams in, and
  `MAX_ROWS_PER_SHEET` (default 500,000) from workbook metadata at upload
  and again when a sheet is parsed.

### Bank statements and GST returns

| Format | Extensions | Sheets |
| --- | --- | --- |
| MT940 (SWIFT) | `.sta`, `.mt940`, `.940`, or `.txt` (recognised by content) | Transactions, Balances |
| CAMT.053 (ISO 20022; CAMT.052/054 too) | `.xml` | Transactions, Balances |
| BAI2 | `.bai`, `.bai2`, or `.txt` (recognised by content) | Transactions, Balances |
| GSTR-2B / GSTR-2A (GST portal JSON) | `.json` | GSTR-2B |

- **Transactions**: one row per booking with booking and value dates (ISO,
  never ambiguous), Debit/Credit, Amount, Signed Amount (credit +, debit −),
  separate Debit and Credit columns, currency, bank transaction code and
  type, customer and bank references, counterparty and narrative. MT940
  `?20…?33` sub-fields are decoded; CAMT batch bookings are split into their
  payments when each has an amount; BAI2 `88` continuations and funds-type
  fields are handled. A BAI2 code outside 100–699 has no defined direction,
  so it gets no signed amount rather than a guessed one.
- **Balances**: opening and closing balance per statement, the booked credits
  and debits, and a check that opening + credits − debits = closing
  ("Balanced" or "Off by …"). A BAI2 control total that does not add up is
  reported here too.
- **GSTR-2B**: one row per document, with exactly the columns the GST
  reconciliation expects (GSTR, trader name, invoice number and date, taxable
  value, IGST/CGST/SGST/cess, invoice value) plus section, document type,
  ITC availability and reason, reverse charge, place of supply, filing dates,
  original numbers for amendments and IRN. Credit notes are negative;
  rate-wise items are summed; imports (bill of entry) are included.
- The file is parsed at upload, so a damaged or mislabelled file is refused
  with the parser's reason. XML is parsed with `defusedxml`: DTDs, entities
  and external references are refused.

## Durable job processing

- `JOB_QUEUE_BACKEND=redis` (production): the API puts the job ID on a Redis
  (RQ) queue and one or more workers run it:
  `python -m app.jobs.worker` (same image as the API; see
  `docker-compose.yml`). `local` runs jobs in an in-process thread pool for
  development.
- The database row is the source of truth. A worker claims a queued job with
  a conditional update (only one worker can win), counts the attempt and
  refreshes a heartbeat while it runs.
- A job whose heartbeat is older than `JOB_LEASE_SECONDS` (default 180) is
  orphaned: its worker crashed or restarted. A sweeper (in every worker, and
  in the API with the local backend) re-queues it, up to
  `JOB_MAX_ATTEMPTS` (default 3), then fails it with an explanation. Queued
  jobs lost by a Redis or API restart are re-queued too.
- Database, storage and queue outages during a run are retried; data or
  configuration errors are not (they would fail the same way again).
- Uploads and reports live in the storage backend. Use
  `STORAGE_BACKEND=azure` in production (or a volume shared by the API and
  the workers). The report data used for previews and custom reports is
  stored as its own object, so it works from any instance.
- Render's free plan has no workers and only ephemeral disk; there the app
  keeps using the local backend. Use a paid worker and Azure Blob Storage for
  durable production processing.

## Saved reconciliations

Save a setup from the review step ("Save for reuse") or from a finished
run. A saved reconciliation stores file pairs, sheet rules, keys, secondary
keys, mappings, transformations, extra passes, name settings, thresholds and
report settings. Editing creates a new version; old versions stay readable.

"Run on new files" matches the saved sheets and columns to the new files:
same name → saved alternative name → same name ignoring punctuation →
accounting synonym (only if one column qualifies) → very similar name (only
if it clearly wins). Anything else is shown for the user to choose; nothing
is guessed. The run is linked to the template and version it used.

## Audit log

Append-only record of uploads (with SHA-256), rejected uploads, pre-checks,
runs, cancellations, deletions, retention pruning, downloads, custom
reports, saved-setup changes (before/after), review decisions and alias
changes. Each entry stores the actor, time, IP address, user agent and
request ID.

- The ORM refuses updates and deletes, and database triggers refuse
  UPDATE, DELETE (and TRUNCATE on PostgreSQL).
- Entries are hash-chained per scope; `GET /api/audit/verify` recomputes the
  chain and reports the first altered or missing entry.
- Export: `GET /api/audit/export?format=csv|json`.
- Actor: until user accounts exist the actor is the anonymous browser
  session (`actor_type=session`) or a system component. The schema already
  separates actor and scope for users and organizations.

## Data-quality pre-check

Before "Run", `POST /api/analysis/precheck` reads the sheets exactly as the
run will and reports: missing columns and empty sheets (blockers), blank and
repeated keys, how many source keys exist in the other file, numbers stored
as text, compared fields with different types, ambiguous or mixed date
formats (with a one-click switch between day-first and month-first), blank
secondary keys and unusable amounts/dates for keyless passes. Blockers must
be fixed; warnings must be confirmed, and the confirmation is stored with
the job and in the audit log.

## Matching

Per sheet rule, in order:

1. **Transformations** (optional) prepare values: trim, case, remove
   prefix/suffix/characters, replace text, leading zeros, keep letters and
   digits, flip or drop the sign, multiply, round, and Debit/Credit → one
   signed amount. Only this fixed list exists (no expressions). The report
   shows the original next to every changed value.
2. **Primary key** (with mandatory secondary keys, as before).
3. **Extra passes** (optional), each only on records still unmatched:
   amount + date within ±N days, or amount within an absolute/percentage
   tolerance (optionally with a date window), each optionally requiring a
   similar reference/narrative. A pair is made only when it is mutually
   unique; otherwise every candidate is listed as "several possible
   matches". A rule may have no key at all (for example a bank statement).

### Name normalization and aliases

Names and text keys are compared in a normal form that treats these as
equal: Pvt/Private, Ltd/Limited, Co/Company, Corp/Corporation,
Inc/Incorporated, LLP, PLC, (P); "&"/"and"; case, punctuation and spacing;
M/s, Messrs and The; A.B.C./ABC; common abbreviations (Intl, Bros, Mfg, A/c,
Pmt…); and, for names, word order. Ambiguous abbreviations (Int, Ind, Tech)
are deliberately excluded. Fuzzy scoring only runs after these rules fail and
never counts as an exact match. The explanation and the "Normalization
applied" column name every rule that produced a match.

Organization aliases ("IBM" = "International Business Machines") are added
by people on the Name aliases page. Reviewers can confirm or reject proposed
matches in the results; a pair is *suggested* as an alias only after it was
confirmed in at least two different reconciliations and never rejected, and
it is used only after someone approves it.

### Learning from reviewers' decisions

Reviewers confirm or reject "Matches to confirm" on the results page. Each
decision is kept (with the method that proposed the match and its score) and
used conservatively:

- A **rejected pairing is never proposed again**. If exactly one other
  candidate then qualifies, that one is proposed instead (still to confirm).
  A rejection can be withdrawn on the Learning page ("reset"); that is
  audited too.
- A pairing confirmed before is labelled "Confirmed by a reviewer N times".
- **Rule weights**: for each method (e.g. "Similar key (company name)" in
  5-point score bands, or a given amount + date pass) RecliQ shows the share
  of proposals reviewers confirmed and a conservative estimate (lower end of
  the 95% Wilson interval). After 5 decisions, results show it as a learned
  confidence. Suggestions follow from it: raise a threshold for a band that
  is often rejected, tighten a weak pass, or (after at least 10 decisions
  with an estimate of 90% or more) add a rule to confirm a reliable method.
- Nothing is confirmed automatically by learning. Name aliases still need
  approval, and decisions on keyless (amount/date) passes never become
  aliases.

### Auto-resolution rules

Rules explain exceptions a team clears the same way every period. Each rule
has one exception type, conditions that must all hold, and a resolution with
optional reason code, GL account and note:

| Exception type | Conditions |
| --- | --- |
| Only in the source / destination file | column contains / is / starts or ends with / is (not) blank; amount at most / at least (with or without sign) / between. `*` = any column |
| Difference in a compared field | field is …; difference at most X or X%; plus column conditions |
| Match to confirm | confirmed by a reviewer at least N times; confidence at least X%; found by a given method |

- Rules run after matching, in the order shown. The first rule that fits
  labels the exception; it is removed from its exception list and listed on
  the report's **09 Auto-resolved** tab with the rule name and version,
  resolution, reason code and GL account. The Checks tab still balances.
- Safety limits: every rule needs a condition; a condition on a column the
  record does not have is false; difference rules must cap the difference;
  confirmation rules need earlier reviewer confirmation or a minimum
  confidence; ambiguous records are never resolved.
- **Test before saving**: the editor shows what a rule would resolve in any
  finished reconciliation (it uses that report's columns).
- **Suggestions**: unresolved unmatched records are grouped by column and
  wording (numbers removed, e.g. "bank charges jan"). Wording seen in 3 or more
  reconciliations and not yet covered by a rule is suggested, with an amount
  cap at the largest amount seen.
- **Audit**: creating, changing (new version, before/after), reordering and
  removing rules are audit events. Each run records which rule versions were
  active (`resolution_rules` in the job summary) and one
  `exceptions.auto_resolved` event with the count per rule version.
