# Demonstration walkthrough (about 15 minutes)

The walkthrough combines two safe sources. **No step sends a real email or needs a new live AI call.**

- **A. The real deployment, viewed only** (http://localhost:3000). The M12.1 live acceptance test left real
  evidence on the controlled test invoices `TEST-M12-001` and `TEST-M12-002`:
  - a sheet import;
  - a Gemini draft;
  - an approved and delivered Gmail reminder;
  - customer replies classified by Gemini;
  - a promise kept, and the full history.

  Only *look* at these records. Do not approve, send or sync during the demo.
- **B. The demo stack**: fictional data, **fake Gemini and fake Gmail**, and its own throwaway database. Use it
  for every action (approve, send, record, resolve). It is the stack the browser tests use.

## Starting the demo stack (B)

PostgreSQL must be running (`docker compose up -d db`), with the backend virtualenv installed.

```bash
# Terminal 1: recreates the collectflow_e2e database, seeds it, runs the API on :8002 and a worker (fake providers)
backend/scripts/e2e_backend.sh

# Terminal 2: the web app on :3002, pointed at that API
cd frontend
API_BASE_URL=http://127.0.0.1:8002 NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8002 npm run build
API_BASE_URL=http://127.0.0.1:8002 npx next start -p 3002 -H 127.0.0.1
```

- **Address and sign-in:** open http://127.0.0.1:3002. The fictional users are `e2e-operator` (Olga, operator),
  `e2e-approver` (Abel) and `e2e-approver2` (Amara), all with the password `e2e-pass-123`.
- **Safeguards:** the scripts refuse any database whose name does not end in `_e2e`, and Gmail and Gemini are
  replaced by fakes. To reset the data, stop terminal 1 and start it again.
- **Rebuilding:** rebuild the real web image (`docker compose build web`) only during a deployment. The demo
  build above lives in `frontend/.next` and does not touch the containers.

## The walkthrough

| # | Topic | Where | What to show |
| --- | --- | --- | --- |
| 1 | **Invoice synchronisation** | A: **Integrations** (Google Sheets), then **Invoices** | The last successful sync and the imported invoices. Open `TEST-M12-001` → **History**: "invoice created" from the sheet, later "updated" to PAID. Point out that the sheet is read-only, and that only the sheet marks an invoice paid. *Avoid pressing Sync Invoices live: it is read-only on the sheet, but newly due sequence steps could then be drafted with Gemini* |
| 2 | **Today and client prioritisation** | B: **Today**, then **Clients** | Today groups work into *Needs your attention*, *Waiting for approval or sending*, *Blocked or at risk*, and *Due now and coming up*. Each item has one action and says who can act; an operator sees "For an approver". On **Clients**, open **Harbor Analytics** → **Why this score**: oldest invoice overdue, broken promises (15 of 25), unanswered reminders. The ranking is risk × outstanding, per currency. A shows the same pages with real data |
| 3 | **AI-generated reminder** | A: `TEST-M12-001` drawer (real Gemini draft) · B: **Elm Studio** `EL-400` | In A: the real draft, its tone chosen by the backend, and the generation attempt in History. In B: open the draft, edit the text, **Save changes**. Explain what Gemini receives (invoice facts, never the email address) and the validators (`docs/ARCHITECTURE.md` §4) |
| 4 | **Human approval and Gmail delivery** | B: `EL-400`, then **Falcon Logistics** `FA-500` · A: `TEST-M12-001` | On `EL-400`: **Approve reminder** (nothing is sent), then **Send email…** → confirm. It shows "Sent via Gmail" (fake). On `FA-500` (firm, 6,500 USD): the operator is refused; an approver who edited it is refused (four-eyes); `e2e-approver2` approves. In A: the single real delivery, with its Gmail message ID, in History. Mention **Umbra Media** `UN-900`: "Delivery outcome unknown" is never offered for resending |
| 5 | **Incoming reply classification** | B: **Inbox**, reply on `RB-300` · A: **Inbox**, the `TEST-M12-001` replies | *What the customer wrote* and *What the AI suggests* are shown separately; the decision is saved as "Decided by a person". In A: the real Gemini labels (promise to pay, claims paid). Chasing stayed on hold, and "claims paid" created a reconciliation task instead of marking the invoice paid |
| 6 | **Promise and dispute handling** | B: reply on `RB-301`, then **Disputes** `DW-201` · A: `TEST-M12-001` | `RB-301`: **Record the promise to pay**, with the AI's date pre-filled. `DW-201`: the operator adds a note and sees "Only an approver can resolve"; the approver chooses **Invoice stands (chasing may resume)** → **Resolve dispute**. In A: the promise turned **KEPT** by itself once the sheet showed PAID |
| 7 | **Analytics and statements** | A or B: **Analytics** · B: **Summit Partners** | Aging per currency, the forecast labelled an *estimate*, the paid-date caveat, and DSO/CEI showing "insufficient history" rather than an estimate (`docs/ANALYTICS.md`). On Summit Partners: **Consolidated statement** → **Prepare statement draft** (fake Gemini writes only the opening and closing; the backend writes the lines and totals). In **Approvals** → **Review draft**, the approver sees "Invoices in this statement" and can **Approve statement**. The stale statement in the seed data shows its changed line and cannot be approved |
| 8 | **Audit history and integration health** | A: any invoice → **History**; **Integrations** | Every sync, draft, edit, approval, send, reply, decision and hold change, as append-only events. Integrations shows the health of Sheets, Gmail sending and reading, Gemini (with its quota pause) and the worker, plus every scheduled job, including "Risk and analytics" (after the M15 deployment) |

## Talking points

- The AI proposes and the backend decides. Approving never sends, and every send is confirmed separately.
- Gmail is called at most once per reminder. An unknown outcome is checked by a person, never retried.
- A promise, dispute or "we paid" reply stops chasing. An email never marks an invoice paid.
- The forecast is an estimate, calculated separately per currency.
- The live acceptance test ran on real Sheets, Gmail and Gemini with controlled test addresses
  (`docs/v2/09-m12-live-provider-acceptance-report.md` §8).
