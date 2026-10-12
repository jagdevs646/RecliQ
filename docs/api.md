# API reference

All endpoints are under `/api` (set by `API_PREFIX`), except the health checks. The interactive OpenAPI documentation is at `/docs` on a running backend and always matches the code; this page is an overview.

## Workspace identity and authentication

Guests remain anonymous. On the first request the backend creates a UUID session, returns it in `X-Session-ID` and sets the guest cookie. Optional accounts use an opaque HttpOnly auth cookie backed by a database session; account workspace ownership is resolved server-side. An authenticated cookie cannot be overridden with `X-Session-ID`. Cookie-authenticated state-changing requests must include `X-CSRF-Token` from `GET /auth/me`.

| Method and path | Purpose |
|---|---|
| `GET /session` | Current anonymous guest session |
| `POST /auth/register`, `POST /auth/verify-email` | Register a standard user, verify email; registration requires configured email delivery |
| `POST /auth/login`, `POST /auth/logout`, `GET /auth/me` | Sign in, revoke current session, resolve current identity/workspace/features |
| `POST /auth/password/forgot`, `POST /auth/password/reset`, `POST /auth/password/change` | Password recovery and change |
| `PUT /auth/profile`, `POST /auth/email/change`, `POST /auth/email/change/verify` | Profile and verified email updates |
| `GET /auth/sessions`, `DELETE /auth/sessions/{id}` | List and revoke account sessions |
| `POST /auth/mfa/setup`, `POST /auth/mfa/enable`, `POST /auth/mfa/disable` | Configure TOTP MFA; required for Super Admin access |
| `GET /auth/preferences`, `PUT /auth/preferences` | Account theme and notification preferences |
| `POST /auth/import-guest` | Explicitly transfer the current guest workspace into the signed-in account |
| `GET /auth/oauth/providers`, `GET /auth/oauth/{provider}` | Provider availability and OAuth start; Google, Facebook and LinkedIn require configured credentials |

OAuth provider email-verification claims are honored when present: an explicit unverified claim is rejected. If a provider omits that claim, RecliQ does not treat the email as verified or link an existing account by email. A new account must verify ownership through RecliQ's email confirmation flow before retrying OAuth. This fallback requires configured email delivery; otherwise the OAuth flow reports that verification cannot be completed.

In `development`, `local` or `test` with `EMAIL_BACKEND=log`, registration and verification-resend responses include a `development_verification_url`, and a password-reset response for an existing account includes `development_reset_url`. These URLs are shown by the local UI for testing; no email is sent. They are never returned by the SMTP flow. Production password-reset responses remain generic whether or not the address belongs to an account, and configured SMTP sends the one-hour, single-use reset link to that account's email address.

Administrative routes are under `/admin`. Every route requires an active Super Admin account with MFA. They cover overview, user search/detail/status/session revocation, feature/quota policy, admin audit, report-email delivery metadata and system health. `GET /admin/email-deliveries/summary` returns database-aggregated counts by delivery status; `GET /admin/email-deliveries?status=…&offset=0&limit=50` returns a paginated recipient-level history. `GET/PUT /admin/email-settings` reads or changes the platform sender address; `POST /admin/email-settings/test` sends a configuration test only to the signed-in Super Admin's verified address. The sender address defaults to `jagdev.s65@gmail.com`; SMTP host and credentials remain backend environment secrets. Admin policy updates never override ownership or audit protections. Account feature overrides can only restrict global features; numeric quotas can be raised or lowered within deployment hard caps, while allowed formats remain an intersection with the global policy.

Timestamps in responses are UTC with an offset (e.g. `2026-09-30T18:52:11Z`).

## Files

| Method and path | Purpose |
|---|---|
| `GET /files/formats` | Workspace-allowed extensions and effective upload/row limits |
| `POST /files/upload` | Upload one file (`multipart/form-data`, field `file`). Content is checked against the extension; size and row limits apply |
| `GET /files/{file_id}/metadata` | File name and its sheets (`[{id, name}]`) |
| `GET /files/{file_id}/columns?orientation=vertical&sheet_id=…` | Column headers of one sheet; `orientation` is `vertical` or `horizontal` |

Supported formats are `.xlsx`, `.xls`, `.csv`, `.tsv`, `.txt`, `.pdf` and `.docx` (tables), MT940 (`.sta`, `.mt940`, `.940`), BAI2 (`.bai`, `.bai2`), CAMT.053 (`.xml`) and GSTR-2B/2A JSON (`.json`). `/files/formats` filters this list by workspace policy, and the upload endpoint enforces the same allowed-format list. Bank statements are converted into Transactions and Balances sheets; see [operations-and-security.md](operations-and-security.md#bank-statements-and-gst-returns).

## Setting up a run

| Method and path | Purpose |
|---|---|
| `POST /analysis/` | Suggest keys and column pairs for two sheets |
| `POST /analysis/precheck` | Data-quality check of a full plan before running: blockers, warnings and information per sheet rule |
| `GET /reconciliation/gst/config` | Columns the GST reconciliation needs |
| `GET /reconciliation/sample-template?type=generic` | Download a sample input workbook (`type=gst` for the GST layout) |

## Running a reconciliation

- `POST /reconciliation/generic`: start a general reconciliation from a **plan**.
- `POST /reconciliation/gst`: start a GST invoice reconciliation.

Both return the new job (`status: queued`). A plan has one entry per file pair, and each file pair has one or more independent **sheet rules**:

```json
{
  "orientation": "vertical",
  "file_pairs": [
    {
      "file_pair_id": "pair-1",
      "source_files": [{ "file_id": "<uuid>" }],
      "destination_files": [{ "file_id": "<uuid>" }],
      "report_metadata": { "label": "Books vs Bank" },
      "sheet_rules": [
        {
          "sheet_rule_id": "rule-1-1",
          "source_sheets": ["Books"],
          "destination_sheets": ["Bank_Statement"],
          "report_label": "Books -> Bank_Statement",
          "matching_strategy": {
            "primary_key_source": ["REFERENCE"],
            "primary_key_destination": ["CHEQUE_UTR"],
            "secondary_conditions": [
              { "source_column": "VENDOR", "destination_column": "PAYEE", "comparison_method": "matcher_based" }
            ],
            "matching_passes": [
              { "type": "amount_date", "amount_source": "DEBIT", "amount_destination": "DEBIT",
                "date_source": "BOOKS_DATE", "date_destination": "BANK_DATE", "date_window_days": 3 }
            ]
          },
          "reconciliation_mapping": [
            { "file_1_fields": ["DEBIT"], "file_2_fields": ["DEBIT"] },
            { "file_1_fields": ["NARRATION"], "file_2_fields": ["NARRATION"] }
          ],
          "transformations": [
            { "operation": "trim", "side": "both", "columns": ["REFERENCE"], "params": {} }
          ],
          "tolerances": [
            { "field": "DEBIT", "amount": 5, "percent": 1 },
            { "field": "NARRATION", "similarity": 75 }
          ],
          "date_format": "day_first",
          "include_columns_file_1": [],
          "include_columns_file_2": []
        }
      ]
    }
  ],
  "precheck_acknowledged": true
}
```

- **Keys**: `primary_key_*` may list several columns (a combined key), or be empty when a matching pass finds the records instead (for example a bank statement without references).
- **`secondary_conditions`**: columns that must also agree before a key match is accepted. `comparison_method` is `exact_text`, `normalized_date`, `numeric_tolerance` (with `numeric_tolerance`) or `matcher_based`.
- **`matching_passes`**: run in order on records still unmatched. `amount_date` needs the same amount and dates within `date_window_days`; `amount_tolerance` accepts an amount within `amount_tolerance` or `amount_tolerance_percent`, optionally with a date window. Either may also require a similar narrative (`narrative_source`, `narrative_destination`, `narrative_threshold`).
- **`transformations`**: `trim`, `uppercase`, `lowercase`, `remove_characters`, `replace_text`, `remove_prefix`, `remove_suffix`, `strip_leading_zeros`, `keep_alphanumeric`, `invert_sign`, `absolute_value`, `multiply`, `round` (half up), and `debit_credit_to_signed` (creates a signed amount column).
- **`tolerances`**: applied after matching. A band names a compared field (or `*` for all) and at least one limit: `amount`, `percent` (of the larger of the two values, so swapping the files gives the same result), `days`, or text `similarity` (0 to 100). Differences within a band are accepted and the record is reported as matched, with the reason in its comment.
- **`date_format`**: `day_first` or `month_first` for ambiguous dates such as 03/04/2026. Year-first dates are always read as year-month-day.
- Optional per rule: `similarity_policy`, `normalization` (company-name rules) and `date_only_override` (allow a date as the only key).

GST request:

```json
{ "file_1_id": "<uuid>", "file_2_id": "<uuid>", "orientation": "vertical", "text_threshold": 85 }
```

## Jobs

| Method and path | Purpose |
|---|---|
| `GET /jobs?limit=50` | Recent runs of this session |
| `GET /jobs/{job_id}` | One run: status (`queued`, `processing`, `completed`, `completed_with_errors`, `failed`, `cancelled`), progress and file names |
| `WS /jobs/{job_id}/ws` | Live progress updates |
| `GET /jobs/{job_id}/plan` | A general run's stored plan and files, to change rules and run again. `409` if the run cannot be reopened or its uploads are gone |
| `GET /jobs/{job_id}/gst-plan` | The owned GST run's source/destination files, selected sheets, orientation, and supplier confidence threshold for Run Again; returns `404` outside the workspace and `409` if setup or uploads are unavailable |
| `POST /jobs/{job_id}/cancel` | Cancel a queued or running job |
| `DELETE /jobs/{job_id}` | Delete a run, its report, and its uploads when no other run uses them |
| `DELETE /jobs` | Delete all runs of this session |

## Reports

| Method and path | Purpose |
|---|---|
| `GET /reports/job/{job_id}/summary` | Counts per outcome, per file pair and per sheet rule |
| `GET /reports/job/{job_id}/preview?category=…&offset=0&limit=25` | One page of a result category. `category`: `discrepancies`, `only_file_1`, `only_file_2`, `review`, `exception_matches`, `ambiguous_matches`, `not_found`, `auto_resolved`. Optional `file_pair_id`, `sheet_rule_id` |
| `GET /reports/job/{job_id}/download` | The Excel report (a ZIP when a run has several file pairs, or `?file_pair_id=` for one) |
| `POST /reports/job/{job_id}/download_custom` | A report rebuilt with chosen sheets, date format and number format |
| `GET /reports/{report_id}/download` | Download by report id |
| `POST /reports/job/{job_id}/email` | Queue report delivery to authorized recipients (feature and quota checks apply) |
| `GET /reports/job/{job_id}/email` | List this workspace's delivery status |
| `POST /reports/email-deliveries/{id}/retry`, `DELETE /reports/email-deliveries/{id}/link` | Retry a failed delivery or revoke its secure-link capability |

Email is sent by the backend worker. `EMAIL_BACKEND=log` is only a local test transport; the application does not claim real delivery unless the configured SMTP transport confirms success.

## Saved setups

| Method and path | Purpose |
|---|---|
| `GET /templates` / `POST /templates` | List, or save a setup from a plan or a finished `job_id` |
| `GET /templates/{id}` and `GET /templates/{id}/versions/{version}` | A setup and its versions |
| `PUT /templates/{id}` / `DELETE /templates/{id}` | Save a new version / archive |
| `POST /templates/{id}/resolve` | Match the saved sheets and columns to new files, listing anything to choose |
| `POST /templates/{id}/run` | Run the setup on new files |

## Name aliases and learning

| Method and path | Purpose |
|---|---|
| `GET /aliases`, `POST /aliases`, `DELETE /aliases/{id}` | Approved organization aliases |
| `POST /aliases/preview` | Would two names be treated as the same? |
| `POST /aliases/decisions`, `GET /aliases/decisions` | Reviewers' confirm/reject decisions on proposed matches |
| `GET /aliases/suggestions`, `POST /aliases/suggestions/approve` | Aliases suggested by repeated confirmations, and approving one |
| `GET /learning` | Decision history, reliability of each matching method, and suggestions |

## Auto-resolution rules

| Method and path | Purpose |
|---|---|
| `GET /resolution-rules/catalog` | Exception types, conditions and resolutions available |
| `GET /resolution-rules`, `POST /resolution-rules` | List, create |
| `PUT /resolution-rules/{id}`, `DELETE /resolution-rules/{id}` | New version, remove |
| `POST /resolution-rules/order` | Change the order rules are tried in |
| `POST /resolution-rules/preview` | What a rule would resolve in a finished run |
| `GET /resolution-rules/suggestions` | Rules suggested from recurring unresolved exceptions |

## Audit log

| Method and path | Purpose |
|---|---|
| `GET /audit/events` | Events, newest first; filter by `action`, `entity_type`, `entity_id`; page with `limit` and `offset` |
| `GET /audit/verify` | Recompute the hash chain and report the first altered or missing entry |
| `GET /audit/export?format=csv\|json` | Export |

## Health

- `GET /health`: liveness.
- `GET /health/ready`: checks the database and, with the Redis queue, Redis; `503` when either is down.

## Errors

Errors return `{"detail": "…"}` with a message meant for the user. Unexpected errors return `{"detail": "Internal server error", "request_id": "…"}`; every response carries `X-Request-ID` for tracing.
