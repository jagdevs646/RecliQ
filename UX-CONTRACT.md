# UX Contract

## Product context

- Audience: finance operators using guest or registered workspaces, plus one or more authorized Super Admins.
- Primary jobs: reconcile files, review results/history, manage account preferences, submit support requests, and administer the platform.
- Target market(s): global English-first product; no market-specific legal behavior is represented.
- Active locales: English.
- Language/content register: direct operational language; use action verbs and explain recovery.
- Timezone/calendar policy: store timestamps in UTC; display in the viewer's local timezone.
- Accessibility target: WCAG 2.2 AA baseline.

## Business-context sources

| Domain / scope | Authoritative source | Source type | Reviewed date |
|---|---|---|---|
| Permission model | `docs/operations-and-security.md`; backend `require_admin` dependency | Permission policy and verified API behavior | 2026-10-10 |
| Data lifecycle | `docs/api.md`; `docs/operations-and-security.md` | API and operations contract | 2026-10-10 |
| Support lifecycle and notification | `docs/support-policy.md` | Product/domain contract added for this request | 2026-10-10 |
| Deletion / retention | No support-ticket retention policy currently exists; ticket storage is non-destructive and has no automated cleanup | Unresolved policy input | 2026-10-10 |
| Billing / payment | Not applicable; no billing capability exists in this application | Product scope | 2026-10-10 |
| Legal / regulatory copy | No legal copy is introduced by this UI | Product scope | 2026-10-10 |

## Visual contract

- Project `DESIGN.md`: this file's sibling `DESIGN.md`.
- Token ownership model: existing runtime CSS remains canonical; DESIGN.md mirrors accepted values.
- Runtime token source: `frontend/src/styles.css` root and dark-theme variables.
- Mapping: semantic `--color-*`, `--font-*`, and component CSS selectors feed the frontend. No duplicate theme provider.
- Token drift gate: `designmd lint DESIGN.md`, followed by browser computed-style inspection.
- Supported themes: light, dark, and system.

## Canonical UI Map

| Capability | Canonical owner | Source of truth | Allowed variants | Verification |
|---|---|---|---|---|
| Table Selection | Native semantic table; no bulk selection in this console | This contract | Read-only rows / detail action | Keyboard and browser check |
| Select/Listbox | Native select for compact filters and short workflow option lists; platform popup is accepted on supported browsers | This contract | Native | Keyboard and popup browser check |
| Date | Native date input for custom admin and history ranges; browser-owned picker is accepted on supported browsers | This contract | Native | Browser and validation check |
| Form | Page-owned form with `noValidate`, inline errors, and persistent entered values | This contract | Create / reply / admin update | API integration and browser flow |
| Scrollbar | Global application stylesheet | `DESIGN.md`, CSS tokens | Stable gutter only when needed | Computed style and browser inspection |
| Toast | Existing inline status/alert regions; no separate toast provider is present | Existing sibling UI | Success / error / info | Live-region browser check |
| CRUD | FastAPI-scoped services and route-specific mutation feedback | `docs/api.md`, `docs/support-policy.md` | Create returns to ticket list; reply stays in detail | API integration tests |

## Component behavior

| Component | Default | Hover | Focus | Active | Disabled | Busy | Error |
|---|---|---|---|---|---|---|---|
| Button | semantic label | subtle background/border | visible outline | pressed | dim, inert | stable label/size | inline or page alert |
| Input | labeled | border emphasis | visible ring | n/a | muted | preserved | `aria-invalid` and associated text |
| Search | labeled text field with immediate clear | border emphasis | visible ring | n/a | muted | keep table frame | keep query and show inline failure |
| Textarea | labeled, auto-growing to 14 rows, then internally scrollable | border emphasis | visible ring | n/a | muted | preserve text | inline field message |
| Table/list | explicit total and page | row tint | visible focus on actions | selected row | n/a | stable header/frame | retry region |

## Dataset navigation

- Admin tables use server pagination; default page size is 25, maximum 100.
- Search and filters are debounced 300ms, IME-safe, and stale responses cannot replace newer results.
- Complaint lists preserve filters and reset to page one when filters change. No bulk selection is offered.
- Empty, no-results, loading, and request-error states remain inside the list panel.
- Search state is transient and is not put in the URL because complaint search may contain personal account identifiers.

## Flow ledger

| Operation | Trigger | Pending | Success destination | Success feedback | Failure recovery | Focus outcome | Source ref |
|---|---|---|---|---|---|---|---|
| Create support request | `Send to RecliQ` | Disable duplicate submit, stable button | My support list and selected ticket | “Support request sent” | Keep form values and show field/global error | Ticket heading | `docs/support-policy.md` |
| Reply to support request | `Send reply` | Disable duplicate submit | Same conversation | “Reply sent” | Keep message text and allow retry | New message/status | `docs/support-policy.md` |
| Triage complaint | Status/priority control and `Save changes` | Pessimistic request; preserve draft | Same complaint detail | “Complaint updated” | Preserve selected values and allow retry | Detail heading | `docs/support-policy.md` |
| Recover password locally | Forgot-password form or reset link from verification dialog | Preserve email and show pending state | Reset form from returned development link | “Local password reset link ready. No email was sent.” | Show request error without revealing account existence in production | Reset form heading | `backend/app/api/routes/auth.py`, `frontend/src/components/AuthDialog.tsx` |
| Change platform sender | Admin email management form | Disable duplicate save; keep draft on error | Same settings panel | “Sender address saved.” | Preserve draft and show actionable error | Sender email field | `backend/app/api/routes/admin.py` |
| Search complaints/users | Search field | Debounced server query | Same route and page one | Result count | Keep query; show retry | Search field | This contract |
| Account security action | Named action | App-owned modal confirmation | Same account detail | Server-confirmed status | Keep modal open on failure | Modal or initiating action | `docs/operations-and-security.md` |

## Navigation and responsive behavior

- Route title policy: `{Section} — RecliQ`; update after hash navigation.
- Admin routes require active Super Admin and MFA both in frontend routing and backend APIs. Unauthorized direct admin navigation returns the user to their allowed dashboard with an access notice.
- A Super Admin opens the dedicated admin dashboard after authentication; ordinary reconciliation routes are not part of the admin sidebar. Account settings and sign-out remain available.
- Mobile navigation is a visible horizontal scroller; it does not hide overflow affordances.
- Tables scroll horizontally within their own panels. Account and complaint details open in modal dialogs from their respective lists; dialogs retain native focus containment, Escape dismissal, focus restoration, a visible close button, and a scrollable body.
- Important values wrap or offer keyboard-accessible detail; no hover-only content.

## Overlays and feedback

- Browser `alert`, `confirm`, and `prompt` are prohibited. Use a semantic modal dialog with native modal focus containment or a maintained app dialog.
- Account and complaint detail dialogs open only after the corresponding row or request is activated. Their backdrops dim and softly blur the page; closing a dialog preserves the list and current detail drafts.
- Account status, session, and policy changes require explicit object-specific confirmation and an audit reason where the API supports one.
- Support submission and admin replies are in-app only. Creating a complaint never triggers email.
- Success and errors are announced using existing `role=status` / `role=alert` inline messages; failure details remain visible for correction.

## Async and resilience

- Mutations are pessimistic; no success is announced before API confirmation.
- Duplicate submissions are blocked while the request is pending.
- Requests are workspace-scoped and server authorization is authoritative. On 401/403, keep entered non-sensitive text and show an actionable message.
- Search results use request cancellation or stale-result guards.
- No offline queue is promised; users may retry after connectivity returns.

## Validation

- Use Pydantic validation on the server and associated inline frontend errors.
- Forms use `noValidate`; first invalid field receives focus.
- Complaint subject, category, workflow stage, impact, and description are required. “Other” requires a custom area. Expected behavior and reproduction steps are optional.
- Complaint bodies are capped; no workbook or other file attachments are accepted. Remind users not to include workbook contents, passwords, or secrets.

## Permission and clipboard

- Admin sections are hidden from normal-user navigation; direct access is handled by a clear application redirect. Backend admin APIs require active MFA-enabled Super Admin.
- User support endpoints filter by server-resolved workspace scope. A ticket ID cannot grant access to another account/session.
- Admin complaint views expose only the support request and account identity needed for follow-up, never source files or reports.
- Admin list/detail access and all triage/reply mutations are audit logged without recording complaint message bodies in audit metadata.

## Verification

- Required checks: backend pytest suite; frontend typecheck, tests, and production build; Premium strict audit; design token lint; support full-flow browser check.
- Browser matrix: desktop, narrow viewport, light/dark theme, keyboard form flow, submit error and success.
- Canonical sibling flow: existing upload and account settings forms for labels, inline status, and theme.
