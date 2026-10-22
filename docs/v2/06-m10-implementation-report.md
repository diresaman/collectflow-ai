# 06 — M10 implementation report: reply ingestion, classification, triage, integration health

- **Date:** 2026-10-22
- **Baseline:** `7c35fa9` (M09 with M09.1–M09.3, live threading verified)
- **Plan:** roadmap Phase 03 (`02-implementation-roadmap.md`) and gap items 15–16
  (`01-gap-analysis.md`), with the M10 authorization's requirements
- **Status:** implemented and tested against **mocked** Gmail and Gemini on PostgreSQL. **Live
  mailbox verification has not been run.** It needs the owner's separate approval to grant
  `gmail.readonly` and read the mailbox (section 9). No OAuth scope was changed, no authorization
  was run, no mailbox was read and no email was sent during M10.

## 1. Behaviour

| Area | What it does now |
| --- | --- |
| Gmail read adapter | `integrations/mailbox.py`, separate from the send client and never importing it (architecture test). It requests only `gmail.readonly` when refreshing, keeps the access token in memory (the shared token file is never rewritten), checks that the token belongs to `GMAIL_SENDER_EMAIL`, and maps errors to *not ready*, *not authorized*, *unavailable* or *history expired*. It calls `num_retries=0`; the next poll is the retry. |
| Authorization (prepared, not run) | `authorize_gmail --with-read-access` requests send + identity + `gmail.readonly`. Without the flag the command behaves exactly as before. `GMAIL_READ_ENABLED` (default **off**) gates everything: no poll is scheduled, the poll job and `poll_replies` refuse to read, and health shows *Awaiting authorization*. |
| Polling | A worker-scheduled `inbox.poll` job per 5-minute slot (idempotent key), under a lease on the `MailboxCursor` row so polls never overlap. `users.history.list` is read page by page from the stored history ID. **The cursor only advances (compare-and-set, lease holder only) after every message up to it was stored.** Any error leaves it unchanged, and the next poll re-reads the same history, with duplicates ignored. |
| Bootstrap and recovery | With no cursor, or when Gmail answers 404 (history expired), or after more than 100 history pages, it scans the known threads of the last `INBOX_RESCAN_DAYS` (30) with `threads.get`, then continues from the history ID read **before** the scan, so nothing arriving during the scan is missed. |
| Scope of what is read | A message's content is fetched only when its Gmail thread contains a SENT CollectFlow delivery. Unrelated mail is never fetched or stored. CollectFlow's own messages (SENT/DRAFT labels, the mailbox's own address, known delivery IDs), spam and messages older than the thread's first CollectFlow send are skipped. |
| Storage | `InboundMessage` (unique Gmail message ID): thread, sender, sender check (*invoice contact*, *same domain*, *unknown*), subject, received time, the CollectFlow message it replies to, every invoice in the thread, and the reply text. The text is `text/plain` (HTML converted, scripts and quoted blocks dropped), with attachments ignored, quoted history and signatures stripped, control characters removed, and a cap of 8,000 characters. A daily `inbox.purge` job (and `purge_reply_bodies`) clears text after 90 days. Labels and the audit trail stay. |
| Immediate hold | Storing a reply, placing a **chase hold** (`AWAITING_CLASSIFICATION`) on each invoice in the thread, recording `REPLY_RECEIVED` and queueing classification happen in **one transaction holding the invoice row locks**. A hold makes the invoice ineligible everywhere eligibility is checked: the tick, draft save (under the invoice lock), approval, send reservation (now under the invoice lock) and delivery (re-checked before Gmail is called). |
| Classification | `inbox.classify` job, `classify-v1` prompt: system rules mark `<client_reply>` as untrusted data, and any `</client_reply>` inside the text is neutralised. Strict JSON schema: `label` (7 values), `confidence` 0–1, `suspicious_instructions`, `rationale` and `extracted` (promised/paid date and amount, payment reference, dispute reason, requested info, return date, new contact email, invoice numbers). The backend re-validates every field: dates, amounts, email, lengths and the exact field set. Temperature is 0. |
| Decision rules (deterministic) | Applied automatically only if confidence ≥ 0.8; no injection is detected (by the model's flag or by text patterns); the sender is known; the reply concerns only this thread's invoice; the label is not OTHER, PROMISE_TO_PAY or DISPUTE; and a label that would resume chasing (REQUESTS_INFO, OUT_OF_OFFICE) is not contradicted by payment or dispute wording. Otherwise it goes to **triage** and the hold stays. A low-confidence promise, dispute or payment claim keeps its own protected hold reason. |
| Label actions | See the table in section 2. **The invoice is never marked paid from an email. No email is drafted or sent by classification.** |
| AI failures | Invalid output or a permanent error goes to triage at once. A transient error (5xx, timeout, 429) sends the reply to triage with an automatic retry: at most `INBOX_CLASSIFY_MAX_ATTEMPTS` (3) attempts in total, each a separate job with a backoff, and never during a quota cooldown. Each call is a `GenerationAttempt(purpose=CLASSIFY)`, so the M09.1 guard counts it and a 429 pauses drafting too. Classification is not blocked by the daily *drafting* budget, because it is safety work. |
| Triage (`/inbox`) | List of replies needing review, handled automatically, or all, with a badge count in the navigation. The detail view shows the text, sender check, invoice, a Gmail conversation link, the AI label with confidence and rationale, why it needs review, holds, tasks and the processing history. A person picks a label (keys 1–7, j/k to move), edits extracted fields, adds a note and applies it. The same deterministic action runs. "Resume chasing" releases holds, and "Ask the AI again" queues one more attempt. |
| Permissions (backend) | Any operator may classify, correct, confirm or retry. **Resuming chasing after a promise, dispute or payment claim (releasing a protected hold, directly or by relabelling) needs an APPROVER and a note** (`403 approver_required`, `400` without a note). Escalating (for example OTHER to DISPUTE) needs no approver. A late AI result never overwrites a person's label. |
| Audit | `ReplyClassification` is an append-only log of every AI proposal (including failed attempts) and every human decision, with the decision taken. PostgreSQL triggers refuse UPDATE, DELETE and TRUNCATE. Timeline events: `REPLY_RECEIVED`, `REPLY_CLASSIFIED`, `REPLY_TRIAGED`, `REPLY_REVIEWED` (with `corrected`), `CHASE_HOLD_PLACED`, `CHASE_HOLD_RELEASED`, `SEQUENCE_POSTPONED` and `TASK_CREATED`. Payloads hold IDs, labels and confidence, never reply text. |
| Integration health (`/settings/integrations`) | `GET /api/integrations/status/` reports Sheets sync, Gmail send, Gmail read, reply polling, Gemini (calls, budget, classifications, cooldown) and the worker, plus the scheduled jobs (tick, poll, purge). Each item has a status (`not_configured`, `awaiting_authorization`, `healthy`, `stale`, `failed`), a detail, a suggested action, and the last success and attempt. It is built from local records only (no provider calls) and includes no tokens, file paths or message text. The page refreshes every 30 seconds. |
| Dashboard | The invoice table shows "On hold: …" and the sequence panel shows a hold banner linking to the Inbox. Tasks from replies show their extracted facts. |

## 2. Label actions

| Label | Chasing | Other effect | Review |
| --- | --- | --- | --- |
| PROMISE_TO_PAY | held (`PROMISE_TO_PAY`, protected) | — (promise record in M11) | always |
| DISPUTE | held (`DISPUTE`, protected) | — (dispute case in M11) | always |
| CLAIMS_PAID | held (`CLAIMS_PAID`, protected) | `RECONCILE` task with paid date, amount and reference | via the task |
| WRONG_CONTACT | held (`WRONG_CONTACT`) | `UPDATE_CONTACT` task with the new contact | via the task |
| REQUESTS_INFO | released | `SEND_INFO` task | no |
| OUT_OF_OFFICE | released | next step postponed to return date + 1 day (09:00), capped at 21 days; 7 days without a date | no |
| OTHER | AI: held (`NEEDS_REVIEW`); person: released | — | AI: always |

Every action is idempotent: one active hold per reply and invoice (partial unique index), one task
per reply and kind (partial unique index), and a postponement that only ever moves later. A
correction re-applies the new label's action without duplicating earlier ones. Tasks created by an
earlier label stay open for a person to close.

## 3. Deviations from the plan, and why

| Plan | Built | Why |
| --- | --- | --- |
| PROMISE and DISPUTE "go to triage until Phase 04" | They go to triage **and** put a protected hold in place that only an approver can release | The M10 authorization requires that a promise, dispute or payment claim cannot let chasing continue silently |
| Hold only for confident labels | **Every** stored reply is held at once (`AWAITING_CLASSIFICATION`), before any AI call | Closes the race with a tick already in flight, and keeps an uncertain or failed classification on the safe side |
| CLAIMS_PAID "pause 5 days" | Held until a person releases it, plus a `RECONCILE` task | No automatic resumption after a payment claim; an approver decides |
| OTHER: triage | AI OTHER is held for review; a person's OTHER releases | "No action fits" should not resume chasing without a person |
| 7-day fallback scan | `INBOX_RESCAN_DAYS` = 30 | A sequence spans 30 days. Duplicates are ignored, so a wider window costs only a few `threads.get` calls |
| `AutomationRun.Kind.REPLY_POLL` | Poll state lives on `MailboxCursor`; results are on the outbox job | The cursor row is the natural place for poll health, and no run history per poll is needed |
| Holds on the enrollment (pause) | Separate `ChaseHold` rows, checked by eligibility | One rule covers every path (tick, drafts, approval, send, delivery, automation runs) without touching enrollment pause and resume |
| Classification budget | Not blocked by the daily drafting budget, but counted and blocked by a quota cooldown | Classification is safety work; drafting backs off instead |

## 4. Data model and migrations (all additive)

| Migration | Contents |
| --- | --- |
| `invoices/0006_chasehold` | `ChaseHold` (invoice, reason, reply ID, detail, release fields); one active hold per invoice and reply |
| `followups/0005_generationattempt_purpose` | `GenerationAttempt.purpose` (`DRAFT` default, `CLASSIFY`) |
| `inbox/0002_replies` | `MailboxCursor`, `InboundMessage`, `ReplyClassification` (+ append-only triggers), `Task.reply` and `Task.detail`, with one task per reply and kind |
| `timeline/0003_reply_events` | New event types (choices only) |

Applied to the development database (`collectflow_dev`), then reversed in dependency order
(`timeline 0002`, `inbox 0001`, `invoices 0005`, `followups 0004`) and re-applied, all cleanly.
**Not applied to the owner's `collectflow` database:** that happens when the stack is redeployed
(section 8).

## 5. Changed files

- **New backend:** `integrations/mailbox.py`; `apps/inbox/{ingestion,classification,actions,triage,holds,text,prompts,preview}.py`;
  `apps/core/integrations.py`; commands `poll_replies`, `purge_reply_bodies` and `preview_reply_discovery`; the four
  migrations; tests in `apps/inbox/tests/` (`helpers`, `test_text`, `test_ingestion`,
  `test_classification`, `test_triage_api`, `test_races`, `test_mailbox_adapter`).
- **Changed backend:** `config/settings.py` (M10 settings); `invoices/models.py` (`ChaseHold`),
  `serializers.py` and `views.py` (`holds`); `followups/eligibility.py` (`on_hold`), `delivery.py`
  (re-check under the invoice lock at reservation), `models.py` (`purpose`), `ai_budget.py` (doc),
  `authorize_gmail` (`--with-read-access`); `sequences/engine.py` (`postpone`, `postponed` due
  rule) and `serializers.py` (`holds`); `automation/outbox.py` (`run_after`) and `scheduling.py`
  (poll and purge); `inbox/models.py`, `serializers.py`, `views.py`, `urls.py` and `apps.py`;
  `timeline/models.py` (events) and `services.py` (append-only triggers for audit tables);
  `core/views.py` and `urls.py`; `core/tests_architecture.py`.
- **Frontend:** `lib/api/replies.ts` and `integrations.ts` (new), `invoices.ts`, `sequences.ts` and
  `tasks.ts` (holds, task detail); `components/inbox/InboxView.tsx`,
  `components/integrations/IntegrationsView.tsx` and `components/layout/InboxBadge.tsx` (new);
  `AppHeader`, `SequencePanel`, `InvoiceTable` and `TasksPanel`; pages `app/inbox` and
  `app/settings/integrations`.
- **Docs:** README ("Customer replies (M10)", variables, API, limitations), `docs/SCHEDULING.md`
  §1c, `backend/.env.example`, the roadmap, and this report.

## 6. Tests and checks (mocked providers, PostgreSQL 16)

- **Backend:** `python manage.py test` on PostgreSQL 16 (`test_collectflow_dev`): **431 tests, all
  passing** (338 before M10 + 93 new). One existing test changed: `run_worker --once` now also runs
  the daily `inbox.purge` job (4 jobs instead of 3) and must not queue a poll while read access is off.
- **Frontend:** `tsc --noEmit` and `eslint` clean; the production image (`frontend/Dockerfile`, `next
  build`) builds.
- **Migrations:** applied, reversed and re-applied on `collectflow_dev` (section 4).
- **No live provider calls:** Gmail and Gemini are replaced by fakes in every test.

| Requirement | Covered by |
| --- | --- |
| History pagination and cursor recovery | `test_ingestion`: multi-page history; the cursor is unchanged when a message fails and advances after a retry; expired cursor → rescan + reset; bootstrap scan |
| Duplicate ingestion | the same reply over two polls is stored once, with one hold and one classify job; 4 concurrent ingests → 1 row (`test_races`) |
| Unrelated thread rejection | unrelated threads are never fetched (`fetched == []`) or stored |
| Message and invoice association | the thread maps to its invoice; a shared thread holds every invoice; own messages and messages older than the first send are skipped; sender check |
| MIME and text extraction | `test_text`: plain over HTML, attachments ignored, nested multipart, charsets, invalid base64, Gmail/Outlook/French quotes, signatures, control characters, cap |
| Classification schema | 10 schema violations rejected; block neutralisation in the prompt |
| Confidence threshold | 0.79 → triage, 0.8 → applied |
| Prompt injection | injection text with a 1.0 "REQUESTS_INFO" → triage, hold kept, no task; the model's own flag honoured |
| Interim safety holds | each of the seven labels; low-confidence dispute keeps a protected hold; payment wording blocks a releasing label; unknown sender; several invoices |
| Human corrections | confirm, correct (OTHER → DISPUTE, OTHER → CLAIMS_PAID), re-apply once, audit trail of AI plus human, append-only enforcement, invalid input |
| Concurrent polling | 3 parallel polls → 1 reply, lease released |
| Tick vs incoming reply | a reply committed while the tick waits on Gemini → the draft is refused (`not_eligible`), the drafting marker is cleared, later ticks skip as `on_hold`; a reply blocks approval, send reservation and delivery after reservation (Gmail never called) |
| Provider failures | Gmail 401/403/404/429/500/timeout mapping, refused refresh, wrong mailbox, deleted message; Gemini invalid, not configured, bounded transient retries, quota cooldown (no call), shared quota pause |
| Integration health | awaiting authorization, not configured, healthy, failed, stale (polling), Gemini cooldown, no secrets in the response |
| Backend authorization | operator 403 on protected release (service and API), approver needs a note, no-role user refused, signed-out refused |
| M08/M09 guarantees | the full existing suite still passes; classification never drafts, approves or sends; the poll job reads nothing while read access is off; the architecture test includes the mailbox adapter |

## 7. Safety properties

- **Scheduled work still never approves or sends.** The poll and classify jobs import neither
  `followups.delivery` nor `integrations.gmail` (architecture test). Their only effects are holds,
  tasks and a later next step.
- **A reply always wins against chasing.** The hold commits with the reply under the invoice lock,
  and drafting, approval and send reservation re-check holds under the same lock. Delivery re-checks
  just before the Gmail call (a reply landing in the milliseconds between that check and Gmail
  accepting the message cannot be caught; this was already the case for payment status).
- **No AI decision is applied silently.** Only the decision rules in section 1 apply a label; a
  promise, dispute or payment claim always reaches a person, and only an approver resumes chasing
  after one.
- **No unbounded retries.** Poll jobs run once per slot; classification attempts are capped at 3.
- **No secrets or message text** in health responses, timeline payloads or logs (logs carry codes
  and HTTP statuses only).

## 8. Deployment and rollback

**Step 1 deployed on 2026-10-22** (approved; record in section 8.1). The procedure used:

1. Back up: `docker compose exec db pg_dump -U collectflow -Fc collectflow > backups/pre-m10.dump`.
2. `docker compose up -d --build --no-deps api worker web` (the api service migrates on start).
   `GMAIL_READ_ENABLED` stays off, so nothing reads the mailbox. The Inbox stays empty, and
   Integrations shows Gmail read as *Awaiting authorization*.

Rollback: redeploy the `pre-m10` images (tagged before the deploy, see 8.1) after reversing the migrations in this order: `timeline 0002`,
`inbox 0001`, `invoices 0005`, `followups 0004` (verified on the development database). Reversing
drops replies, holds and classifications, so take a backup first.

### 8.1 Step 1 deployment record (2026-10-22)

| Check | Result |
| --- | --- |
| Backup | `backend/backups/collectflow-before-m10-20261022T172940.dump` (custom format, 115 KB, mode 600, SHA-256 in `.sha256`), taken with the worker stopped |
| Backup verification | Restored into a scratch database: 28/28 tables with identical row counts; content hashes identical for invoices, follow-ups, delivery attempts, enrollments, timeline, users and profiles; timeline triggers present. Scratch database dropped |
| Rollback readiness | Images tagged `collectflow-{api,worker,web}:pre-m10`; migration reversal order verified on `collectflow_dev` |
| Migrations | `followups 0005`, `invoices 0006`, `inbox 0002`, `timeline 0003` applied by the api on start. Existing data unchanged: only new empty tables, permission and content-type rows, one tick run, two jobs (tick, purge) and the verification login's session differ from before |
| Services | api healthy, worker running (heartbeat healthy; scheduled tick COMPLETED with 0 drafts; `inbox.purge` SUCCEEDED), web rebuilt from `b509999` plus the Dockerfile change committed in `78bde0e`, db container untouched |
| API via web proxy | Login as `collectflow` (ADMIN); `me`, `replies`, `replies?triage=true`, `replies/summary`, `integrations/status`, `invoices` (with `holds`), `enrollments`, `tasks`, `dashboard`, `follow-ups`, `sequences`: all 200 |
| Pages | `/`, `/inbox`, `/settings/integrations`, `/sequences`, `/automation`: 200 |
| Integration health | Sheets, Gmail send, Gemini, worker: healthy. Gmail read and reply polling: awaiting authorization. No secrets, paths or addresses in the response |
| Gmail reading | `GMAIL_READ_ENABLED` unset (off) in the containers; no `inbox.poll` job has ever been queued; no OAuth scope changed; `authorize_gmail --with-read-access` and `preview_reply_discovery` not run (only `--help`) |

Deviations found and fixed during Step 1:

1. **Proxy allowlist (M10 bug).** The Next.js proxy forwards only allowlisted routes, and the M10
   endpoints were missing, so `/inbox` and `/settings/integrations` could not load data. Fixed in
   `b509999` and redeployed (web only).
2. **Network.** Rebuilding the web image failed several times on a degraded connection (DNS and TLS
   timeouts to Docker Hub, `npm ci` timeouts). The frontend Dockerfile now keeps npm downloads in a
   BuildKit cache between builds and retries patiently, so a rebuild rarely needs the network. Until
   the rebuild succeeded, the earlier M10 web image kept serving; no other service was affected.
3. **Preview tool added before the deploy** (`15986a7`, section 9.1), so Step 2 needs no further
   code deployment.

## 9. Live Gmail verification (not run; needs approval)

Mocked-test completion (section 6) is separate from live verification, which has **not** been done.
It needs, in this order and each with the owner's explicit approval:

1. **Grant read access:** `python manage.py authorize_gmail --with-read-access`, signed in as
   `diresaman@gmail.com`, then set `GMAIL_READ_ENABLED=true` for the dev worker only.
2. **Test customer:** a dev invoice (on `collectflow_dev`) whose contact is the owner-controlled
   `getdera6591@gmail.com`, added to `GMAIL_ALLOWED_RECIPIENTS`. Replies from the sender's own
   address are skipped as CollectFlow's own, so the customer must be a different mailbox.
3. **Run:** send step 1 to it (approval in the UI), reply from `getdera6591@gmail.com` with a
   promise, then a payment claim, then an out-of-office reply. Run `poll_replies` (or let the worker
   poll) and check that each reply is stored once, the invoice is held at once, the labels and
   actions match section 2, and nothing else in the mailbox was stored.
4. **Injection reply:** a reply containing "ignore previous instructions…" must end in triage with
   chasing held.

**On the owner's real data:** the first poll scans the last 30 days of CollectFlow threads. Any
customer reply that arrived in that window, possibly to a v1 reminder, will be stored and will put
its invoice on hold until reviewed. This is intended (an unseen dispute must stop chasing), but the
Inbox may show older replies on day one. Preview it first (section 9.1).

### 9.1 Preview of the first 30-day discovery (read-only)

`python manage.py preview_reply_discovery [--days 30] [--json]` reports what the first poll would
do, **without doing it**:

- the CollectFlow threads in the window and the messages in them;
- the replies the first poll would store, with skipped messages counted by reason (own message,
  older than CollectFlow's first send, deleted, already stored);
- per affected invoice: the reply count, the sender check (invoice contact, same domain, unknown),
  the latest reply date, the sequence position, any active reminder, and the effect of the hold
  ("chasing stops", "the draft reminder can no longer be approved", "not being chased anyway").

Guarantees, all covered by `apps/inbox/tests/test_preview.py`:

- It reads Gmail **headers only** (`messages.get(format=metadata)`), never a message body. Only
  threads containing a SENT CollectFlow delivery are touched.
- It **writes nothing**: no replies, holds, tasks, jobs, cursor, timeline events, follow-up or
  enrollment changes. The test compares every affected table and enrollment timestamps before and
  after.
- It works while `GMAIL_READ_ENABLED` is **off**, so polling never starts during the preview. It
  still requires the `gmail.readonly` grant, and it prints no addresses, subjects or text.

**Procedure, once read access is separately approved (Step 2):**

1. Back up the database (as in Step 1).
2. On the host: `cd backend && .venv/bin/python manage.py authorize_gmail --with-read-access`, signed
   in as `diresaman@gmail.com`. This replaces the token in `backend/credentials/` (mounted into the
   containers). Sending keeps working with the new token.
3. Leave `GMAIL_READ_ENABLED` unset (off). Confirm on `/settings/integrations` that Gmail read still
   shows *Awaiting authorization* and that no `inbox.poll` job exists.
4. Run the preview against the real database:
   `docker compose exec api python manage.py preview_reply_discovery`.
5. The owner reviews the list of invoices that would be held. Only then, and only with separate
   approval, set `GMAIL_READ_ENABLED=true` and restart the worker (Step 3). The first poll then stores
   exactly what the preview listed, plus anything that arrived in between.

To back out after step 2 without ever polling: delete the token, re-run `authorize_gmail` without
the flag, or revoke the app's access in the Google account. `GMAIL_READ_ENABLED` was never switched
on.

## 10. Known limitations

- **Live mailbox behaviour is unverified** (section 9): Gmail history paging, real MIME variety and
  quota under real load have been tested against mocks only.
- **Promise and dispute workflows are interim.** Holds plus review stand in until M11 adds
  `PromiseToPay` and `DisputeCase`; promised dates are stored in `extracted` only.
- **Replies outside CollectFlow threads** (a customer writing a new email) are not ingested.
  Matching on `In-Reply-To` or the sender address alone was left out to avoid reading unrelated mail.
- **Pattern screens are English-centric.** Injection and payment-wording patterns are a backstop;
  the model's own flag and human review remain the main defence for other languages.
- **A reply to a step that is already drafted** holds approval and sending, but the draft remains.
  A person rejects or regenerates it after reading the reply.
- **Tasks from an earlier label stay open after a correction**, for a person to close.
- **The health page** reports what CollectFlow last observed. It does not test credentials live
  (by design: no provider calls in a request).
