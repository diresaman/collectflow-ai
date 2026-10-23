# 10 — M14 implementation report: analytics, consolidated statements and cash forecast

- **Date:** 2026-10-23
- **Baseline:** `586be3b` (M13, not deployed)
- **Plan:** roadmap Phase 07 (`02-implementation-roadmap.md`) and the M14 authorization
- **Status:** implemented and tested on the development database (`collectflow_dev`) with mocked
  Google Sheets, Gmail and Gemini. **Three additive migrations.** **Not deployed.**

| Gate | Status |
| --- | --- |
| Mocked P0 acceptance (M12) | PASSED |
| Real-stack M12 deployment | PASSED |
| **M12.1 live-provider acceptance** | **PENDING** (promise confirmation and cleanup outstanding; `09-m12-live-provider-acceptance-report.md`) |
| **M13 production deployment** | **PENDING** (awaiting approval) |
| **M14 production deployment** | **PENDING** (awaiting approval; section 10) |

## 1. What was built

| Area | What it does |
| --- | --- |
| Client profile | The M13 client page (`/clients`) gains: every name the sheet used for the email (no merging beyond the email, and no claim that companies sharing an address are one company), paid history with the observed paid date and days late (**`null` = unknown, shown as "paid date unknown", never 0**), promise counts (kept, broken, open, cancelled; no kept rate without a settled promise), communication history (reminders and statements sent, replies received), statements, the M13 risk breakdown |
| Consolidated statements | For a client with 2 or more chaseable invoices, a person prepares one statement email listing them (section 4). Reviewed, edited, approved and sent through the existing reminder drawer and the existing Gmail delivery |
| Analytics (`/analytics`) | Summary strip (overdue, expected cash in 30 days, DSO, open promises) per currency; aging; DSO and CEI; payments after reminders; the explained 30-day forecast with per-invoice reasons. Plain bar charts (no new dependency) |
| Snapshots | One `AnalyticsSnapshot` row per kind (aging, DSO/CEI, attribution, forecast, KPIs), replaced in place by the existing `insights.recompute` worker job, right after the M13 risk recompute. Page views only read them |
| APIs | `GET /api/analytics/`, `GET /api/analytics/{aging,dso,cei,attribution,forecast,kpis}/`; `GET/POST /api/clients/{id}/statement/`; client detail gains `paid_history`, `communications`, `promise_summary`, `statements`; follow-ups gain `kind` and `statement` (lines and totals) and are listed under every invoice a statement includes |
| Gemini retry fix | Section 7 |

## 2. Source of each rule

| Rule | Source |
| --- | --- |
| Aging buckets current, 1–30, 31–60, 61–90, 90+ | Design §3 (exact) |
| Statement for 2 or more eligible invoices; `FollowUp.kind`; `StatementLine` (follow-up, invoice, amount snapshot); backend-computed lines and total; always an approver who is not the last editor; "one active per invoice" also covers statement lines | Design §3, §5, §6, §7C; gap analysis 21 |
| Forecast: outstanding × P(paid in 30 days); open promise in the window → the client's promise-kept rate; otherwise aging bucket and the client's average days late; fewer than 3 paid invoices → portfolio averages, low confidence | Design §7H, §12 |
| DSO, CEI and attribution formulas; the forecast probabilities and adjustments; statement eligibility details (cooldown, in-flight reminders) | **Not in the design.** Conservative proposals, section 3, **pending owner approval**; all tunable in settings |
| Uncertainty bands | The design specifies none, so none are drawn. The forecast shows the promise-backed part and the excluded disputed amount instead |

## 3. Formulas and heuristics (proposed where marked)

**Data conventions.** An invoice is *billed* when CollectFlow first saw it (its `INVOICE_CREATED` event,
or the row's creation time): the sheet has no invoice-date column. An invoice is *paid* when a sync first
showed PAID (`INVOICE_UPDATED` event): **the observation date, not the bank settlement date**; invoices
already PAID when imported have no paid date and are reported as unmeasured. Invoices no longer in the
sheet are excluded from every figure. Days use the local business date (`DJANGO_TIME_ZONE`).
Nothing is ever added across currencies.

| Metric | Formula |
| --- | --- |
| Aging (design) | Unpaid in-sheet invoices by days past due: ≤ 0 current; 1–30; 31–60; 61–90; > 90. Totals and counts per currency |
| DSO *(proposed)* | Over the last `ANALYTICS_PERIOD_DAYS` = 30: closing AR ÷ billed in the period × 30 |
| CEI *(proposed, standard formula)* | (opening AR + billed − closing AR) ÷ (opening AR + billed − closing current AR) × 100. Opening AR = invoices seen before the period start and not seen PAID before it; closing current AR = unpaid and not yet due |
| DSO/CEI availability | Reported only when CollectFlow saw invoices before the period start; otherwise "insufficient history" with the date it becomes available. **Opening balances are never estimated.** Undefined denominators are shown as undefined, with the reason |
| Attribution *(proposed)* | For each payment seen in the last 90 days, the latest reminder or statement about the invoice sent before it. Within 7 days (`ATTRIBUTION_WINDOW_DAYS`) it is "paid after a reminder", labelled by step and tone or "statement". **A time association, not proof of cause** (stated on the page) |
| Forecast (design rule, proposed values) | Per unpaid in-sheet invoice: open dispute → P = 0 (shown as excluded). Open promise dated within 30 days → P = client's kept rate if ≥ 3 settled promises, else portfolio rate (low confidence), else 0.5 (low confidence); dated on the promised day. Otherwise P = bucket probability (current 0.80, 1–30 0.60, 31–60 0.40, 61–90 0.25, 90+ 0.10), dated on due date + average days late (client's if ≥ 3 measured payments, else portfolio, else 0; low confidence); if that date is after the window, P × 0.25. A promise broken in the last 180 days: P × 0.5. An unconfirmed payment claim changes nothing and is flagged. Expected = amount × P, by week; labelled an estimate |
| KPI strip (design) | Overdue amount, expected cash in 30 days, DSO, open promises (count and amount), per currency |

## 4. Statement architecture

| Step | Behaviour |
| --- | --- |
| Eligibility | The client's unpaid in-sheet invoices that pass the shared `ineligibility()` (overdue, no hold or dispute, valid email, not in another active statement), have no APPROVED, SENDING or DELIVERY_UNKNOWN reminder, and are outside the 7-day cooldown (a statement counts as a reminder for each invoice it listed). At least `STATEMENT_MIN_INVOICES` = 2. The preview lists excluded invoices with reasons |
| Request | `POST /api/clients/{id}/statement/` (operator) checks without any AI call and queues one `followups.statement` job (one at a time per client). No scheduled job ever prepares, approves or sends a statement |
| Drafting | Gemini (`statement-v1`) receives the client name, invoice count and numbers only: **no amounts, no email**. It returns subject, opening, closing and reasoning. Text containing an amount (decimals, currency symbols or the statement's currency codes next to digits) or a link is rejected (502, attempt recorded). The backend inserts the exact invoice lines and per-currency totals between the opening and closing |
| Saving | Under locks (unsent reminders, then invoices by primary key): every invoice unchanged since the request and still eligible, exactly the same set; unsent per-invoice drafts are withdrawn (`cancelled_reason="statement"`, their sequence steps stay pending); `FollowUp(kind=STATEMENT)` on the oldest invoice plus one `StatementLine` per invoice with its facts; a `DRAFT_GENERATED` event on every invoice |
| Lock | While a statement is active, each of its invoices reports `in_statement` from `ineligibility()`, so per-invoice drafting, approval, sending and the sequence tick all refuse or wait through the existing checks |
| Edit | Subject and body as for reminders; the backend's lines block must stay in the body verbatim |
| Approval | Always APPROVER, never the last editor (`apps.accounts.approval`). Re-checks every line invoice under its lock: eligibility, unchanged facts, one recipient, lines block intact |
| Send reservation and delivery | The existing `FollowUpDeliveryService`: conditional APPROVED → SENDING, every line invoice re-checked under its lock, one delivery job (`max_attempts=1`), re-checked again before the single Gmail call, `num_retries=0`; DELIVERY_UNKNOWN is never retried and keeps the invoices locked until reconciled. The outcome event is written on every listed invoice |
| After | A dispute or promise recorded on any listed invoice withdraws the unsent statement (M11 path extended). A reply in a statement's thread is held against every listed invoice (and, concerning several invoices, goes to a person) |

## 5. Changed files

- **New:** `apps/followups/statements.py`, `followups/migrations/0007_statements.py`,
  `apps/insights/analytics.py`, `insights/models.py`, `insights/migrations/0001_initial.py`, tests
  (`followups/tests/test_statements.py`, `insights/tests/test_analytics.py`), frontend
  `lib/api/analytics.ts`, `components/analytics/AnalyticsView.tsx`, `app/analytics/page.tsx`, this report.
- **Changed (backend):** `followups/models.py` (kind, `StatementLine`, purpose), `eligibility.py`
  (`in_statement`), `services.py` (statement-aware eligibility, edit, approval, withdrawal), `delivery.py`
  (line checks, outcome events), `prompts.py` (`statement-v1`), `serializers.py`, `views.py`, `urls.py`,
  `apps.py`; `accounts/approval.py` (statements always need an approver); `cases/common.py` (locks
  statements too); `sequences/engine.py` (statements count for cooldown; excluded from threading and
  history); `inbox/ingestion.py` (statement threads), `inbox/classification.py` (retry fix);
  `insights/jobs.py`, `views.py`, `urls.py`; settings; `.env.example`.
- **Changed (frontend):** reminder drawer (statement lines), dashboard (statements listed under each
  invoice), Clients page (statement panel, payment and communication history), navigation (Analytics),
  proxy allowlist.

## 6. Migrations (additive)

| Migration | Contents |
| --- | --- |
| `invoices/0009_client_insight_db_defaults` | Database defaults for M13's `Client.risk_factors` (`{}`) and `risk_low_confidence` (false), so code from before M13 can still create clients if only the images are rolled back |
| `followups/0007_statements` | `FollowUp.kind` (default INVOICE in Django **and in the database**, so existing rows are reminders and older code can still insert); `GenerationAttempt.purpose` choice STATEMENT; `StatementLine` with "once per statement" and "amount > 0" constraints |
| `insights/0001_initial` | `AnalyticsSnapshot` (unique `kind`) |

All applied on `collectflow_dev`; `makemigrations --check` clean. Reversal: `migrate insights zero`,
`migrate followups 0006`, `migrate invoices 0008` (and `0007` for M13). Reversing `followups 0007`
deletes any statement lines, so if statements were prepared, roll back the images only: the database
defaults keep the older code working on the newer schema.

## 7. Gemini retry fix (M12.1 finding)

Small and isolated, so fixed here (`apps/inbox/classification.py`):

- A classification that meets an active quota pause no longer spends its attempt: the same attempt is
  re-queued for the end of the pause (`deferral` counter, at most `INBOX_CLASSIFY_MAX_DEFERRALS` = 6,
  then it counts as before, so it cannot loop).
- After a real Gemini quota error, the next attempt is scheduled no earlier than the pause that error
  just extended.

Tests: the last attempt is deferred, not spent, and later really calls Gemini; deferrals are bounded;
the next attempt waits for the extended pause. No Gemini call was made to verify it.

## 8. Tests

New: 21 statement tests (`followups/tests/test_statements.py`), 16 analytics tests
(`insights/tests/test_analytics.py`), 3 classification-retry tests (`inbox/tests/test_classification.py`): **40**.

| Required scenario | Covered by |
| --- | --- |
| 1 Client profile | `AnalyticsApiTests.test_client_profile_separates_missing_from_zero` |
| 2 Several invoices and currencies | `test_bucket_boundaries_per_currency`, `test_currencies_and_reproducibility`, `test_currencies_get_separate_totals` |
| 3 Aging boundaries | `test_bucket_boundaries_per_currency` (−5, 0, 1, 30, 31, 60, 61, 90, 91 days) |
| 4 DSO and missing data | `test_formulas_from_observed_history`, `test_short_history_is_reported_not_estimated`, `test_nothing_billed_leaves_dso_undefined` |
| 5 CEI and missing data | same tests (CEI 26.3 % worked example) |
| 6 Attribution | `AttributionTests` (in and out of the window, statements count per invoice) |
| 7 Paid-date semantics | `test_paid_date_is_the_first_sync_that_showed_paid` (first PAID event; imported-paid unmeasured) |
| 8 Forecast and reproducibility | `ForecastTests` (bucket P, lateness beyond the window, identical results) |
| 9 Promises and disputes in the forecast | `test_promises_use_the_kept_rate`, `test_disputes_broken_promises_and_claims` |
| 10 Statement eligibility | `EligibilityTests` (holds, cooldown, approved reminder, not due, minimum two) |
| 11 Editing and four-eyes | `test_always_an_approver_who_did_not_edit_it`, `test_the_invoice_lines_cannot_be_edited` |
| 12 Invoice changes after approval | `test_invoice_paid_held_or_changed_after_approval_blocks_the_send`, `test_a_queued_delivery_rechecks_before_gmail`, `test_approval_refused_when_a_line_changed`, `test_a_dispute_on_one_line_withdraws_the_statement` |
| 13 Concurrent sends | `ConcurrentSendTests` (PostgreSQL threads: three requests, one attempt) |
| 14 Unknown outcomes | `test_unknown_outcome_is_never_retried` |
| 15 Restarts and idempotency | `RefreshTests` (snapshots replaced, repeated job identical), M13 schedule tests |
| 16 Roles | statement and analytics API tests (no role 403; operator cannot approve a statement) |
| 17 Today and M13 unchanged | existing Today, P0 and insights suites (run in full) |

Results (all on PostgreSQL 16, `collectflow_dev`, mocked providers):

| Run | When | Result |
| --- | --- | --- |
| `apps.inbox` (incl. the 3 retry tests) | after the retry fix | 101 tests OK |
| `apps.followups apps.sequences apps.cases apps.inbox apps.insights apps.accounts` | after statements were wired into eligibility, approval, delivery, cases, the engine and ingestion | 374 tests OK (one locking error, `FOR UPDATE` with `DISTINCT`, found and fixed before this run) |
| `followups.tests.test_statements` | after the statement test fixes | 20 tests OK |
| `apps.insights` (incl. the 16 analytics tests and all M13 tests) | after analytics | 51 tests OK |
| `test_statements.WorklistSuggestionTests` + `insights.tests.test_worklist` | after the last backend change (worklist and statement interaction, database defaults) | 12 tests OK |
| `manage.py check`; `makemigrations --check` | final code | clean; no changes |
| Frontend `tsc --noEmit`, `eslint` | final code | clean |
| Frontend production build (`next build`, local) | final code | succeeds; routes include `/analytics` and `/clients` |
| Proxy allowlist check (scripted) | final code | 68 frontend API calls, 0 missing |

**Full backend suite: not completed for M14.** A first full run was stopped because it had started
before the last model change (database defaults). The restarted run was then broken by a targeted test
run started at the same time: both use `test_collectflow_dev`, and the second run dropped the first
run's database ("database test_collectflow_dev does not exist"). A third run was stopped on purpose when
the full regression was deferred; its output showed no failure or error before it stopped, but it is
not a complete result. **The full isolated regression suite is deferred to M15.** The last complete
full-suite result is M13's (537 tests).

Rollback compatibility (section 10): verified at the database level only. On `collectflow_dev`, inside
a transaction that was rolled back, a client and a follow-up were inserted without the new columns (as
the M12 code does); the database filled `risk_factors = {}`, `risk_low_confidence = false` and
`kind = INVOICE`. Running the pre-M13 images against the new schema has **not** been exercised and
still needs verification during the deployment rehearsal.

## 9. Data limitations and known issues

- Paid dates are sync observations; DSO, CEI, attribution and lateness inherit that lag.
- No invoice date in the sheet: "billed" means first seen by CollectFlow. A bulk import counts as
  billing on the import day, which is why DSO and CEI wait for a full tracked period.
- DSO and CEI will show "insufficient history" on the real stack until 30 days after the first import.
- Invoices removed from the sheet are excluded rather than treated as collected or written off.
- Forecast probabilities are fixed heuristics (section 3), not fitted to this business.
- The design's `/` worklist home page, Recharts and keyboard navigation are not built (M13 decision kept;
  plain bars avoid a new dependency; keyboard work is M15).
- A statement is reviewed in the drawer of its oldest invoice (and listed under each of its invoices),
  not on a separate statement page.
- The full backend regression suite has not completed on the M14 code (section 8); M15 runs it in
  isolation, plus browser-level tests.
- The M14 parameters marked "proposed" (section 3) await the owner's approval.

## 10. Deployment and rollback (not done; needs approval)

M13 is not deployed yet, so the next deployment carries M13 and M14 together (or M13 first, if preferred):

1. Back up the real database and verify it (restore and compare, as for M10–M12).
2. Tag the running images `pre-m13`; build the new images while the old stack serves.
3. Pause the worker (0 jobs in flight); recreate the api, which applies `invoices 0008`,
   `followups 0007` and `insights 0001`; `manage.py check`; confirm row counts and hashes are unchanged
   except for the new empty tables and columns (`FollowUp.kind` = INVOICE on every row).
4. Recreate web; resume the worker; confirm the first `insights.recompute` writes the risk scores and
   the five snapshots.
5. Verify `/clients`, `/analytics`, the statement preview (no statement prepared), the existing pages;
   no Gmail sends, no Gemini calls.

Rollback: pause the worker and redeploy the `pre-m13` images. The new columns have database defaults,
so the older code should keep working on the newer schema (database-level evidence only, section 8;
rehearse it before relying on it); this keeps any statements prepared meanwhile. If
no statement was prepared, the schema can also be reversed: `migrate insights zero`,
`migrate followups 0006`, `migrate invoices 0007`.

Development-only database changes: `invoices 0009`, `followups 0007` and `insights 0001` are applied
to `collectflow_dev` only. The real database and the running stack were not touched by M14.

No live Gemini call, Gmail send or Sheets read was made for M14.

### 10.1 Production deployment record (2026-10-23): PARTIAL

The deployment covered M13, M14 and the UX work (HEAD `96bedac`). The owner ran `backend/backups/deploy-m13.sh`;
Claude verified the result read-only. Times are UTC.

| Check | Result |
| --- | --- |
| Backup | `backend/backups/collectflow-before-m13-20261023T103338.dump`, checksum verified. The restored copy was identical to live. A migration rehearsal on that copy preserved all data |
| Rollback images | `collectflow-{api,worker,web}:pre-m13` = `0edc57667052`, `137cc5023d76`, `1b514d58a643` (M12) |
| New images | api `a96708d810f7` (running since 11:09:04), web `0cce2d611845` (running since 11:09:56), worker `ed158895c363` (built, **not started**) |
| Migrations | `invoices 0008` and `0009`, `followups 0007`, `insights 0001` applied at 11:09:21–22; 51 migrations in total; `manage.py check` clean |
| **Worker** | **Not running.** It was stopped cleanly at 11:08:42 (script step 3, 0 jobs in flight), and the script ended before step 6 ("resume the worker"). The container is exited and still on the M12 image. The cause (a failed check or an interrupted run) is not visible in the logs. Until it is started, scheduled syncs, reply polls, the sequence tick, the promise and task checks, and the first risk and analytics calculation are paused. Integrations correctly reports the worker as failed |
| Record preservation | Follow-ups and clients are identical to the pre-deployment reference apart from the new columns (`670d87d3…`, `04ebcce0…`); all 9 follow-ups have kind INVOICE. All other content hashes and workflow states are identical. The only row differences are the new empty tables (`followups_statementline`, `insights_analyticssnapshot`), Django's migration, permission and content-type rows, and normal M12-worker activity between the backup and the pause (10 jobs, 2 sheet syncs, the mailbox cursor) |
| Providers | 7 delivery attempts (unchanged; the last is the 06:10 live test email). 0 Gemini calls since the backup. 0 statements. No duplicate jobs |
| Configuration | `.env`, the Gmail token and credential files, `GMAIL_READ_ENABLED=true`, the sender and the recipient allowlist are all unchanged |
| Pages (signed out) | All 12 pages return 200, including `/approvals`. New API routes return 401 when signed out; an unknown route 404; a cross-origin write 403 |
| API (signed in, in-process, rolled back) | Today, worklist, clients and client detail (paid history, communications), analytics (`not_computed`, as expected while the worker is stopped), forecast, follow-ups (`kind`, `statement`), replies, tasks, disputes and integrations: 200. Statement preview works; nothing is eligible yet. No role: 403. CSRF enforced on the recompute and statement writes. Risk and analytics computed correctly on production data inside the rolled-back transaction |

**Status: API and web deployed and verified; worker pending.** To complete it, the owner runs
`docker compose up -d --no-deps --no-build worker`. Claude then checks that the worker runs `ed158895c363`,
that the first `insights.recompute` succeeds (risk scores and 5 analytics snapshots), that the scheduled jobs
are healthy, and that there are still no sends or Gemini calls.
Rollback stays available through the `pre-m13` images; the new columns have database defaults.
