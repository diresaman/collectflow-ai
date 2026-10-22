# 03 — Phase 02 implementation plan: sequences, threaded delivery, four-eyes (M09)

> **Why Phase 02 and not Phase 01:** Phase 01 (foundation) is already implemented in commit `676b43c`
> (see `00-repository-audit.md`), so its implementation plan is skipped. This document is the
> implementation-ready plan for the next phase. **Nothing here is implemented yet; it awaits approval.**

## 1. Objective

Turn one-off reminders into versioned, multi-step collection sequences. Later steps continue in the
original Gmail thread, and the four-eyes approval rule applies, without weakening any v1 delivery
guarantee.

## 2. Scope

**In scope**
- `sequences` app: `Sequence`, `SequenceStep`, `SequenceEnrollment`; a default sequence seeded by migration.
- Enrollment reconciliation after each successful sync: enroll, exit on PAID or removal, complete.
- Sequence tick: every 15 minutes from the worker, after each sync, and inside automation runs. At most one
  step per enrollment per tick; it never approves or sends.
- Tone from the sequence step (plus an escalation level, used from Phase 04); `draft-v2` prompt.
- Threaded Gmail send: `threadId`, `Message-ID`, `In-Reply-To`, `References`, `Re:` subject.
- Four-eyes approval policy (`approver_required`, `self_approval`).
- `inbox.Task` model with the `CALL` kind (step 4 of the default sequence) and its APIs.
- Manual pause and resume of enrollments.
- Stale-data flag: no successful sync for 26 hours.
- UI for all of the above (section 9).

**Out of scope:** reply ingestion, promises, disputes, statements, risk, analytics, the full drag-and-drop
sequence editor (Phase 05). PostgreSQL is already in place (M08.1).

## 3. Existing code reused

| Module | How it is used |
| --- | --- |
| `followups/services.py` `FollowUpGenerationService` | Same generation path. New optional arguments: `enrollment`, `step`, `tone`, `thread_reply_to`. Validation, snapshot, persistence and timeline unchanged. |
| `followups/eligibility.py` `ineligibility()` | The single gate for tick, analyze, approve, send and worker delivery. Unchanged rules. |
| `followups/delivery.py` | Reservation, release, finish and reconciliation unchanged. `deliver()` passes thread headers to Gmail. |
| `integrations/gmail.py` | `build_message` and `send` gain optional threading parameters. Error mapping unchanged. |
| `automation/orchestrator.py` | Keeps its lease, `WorkflowStep`s and `AutomationRunItem`s. EVALUATING becomes "reconcile enrollments"; GENERATING becomes "sequence tick". |
| `automation/outbox.py`, `run_worker` | New job kinds; the worker also enqueues the periodic tick. |
| `automation/lease.py` | Ticks and automation runs share the automation lease, so they never overlap. |
| `timeline/services.py` | New event types only. |
| `accounts/roles.py` | The approval policy uses `has_role`. |

`automation/policy.py` stops deciding drafts. Its reason codes are kept for run items so the workspace UI
keeps working.

## 4. Data model

### 4.1 New app `sequences`

```
Sequence
  id UUID · name · version (int) · is_default (bool) · active (bool)
  created_by FK user null · created_at
  unique(name, version)
  partial unique: one row with is_default=True and active=True

SequenceStep
  id · sequence FK (CASCADE) · order (1..5) · offset_days (≥ 0, from the due date)
  channel EMAIL | TASK · tone FRIENDLY | PROFESSIONAL | FIRM (null for TASK)
  requires_approver (bool)
  unique(sequence, order)
  checks: EMAIL ⇒ tone not null; offset_days strictly increasing (validated in the service)

SequenceEnrollment
  id UUID · invoice FK (PROTECT) · sequence FK (PROTECT)
  status ACTIVE | PAUSED | COMPLETED | EXITED
  current_step (int, 0 = nothing done yet; the order of the last step acted on)
  next_action_at (datetime, null when waiting on a person)
  escalation_level (int, default 0; raised by broken promises in Phase 04)
  pause_reason · exit_reason · created_at · updated_at · completed_at
  partial unique(invoice) where status in (ACTIVE, PAUSED)
```

A sequence version is **immutable once any enrollment uses it**. Editing creates version n+1, and existing
enrollments keep their version.

**Default seed** (data migration, idempotent): "Standard collection" v1, the default:

| Order | Offset | Channel | Tone | Approver |
| --- | --- | --- | --- | --- |
| 1 | due + 1 | EMAIL | FRIENDLY | no |
| 2 | due + 8 | EMAIL | PROFESSIONAL | no |
| 3 | due + 15 | EMAIL | FIRM | yes |
| 4 | due + 30 | TASK (call) | — | — |

### 4.2 New app `inbox` (only `Task` in this phase)

```
Task
  id UUID · kind CALL | UPDATE_CONTACT | SEND_INFO | RECONCILE (only CALL is created in Phase 02)
  invoice FK null · client FK · enrollment FK null · step FK null
  title · assignee FK user null · due_at · done_at · done_by FK user null · created_at
  partial unique(enrollment, step) where step is not null   ← a tick can never create a task twice
```

### 4.3 Changes to existing models (all nullable or defaulted; no data rewritten)

| Model | Field | Purpose |
| --- | --- | --- |
| `FollowUp` | `enrollment` FK null, `step` FK null | Which step a draft belongs to. Legacy rows stay null. |
| `FollowUp` | `thread_reply_to` FK `DeliveryAttempt` null | The sent message this one continues |
| `DeliveryAttempt` | `rfc_message_id` (char, blank), `rfc_references` (text, blank) | Threading headers for the next step |
| `AutomationRun.Kind` | `SEQUENCE_TICK` | Records periodic ticks |
| `TimelineEvent.type` | `ENROLLED`, `ENROLLMENT_PAUSED`, `ENROLLMENT_RESUMED`, `ENROLLMENT_EXITED`, `ENROLLMENT_COMPLETED`, `STEP_DRAFTED`, `TASK_CREATED`, `TASK_DONE` | Audit |

### 4.4 Migration strategy

1. Take a `pg_dump -Fc` backup of the PostgreSQL database.
2. Additive migrations: `sequences 0001` (tables plus seed), `inbox 0001`, `followups 0004`,
   `invoices 0005` (run kind), timeline choices (no schema change).
3. **No enrollment is created by a migration.** Existing invoices are enrolled by the first reconciliation
   run, which can be previewed with `run_automation --dry-run`.
4. **History-aware starting step:** for an invoice with SENT follow-ups from v1, `current_step` starts at
   the number of SENT reminders (capped at the last EMAIL step). The next draft is then the next step,
   threaded to the latest SENT attempt, so a client already reminded twice gets step 3, not another
   friendly reminder.
5. A never-contacted overdue invoice starts at step 1 (friendly), as the design's enrollment table and
   acceptance scenario specify. It advances at most one step per tick after each previous step is sent or
   rejected. **Decision point:** the alternative, starting at the most recently passed offset, is not
   proposed.

Reverse migrations drop only the new tables and columns; v1 data is untouched.

## 5. Rules and algorithms

### 5.1 Enrollment reconciliation (after each successful sync; in the sync job and the orchestrator)

For each invoice, in one transaction per batch:
- **Exit** an ACTIVE or PAUSED enrollment when the invoice is PAID (`exit_reason=paid`) or not in the
  sheet (`not_in_sheet`).
- **Enroll** an invoice that is eligible (`ineligibility()` empty), overdue and has no ACTIVE or PAUSED
  enrollment into the default sequence. The starting step comes from history (section 4.4), and
  `next_action_at` is computed for the next step.
- **Re-enroll** an invoice that returns to the sheet: a new enrollment is allowed because only ACTIVE and
  PAUSED are unique. History still sets the starting step.

Every change writes a timeline event. A concurrent duplicate enrollment fails on the partial unique index
and is treated as "already enrolled".

### 5.2 Next action time

```
next_action_at(step n) = max( due_date + offset_days(n)            (start of the business day, DJANGO_TIME_ZONE),
                              last SENT reminder for the invoice + FOLLOWUP_COOLDOWN_DAYS )
```

### 5.3 Tick, for one enrollment (ACTIVE and `next_action_at <= now`, oldest first, limit `AUTOMATION_MAX_DRAFTS_PER_RUN`)

1. Re-read the invoice. If PAID or not in the sheet → exit (as in 5.1). If otherwise ineligible → skip
   with the reason code.
2. Look at the invoice's latest follow-up (with or without an enrollment, so v1 history counts):
   - DRAFT or APPROVED → skip `draft_awaiting_review` / `approved_awaiting_send`.
   - SENDING or DELIVERY_UNKNOWN → skip `delivery_unresolved` (blocks until reconciled).
   - FAILED → skip `delivery_failed` (blocks; the operator re-drafts the same step with Analyze).
   - SENT or REJECTED, or none → continue.
3. `n = current_step + 1`. If n is past the last step → COMPLETED (event).
4. Recompute `next_action_at(n)`. If it is in the future → save it and skip `not_due`.
5. **TASK step:** create `Task(CALL)` (idempotent via the unique constraint), set `current_step = n`, and
   compute `next_action_at(n+1)` or complete.
6. **EMAIL step:**
   - tone = the step's tone raised by `escalation_level`, capped at FIRM;
   - `thread_reply_to` = the latest SENT attempt for the invoice (none for a first email);
   - generate the draft through `FollowUpGenerationService` with `enrollment`, `step`, `tone` and
     `thread_reply_to`;
   - on success: `current_step = n`, `next_action_at = null` (waiting on a person). The following step's
     time is computed when this follow-up becomes SENT or REJECTED, on the next tick.
   - on an AI error: leave the enrollment due, so the next tick retries (v2 §11), and record a FAILED
     item.

There is no loop over steps, so a tick advances an enrollment by **at most one step**; three missed days
never cause a burst. The tick runs under the automation lease, so it never overlaps a daily or manual run.

### 5.4 Manual actions

- **Analyze (`POST /invoices/{id}/analyze/`):** if the invoice has no enrollment, enroll it first; then
  draft step `current_step + 1`, or re-draft step `current_step` after FAILED or REJECTED, with the tone
  rules above. It ignores `next_action_at`, since a person asked, but obeys every gate in 5.3 step 2 and
  `ineligibility()`. Regenerate keeps the step and tone.
- **Pause:** ACTIVE → PAUSED with a reason; the tick ignores it. **Resume:** PAUSED → ACTIVE, and
  `next_action_at` is recomputed. Both write events. Both need OPERATOR.

### 5.5 Four-eyes approval (`accounts/approval.py`, enforced in `followups.services.approve`)

```
approval_requirement(follow_up) -> {role, reasons[]}
  APPROVER if any of: tone == FIRM ; step.requires_approver ; invoice.amount >= HIGH_VALUE_THRESHOLD
                      (STATEMENT kind is added in Phase 07)
  else OPERATOR

approve(actor):
  role below the requirement             -> 403 approver_required  (reasons listed)
  APPROVER required and actor == last_edited_by -> 403 self_approval
```

`HIGH_VALUE_THRESHOLD` is a setting, default `5000`, compared in the invoice's own currency as v2 states.
The serializer exposes `approval: {required_role, reasons, blocked_for_you: null | "approver_required" |
"self_approval"}`, so the UI can explain itself. Send and withdraw are unchanged (OPERATOR).

### 5.6 Threaded delivery

- **Message-ID:** `build_message` sets `Message-ID: make_msgid(domain=<sender domain>)`. The ID is stored
  on the attempt before Gmail is called (in the reservation), so it is known even for an unknown outcome.
- **Threaded steps** (`thread_reply_to` set):
  - body `{"raw": …, "threadId": prev.gmail_thread_id}`;
  - headers `In-Reply-To: prev.rfc_message_id`, `References: prev.rfc_references + " " + prev.rfc_message_id`;
  - `rfc_references` is stored for the next step.
  - Legacy v1 attempts have no `rfc_message_id`: send with `threadId` only, which Gmail still threads.
- **Subject:** Gmail joins a thread only when the subject matches, so a threaded draft's subject is set by
  the backend to `Re: <subject of the first message in the thread>`. The model's subject is discarded.
  `PATCH` refuses subject edits with `400 subject_locked`. The UI shows the subject read-only with the
  reason.
- **Verification (first hour of the phase):** send step 1 and step 2 to an allowlisted inbox, then check
  with Gmail's "Show original" that the stored `Message-ID` matches and step 2 sits in the same thread on
  both sides. **If Gmail replaced the Message-ID:** after a send, read the real header with
  `messages.get(format=metadata)`. That needs the `gmail.metadata` scope, so add it to `authorize_gmail` now
  (Phase 03 adds `gmail.readonly` anyway) and re-authorize.

### 5.7 `draft-v2` prompt

The context adds `step_number`, `total_email_steps`, `previous_send_date` (if any) and
`is_follow_up_in_thread`. The instructions add: refer to the earlier reminder without restating it, keep the
pinned tone, and still never invent facts. `PROMPT_VERSION = "draft-v2"`. Stored follow-ups keep their old
version.

### 5.8 Scheduling

- `run_worker` enqueues `sequences.tick` every 15 minutes with idempotency key
  `sequences.tick:<UTC slot start>`, so restarts never double-enqueue. A missed slot is not back-filled;
  the next slot catches up one step.
- A successful sync job enqueues reconciliation plus one tick.
- The daily cron (`run_automation`) runs sync, reconcile and tick inside its workflow, as today.
- If the tick finds the lease held, it succeeds with `{"skipped": "automation_running"}`.
- `python manage.py tick_sequences [--dry-run]` runs one tick from the command line.

### 5.9 Stale data

`GET /api/dashboard/` adds `last_successful_sync_at` and `stale` (no successful sync for 26 hours). Ticks
continue on the last good data, as v2 §11 specifies, and the UI shows a banner.

## 6. API changes

| Method | Path | Role | Notes |
| --- | --- | --- | --- |
| GET | `/api/sequences/` | OPERATOR | Versions with steps and enrollment counts per step |
| POST | `/api/sequences/` | ADMIN | Create a new version (2–5 steps, validated); optionally make it the default for new enrollments |
| GET | `/api/sequences/{id}/` | OPERATOR | Steps and counts |
| GET | `/api/enrollments/` | OPERATOR | Filters `?status=`, `?invoice=`, `?due_before=` |
| POST | `/api/enrollments/{id}/pause/`, `/resume/` | OPERATOR | Body `{reason}`; 409 on the wrong status |
| GET | `/api/tasks/` | OPERATOR | Filters `?open=true`, `?invoice=` |
| POST | `/api/tasks/{id}/done/` | OPERATOR | Idempotent |
| POST | `/api/follow-ups/{id}/approve/` | policy | New 403 codes `approver_required`, `self_approval` |
| PATCH | `/api/follow-ups/{id}/` | OPERATOR | 400 `subject_locked` on threaded drafts |
| GET | `/api/invoices/`, `/api/invoices/{id}/` | OPERATOR | Adds `enrollment {id, status, current_step, total_steps, next_action_at, pause_reason}` |
| GET | `/api/follow-ups/…` | OPERATOR | Adds `step {order, channel, tone}`, `threaded`, `approval {…}` |
| GET | `/api/dashboard/` | OPERATOR | Adds `last_successful_sync_at`, `stale` |

The proxy allowlist and the frontend runtime validators change in the same commit.

## 7. Integration requirements

- **Gmail:** no new scope if the Message-ID check passes; otherwise add `gmail.metadata` and run
  `authorize_gmail` again. `check_ready` reports a missing scope as "not ready" with that instruction.
- **Gemini:** one prompt change (`draft-v2`); quota use is unchanged (one call per draft).
- **Sheets:** unchanged.

## 8. Safety checklist for this phase

- The tick and reconciliation code live in `sequences` and never import `followups.delivery`. The existing
  test that makes `GmailClient.send` fail when called is extended to the tick and the periodic scheduling.
- A tick never approves. Approval stays an explicit API call with the four-eyes check.
- An unknown or failed delivery blocks the enrollment.
- Threading headers are computed by the backend from stored IDs, never from AI output.
- PAID or removed invoices exit at reconciliation and are refused again by `ineligibility()` on every later
  path.

## 9. Frontend changes

- **Invoice table:** sequence column ("Step 2 of 4 · next 3 Nov", or Paused / Completed / Exited with the
  reason).
- **Follow-up drawer:**
  - sequence progress strip (steps with done, current and upcoming);
  - Pause and Resume with a reason prompt;
  - "Draft step N" instead of "Analyze" when enrolled;
  - threaded badge plus read-only subject with an explanation.
- **Approval controls:** when blocked, a short reason instead of a silently disabled button ("Needs an
  approver: firm tone", "You edited this draft; another approver must approve it").
- **Tasks panel** on the dashboard: open call tasks with Mark done.
- **`/sequences`** (read-only for OPERATOR and APPROVER): versions, steps on a day line, enrollment counts.
  ADMIN gets a simple "new version" form; the visual editor is Phase 05.
- **Stale-data banner** on the dashboard.
- **Types and validators** in `src/lib/api/` (`sequences.ts`, `tasks.ts`, extended `invoices.ts` and
  `followups.ts`); proxy allowlist entries.

## 10. Testing strategy

**Unit and service tests (Django):**

| Area | Cases |
| --- | --- |
| Models | Partial unique enrollment, step uniqueness, task uniqueness, seed idempotent, immutable used version |
| Reconciliation | Enroll overdue eligible; skip not overdue; exit on PAID and on removal; re-enroll on return; starting step from 0, 1, 2 and 3 legacy SENT reminders |
| Tick | One step per tick across a simulated 3-day gap; cooldown; due + offset; gating on DRAFT, APPROVED, SENDING, UNKNOWN and FAILED; continues after REJECTED; TASK step creates one task; completes after the last step; AI failure leaves it due; run limit; lease held → skipped |
| Tone | Step tone, escalation cap at FIRM |
| Four-eyes | Matrix of {FRIENDLY, FIRM} × {below, at threshold} × {OPERATOR, APPROVER} × {self-edited, other} |
| Threading | MIME headers (`Message-ID`, `In-Reply-To`, `References`, `Re:` subject), `threadId` in the request body, legacy attempt without `rfc_message_id`, subject edit refused |
| Delivery | Unknown outcome still stores the Message-ID; release and finish unchanged |
| Scheduling | Slot key idempotent; tick never calls `GmailClient.send` |
| API | Role checks (sequence write is ADMIN), pause and resume conflicts, serializer fields, route-wide auth test still green |
| Workflow | Existing `/automation` workflow tests adapted: evaluate becomes reconcile, draft becomes tick |

**Frontend:** lint, typecheck and build. Manual check of the drawer, approval reasons and `/sequences` at
375 px and desktop.

**Live (with the owner's go-ahead, allowlisted inbox only):** step 1 send → advance the business date or
offsets in a test copy → step 2 draft → approve → send → confirm the same thread and matching headers.

**Design acceptance scenarios covered:**
- Invoice becomes overdue → enrolled; step 1 draft at due + 1, friendly.
- Step 1 sent, no reply, 7 days pass → step 2 draft replies in the same Gmail thread.
- Machine off for 3 days → each enrollment advances one step only.
- Operator edits a firm draft, then approves → 403 `self_approval`; a different approver can approve.
- Scheduled run → syncs and drafts, never approves or sends; an overlapping run exits with code 2.
- Paid or future-due invoice → no draft, approval or send (existing, re-verified).

## 11. Implementation sequence

Each step keeps the suite green.

1. **Spike (30 min):** Message-ID preservation and threading, live with the owner's approval. This decides
   whether the metadata scope is needed.
2. Models, migrations, seed, admin-free management via API; model tests.
3. Reconciliation service plus timeline events; tests.
4. Tick service (pure decision function plus executor); tests including the 3-day gap.
5. Generation changes (step, tone, thread, `draft-v2`) and the subject lock; tests.
6. Gmail threading in `build_message`, `send` and `deliver`; reservation stores the Message-ID; tests.
7. Four-eyes policy and serializer `approval` block; tests.
8. Orchestrator switched to reconcile plus tick; workflow tests adapted; `--dry-run` over enrollments.
9. Worker periodic tick, `tick_sequences` command, sync → tick chaining; tests.
10. Tasks and enrollment APIs, invoice and follow-up serializer fields, dashboard `stale`; API tests.
11. Frontend types, proxy, drawer, approvals, tasks panel, `/sequences`, banner; lint, typecheck, build.
12. README and SCHEDULING updates; a smoke test on a database copy (as in M08); commit with a summary.

## 12. Rollback considerations

- All schema changes are additive. `migrate sequences zero` and `inbox zero` plus reversing `followups 0004`
  restore the M08 schema. v1 tables and rows are never modified by these migrations.
- Follow-ups created under sequences remain valid v1 follow-ups after a rollback; the nullable FKs are
  dropped.
- A `SEQUENCES_ENABLED` setting (default true) lets the orchestrator fall back to the v1 policy path
  without a schema rollback if live behaviour surprises us. It is removed in Phase 05 once the P0 gate
  passes.
- If the Gmail scope is changed, rolling back means re-running `authorize_gmail` with the previous code.

## 13. Acceptance criteria (definition of done)

1. All section 10 tests pass, and all existing tests pass (adapted only where v2 semantics changed them).
2. `makemigrations --check`, lint, typecheck and build are clean.
3. On a copy of the real database: existing invoices are enrolled with history-aware steps, and a dry run
   previews the drafts.
4. Live: step 2 lands in the step 1 Gmail thread for the allowlisted inbox (or the fallback scope is in
   place and verified).
5. Four-eyes refusals are visible and explained in the UI.
6. README and SCHEDULING are updated; a commit with "what to expect" notes.

**Estimate:** 7–8 hours.

## 14. Decisions requested before starting

1. **Starting step for never-contacted overdue invoices:** step 1 (design, recommended) vs. the most
   recently passed offset.
2. **`HIGH_VALUE_THRESHOLD`:** a fixed 5,000 in the invoice currency (design) vs. a percentage of the
   client's balance (open question in v2 §14). Fixed is recommended.
3. **Periodic scheduling inside `run_worker`** instead of a separate `run_scheduler` process (recommended:
   one process fewer).
4. **Permission for one live test send** of step 1 and step 2 to an allowlisted inbox during the spike.
