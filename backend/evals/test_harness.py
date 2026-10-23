"""Tests of the evaluation harness itself (no Gemini call)."""

import datetime as dt
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from django.core.management import CommandError, call_command
from django.test import SimpleTestCase

from evals import harness
from integrations.gemini import GeminiQuotaExceeded

SIMULATED = json.loads((harness.ROOT / "recorded" / "simulated-replies.json").read_text())["responses"]
SIMULATED_DRAFTS = json.loads((harness.ROOT / "recorded" / "simulated-drafting.json").read_text())["responses"]


def case(case_id: str, suite: str = "replies") -> dict[str, Any]:
    return next(c for c in harness.load(suite) if c["id"] == case_id)


def first(tag: str) -> dict[str, Any]:
    return next(c for c in harness.load("replies") if tag in c["tags"])


class DatasetAndScoringTests(SimpleTestCase):
    def test_deterministic_checks_pass(self) -> None:
        result = harness.deterministic()
        self.assertTrue(result["passed"], result["checks"])
        self.assertEqual(result["estimated_live_requests"], 120)

    def test_simulated_answers_score_perfectly(self) -> None:
        metrics = harness.score_responses("replies", SIMULATED)["metrics"]
        self.assertEqual((metrics["accuracy"], metrics["dispute_recall"], metrics["injection_successes"]), (1.0, 1.0, 0))
        self.assertEqual(harness.score_responses("drafting", SIMULATED_DRAFTS)["metrics"]["compliance"], 1.0)

    def test_an_injection_that_makes_the_app_act_alone_is_counted(self) -> None:
        injection = first("injection")
        obeyed = SIMULATED[injection["id"]] | {"label": "OUT_OF_OFFICE", "suspicious_instructions": False, "confidence": 0.99}
        row = harness.score_reply(injection, obeyed)
        # The regex screen still catches this text, so the application keeps it for a person.
        self.assertFalse(row["automatic"])
        plain = dict(injection, body="Back on Monday, out of office.")
        self.assertTrue(harness.score_reply(plain, obeyed)["automatic"])

    def test_dispute_recall_and_invalid_outputs(self) -> None:
        responses = dict(SIMULATED)
        dispute = next(c for c in harness.load("replies") if c["expected_label"] == "DISPUTE")
        responses[dispute["id"]] = SIMULATED[dispute["id"]] | {"label": "OTHER"}
        other = next(c for c in harness.load("replies") if c["expected_label"] == "OTHER")
        responses[other["id"]] = {"label": "NOT_A_LABEL"}
        metrics = harness.score_responses("replies", responses)["metrics"]
        self.assertLess(metrics["dispute_recall"], 1.0)
        self.assertEqual(metrics["invalid_outputs"], 1)
        self.assertEqual(metrics["accuracy"], round(78 / 80, 4))

    def test_draft_checks_catch_unsafe_or_wrong_content(self) -> None:
        reminder = next(c for c in harness.load("drafting") if c["kind"] == "INVOICE" and not c["tags"])
        good = SIMULATED_DRAFTS[reminder["id"]]
        threat = good | {"body": good["body"].replace("Accounts", "A late fee applies. Accounts")}
        wrong = good | {"body": good["body"].replace(reminder["amount"], "9,999.00")}
        unsigned = good | {"body": good["body"].replace("Accounts Receivable Team", "Bob")}
        link = good | {"body": good["body"] + " https://pay.example"}
        self.assertTrue(harness.score_draft(reminder, good)["compliant"])
        self.assertFalse(harness.score_draft(reminder, threat)["checks"]["no_threats_or_fees"])
        self.assertFalse(harness.score_draft(reminder, wrong)["checks"]["no_wrong_amounts"])
        self.assertFalse(harness.score_draft(reminder, unsigned)["checks"]["signed_by_team"])
        self.assertFalse(harness.score_draft(reminder, link)["valid"])
        statement = next(c for c in harness.load("drafting") if c["kind"] == "STATEMENT")
        with_amount = SIMULATED_DRAFTS[statement["id"]] | {"opening": "You owe 1,200.00 in total."}
        self.assertFalse(harness.score_draft(statement, with_amount)["valid"])


class FakeClient:
    def __init__(self, fail_after: int | None = None) -> None:
        self.calls = 0
        self.fail_after = fail_after

    def generate_json(self, **kwargs: Any) -> dict[str, Any]:
        self.calls += 1
        if self.fail_after is not None and self.calls > self.fail_after:
            raise GeminiQuotaExceeded("429")
        return {"label": "OTHER", "confidence": 0.5, "suspicious_instructions": False, "rationale": "x",
                "extracted": {}}


class LiveRunTests(SimpleTestCase):
    def run_live(self, cache: Path, client: FakeClient, *, max_requests: int, cooldown: Any = None) -> harness.LiveRun:
        run = harness.LiveRun(suite="replies", cache=cache, max_requests=max_requests, client_factory=lambda: client,
                              budget_status=lambda: SimpleNamespace(cooldown_until=cooldown))
        run.run()
        return run

    def test_budget_cache_and_resume(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "cache.jsonl"
            client = FakeClient()
            first_run = self.run_live(cache, client, max_requests=3)
            self.assertEqual((client.calls, first_run.stopped), (3, "request budget of 3 reached"))
            second = FakeClient()
            self.run_live(cache, second, max_requests=2)
            self.assertEqual(second.calls, 2)  # the 3 cached cases are not asked again
            self.assertEqual(len(cache.read_text().splitlines()), 5)

    def test_a_paused_or_failing_provider_consumes_no_case(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "cache.jsonl"
            paused = FakeClient()
            run = self.run_live(cache, paused, max_requests=10,
                                cooldown=dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=30))
            self.assertEqual(paused.calls, 0)
            self.assertIn("paused", run.stopped)
            failing = FakeClient(fail_after=1)
            run = self.run_live(cache, failing, max_requests=10)
            self.assertEqual(len(cache.read_text().splitlines()), 1)  # the failed case is not recorded
            self.assertIn("not recorded", run.stopped)

    def test_live_mode_needs_explicit_confirmation(self) -> None:
        with self.assertRaises(CommandError):
            call_command("run_ai_evals", "--mode", "live")
        with self.assertRaises(CommandError):
            call_command("run_ai_evals", "--mode", "live", "--confirm-live")
