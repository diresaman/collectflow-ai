import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from evals import harness

RESULTS = harness.ROOT / "results"


class Command(BaseCommand):
    help = (
        "AI evaluations (design §11). --mode deterministic | recorded | live. Live mode calls Gemini and needs "
        "--confirm-live and --max-requests (the owner's approval); results are cached and resumable."
    )

    def add_arguments(self, parser):  # type: ignore[no-untyped-def]
        parser.add_argument("--mode", choices=["deterministic", "recorded", "live"], default="deterministic")
        parser.add_argument("--suite", choices=["replies", "drafting", "all"], default="all")
        parser.add_argument("--responses", help="recorded mode: JSON file with {'responses': {case_id: output}}")
        parser.add_argument("--max-requests", type=int, default=0, help="live mode: hard cap on Gemini calls this run")
        parser.add_argument("--confirm-live", action="store_true", help="live mode: required; calls Gemini")
        parser.add_argument("--cache", default=str(RESULTS / "live-cache.jsonl"), help="live mode: resumable cache")

    def handle(self, *args, **options):  # type: ignore[no-untyped-def]
        suites = ["replies", "drafting"] if options["suite"] == "all" else [options["suite"]]
        mode = options["mode"]
        if mode == "deterministic":
            result = harness.deterministic()
            self._print("DETERMINISTIC (no model output)", result)
            if not result["passed"]:
                raise CommandError("dataset or screen checks failed")
            return
        if mode == "recorded":
            if not options["responses"]:
                raise CommandError("--responses is required in recorded mode")
            data = json.loads(Path(options["responses"]).read_text())
            label = "SIMULATED" if "SIMULATED" in data.get("note", "") else "RECORDED"
            for suite in suites:
                result = harness.score_responses(suite, data["responses"])
                if result["scored"]:
                    self._print(f"{label} {suite} ({data.get('note', '')[:80]})", self._summary(suite, result))
            return
        # live
        if not options["confirm_live"] or options["max_requests"] <= 0:
            raise CommandError("Live mode calls Gemini: pass --confirm-live and a positive --max-requests "
                               f"(about {harness.deterministic()['estimated_live_requests']} requests in total).")
        from apps.followups import ai_budget
        from integrations.gemini import GeminiClient

        RESULTS.mkdir(exist_ok=True)
        remaining = options["max_requests"]
        for suite in suites:
            run = harness.LiveRun(suite=suite, cache=Path(options["cache"]), max_requests=remaining,
                                  client_factory=GeminiClient.from_settings, budget_status=ai_budget.status)
            result = run.run()
            remaining -= run.requests
            self._print(f"LIVE {suite}", {"requests_made": result["requests_made"], "stopped": result["stopped"]}
                        | self._summary(suite, result))
            if run.stopped and "all cases" not in run.stopped and "budget" not in run.stopped:
                break

    @staticmethod
    def _summary(suite: str, result: dict) -> dict:  # type: ignore[type-arg]
        summary = {"scored": f"{result['scored']}/{result['of']}", "metrics": result["metrics"]}
        if suite == "replies" and result["metrics"]:
            m = result["metrics"]
            summary["targets"] = {
                "accuracy>=0.90": m["accuracy"] is not None and m["accuracy"] >= harness.TARGETS["accuracy"],
                "dispute_recall>=0.95": m["dispute_recall"] is not None and m["dispute_recall"] >= harness.TARGETS["dispute_recall"],
                "injection_successes==0": m["injection_successes"] == 0,
            }
        return summary

    def _print(self, title: str, payload: dict) -> None:  # type: ignore[type-arg]
        self.stdout.write(f"== {title}")
        self.stdout.write(json.dumps(payload, indent=1, default=str))
