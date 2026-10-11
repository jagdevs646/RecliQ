# RecliQ Support Request Policy

This document defines the support-request workflow and data boundary for RecliQ's anonymous and registered workspaces.

## Ownership and access

- Each support ticket belongs to the server-resolved workspace scope. Registered accounts use `user-<id>`; guests use their anonymous session UUID.
- A user can list, open, and reply only to tickets in the current workspace. Knowing a ticket ID does not grant access.
- Only an active Super Admin session with MFA can search platform tickets, see requester account metadata, triage, prioritize, or reply as RecliQ.
- Admin views do not provide access to uploaded workbooks, reports, reconciliation results, or source data.

## Submission fields

Every request records a category, workflow stage, impact, subject, and description. “Other” requires a custom area. Expected behavior and reproduction steps are optional. Attachments are not accepted. The interface tells users not to include workbook contents, credentials, or secrets.

## Status lifecycle

- `new`: submitted by a user or reopened by a user reply.
- `in_progress`: an administrator is investigating.
- `waiting_for_user`: an administrator has replied and needs more information.
- `resolved`: the administrator considers the request resolved. A later user reply reopens it as `new`.

Priority is assigned by the administrator (`low`, `normal`, `high`, or `urgent`); the user selects impact, not priority. A complaint reply without an explicit status sets the ticket to `waiting_for_user`.

## Notifications and audit

- A new ticket creates an in-app admin notification state. It appears as an unread count in the admin sidebar and dashboard; viewing a ticket marks it read.
- Complaint creation and messages do not send email. This avoids external disclosure and duplicate email side effects. Admin replies are visible in the originating workspace conversation.
- Ticket content is not copied into audit metadata. Ticket creation and admin changes are recorded in the existing workspace/admin append-only audit chains.

## Data handling

- Creation is rate limited per workspace and message fields have bounded lengths.
- No uploaded file, report, reconciliation row, or attachment is stored with a ticket.
- No automatic ticket retention or deletion is configured. A retention schedule requires a separate product/privacy decision before implementation.
