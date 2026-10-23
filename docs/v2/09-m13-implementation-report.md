# 09 — M13 implementation report: client risk scoring and prioritization

- **Date:** 2026-10-23
- **Baseline:** `15a1409` (M12 deployed `0235e2d`/`95c421f`; M12.1 live-acceptance plan)
- **Plan:** roadmap Phase 06 (`02-implementation-roadmap.md`) and the M13 authorization
- **Status:** implemented and tested on the development database (`collectflow_dev`). **One additive
  migration.** **Not deployed** (section 9). No live provider was called.

| Gate | Status |
| --- | --- |
| Mocked P0 acceptance (M12) | PASSED |
| Real-stack M12 deployment | PASSED |
| **Live-provider end-to-end acceptance (M12.1)** | **PENDING** (see `09-m12-live-provider-acceptance-report.md`) |

## 1. What was built

| Area | What it does |
| --- | --- |
| Client identity | Unchanged v2 rule: a client is the normalized (lowercased) email of its invoices. No fuzzy or AI matching. Every client name the sheet used for that email is listed (`invoice_names`), so a shared mailbox is visible rather than hidden |
| Risk score | Deterministic 0–100 score per client from five factors (section 3). Stored on `Client` with every factor's points, maximum, one-sentence explanation and the source records (invoice, promise, dispute and follow-up IDs and dates), plus the calculation time |
| Metrics | `avg_days_late` (the client's own measured payments), `promise_kept_rate` (kept ÷ kept + broken), `response_rate` (reminders answered ÷ reminders past the reply window) |
| Recalculation | `insights.recompute` worker job: after every relevant change (section 4) and once per hour. Requests coalesce; recomputes serialize on a PostgreSQL advisory lock |
| Worklist | `GET /api/worklist/`: one row per client **and currency**, ranked within each currency by risk × outstanding. Each row: score, low-confidence flag, outstanding, priority, top three factors, suggested next action, blockers, invoice links |
| Client API | `GET /api/clients/`, `GET /api/clients/{id}/` (breakdown, metrics, invoices with blockers and open promise/dispute, promises, disputes, recent replies, open tasks), `POST /api/clients/recompute/` (queues a job, 202) |
| Frontend | New **Clients** page (`/clients`, deep link `/clients?client=<id>`) in the nav after Today: ranked worklist grouped by currency, urgent-work banner, client panel with the score breakdown, next action, blockers and links. `/today` is unchanged |

## 2. Architecture and decisions

| Decision | Why |
| --- | --- |
| New `apps.insights` app; fields on the existing `Client` (design §5) | The design lists `risk_score`, `risk_factors`, `avg_days_late`, `promise_kept_rate`, `response_rate`, `metrics_computed_at` on Client. One extra field, `risk_low_confidence`, records the design's low-confidence label |
| Recompute trigger = the timeline | Every change that can move a score already writes a timeline event in its own transaction. `timeline.services` gained a tiny listener hook; insights subscribes and, **on commit**, queues a recompute for the trigger types. No service was edited to call insights, so no path can be missed and no circular import is introduced |
| Coalescing, not per-event jobs | A request is skipped while a recompute is still PENDING (it will read the new data). A RUNNING one does not absorb requests, since it may have read older data |
| Whole-portfolio recompute | The portfolio average (design rule for short histories) depends on every client, and the data is small. One transaction, clients locked in primary-key order, results identical for identical data and time |
| Suggested action = Today's rules | The next action is the most urgent M12 Today item for the row's invoices, in Today's fixed section order, with Today's "who can act" and "why you cannot". No new action rules. Draft and approved-reminder items are suggested only when the invoice is currently eligible |
| `/today` left as is; worklist on `/clients` | The authorization requires the eleven-section Today queue to stay. Design §10 wanted `/` to become the worklist; that would have replaced M12 and the invoice dashboard. Risk is not added to Today |
| Priority uses the stored score × the live outstanding | Money shown is always current (from the last sync); the score shows when it was calculated and from which facts |
| Currency | Outstanding amounts are kept per currency; rows exist per client and currency; ranking never crosses currencies; no exchange rates |

## 3. Risk calculation

The design gives only each factor's **maximum**. The mappings below were **proposed in M13 and approved
by the owner on 2026-10-23**; they are not part of the original design text. They live in
`apps/insights/risk.py` with their parameters in settings (`RISK_OVERDUE_FULL_DAYS`,
`RISK_BROKEN_PROMISE_WINDOW_DAYS`, `RISK_LATE_FULL_DAYS`, `RISK_MIN_PAID_HISTORY`, `RISK_REPLY_WAIT_DAYS`).

| Factor (design maximum) | Mapping | Facts used |
| --- | --- | --- |
| Oldest invoice days overdue (35) | `round_half_up(35 × min(days, 90) / 90)`; e.g. 30 → 12, 45 → 18, 60 → 23, 90+ → 35 | UNPAID invoices still in the sheet; days = local today − due date |
| Broken promises, last 180 days (25) | 0 → 0, 1 → 15, 2 or more → 25 | `PromiseToPay` BROKEN with `resolved_at` in the window |
| Average days late on paid invoices (20) | `round_half_up(20 × min(avg, 60) / 60)`. With fewer than 3 measured payments, the **portfolio** average is used and the client is marked low confidence (design §12). No measured payment anywhere → 0 points | PAID invoices whose paid date is known: the first sync event that showed UNPAID → PAID; days late = max(0, that local date − due date) |
| No reply to the last two sends (10) | 5 per unanswered reminder among the client's last two sends; a reminder counts as unanswered only after 3 days | SENT follow-ups; any stored reply from the client received after the send answers it (and every earlier send) |
| Open dispute (10) | 10 if any dispute case is OPEN | `DisputeCase` |

Total = sum of the five (0–100, also a database check). Rounding is half-up on Decimals.

**Data assumptions and limitations**

- The paid date is when CollectFlow's sync **saw** the payment, not the bank date (the sheet has no paid
  date column). If the machine is off, lateness is overstated by the gap.
- Invoices that were already PAID when first imported have no known paid date and are not measured.
- Outstanding = full amount of unpaid invoices still in the sheet (no partial payments, as before).
- A reply counts for the client if it belongs to the client's invoice thread or was matched to the
  client; replies from unknown senders in the thread still count as a response.
- One email shared by several companies (as in the owner's test sheet) is one client by design.

## 4. Score lifecycle

| Change | How it reaches the score |
| --- | --- |
| Sheet sync: new, changed, removed or returned invoice; UNPAID ↔ PAID | `INVOICE_CREATED/UPDATED/REMOVED_FROM_SHEET/RETURNED_TO_SHEET` → recompute on commit |
| Reminder delivered | `FOLLOW_UP_SENT` |
| Reply stored | `REPLY_RECEIVED` |
| Promise recorded, kept, broken, cancelled | `PROMISE_*` |
| Dispute opened or resolved | `DISPUTE_OPENED/RESOLVED` |
| Time passing (days overdue, reply window) | hourly `insights.recompute:<slot>` job, enqueued by the worker like the other periodic jobs |

Repeated scheduling and worker restarts create one job per slot (idempotency key). Re-running a
recompute writes the same values (tested). Concurrent recomputes serialize on the advisory lock (tested
with PostgreSQL threads). The recompute never writes timeline events, holds, follow-ups or invoice
status.

## 5. Safety

| Rule | How it holds |
| --- | --- |
| AI never decides payment status; the sheet is authoritative | No AI in M13; invoice status is read, never written |
| Risk never clears a hold, approves or sends | `apps.insights` writes only the Client insight fields; it is added to the architecture test that forbids importing Gmail delivery |
| Scheduled jobs never approve or send | The new job only recomputes |
| Four-eyes, holds, disputes, cooldown, DELIVERY_UNKNOWN | Unchanged code paths. The worklist suggests a draft or approval only for an eligible invoice and repeats Today's approval explanation; a test confirms a blocked invoice with a high score is still refused (409) |
| Urgent work is not hidden | Integration problems, replies to review and unknown deliveries are shown above the ranking with a link to Today |
| Permissions | All endpoints use the default role check (a user without a role gets 403); the recompute POST needs CSRF |

## 6. Tests

New: `apps/insights/tests/` (35 tests: 17 scoring, 7 jobs and triggers, 11 worklist and API).

| Required scenario | Covered by |
| --- | --- |
| 1 All five factors | `test_risk.FactorTests` (one test per factor) |
| 2 Maximum and minimum scores | `test_maximum_score_is_100`, `test_minimum_score_for_a_clean_client` |
| 3 Missing payment history | `test_missing_payment_history_scores_nothing`, `test_short_history_falls_back_to_the_portfolio_average` |
| 4 Several invoices per client | `test_oldest_overdue_uses_the_oldest_unpaid_invoice_in_the_sheet`, `test_multiple_invoices_and_currencies_stay_separate` |
| 5 Paid vs outstanding | same, plus `test_sync_payment_and_cases_recompute_through_the_worker` (sheet PAID → nothing outstanding) |
| 6 Broken promises in and out of 180 days | `test_broken_promises_inside_and_outside_the_window` |
| 7 Reply history | `test_no_reply_counts_the_last_two_sends_after_the_wait`, `test_a_recent_send_is_not_unanswered_yet` |
| 8 Open and resolved disputes | `test_open_and_resolved_disputes` |
| 9 Recalculation after changes | `TriggerTests` (sync, dispute open/resolve, promise, payment, through the real worker loop) |
| 10 Repeated jobs and restarts | `ScheduleTests`, `test_repeated_recompute_is_stable`, `ConcurrentRecomputeTests` (PostgreSQL threads) |
| 11 Deterministic ordering | `test_ranked_by_score_times_outstanding_within_each_currency`, `test_ties_break_on_score_then_days_then_email` |
| 12 Currency separation | same ranking test (EUR and USD groups, a client in both) |
| 13 Blocked invoices stay blocked | `test_high_risk_never_suggests_chasing_a_blocked_invoice` (approve refused 409), `test_never_touches_holds_follow_ups_or_the_timeline` |
| 14 M12 Today unchanged | `test_today_is_unchanged` plus the existing Today and P0 suites |

Results:

- **Backend:** full suite on PostgreSQL 16 (`collectflow_dev`): **537 tests**. The full run had 536
  passing and one expected failure: the worker `--once` test counts periodic jobs, and M13 adds one
  (the hourly recompute). The test was updated to 6 jobs and now also asserts the recompute succeeded;
  the rerun of that module with the insights, Today and architecture tests (62 tests) passed.
  502 before M13 + 35 new = 537.
- **Django:** `manage.py check` clean; `makemigrations --check` reports no changes.
- **Frontend:** `tsc --noEmit` and `eslint` clean; production build (`next build`) succeeds with the new
  `/clients` route. The Docker image build was stopped by a 15-minute limit during a slow npm download,
  so the production build was run locally instead; the Dockerfile is unchanged since M12.
- **Proxy allowlist:** the three new calls (`worklist`, `clients/{id}`, `clients/recompute`) are allowed;
  65 frontend API calls in total.

## 7. Changed files

- **New:** `apps/insights/` (`apps`, `risk`, `jobs`, `worklist`, `views`, `urls`, tests),
  `apps/invoices/migrations/0008_client_insights.py`, `frontend/src/lib/api/clients.ts`,
  `frontend/src/components/clients/ClientsView.tsx`, `frontend/src/app/clients/page.tsx`, this report.
- **Changed:** `invoices/models.py` (Client fields and check), `timeline/services.py` (listener hook),
  `automation/scheduling.py` (hourly recompute), `config/settings.py` (app and `RISK_*`),
  `config/urls.py`, `.env.example`, `core/tests_architecture.py` (insights restricted), the web proxy allowlist,
  `lib/url.ts`, `AppHeader.tsx` (Clients), README, roadmap.

## 8. Migration

`invoices/0008_client_insights`: seven nullable or defaulted columns on `invoices_client` and one check
constraint (`risk_score` null or ≤ 100). Additive only, no data step; the hourly job fills the values
after deployment (within minutes, since the worker enqueues the current slot at start). Reversal:
`migrate invoices 0007` drops them. Applied and checked on `collectflow_dev`.

## 9. Deployment and rollback plan (not done; needs approval)

1. Back up the real database and verify the backup (restore and compare, as for M10–M12).
2. Tag the running images `pre-m13`; build the new images while the old stack serves.
3. Pause the worker (0 jobs in flight); recreate the api, which applies `invoices 0008`; check
   `manage.py check` and that only the new columns were added (row counts and hashes unchanged).
4. Recreate web; resume the worker; confirm the first `insights.recompute` succeeds and scores appear.
5. Verify `/clients`, `/api/worklist/`, `/api/clients/` and the existing pages; confirm no Gmail sends,
   no Gemini calls, Today unchanged.

Rollback: pause the worker, `migrate invoices 0007` (drops only the new columns), redeploy the
`pre-m13` images. No other data is touched.

## 10. Remaining issues

| Item | Status |
| --- | --- |
| Live-provider acceptance (M12.1) | **PENDING**. Reply 1 was ingested and held; Gemini classification was blocked by the provider quota. Recorded separately |
| Classification retry inside a quota pause (found during M12.1) | The third attempt can fall inside the longer pause created by the second failure and is used up without calling Gemini (safe: the reply goes to human review). Not changed in M13; proposed fix: schedule the next attempt after the recomputed cooldown |
| Design §10 "`/` is the worklist", KPI strip, keyboard navigation | Not done: `/today` and the invoice dashboard are kept as approved; KPI strip belongs with M14 analytics; keyboard navigation with M15 |
| Paid date precision | Sync-observed, see section 3 |
| Client detail page `/clients/[id]` | A panel with a deep link (`/clients?client=`) instead, matching the M12 pages |

No live Gemini call, Gmail send or Sheets read was needed for M13: all tests use the database and fakes.
