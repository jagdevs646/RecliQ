# API reference

All endpoints are under `/api` (set by `API_PREFIX`), except the health checks. The interactive OpenAPI documentation is at `/docs` on a running backend and always matches the code; this page is an overview.

## Sessions

There are no accounts. On the first request the backend creates an anonymous session (a UUID), returns it in the `X-Session-ID` header and sets it as an HttpOnly cookie. The frontend also sends it back in `X-Session-ID`. Every file, run, report, saved setup, alias, rule and audit entry belongs to one session, and queries only ever see their own session's data.

- `GET /session`: the current session.

Timestamps in responses are UTC with an offset (e.g. `2026-09-30T18:52:11Z`).

## Files

| Method and path | Purpose |
|---|---|
| `GET /files/formats` | Accepted extensions and their descriptions |
| `POST /files/upload` | Upload one file (`multipart/form-data`, field `file`). Content is checked against the extension; size and row limits apply |
| `GET /files/{file_id}/metadata` | File name and its sheets (`[{id, name}]`) |
| `GET /files/{file_id}/columns?orientation=vertical&sheet_id=…` | Column headers of one sheet; `orientation` is `vertical` or `horizontal` |

Accepted formats: `.xlsx`, `.xls`, `.csv`, `.tsv`, `.txt`, `.pdf` and `.docx` (tables), MT940 (`.sta`, `.mt940`, `.940`), BAI2 (`.bai`, `.bai2`), CAMT.053 (`.xml`) and GSTR-2B/2A JSON (`.json`). Bank statements are converted into Transactions and Balances sheets; see [operations-and-security.md](operations-and-security.md#bank-statements-and-gst-returns).

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
- **`tolerances`**: applied after matching. A band names a compared field (or `*` for all) and at least one limit: `amount`, `percent` (of the destination value), `days`, or text `similarity` (0 to 100). Differences within a band are accepted and the record is reported as matched, with the reason in its comment.
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
| `GET /jobs/{job_id}/plan` | The run's stored plan and its files, to change the rules and run again. `409` with a reason if it cannot be reopened (GST runs, or files no longer stored) |
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
