# 07 — M11 implementation report: promises, disputes and tasks

- **Date:** 2026-10-22
- **Baseline:** `0867f1c` (M10 deployed; Gmail reading enabled and verified with a live reply)
- **Plan:** roadmap Phase 04 (`02-implementation-roadmap.md`), gap items 14, 17 and 18
  (`01-gap-analysis.md`), and the M11 authorization
- **Status:** implemented and tested on the development database (`collectflow_dev`) with mocked
  providers. Migrations were also rehearsed on a restored copy of production. **Not deployed:** the
  owner's `collectflow` database is unchanged, pending a verified backup and explicit deployment
  approval (section 8).

## 1. Behaviour

| Area | What it does now |
| --- | --- |
| One hold system | M10's `ChaseHold` is reused, not duplicated. A hold now has exactly one owner (a reply, a promise or a dispute; database check constraint), and only that owner ends it. Chasing resumes only when an invoice has no active hold. The invoice API lists each hold with its source and "who can end it". |
| Promise to pay | `PromiseToPay` holds the invoice, client, source reply, date, amount and currency, status, note, owner (who confirmed it), timestamps, resolver, resolution note and when payment was seen. It is created only by a person (`POST /api/promises/` or the Inbox and drawer forms); the AI's extracted date and amount only prefill the form. The date must be from today to 60 days ahead and the amount positive, at most the invoice amount. One OPEN promise per invoice and one promise per reply (unique indexes); confirming the same reply twice returns the same promise. |
| Recording a promise | In one transaction: the promise; the invoice's unsent reminders withdrawn (`cancelled_reason="promise"`, which **keeps the pending step pending**); a promise-owned hold; the reply's own hold on this invoice handed over; a HUMAN classification on the reply next to the AI's (which stays unchanged); and a `PROMISE_RECORDED` event. |
| Monitoring | `cases.monitor` runs hourly (idempotent slot) and after every successful sync. It is deterministic. **KEPT** when the sync's `INVOICE_UPDATED` event shows UNPAID → PAID on or before promised date + 2 days. **BROKEN** when it is still unpaid after that day: the hold is released and the enrollment's `escalation_level` goes up by one, so the next reminder is one tone firmer (capped at firm) and the `draft-v3` prompt mentions the missed date. Paid later than the grace period counts as BROKEN with no escalation, since the invoice leaves its sequence anyway. An invoice missing from the sheet stays OPEN. Each promise is settled under `FOR UPDATE SKIP LOCKED` with a status re-check, so a transition happens once. Never drafts, approves or sends. |
| Cancel a promise | Approver only, with a note. Releases only the promise's hold and reports whether chasing resumes. |
| Dispute case | `DisputeCase` holds the invoice, client, source reply, reason (6 choices), description, owner, opener, status, resolution, resolver and timestamps; `DisputeNote` holds internal notes. One OPEN case per invoice and one per reply. Opening it: unsent reminders are withdrawn (step kept pending), a dispute-owned hold is placed, the reply's hold is handed over, and a "Dispute review" task is created for an approver. |
| Dispute lock | Eligibility reports `invoice_disputed` for a dispute-owned hold. Every path refuses with **409 `invoice_disputed`**: draft request, generation (again under the invoice lock), approval, send reservation (under the invoice lock), a delivery already queued (released before Gmail is called), the sequence tick and automation runs. A stale approval cannot slip through: reminders are withdrawn when the case opens, and reservation and delivery re-check anyway. |
| Resolving a dispute | Approver only, with a written resolution; resolving twice is refused. **Customer is right** (`RESOLVED_VALID`): the sequence exits (`dispute_upheld`) and is not re-enrolled until the sheet changes the invoice. **Invoice stands** (`RESOLVED_INVALID`): the dispute's hold is released. Either way the review task closes, nothing is sent, and the response reports `chasing: {resumes, blocked_by, next_action_at}`, checking other holds, invoice status, eligibility and the sequence state. |
| Tasks | The existing `Task` is extended. New: `status` (OPEN/DONE/CANCELLED, with a check constraint tying it to `done_at` and `cancelled_at`), completion note, `created_by`, cancellation fields, `overdue_notified_at`, a link to its dispute, and the kinds dispute review and other follow-up. Assignment, completion (idempotent, row-locked), cancellation (note required; call-step tasks cannot be cancelled) and manual creation, all on the timeline. Completing a sequence call task records the step on commit, exactly once. Completing any other task never resumes chasing. Overdue tasks are recorded once by `cases.monitor`. |
| Inbox | A PROMISE_TO_PAY reply shows "Confirm the promise" (date and amount prefilled as suggestions); a DISPUTE reply shows "Open a dispute case" (reason, description, owner). Once done, the reply links to its promise or dispute. AI proposals and human decisions stay separate in the append-only classification log. CLAIMS_PAID keeps its reconciliation task and hold, and the invoice is never marked paid. REQUESTS_INFO and WRONG_CONTACT tasks follow the new lifecycle. |
| Invoice drawer | The new "Promises, disputes and tasks" section shows: the active holds with who can end them; promise and dispute cards (status, dates, grace, owner, notes); record and open forms; approver-only cancel and resolve (operators see who can act); owner and assignee pickers; tasks with done, cancel and add; and recent case history. |
| Audit | Timeline events: `PROMISE_RECORDED/KEPT/BROKEN/CANCELLED`, `SEQUENCE_ESCALATED`, `DISPUTE_OPENED/ASSIGNED/NOTE_ADDED/RESOLVED`, `FOLLOW_UP_CANCELLED`, `TASK_ASSIGNED/CANCELLED/OVERDUE`, plus the existing hold and task events. Payloads hold IDs and codes; dispute note text is not copied into the timeline. |

## 2. Design decisions and deviations

| Plan or design | Built | Why |
| --- | --- | --- |
| "Pause until date + 2" | A promise-owned hold until the promise is settled | The M11 authorization forbids a parallel pause system. The hold covers every path, and the enrollment's pending step is preserved. |
| "Cancel unsent drafts" | Withdrawn as REJECTED with `cancelled_reason`, ignored by the sequence engine | A person's rejection completes a step (v2 rule); a system withdrawal must not, so the step is re-drafted (escalated after a broken promise) when chasing resumes. |
| KEPT "from sync status history" | The sync's `INVOICE_UPDATED` event time, after the promise was recorded | A check that runs late (machine off) still judges by when the sheet showed payment, not when it noticed. |
| "Daily `check_promises`" | Hourly `cases.monitor`, plus after each sync | Cheap and idempotent; promptly settles a promise once payment syncs. |
| Dispute → "notify an approver (a task)" | A "Dispute review" task linked to the case | The task closes automatically on resolution. |
| Valid dispute "exits" | Exit plus no re-enrollment until the sheet changes the invoice | Otherwise the next reconcile would re-enroll the same invoice and chase the disputed amount again. |
| Promise on a disputed invoice | Refused (`409 invoice_disputed`); an existing promise survives a later dispute | A dispute must be settled first. The two holds stay independent. |
| Task kind names in the authorization | Kept the existing codes (`SEND_INFO`, `UPDATE_CONTACT`, `RECONCILE`) with the authorization's display names (information request, contact update, reconciliation) | Renaming stored codes would rewrite data for no behavioural gain. |
| `/disputes` page (roadmap) | Not built; disputes live in the drawer and Inbox | The authorization asked to extend the drawer and Inbox and to avoid redesigns. M12 owns new pages. |

## 3. Data model and migrations (all additive)

| Migration | Contents |
| --- | --- |
| `invoices/0007_hold_owners` | `ChaseHold.promise_id`, `.dispute_id`; one active hold per invoice and promise or dispute; `hold_has_exactly_one_owner` |
| `cases/0001_initial` | `PromiseToPay`, `DisputeCase`, `DisputeNote` and their constraints |
| `followups/0006_followup_cancelled_reason` | `FollowUp.cancelled_reason` |
| `inbox/0003_task_lifecycle` | Task status, notes, cancellation, creator, dispute link, overdue marker, new kinds; a data step marking already-completed tasks DONE before the status constraint; one task per dispute and kind; a status/due index |
| `timeline/0004_case_events` | New event types (choices only) |

Verified three ways: applied on `collectflow_dev`; reversed in order (`timeline 0003`, `inbox 0002`,
`followups 0005`, `cases zero`, `invoices 0006`) and re-applied; and **rehearsed on a restored copy of
production**. There, the existing information-request task became OPEN and the existing reply hold
satisfied the owner constraint. The rehearsal database and dump were deleted afterwards.

## 4. Changed files

- **New:** `apps/cases/` (`models`, `common`, `promises`, `disputes`, `monitoring`, `serializers`,
  `views`, `urls`, `apps`, migration, tests), `frontend/src/lib/api/cases.ts`,
  `frontend/src/components/dashboard/CasePanel.tsx`, this report.
- **Backend changes:**
  - `config/settings.py` (app and M11 settings) and `urls.py`;
  - `invoices/models.py` (hold owners), `serializers.py` (`source`, `release_rule`, `open_promise`, `open_dispute`), `views.py` and `sync.py` (monitor after sync);
  - `followups/eligibility.py` (`invoice_disputed`), `services.py` (`refuse_if_ineligible`, `InvoiceDisputed`, `cancel_unsent`, broken-promise context), `delivery.py`, `prompts.py` (`draft-v3`) and `models.py`;
  - `sequences/engine.py` (cancelled reminders ignored, escalation after a broken promise, no re-enrollment after an upheld dispute);
  - `inbox/models.py` (Task), `services.py` (task lifecycle), `views.py`, `urls.py`, `serializers.py` and `holds.py` (owned holds, per-invoice release);
  - `automation/scheduling.py`; `accounts/views.py` and `urls.py` (`/api/users/`); `core/integrations.py` (monitor row); `timeline/models.py` (events).
- **Frontend changes:**
  - `lib/api/tasks.ts`, `invoices.ts` and `replies.ts`;
  - `FollowUpDrawer`, `TasksPanel` and `InboxView`;
  - the proxy allowlist (all new routes).
- **Docs:** README ("Promises, disputes and tasks (M11)", variables, API), `backend/.env.example`, the
  roadmap, and this report.

## 5. Tests and checks (PostgreSQL 16, mocked providers)

- **Backend:** `python manage.py test` on PostgreSQL 16: **484 tests, all passing** (437 before M11 +
  47 new in `apps/cases/tests/`). Three existing tests were adjusted for intended changes: the prompt
  version is now `draft-v3`; the worker runs 5 jobs with `--once` (plus `cases.monitor`); the call-task
  test sets the new status field.
- **Django:** `manage.py check` clean; `makemigrations --check` reports no changes; migrations applied,
  reversed, re-applied and rehearsed on a copy of production (section 3).
- **Frontend:** `tsc --noEmit` and `eslint` clean; the production image builds (built under a separate
  tag and removed, so the running stack was not touched).
- **Regression found and fixed during M11:** marking a task done with a bare POST (no JSON body)
  returned 415 once the view read an optional note. Request bodies that are missing or not JSON are now
  treated as empty.

| Requirement | Covered by |
| --- | --- |
| Promise creation and validation; date and amount handling | `test_promises`: from a reply (hold hand-over, HUMAN plus AI records), past or > 60 days, bad dates, negative, three decimals, above the invoice, non-numeric amounts; inclusive bounds; partial amounts |
| Grace-period boundaries; kept and broken | OPEN through promised + 2; BROKEN on + 3 with escalation; KEPT when paid by + 2 at 23:00; paid after grace → BROKEN without escalation; missing from sheet stays OPEN |
| Escalated draft | after a broken promise the next tick drafts FIRM (step 2 is professional) with `broken_promise_date` in the prompt; Gmail never called |
| Duplicate prevention; reply reprocessing | one OPEN promise and one promise per reply; one dispute per reply and one review task; classification never creates a promise |
| Dispute creation, assignment, notes | `test_disputes`: from a reply, validation, one open case, owner and notes audited, note text kept out of the timeline |
| Dispute lock on every outbound path | draft request and generation, approval of a pre-dispute draft (withdrawn), stale approval at reservation, a queued delivery released before Gmail, the tick (no Gemini call), API `409 invoice_disputed` |
| Resolution permissions and effects | approver plus resolution required; INVALID resumes with no send; VALID exits and is not re-enrolled until the sheet changes; resolving twice refused |
| Overlapping holds | promise plus dispute independent; resolving one keeps the others; reply holds survive promise and dispute resolution |
| Task lifecycle and call step | `test_tasks`: create, assign, done with note (idempotent), cancel with note, role checks, call task completes step 4 exactly once and cannot be cancelled, an unrelated task does not lift a dispute, overdue flagged once; API incl. `assignee=me` and `/api/users/` without emails |
| Concurrency | `test_races`: 4 parallel promises → 1; parallel VALID and INVALID resolution → 1; 4 parallel task completions → 1 event; 3 parallel promise checks → one BROKEN and one escalation; promise check racing a payment sync → KEPT; dispute racing a send reservation → never sent |
| Regression | the full M08–M10 suite; the existing call-task test uses the status field; the existing task-done API (bare POST) still works |

## 6. Safety properties

- Scheduled jobs (`cases.monitor`, ticks) never approve or send; the architecture test still holds.
- No invoice is marked paid from an email; KEPT relies on the sheet.
- Resolving a promise or dispute never clears another owner's hold.
- Resuming chasing after a promise, dispute or payment claim stays approver-only with a note (M10 rule
  kept; promise cancellation and dispute resolution follow it).
- The M09.1 spending guard and the Gmail delivery guarantees are unchanged.

## 7. Known limitations

- **Lock order:** generation saves a draft by locking the invoice then reminders, while approval and
  case services lock reminders then the invoice (pre-M11 code). A simultaneous draft save and promise
  record on the same invoice could deadlock; PostgreSQL aborts one with an error and nothing is
  half-done.
- **Partial payments** are not tracked: a promise is KEPT only when the sheet shows the invoice PAID.
- **No `/disputes` overview page** yet (M12); disputes are reached from the invoice drawer and Inbox.
- **Notifications:** the dispute-review task is how approvers learn of a case; there is no email or
  push alert.

## 8. Deployment (not done; needs approval)

1. Back up `collectflow` and verify the backup by restoring it into a scratch database (as in M10 Step 1).
2. Tag the running images `pre-m11`.
3. `docker compose up -d --build --no-deps api worker web` (the api migrates on start).
4. Check: migrations, the worker queues `cases.monitor`, `/api/promises/`, `/api/disputes/` and
   `/api/users/` through the web proxy, and the drawer and Inbox load.

Rollback: reverse the migrations in the order in section 3 and redeploy the `pre-m11` images. This
drops promises, disputes and the new task fields, so restore from the backup if any were recorded.
