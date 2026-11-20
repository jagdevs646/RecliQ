# TODOS

## Payroll check

### Recovery export for overpayments

**What:** A one-click CSV of the "paid twice" rows, laid out for a bank recall request or a clawback note.

**Why:** Finding the overpayment is half the job; recovering it is the step that stops money sitting in the wrong account for two weeks.

**Context:** Deferred from the /autoplan CEO review of docs/designs/payroll-payout-check.md (proposal E-4). Build after the payroll preset ships and at least one user has asked how to act on a "paid twice" row. The result groups and their one-line reasons are the input.

**Effort:** M (human) / S (CC)
**Priority:** P3
**Depends on:** Payroll preset shipped.

### Payroll-day reminder email

**What:** An opt-in email before each payroll cycle reminding the user to run their saved check.

**Why:** The business depends on the check becoming a habit every cycle; a reminder turns a one-off check into a recurring one.

**Context:** Deferred from the CEO review (E-5). Needs a schedule per saved check, explicit consent, and real SMTP delivery (EMAIL_BACKEND=log is local-only). Revisit when re-run rates are known from admin insights.

**Effort:** M (human) / S (CC)
**Priority:** P3
**Depends on:** Saved payroll checks; SMTP configured.

### Per-employee history across cycles

**What:** Show when the same employee was mispaid in an earlier cycle.

**Why:** Repeated errors point at a broken master record, which is worth more to a payroll lead than a single-cycle check.

**Context:** Deferred from the CEO review (E-6). Conflicts with the 14-day deletion of payroll data, so it needs a privacy design first (for example, keeping only a hashed employee key and the error type). Do not build before a paying user asks for it.

**Effort:** L (human) / M (CC)
**Priority:** P4
**Depends on:** Privacy design for retained identifiers.
