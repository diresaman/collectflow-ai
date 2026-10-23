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

## 5a. Second pass: application structure (owner request)

The owner asked for menus with a clear meaning and order, so the app feels like one organised product.

**Research.** How established accounts-receivable tools organise work:
- **Upflow:** an *Actions* list with *To-do* and *All* tabs, plus *Customers*, *Invoices*, *Workflows* and separate *Insights*.
- **Versapay:** the customer page is the hub, with promise-to-pay progress, and there is a collector queue.
- **Kolleno:** a dashboard of collected versus overdue, and a separate dispute triage queue.
- **SAP Fiori worklist pattern:** tabs over one dataset, with consistent colours for meaning.
- **General B2B guidance:** a grouped left sidebar for 5–20 sections, daily work first, reports and admin apart,
  shallow hierarchy and plain labels.

Sources: [Upflow Actions list view](https://docs.upflow.io/en-us/core-entities/actions/your-actions-list-view),
[Upflow workflows](https://docs.upflow.io/en-us/essentials/get-started/step-5-configure-workflows),
[Versapay promise to pay](https://updates.versapay.com/announcements/ann_eSbyRmOFSUoWX),
[Kolleno dashboard](https://www.kolleno.com/kolleno-2-0-part-1-revamped-dashboard/),
[SAP Fiori worklist](https://www.sap.com/design-system/fiori-design-web/v1-71/page-types/floorplans/work-list/usage),
[Cloudscape side navigation](https://cloudscape.design/patterns/general/service-navigation/side-navigation/),
[Journey: B2B navigation](https://journey.io/blog/b2b-navigation-design-tips-for-better-ux).

**Decisions (approved by the owner):** a grouped left sidebar, and a dedicated Approvals page.

| Area | Change |
| --- | --- |
| App shell (`components/layout/AppShell.tsx`) | A fixed left sidebar in groups, in order of daily use. **Today** comes first. **Collections:** Inbox, Approvals, Tasks, Disputes. **Receivables:** Clients, Invoices. **Insights:** Analytics. **Setup:** Sequences, Automation, Integrations. Each item has an icon and a **live count** from the Today report: replies to review, approvals, due tasks, open disputes, and a red "!" on Integrations when something is broken. The user's name, role and Sign out sit at the bottom. On phones, a top bar with **Menu** (showing the count) opens the same sidebar as a drawer. The old top bar, Setup dropdown, inbox badge and user menu were removed. All pages use the shell, and no route was removed |
| Approvals (`/approvals`) | A focused work queue with two tabs: **Waiting for approval** and **Approved, not sent**. Each row shows client, invoice or statement, amount, subject, tone, step and age, plus whether an approver is needed and why. When the user cannot approve, it says so and why ("You edited it, so another approver must approve it" or "Your role cannot approve it"). The button reads **Review draft** or **Review and send**, or **View** for someone who cannot act. It opens the existing review drawer, where approval and sending work exactly as before and the backend enforces the rules. This is only a filtered view of the existing follow-up list |
| Today | Admin-only integration problems become **one summary line** for non-admins: "*N* integration problem(s), for an admin; reminders may be delayed", with **View details**. "Invoices on hold" repeats items listed above, so it is folded by default. The approval group links to Approvals |
| Invoices | The approvals panel is replaced by one line: "*N* reminder(s) waiting for approval or sending · **Go to Approvals →**". The last column is now **Reminders** and wraps instead of being cut off |

**Re-verified:** the 10 scenarios plus 4b all **PASS**, with scenario 4 now starting from the sidebar's
Approvals link. Frontend type-check, lint and production build are clean. Screenshots:
`docs/v2/ux/today-after.png`, `approvals.png`, `mobile-menu.png` and `today-mobile-after.png`.

## 5b. Third pass: navigation and terminology (2026-10-23)

Owner request: a first-time user should know where to go without training. Wording and navigation only: no new
feature, no backend change, no migration, no new page or dashboard, and no collection safety rule touched. The
screenshot mentioned in the request was not attached, so the baseline is the second-pass sidebar
(Today / Collections / Receivables / Insights / Setup).

**Sidebar (four groups):**

| Group | Item | Route (unchanged unless noted) | Count shown |
| --- | --- | --- | --- |
| Workspace | Today's Work | `/today` | Everything actionable on Today's Work |
| | Customer Replies | `/inbox` | Replies to review |
| | Approvals | `/approvals` | Drafts awaiting approval plus approved, not sent |
| | Tasks | `/tasks` | Tasks due today or overdue |
| | Disputes | `/disputes` | Open disputes |
| Customers & Payments | Customers | `/clients` | — |
| | Invoices | **`/invoices`** (was `/`) | — |
| Reports | Reports & Insights | `/analytics` | — |
| Settings | Reminder Schedule | `/sequences` | — |
| | Automation Settings | `/automation` | — |
| | Connected Services | `/settings/integrations` | "!" when a service or scheduled job has a problem |

- **Counts:** they come from Today's open sections, as before. Completed, cancelled, resolved and sent items are
  never in them. Broken promises, promises due, holds and schedule blocks are not counted in the menu.
- **Routes:**
  - `/` now redirects to `/today`, so Today's Work is the landing page. Before this pass, signing in without a
    return path landed on Invoices.
  - Sign-in goes to `/today`.
  - The older `/?invoice=<id>` still works: it redirects to `/invoices?invoice=<id>` (`next.config.ts`). The API and
    stored analytics snapshots still return that older form. In the app, `appLink()` (`lib/url.ts`) maps it before
    linking, so there is no extra round-trip.
  - No backend route, endpoint, model or table was renamed.

**Terminology** (display only; API names stay):

| Before | After | Where |
| --- | --- | --- |
| Today | Today's Work | Menu, page heading, tab title, "Open Today's Work" links |
| Inbox | Customer Replies | Menu, heading, tab title, "(see Customer Replies)", "Review in Customer Replies", the Disputes empty state |
| Clients / client | Customers / customer | Menu, heading, tab title, description, empty state, list label, the invoice table column, error text, a note placeholder, "the customer's page" |
| Analytics | Reports & Insights | Menu, heading, tab title, error text |
| Sequences / sequence | Reminder Schedule / reminders | Menu, heading, tab title. Invoice status ("Reminders paused", "Reminders finished", "Reminders stopped (paid)"), "Pause reminders", "Resume reminders", "reminder step N", the empty states on Approvals and Tasks, the dispute outcome "Customer is right (stop reminders)", the task completion notes |
| Automation | Automation Settings | Menu, heading, tab title. The description still says what the page does: sync, draft, review and confirm sends |
| Integrations / integration | Connected Services / service | Menu, heading, tab title, error text. Today's "Check service" and "connected service problem(s)". Today's section titles via `SECTION_TITLES` ("Connected service problems", "Reminder schedules that need a person"), also used in the urgent line on Customers |

Technical words are kept where accuracy needs them, for example "Gmail", "Gemini", "sheet", "statement" and
"approver".

**Visual and accessibility:**
- **Group headings:** small uppercase labels, with more space between groups and no dividers. Each group's list
  is named by its heading for screen readers.
- **Items:** at least 40 px tall, with 20 px icons throughout. Long labels truncate, and the full text is in the
  link's title.
- **Active state:** the indigo active state and `aria-current="page"` are unchanged.
- **Keyboard:** visible focus rings on menu items, the logo link and Sign out.
- **Unchanged:** the user's name, role and Sign out, the phone drawer, and the persistent shell (no remount
  between pages).

**First-time user check** (each answer is one click from the sidebar, and the page heading repeats the menu
words):

| Task | Where |
| --- | --- |
| 1. Start daily work | Workspace → Today's Work (also the home page) |
| 2. Review a customer email | Workspace → Customer Replies |
| 3. Approve a reminder | Workspace → Approvals |
| 4. Find an invoice | Customers & Payments → Invoices |
| 5. Find a customer | Customers & Payments → Customers |
| 6. Review collection performance | Reports → Reports & Insights |
| 7. Configure reminder timing | Settings → Reminder Schedule |
| 8. Check connected Google services | Settings → Connected Services (Google Sheets, Gmail, Gemini, worker) |

**Verification:**
- **Static checks:** `npm run typecheck`, `npm run lint` and the production build (`next build`, run by the suite)
  pass.
- **Playwright:** 24 passed (fake providers, throwaway `collectflow_e2e` database).
- **Updated tests:**
  - journey 1 now clicks every one of the 11 menu items. It checks that each page's heading matches the menu
    words, that the item is highlighted, and that no session check flashes between pages;
  - a new test checks that `/` opens Today's Work and that `/?invoice=<id>` opens the invoice on `/invoices`, with
    Invoices highlighted;
  - the phone test uses the new group name.
- **Journey 5:** it no longer races the Inbox's existing auto-advance to the next reply after a decision. It opens
  the handled reply under "All replies" and checks the decision there. App behaviour is unchanged.
- **Providers:** no live provider was used.

**Not changed:**
- **Backend wording:** some API text still says "sequence", for example Today's item titles "Sequence step 2 held"
  and "Sequence position needs review", and the worklist's "step N of <schedule name>". Changing it means a
  backend release and its tests.
- **Automation Settings:** the label follows the request. The page is a run-and-review workspace rather than a
  settings form; its description says so.
- **Deployment:** deployed with the final release on 2026-10-23 (`12-m15-release-readiness-report.md` §13).

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
