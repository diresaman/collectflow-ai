# 14 — Complete UX redesign: report

- **Date:** 2026-10-23
- **Baseline:** `6b49eb4`
- **Plan and research:** `13-complete-ux-redesign-plan.md`
- **Scope:** frontend presentation only.
  - No backend file changed; `git diff -- backend` is empty.
  - No migration, no new API, no business rule changed.
  - No live provider used and no deployment.

## 1. Research findings and references

The full table, with links, is in plan §1. In short:
- **Tabs and filters:** tabs are for distinct content; segmented controls are for views of one list (Carbon, Atlassian, NN/g).
- **Tables:** a readable first column, right-aligned amounts, a side panel for detail, one or two row actions (NN/g, Carbon).
- **Record pages:** highlights on top, tabs below, one timeline (Attio, Upflow).
- **Work queues:** prioritised items with an owner, due date and one action (Versapay, Kolleno, Linear).
- **Empty states:** say what is missing and what to do next (NN/g, Carbon, Atlassian).
- **Accessibility:** visible focus, 24 px minimum targets, no colour alone, APG tabs and modal-dialog behaviour (WCAG 2.2, WAI-ARIA APG).

**Sources:**
- **Fetched directly:** the NN/g articles, the W3C pages, Atlassian, Stripe, Linear, the Attio help page, and the Upflow, Kolleno and Versapay product pages.
- **Carbon:** read from its official GitHub source.
- **Not accessible:** plan §1.13 lists every site that could not be opened; nothing is claimed about them.

**Deliberately not adopted:**
- **Batch approve and send:** it would weaken the one-confirmed-send rule.
- **Keyboard shortcuts, snoozing, saved views and dashboard customisation:** these would be new features.

## 2. Original usability problems

Plan §2 lists them page by page, from before screenshots at 1440 and 390 px. The main ones:
- **Colour:** no design tokens; about 415 hard-coded colour classes.
- **Tabs and filters:** filters styled as dark tabs, in three styles; once (Tasks) placed in the title row far from the list.
- **Today's Work:** very tall items, heavy coloured borders, no "what kind of thing is this".
- **Invoice drawer:** one long scroll, with the reminder last and blockers buried.
- **Customers:** a narrow list next to an unstructured detail column.
- **Reports:** one long page with a badly wrapping forecast table.
- **Automation Settings:** an empty state only, although the job data existed.

## 3. Design-system decisions

| Area | Decision |
| --- | --- |
| Tokens (`app/globals.css`) | `brand` (the logo purple, Tailwind indigo values), `success`, `warning`, `danger`, `info` (blue); `canvas`, `surface`, `line`, `line-strong`, `ink`, `ink-muted`, `ink-subtle`. All 415 palette classes now use the semantic names; the neutral scale is slate |
| Type | Geist. Page title 24 px, section title 16 px, body 14 px, support text 12–13 px. Tabular numerals for amounts and counts |
| Spacing | 4 px scale. 24 px between sections, 20–24 px card padding, 32 / 24 / 16 px page padding (desktop / tablet / phone) |
| Shell | 248 px sidebar; one content width (max 1280 px) on every page; titles, cards, tabs and tables share one left edge |
| Controls | Buttons 40 px (compact 32 px); inputs 40 px with 8 px radius; cards 12 px radius with a hairline border |
| Motion | `prefers-reduced-motion` turns animation and transitions off |

## 4. Navigation changes

The sidebar groups, labels and routes are unchanged from `6b49eb4`. Changes:
- **Phone menu:** the drawer now has a visible close button; the menu button is 40 px high.
- **Sidebar user area:** an avatar initial, name and role, and a larger Sign out.
- **Customer view:** opening a customer is a real navigation (`router.push`), so the browser Back button returns to the list. `?client=` deep links still open the customer.
- **Consolidation (no feature lost):**
  - The Invoices page's "All reminders" panel is now the **All** filter on Approvals, which lists every reminder and statement with its status.
  - The Invoices page's "Run automation now" panel called the same `POST /api/automation/run/` as **Run Automation** on Automation Settings. It now lives only there, the one place that explains the automation.
  - Both old components are removed: `FollowUpHistory.tsx` and `AutomationPanel.tsx`.

## 5. Page-by-page changes

| Page | Result |
| --- | --- |
| Today's Work | Header with "Updated" time and Refresh. Four equal summary cards (each links to its group). The four groups render as sections. All eleven backend sections, their order and their rules are unchanged. Each item is one compact row: icon and kind ("Customer reply · RB-300 · Fictional Studio"), then title and reason, then date and overdue, then who can act and one action. The holds list stays folded (it repeats items above); integration problems never fold |
| Customer Replies | Master-detail (38/62). The list has search (over the loaded replies), a segmented filter, and rows with customer, invoice, preview, date and status. The detail has a header (customer and invoice, status, Open in Gmail), then three separate sections: what the customer wrote, the AI suggestion (label, confidence, rationale, suggested extracted values), and your decision. "Record promise to pay" and "Open dispute case" are separate primary actions; "Save label only" sits in a disclosure. Actions are bottom-right. **Feedback fix:** a handled reply stays open with a "Saved" notice instead of the list jumping to the next one. Phones show the list, then a full-screen reply with Back |
| Approvals | Filters: Awaiting approval, Approved (not sent), Rejected, All. A table from 1280 px: customer and drafted date; invoice, type, subject and tone; amount; approval need with reasons, whether *you* can approve, and the last editor; and Review. Stacked rows below 1280 px. The approval itself happens in the invoice drawer |
| Tasks | Filters: To do, Overdue, Assigned to me, Completed, Cancelled, All. "Overdue" narrows the open tasks; the others are the API's. A table: task, invoice and customer, owner (select), due date (overdue in red with a text label), status, and **Complete…**. That opens a row below with the note and the rule stated inline ("optional to complete, required to cancel"). Assigning and completing are visibly separate |
| Disputes | Master-detail. The detail has a summary (amount, due date, invoice status, owner), the customer's original reply, the dispute, owner and internal notes, **collection impact** (every blocker listed), and a visually distinct **Resolution** panel for approvers. It says that other holds stay in place |
| Customers | A full-width worklist table per currency from 1280 px: customer, outstanding, overdue invoices, risk (labelled "Risk n / 100", plus low confidence), main reason, next action, chasing status. Stacked rows below. **Customer view:** header (name, email, outstanding per currency, risk), then tabs Overview / Invoices / Activity / Statements. Overview has outstanding, the score breakdown and the next action on the left, and the customer, payment behaviour and cases on the right. Activity shows communications, payments seen, and the full history from the existing `/timeline/?client=` endpoint. Statements has the preview, Prepare, and past statements |
| Invoices | Header with Sync Invoices, one compact sync strip, warnings only when relevant, and three equal cards. The table has filters top-right. Columns: customer, invoice (a button), due date with days overdue, amount, status, and **collection progress as one state**: Awaiting approval, Reminder sent, Disputed, On hold, Reminders paused, or a Draft reminder button. Stacked rows below 1280 px; avatars removed |
| Invoice drawer | 640 px, full screen on phones. A fixed summary (amount, due date, payment status, reminder state) and a **chasing line**: Paid / Not in the sheet / "Chasing is stopped" with every hold and its link / Reminders held / Not due yet / Chasing allowed. Tabs: Overview (facts, collection status, the next permitted action), Reminders (schedule, current draft or email, AI suggestion, approve/send, earlier reminders), Activity (full timeline), Related (promises, disputes, tasks). It opens on Reminders when a reminder needs a person and stays there after the action. Approve uses the primary style only when you may approve; otherwise the reason is shown above the buttons |
| Reports & Insights | Header with the currency (a selector when there are several; never combined) and the period (fixed 30 days in the backend). Four summary cards: outstanding, overdue, CEI, expected in 30 days (estimate). Tabs: Overview (aging, forecast, DSO and promises, how to read the figures), Aging (bars plus a table with bucket meanings and shares), Collections (DSO and CEI in plain words with formula and inputs; payments after reminders), Forecast ("this is an estimate", weekly bars, main assumptions, contributing invoices in a proper table) |
| Reminder Schedule | A vertical, connected timeline per version: step number; day and kind; what happens; approver requirement. Viewing is the default. **Create new version** (admins) opens the form, which says that versions are immutable and enrolled invoices keep theirs |
| Automation Settings | A note that automation prepares work and people approve and send. **Scheduled processes**: plain-language title and description, backend name, status, frequency, last run, failures. Then "Run the automation by hand" with the existing workspace. "Next run" is not shown: the API does not provide it |
| Connected Services | A problems summary, then **Services** as structured rows: what the service is for; status, detail and what to do; last success and attempt, with facts under Details. **Scheduled jobs** are a separate section, so service and job health aren't confused. The risk and analytics job is listed. No credential or token is shown |
| Sign in | The primary button uses the brand colour like everywhere else |

## 6. Shared component improvements (`components/dashboard/ui.tsx`)

| Component | What it provides |
| --- | --- |
| `PageHeader` | Title, purpose line, actions top-right (full width on phones) |
| `Section` | Titled card: count, description, right-aligned filters or actions; body with or without padding |
| `StatCard` | Equal-height metric card: label, value, detail at the bottom |
| `Tabs` / `TabPanel` | WAI-ARIA tabs: roving tabindex, arrows, Home and End, underline and bold for the selected tab, horizontal scroll |
| `SegmentedFilter` | List filters as `aria-pressed` buttons in a labelled group; can fill narrow panels |
| `Notice` | Inline message with icon, title and tone |
| `EmptyState` | Title, explanation, optional action; a compact variant |
| `StatusPill` | Dot and text label; tones neutral, info, brand, good, warn, bad |
| `KeyValues` | Label/value pairs |
| `Drawer` | Header with close, fixed summary and tabs, scrolling body, optional footer. Focus moves in, is trapped, Esc closes (unless a confirmation dialog is open), focus returns, body scroll is locked |
| `buttonStyles`, `inputStyles`, `tableStyles` | One style for every button size, input, select, textarea and table part (headers, 56–64 px rows, right-aligned amounts) |

Also:
- `FollowUpBadges` now use `StatusPill`.
- `components/integrations/health.tsx` provides the status wording, job descriptions, the jobs table and the report hook, shared by Connected Services and Automation Settings.

## 7. Desktop and mobile verification

**Screenshots:** every main page plus the invoice drawer and the customer view (13 views) at 1440, 1280, 1024, 768, 390 and 320 px. That is 78 screenshots on a production build with fake providers and the seeded `collectflow_e2e` data (approver account), plus 13 at 1440 px as an operator.

**Overflow:** an automated check found **no horizontal page overflow at any width**.

**Fixed during verification:**
- A screen-reader label in tables pushed the page 33 px wide.
- The Approvals table and the invoice table's last column were clipped at 1280 and 1024 px. They now switch to stacked rows below 1280 px, and the Approvals table was reduced to the essential columns.
- Narrow list filters ("All" clipped).
- Sidebar labels truncated at 240 px (the sidebar is now 248 px).
- Summary-card labels wrapping and misaligning values.
- A nested button inside the phone invoice card.
- The invoice drawer's tab flipping after a send.

## 8. Accessibility checks

- **Navigation:** landmarks and named sidebar groups; one `h1` per page; `aria-current` on the active item.
- **Tabs:** follow the APG tabs pattern; a browser test covers the arrow and End keys.
- **Filters:** `aria-pressed`, with a browser test.
- **Drawer and phone menu:** labelled dialogs; focus moves in, is trapped and is restored; Esc closes (browser test).
- **Focus and targets:** visible focus rings on all interactive elements; targets are 32–40 px.
- **Statuses:** every status has a text label (Overdue, Disputed, Paid, …), never colour alone; overdue dates also say "Overdue".
- **Forms:** inputs have visible labels; required notes are stated in the label.
- **Reduced motion:** respected.

**Not done:** no automated contrast audit (such as axe) was run. Text uses the 600–900 shades on white or 50-shade backgrounds, which normally meets AA, but this is not measured.

## 9. Screenshots

In `docs/v2/ux/redesign/`:
- `after-<page>-1440.png`: all 13 views;
- `after-<page>-390.png`: Today's Work, Customer Replies, Approvals, Invoices, the invoice drawer and the customer view;
- `after-operator-invoice-detail-1440.png`: the restricted-approval state;
- `before-<page>-1440.png`: for comparison.

## 10. Automated test results

| Check | Result |
| --- | --- |
| `npm run typecheck` | PASS |
| `npm run lint` | PASS |
| Production build (`next build`, run by the suite and for the screenshots) | PASS |
| Playwright, full suite, fake providers | **28 passed** (two consecutive full runs) |

**Updated tests** (selectors only; no assertion about safety was weakened):
- the customer and statement journeys open the customer view and its Statements tab;
- the Approvals and Tasks journeys use table rows and the "Review" and "Complete…" actions;
- the reply journey uses the filter button.

**New tests:**
- report tabs keyboard behaviour;
- the invoice drawer opens on the tab that needs action, and Esc closes it;
- filters are pressed buttons;
- Back from a customer returns to the list.

**Backend tests:** not run, because no backend file changed.

## 11. Known remaining UX issues

- **Backend wording:** some text from the API is still technical. Today items quote job names such as "Sequence tick" and say "Sequence step 2 held"; the frontend maps only the titles it owns.
- **Search:** only Customer Replies has search (over the loaded list). Invoices and Approvals have filters only: the API has no search.
- **Reports:** no period selector (the backend computes a fixed 30 days); bars, not charts.
- **Automation Settings:** no "next run" time, because the API does not expose it.
- **Drawer:** it is modal, so the table behind it cannot be used while it is open.
- **Phones:** the risk-factor table is dense at 320–390 px.
- **Contrast:** not measured with an automated tool (§8).
- **Generated file:** `next dev` created `frontend/AGENTS.md`. It is not part of this work and is not committed.

## 12. Business and safety behaviour

Unchanged:
- **API calls:** every write uses the same API functions as before. The only new read is `GET /timeline/?client=` (an existing endpoint, already allowed by the proxy).
- **Approval:** the four-eyes rules, approver-only actions and the self-approval refusal are still decided by the backend. The UI only presents them; Approve stays disabled exactly when it was before.
- **Sending:** still a separate, confirmed step in the unchanged `DeliveryPanel`. Delivery-unknown is never offered for resending (browser test).
- **Holds and disputes:** they still block chasing. Promises, disputes and payment claims still need an approver to release. Emails never mark invoices paid.
- **Formulas:** no financial formula, risk calculation, forecast parameter or sequence behaviour changed.
- **No side effects:** no production data, migration, deployment, live Gemini call or email.
