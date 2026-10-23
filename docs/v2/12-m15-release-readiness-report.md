# 12 — M15 release readiness report

- **Date:** 2026-10-23
- **Baseline:** `14f40ab` (M13, M14 and UX deployed and verified)
- **Scope:** testing and stabilisation only. No new business features, no production change, no live
  Gmail or Gemini call.
- **Final assignment release update (2026-10-23):** sections 1, 8, 9, 10 and 11, below. The code is unchanged
  since `7e46f80`.

## 0. Final assignment release status

| Item | Status |
| --- | --- |
| M12.1 live-provider acceptance | **PASSED** (`a6ace03`; `09-m12-live-provider-acceptance-report.md` §8) |
| Live 120-case AI evaluation | **NOT RUN.** The owner excluded it from the assignment scope. Not a release requirement |
| M16 reconciliation | **OUT OF SCOPE** |
| M15 deployment (with the navigation update and the complete UX redesign) | **PASSED**, deployed and verified 2026-10-23 (section 13) |
| M14 analytics parameters | Kept unchanged as an assignment demonstration, pending the owner's final review (section 11) |

**Recommendation:** released. Everything is deployed and verified (section 13); no release blocker remains.

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
| AI evaluation, live model quality (accuracy, dispute recall, injections) | **NOT RUN**: excluded from the assignment scope by the owner (about 120 Gemini calls) |
| CI pipeline (`.github/workflows/ci.yml`) | **NOT TESTED** remotely: the repository has no remote. Every step was run locally |
| Security and operational verification | **PASS** (section 7) |
| M12.1 live-provider acceptance | **PASS** (2026-10-23, after M15; see `09-m12-live-provider-acceptance-report.md` §8) |
| Production deployment (M13, M14, UX) | **PASS** (`14f40ab`). Final release with M15 and the redesign: **PASS** (section 13) |
| Re-run on the release commit (code identical to `7e46f80`) | **PASS**: `check`, `makemigrations --check`, `lint-imports` (2 kept), deterministic and simulated evaluations, type-check, lint. The demo stack was started and signed in to. The full suite and Playwright were not repeated: no code changed since they passed |

*Superseded by section 0:* the original M15 recommendation ("not yet ready for a full release") depended on
two open gates. M12.1 has since passed, and the owner has excluded the live evaluation from the assignment
scope.

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
| live | **NOT RUN** | Excluded from the assignment scope by the owner |

**Live run, if it is ever approved:**

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

## 8. Known issues and release blockers

| Issue | Severity | Status |
| --- | --- | --- |
| M15 fixes (deep links F4, Integrations job row, statement reconciliation event) | Closed | Deployed and verified 2026-10-23 (section 13) |
| Live AI quality not measured on the 120 cases | — | **Not run, by the owner's decision** for the assignment. Evidence: deterministic and simulated harness runs, and the M12.1 live test |
| Free Gemini quota too small for daily use (F3) | Low (for the assignment) | Open. A paid key is recommended for real use |
| M12.1 live acceptance | Closed | **Passed** 2026-10-23, findings F1–F4 recorded |
| Forecast week-1 concentration | Low | Correction ready, off until approved (section 11) |
| DSO and CEI unavailable until about 2026-11-15 (no history) | Low | By design |
| Unknown deliveries need an admin command (no screen) | Low | Documented, not a blocker |
| The payment/dispute wording screen catches 20/36 English cases | Low | A backstop; protected labels always go to a person |
| CI not run remotely | Low | Needs a git remote |
| Hypothesis state-machine tests (design §11) not added | Low | Covered by example-based and threaded PostgreSQL tests instead |
| M16 payment reconciliation | — | **Out of scope** |

**No critical security or workflow defect is open.** Section 7's guarantees hold on the M15 code. The
defects found in M15 and in M12.1 are fixed. Their deployment status is shown above.

### Implemented, tested and deployed

| Area | Implemented | Tested offline | Tested live (real providers) | In production |
| --- | --- | --- | --- | --- |
| Sheets sync, sequences, AI drafting, approval, Gmail delivery (M08–M09, M12) | Yes | Suite + browser | **Yes** (M12.1) | Yes |
| Reply reading and classification, holds (M10) | Yes | Suite + browser | **Yes** (promise, claims paid) | Yes |
| Promises, disputes, tasks (M11) | Yes | Suite + browser | Promise and task **yes**; dispute no (no live dispute reply was sent) | Yes |
| Roles, four-eyes, CSRF (M09.x, M12) | Yes | Suite + browser | Approval refusal on TEST-M12-002 **yes** | Yes |
| Client risk and worklist (M13) | Yes | Suite + browser | Computed on production data | Yes |
| Statements, analytics, forecast (M14) | Yes | Suite + browser | Analytics computed on production data; no live statement sent | Yes |
| UX shell, Approvals (UX) | Yes | Browser | Used in M12.1 | Yes |
| Deep-link fix, Integrations job row, statement reconciliation event (M15) | Yes | Suite + browser | — | **Yes** (final release) |
| Browser suite, evaluation harness, import contracts, CI (M15) | Yes | Run locally | — | Development and CI only |

## 9. M12.1 live acceptance: closed

Every owner action listed here before is done, and the Stage C report is written
(`09-m12-live-provider-acceptance-report.md` §8, commit `a6ace03`):
- **Invoices:** both TEST invoices are PAID from the sheet, and every enrollment exited with reason "paid".
- **Promise:** KEPT automatically.
- **Gmail:** 7 delivery attempts in total, exactly one of them a live test send.
- **Scope:** no other record changed.

## 10. M15 production deployment plan (prepared, not executed)

**Needs the owner's separate approval. Claude does not run it.**

**No migrations: verified.**
- No migration file changed between the deployed commit `14f40ab` and HEAD.
- `makemigrations --check` reports no changes.
- Production records 51 migrations: the 33 app migrations plus Django's built-in ones.
- The script refuses to continue if that count is not 51, before or after the api swap.

**Environment:** unchanged.
- No new required variable: `FORECAST_OVERDUE_LAG_DAYS` is a settings constant that defaults to off.
- `backend/.env`, the credentials and the token files are neither read nor modified.
- `backend/.env.example` gained one optional, commented setting (`SEQUENCE_DRAFTING_TIMEOUT_SECONDS`, which
  already defaulted to 900). It is a template only.

**Images:**
- Running now (M13, M14 and UX): api `a96708d810f7`, worker `ed158895c363`, web `0cce2d611845`.
- The script tags them `collectflow-{api,worker,web}:pre-m15` before building. The older `pre-m13` tags stay.

**Script:** `bash backend/backups/deploy-m15.sh`. It is git-ignored like the earlier deployment scripts, and
it sends no email and calls no AI provider. Steps:

| Step | Action | Stops if |
| --- | --- | --- |
| 0 | Preconditions: a clean tree; code identical to `7e46f80`; 51 migrations | Any check fails |
| 1 | Tag the running images `pre-m15` | — |
| 2 | `docker compose build api worker web` (the current stack keeps serving) | The build fails |
| 3 | **Pause the worker**, only if no job is running and no delivery is queued | Busy: try again in a minute |
| 4 | **Verified backup** while the worker is paused: `pg_dump -Fc`, permissions 600, a SHA-256 checksum, a restore into the scratch database `collectflow_restorecheck`, and a row-count comparison against live on 10 key tables. The scratch database is then dropped | The checksum or the counts differ |
| 5 | Recreate the api; wait until it is healthy; `manage.py check`; the migration count is still 51 | Unhealthy, or the count changed |
| 6 | Recreate the web app. It polls for up to 2 minutes until `/login` answers, then checks `/login`, `/today`, `/approvals`, `/analytics` and the health endpoint through the proxy | Never: a warning only |
| 7 | **Resume the worker** | — |
| 8 | Print the running images, the backup path and the rollback command | — |

**Fix carried over from M13:** that script ended before "resume the worker". The likely cause is
`set -e` with a `curl` five seconds after the web container was recreated: Next.js was not listening yet,
curl exited with code 7, and the worker stayed down for 2 h 20 min. The M15 script waits for the web app,
treats page checks as warnings, and always resumes the worker. On any fatal error it prints the worker's
state together with the exact resume and rollback commands.

**Read-only verification by Claude afterwards** (no login sessions minted; in-process checks inside a
rolled-back transaction, plus signed-out proxy checks):
1. **Images and backup:** the new image IDs are running with 0 restarts; the `pre-m15` tags exist; the
   backup and checksum files exist.
2. **Database:** 51 migrations, and the 10 key table counts equal those printed by step 4, apart from new
   worker activity (jobs, syncs).
3. **Pages:** all 12 pages return 200 signed out; API routes return 401 signed out; a cross-origin write
   returns 403.
4. **API:** in-process, Today, worklist, approvals, analytics and integrations return 200, and integrations
   lists the "Risk and analytics" job.
5. **Deep links:** the built web bundle contains the `useUrlParam` deep-link handling.
6. **Worker:** the first scheduled jobs succeed; no duplicate idempotency keys; nothing stuck.
7. **Providers:** delivery attempts are still 7, and there are 0 Gemini calls caused by the deployment.

**Rollback** (no database change is involved, so this is enough):
```
docker compose stop worker
for c in api worker web; do docker tag collectflow-$c:pre-m15 collectflow-$c:latest; done
docker compose up -d --no-deps --no-build api web worker
```

## 11. Analytics decision (final assignment release)

The deterministic M14 analytics stay as they are, as an **assignment demonstration**.
- **Formulas and parameters:** unchanged. The DSO and CEI definitions, the attribution window, the forecast
  probabilities and the M15 `FORECAST_OVERDUE_LAG_DAYS` proposal (still off) await the owner's final review.
- **Limitations:** documented in `docs/ANALYTICS.md`:
  - DSO and CEI need a full 30-day history; opening balances are never estimated;
  - the observed paid date differs from the bank settlement date;
  - the forecast's assumptions and uncertainty;
  - every calculation is separate per currency;
  - why forecast figures are not guaranteed collections.

## 12. Submission package

| Item | Location |
| --- | --- |
| Setup, Docker Compose start-up, feature reference | `README.md` |
| Environment template (no credentials) | `backend/.env.example`, `frontend/.env.example` |
| Architecture, safety, AI integration | `docs/ARCHITECTURE.md` |
| Analytics limitations | `docs/ANALYTICS.md` |
| Testing summary | `docs/TESTING.md` |
| Demonstration walkthrough (existing live evidence plus a fake-provider demo stack; no new live email) | `docs/DEMO.md` |
| Known limitations | `README.md` "Known limitations", and section 8 here |

## 13. Final production deployment record (2026-10-23): PASSED

**What was deployed:** M15, the navigation and terminology update (`6b49eb4`) and the complete UX redesign
(`877bea9`).
- **Code:** commit `67344ad`, whose code is identical to HEAD; later commits change documentation only.
- **Run:** the owner ran `backend/backups/deploy-final.sh`, the section 10 procedure updated for the final code.
  Claude cannot run production deployments. Claude verified the result read-only. Times are UTC.

| Check | Result |
| --- | --- |
| Backup | `backend/backups/collectflow-before-final-20261023T182130.dump` (permissions 600), taken with the worker paused. The checksum verified. The script restored it into a scratch database and compared 10 key tables with live before replacing anything |
| Rollback images | `collectflow-{api,worker,web}:pre-final` = `a96708d810f7`, `ed158895c363`, `0cce2d611845` (the M13, M14 and UX release) |
| New images | api `9e025f80b5b7` (running since 18:25:36), web `d807bfe8b4cf` (since 18:25:58), worker `dc62e4ac19aa` (since 18:26:05), each with 0 restarts |
| Migrations | 51 before and after; no migration applied |
| Records | Identical to the pre-deployment reference (18:15): 10 invoices, 9 follow-ups, 7 delivery attempts, 18 generation attempts, 69 timeline events, 4 replies, 2 tasks, 1 promise, 0 disputes |
| Providers | Gmail: 7 delivery attempts, unchanged. Gemini: no call since 15:43 (before the deployment) |
| Pages, signed out | All 12 return 200. `/` → 307 to `/today`. `/?invoice=x` → 307 to `/invoices?invoice=x` |
| API, signed out | `today`, `worklist`, `invoices`, `follow-ups`, `replies`, `tasks`, `disputes`, `analytics`, `integrations/status` and `timeline`: 401. A cross-origin write: 403. An unknown route: 404. Health: 200 |
| API, signed in (in process, in-memory users, rolled back; no session created) | 15 routes, including the client timeline and the statement preview: 200 for an operator and an approver, 403 without a role. The temporary users were rolled back (0 left) |
| Health | Services: Sheets, Gmail sending, Gmail reading, reply polling, Gemini and the worker are all healthy. Scheduled jobs: `sequences.tick`, `inbox.poll`, `inbox.purge`, `cases.monitor` and `insights.recompute` are all healthy |
| Worker | Started 18:26:05; reply polls succeed; no errors in the log; nothing failed or stuck; no duplicate idempotency keys |
| Web bundle | Contains the redesign (for example "Effectiveness (CEI)", "Chasing is stopped", "Prepare due reminders") and the deep-link fix. No test artefacts are in the image (`.dockerignore`, `67344ad`) |

**Rollback** (no database change is involved):
```
docker compose stop worker
for c in api worker web; do docker tag collectflow-$c:pre-final collectflow-$c:latest; done
docker compose up -d --no-deps --no-build api web worker
```

**Final status: PASSED.** Everything built for the assignment is in production. Scope exclusions decided by the
owner: the live 120-case AI evaluation (not run) and M16 reconciliation (out of scope).

