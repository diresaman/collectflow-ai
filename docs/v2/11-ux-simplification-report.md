# 11 — UX simplification and usability report

- **Date:** 2026-10-23
- **Baseline:** `9109bad` (M14)
- **Scope:** usability only. No new business capability, state, rule, AI behaviour or integration.
- **Status:** done on the development stack; **not deployed**.

| Gate | Status |
| --- | --- |
| M13 production deployment | PENDING |
| M14 production deployment | PENDING |
| M12.1 live-provider acceptance | PENDING |
| Full isolated regression suite and browser test suite | Deferred to M15 |

## 1. How it was reviewed

The app was run locally and used in a real browser (headless Chromium driven by Playwright), not only
read as code: backend on port 8001 and web app on 3001, against a **throwaway copy of the development
database** (`collectflow_ux`), seeded with fictional first-time-user scenarios. Gmail, Gemini and Sheets
were switched off through environment overrides, so no provider was contacted. Every main page was
captured at desktop (1366 px) and phone (390 px) width before and after; examples are in `docs/v2/ux/`.

## 2. Highest-impact problems found (before)

| # | Problem | Effect on a new user |
| --- | --- | --- |
| 1 | Today showed 11 equally weighted sections, every item with a generic **Open** button, and integration noise first | No sense of what matters most or what clicking does |
| 2 | Ten top-level menu items wrapping onto two lines | Hard to find anything; configuration mixed with daily work |
| 3 | A red **"API Unavailable"** badge in the header on a working app (the check bypassed the app's proxy) | Alarms users for nothing; technical wording |
| 4 | Reply review showed all nine extracted fields for every label; "Confirm Promise to pay" saved only the label and created no promise (seen in the M12.1 live test) | Users think the promise is recorded when it is not |
| 5 | Reminder status words differed per screen ("Generated Draft" meant "awaiting approval") | Unclear whether an email went out |
| 6 | The Invoices page stacked six panels (summary, automation, approvals, tasks, table, history) | Dense; tasks duplicated Today and Tasks |
| 7 | Buttons offered actions the backend would refuse (e.g. "Draft step 1" on a disputed invoice; "Check integration" to operators who cannot fix it) | Errors after clicking |
| 8 | Raw internals in text: `triage:low_confidence`, "in reply to step ?", ISO timestamps, `manage.py` | Feels broken |
| 9 | Two different clients both shown as "Fictional Studio" with nothing to tell them apart | Wrong client opened |

## 3. Changes by screen and workflow

| Screen | Change |
| --- | --- |
| Navigation | Seven daily destinations (Today · Inbox · Invoices · Clients · Tasks · Disputes · Analytics) on one line; **Setup** menu for Sequences, Automation and Integrations; a **Menu** button on phones (with the Inbox count). The logo goes to Today. No route removed |
| Connection | The header badge is gone. The health check runs through the same proxy as every request; a plain banner appears **only** when the server cannot be reached ("Cannot reach the CollectFlow server… actions will not be saved") with **Try again** |
| Today | The backend's eleven sections (unchanged rules, order and items) are grouped as **Needs your attention · Waiting for approval or sending · Blocked or at risk · Due now and coming up**, with four summary tiles linking to them. Empty groups are hidden; an "All clear" state when nothing is due; any future section is still shown. Each item has a specific action (**Review reply, Review dispute, Check delivery, Review draft, Review and send, Fix sequence, See why, Open task…**). Items the user's role cannot do are marked **For an admin / For an approver** with a secondary **View** button and "let an approver know". Each section says what happens next |
| Inbox | The three voices are labelled: **What the customer wrote · What the AI suggests** ("a suggestion only") · **Your decision**. Only the fields for the chosen label are shown. **What happens** is stated before saving. For a promise or dispute, the main action is now **Record promise to pay** / **Open dispute case**; saving only the label is a secondary, explained option. Buttons: **Save decision: \<label\>**, **Resume chasing without a label**; a required note says why it is required, with an example. History in plain words; "in reply to our email" when the step is unknown |
| Reminder review (drawer) | **Approve reminder / Approve statement**, **Reject draft**, **Rewrite with AI**, **Send email…**. "Approving … does not send anything: sending is the next, separate step." When the user cannot approve: why, and where an approver finds it |
| Status labels | One table (`frontend/src/lib/status.ts`) maps backend states to: Awaiting approval · Approved, not sent · Sending… · Sent · Not sent · Delivery unknown · Rejected; Disputed / Resolved; Promise due / kept / broken / cancelled; To do / Completed / Cancelled; Needs review / Handled automatically / Reviewed. Reminder badges explain themselves on hover. No state was added |
| Invoices | Titled **Invoices**, with one line on what it is (the sheet decides what is paid). Summary, **Waiting for approval** and the invoice table first; **Run automation now** and **All reminders** folded into expandable sections. The duplicate tasks panel was removed (Tasks and Today show the same tasks with the same actions). Rows that are held, disputed or on a promise show **On hold / Disputed / Promise due: see why** instead of a draft button the backend would refuse; hold notes point to Inbox or Disputes as appropriate |
| Tasks | Filters **To do · Assigned to me · Completed · Cancelled · All**; each task shows its invoice, client and assignee. **Mark task complete** with an example note ("Called; they will pay Friday") and what completing does (a call task completes its sequence step; other tasks never resume chasing). A confirmation message after assigning, completing or cancelling. Empty state explains where tasks come from |
| Disputes | Filters **Open cases · No owner yet · Owned by me · Resolved · All**; statuses **Disputed / Resolved: customer was right / Resolved: invoice stands**; one-line purpose |
| Clients | Each ranked row shows the client's email (same names are distinguishable); plain description of the ranking |
| Integrations | A summary line at the top: **Everything is working** or **N problem(s): …**, with problems listed first and in plain words (**Not working / Not running lately**) |
| Today (backend copy) | `apps/core/today.py` integration items now read "Background worker not working", "Scheduled job "Sequence tick" has not run recently — Last ran Oct 22, 15:45. Usually the background worker is stopped"; the unknown-delivery text explains why it is never resent and who records the result. Wording only: same items, sections, rules and fields |
| All pages | Titles match the menu (Sequences, Automation), consistent heading style, shared `PageHeader`, `EmptyState`, `StatusPill` and button styles in `components/dashboard/ui.tsx` |

## 4. Safety guarantees preserved

No backend rule, permission, state or endpoint contract changed; the only backend edit is wording in
`apps/core/today.py`. Every button still calls the same API, and the backend still decides:
four-eyes approval, roles, CSRF, holds and their owners, disputes and promises, Sheets as the payment
authority, at-most-once delivery, DELIVERY_UNKNOWN handling, AI quota controls and the audit trail.
Frontend changes only hide or relabel actions the backend would refuse, and say why; none makes an
action available that was not. Approval and sending remain two separate, explicit steps. The new
`/api/backend/health` proxy route is a read-only, unauthenticated liveness check that already existed
at `/api/health/`.

## 5. Verification

**First-time-user scenarios** (browser, operator and approver accounts, fresh seeded database), all
**PASS**:

| # | Scenario | What the screen shows without explanation |
| --- | --- | --- |
| 1 | Most important work | Today's first group is "Needs your attention", with "Review reply" and "Review dispute" |
| 2 | Why an invoice cannot be chased | The disputed invoice's drawer says it is disputed and that an approver ends it |
| 3 | Review and classify a reply | Customer, AI and decision separated; choosing "Promise to pay" leads to "Record the promise to pay"; only the relevant field for "Requests info"; decision saved |
| 4 | Reminder awaiting approval | Today marks it "For an approver"; the drawer disables "Approve reminder", says why, and that approval never sends |
| 4b | Approver approves it | Approval succeeds and the drawer then offers the separate "Send email…" step (nothing sent) |
| 5 | Disputed invoice | "Disputed", and that its invoice is not chased |
| 6 | Assign and complete a task | Each action confirmed in a message |
| 7 | Client risk | Ranked list with emails; "Why this score" breakdown |
| 8 | Aging and forecast | Aging buckets; forecast labelled an estimate; paid-date caveat |
| 9 | Statement | Lines and totals, "Prepare statement draft", approver requirement before sending |
| 10 | Integration problem | Summary banner naming the failing parts; "What to do" per card |

**Checks:** frontend `tsc --noEmit` and `eslint` clean; production build succeeds (all 13 routes);
backend `apps.core` and `apps.insights` tests (86, including the M12 Today tests and the P0
acceptance suite) pass on `collectflow_dev`, run alone. The scenario script lives outside the repository
(it uses a Playwright install from another local project); M15 is where a committed browser suite belongs.

## 6. Known usability gaps

- Resolving a **delivery with an unknown outcome** still needs an admin command (`reconcile_delivery`);
  there is no screen for it. Today now explains this plainly, but a screen would be better (would need a
  new endpoint, so out of scope here).
- The Integrations and drawer detail texts that come from the backend (worker heartbeat, AI quota)
  are still partly technical.
- Sequences and Automation screens were only retitled; their editors remain expert tools.
- Statements are reviewed in the drawer of the oldest invoice, not on their own page.
- The invoice drawer is long for invoices with promises, disputes and tasks; it is not restructured.
- Next.js shows its development "N" badge in local screenshots only (not in production builds).

## 7. Remaining work

- Full isolated backend regression suite and a committed browser test suite (M15).
- M13 and M14 production deployments and M12.1 live acceptance remain separate pending gates.
