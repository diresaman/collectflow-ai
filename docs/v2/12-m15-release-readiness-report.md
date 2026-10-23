# 12 — M15 release readiness report

- **Date:** 2026-10-23
- **Baseline:** `14f40ab` (M13, M14 and UX deployed and verified)
- **Scope:** testing and stabilisation only. No new business features, no production change, no live
  Gmail or Gemini call.

## 1. Summary

| Gate | Status |
| --- | --- |
| Full backend regression (PostgreSQL, isolated) | **PASS**: 588 tests, OK (final isolated run on the M15 code) |
| Django system checks; migrations match the models | **PASS** |
| Import contracts (import-linter) | **PASS**: 2 kept, 0 broken |
| Frontend type-check, lint, production build | **PASS** |
| Playwright browser suite (committed, fake providers) | **PASS**: 23 passed (19 desktop journeys and negative paths, 1 phone, 3 sign-in setups) |
| AI evaluation harness, deterministic checks | **PASS** |
| AI evaluation, simulated answers (harness check only) | **PASS** (SIMULATED: not a model measurement) |
| AI evaluation, live model quality (accuracy, dispute recall, injections) | **NOT TESTED**: needs owner approval (about 120 Gemini calls) |
| CI pipeline (`.github/workflows/ci.yml`) | **NOT TESTED** remotely: the repository has no remote. Every step was run locally |
| Security and operational verification | **PASS** (section 7) |
| M12.1 live-provider acceptance | **BLOCKED**: owner actions pending (section 9) |
| Production deployment (M13, M14, UX) | **PASS** (`14f40ab`). The M15 changes are **not deployed** |

**Recommendation: not yet ready for a full release.** The software side is release-candidate quality: all
offline checks pass and the browser suite found and fixed a real defect. Two mandatory gates remain open, the
live AI quality evaluation and the M12.1 live acceptance, and both need the owner's approval for live
provider use. A controlled internal release on the current deployment is reasonable. M15's fixes should be
deployed (section 10) before the live evaluation, so the measurement uses the final code.

## 2. Backend regression

| Run | Code | Result |
| --- | --- | --- |
| 1 (baseline, isolated) | `14f40ab` | **577 tests, OK** in 643 s. The first complete full run on the M14 and UX code (537 from M13 plus 40 from M14) |
| Targeted runs during M15 (one at a time) | M15 work in progress | `evals apps.insights apps.core`: 96 tests, 1 failure (below), fixed; `apps.insights.tests.test_worklist`: 11 OK; `followups.tests.test_statements` and `test_delivery`: 55 OK |
| Final (isolated) | M15 | **588 tests, OK** in 665 s (577 + 8 eval-harness + 2 forecast-lag + 1 statement-reconciliation tests) |

**The one failure (an outdated assertion, not an application defect):** M13's `test_today_is_unchanged`
asserted that the word "risk" never appears in the Today data. Since M15 adds the "Risk and analytics" job to
integration health, a stale job can legitimately appear there. The assertion now checks the actual intent:
no risk-score fields (`risk_score`, `risk_factors`, `top_factors`, `priority`, `low_confidence`) in Today.
Nothing was weakened.

**Environment rule kept:** never run two backend test processes at once (they share `test_collectflow_dev`).
Every run above ran alone. The browser suite uses its own `collectflow_e2e` database, so it never collides.

## 3. Playwright browser suite

Playwright `1.64.1` is now a pinned dev dependency (`frontend/package.json`); `npm run test:e2e` runs it, and
`npx playwright install chromium` fetches the matching browser on a new machine. `playwright.config.ts` starts:

- `backend/scripts/e2e_backend.sh`: recreates `collectflow_e2e`, seeds one fictional record set per journey
  (`apps/core/e2e.py`), and runs the API (`manage.py e2e_server`) and a worker (`manage.py e2e_worker`) with fake
  Gemini and Gmail. The fake Gmail returns "delivery unknown" for one recipient. The e2e commands refuse any
  database not ending in `_e2e` or with DEBUG off;
- a production build of the web app (`next build && next start`).

| # | Journey | Role(s) | Result |
| --- | --- | --- | --- |
| 1 | Signed out → sign-in page; grouped sidebar, role and live navigation with the shell kept mounted | anonymous, operator | PASS |
| 2 | Today: first group "Needs your attention", action buttons, "For an approver"; **clicking "Review reply" opens that reply** | operator | PASS |
| 3 | Client risk score explained (Broken promises 15/25, oldest invoice) | operator | PASS |
| 4 | Invoice drawer explains the dispute blocker and who ends it | operator | PASS |
| 5 | Reply review: customer, AI and decision separated; only the fields for the label; decision saved | operator | PASS |
| 6 | Edit a reminder, save, approve, send (fake Gmail) → "Sent via Gmail" | operator | PASS |
| 7 | Four-eyes: operator refused; editing approver refused (self-approval); second approver approves | operator, approver, approver 2 | PASS |
| 8 | Dispute: operator adds a note and sees "Only an approver can resolve"; approver resolves "invoice stands" | operator, approver | PASS |
| 9 | Promise recorded from a reply, with the AI's date prefilled | operator | PASS |
| 10 | Task assigned (confirmation) and completed with a note (confirmation) | operator | PASS |
| 11 | Analytics: aging, forecast labelled an estimate, paid-date caveat, "opening balances never estimated" | operator | PASS |
| 12a | Statement prepared (fake Gemini, real worker job), listed in Approvals, opened by an approver with lines and totals | operator, approver | PASS |
| 12b | **Stale statement:** the changed line is shown; approval is refused; no Send offered | approver | PASS |
| 13a | **Unknown delivery:** explained on Today and in the drawer; never offered for resending | operator | PASS |
| 13b | Integrations summary, and the "Risk and analytics" job listed | operator | PASS |
| N1 | **Missing CSRF token** → 403 `csrf_failed` | operator | PASS |
| N2 | **Operator resolving a dispute through the API** → 403 `approver_required` | operator | PASS |
| N3 | **Write from another site** → 403 at the proxy | approver | PASS |
| M | **Phone (390 px):** menu drawer, Today, Approvals, no horizontal overflow | operator | PASS |

**Defect found and fixed by the suite (release blocker, now fixed):** after the shared layout (`96bedac`),
pages read their deep-link parameter (`?invoice=`, `?reply=`, `?case=`, `?task=`, `?client=`) only once, at
mount. Clicking **Review draft** on Approvals, or **Review reply** on Today, opened the right page but not the
record. The pages now follow `useSearchParams` (`useUrlParam` in `lib/url.ts`); the layout has the required
Suspense boundary. Journey 2 and 12a guard it. The first run had 4 failures: this defect (2 tests) and 2
selector mistakes in the tests themselves (a duplicated text match, an option label). The second run passed
in full.

## 4. AI evaluation harness (design §11)

`backend/evals/`: datasets in `datasets/`, harness `harness.py`, command `manage.py run_ai_evals`.

| Dataset | Content |
| --- | --- |
| `replies.json` (80) | Every label (promise 14, dispute 17, claims paid 13, requests info 11, other 10, out of office 8, wrong contact 7); 15 multilingual (es, fr, de), 4 sarcastic, 4 multi-invoice, 8 prompt-injection attempts. `must_hold` marks replies that must never resume chasing automatically |
| `drafting.json` (40) | 33 single-invoice reminders across the three tone bands and steps 1–3, including 6 broken promises and 3 with instructions hidden in the client name; 7 consolidated statements with 2–5 lines (one in two currencies) |

**Scoring uses the application's own code**: the reply validator and `triage_reason`, so a result means what
the app would do, and the draft and statement validators. Reported metrics:
- **Classification accuracy** (target ≥ 90 %).
- **Dispute recall** (target ≥ 95 %), plus "disputes kept on hold".
- **Successful injections** (target 0): an injection text the app would act on automatically, without a person.
- **Must-hold replies that would resume chasing.**
- **Draft compliance:** the validator, the invoice number, no wrong amounts, the missed promise date when
  relevant, no threats or fees, no "payment received" claim, no email address, the team sign-off, and the
  backend lines block for statements.

| Mode | Status | Result |
| --- | --- | --- |
| deterministic | **PASS** | All 8 dataset checks pass. Deterministic screens: the injection regex catches 8/8 injection texts; the payment/dispute wording screen catches 20/36 English promise, dispute and payment replies (a backstop only: those labels always go to a person anyway) |
| recorded, `recorded/simulated-*.json` | **PASS (SIMULATED)** | Hand-written oracle answers: accuracy 1.0, dispute recall 1.0, 0 injections, compliance 1.0. **This proves the harness works, not the model** |
| live | **NOT TESTED** | Awaiting approval |

**Live run, when approved:**

```
python manage.py run_ai_evals --mode live --confirm-live --max-requests 20   # repeat until all 120 are cached
python manage.py run_ai_evals --mode live --confirm-live --max-requests 120  # with a paid key
```

- **Request budget:** about 120 calls (80 classifications and 40 drafts), plus retries for any case that was
  never answered.
- **Free tier:** about 20 a day, shared with production drafting, so roughly 6 days. A paid key would finish
  it in one run.
- **Running in steps:** each answered case is cached (`evals/results/live-cache.jsonl`, git-ignored). A rerun
  skips cached cases.
- **Stopping safely:** the run stops before any call while Gemini is in its quota pause. On a quota or
  availability error it stops without recording the case. `--max-requests` is a hard cap.
- **Accounting gap:** the live calls do not create `GenerationAttempt` rows (those need an invoice). The
  in-app budget will not count them, but they do use the same provider quota.

## 5. Targeted stabilisation

| Item | Outcome |
| --- | --- |
| Forecast concentration in week 1 | Diagnosed: overdue invoices without payment history get "expected = today". A conservative correction exists but is **off by default**, because it changes an unapproved parameter: `FORECAST_OVERDUE_LAG_DAYS`. Proposed value `{"1_30": 7, "31_60": 14, "61_90": 21, "90_plus": 28}`. It places an invoice already past its expected date that many days ahead and says so in the row's explanation. Currency separation and the weekly totals are unchanged and tested. **Awaiting owner approval** |
| Integrations monitoring | The "Risk and analytics" job (`insights.recompute`, hourly) is listed with the other scheduled jobs, using the same health rules. Browser-tested |
| Unknown delivery handling | Reviewed: `reconcile_delivery` lists unresolved deliveries; it accepts only `--sent` or `--failed`, only from SENDING or DELIVERY_UNKNOWN, under a row lock with a conditional update and an audit event, and it never sends. Covered by existing tests, and now documented in the README. **One defect fixed:** reconciling a *statement* recorded the event only on its first invoice; it is now recorded on every listed invoice (new test). No new delivery-management feature (not a release blocker) |
| UX regression | Navigation, Approvals and the persistent shell are covered by the browser suite. The deep-link defect above was found and fixed |
| Classification retry in a quota pause (M14 fix) | Covered by the full suite |

## 6. Import boundaries and CI

- **`backend/.importlinter`** has two contracts.
  - **Scheduled and automatic code** (`sequences`, `inbox`, `automation`, `insights`, `cases`) cannot import
    `apps.followups.delivery` or `integrations.gmail`, directly or indirectly. Exemptions: test modules, and the
    integration health report's read-only use of `GmailClient.check_configured`.
  - **The Gmail reply reader** cannot import the send client.
  - **Result:** 2 kept, 0 broken.
  - **Sanity check:** adding a delivery import to `apps/insights/jobs.py` broke the first contract, and the
    change was reverted.
- **`backend/requirements-dev.txt`** pins `import-linter==2.3`. It is development and CI only; the production
  image is unchanged.
- **`.github/workflows/ci.yml`** has three jobs, each with its own PostgreSQL service and no Google credentials:
  - **backend:** check, migration check, `lint-imports`, the deterministic and simulated evaluations, and the
    full suite run in a single process with verbose output;
  - **frontend:** type-check, lint, build;
  - **e2e:** Playwright, with the HTML report and traces uploaded on failure.
- **Remote status:** not executed. There is no git remote, so nothing was pushed.

## 7. Security and operational verification

| Guarantee | Evidence |
| --- | --- |
| Authentication and roles | Full suite (route-wide 401/403 test, role tests); browser N2; signed-out redirect (journey 1) |
| CSRF | Full suite; browser N1 (`csrf_failed`); N3 (cross-origin refused by the proxy) |
| Approval separation (four-eyes, statements always need an approver) | Full suite; browser journeys 7 and 12 |
| Hold ownership; promise and dispute protections | Full suite (cases, P0 acceptance); browser journeys 4, 8 and 9 |
| Invoice eligibility, including statement lines | Full suite; browser 12b (a stale statement cannot be approved) |
| Transactional outbox; worker restart idempotency | Full suite (outbox concurrency, slot keys, restart tests); the e2e worker runs real outbox jobs |
| Gmail at most once; DELIVERY_UNKNOWN never retried | Full suite (P0 #12, delivery and statement concurrency); browser 13a |
| Scheduled jobs never approve or send | Architecture test and import contracts |
| Email payment claims never mark invoices paid | Full suite (P0 #10); evaluation `must_hold` metric |
| Secrets excluded | No `.env`, credentials, token, backup or key files are tracked, now or in history. No key-like strings in tracked files (one fake string inside a test). `.env`, `credentials/`, `backups/`, `evals/results/` and `e2e/.auth/` are git-ignored. No tokens or keys in `backend/logs/` or in the last 24 h of container logs |

## 8. Known issues

| Issue | Severity | Status |
| --- | --- | --- |
| Live AI quality not measured | High (release gate) | Needs approval and quota (section 4) |
| M12.1 live acceptance incomplete | High (release gate) | Owner checklist (section 9) |
| M15 fixes not in production (deep links, Integrations job row, statement reconciliation event) | Medium | Deployment plan (section 10). The deep-link defect is live in production since the UX deployment: clicking an action link opens the page but not the record |
| Forecast week-1 concentration | Low | Fix ready, off until approved |
| DSO and CEI unavailable until about 2026-11-15 (no history) | Low | By design |
| Unknown deliveries need an admin command (no screen) | Low | Documented; not a blocker |
| The payment/dispute wording screen catches 20/36 English cases | Low | A backstop; protected labels always go to a person |
| CI not executed remotely | Low | Needs a git remote |
| Hypothesis state-machine tests (design §11) not added | Low | Concurrency and state rules are covered by example-based and threaded PostgreSQL tests; property tests remain a gap |

**Analytics assumptions awaiting approval:**
- the M14 DSO and CEI definitions, the attribution window and the forecast probabilities
  (`10-m14-implementation-report.md` §3);
- the M15 `FORECAST_OVERDUE_LAG_DAYS` proposal.

## 9. M12.1 live acceptance: remaining owner actions

Only on the controlled test records (sender `diresaman@gmail.com`, customer `getdera6591@gmail.com`, invoices
TEST-M12-001 and TEST-M12-002), and only with the owner's approval. Nothing here runs automatically.

1. **Confirm the promise** on TEST-M12-001: Inbox → a promise reply → **Record the promise to pay** (date
   2026-10-28, amount 900).
2. **Reject the TEST-M12-002 draft**: open TEST-M12-002 → **Reject draft**. Do not approve or send it.
3. **Complete the reconciliation task**: Tasks → "Check whether invoice TEST-M12-001 was paid" → note → **Mark task complete**.
4. **Cancel the test promise** (approver): invoice TEST-M12-001 → promise → note "M12.1 acceptance test" → **Cancel promise**.
5. **Update the Sheet**: set both TEST rows to `PAID`, or remove them, then sync.
6. **Verify**:
   - both enrollments have exited;
   - no hold remains on the TEST invoices;
   - the invoices show PAID from the sheet;
   - Gmail delivery attempts are still 7;
   - there are no new Gemini calls beyond those expected.
7. **Write the Stage C report** in `09-m12-live-provider-acceptance-report.md`, with the results already
   recorded: one live send; reply ingestion and deduplication; live classification of a promise and a payment
   claim; the quota events; the retry fix; the label-versus-promise usability finding, which UX pass 1 fixed.

## 10. Deployment of the M15 changes (not done; needs approval)

M15 has **no migrations**. It is the same procedure as M12:
1. a verified backup;
2. `pre-m15` rollback images;
3. build;
4. pause the worker;
5. swap api, web and worker;
6. read-only verification.

The deep-link fix is the reason to deploy soon. Rollback is the `pre-m15` images; no database change.
