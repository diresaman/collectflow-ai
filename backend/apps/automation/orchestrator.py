"""
AutomationOrchestrator: sync invoices, evaluate the policy, and draft follow-ups with Gemini.

It reuses `sync_invoices` and `FollowUpGenerationService` unchanged and never calls Gmail:
generated drafts always wait for operator approval. No database transaction is opened here,
so none is held during Google Sheets or Gemini calls.
"""

import logging
from collections import defaultdict
from dataclasses import dataclass

from django.conf import settings
from django.utils import timezone

from apps.followups.models import FollowUp
from apps.followups.services import ActiveFollowUpExists, FollowUpError, FollowUpGenerationService, NotEligible
from apps.invoices.models import AutomationRun, Invoice
from apps.invoices.sheets_client import GoogleSheetsClient
from apps.invoices.sync import sync_invoices
from integrations.gemini import GeminiClient

from .lease import Lease, LeaseUnavailable
from .models import AutomationRunItem
from .policy import Decision, evaluate

logger = logging.getLogger(__name__)


class AutomationAlreadyRunning(Exception):
    def __init__(self, run_id: int | None) -> None:
        super().__init__("Another automation run is in progress.")
        self.run_id = run_id


class LeaseLost(Exception):
    pass


@dataclass(frozen=True)
class PlannedItem:
    """Dry-run result for one invoice."""

    invoice_external_id: str
    outcome: str  # "WOULD_GENERATE" or "SKIPPED"
    reason_code: str
    detail: str


class AutomationOrchestrator:
    def __init__(
        self,
        *,
        sheets_client: GoogleSheetsClient | None = None,
        gemini: GeminiClient | None = None,
    ) -> None:
        self._sheets_client = sheets_client
        self._gemini = gemini

    # Public API ---------------------------------------------------------------

    def run(self, *, trigger: str) -> AutomationRun:
        """Runs one automation. Raises AutomationAlreadyRunning if another run holds the lease."""
        try:
            lease = Lease.acquire()
        except LeaseUnavailable as exc:
            raise AutomationAlreadyRunning(exc.run_id) from None

        try:
            run = AutomationRun.objects.create(
                kind=AutomationRun.Kind.AUTOMATION, trigger=trigger, status=AutomationRun.Status.RUNNING
            )
            lease.attach_run(run)
            try:
                self._execute(run, lease)
            except LeaseLost:
                logger.error("Automation run %s lost its lease and stopped", run.pk)
                self._finish(run, AutomationRun.Status.FAILED, "Stopped: the run lost its lease after a stall.")
            except Exception:
                logger.exception("Automation run %s failed unexpectedly", run.pk)
                self._finish(run, AutomationRun.Status.FAILED, "Unexpected error; the run was stopped.")
            return run
        finally:
            lease.release()

    def plan(self) -> list[PlannedItem]:
        """Dry run: applies the policy to current data. No sync, no Gemini, no Gmail, no writes."""
        planned: list[PlannedItem] = []
        would_generate = 0
        for invoice, decision in self._decisions():
            if decision.eligible and would_generate >= settings.AUTOMATION_MAX_DRAFTS_PER_RUN:
                decision = Decision.skip("run_limit_reached", "Draft limit for one run reached.")
            if decision.eligible:
                would_generate += 1
                planned.append(PlannedItem(invoice.external_id, "WOULD_GENERATE", "", "Eligible for a new draft."))
            else:
                planned.append(PlannedItem(invoice.external_id, "SKIPPED", decision.reason_code, decision.detail))
        return planned

    # Execution ----------------------------------------------------------------

    def _execute(self, run: AutomationRun, lease: Lease) -> None:
        sync_run = sync_invoices(trigger=run.trigger, client=self._sheets_client)
        run.sync_run = sync_run
        run.save(update_fields=["sync_run"])
        if sync_run.status != AutomationRun.Status.SUCCEEDED:
            headline = sync_run.error_summary.split("\n", 1)[0]
            self._finish(run, AutomationRun.Status.FAILED, f"Synchronization failed; no invoices evaluated. {headline}")
            return
        self._heartbeat(lease)

        generator = FollowUpGenerationService(gemini=self._gemini)
        for invoice, decision in self._decisions():
            if decision.eligible and run.drafts_generated >= settings.AUTOMATION_MAX_DRAFTS_PER_RUN:
                decision = Decision.skip("run_limit_reached", "Draft limit for one run reached.")
            if not decision.eligible:
                self._record(run, invoice, AutomationRunItem.Outcome.SKIPPED, decision.reason_code, decision.detail,
                             decision.follow_up)
                continue

            # Generation enforces eligibility and uniqueness again at the service/database level.
            try:
                follow_up = generator.generate(invoice.pk)
            except (NotEligible, ActiveFollowUpExists) as exc:
                self._record(run, invoice, AutomationRunItem.Outcome.SKIPPED, exc.code, exc.message)
            except FollowUpError as exc:
                self._record(run, invoice, AutomationRunItem.Outcome.FAILED, exc.code, exc.message)
            except Exception:
                logger.exception("Unexpected error generating a follow-up in automation run %s", run.pk)
                self._record(run, invoice, AutomationRunItem.Outcome.FAILED, "unexpected_error",
                             "Unexpected error while generating the draft.")
            else:
                self._record(run, invoice, AutomationRunItem.Outcome.GENERATED, "", "Draft generated; awaiting approval.",
                             follow_up)
            self._heartbeat(lease)

        if run.generation_failures == 0:
            self._finish(run, AutomationRun.Status.COMPLETED)
        elif run.drafts_generated > 0:
            self._finish(run, AutomationRun.Status.PARTIAL,
                         f"{run.generation_failures} draft(s) could not be generated; see run items.")
        else:
            self._finish(run, AutomationRun.Status.FAILED,
                         f"All {run.generation_failures} draft generation attempt(s) failed; see run items.")

    @staticmethod
    def _decisions():  # type: ignore[no-untyped-def]
        invoices = list(Invoice.objects.order_by("due_date", "external_id"))
        follow_ups: dict[object, list[FollowUp]] = defaultdict(list)
        for follow_up in FollowUp.objects.filter(invoice__in=invoices).order_by("-generated_at"):
            follow_ups[follow_up.invoice_id].append(follow_up)
        now = timezone.now()
        for invoice in invoices:
            yield invoice, evaluate(invoice, follow_ups[invoice.pk], now=now)

    @staticmethod
    def _heartbeat(lease: Lease) -> None:
        if not lease.heartbeat():
            raise LeaseLost()

    @staticmethod
    def _record(
        run: AutomationRun,
        invoice: Invoice,
        outcome: str,
        reason_code: str,
        detail: str,
        follow_up: FollowUp | None = None,
    ) -> None:
        AutomationRunItem.objects.create(
            run=run,
            invoice=invoice,
            invoice_external_id=invoice.external_id,
            outcome=outcome,
            reason_code=reason_code[:50],
            detail=detail[:300],
            follow_up=follow_up,
        )
        run.invoices_evaluated += 1
        if outcome == AutomationRunItem.Outcome.GENERATED:
            run.drafts_generated += 1
        elif outcome == AutomationRunItem.Outcome.SKIPPED:
            run.invoices_skipped += 1
        else:
            run.generation_failures += 1
        # Saved per item so the dashboard can show live progress.
        run.save(update_fields=["invoices_evaluated", "drafts_generated", "invoices_skipped", "generation_failures"])

    @staticmethod
    def _finish(run: AutomationRun, status: str, error_summary: str = "") -> None:
        run.status = status
        run.completed_at = timezone.now()
        run.error_summary = error_summary[:1000]
        run.save(update_fields=["status", "completed_at", "error_summary"])
