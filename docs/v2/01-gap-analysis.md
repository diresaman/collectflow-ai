# 01 — Gap analysis: v1 → v2

Baseline: commit `676b43c`. Status values:

- **IMPLEMENTED**: exists and satisfies v2.
- **PARTIAL**: exists but needs extension.
- **MISSING**: not implemented.
- **REQUIRES MIGRATION**: the existing implementation must change to satisfy v2.
- **DEFERRED**: optional (P2) or replaced by an agreed decision.

Every status is verified in code unless marked **Assumption**. Section numbers refer to the v2 design
document.

## 1. Summary

| # | Feature (v2 section) | Status | Phase |
| --- | --- | --- | --- |
| 1 | Sheet sync, validation, all-or-nothing (§3, §4) | IMPLEMENTED | — |
| 2 | Sync status history on the timeline (§4) | IMPLEMENTED | — |
| 3 | Removed rows: kept, `not_in_sheet`, exit sequence (§4) | PARTIAL (exit part needs sequences) | 02 |
| 4 | Clients grouped by email (§5) | PARTIAL (no metrics) | 06 |
| 5 | Session login, roles, CSRF, rate limit (§8, §11) | IMPLEMENTED | — |
| 6 | Four-eyes approval (§6) | MISSING | 02 |
| 7 | Transactional outbox and worker (§7) | IMPLEMENTED (SKIP LOCKED claiming since M08.1) | — |
| 8 | PostgreSQL 16, Docker Compose (§9) | IMPLEMENTED in M08.1 (data cut over and verified) | 01 (M08.1) |
| 9 | Append-only timeline (§5, §11) | IMPLEMENTED for sync, draft and send events; new event types per phase | 02–04 |
| 10 | Sequences, steps, enrollments, versioning (§3, §6) | MISSING | 02 |
| 11 | Sequence tick, one step per tick, cooldown (§6, §7B) | MISSING | 02 |
| 12 | Gemini draft with sequence context (§7C) | REQUIRES MIGRATION (tone source changes) | 02 |
| 13 | Threaded Gmail delivery (§6) | MISSING | 02 |
| 14 | Tasks (§5) | MISSING | 02 (model), 04 (all kinds) |
| 15 | Gmail read scope and reply polling (§4, §7E) | MISSING | 03 |
| 16 | Reply classification and triage (§6, §7F) | MISSING | 03 |
| 17 | Promise-to-pay (§6, §7G) | MISSING | 04 |
| 18 | Dispute cases (§6) | MISSING | 04 |
| 19 | Closed-loop UI routes (§10) | PARTIAL | 02–05 |
| 20 | Client metrics, risk score, worklist (§3, §6) | MISSING | 06 |
| 21 | Statement emails (§3) | MISSING | 07 |
| 22 | Aging, DSO, CEI, attribution, forecast (§3, §7H) | MISSING | 07 |
| 23 | Scheduling: daily run, 15-minute tick, 5-minute poll (§7J) | PARTIAL (daily run only) | 02, 03 |
| 24 | Integrations health page (§8, §10) | PARTIAL (Gmail status endpoint only) | 03 |
| 25 | AI evaluation harness (§12) | MISSING | 08 |
| 26 | Hypothesis, import-linter, Playwright, CI (§9, §12) | MISSING | 08 |
| 27 | Observability: structlog, `/metrics` (§9) | MISSING | 08 (scoped down, see §5) |
| 28 | Inbound body retention purge (§11) | MISSING | 03 |
| 29 | Payment reconciliation (§3 P2, §7I) | DEFERRED (owner decides later) | 09 |

## 2. Implemented foundation (verified)

**1 · Sheet sync.** All-or-nothing validation, row-numbered errors, upsert by `external_id`, no transaction
during the fetch. Files: `invoices/sheet_parser.py`, `sync.py`, `sheets_client.py`. Tests: `test_parser.py`,
`test_sync.py`. **Remaining gap:** after a sync, also queue enrollment reconciliation and (Phase 06) metric
recomputation.

**2 · Status history.** Each sync writes `INVOICE_CREATED`, `INVOICE_UPDATED {changes}`,
`INVOICE_REMOVED_FROM_SHEET` and `INVOICE_RETURNED_TO_SHEET` events. `UNPAID → PAID` can be queried with
`payload__changes__status__to="PAID"` (`timeline/tests/test_timeline.py`). Promise checks and attribution
can rely on it.

**5 · Accounts.** `accounts/` provides roles, Argon2, CSRF on login, 5 failures per 15 minutes, and
`create_user`. The route-wide test covers 401 without a session and 403 without a role. **Gap:** no
endpoint declares a role above OPERATOR yet. Sequence writes (ADMIN) and dispute resolution (APPROVER)
arrive with their features.

**7 · Outbox and worker.** `automation/outbox.py`, `run_worker`. Jobs: `invoices.sync`, `followups.generate`,
`followups.deliver` (max 1 attempt), `automation.run`. There is a threaded test for single-claim and for at
most one Gmail call. **Gap:** no periodic scheduling inside the worker (feature 23).

**9 · Timeline.** `TimelineEvent` with a PostgreSQL trigger blocking UPDATE, DELETE and TRUNCATE. **Gap:** event types for enrollments, tasks, replies,
promises, disputes and overrides are added in the phase that introduces each.

## 3. Feature details (gaps)

Each entry: **v2 requirement · current · files · gap · changes · dependencies · acceptance.**

### 3 · Removed rows exit their sequence
- **v2:** a removed invoice is kept, marked `not_in_sheet`, and exits its sequence.
- **Current:** `in_sheet` is maintained and eligibility blocks it (`eligibility.missing_from_sheet`).
- **Gap:** sequences don't exist yet.
- **Change:** enrollment reconciliation exits enrollments with `exit_reason="not_in_sheet"`.
- **Depends on:** 10.
- **Accept:** removing a row exits its enrollment in the same sync job, and a timeline event is written.

### 4 · Clients
- **v2:** client with risk score, factors, average days late, promise-kept rate, response rate,
  `metrics_computed_at`.
- **Current:** `Client(email, display_name)`.
- **Gap:** metrics fields and their computation.
- **Change:** Phase 06 adds the fields (nullable, so no backfill risk) and an `insights` recompute job.
- **Depends on:** 17 and 15 (promise-kept and response rates).
- **Accept:** see feature 20.

### 6 · Four-eyes approval
- **v2:** a firm tone, a statement, or an amount ≥ `HIGH_VALUE_THRESHOLD` (5,000 in the invoice currency)
  needs an APPROVER who is not the follow-up's last editor. Otherwise OPERATOR can approve. Errors:
  403 `approver_required` / `self_approval`.
- **Current:** `services.approve()` lets any role approve. `FollowUp.last_edited_by` and `approved_by` exist.
- **Files:** `followups/services.py`, `followups/views.py`.
- **Change:** an `approval_requirement(follow_up)` policy in `accounts`, enforced in `approve()` inside the
  locked transaction. Expose `required_role` and `approval_blocked_reason` on the serializer so the UI can say
  why approval is unavailable.
- **Depends on:** none (statements are added in Phase 07).
- **Accept:** design scenario "Operator edits a firm draft, then approves it → 403 self_approval; a
  different approver can approve".

### 10 · Sequences, steps, enrollments
- **v2:** admin-defined 2–5 step sequences (offset days, tone, channel EMAIL/TASK, `requires_approver`).
  A version is immutable once used. At most one ACTIVE or PAUSED enrollment per invoice. Default: +1
  friendly, +8 professional, +15 firm (approver), +30 call task.
- **Current:** none. One-off follow-ups; tone from days overdue; cooldown in `automation/policy.py`.
- **Change:** new app `sequences` with the models, a default seeded by a data migration, an enrollment
  reconciliation service and APIs (`/api/sequences/`, `/api/enrollments/`, pause and resume).
- **Data:** existing invoices are enrolled by the first reconciliation run. History is preserved: the
  starting step is derived from the number of SENT follow-ups, so an invoice already chased twice does not
  restart at "friendly".
- **Depends on:** 7, 9.
- **Accept:** design scenario "Invoice becomes overdue → enrolled; step 1 draft at due + 1 with friendly
  tone".

### 11 · Sequence tick
- **v2:** every 15 minutes and after each sync, lock ACTIVE enrollments that are due (at most
  `AUTOMATION_MAX_DRAFTS_PER_RUN`), re-check eligibility, use the step's tone and queue a draft. Never
  approve or send. `next_action_at = due + offset`, never earlier than last send + 7 days. A step's draft
  requires the previous step SENT or REJECTED. FAILED or DELIVERY_UNKNOWN blocks. One step per tick.
- **Current:** the orchestrator evaluates every invoice with `policy.evaluate` and drafts directly.
- **Gap:** a full replacement of the draft-selection logic (REQUIRES MIGRATION of `orchestrator.py` and
  `policy.py`).
- **Change:** a `sequences.tick` service and job. The orchestrator's evaluate and draft steps call
  reconciliation plus a tick instead of `policy.evaluate`. `WorkflowStep` and `AutomationRunItem` are
  reused, so the `/automation` workspace keeps working.
- **Depends on:** 10.
- **Accept:** design scenarios "Machine off for 3 days → one step only" and "Scheduled run never approves
  or sends". Existing workflow tests still pass after adapting them to sequence semantics.

### 12 · Drafting with sequence context
- **v2:** Gemini receives the client name, invoice number, amount, currency, due date, days overdue, step
  number, previous send date and any broken-promise date. The backend picks the tone. `prompt_version` is
  stored.
- **Current:** `prompts.build_context` with v1 fields; tone from `tone_for_days_overdue`; `prompt_version`
  `draft-v1`.
- **Change:** `draft-v2` prompt adding the step number and previous send date (the broken-promise date
  arrives in Phase 04). Tone comes from the enrollment's step plus its escalation level (capped at FIRM).
  Legacy follow-ups keep their stored tone and version.
- **Accept:** a tone mismatch is still rejected; the prompt context contains no email address.

### 13 · Threaded delivery
- **v2:** steps after the first set `In-Reply-To` and `References` to the previously sent message and stay
  in its Gmail thread.
- **Current:** `GmailClient.send(recipient, subject, body)` sends no thread ID or reply headers. Stored:
  Gmail message ID and thread ID only, no RFC `Message-ID`.
- **Change:** `send(..., thread_id, in_reply_to, references)`. Store `rfc_message_id` on `DeliveryAttempt`.
  `FollowUp.thread_reply_to` → the previous sent attempt.
  - **Getting the RFC Message-ID:** preferred is generating it ourselves (`email.utils.make_msgid`) when
    building the message. **Assumption to verify live:** Gmail keeps a client-supplied `Message-ID` on
    `messages.send`. Fallback: read it with `messages.get(format=metadata)`, which needs `gmail.metadata`
    or `gmail.readonly` (the Phase 03 scope, pulled forward).
  - **Subject:** Gmail threads by `threadId` plus a matching subject, so threaded steps use
    `Re: <first subject>` and the subject is locked in the editor.
- **Depends on:** 10.
- **Accept:** design scenario "Step 1 sent, no reply, 7 days pass → step 2 draft replies in the same Gmail
  thread", verified once live to an allowlisted inbox.

### 14 · Tasks
- **v2:** `Task(invoice, client, kind CALL / UPDATE_CONTACT / SEND_INFO / RECONCILE, assignee, due_at,
  done_at)`.
- **Current:** none.
- **Change:** Phase 02 adds the model (in a new `inbox` app, its owner per §9) with `CALL` for sequence
  step 4, plus `/api/tasks/` and `/api/tasks/{id}/done/`. The other kinds are created by Phase 03/04 reply
  actions.

### 15 · Reply ingestion
- **v2:** `gmail.readonly` on the test mailbox; `users.history.list` every 5 minutes from the stored
  historyId, with a 7-day scan of known threads when it expires. Only threads with a SENT message are
  ingested. Store the message ID (unique), thread ID, sender, received time, subject and body (8 KB,
  quotes and signatures stripped). Skip own messages; no attachments. Purge bodies after 90 days.
- **Current:** none. The Gmail client is send-only, and `authorize_gmail` requests `gmail.send` plus
  identity scopes.
- **Change:** extend the scopes and re-authorize; add a read client, an `InboundMessage` model, a poll job
  and a historyId cursor (`MailboxCursor`); dedupe by the unique message ID; purge command.
- **Depends on:** 13 (known threads).
- **Accept:** the same reply polled twice is stored once; mail in unknown threads is never stored.

### 16 · Classification and triage
- **v2:** Gemini returns `{label, confidence, extracted, rationale}` against a strict schema; the reply sits
  in a `<client_reply>` block. At confidence ≥ 0.8 the backend applies the label's action. Low confidence,
  a schema violation or several invoices go to triage. A human can override; the same action runs.
- **Current:** none.
- **Change:** `ReplyClassification`, a classify job, an action dispatcher (single transaction) and triage
  APIs.
- **Depends on:** 15 for input; the actions depend on 17, 18 and 14. Phase 03 can apply only the actions
  whose targets exist (tasks, out-of-office rescheduling, pause) and route PROMISE and DISPUTE to triage
  until Phase 04.
- **Accept:** design scenarios for the prompt-injection reply and the low-confidence reply.

### 17 · Promise-to-pay
- **v2:** OPEN → KEPT (PAID by date + 2 days, from sync), BROKEN (grace passed and still UNPAID; resume one
  tone firmer, draft references the missed date) or CANCELLED. At most one OPEN per invoice. A past date or
  one more than 60 days out goes to triage.
- **Current:** none.
- **Change:** model, daily `check_promises` job, enrollment pause and resume with an escalation level.
- **Depends on:** 10, 16.

### 18 · Disputes
- **v2:** a case with reason, owner, notes and status OPEN / RESOLVED_VALID / RESOLVED_INVALID. While it is
  open: no drafts, approvals or sends (409 `invoice_disputed`). Resolving invalid resumes the enrollment;
  resolving valid exits it. Resolution needs an APPROVER.
- **Current:** none.
- **Change:** model; an `open_dispute` rule added to `eligibility.ineligibility()`, which already gates
  generate, approve, send and the worker re-check, so one rule covers every path.
- **Depends on:** 10.

### 19 · UI routes
- **v2 routes:** `/login`, `/` (Today), `/invoices`, `/invoices/[id]`, `/clients`, `/clients/[id]`, `/inbox`,
  `/approvals`, `/disputes`, `/analytics`, `/sequences`, `/automation`, `/settings/integrations`. Also: the
  explicit unavailable states, j/k keyboard navigation, 375 px layouts and WCAG AA.
- **Current:** `/login`, `/` (v1 dashboard), `/automation`.
- **Change:** each phase ships the UI for its own backend (see the roadmap). Phase 05 consolidates the
  invoice detail page, approvals and the sequence editor. Phase 06 makes `/` the Today worklist; the v1
  dashboard content moves to `/invoices`.

### 20 · Risk score and worklist
- **v2:** a 0–100 score (oldest days overdue ≤ 35, broken promises in 180 days ≤ 25, average days late
  ≤ 20, no reply to the last 2 sends ≤ 10, open dispute 10), recomputed after sync and after replies.
  The worklist is sorted by risk × outstanding amount, with the top factors and a suggested action.
- **Current:** none.
- **Depends on:** 15, 17, 18.
- **Accept:** the score is reproducible from stored facts; a factor breakdown is shown.

### 21 · Statement emails
- **v2:** for a client with 2 or more eligible invoices, one consolidated draft with backend-computed lines
  and total. It always requires an approver.
- **Current:** none; `FollowUp` is tied to one invoice and has no `kind`.
- **REQUIRES MIGRATION:** add `FollowUp.kind` (INVOICE/STATEMENT, default INVOICE) and a `StatementLine`
  model. The "one active per invoice" constraint must also cover statement lines. Enforce it in the
  service, because partial unique indexes cannot span tables.
- **Depends on:** 6, 10.

### 22 · Analytics and forecast
- **v2:** aging buckets, DSO, CEI, reminder-to-payment attribution, and a 30-day forecast
  (outstanding × P(paid in 30 days); an open promise uses the promise-kept rate; otherwise the aging
  bucket plus average days late; fewer than 3 paid invoices fall back to portfolio averages).
- **Current:** dashboard totals only.
- **Depends on:** 2 (paid dates from the timeline), 17, 20.

### 23 · Scheduling
- **Current:** a daily cron `run_automation`.
- **Gap:** the 15-minute tick (Phase 02), the 5-minute reply poll (Phase 03) and the daily promise check
  (Phase 04).
- **Decision proposed:** `run_worker` enqueues these periodic jobs itself, keyed by time slot, instead of a
  separate `run_scheduler` process. Tick and daily runs share the automation lease.

### 24 · Integrations health
- **v2:** Sheets, Gmail send, Gmail read, Gemini quota used today, worker heartbeat, last error code.
- **Current:** `GET /api/integrations/gmail/status/` (network call in the request) and `worker_alive` on job
  responses.
- **Change:** Phase 03 adds `/api/integrations/status/`, built from stored results (last job outcomes,
  heartbeat, attempt counts), so it makes no provider calls in the request.

### 25 · AI evaluation harness
- **v2:** `run_ai_evals`; 40 drafting scenarios and 80 labelled replies in `backend/evals/`. Deterministic
  draft checks block; an LLM judge is advisory. Gates: accuracy ≥ 90 %, DISPUTE recall ≥ 95 %, zero
  successful injections. CI uses recorded responses.
- **Current:** none (unit tests use fakes).
- **Note:** the Gemini free tier (about 20 requests/day) cannot run 120 live calls in a day. Record
  responses over several days, or use a paid key once.

### 26 · Engineering quality
- **Missing:** Hypothesis state-machine tests, import-linter contracts (automation, sequences and inbox
  must not import `followups.delivery`), ruff, mypy, Playwright end-to-end, frontend component tests,
  GitHub Actions.
- **Note:** v2's "concurrency tested against real Postgres" is in place since M08.1 (threaded tests with
  separate PostgreSQL connections: SKIP LOCKED, single claim, one approval winner, at most one Gmail call).

### 27 · Observability
- **v2:** structlog JSON logs with request and job IDs, a local Prometheus `/metrics` endpoint.
- **Current:** standard logging (IDs and codes only).
- **Proposal:** add request and job IDs to log records (cheap). Treat `/metrics` as optional; it is not
  part of any acceptance scenario.

### 28 · Inbound retention
- Purge `InboundMessage.body_text` after `INBOUND_RETENTION_DAYS` (90) and keep classifications. A daily
  job in Phase 03.

### 29 · Reconciliation (P2)
- DEFERRED until P0, P1 and Phase 08 pass; the owner decides later.

## 4. Data migration impact

| Change | Phase | Data risk | Strategy |
| --- | --- | --- | --- |
| New tables (sequences, enrollments, tasks, inbound, classifications, promises, disputes) | 02–04 | None | Additive |
| Default sequence seed | 02 | None | Data migration, idempotent `get_or_create` |
| Enrolling existing invoices | 02 | Medium: could restart chasing | Starting step derived from SENT history; enrollment at most one step per tick; the first run is previewable with `run_automation --dry-run` |
| `FollowUp.enrollment`, `step`, `thread_reply_to`, `kind` | 02, 07 | Low | Nullable FKs; `kind` defaults to INVOICE; legacy rows untouched |
| `DeliveryAttempt.rfc_message_id` | 02 | None | Nullable; legacy sends thread by `gmail_thread_id` only |
| Client metric fields | 06 | None | Nullable, computed later |
| Gmail scope change | 02/03 | Operational: needs re-authorization | `authorize_gmail` again; the status check reports the missing scope |

No planned migration deletes or rewrites existing rows. The timeline triggers prevent deleting history.

## 5. Conflicts and decisions to confirm

1. **Tone source.** v2 replaces tone-by-days-overdue with tone-by-step. An invoice first enrolled at 40
   days overdue therefore starts with a friendly step 1, as the design's enrollment table and acceptance
   scenario specify. This is recommended. Invoices with v1 send history start after the steps already sent
   (see the Phase 02 plan). The alternative, starting at the most recently passed offset, needs an explicit
   decision.
2. **Manual "Analyze".** With sequences, Analyze drafts the enrollment's current step (enrolling first if
   needed) instead of choosing a tone from days overdue.
3. **The `/automation` workspace** keeps its step tracker. "Evaluate" becomes "reconcile enrollments", and
   "Draft" becomes "sequence tick".
4. **Observability** is scoped down (§3, feature 27).
5. **PostgreSQL and Docker Compose** are adopted (M08.1). **`run_scheduler`** is folded into `run_worker`
   (proposed in the Phase 02 plan).
