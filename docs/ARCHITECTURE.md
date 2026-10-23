# CollectFlow AI: architecture, safety and AI integration

This is the submission overview. The README covers setup and the reference for each feature. The design
decisions and the reports for each milestone are in `docs/v2/`.

## 1. What the system does

CollectFlow reads invoices from a Google Sheet. It works out which ones need chasing and drafts reminders with
Gemini. A person approves each reminder, and it is sent through Gmail only after a separate, confirmed send.
CollectFlow then reads customer replies, classifies them, and turns promises, disputes and payment claims into
holds, promises, dispute cases and tasks. It also ranks clients by risk and reports aging, DSO, CEI and a 30-day
cash forecast.

**The sheet is the source of truth for payment status.** CollectFlow never writes to the sheet, and nothing
it reads, including an email saying "we paid", marks an invoice paid.

## 2. Components

```
 Browser ─▶ Next.js 16 web app (127.0.0.1:3000)
             ├─ (app) route group: one SessionGate + AppShell kept mounted across pages
             │   Today · Inbox · Approvals · Tasks · Disputes · Clients · Invoices · Analytics
             │   Sequences · Automation · Integrations
             └─ /api/backend/* proxy: route allowlist, same-origin check, forwards only the
                session and CSRF cookies and X-CSRFToken (holds no secret)
                    │
                    ▼
 Django 5.2 + DRF API (127.0.0.1:8000): session login, roles OPERATOR < APPROVER < ADMIN, CSRF on writes
   apps/accounts    users, roles, login rate limiting, approval separation (four-eyes)
   apps/invoices    Client, Invoice, read-only Sheets sync
   apps/sequences   collection sequences and steps (drafts only)
   apps/followups   reminders and statements: drafting, edit, approve, the ONLY Gmail send path
   apps/inbox       Gmail reply reading, AI classification, deterministic triage, holds
   apps/cases       promises to pay, disputes, tasks, the promise and dispute monitor
   apps/insights    risk scores, worklist, analytics and forecast snapshots
   apps/timeline    append-only TimelineEvent (database triggers block UPDATE, DELETE and TRUNCATE)
   apps/automation  transactional outbox, worker, the automation orchestrator
   apps/core        health, integration status, error format
                    │
                    ▼
 PostgreSQL 16: state, audit timeline and job queue (no Redis)
                    ▲
 run_worker ────────┘ claims OutboxJobs (SELECT … FOR UPDATE SKIP LOCKED) ─▶ Sheets (read) · Gemini · Gmail
```

Docker Compose runs four services: `db`, `api`, `worker` and `web`. Every port is published on `127.0.0.1` only.

### Background work

- **No provider calls during web requests.** Syncs, AI drafting, classification and Gmail delivery are written
  as an `OutboxJob` in the same transaction as the state change. The API answers `202`, and the worker does the
  work.
- **Scheduled jobs** use slot keys, so each time slot runs at most once, even with several workers:
  - `sequences.tick` (15 min): drafts due sequence steps;
  - `inbox.poll`: reads new replies;
  - `inbox.purge`: removes reply bodies after the retention period;
  - `cases.monitor`: marks promises kept or broken and tasks overdue;
  - `insights.recompute` (hourly, and after relevant changes): risk scores and analytics.
- **Scheduled jobs never approve or send.** An import contract enforces this (`backend/.importlinter`):
  scheduled and automatic code cannot import the Gmail send path, even indirectly.

## 3. Safety guarantees

Each guarantee is enforced in code and covered by tests. Section 7 of
`docs/v2/12-m15-release-readiness-report.md` lists the evidence for each one.

| Guarantee | How |
| --- | --- |
| A person approves every email | AI output is always a DRAFT. Approval is a separate step, and sending is a separate, confirmed step after it |
| Four-eyes approval | These need an APPROVER who did not make the last edit: a reminder at or above `HIGH_VALUE_THRESHOLD` (in its own currency), a firm-tone reminder, a sequence step marked "requires approver", and every statement |
| Gmail at most once | Each step is a conditional update: APPROVED → SENDING. A partial unique index allows one in-flight send per reminder. The delivery job has `max_attempts=1`, and the Gmail client has `num_retries=0` |
| An unknown outcome is never resent | `DELIVERY_UNKNOWN` locks the invoice until an admin checks the Sent folder and records the result with `reconcile_delivery` |
| Eligibility is checked at every step | `ineligibility()` checks unpaid, overdue, no hold or dispute, a valid email, and not already in a statement. It runs at drafting, approval, send reservation and immediately before the Gmail call |
| Holds belong to their records | A promise, dispute or payment claim puts the invoice on hold. Only an APPROVER, with a note, can release a hold that protects the customer |
| Email never marks an invoice paid | A "we paid" reply creates a reconciliation task. Only the sheet changes payment status |
| Recipient allowlist | While `DJANGO_DEBUG=true`, Gmail sends only to `GMAIL_ALLOWED_RECIPIENTS` |
| Audit trail | Every sync, draft, edit, approval, send, reply, decision and hold change is a `TimelineEvent` that cannot be changed or deleted |
| Access control | Every endpoint except health and login needs a session and a role, and every write needs CSRF. The web proxy rejects requests from another origin. Logins are rate-limited |
| Secrets | `.env`, `credentials/` (Sheets key, Gmail OAuth client and token) and backups are git-ignored and were never committed. `NEXT_PUBLIC_*` variables hold no secrets |

## 4. AI integration (Gemini)

Gemini (`GEMINI_MODEL`, default `gemini-3.5-flash`) does three jobs. In each one **the AI suggests and the
backend decides**.

| Use | Prompt | The model receives | The model returns | Deterministic checks before anything happens |
| --- | --- | --- | --- | --- |
| Reminder drafting | `draft-v3` | Client name, invoice number, amount, currency, due date, days overdue, the tone the backend chose, the sequence step and a missed promise date. **Never the email address** | JSON: subject, body, tone, reasoning | The backend chooses the tone from days overdue; the model cannot change it. Output is validated for schema, length, a one-line subject, no links and the matching tone. A person reviews and approves every draft |
| Consolidated statement | `statement-v1` | Client name, the number of invoices and their numbers. **No amounts, no email** | Subject, opening, closing | Text containing amounts or links is rejected. The backend writes the invoice lines and per-currency totals, and approval checks they are intact |
| Reply classification | `classify-v1` | The reply inside `<client_reply>` tags, marked as untrusted data, plus the invoice facts | Label, confidence, an injection flag, extracted fields (promised date and amount, dispute reason, …) | Applied automatically only if confidence ≥ 0.8, there is no sign of injection (the model's flag or a regex), the sender matches the invoice contact or domain, and only one invoice is involved. A promise, dispute or "other" label **always** goes to a person; a label that would release a hold is held back if the text mentions payment or a dispute |

**Spending and failure handling:**
- every call is recorded as a `GenerationAttempt`;
- automatic drafting has a daily budget (`AI_DAILY_GENERATION_BUDGET`);
- a quota error starts a cool-down (`AI_QUOTA_BACKOFF_MINUTES`), and nothing calls Gemini during it;
- classification retries are capped, and since M14 a retry is not used up while the quota pause lasts;
- while a reply waits for classification, chasing stays on hold.

**Evaluation.** `backend/evals/` holds 80 labelled replies and 40 drafting scenarios. The harness scores them
with the application's own validators and triage rules.
- **Deterministic and simulated modes pass**, in local runs and in the CI steps.
- **The live 120-call model evaluation was not run.** The owner excluded it from the assignment scope.
- **Real-model evidence** comes from the M12.1 live acceptance test: real drafts, a promise and a payment claim
  classified correctly, and the quota pause handled safely (`docs/v2/09-m12-live-provider-acceptance-report.md`
  §8).

## 5. Data model highlights

| Model | Notes |
| --- | --- |
| `Client` | Identified by the normalised invoice email. Holds the risk score, its factors and payment-behaviour metrics |
| `Invoice` | Mirrors the sheet. Unique `external_id`. Holds and eligibility are derived from related records |
| `FollowUp` | Kind INVOICE or STATEMENT. States DRAFT → APPROVED → SENDING → SENT, FAILED or DELIVERY_UNKNOWN, plus REJECTED. A partial unique index allows one active follow-up per invoice. Statement lines are snapshotted in `StatementLine` |
| `GenerationAttempt`, `DeliveryAttempt` | Every AI call and every Gmail attempt, with sanitised errors and Gmail message IDs |
| `InboundMessage`, `ReplyClassification` | Customer replies and an append-only log of AI and human labels |
| `PromiseToPay`, `DisputeCase`, `Task` | Collections cases, each with an owner, notes and an outcome |
| `OutboxJob` | The job queue: idempotency keys, attempts, back-off, leases |
| `AnalyticsSnapshot` | Analytics computed by the worker. Pages only read them |
| `TimelineEvent` | The append-only audit history shown on each invoice and in the Inbox |
