# 05 — M09 implementation report: sequences, threaded delivery, four-eyes, call tasks

- **Date:** 2026-10-22
- **Baseline:** `86c2bc0` (M08.1, PostgreSQL 16 under Docker Compose)
- **Plan:** `03-phase-02-implementation-plan.md` plus the M09 authorization's adjustments
- **Status:** complete and deployed to the local Compose stack. **Live Gmail threading
  verification is prepared but not run**: it needs a test send, which awaits explicit
  authorization (section 8).

## 1. Behaviour

| Area | What it does now |
| --- | --- |
| Sequences | Versioned cadences of 2–5 steps (offset from the due date, EMAIL with a tone or a call TASK, approver flag). Default "Standard collection" v1: +1 friendly, +8 professional, +15 firm (approver), +30 call task. A used version is never edited; saving creates a new version. |
| Enrollment | After each successful sync (and in every tick or run), eligible overdue invoices are enrolled; paid or removed invoices leave (`EXITED`). One live enrollment per invoice, enforced by the database. An invoice that completed its sequence is not re-enrolled. |
| Progression | `last_completed_step` and `pending_step` are separate. A step completes only when its reminder is `SENT`, a person `REJECTED` its draft, or its task is done. `DRAFT`, `APPROVED` and `SENDING` wait; `DELIVERY_UNKNOWN` and `FAILED` hold the step (`blocked_reason`). The next step is due at due date + offset and never before the last send + 7 days. A tick starts **at most one new step** per enrollment. |
| Existing history | The position comes from the reminders actually sent. Clean history continues after them. Mismatched history (tones that don't match the first steps, more sends than email steps, an unresolved delivery) is flagged `review_required`. It continues only if the next step is neither firm nor approver-only; otherwise it is paused until an approver confirms the position (`POST /api/enrollments/{id}/review/`). Historical follow-ups are never modified. |
| Drafting | The tone comes from the step, raised by `escalation_level` (capped at FIRM; used from Phase 04). The `draft-v2` prompt adds the step number, total email steps and the previous reminder date. Eligibility is re-checked when the draft is saved. Manual "Draft step N" drafts the next step (enrolling first) even before it is due, but never past a pending step, an unknown delivery, a pause or a pending review. |
| Threading | Every send carries a CollectFlow-generated `Message-ID` (stored before Gmail is called, so it is known even if the outcome is not). Later steps send with Gmail `threadId`, `In-Reply-To` and `References` (the chain grows per step), and the subject is fixed to `Re: <original subject>`. Editing it returns `400 subject_locked`. |
| Four-eyes | A firm tone, an approver step, or an amount ≥ `HIGH_VALUE_THRESHOLD` (5,000, in the invoice currency) needs an APPROVER who is not the last editor. Refusals are `403 approver_required` or `403 self_approval`, enforced in `followups.services.approve` (also for internal calls without a user). Each follow-up's `approval` block lists the reasons and why the signed-in user can't approve. |
| Tasks | `inbox.Task` (CALL in this phase): one per enrollment step (unique index); list and mark done; done completes the step on the next tick. |
| Scheduling | `run_worker` queues one `sequences.tick` per 15-minute slot (key = slot start: restart-safe and multi-worker-safe; no back-fill). A successful sync queues a tick. Ticks hold the automation lease and run as `SEQUENCE_TICK` runs. Abandoned tick runs are recovered like automation runs. **Ticks never approve or send.** |
| Automation run | The orchestrator's *evaluate* step is enrollment reconciliation; *draft* is the tick. Run items, reason codes (`stale_draft`, `delivery_unresolved`, `cooldown`, `recently_rejected`…) and the `/automation` workspace are preserved. `run_automation --dry-run` and `tick_sequences --dry-run` preview steps. |
| UI | Invoice table: per-invoice sequence state and "Draft step N". Drawer: step strip, held reasons, pause/resume, approver history review, threaded subject lock, four-eyes explanation, and "why you can't draft" messages. Approval queue: step and "Needs approver" badges. Dashboard: open call tasks and a stale-data banner (26 h). New `/sequences` page: versions on a day timeline with per-step counts; admins can save a new version. |

## 2. Data model and migrations (all additive)

| Migration | Contents |
| --- | --- |
| `sequences/0001_initial` | `Sequence`, `SequenceStep`, `SequenceEnrollment`; constraints: unique name+version, one active default, step order 1–5, tone matches channel, one live enrollment per invoice, `pending_step > last_completed_step` |
| `sequences/0002_default_sequence` | Seeds "Standard collection" v1 (idempotent). The reverse removes it only if unused (tested by a real rollback) |
| `inbox/0001_initial` | `Task` with "one task per enrollment step" |
| `followups/0004_sequence_and_threading` | `FollowUp.enrollment`, `.step`, `.thread_reply_to` (nullable); `DeliveryAttempt.rfc_message_id`, `.rfc_references` |
| `invoices/0005_sequence_tick_runs` | `AutomationRun.Kind.SEQUENCE_TICK` (choices only) |
| `timeline/0002_sequence_events` | New event types (choices only) |

No migration rewrites or deletes data. Enrollments are created by the engine at run time, not by a
migration.

## 3. Deviations from the Phase 02 plan, and why

1. **Progression state:** `last_completed_step` + `pending_step` (+ `blocked_reason`,
   `drafting_started_at`) instead of a single `current_step`, per the authorization: creating a draft is
   not completing a step.
2. **History placement:** count-based with ambiguity detection and a firm-step guard (authorization),
   instead of "start after the number of sends" unconditionally. A recent pre-sequence rejection keeps the
   v1 `recently_rejected` cooldown.
3. **No `SEQUENCES_ENABLED` fallback flag:** two drafting engines writing to the same invoices would leave
   enrollments out of step with follow-ups. Rollback is a code revert plus reverse migrations (section 7).
4. **Gemini is called inside the tick job** (itself a worker job) rather than as one queued job per step.
   Generation runs after the enrollment row's transaction commits, with `drafting_started_at` marking the
   step; the outcome is the same.
5. **Sequence editor:** a simple new-version form; the visual day-timeline editor remains Phase 05.
6. **Default-sequence recovery:** if no sequence exists at all, `default_sequence()` re-creates the standard
   one. If a deactivated one exists, it refuses rather than guesses.
7. **Approval without a user** (internal callers) is refused for four-eyes reminders.

## 4. Changed files

- **Backend, new:**
  - `apps/sequences/` (models, engine, services, serializers, views, urls, `tick_sequences`, migrations, tests)
  - `apps/inbox/` (Task model, services, serializers, views, urls, migration)
  - `apps/accounts/approval.py`
  - `apps/automation/scheduling.py`
  - `apps/core/tests_architecture.py`
- **Backend, changed:**
  - `followups/{models,services,delivery,serializers,views,prompts}.py`
  - `integrations/gmail.py`
  - `automation/{orchestrator,outbox,lease,workflow,views}.py`
  - `run_worker`, `run_automation`
  - `invoices/{models,serializers,views,sync}.py`
  - `timeline/models.py`
  - `config/{settings,urls}.py`
  - `.env.example`
- **Frontend, new:**
  - `lib/api/{sequences,tasks}.ts`
  - `components/dashboard/{SequencePanel,TasksPanel}.tsx`
  - `components/sequences/SequencesView.tsx`
  - `app/sequences/page.tsx`
- **Frontend, changed:**
  - `lib/api/{followups,invoices}.ts`
  - the proxy allowlist
  - `DraftEditor`, `FollowUpDrawer`, `ApprovalQueue`, `InvoiceTable`, `Dashboard`, `AppHeader`
- **Docs:**
  - `README.md` (new "Collection sequences" section, API, settings)
  - `docs/SCHEDULING.md`
  - `docs/v2/01`, `02`, `03` (status)
  - this report

## 5. Tests and checks

| Check | Result |
| --- | --- |
| Backend suite on PostgreSQL 16 (Compose) | **323 tests, OK** (269 before + 54 new) |
| `manage.py check` / `makemigrations --check --dry-run` | No issues / no changes |
| Frontend `lint`, `typecheck`, `build` | All pass; new route `/sequences` |

New tests:
- **Engine** (`apps/sequences/tests/test_engine.py`, 35 tests):
  - the v2 scenarios: overdue → step 1 friendly; step 1 sent + 7 days → step 2 in the same Gmail
    thread; machine off → one step per tick;
  - every pending outcome (DRAFT, APPROVED, SENDING, UNKNOWN, FAILED, SENT, REJECTED);
  - cooldown, idempotent ticks, generation failure retried, draft limit, call task created once and
    completion, exit on paid or removed, no re-enrollment after completion;
  - history: clean, ambiguous non-firm (flagged), ambiguous firm (paused), unresolved delivery, adopted
    in-progress draft, recent rejection, history untouched;
  - manual drafting rules, the threaded subject lock, the growing References chain;
  - scheduled tick never sends or approves, slot idempotency, lease skip, sync queues a tick.
- **API** (`test_api.py`, 13 tests):
  - the four-eyes matrix, including v2's "Operator edits a firm draft, then approves → 403
    self_approval; a different approver can approve";
  - sequences: admin-only create, versioning, validation;
  - enrollments: pause/resume and approver review;
  - invoice enrollment field; tasks; dashboard staleness.
- **PostgreSQL concurrency** (`test_concurrency.py`, real threads and connections): parallel
  enrollment yields one enrollment; parallel ticks draft a step once (one Gemini call); parallel ticks
  create a task once.
- **Architecture boundary** (`apps/core/tests_architecture.py`): `sequences`, `inbox` and `automation`
  import neither `apps.followups.delivery` nor `integrations.gmail`.
- **Gmail adapter:** threaded send sets `threadId`, `Message-ID`, `In-Reply-To`, `References`; a first
  message has none of the reply headers.

**Adapted existing tests** (intended behaviour changes only):
- A never-contacted overdue invoice drafts step 1 FRIENDLY (was tone by days overdue).
- `draft-v2` prompt version.
- Fake Gmail records the threading fields.
- `run_worker --once` also runs the current tick slot.
- A successful sync's tick run appears among automation runs.

## 6. Verification on real data

The dev copy (`collectflow_dev`, restored from a backup of the real database) was used during
development. The real database was then upgraded through `docker compose up -d --build`, after a
`pg_dump` to `backend/backups/collectflow-before-m09-20261022T154245.dump`.

| Invoice | Sent before | Placement | Next step |
| --- | --- | --- | --- |
| INV-002 | 1 friendly | Clean: step 1 done | Step 2 (professional), due 2026-10-23 (cooldown) |
| INV-001, 005, 006, 007 | 1 professional (v1 chose tone by days overdue) | Step 1 done, **flagged for review** (tone mismatch); continues because step 2 is not firm | Step 2, due 2026-10-23 (INV-001: 2026-10-26) |
| INV-003 | — | Not enrolled (not overdue yet) | — |
| INV-004 | — | Not enrolled (paid) | — |

The first scheduled tick in the worker container did this with **0 drafts, 0 Gemini calls and 0 Gmail
calls**. The 6 historical follow-ups are unchanged, and the timeline gained 5 `ENROLLED` events. An approver can
confirm or correct the four flagged positions from the invoice drawer.

**Incident during development:** one `migrate` ran against the real database (the shell environment
pointed there) before the code was final. It was noticed immediately; a `pg_dump` was taken
(`collectflow-before-m09-rollback-20261022T151537.dump`) and the M09 migrations were reversed. This
exposed and fixed a bug in the seed migration's reverse step. The database was verified back at its
M08.1 state (no M09 tables or migration records; data intact) before development continued on a
separate `collectflow_dev` database.

## 7. Rollback

1. `docker compose stop api worker web`.
2. Reverse the migrations: `migrate inbox zero`, `migrate followups 0003`, `migrate sequences zero`,
   `migrate invoices 0004`, `migrate timeline 0001`. All reverse steps are tested; the timeline keeps its
   events because its table is append-only, and unknown event types remain readable.
3. Check out `86c2bc0` and `docker compose up -d --build`.
4. Alternatively, `pg_restore` the pre-M09 dump into an empty database (this loses writes made since).

Do **not** reverse the migrations after reminders have been drafted under sequences without exporting
them first: `followups 0004` drops the step and thread links (the follow-ups themselves stay).

## 8. Live Gmail threading verification (prepared, not run)

Automated tests cover the headers and `threadId`. One assumption is still unverified live: **Gmail keeps
a client-supplied `Message-ID`**. To check it, with authorization:

1. Pick an allowlisted test invoice whose step 1 reminder is approved, and send it from the UI.
2. Make step 2 due: wait 7 days, or on a test copy move the first reminder's `sent_at` back. Draft
   step 2, approve it, send it.
3. In the sender's Gmail, open both messages with "Show original" and confirm:
   - the `Message-ID` matches `DeliveryAttempt.rfc_message_id`;
   - step 2's `In-Reply-To` equals step 1's `Message-ID`;
   - both sit in one conversation in the sender's and the recipient's mailboxes.

If Gmail replaced the `Message-ID`, the fallback is to read the real header after sending with
`messages.get(format=metadata)`. That needs the `gmail.metadata` scope and a re-run of
`authorize_gmail`, which needs separate confirmation. No OAuth scope was changed in M09.

## 9. Known limitations and remaining risks

- **Live threading is unverified** (section 8). Gmail threads by `threadId` and subject anyway, but
  recipients' mail clients rely on `In-Reply-To`/`References`.
- **Flagged histories** (4 real invoices) continue at a non-firm step without a person's confirmation.
  Only firm or approver steps wait. Review them from the drawer.
- **Gemini quota:** from 2026-10-23, five step-2 drafts become due at once. **Addressed in M09.1**
  (section 10): a daily budget, a global quota cooldown, per-step exponential backoff, no automatic
  retry of non-retryable errors, and no in-call retry of HTTP 429.
- **REJECTED completes a step** (v2 rule). Rejecting a step-2 draft moves on to step 3, which is firm
  and needs an approver. People should regenerate rather than reject when they want different wording.
- **Escalation level** is stored but only raised by broken promises in Phase 04.
- **Sequence editing** is a basic form; per-step validation messages come from the API.
- **Development database:** `collectflow_dev` exists on the Compose server for M10 development. Drop it
  with `docker compose exec db dropdb -U collectflow collectflow_dev` if it isn't wanted.

## 10. M09.1 patch: Gemini spending guard (2026-10-22)

Added before the first scheduled drafting cycle (five step-2 drafts due 2026-10-23):

| Safeguard | Behaviour |
| --- | --- |
| Error classes | The adapter distinguishes `ai_quota_exhausted` (HTTP 429), `ai_unavailable` (5xx, timeout, network), `ai_not_configured` and `ai_bad_response` (invalid output, rejected request). **429 is no longer retried inside a call**; 5xx still is, briefly. |
| Daily budget | Automatic drafting stops once `AI_DAILY_GENERATION_BUDGET` (15) attempts were made in a rolling 24 h, counting manual drafts. Due steps are not started, so nothing is marked as failed. |
| Quota cooldown | After a quota error, all automatic drafting pauses for 30 min × 2^(n−1) after the n-th consecutive quota error (cap 24 h). The first quota error in a tick stops further Gemini calls in that tick. A successful generation (e.g. a manual one) ends the cooldown. |
| Per-step backoff | A transient failure retries that step after 15 min × 2^(n−1) (cap 24 h); `retry_after` and `generation_failures` are on the enrollment and reset on success. |
| Non-retryable | Invalid output, missing configuration and unexpected errors set `blocked_reason=generation_failed`; ticks never retry them. A person drafts manually, which clears the hold. |
| Visibility | The dashboard API returns `ai_budget {used_24h, limit, cooldown_until, automatic_drafting_blocked}`, and the dashboard shows a banner when automatic drafting is paused. The sequence panel explains held steps and the next retry time. |

Migration `sequences/0003_generation_backoff` (two additive fields). Tests: `apps/sequences/tests/test_ai_budget.py`
(9 tests) plus an adapter test for "429 classified and not retried".

## 11. M09.2 fix: step outcomes recorded immediately (2026-10-22)

Found during the threading test: after step 1 was **sent**, the enrollment still showed step 1
in progress, and the UI offered "Draft step 1". The engine recorded outcomes only on the next tick
(up to 15 minutes later). Drafting would still have produced step 2, because the manual planner
resolves the pending step first, but the label and step strip were wrong.

Fix: the follow-up service now calls registered *outcome listeners* after commit (sent, failed,
unknown, released, rejected or withdrawn, reconciled). The sequences app registers
`engine.on_follow_up_outcome`, which records the step's result under the enrollment's row lock.
It never starts a step (no Gemini call, no task); if the next step is already due, it marks it due
for the next tick. Regenerating a draft does not complete a step. The dependency still points from
sequences to follow-ups. Tests: `ImmediateOutcomeTests` (4). Suite: 337 tests OK.

## 12. M09.3 fix: worker survives database restarts; queued drafts can be followed (2026-10-22)

Found during the threading test:

- **Worker crash on a database restart.** Redeploying recreated the `db` container (Compose
  recreates it when `backend/.env` changes, because `db` uses the same env file). A worker
  connected to it exited on `OperationalError`, leaving queued jobs unprocessed. `run_worker` now
  catches connection errors, waits with backoff (2 s doubling, max 60 s) and reconnects. A job
  interrupted mid-way stays RUNNING and is handled by stale-job recovery (delivery jobs are never
  re-run). Deploy app containers with `docker compose up -d --build --no-deps api worker web` to
  avoid restarting the database.
- **"Already requested" instead of progress.** Retrying a draft while one was queued returned
  `409 generation_in_progress` with no way to follow it. The 409 now includes the queued `job_id`,
  and the frontend waits on that job and shows the normal "drafting" progress.

Tests: worker resilience (1), job_id on the 409 (1). Suite: 338 OK; frontend lint, typecheck and
build pass.

