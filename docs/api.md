# API Overview

Base URL: `/api`

## Authentication

There is no authentication in the MVP. The backend creates an anonymous UUID v4 session for each visitor, returns it in `X-Session-ID`, and stores it in an HttpOnly cookie. The frontend also persists and sends the value in `X-Session-ID`. Every file, job, history, and report query is filtered by that session.

## Files

- `POST /files/upload` - upload `.xlsx` or `.xls`.
- `GET /files/{file_id}/columns?orientation=vertical` - return normalized columns.

## Reconciliation

- `POST /reconciliation/generic`
- `POST /reconciliation/gst`

Generic request body:

```json
{
  "file_1_id": "uuid",
  "file_2_id": "uuid",
  "key_file_1": "Invoice No",
  "key_file_2": "Invoice_Number",
  "orientation": "vertical",
  "rules": [
    {
      "file_1_fields": ["Amount"],
      "file_2_fields": ["Invoice Amount"]
    },
    {
      "file_1_fields": ["Taxable Value", "GST"],
      "file_2_fields": ["Total Taxable", "Tax"]
    }
  ],
  "include_columns_file_1": ["Vendor"],
  "include_columns_file_2": ["Supplier"]
}
```

GST request body:

```json
{
  "file_1_id": "uuid",
  "file_2_id": "uuid",
  "orientation": "vertical",
  "text_threshold": 85
}
```

## Jobs And Reports

- `GET /jobs` - recent jobs.
- `GET /jobs/{job_id}` - job status.
- `GET /reports/{report_id}/download` - download by report id.
- `GET /reports/job/{job_id}/download` - download report for a completed job.

## Health

- `GET /health`
