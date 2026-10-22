"""
SQLite → PostgreSQL transfer (M08.1), against a representative SQLite snapshot.

The snapshot is a real SQLite database file, created and migrated by the test runner under the
alias below. The target is the PostgreSQL test database.
"""

import datetime as dt
import tempfile
import uuid
from decimal import Decimal
from io import StringIO
from pathlib import Path

from django.contrib.auth.hashers import make_password
from django.core.management import CommandError, call_command
from django.db import connections
from django.db.migrations.recorder import MigrationRecorder
from django.test import override_settings
from django.utils import timezone

from apps.accounts.models import Profile
from apps.automation.models import AutomationLease, OutboxJob, WorkerHeartbeat, WorkflowStep
from apps.core.sqlite_transfer import TransferError, transfer
from apps.followups.models import DeliveryAttempt, FollowUp, GenerationAttempt
from apps.invoices.models import AutomationRun, Client, Invoice
from apps.timeline.models import TimelineEvent
from apps.timeline.testing import TimelineTransactionTestCase

SNAPSHOT = "sqlite_snapshot"
_SNAPSHOT_FILE = Path(tempfile.gettempdir()) / f"collectflow-transfer-snapshot-{uuid.uuid4().hex[:8]}.sqlite3"

# Registered at import time so the test runner creates and migrates it like any test database.
if SNAPSHOT not in connections.settings:
    connections.settings[SNAPSHOT] = connections.configure_settings(
        {"default": dict(connections.settings["default"]),
         SNAPSHOT: {"ENGINE": "django.db.backends.sqlite3", "NAME": str(_SNAPSHOT_FILE),
                    # Throwaway file: skip fsync so flushing it between tests is fast.
                    "OPTIONS": {"init_command": "PRAGMA synchronous=OFF; PRAGMA journal_mode=MEMORY;"},
                    "TEST": {"NAME": str(_SNAPSHOT_FILE)}}}
    )[SNAPSHOT]

PRECISE = dt.datetime(2026, 10, 1, 9, 30, 15, 123456, tzinfo=dt.timezone.utc)


def build_snapshot() -> dict[str, object]:
    """A small but complete v1 + M08 dataset, including unfinished work, in the SQLite snapshot."""
    db = SNAPSHOT
    User = Profile._meta.get_field("user").related_model
    ana = User.objects.db_manager(db).create(username="ana", password=make_password("x" * 12), email="ana@example.test")
    Profile.objects.using(db).create(user=ana, role="APPROVER")
    acme = Client.objects.using(db).create(email="ap@acme.test", display_name="Acme")

    def invoice(external_id: str, **fields: object) -> Invoice:
        base: dict[str, object] = dict(
            external_id=external_id, client=acme, client_name="Acme", client_email="ap@acme.test",
            amount=Decimal("1250.50"), currency="USD", due_date=dt.date(2026, 9, 1), status="UNPAID",
            last_synced_at=PRECISE,
        )
        base.update(fields)
        obj = Invoice(**base)
        obj.save(using=db)
        return obj

    sent_inv, queued_inv, running_inv, unknown_inv = (invoice(f"INV-{n}") for n in range(1, 5))
    invoice("INV-PAID", status="PAID", in_sheet=False)

    def follow_up(inv: Invoice, status: str) -> FollowUp:
        return FollowUp.objects.using(db).create(
            invoice=inv, subject=f"Reminder {inv.external_id}", body="Hello, a reminder about your invoice.",
            tone="FRIENDLY", reasoning="r", recommended_action="a", status=status,
            invoice_snapshot={"external_id": inv.external_id, "amount": "1250.50", "nested": {"k": [1, 2]}},
            prompt_version="draft-v1", approved_by=ana, generated_at=PRECISE,
        )

    sent = follow_up(sent_inv, "SENT")
    DeliveryAttempt.objects.using(db).create(follow_up=sent, status="SENT", recipient_email="ap@acme.test",
                                             gmail_message_id="m-1", gmail_thread_id="t-1", started_at=PRECISE,
                                             requested_by=ana)
    GenerationAttempt.objects.using(db).create(invoice=sent_inv, follow_up=sent, outcome="SUCCEEDED")

    queued = follow_up(queued_inv, "SENDING")
    queued_attempt = DeliveryAttempt.objects.using(db).create(follow_up=queued, recipient_email="ap@acme.test")
    queued_job = OutboxJob.objects.using(db).create(kind="followups.deliver", payload={"attempt_id": str(queued_attempt.pk)},
                                                    idempotency_key=f"followups.deliver:{queued_attempt.pk}", max_attempts=1)

    running = follow_up(running_inv, "SENDING")
    running_attempt = DeliveryAttempt.objects.using(db).create(follow_up=running, recipient_email="ap@acme.test")
    running_job = OutboxJob.objects.using(db).create(
        kind="followups.deliver", payload={"attempt_id": str(running_attempt.pk)},
        idempotency_key=f"followups.deliver:{running_attempt.pk}", max_attempts=1, status="RUNNING", attempts=1,
        locked_by="old-worker", locked_at=PRECISE,
    )

    unknown = follow_up(unknown_inv, "DELIVERY_UNKNOWN")
    DeliveryAttempt.objects.using(db).create(follow_up=unknown, status="DELIVERY_UNKNOWN", recipient_email="ap@acme.test")

    sync_run = AutomationRun.objects.using(db).create(trigger="MANUAL", status="RUNNING",
                                                      errors=[{"row": 3, "field": "amount", "message": "bad"}])
    sync_job = OutboxJob.objects.using(db).create(kind="invoices.sync", payload={"run_id": sync_run.pk},
                                                  idempotency_key=f"invoices.sync:{sync_run.pk}", max_attempts=1)
    auto_run = AutomationRun.objects.using(db).create(kind="AUTOMATION", trigger="SCHEDULED", status="RUNNING")
    WorkflowStep.objects.using(db).create(run=auto_run, key="GENERATING_DRAFTS", status="RUNNING")
    WorkflowStep.objects.using(db).create(run=auto_run, key="FINALIZING", status="PENDING")
    AutomationLease.objects.using(db).create(name="automation", owner="dead", run=auto_run,
                                             expires_at=timezone.now() - dt.timedelta(hours=1))
    WorkerHeartbeat.objects.using(db).create(name="worker", worker_id="old", pid=1, started_at=PRECISE,
                                             last_seen_at=timezone.now() - dt.timedelta(hours=2))
    for n in range(3):
        TimelineEvent.objects.using(db).create(
            type="INVOICE_UPDATED", actor_type="USER", actor=ana, invoice=sent_inv, client=acme,
            payload={"changes": {"status": {"from": "UNPAID", "to": f"v{n}"}}}, occurred_at=PRECISE,
        )
    return {"user": ana, "queued": queued, "queued_attempt": queued_attempt, "queued_job": queued_job,
            "running": running, "running_job": running_job, "unknown": unknown, "sync_run": sync_run,
            "sync_job": sync_job, "auto_run": auto_run}


class SqliteTransferTests(TimelineTransactionTestCase):
    databases = {"default", SNAPSHOT}

    def test_transfer_preserves_every_row_and_applies_the_cutover_policy(self) -> None:
        data = build_snapshot()

        report = transfer(source_alias=SNAPSHOT)

        self.assertTrue(report.committed)
        self.assertTrue(all(source == target for source, target in report.counts.values()))
        self.assertEqual(report.counts["invoices.invoice"], (5, 5))
        self.assertEqual(report.timeline_digest[0], report.timeline_digest[1])

        # Primary keys, password hashes, microsecond timestamps, JSON and relationships survive.
        user = Profile.objects.get().user
        self.assertEqual((user.pk, user.password), (data["user"].pk, data["user"].password))  # type: ignore[attr-defined]
        self.assertEqual(FollowUp.objects.get(pk=data["queued"].pk).generated_at, PRECISE)  # type: ignore[attr-defined]
        self.assertEqual(FollowUp.objects.get(pk=data["queued"].pk).invoice_snapshot["nested"], {"k": [1, 2]})  # type: ignore[attr-defined]
        self.assertEqual(AutomationRun.objects.get(pk=data["sync_run"].pk).errors[0]["row"], 3)  # type: ignore[attr-defined]
        self.assertEqual(TimelineEvent.objects.filter(actor=user, payload__changes__status__to="v2").count(), 1)

        # Queued, never submitted: released for a person to send again.
        attempt = DeliveryAttempt.objects.get(pk=data["queued_attempt"].pk)  # type: ignore[attr-defined]
        self.assertEqual((attempt.status, attempt.error_code), ("FAILED", "cutover_not_sent"))
        self.assertEqual(FollowUp.objects.get(pk=data["queued"].pk).status, "APPROVED")  # type: ignore[attr-defined]
        self.assertEqual(OutboxJob.objects.get(pk=data["queued_job"].pk).last_error_code, "cutover_cancelled")  # type: ignore[attr-defined]
        self.assertTrue(TimelineEvent.objects.filter(type="SEND_CANCELLED").exists())
        # Running delivery: Gmail may have been called; left for reconciliation, never retried.
        running_job = OutboxJob.objects.get(pk=data["running_job"].pk)  # type: ignore[attr-defined]
        self.assertEqual((running_job.status, running_job.last_error_code), ("FAILED", "abandoned"))
        self.assertEqual(FollowUp.objects.get(pk=data["running"].pk).status, "SENDING")  # type: ignore[attr-defined]
        self.assertEqual(FollowUp.objects.get(pk=data["unknown"].pk).status, "DELIVERY_UNKNOWN")  # type: ignore[attr-defined]
        # Other unfinished work is cancelled, not resumed.
        self.assertEqual(AutomationRun.objects.get(pk=data["sync_run"].pk).status, "FAILED")  # type: ignore[attr-defined]
        self.assertEqual(AutomationRun.objects.get(pk=data["auto_run"].pk).status, "FAILED")  # type: ignore[attr-defined]
        self.assertEqual(dict(WorkflowStep.objects.values_list("key", "status")),
                         {"GENERATING_DRAFTS": "FAILED", "FINALIZING": "SKIPPED"})
        self.assertEqual(AutomationLease.objects.get().owner, "")
        self.assertFalse(OutboxJob.objects.filter(status__in=["PENDING", "RUNNING"]).exists())

        # The source snapshot is unchanged.
        self.assertEqual(OutboxJob.objects.using(SNAPSHOT).filter(status__in=["PENDING", "RUNNING"]).count(), 3)

        # Sequences continue after the copied ids.
        run = AutomationRun.objects.create(trigger="MANUAL")
        self.assertGreater(run.pk, data["auto_run"].pk)  # type: ignore[attr-defined]

    def test_dry_run_verifies_and_rolls_back(self) -> None:
        build_snapshot()
        report = transfer(source_alias=SNAPSHOT, dry_run=True)
        self.assertFalse(report.committed)
        self.assertGreater(report.counts["invoices.invoice"][1], 0)
        self.assertFalse(Invoice.objects.exists())

    def test_refuses_a_target_that_already_has_data(self) -> None:
        build_snapshot()
        Client.objects.create(email="x@y.test", display_name="X")
        with self.assertRaisesMessage(TransferError, "already contains data"):
            transfer(source_alias=SNAPSHOT)

    def test_refuses_a_snapshot_taken_while_the_worker_was_running(self) -> None:
        build_snapshot()
        WorkerHeartbeat.objects.using(SNAPSHOT).update(last_seen_at=timezone.now())
        with self.assertRaisesMessage(TransferError, "worker was alive"):
            transfer(source_alias=SNAPSHOT)
        self.assertFalse(Invoice.objects.exists())

    def test_refuses_a_snapshot_with_a_live_lease(self) -> None:
        build_snapshot()
        AutomationLease.objects.using(SNAPSHOT).update(expires_at=timezone.now() + dt.timedelta(minutes=5))
        with self.assertRaisesMessage(TransferError, "lease is held"):
            transfer(source_alias=SNAPSHOT)

    def test_refuses_schema_mismatch(self) -> None:
        build_snapshot()
        recorder = MigrationRecorder(connections[SNAPSHOT])
        recorder.record_unapplied("timeline", "0001_initial")
        try:
            with self.assertRaisesMessage(TransferError, "schemas differ"):
                transfer(source_alias=SNAPSHOT)
        finally:
            # django_migrations is not emptied between tests; restore it for the others.
            recorder.record_applied("timeline", "0001_initial")

    def test_verification_failure_commits_nothing(self) -> None:
        build_snapshot()
        from apps.core import sqlite_transfer

        original = sqlite_transfer._timeline_digest
        sqlite_transfer._timeline_digest = lambda alias: original(alias) + ("x" if alias == "default" else "")
        try:
            with self.assertRaisesMessage(TransferError, "timeline digest differs"):
                transfer(source_alias=SNAPSHOT)
        finally:
            sqlite_transfer._timeline_digest = original
        self.assertFalse(Invoice.objects.exists())
        self.assertFalse(TimelineEvent.objects.exists())

    @override_settings(BASE_DIR=Path(tempfile.gettempdir()))
    def test_command_requires_the_source_alias(self) -> None:
        with self.assertRaisesMessage(CommandError, "SQLITE_SOURCE_PATH"):
            call_command("migrate_from_sqlite", stdout=StringIO())
