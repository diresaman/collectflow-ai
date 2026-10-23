# 09 — M12.1 live-provider P0 acceptance

- **Date:** 2026-10-23
- **Baseline:** `95c421f` (M12 deployed to the owner's stack)
- **Status:** **Stage C complete: live-provider acceptance PASSED, with recorded limitations** (section 8).
  It was run with real Google Sheets, Gmail and Gemini on the controlled test records only. The owner
  performed every action; Claude observed read-only.

## 1. Test subjects

| Item | Value | Why |
| --- | --- | --- |
| Test invoice | `TEST-M12-001`, client `TEST Acceptance Co`, `getdera6591@gmail.com`, **900.00 USD**, due **2026-10-20**, `UNPAID` | New row, so no existing invoice is touched. Under the 5,000 high-value threshold, so the owner (the only user, ADMIN) may edit and approve it. Due 2026-10-20 makes step 1 (friendly, +1 day) due now and step 2 not until 2026-10-28 |
| Optional four-eyes invoice | `TEST-M12-002`, same client, **6,000.00 USD**, due 2026-10-20 | Shows the four-eyes rule live: after the owner edits it, the owner's own approval is refused. **Never approved or sent**; its draft is rejected at the end |
| Recipient | `getdera6591@gmail.com` (owner-controlled, already in `GMAIL_ALLOWED_RECIPIENTS`) | No real customer |
| Sender | `diresaman@gmail.com` (existing token and scopes, unchanged) | |

The sheet is read with `spreadsheets.readonly`; CollectFlow cannot write it. **The owner adds and later
changes the test rows.** Sheet columns: `invoice_id, client_name, client_email, amount, currency,
due_date, status`.

## 2. Provider operations expected (complete list)

| Provider | Operation | Count | Triggered by |
| --- | --- | --- | --- |
| Google Sheets | Read the range (normal sync) | every tick (15 min) and any "Sync" click | existing schedule |
| Gemini | Draft generation, step 1 | 1 (+1 for `TEST-M12-002`) | the scheduled tick or the owner's "Draft" click |
| Gemini | Reply classification | 1 per reply (2 planned) | the worker, after the poll stores a reply |
| Gmail send | **Exactly 1** message to `getdera6591@gmail.com` | 1 | **the owner's "Send" click** after approval |
| Gmail read | History and metadata reads (`gmail.readonly`) | every 5 min | existing schedule |

AI budget: 15 generations per 24 hours. Unrelated to this test, the scheduled tick will also draft step
2 for INV-002, INV-005, INV-006 and INV-007 at about 19:40–20:30 UTC today (4 calls; drafts only, never
sent). With this test (3–4 calls) the total stays well under 15.

## 3. Steps, evidence and expected state

The owner acts in the UI; Claude only observes (read-only SQL, logs, read-only API calls in the api
container) and records the evidence.

| # | Scenario | Who does what | Expected state transition (evidence) |
| --- | --- | --- | --- |
| 0 | Baseline | Claude | Verified backup `collectflow-before-m12-1-<ts>.dump`; snapshot of counts and hashes; outbox idle; send and Gemini counters recorded |
| 1 | Sheets import | Owner adds the row(s); clicks **Sync** (or waits ≤15 min) | `Invoice TEST-M12-001` created, `in_sheet=true`, `UNPAID`; client upserted; `INVOICE_CREATED` timeline event; the other 8 invoices' hashes unchanged |
| 2 | Sequence | automatic (reconcile) | `SequenceEnrollment` ACTIVE on "Standard collection" v1, step 1 due; `SEQUENCE_ENROLLED` event |
| 3 | Gemini draft | tick, or owner clicks **Draft** | `GenerationAttempt(purpose=DRAFT, outcome=SUCCESS)`, `FollowUp DRAFT`, tone FRIENDLY, budget counter +1; Today "Drafts awaiting approval" lists it |
| 4 | Human edit | Owner edits subject/body | `last_edited_by` set; edit event |
| 5 | Approval rules | Owner: (a) checks the draft offers no **Send** before approval; (b) approves. Optional: edit then approve `TEST-M12-002` | (a) Send appears only on an APPROVED reminder (the server also refuses it, covered by the tests); (b) `APPROVED` with snapshot. Optional: `403 self_approval` (four-eyes), then the owner **rejects** that draft |
| 6 | **One live send** | **Owner clicks Send once** | One `DeliveryAttempt SENT` (`num_retries=0`), Gmail message and thread ID, RFC Message-ID; `FollowUp SENT`; step 1 completed; next step 2026-10-28 or later (cooldown 7 days, so after 2026-10-30). Mail visible in diresaman Sent and getdera Inbox. Delivery-attempt count +1 exactly |
| 7 | Reply A (promise) | Owner replies **in the same thread** from getdera with text R1 (section 4) | Within ~5 min: one `InboundMessage` linked to `TEST-M12-001` (`sender_check` MATCH), an AWAITING_CLASSIFICATION hold placed in the same transaction |
| 8 | Dedup and classification | automatic (polls continue) | Several further polls store no duplicate; one `classify` job; `GenerationAttempt(purpose=CLASSIFY)`; label `PROMISE_TO_PAY` (confidence ≥ 0.8, else held for review); append-only classification row |
| 9 | Chasing blocked | Claude checks read-only; owner may click **Draft** to see the refusal | Eligibility reports a hold; draft request refused (409); Today shows the reply under "Replies to review"; the tick drafts nothing for this invoice |
| 10 | Human confirms | Owner opens the reply in the Inbox and **confirms the promise** (prefilled date) | `PromiseToPay OPEN`; promise-owned hold; reply hold handed over; HUMAN classification next to the AI's; `PROMISE_RECORDED` event |
| 11 | Payment claim | Owner sends reply B (text R2) in the same thread | New reply → label `CLAIMS_PAID` → `RECONCILE` task + reply hold; **invoice stays `UNPAID`** (status only ever comes from the sheet) |
| 12 | Consistency and audit | Claude | Exactly the expected holds (promise + claims-paid), one OPEN promise, one OPEN RECONCILE task; enrollment ACTIVE but blocked; the timeline shows the full chain sync → enroll → draft → edit → approve → send → reply → classify → promise → reply → task; no other invoice changed; Gmail sends total +1; Gemini calls as in section 2 |

## 4. Reply texts (owner sends from getdera6591@gmail.com, replying in the thread)

- **R1 (promise):** "Hi, thanks for the reminder about TEST-M12-001. We will pay the full 900 USD on
  28 October 2026. Regards, TEST Acceptance Co"
- **R2 (claims paid):** "Hello again, actually our accounts team says TEST-M12-001 was already paid
  yesterday by bank transfer, reference TEST-REF-4471. Please check."

## 5. Cleanup and rollback

- **No deletions.** Test records stay as the audit trail, clearly marked `TEST-`. The timeline and
  classification log are append-only by design.
- **Closing the test:** the owner (approver) cancels the promise with a note "M12.1 acceptance test",
  completes the RECONCILE task with a note, then **changes the sheet rows to `PAID`** (or deletes
  them). The next sync exits both enrollments, so nothing can ever be chased again. Rejected drafts stay
  rejected.
- **If something goes wrong:** the owner stops clicking; no automatic retry exists for a delivery with
  an unknown outcome (`DELIVERY_UNKNOWN` is reconciled by a person only). Removing the row from the
  sheet stops all chasing of the test invoice. The database backup from step 0 is **not** restored
  automatically (other legitimate writes would be lost); the application images are unchanged (no
  deployment in this test).
- Gmail messages stay in both owner mailboxes; the owner may delete them.

## 6. Constraints kept

No real customer is contacted; no existing invoice is modified; no OAuth scope, credential or
`.env` change; scheduled jobs stay on; approval, cooldown, hold and delivery checks are not bypassed;
no send is initiated by Claude or by any job.

## 7. Owner authorizations requested

1. Add the row `TEST-M12-001` (and optionally `TEST-M12-002`) to the Google Sheet, and let CollectFlow
   sync it.
2. Allow the resulting Gemini calls: 1–2 drafts and 2 classifications.
3. **Send exactly one email** to `getdera6591@gmail.com` for `TEST-M12-001`, by the owner clicking
   **Send** in the drawer after approving.
4. Send replies R1 and R2 from `getdera6591@gmail.com` and let the scheduled polls read them.
5. Confirm the promise, try the refused actions in steps 5 (optional) and 9, and close the test as in section 5.
6. Claude takes a backup and read-only snapshots throughout, and writes the Stage C report.


## 8. Stage C: results (2026-10-23, times UTC)

Live providers: Google Sheets (the owner's sheet), Gmail (sender `diresaman@gmail.com`), Gemini
(`gemini-3.5-flash`, free tier). Only `getdera6591@gmail.com` was emailed. Most steps ran on the M12 deployment;
from 11:09 the M13/M14/UX version was live (the promise confirmation and cleanup ran on it).

| # | Scenario | Result | Evidence |
| --- | --- | --- | --- |
| 0 | Baseline | PASS (late) | The owner started before the baseline; the backup `collectflow-m12-1-before-reply-20261023T061258.dump` was taken before the first reply |
| 1 | Sheets import | **PASS** | TEST-M12-001 (900 USD) and TEST-M12-002 (6,000 USD) imported at 06:05:18 |
| 2 | Sequence | **PASS** | Both enrolled at 06:05:18 on "Standard collection v1" |
| 3 | Gemini draft | **PASS** | TEST-M12-001 drafted at 06:05:27. TEST-M12-002 was refused at 06:05:29 (HTTP 429, quota); the backoff held and it drafted at 07:46:04 after the pause |
| 4 | Human edit | **PASS** (on TEST-M12-002) | Not done on TEST-M12-001; TEST-M12-002's body was edited by the owner at 08:15:55 |
| 5 | Approval rules | **PASS** | TEST-M12-001 (under the threshold) was approved by the owner. TEST-M12-002 (6,000 USD, edited by the owner): the owner could not approve it (no approve request reached the server), then **rejected** it. The server-side refusal itself is covered by the automated tests and the browser suite (journey 7) |
| 6 | One live send | **PASS** | Exactly one delivery attempt: SENT at 06:10:20 to `getdera6591@gmail.com`, thread `1a16761c868f7a70`. Delivery attempts in total: 7 before and 7 after the whole test (the 7th is this send) |
| 7 | Reply A (promise) | **PASS** | Stored once at 06:15:49 (sender MATCH); an AWAITING_CLASSIFICATION hold placed in the same transaction |
| 8 | Deduplication and classification | **PASS** after a quota delay | 3 replies, each stored once over many polls. Reply A: attempts at 06:15 and 06:35 met the Gemini quota pause; attempt 3 at 07:05 was spent inside the pause (**finding F1**); a manual retry at 07:59 classified it PROMISE_TO_PAY (0.98) → review required |
| 9 | Chasing blocked | **PASS** | Holds kept TEST-M12-001 out of chasing all day; every scheduled tick generated 0 drafts for it |
| 10 | Human confirms the promise | **PASS** after a usability issue | At first the owner confirmed only the AI's *label* ("Confirm Promise to pay"), which records no promise (**finding F2**). The promise was recorded at 15:49:52 from reply 3 (900 USD, due 2026-10-28); that reply's hold was handed over to the promise |
| 11 | Payment claim | **PASS** | Reply B at 08:08 was classified CLAIMS_PAID (0.95) by Gemini and acted on automatically: a RECONCILE task and a protected hold. **The invoice stayed UNPAID** until the Sheet said PAID |
| 12 | Consistency and audit | **PASS** | The owner set the Sheet rows to PAID and synced (15:52, 15:55, 15:58). **The promise turned KEPT automatically** ("Paid within the grace period") and its hold was released. The task was completed at 15:54. Every enrollment exited with reason `paid`. 50 timeline events on the TEST invoices record the full chain, from import to promise kept. No other invoice changed. No pending draft or reminder remains |

**Gemini usage for the test: 8 calls.**
- **Drafts (3):** one succeeded, one quota refusal, one succeeded after the pause.
- **Classifications (5):**
  - one quota refusal at 06:35;
  - three successes at 07:59, 08:11 and 08:20;
  - one failed with "unavailable" at 15:43, a manual retry on reply 3.

The quota refusals and the backoff behaved as designed: nothing was sent and chasing stayed blocked.

**Additional behaviour observed:** TEST-M12-002 was briefly set back to UNPAID in the Sheet (15:55). CollectFlow
correctly re-enrolled it (next step on 30 Oct, only a draft for approval), then exited again when the Sheet said
PAID at 15:58.

**Findings:**

| # | Finding | Status |
| --- | --- | --- |
| F1 | A classification retry could be spent inside the longer quota pause created by the previous failure | **Fixed in M14** (the attempt is deferred, not spent); not yet re-verified live |
| F2 | "Confirm Promise to pay" saved the label only; recording the promise was a separate box | **Fixed in UX pass 1**: for promise and dispute replies, the main action is now "Record promise to pay" / "Open dispute case" |
| F3 | Free-tier Gemini quota (about 20 a day) is too small for comfortable daily use | Open: consider a paid key, especially before the live AI evaluation (M15) |
| F4 | After the shared-layout change, links inside the app opened the page but not the record | **Fixed in M15**, not yet deployed |

**Leftovers (no action needed):** two reply-owned holds (the first promise reply and the payment-claim reply)
remain on TEST-M12-001. They no longer have any effect, because the invoice is PAID and its sequence has ended,
and they stay as part of the audit trail. The TEST rows remain in the Sheet as PAID.

**Verdict: M12.1 live-provider acceptance PASSED.**
- **What passed:** every P0 scenario was exercised once with live Sheets, Gmail and Gemini.
- **Safety:** exactly one email was sent, no invoice was marked paid by an email, and the protections held
  throughout.
- **Limitations:**
  - one run, by one person, so the four-eyes rule was shown in the interface rather than with two approvers;
  - F1 is fixed but not re-verified live;
  - F3 remains open.
