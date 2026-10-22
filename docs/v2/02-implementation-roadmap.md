# 02 — Implementation roadmap

Baseline: commit `676b43c`. Phase numbers follow the requested structure; the milestone (M) numbers
continue the repository's commit history.

## 1. Phases at a glance

| Phase | Milestone | Scope | Tier | Estimate | Status |
| --- | --- | --- | --- | --- | --- |
| 01 Foundation and architecture | M08 | Sessions and roles, outbox and worker, timeline, clients | P0 | — | **Done** (`676b43c`) |
| 01 Foundation: PostgreSQL migration | M08.1 | PostgreSQL 16, Docker Compose, SKIP LOCKED, SQLite → PostgreSQL transfer | P0 | — | **Done**: data cut over and verified 2026-10-22 |
| 02 Sequences, threading, four-eyes | M09 | Sequence engine, tick, threaded send, approval policy, call tasks | P0 | 7–8 h | **Done** (see `05-m09-implementation-report.md`); live threading verified 2026-10-22 |
| 03 Reply ingestion and classification | M10 | Gmail read, poll, classify, triage, health page | P0 | 6–7 h | **Next** |
| 04 Promises, disputes, tasks | M11 | Promise lifecycle, dispute lock, all task kinds, daily promise check | P0 | 4–5 h | |
| 05 Closed-loop UI and P0 gate | M12 | Invoice detail, approvals, sequence editor, explicit unavailable states, P0 demo | P0 | 4–5 h | |
| 06 Risk and worklist | M13 | Client metrics, risk score, Today page, `/clients` | P1 | 3–4 h | |
| 07 Statements, analytics, forecast | M14 | Statement email, aging, DSO, CEI, attribution, forecast | P1 | 4–5 h | |
| 08 QA, AI evals, CI, delivery | M15 | Eval harness, Hypothesis, import-linter, Playwright, CI, docs | Release | 6–7 h | |
| 09 Reconciliation | M16 | Stripe test mode or CSV, suggested matches | P2 | 3–4 h | Owner decides after 08 |

**Remaining effort: about 34–41 h for Phases 02–08**, plus 3–4 h if Phase 09 is approved.

## 2. Changes to the proposed phase boundaries

1. **Phase 01 is complete** in two parts: M08 (application foundation) and M08.1 (PostgreSQL 16 and
   Docker Compose, which reverses the earlier decision to stay on SQLite). See
   `04-m08-postgresql-migration-report.md`.
2. **Four-eyes approval moves from 05 to 02.** Step 3 of the default sequence is "firm, approver required".
   Shipping that step without the rule would leave firm emails approvable by anyone for three phases.
3. **Gmail threading needs the RFC `Message-ID` of the previous send.** Phase 02 generates it locally. If a
   live check shows Gmail does not keep it, Phase 02 pulls the read scope forward from Phase 03 (see the
   Phase 02 plan).
4. **Each phase ships its own UI.** The proposed Phase 05 held all closed-loop UI, which would leave Phases
   02–04 as backend work nobody can use or demo. Phase 05 now consolidates the pages that span several
   features and runs the P0 gate.
5. **The task model starts in Phase 02**, because step 4 of the default sequence is a call task. Phase 04
   adds the reply-driven kinds.
6. **Phase 03 routes PROMISE_TO_PAY and DISPUTE to triage** until Phase 04 adds their actions. Classifying
   them early is safe because the human picks the outcome.

## 3. Release gates

| Gate | When | Must demonstrate |
| --- | --- | --- |
| **P0 gate** | End of Phase 05 | 1. sync and automatic enrollment; 2. AI draft, approval (four-eyes where required) and send; 3. a real Gmail reply ingested and classified; 4. a promise and a dispute created from replies; 5. pause, resume and broken-promise escalation, with the next draft in the same thread; 6. the complete timeline for those invoices |
| **P1 gate** | End of Phase 07 | Today worklist and 30-day forecast computed from live synced data |
| **Release gate** | End of Phase 08 | Classification accuracy ≥ 90 %, DISPUTE recall ≥ 95 %, zero successful injections; all suites green in CI; README, QA and eval reports, sample data |

Every phase must also keep: all existing tests passing, `makemigrations --check` clean, lint, typecheck
and build passing, and a commit with a summary of what to expect.

## 4. Safety invariants (checked in every phase)

1. AI writes text and proposes labels; deterministic backend rules decide every state change.
2. Every outbound email needs approval plus an explicit send confirmation; nothing scheduled approves or
   sends. Automation, sequences and inbox code never import `followups.delivery`. This is enforced by a
   test now and by import-linter in Phase 08.
3. Gmail delivery is never retried (`max_attempts=1`, `num_retries=0`); unknown outcomes wait for
   reconciliation.
4. Paid, removed, disputed and otherwise ineligible invoices get no drafts, approvals or sends. All of these
   rules live in `eligibility.ineligibility()`, which every path calls.
5. Credentials stay server-side; the proxy forwards only session cookies.
6. Migrations are additive or backfilling; none deletes or rewrites history.
7. The backend serializer and the frontend runtime validator change in the same commit.

## 5. Phase details

### Phase 01 — Foundation and architecture (done, M08)

Delivered: `accounts` (roles, sessions, CSRF, Argon2, rate limit), `timeline` (append-only, triggers),
`Client` and `in_sheet` with backfill, `OutboxJob` and `run_worker`, 202 responses for sync, analyze,
send and run, login UI, job polling, Python 3.12, a file-based test database and threaded concurrency
tests.

### Phase 01b — PostgreSQL migration (M08.1)

Delivered: PostgreSQL 16 as the only runtime database (psycopg 3, `POSTGRES_*` settings), Docker
Compose (db with a persistent volume and health check, api, worker, web; ports on 127.0.0.1),
`SELECT … FOR UPDATE SKIP LOCKED` job claiming, a PL/pgSQL append-only trigger, the
`migrate_from_sqlite` command and `scripts/cutover_to_postgres.sh` (verified backup, row-by-row
verification, cutover policy for unfinished work), and PostgreSQL concurrency tests. The detailed
plan for the next phase is `03-phase-02-implementation-plan.md`.

### Phase 02 — Sequences, threaded delivery, four-eyes (M09)

- **Objective:** turn one-off reminders into versioned multi-step sequences that continue in one Gmail
  thread, with the four-eyes rule.
- **Reuse:** `FollowUpGenerationService`, eligibility, the outbox, the lease, `AutomationOrchestrator`
  workflow steps and items, delivery reservation and release, the timeline.
- **Backend:**
  - new `sequences` app (Sequence, SequenceStep, SequenceEnrollment);
  - enrollment reconciliation after each sync;
  - `sequences.tick` job, every 15 minutes from the worker and after each sync;
  - tone from the step;
  - `draft-v2` prompt with the step number and previous send date;
  - threaded Gmail send;
  - approval policy (`approver_required`, `self_approval`);
  - `inbox.Task` (CALL);
  - pause and resume;
  - a stale-data flag after 26 h without a successful sync.
- **Frontend:**
  - invoice table columns for sequence step, next action and enrollment state;
  - the drawer shows sequence progress, pause and resume, and a locked subject on threaded steps;
  - approval shows why it is unavailable;
  - a read-only `/sequences` page (admins can create a new version);
  - a tasks panel;
  - a stale-data banner.
- **Migrations:** the sequences tables plus a default seed; `inbox.Task`; nullable `FollowUp.enrollment`,
  `step` and `thread_reply_to`; `DeliveryAttempt.rfc_message_id`; new timeline event types.
- **API:** `GET/POST /api/sequences/` (ADMIN to write), `GET /api/sequences/{id}/`,
  `GET /api/enrollments/`, `POST /api/enrollments/{id}/pause|resume/`, `GET /api/tasks/`,
  `POST /api/tasks/{id}/done/`; invoice and follow-up serializers gain enrollment and approval fields;
  approve returns 403 for four-eyes refusals.
- **Integration:** a live check that Gmail keeps a client-set `Message-ID` and threads step 2; otherwise
  re-authorize with `gmail.metadata` or `gmail.readonly`.
- **Depends on:** Phase 01.
- **Tests:** enrollment and backfill from history, one step per tick, cooldown, previous-step gating,
  blocking on FAILED or UNKNOWN, task step, pause and resume, exit on PAID or removal, threaded MIME
  headers, four-eyes matrix, scheduled path never sends.
- **Acceptance:** v2 §12 scenarios "Invoice becomes overdue", "Step 1 sent… step 2 in the same thread",
  "Machine off for 3 days", "Operator edits a firm draft…", "Scheduled run…".
- **Exclusions:** replies, promises, disputes, statements, risk, the full sequence editor UX.

### Phase 03 — Gmail reply ingestion and classification (M10)

- **Objective:** ingest replies in CollectFlow threads, classify them with Gemini and apply safe actions.
- **Reuse:** the Gmail OAuth plumbing, `integrations/gemini.py`, the outbox, the timeline, tasks,
  enrollment pause and reschedule.
- **Backend:**
  - Gmail scopes extended (`gmail.readonly`) and a read client;
  - `MailboxCursor` (historyId);
  - `InboundMessage` (unique Gmail message ID);
  - text extraction (8 KB, quoted text and signatures stripped);
  - 5-minute `inbox.poll` job with a 7-day fallback scan;
  - `ReplyClassification` and `classify-v1` prompt with a `<client_reply>` block;
  - a dispatcher in one transaction: CLAIMS_PAID (pause 5 days plus a reconcile task), REQUESTS_INFO
    (task), OUT_OF_OFFICE (move `next_action_at`, at most 21 days), WRONG_CONTACT (pause plus a task);
    OTHER, low confidence, schema failure or several invoices go to triage; PROMISE and DISPUTE go to
    triage until Phase 04;
  - human classification and override APIs;
  - a 90-day body purge;
  - `/api/integrations/status/` with no provider calls in the request.
- **Frontend:** `/inbox` triage (AI suggestion, confidence, rationale, label picker, extracted-field editor,
  override history, keys j/k and 1–7) and `/settings/integrations`.
- **Migrations:** inbox models; `AutomationRun.Kind.REPLY_POLL`.
- **API:** `GET /api/replies/?triage=true`, `POST /api/replies/{id}/classify/`,
  `GET /api/integrations/status/`.
- **Depends on:** Phase 02 (known threads, tasks, pause and reschedule).
- **Tests:** deduplication, unknown threads ignored, own messages ignored, historyId expiry, schema
  violations, confidence threshold, injection text, override reapplying the action, and that classification
  never creates a follow-up.
- **Acceptance:** a real reply is ingested once within one poll, classified and recorded; the
  prompt-injection reply changes nothing beyond triage; no email is sent.
- **Exclusions:** promise and dispute actions, the eval harness.

### Phase 04 — Promises, disputes, tasks (M11)

- **Objective:** the business workflows that replies trigger.
- **Backend:**
  - `PromiseToPay` (one OPEN per invoice) and `DisputeCase`;
  - PROMISE_TO_PAY action: open, pause until date + 2, cancel unsent drafts; past dates or > 60 days go to
    triage;
  - DISPUTE action: open a case, pause, cancel unsent drafts, notify an approver (a task);
  - an `open_dispute` rule in `ineligibility()` (409 `invoice_disputed` on every path);
  - daily `check_promises` (KEPT from sync status history; BROKEN escalates one tone and resumes);
  - broken-promise date in the prompt context;
  - dispute resolution (APPROVER): valid exits, invalid resumes;
  - manual dispute creation and promise cancellation;
  - all four task kinds.
- **Frontend:** promise and dispute panels on the invoice, `/disputes`, task list actions.
- **Depends on:** 02, 03.
- **Acceptance:** v2 §12 scenarios for the promise reply, a broken promise, a promise kept, and a dispute
  (approve and send → 409 `invoice_disputed`).
- **Exclusions:** risk, statements.

### Phase 05 — Closed-loop UI and P0 gate (M12)

- **Objective:** make the loop usable end to end and pass the P0 gate.
- **Frontend:**
  - `/invoices` with search and filters (closes the v1 search gap) plus `/invoices/[id]` with source facts,
    follow-up drawer, Gmail thread view, sequence progress, promise and dispute panels, and the full
    timeline;
  - `/approvals` grouped by required role with the four-eyes reason;
  - the `/sequences` editor (steps on a day timeline, versions, enrollment counts per step);
  - every disabled action states why (stale invoice, unknown outcome, dispute, promise, worker down,
    Gmail not ready, AI unavailable);
  - 375 px layouts; drawers full-screen on mobile.
- **Backend:** invoice detail payload (follow-ups, thread, promise, dispute, recent timeline); invoice list
  filters.
- **Acceptance:** the P0 gate (section 3), run live and recorded.

### Phase 06 — Client risk and Today worklist (M13)

- **Backend:**
  - new `insights` app;
  - client metrics (average days late from the PAID dates in the timeline, promise-kept rate, response
    rate);
  - deterministic risk score with stored factors;
  - recompute job after sync and replies;
  - `/api/worklist/` ranked by risk × outstanding, with the top factors and a suggested action;
  - `/api/clients/`.
- **Frontend:** `/` becomes Today (ranked actions plus KPI strip); `/clients` and `/clients/[id]`;
  keyboard navigation.
- **Acceptance:** every score is reproducible from its listed factors; removed invoices are excluded.

### Phase 07 — Statements, analytics, forecast (M14)

- **Backend:**
  - `FollowUp.kind` and `StatementLine`;
  - statement draft (backend total; approver always required; lines locked from per-invoice drafts);
  - aging, DSO, CEI and attribution endpoints;
  - forecast snapshot (v2 §7H heuristic, portfolio fallback labelled low-confidence).
- **Frontend:** "Generate statement" on the client page; `/analytics` with Recharts.
- **Acceptance:** "Client with 3 overdue invoices → statement draft lists 3 lines with a backend-computed
  total"; the forecast moves after marking an invoice PAID and re-syncing.

### Phase 08 — QA, AI evaluation, CI, delivery (M15)

- **Evals:** `run_ai_evals`, `backend/evals/` (40 drafting scenarios, 80 replies including multilingual,
  sarcastic, multi-invoice and injection cases), recorded responses for CI, a gated report.
- **Engineering:**
  - Hypothesis state-machine tests;
  - import-linter;
  - ruff and mypy;
  - Playwright end to end: login → worklist → approve → send (mocked) → simulated reply → triage →
    promise → broken promise → escalated draft;
  - component tests for the worklist and triage;
  - GitHub Actions;
  - request and job IDs in logs.
- **Delivery:** README, a refreshed QA report, eval report, sample sheet template and reply fixtures
  (closes the v1 gap), the scheduling guide updated for ticks and polls, presentation materials.
- **Exclusions:** `/metrics` (optional).

### Phase 09 — Payment reconciliation (P2, M16, optional)

`PaymentRecord`, Stripe test-mode or CSV import, scored match suggestions, APPROVER confirmation that
exits the enrollment and shows a sheet-mismatch banner. The sheet is never written. Starts only on the
owner's approval after Phase 08.

## 6. Risks

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Gemini free tier (about 20 requests/day) shared by drafting, classification and evals | Blocks live demos and evals | Classification first; recorded responses in CI; collect eval recordings over several days or use a paid key once |
| Gmail rewrites a client-set `Message-ID` | Threading breaks | **Closed 2026-10-22:** verified live that Gmail keeps it (05 §8) |
| Re-authorizing Gmail with a restricted scope; 7-day refresh-token expiry in Testing mode | Polling stops | Health page shows it; re-run `authorize_gmail`; plan the demo within a token lifetime |
| Enrolling existing invoices restarts chasing | A wrong-tone email to a client | Start step derived from history; dry-run preview; human approval still required |
| Cron runs on the host and needs the Compose database | A missed daily run if `db` is down | `restart: unless-stopped`; the scheduling guide says to keep `db` up; the run exits 1 and is visible in the log |
| Scope size (about 35 h) | P1 or QA squeezed | P0 gate first; P1 and QA before P2; each phase independently shippable |
