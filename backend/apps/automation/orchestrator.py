"""
AutomationOrchestrator: sync invoices, reconcile sequence enrollments, and advance due sequence
steps (AI drafts and call tasks).

It reuses `sync_invoices` and the sequence engine, and never calls Gmail: generated drafts always
wait for operator approval. No database transaction is opened here,
so none is held during Google Sheets or Gemini calls.
"""

import logging
from dataclasses import dataclass
from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.followups.models import FollowUp
from apps.invoices.models import AutomationRun, Invoice
from apps.invoices.sheets_client import GoogleSheetsClient
from apps.invoices.sync import default_actor, sync_invoices
from apps.sequences import engine as sequences
from integrations.gemini import GeminiClient

from . import outbox
from .lease import LEASE_NAME, Lease, LeaseUnavailable
from .models import AutomationLease, AutomationRunItem, OutboxJob, WorkflowStep

logger = logging.getLogger(__name__)

RUN_JOB_KIND = "automation.run"


class AutomationAlreadyRunning(Exception):
    def __init__(self, run_id: int | None) -> None:
        super().__init__("Another automation run is in progress.")
        self.run_id = run_id


class LeaseLost(Exception):
    pass


def request_run(*, actor: Any) -> OutboxJob:
    """Queues a manual run for the worker. Refused while a run is queued or holds the lease."""
    with transaction.atomic():
        lease = AutomationLease.objects.filter(name=LEASE_NAME).first()
        if lease is not None and lease.owner and lease.expires_at and lease.expires_at >= timezone.now():
            raise AutomationAlreadyRunning(lease.run_id)
        if outbox.has_active(RUN_JOB_KIND):
            raise AutomationAlreadyRunning(None)
        return outbox.enqueue(RUN_JOB_KIND, {"trigger": AutomationRun.Trigger.MANUAL}, max_attempts=1, created_by=actor)


def handle_run_job(job: OutboxJob) -> dict[str, Any]:
    try:
        run = AutomationOrchestrator().run(trigger=job.payload.get("trigger", AutomationRun.Trigger.MANUAL))
    except AutomationAlreadyRunning as exc:
        raise outbox.JobFailed("automation_running", {"code": "automation_running", "run_id": exc.run_id}) from None
    return {"run_id": run.pk, "status": run.status}


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
            WorkflowStep.objects.bulk_create(WorkflowStep(run=run, key=key) for key in WorkflowStep.Key.values)
            lease.attach_run(run)
            try:
                self._execute(run, lease)
            except LeaseLost:
                logger.error("Automation run %s lost its lease and stopped", run.pk)
                self._finish(run, AutomationRun.Status.FAILED, "Stopped: the run lost its lease after a stall.")
            except Exception:
                logger.exception("Automation run %s failed unexpectedly", run.pk)
                self._finish(run, AutomationRun.Status.FAILED, "Unexpected error; the run was stopped.")
            self._close_steps(run)
            return run
        finally:
            lease.release()

    def plan(self) -> list[PlannedItem]:
        """Dry run of the sequence tick on current data. No sync, no Gemini, no Gmail, no writes."""
        return [
            PlannedItem(invoice.external_id, outcome, "" if outcome.startswith("WOULD") else reason,
                        reason if outcome.startswith("WOULD") else "")
            for invoice, outcome, reason in sequences.preview()
        ]

    # Execution ----------------------------------------------------------------

    def _execute(self, run: AutomationRun, lease: Lease) -> None:
        self._step(run, WorkflowStep.Key.SYNCING_INVOICES, WorkflowStep.Status.RUNNING)
        sync_run = sync_invoices(trigger=run.trigger, client=self._sheets_client)
        run.sync_run = sync_run
        run.save(update_fields=["sync_run"])
        if sync_run.status != AutomationRun.Status.SUCCEEDED:
            headline = sync_run.error_summary.split("\n", 1)[0]
            self._step(run, WorkflowStep.Key.SYNCING_INVOICES, WorkflowStep.Status.FAILED, headline)
            self._finish(run, AutomationRun.Status.FAILED, f"Synchronization failed; no invoices evaluated. {headline}")
            return
        self._step(
            run, WorkflowStep.Key.SYNCING_INVOICES, WorkflowStep.Status.COMPLETED,
            f"{sync_run.records_processed} rows: {sync_run.created_count} new, {sync_run.updated_count} updated.",
        )
        self._heartbeat(lease)

        # Evaluate: keep sequence enrollments in line with the synced invoices (no external calls).
        self._step(run, WorkflowStep.Key.EVALUATING_INVOICES, WorkflowStep.Status.RUNNING)
        actor = default_actor(run.trigger)
        summary = sequences.reconcile(actor=actor)
        today = timezone.localdate()
        run.invoices_overdue = Invoice.objects.filter(status=Invoice.Status.UNPAID, due_date__lt=today).count()
        run.save(update_fields=["invoices_overdue"])
        self._step(
            run, WorkflowStep.Key.EVALUATING_INVOICES, WorkflowStep.Status.COMPLETED,
            f"{run.invoices_overdue} overdue; {summary.enrolled} newly enrolled, {summary.exited} left their "
            f"sequence, {summary.flagged} flagged for history review.",
        )
        self._heartbeat(lease)

        # Draft: advance each due enrollment by at most one sequence step (Gemini; never sends).
        self._step(run, WorkflowStep.Key.GENERATING_DRAFTS, WorkflowStep.Status.RUNNING, "Advancing sequences.")
        seen: set[Any] = set()

        def record(result: sequences.StepResult) -> None:
            seen.add(result.invoice.pk)
            outcome = {"GENERATED": AutomationRunItem.Outcome.GENERATED, "FAILED": AutomationRunItem.Outcome.FAILED}.get(
                result.outcome, AutomationRunItem.Outcome.SKIPPED
            )
            self._record(run, result.invoice, outcome, result.reason, result.detail, result.follow_up)
            self._step(run, WorkflowStep.Key.GENERATING_DRAFTS, WorkflowStep.Status.RUNNING,
                       f"{run.drafts_generated} drafted, {run.generation_failures} failed so far.")

        sequences.run_tick(actor=actor, record=record, heartbeat=lambda: self._heartbeat(lease),
                           gemini=self._gemini, reconcile_first=False)
        # Invoices that no sequence step touched, with the reason (paid, not overdue, paused, ...).
        for invoice, _, reason in sequences.preview():
            if invoice.pk not in seen:
                self._record(run, invoice, AutomationRunItem.Outcome.SKIPPED, reason or "no_action", _detail(reason))

        if run.drafts_generated == 0 and run.generation_failures == 0:
            self._step(run, WorkflowStep.Key.GENERATING_DRAFTS, WorkflowStep.Status.SKIPPED, "No sequence step was due.")
        else:
            all_failed = run.drafts_generated == 0 and run.generation_failures > 0
            self._step(
                run, WorkflowStep.Key.GENERATING_DRAFTS,
                WorkflowStep.Status.FAILED if all_failed else WorkflowStep.Status.COMPLETED,
                f"{run.drafts_generated} drafted, {run.generation_failures} failed.",
            )

        if run.generation_failures == 0:
            self._finish(run, AutomationRun.Status.COMPLETED)
        elif run.drafts_generated > 0:
            self._finish(run, AutomationRun.Status.PARTIAL,
                         f"{run.generation_failures} draft(s) could not be generated; see run items.")
        else:
            self._finish(run, AutomationRun.Status.FAILED,
                         f"All {run.generation_failures} draft generation attempt(s) failed; see run items.")

    @staticmethod
    def _step(run: AutomationRun, key: str, status: str, detail: str = "") -> None:
        now = timezone.now()
        fields: dict[str, object] = {"status": status, "detail": detail[:300]}
        if status == WorkflowStep.Status.RUNNING:
            WorkflowStep.objects.filter(run=run, key=key, started_at__isnull=True).update(started_at=now)
        else:
            WorkflowStep.objects.filter(run=run, key=key, started_at__isnull=True).update(started_at=now)
            fields["completed_at"] = now
        WorkflowStep.objects.filter(run=run, key=key).update(**fields)

    @staticmethod
    def _close_steps(run: AutomationRun) -> None:
        """After processing: skip steps that never ran; finalize at once if nothing needs review."""
        steps = WorkflowStep.objects.filter(run=run).exclude(key=WorkflowStep.Key.FINALIZING)
        # A step still RUNNING was interrupted by a failure; steps that never started were skipped.
        steps.filter(status=WorkflowStep.Status.RUNNING).update(status=WorkflowStep.Status.FAILED, completed_at=timezone.now())
        steps.filter(status=WorkflowStep.Status.PENDING).update(status=WorkflowStep.Status.SKIPPED)
        if run.drafts_generated == 0:
            now = timezone.now()
            AutomationRun.objects.filter(pk=run.pk, finalized_at__isnull=True).update(finalized_at=now)
            run.refresh_from_db(fields=["finalized_at"])
            WorkflowStep.objects.filter(run=run, key=WorkflowStep.Key.FINALIZING).update(
                status=WorkflowStep.Status.COMPLETED, started_at=now, completed_at=now,
                detail="Nothing needed review; finalized automatically.",
            )

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


REASON_DETAILS = {
    "not_unpaid": "Invoice is not unpaid.",
    "not_overdue": "Invoice is not overdue yet.",
    "not_in_sheet": "Invoice was not in the latest Google Sheets sync.",
    "invalid_recipient": "Client email address is invalid.",
    "invalid_amount": "Invoice amount must be positive.",
    "history_review": "Earlier reminders don't match the sequence; a person must review the position.",
    "sequence_paused": "The invoice's sequence is paused.",
    "sequence_completed": "All sequence steps are done.",
    "sequence_exited": "The invoice left its sequence.",
}


def _detail(reason: str) -> str:
    return REASON_DETAILS.get(reason, reason.replace("_", " ").capitalize() + ".")
