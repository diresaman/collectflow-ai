# 13 — Complete UX redesign: research and plan

- **Date:** 2026-10-23
- **Baseline:** `6b49eb4` (navigation and terminology refinement)
- **Scope:** presentation only.
  - No new business feature, state, rule, formula or API.
  - No change to approval, delivery, hold or payment authority.
  - No backend change, migration or deployment.
- **Status:** implemented. Results, screenshots and test outcomes are in `14-complete-ux-redesign-report.md`.

## 1. Research findings

The research was run from public sources on 2026-10-23.
- **Fetched directly:**
  - NN/g articles;
  - the WAI-ARIA Authoring Practices and WCAG 2.2 Understanding documents;
  - the Carbon Design System guidance, read from its official GitHub source because carbondesignsystem.com would not load;
  - Atlassian Design System pages;
  - Stripe Dashboard and Currencies docs;
  - Linear docs;
  - Attio's record-page help;
  - the Upflow, Kolleno and Versapay product and help pages.
- **Seen in search results only:** a few sources, marked *(search only)*.
- **Not accessible, so not reviewed:** listed in §1.13. No claim is made about them.

| # | Topic | Finding | Applied to CollectFlow |
| --- | --- | --- | --- |
| 1 | Sidebar IA | Keep a left vertical nav visible on desktop. Labels start with their keyword; text, not icons alone; less-used items at the bottom ([NN/g vertical nav](https://www.nngroup.com/articles/vertical-nav/)). Stripe groups a primary block, then product areas, then settings ([Stripe dashboard basics](https://docs.stripe.com/dashboard/basics)) | Keep the four groups: daily work first, settings last. Labels lead with the keyword, numeric counts sit on the right, and a 240 px sidebar is always visible on desktop |
| 2 | Dashboard hierarchy | One dashboard, one job. Operational dashboards (time-sensitive) differ from analytical ones. Encode by position and length (bars), not pies or gauges; never colour alone ([NN/g dashboards](https://www.nngroup.com/articles/dashboards-preattentive/)) | Today's Work is operational: a work queue with a small summary, no charts. Reports are analytical: bars only, labelled values |
| 3 | Work queues | Prioritised to-do lists per collector ([Versapay](https://www.versapay.com/collections/)). Each task has an owner, due date and status, with unassigned and done views ([Kolleno tasks](https://help.kolleno.com/en/collections/18646823-finding-managing-tasks)). Triage is decided item by item ([Linear triage](https://linear.app/docs/triage)) | Each Today item shows what it is, which record, why, when, who can act, and one labelled action. Tasks list owner, due date and status |
| 4 | Tables and filters | The first column is a readable identifier, and columns run by importance. Open row detail in a side panel. Keep one or two labelled inline actions ([NN/g data tables](https://www.nngroup.com/articles/data-tables/)). Carbon's table anatomy: title, toolbar, header, rows, then pagination or count ([Carbon data table](https://github.com/carbon-design-system/carbon-website/blob/main/src/pages/components/data-table/usage.mdx)). Show active filters clearly ([Carbon filtering](https://github.com/carbon-design-system/carbon-website/blob/main/src/pages/patterns/filtering/index.mdx)) | One table style: title and count with filters top-right, sentence-case headers, amounts right-aligned, the identifier as a link, and a side drawer for detail. At most one action per row |
| 5 | Detail pages | Attio record pages put key attributes on top and tabs below (Overview, Activity, …) ([Attio](https://attio.com/help/reference/managing-your-data/records/configure-record-pages)). Upflow keeps one timeline per customer ([Upflow](https://upflow.io/automated-collections-software)) | Invoice and customer detail: a persistent summary (identity, amount, status, whether chasing is allowed) with tabs below. The Activity tab reads the existing timeline API |
| 6 | Tabs and filters | Tabs are for distinct content. A content switcher (segmented control) shows alternate views of the same content ([Carbon content switcher](https://github.com/carbon-design-system/carbon-website/blob/main/src/pages/components/content-switcher/usage.mdx)). Atlassian: don't use tabs to filter one table; content common to all tabs goes above them ([Atlassian tabs](https://atlassian.design/components/tabs/usage)). NN/g: few tabs, the most-used first, two selected indicators ([NN/g tabs](https://www.nngroup.com/articles/tabs-used-right/)) | Two components. **Tabs** (underline plus bold, ARIA tabs) for record and report sections. **SegmentedFilter** (pressed buttons) for list filters. Every list filter becomes a SegmentedFilter |
| 7 | Financial data | `tabular-nums` aligns digits ([MDN](https://developer.mozilla.org/en-US/docs/Web/CSS/font-variant-numeric)). Right-align amounts, left-align identifiers and dates *(search only: Spiris)*. Format by currency code ([Stripe currencies](https://docs.stripe.com/currencies)) | Amounts right-aligned with tabular numerals and formatted per currency (`Intl`). Totals are never added across currencies; each currency is labelled |
| 8 | Approvals | AI as a copilot that drafts while a person acts ([Kolleno](https://www.kolleno.com/)). Present AI matches as suggestions to review ([Upflow](https://upflow.io/automated-collections-software)). Confirmations only for consequential actions, buttons labelled by outcome, no default "Yes" ([NN/g confirmation dialogs](https://www.nngroup.com/articles/confirmation-dialog/)) | AI output is labelled "AI suggestion". Approve never reads as Send. The send confirmation names the recipient. No batch approve or send: CollectFlow sends one confirmed email at a time by design |
| 9 | Forms | Single column, labels close to fields, no placeholder-as-label, required fields marked, specific errors that keep input ([NN/g form design](https://www.nngroup.com/articles/web-form-design/)). Progressive disclosure at most 2 levels deep ([NN/g](https://www.nngroup.com/articles/progressive-disclosure/)) | Visible labels on drawer forms. Required notes are stated inline. Secondary actions sit behind one disclosure |
| 10 | Empty states and feedback | Never show "no records" while loading; say what will appear and how to get there ([NN/g empty states](https://www.nngroup.com/articles/empty-state-interface-design/)). Carbon: no data, user action (all done) and error types, each with one action ([Carbon empty states](https://github.com/carbon-design-system/carbon-website/blob/main/src/pages/patterns/empty-states-pattern/index.mdx)). Atlassian: a heading and one action ([Atlassian empty state](https://atlassian.design/components/empty-state/usage)). Errors are not transient toasts ([NN/g](https://www.nngroup.com/articles/indicators-validations-notifications/)) | EmptyState gives a title, whether it is normal, and the next step. Loading uses skeletons. Results stay inline next to the record, as today |
| 11 | Responsive | Design the desktop table, then reduce it for mobile; signal horizontal scroll ([NN/g mobile tables](https://www.nngroup.com/articles/mobile-tables/)). One spacing scale ([Carbon spacing](https://github.com/carbon-design-system/carbon-website/blob/main/src/pages/elements/spacing/overview.mdx), [Atlassian spacing](https://atlassian.design/foundations/spacing)) | Lists become stacked rows on phones; tabs scroll horizontally; drawers go full screen. A 4 px scale throughout |
| 12 | Accessibility | Focus visible (2.4.7) and not obscured (2.4.11); targets at least 24 × 24 px (2.5.8); don't rely on colour (1.4.1) ([WCAG 2.2](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html)). APG tabs: arrow keys and roving tabindex ([APG tabs](https://www.w3.org/WAI/ARIA/apg/patterns/tabs/)). APG modal dialog: focus moves in, is trapped, Esc closes, focus returns ([APG dialog](https://www.w3.org/WAI/ARIA/apg/patterns/dialog-modal/)). Status indicators combine colour, shape and label ([Carbon status](https://github.com/carbon-design-system/carbon-website/blob/main/src/pages/patterns/status-indicator-pattern/index.mdx)) | Focus rings on every control. Buttons are 32–40 px high. Tabs follow APG. The drawer traps focus and restores it. Every status pill has a dot and a text label. Reduced motion is respected |

**Recommendations deliberately not adopted:**
- **Batch approve and send** (Versapay, NN/g tables): it would weaken the one-confirmed-send-at-a-time guarantee.
- **New keyboard shortcuts, snoozing, saved views and URL filter state:** these are new features. Only the existing j/k reply navigation stays.
- **Customising the dashboard:** a new feature.

### 1.13 Sources not accessible or not reviewed

The following are cited from search results only, or not at all:
- carbondesignsystem.com (blocked; the GitHub source was used instead);
- the NN/g segmented-control and list-detail articles (not found);
- the Atlassian page-header page (blocked);
- upflow.io/product and docs.upflow.io (404 or blocked);
- versapay.com/collaborative-accounts-receivable (404);
- the Notion help pages (404; search results only);
- the Kolleno "query" article (404);
- the GOV.UK table and Stripe Apps design pages (the connection failed).

## 2. Page inventory (current state, before)

Each page was inspected in the browser at 1440 px and 390 px with fake providers, the seeded `collectflow_e2e` data and an approver account. The screenshots are kept for the report.

| Route | Page | Main problems found |
| --- | --- | --- |
| `/today` | Today's Work | Heavy coloured left borders and four coloured tiles. Every item is tall: title, ids, detail, "Who can act", each on its own line. Items don't say what kind of record they are. "Updated" sits at the very bottom |
| `/inbox` | Customer Replies | The filter is styled as dark tabs (black pill). The decision area mixes radio buttons, the promise form and "save the label only". The review action is not at the bottom-right |
| `/approvals` | Approvals | Black-pill filter; no columns. Customer, amount, tone and the reason an approver is needed all run as text. The stale-statement warning looks like body text |
| `/tasks` | Tasks | Filters in the title row, far from the list. Every task renders an always-open completion form (note, two buttons, help text), which makes it very tall. Owner and due date are hard to scan |
| `/disputes` | Disputes | Black-pill filter. Detail sections (impact, owner, notes, resolution) have no clear hierarchy. The resolution form blends into notes |
| `/clients` | Customers | A narrow ranked list with dense multi-line rows, beside a long unstructured detail column (risk, invoices, communications, statement, promises). No tabs. The urgent banner is loud |
| `/invoices` | Invoices | Sync strip, stale warning, AI banner, approvals banner and three cards before the table. The reminders column has two lines of tiny coloured text and draft buttons. Avatars add noise. Two collapsed panels sit at the bottom |
| drawer | Invoice detail | 576 px wide. One long scroll: facts, schedule steps, a history-review form, pause form, promise, dispute and task forms, then the reminder at the bottom. The header says "AI follow-up". Blockers appear only inside the case section |
| `/analytics` | Reports & Insights | One long page of four cards. The forecast table wraps badly (invoice and customer split over three lines). DSO and CEI explanations are long |
| `/sequences` | Reminder Schedule | Four small boxes on a coloured dot line. Readable, but the steps don't read as a sequence or timeline |
| `/automation` | Automation Settings | Shows only "No automation workflow yet". Nothing says which background processes run, how often, or that they never send |
| `/settings/integrations` | Connected Services | Good structure. "What to do" boxes with environment variable names. Job health is a plain table without descriptions |

**Problems across pages:**
1. **Colour:** about 415 hard-coded palette classes in 9 colour families; no semantic tokens.
2. **Filters:** three filter styles (black pills, grey segments, the Tasks title row) and two tab styles. None uses the ARIA tabs pattern correctly; filters are marked as tabs.
3. **Page width:** set by the shell (1152 px max), so wide tables wrap.
4. **Headers:** align their actions to the bottom (`items-end`), so buttons float next to the description.
5. **Empty states:** differ per page, and some say only "No … here".
6. **Lists:** cards, lists and tables each use different paddings (`px-4`, `px-5`, `p-4`, `p-5`).
7. **Details:** collapsible sections hide important detail behind small summary text.

## 3. Proposed design system

| Area | Decision |
| --- | --- |
| Colour tokens (`app/globals.css`) | `brand` (the logo's purple, Tailwind indigo values); `success`, `warning`, `danger` and `info` (blue) for states; `canvas` for the page, `surface` for cards, `line` and `line-strong` for borders, `ink`, `ink-muted` and `ink-subtle` for text. The neutral scale stays slate. Components use only these names |
| Typography | Geist. Page title 24 px semibold; section title 16 px semibold; body 14 px; supporting text 12–13 px. Tabular numerals for amounts and counts |
| Spacing | 4 px scale: 8 px between related items, 16 px inside components, 20–24 px card padding, 24 px between sections, 32 px desktop page padding (24 px tablet, 16 px phone) |
| Shell | 240 px sidebar. One content width for every page (max 1280 px), so titles, cards, tabs and tables share one left edge |
| Controls | Buttons 40 px high, compact 32 px; inputs 40 px with 8 px radius; cards 12 px radius with a 1 px border and a hairline shadow |
| Shared components (`components/dashboard/ui.tsx`) | **PageHeader:** title and purpose left, primary action top-right, full width on phones. **Section:** titled card, filters or actions on the right. **StatCard:** equal height, aligned label, value and detail. **Tabs and TabPanel:** ARIA tabs with arrow keys, underline, horizontal scroll. **SegmentedFilter:** `aria-pressed` buttons. **StatusPill:** dot plus label. **Notice:** icon plus title, tone by state. **EmptyState:** title, explanation, optional action. **KeyValues.** **Drawer:** header, fixed summary and tabs, scrolling body, fixed footer, focus trap and restore, full screen on phones. **tableStyles, buttonStyles, inputStyles** |
| Statuses | Existing labels from `lib/status.ts` only. Each shown as dot plus text, readable without colour |

## 4. Page plan

The plan follows the specification's page sections; the detail is in the report. In brief:
- **Today's Work:** a summary of equal cards linking to four groups; compact rows (kind and record · reason · date · who can act · action). The holds list stays folded because it repeats items above. Integration problems are never folded.
- **Customer Replies:** master-detail, about 38/62. The list has preview, date and state. The detail has header and status, then "What the customer wrote", "AI suggestion", "Your decision". A promise is recorded as its own primary action, distinct from saving a label. Actions at the bottom-right.
- **Approvals:** filters "Awaiting approval" and "Approved, not sent" (the only states this queue holds; rejected drafts have no queue in the API). A table: customer, invoice or statement, type, amount, tone, approval need, status, Review. The detail opens the invoice drawer on its Reminders tab.
- **Tasks:** filters To do, Assigned to me, Completed, Cancelled, All (the supported filters; overdue is shown per row). A table: task, invoice and customer, owner, due, status, action. Completion and cancellation open in a row expansion with the note rule inline.
- **Disputes:** master-detail. Sections: summary and status, original reply, reason, owner and notes, collection impact (all blockers), and a distinct resolution panel for approvers.
- **Customers:** a full-width worklist table per currency: customer, outstanding, overdue invoices, risk, main reason, next action, blocker. The detail becomes a customer view with a header and the tabs Overview, Invoices, Activity, Statements.
- **Invoices:** header with Sync, one compact sync strip, three equal cards, then the table (filters top-right; columns customer, invoice, due date, amount, status, collection progress as one clear state). The detail is a 640 px drawer: a persistent summary, a "chasing allowed?" line and blockers, then the tabs Overview, Reminders, Activity, Related.
- **Reports & Insights:** a summary strip per currency, then the tabs Overview, Aging, Collections, Forecast. Plain-language explanations; the forecast is labelled an estimate, with its assumptions.
- **Reminder Schedule:** a vertical connected timeline per version: step, timing, tone or task, approval need, invoices at the step. The read-only explanation and the admin-only new-version form stay as today.
- **Automation Settings:** the scheduled processes (from the integrations report): what each does, status, how often, last run, failures. A statement that scheduled automation never approves or sends. The manual automation run below.
- **Connected Services:** service cards (identity, status, last activity, what to do). Background-job health shown separately below.

## 5. Implementation sequence

| Phase | Work | Check |
| --- | --- | --- |
| A Foundation | Tokens, typography, shell, shared components (§3) | Type-check; screenshots of every page (no regressions) |
| B Daily work | Today's Work, Customer Replies, Approvals, Tasks, Disputes | Screenshots at 1440 and 390 px; fix alignment before continuing |
| C Financial records | Customers and customer detail, Invoices and invoice drawer | Screenshots; deep links (`?invoice=`, `?client=`) |
| D Insights and configuration | Reports & Insights, Reminder Schedule, Automation Settings, Connected Services | Screenshots |
| E Polish | Navigation, deep links, responsive widths (1440, 1280, 1024, 768, 390, 320), accessibility, final visual pass | Type-check, lint, build, full Playwright suite with updated and new tests |

**Constraints kept throughout:** existing APIs only, no change to any safety rule, no live provider call, and the backend untouched.
