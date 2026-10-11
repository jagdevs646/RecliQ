---
version: alpha
name: RecliQ
description: "A finance reconciliation workspace with a teal-led operational interface for accountants and platform administrators."
colors:
  primary: "#087d72"
  primary-hover: "#056b62"
  background: "#f4f7f7"
  surface: "#ffffff"
  text: "#1e3037"
  muted-text: "#56676d"
  border: "#d9e4e4"
  success: "#08734f"
  warning: "#97610a"
  danger: "#a8413a"
typography:
  sans:
    fontFamily: '"Inter Variable", Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    fontSize: "14px"
    lineHeight: "1.45"
  mono:
    fontFamily: "ui-monospace, SFMono-Regular, Consolas, monospace"
rounded:
  control: "6px"
  panel: "7px"
  prominent: "8px"
spacing:
  page-padding: "32px"
  panel-padding: "15px"
  control-gap: "8px"
  sidebar-width: "244px"
components:
  button:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.surface}"
    rounded: "{rounded.control}"
    height: "40px"
    padding: "10px"
  button-hover:
    backgroundColor: "{colors.primary-hover}"
    textColor: "{colors.surface}"
    rounded: "{rounded.control}"
  input:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    rounded: "{rounded.control}"
    height: "40px"
    padding: "10px"
  admin-panel:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    rounded: "{rounded.panel}"
  admin-panel-muted:
    backgroundColor: "{colors.background}"
    textColor: "{colors.muted-text}"
    rounded: "{rounded.panel}"
  divider:
    backgroundColor: "{colors.border}"
    height: "1px"
  success-status:
    backgroundColor: "{colors.success}"
    textColor: "{colors.surface}"
    rounded: "{rounded.control}"
  warning-status:
    backgroundColor: "{colors.warning}"
    textColor: "{colors.surface}"
    rounded: "{rounded.control}"
  danger-status:
    backgroundColor: "{colors.danger}"
    textColor: "{colors.surface}"
    rounded: "{rounded.control}"
---

## Overview

### Creative North Star

RecliQ should feel like a well-run accounting operations desk: source sheets, reconciliation exceptions, and operational status are the subject matter, while the UI stays quiet enough for repeated daily work. The signature is a precise teal thread that connects reconciliation actions and status, not a decorative hero or an oversized metric wall.

### Product context and register

- **Audience and primary job:** Accountants and finance operators reconcile workbooks; one Super Admin maintains platform access and operations.
- **Target market(s) and evidence:** Global English-first accounting teams; product and API terminology is English. No country-specific legal or market claim is encoded in this UI.
- **Locale(s) and language policy:** English UI; timestamps follow the viewer's local timezone, with backend instants stored in UTC.
- **Usage scene:** Desktop-first repeated office work, with responsive access on tablets and phones.
- **Register:** Product interface on all application routes. The Super Admin console is a dedicated operations environment, not a marketing page or a personal reconciliation workspace.
- **Memorable signature:** Teal is reserved for the active operation, selected route, and completed reconciliation state.
- **Restraint:** Dense lists remain legible; administrative panels and tables prioritize scannability over decoration.
- **Anti-references:** No giant SaaS hero, nested-card dashboard, ornamental gradient/orb, or colorful status without textual meaning.
- **Token ownership/runtime mapping:** Existing CSS variables in `frontend/src/styles.css` remain canonical (Model B). This file records their accepted values; semantic CSS variables feed shared controls and pages. Verify with `designmd lint DESIGN.md` and compare tokens against the root/dark theme blocks.

## Colors

The light theme uses a cool near-white canvas, white work surfaces, restrained blue-green borders, and dark blue-gray text. `primary` and `primary-hover` represent actions and selection only. Success, warning, and danger use distinct semantic colors and always include labels or icons. Dark mode remaps the same roles in `:root[data-theme="dark"]`; never copy the light hex values into feature-level styles. Charts use the existing matched/differ/directional palette in `frontend/src/styles.css`.

## Typography

Inter Variable is the application sans family. The 14px body and 13px utility scale support long work sessions; headings use the existing 18px, 22px, and 28px steps. Use tabular numerals for counts where comparison matters. Identifiers and technical values may use the system monospace stack. All UI copy is English; use flexible wrapping and do not rely on letter spacing for hierarchy.

## Layout

Desktop uses a 244px persistent sidebar and a centered page up to 1350px. The page uses 32px padding on wide viewports and tighter spacing at smaller breakpoints. Admin screens use compact, aligned metric groups and server-paginated tables. Tables own their horizontal scrolling; long forms and detail panels keep normal document scrolling. Keep navigation, filter, and primary action geometry stable as data loads.

## Elevation & Depth

Hierarchy comes from surfaces, thin borders, and spacing. Static panels use no more than the existing subtle one-pixel shadow. Modal detail dialogs dim and softly blur the workspace behind them; reserve this effect for focused overlays, not static panels. Avoid glass, sticky overlays over important content, and shadows as decoration. Dark mode relies on surface contrast rather than a light-theme shadow.

## Shapes

Use 6px controls, 7px admin panels and buttons, and 8px workflow surfaces, matching the current CSS. Badges can use compact corners; a pill shape is reserved for workflow status where it already exists. Dividers are one-pixel semantic borders.

## Components

### Foundational visual states

Controls use visible focus rings, hover, pressed, selected, disabled, and busy states. Invalid input includes a text message and border state. Initial data loads use the existing stable app loading treatment; no skeleton system is selected.

### Buttons and actions

The primary button is teal with white text. Secondary actions are bordered neutral controls. Destructive and security-sensitive actions use the danger intent and are separated from routine actions. Busy labels do not change button dimensions.

### Navigation and data display

Sidebar navigation is persistent at desktop widths and horizontally scrollable on narrow screens. The admin navigation is role-specific. Read-oriented datasets use semantic tables, sticky headers only inside their table surface, explicit totals and pagination, and full-value access for truncated content.

### Forms and overlays

Inputs use 40px minimum height and visible labels. App-owned forms disable native validation bubbles and show associated inline errors. Modal operations use native modal dialog behavior or the shared app dialog and retain keyboard focus. Account and complaint details open in centered dialogs with an explicit close action and an internally scrollable body. Support messages explicitly warn users not to include uploaded financial documents or secrets.

### Iconography

Use `lucide-react` outline icons at 16–19px for compact controls. High-impact actions retain text labels; icon-only actions require accessible names.

### Motion

Existing controls use short 180ms color/border transitions. Motion communicates a state change only; honor `prefers-reduced-motion` and avoid staggered metric animations.

### Content and data visualization

Use direct labels such as “Reconciliations,” “Unread complaints,” and “Storage used.” Distinguish lifetime from selected-period counts. Charts must derive from database records; show an honest empty state when data is absent and provide a textual summary.

## Do's and Don'ts

- **Do:** Keep file names, statuses, and user-visible outcomes explicit.
- **Do:** Keep admin access, account ownership, and support messages enforced by backend scope and authorization.
- **Don't:** Present simulated usage as platform analytics or expose source workbooks in admin tools.
- **Don't:** Hide navigation overflow, focus state, or the full content of important identifiers.
