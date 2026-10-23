# Testing summary

All figures are from the final M15 code (commit `7e46f80`). Later commits change documentation only. The full
record is in `docs/v2/12-m15-release-readiness-report.md`.

| Check | Command | Result |
| --- | --- | --- |
| Backend test suite (PostgreSQL 16, one process) | `python manage.py test --noinput --parallel 1` | **588 tests, OK** (665 s) |
| Django system checks | `python manage.py check` | **PASS**, re-run on the release commit |
| Migrations match the models | `python manage.py makemigrations --check --dry-run` | **PASS**, re-run on the release commit |
| Import contracts | `lint-imports` | **2 kept, 0 broken**, re-run on the release commit |
| Frontend type-check and lint | `npm run typecheck`, `npm run lint` | **PASS**, re-run on the release commit |
| Frontend production build | `npm run build` | **PASS** (M15) |
| Browser tests (Playwright, Chromium, fake providers) | `npm run test:e2e` | **23 passed**: 19 desktop journeys and negative paths, 1 phone layout, 3 sign-in setups |
| AI evaluation, deterministic | `python manage.py run_ai_evals --mode deterministic` | **PASS**, re-run on the release commit |
| AI evaluation, simulated answers | `run_ai_evals --mode recorded --responses evals/recorded/simulated-*.json` | **PASS**. This checks the harness only, not the model |
| AI evaluation, live model (120 calls) | `run_ai_evals --mode live --confirm-live` | **NOT RUN**: the owner excluded it from the assignment scope |
| Live-provider acceptance (real Sheets, Gmail and Gemini) | Manual, controlled test records | **PASSED** (M12.1, 2026-10-23) |
| CI pipeline | `.github/workflows/ci.yml` | **Defined, not run remotely**: the repository has no remote. Every step was run locally |

## What the tests cover

- **Backend unit and integration tests**:
  - the eligibility, tone and approval rules;
  - the reminder and statement state machines;
  - sequences and the risk score;
  - analytics and the forecast, separately per currency;
  - reply classification and triage, including the injection and sender checks;
  - holds, promises, disputes and tasks;
  - the outbox and the worker, sync, and delivery reconciliation.
- **Concurrency on real PostgreSQL**, in threaded tests with separate connections:
  - each job is claimed once (`SKIP LOCKED`);
  - parallel approvals have one winner;
  - parallel send requests call Gmail at most once;
  - a worker restart does not repeat a job.
- **Security**:
  - a route-wide test shows that every endpoint except health and login returns 401 without a session and 403
    without a role;
  - writes without CSRF are refused;
  - role and four-eyes refusals hold;
  - the timeline cannot be changed;
  - scheduled code cannot import the Gmail send path (import contracts and an architecture test).
- **Providers are always mocked in tests.** No test makes a network call or sends email. The automation
  tests replace Gmail's send with a function that fails the test if it is called.
- **Browser journeys**:
  - signing in and the navigation;
  - Today's next actions and their deep links;
  - client risk explained;
  - invoice blockers;
  - reply review;
  - edit, approve and send (fake Gmail);
  - four-eyes approval;
  - disputes, promises and tasks;
  - analytics labelled as estimates;
  - statements, including a stale one that cannot be approved;
  - an unknown delivery outcome that is never resent;
  - integration health;
  - CSRF, role and cross-site refusals;
  - the phone layout.

## Defects found during final QA

| Defect | Found by | Status |
| --- | --- | --- |
| Links inside the app opened the right page but not the record (F4) | Playwright, journeys 2 and 12a | Fixed in M15 (`useUrlParam` in `frontend/src/lib/url.ts`). **Not yet in production** |
| Reconciling a statement's delivery recorded the event on its first invoice only | Review | Fixed in M15, with a new test |
| A classification retry could be used up while Gemini was paused (F1) | M12.1 live test | Fixed in M14 |
| "Confirm Promise to pay" saved the label but not the promise (F2) | M12.1 live test | Fixed in UX pass 1 |

## Not covered

- **Model quality on the 80 + 40 evaluation cases.** Live evidence is limited to the M12.1 acceptance test.
- **Property-based (Hypothesis) state-machine tests.** The state rules are covered by example-based and threaded
  tests instead.
- **Remote CI.**
