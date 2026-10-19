import datetime as dt
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.core import mail
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from apps.automation.lease import LEASE_NAME, Lease
from apps.automation.models import AutomationLease, AutomationRunItem
from apps.automation.orchestrator import AutomationAlreadyRunning, AutomationOrchestrator
from apps.followups.models import FollowUp
from apps.invoices.models import AutomationRun, Invoice
from apps.invoices.tests.factories import FakeSheetsClient, row
from integrations.gemini import GeminiResponseError, GeminiUnavailable

from .helpers import RoutingGemini, follow_up_for, iso, overdue_row, sheet

GMAIL_FORBIDDEN = mock.patch(
    "integrations.gmail.GmailClient.send", side_effect=AssertionError("automation must never call Gmail")
)


def run(sheets: FakeSheetsClient, gemini: RoutingGemini | None = None, trigger: str = "MANUAL") -> AutomationRun:
    orchestrator = AutomationOrchestrator(sheets_client=sheets, gemini=gemini or RoutingGemini())  # type: ignore[arg-type]
    with GMAIL_FORBIDDEN, mock.patch("integrations.gmail.GmailClient.check_ready", side_effect=AssertionError):
        return orchestrator.run(trigger=trigger)


def items(run_: AutomationRun) -> dict[str, tuple[str, str]]:
    return {item.invoice_external_id: (item.outcome, item.reason_code) for item in run_.items.all()}


def seed(*rows: list) -> None:  # type: ignore[type-arg]
    """Sync rows into the database without running automation."""
    from apps.invoices.sync import sync_invoices

    assert sync_invoices(client=sheet(*rows)).status == "SUCCEEDED"


@override_settings(FOLLOWUP_COOLDOWN_DAYS=7, AUTOMATION_MAX_DRAFTS_PER_RUN=10, AUTOMATION_LEASE_SECONDS=600)
class FullRunTests(TestCase):
    def test_successful_full_run(self) -> None:
        gemini = RoutingGemini()
        result = run(
            sheet(
                overdue_row("INV-1", days=3),
                overdue_row("INV-2", days=10),
                row(invoice_id="PAID-1", due_date=iso(-20), status="PAID"),
                row(invoice_id="FUTURE-1", due_date=iso(5)),
                row(invoice_id="TODAY-1", due_date=iso(0)),
            ),
            gemini,
        )

        self.assertEqual(result.kind, AutomationRun.Kind.AUTOMATION)
        self.assertEqual(result.status, AutomationRun.Status.COMPLETED)
        self.assertEqual(result.sync_run.status, AutomationRun.Status.SUCCEEDED)
        self.assertEqual(result.sync_run.kind, AutomationRun.Kind.SYNC)
        self.assertEqual(
            items(result),
            {
                "INV-1": ("GENERATED", ""),
                "INV-2": ("GENERATED", ""),
                "PAID-1": ("SKIPPED", "not_unpaid"),
                "FUTURE-1": ("SKIPPED", "not_overdue"),
                "TODAY-1": ("SKIPPED", "not_overdue"),
            },
        )
        self.assertEqual(
            (result.invoices_evaluated, result.drafts_generated, result.invoices_skipped, result.generation_failures),
            (5, 2, 3, 0),
        )
        self.assertIsNotNone(result.completed_at)
        self.assertEqual(sorted(gemini.calls), ["INV-1", "INV-2"])
        self.assertEqual(FollowUp.objects.filter(status=FollowUp.Status.DRAFT).count(), 2)
        generated = AutomationRunItem.objects.get(invoice_external_id="INV-1")
        self.assertEqual(generated.follow_up.status, FollowUp.Status.DRAFT)
        self.assertEqual(mail.outbox, [])

    def test_items_store_no_customer_details(self) -> None:
        result = run(sheet(overdue_row("INV-1")))
        item = result.items.get()
        self.assertNotIn("fictional.test", f"{item.detail}{item.reason_code}{item.invoice_external_id}")

    def test_repeated_runs_do_not_duplicate_drafts(self) -> None:
        rows = (overdue_row("INV-1"), overdue_row("INV-2"))
        run(sheet(*rows))
        second = run(sheet(*rows))

        self.assertEqual(second.drafts_generated, 0)
        self.assertEqual(items(second), {"INV-1": ("SKIPPED", "draft_awaiting_review"), "INV-2": ("SKIPPED", "draft_awaiting_review")})
        self.assertEqual(FollowUp.objects.count(), 2)

    def test_run_limit_bounds_generation(self) -> None:
        with self.settings(AUTOMATION_MAX_DRAFTS_PER_RUN=1):
            result = run(sheet(overdue_row("INV-1", days=9), overdue_row("INV-2", days=3)))
        self.assertEqual(items(result)["INV-2"], ("SKIPPED", "run_limit_reached"))
        self.assertEqual(result.drafts_generated, 1)


@override_settings(FOLLOWUP_COOLDOWN_DAYS=7)
class PolicyTests(TestCase):
    def test_existing_draft_and_approved_are_skipped(self) -> None:
        seed(overdue_row("INV-D"), overdue_row("INV-A"))
        follow_up_for("INV-D", FollowUp.Status.DRAFT)
        follow_up_for("INV-A", FollowUp.Status.APPROVED)
        gemini = RoutingGemini()

        result = run(sheet(overdue_row("INV-D"), overdue_row("INV-A")), gemini)

        self.assertEqual(
            items(result),
            {"INV-D": ("SKIPPED", "draft_awaiting_review"), "INV-A": ("SKIPPED", "approved_awaiting_send")},
        )
        self.assertEqual(gemini.calls, [])

    def test_changed_invoice_surfaces_stale_approval_without_replacing_it(self) -> None:
        seed(overdue_row("INV-A", amount=100))
        approved = follow_up_for("INV-A", FollowUp.Status.APPROVED)

        result = run(sheet(overdue_row("INV-A", amount=250)))

        item = result.items.get()
        self.assertEqual((item.outcome, item.reason_code), ("SKIPPED", "stale_approved"))
        self.assertIn("amount", item.detail)
        self.assertEqual(item.follow_up, approved)
        approved.refresh_from_db()
        self.assertEqual(approved.status, FollowUp.Status.APPROVED)  # not replaced, not sent
        self.assertEqual(FollowUp.objects.count(), 1)

    def test_recent_sent_reminder_is_in_cooldown(self) -> None:
        seed(overdue_row("INV-S"))
        follow_up_for("INV-S", FollowUp.Status.SENT, sent_at=timezone.now() - dt.timedelta(days=2))

        result = run(sheet(overdue_row("INV-S")))

        self.assertEqual(items(result), {"INV-S": ("SKIPPED", "cooldown")})

    def test_expired_cooldown_permits_new_draft(self) -> None:
        seed(overdue_row("INV-S", days=30))
        follow_up_for("INV-S", FollowUp.Status.SENT, sent_at=timezone.now() - dt.timedelta(days=8))

        result = run(sheet(overdue_row("INV-S", days=30)))

        self.assertEqual(items(result), {"INV-S": ("GENERATED", "")})
        self.assertEqual(FollowUp.objects.filter(status=FollowUp.Status.DRAFT).count(), 1)

    def test_unresolved_and_failed_deliveries_are_skipped(self) -> None:
        seed(overdue_row("INV-U"), overdue_row("INV-S"), overdue_row("INV-F"))
        follow_up_for("INV-U", FollowUp.Status.DELIVERY_UNKNOWN)
        follow_up_for("INV-S", FollowUp.Status.SENDING)
        follow_up_for("INV-F", FollowUp.Status.FAILED)

        result = run(sheet(overdue_row("INV-U"), overdue_row("INV-S"), overdue_row("INV-F")))

        self.assertEqual(
            items(result),
            {
                "INV-U": ("SKIPPED", "delivery_unresolved"),
                "INV-S": ("SKIPPED", "delivery_unresolved"),
                "INV-F": ("SKIPPED", "delivery_failed"),
            },
        )

    def test_recently_rejected_is_not_redrafted(self) -> None:
        seed(overdue_row("INV-R"))
        follow_up_for("INV-R", FollowUp.Status.REJECTED)

        self.assertEqual(items(run(sheet(overdue_row("INV-R")))), {"INV-R": ("SKIPPED", "recently_rejected")})

    def test_invoice_removed_from_sheet_gets_no_reminder(self) -> None:
        # Google Sheets is the source of truth: a deleted row must not stay "unpaid" locally.
        seed(overdue_row("INV-GONE"), overdue_row("INV-KEPT"))
        gemini = RoutingGemini()

        result = run(sheet(overdue_row("INV-KEPT")), gemini)

        self.assertEqual(items(result)["INV-GONE"], ("SKIPPED", "not_in_sheet"))
        self.assertEqual(items(result)["INV-KEPT"], ("GENERATED", ""))
        self.assertEqual(gemini.calls, ["INV-KEPT"])

    def test_removed_invoice_blocks_approval_and_sending_of_existing_reminders(self) -> None:
        from apps.followups import services
        from apps.followups.delivery import FollowUpDeliveryService
        from apps.followups.tests.helpers import FakeGmail

        seed(overdue_row("INV-D"), overdue_row("INV-A"))
        draft = follow_up_for("INV-D", FollowUp.Status.DRAFT)
        approved = follow_up_for("INV-A", FollowUp.Status.APPROVED)
        seed(overdue_row("INV-OTHER"))  # both rows were deleted from the sheet

        with self.assertRaises(services.StaleInvoice):
            services.approve(draft.pk)
        gmail = FakeGmail()
        with self.settings(DEBUG=False, GMAIL_ALLOWED_RECIPIENTS=[]), self.assertRaises(services.StaleInvoice):
            FollowUpDeliveryService(gmail=gmail).send(approved.pk)  # type: ignore[arg-type]

        self.assertEqual(gmail.sent, [])
        draft.refresh_from_db()
        approved.refresh_from_db()
        self.assertEqual((draft.status, approved.status), (FollowUp.Status.DRAFT, FollowUp.Status.APPROVED))


class FailureHandlingTests(TestCase):
    def test_sync_failure_stops_before_evaluation(self) -> None:
        seed(overdue_row("INV-1"))  # eligible invoice already in the database
        gemini = RoutingGemini()

        result = run(FakeSheetsClient(error="Google Sheets denied access (HTTP 403)."), gemini)

        self.assertEqual(result.status, AutomationRun.Status.FAILED)
        self.assertEqual(result.sync_run.status, AutomationRun.Status.FAILED)
        self.assertIn("Synchronization failed", result.error_summary)
        self.assertIn("HTTP 403", result.error_summary)
        self.assertEqual(result.invoices_evaluated, 0)
        self.assertEqual(gemini.calls, [])
        self.assertFalse(FollowUp.objects.exists())

    def test_invalid_sheet_also_stops_the_run(self) -> None:
        result = run(sheet(overdue_row("INV-1", amount=-5)))
        self.assertEqual(result.status, AutomationRun.Status.FAILED)
        self.assertEqual(result.invoices_evaluated, 0)

    def test_one_gemini_failure_does_not_stop_others_and_marks_partial(self) -> None:
        gemini = RoutingGemini(fail_for={"INV-2": GeminiUnavailable("Gemini did not respond in time. Try again.")})

        with self.assertLogs("apps.followups.services"):
            result = run(sheet(overdue_row("INV-1", days=9), overdue_row("INV-2", days=6), overdue_row("INV-3", days=3)), gemini)

        self.assertEqual(sorted(gemini.calls), ["INV-1", "INV-2", "INV-3"])
        self.assertEqual(result.status, AutomationRun.Status.PARTIAL)
        self.assertEqual((result.drafts_generated, result.generation_failures), (2, 1))
        failed = result.items.get(outcome="FAILED")
        self.assertEqual((failed.invoice_external_id, failed.reason_code), ("INV-2", "ai_unavailable"))
        self.assertIn("did not respond", failed.detail)
        self.assertFalse(FollowUp.objects.filter(invoice__external_id="INV-2").exists())

    def test_all_generations_failing_marks_run_failed(self) -> None:
        error = GeminiResponseError("Gemini returned invalid JSON.")
        gemini = RoutingGemini(fail_for={"INV-1": error, "INV-2": error})

        with self.assertLogs("apps.followups.services"):
            result = run(sheet(overdue_row("INV-1"), overdue_row("INV-2")), gemini)

        self.assertEqual(result.status, AutomationRun.Status.FAILED)
        self.assertEqual(result.generation_failures, 2)
        self.assertFalse(FollowUp.objects.exists())


class DuplicatePreventionTests(TestCase):
    def test_draft_created_concurrently_is_caught_by_service(self) -> None:
        def manual_generation_wins(invoice_id: str) -> None:
            follow_up_for(invoice_id, FollowUp.Status.DRAFT)  # operator clicked Analyze meanwhile

        with self.assertLogs("apps.followups.services"):
            result = run(sheet(overdue_row("INV-1")), RoutingGemini(on_call=manual_generation_wins))

        self.assertEqual(items(result), {"INV-1": ("SKIPPED", "active_follow_up_exists")})
        self.assertEqual(FollowUp.objects.filter(status=FollowUp.Status.DRAFT).count(), 1)


@override_settings(AUTOMATION_LEASE_SECONDS=600)
class LeaseTests(TestCase):
    def test_held_lease_blocks_any_trigger(self) -> None:
        lease = Lease.acquire()
        try:
            with self.assertRaises(AutomationAlreadyRunning):
                run(sheet(overdue_row("INV-1")))
            with self.assertRaises(CommandError) as ctx:
                call_command("run_automation", stdout=StringIO())
            self.assertEqual(ctx.exception.returncode, 2)
        finally:
            lease.release()
        self.assertFalse(AutomationRun.objects.filter(kind="AUTOMATION").exists())

    def test_scheduled_trigger_during_manual_run_is_refused(self) -> None:
        outcomes: list[str] = []

        def scheduled_run_starts(invoice_id: str) -> None:
            try:
                call_command("run_automation", stdout=StringIO())
                outcomes.append("ran")
            except CommandError as exc:
                outcomes.append(f"refused:{exc.returncode}")

        result = run(sheet(overdue_row("INV-1")), RoutingGemini(on_call=scheduled_run_starts))

        self.assertEqual(outcomes, ["refused:2"])
        self.assertEqual(result.status, AutomationRun.Status.COMPLETED)
        self.assertEqual(AutomationRun.objects.filter(kind="AUTOMATION").count(), 1)

    def test_lease_is_released_after_run_and_after_failure(self) -> None:
        run(sheet(overdue_row("INV-1")))
        run(FakeSheetsClient(error="down"))
        self.assertEqual(AutomationLease.objects.get(name=LEASE_NAME).owner, "")

    def test_live_lease_cannot_be_stolen_but_expired_lease_can(self) -> None:
        holder = Lease.acquire()
        with self.assertRaises(Exception):
            Lease.acquire()

        # Simulate a crashed owner: no heartbeat for longer than the lease duration.
        abandoned = AutomationRun.objects.create(kind="AUTOMATION", trigger="SCHEDULED", status="RUNNING")
        AutomationLease.objects.filter(name=LEASE_NAME).update(
            run=abandoned, expires_at=timezone.now() - dt.timedelta(seconds=1)
        )
        taker = Lease.acquire()

        abandoned.refresh_from_db()
        self.assertEqual(abandoned.status, AutomationRun.Status.FAILED)
        self.assertIn("Abandoned", abandoned.error_summary)
        self.assertFalse(holder.heartbeat())  # the stalled owner can no longer renew
        self.assertTrue(taker.heartbeat())
        taker.release()

    def test_run_stops_when_lease_is_lost(self) -> None:
        def lease_taken_over(invoice_id: str) -> None:
            AutomationLease.objects.filter(name=LEASE_NAME).update(owner="someone-else")

        with self.assertLogs("apps.automation.orchestrator", level="ERROR"):
            result = run(sheet(overdue_row("INV-1"), overdue_row("INV-2")), RoutingGemini(on_call=lease_taken_over))

        self.assertEqual(result.status, AutomationRun.Status.FAILED)
        self.assertIn("lost its lease", result.error_summary)
        self.assertEqual(result.invoices_evaluated, 1)  # stopped after the step that lost the lease


class DryRunTests(TestCase):
    def test_dry_run_changes_nothing_and_calls_nothing(self) -> None:
        seed(overdue_row("INV-1"), row(invoice_id="PAID-1", due_date=iso(-3), status="PAID"))
        gemini = RoutingGemini()
        out = StringIO()

        with mock.patch("apps.followups.services.GeminiClient.from_settings", return_value=gemini), \
                mock.patch("apps.invoices.sync.GoogleSheetsClient.from_settings", side_effect=AssertionError), \
                GMAIL_FORBIDDEN:
            call_command("run_automation", "--dry-run", stdout=out)

        self.assertIn("INV-1: WOULD_GENERATE", out.getvalue())
        self.assertIn("PAID-1: SKIPPED (not_unpaid)", out.getvalue())
        self.assertEqual(gemini.calls, [])
        self.assertFalse(FollowUp.objects.exists())
        self.assertFalse(AutomationRun.objects.filter(kind="AUTOMATION").exists())


class CommandTests(TestCase):
    def patched(self, sheets: FakeSheetsClient, gemini: RoutingGemini):  # type: ignore[no-untyped-def]
        return (
            mock.patch("apps.invoices.sync.GoogleSheetsClient.from_settings", return_value=sheets),
            mock.patch("apps.followups.services.GeminiClient.from_settings", return_value=gemini),
        )

    def test_command_runs_scheduled_automation_and_prints_summary(self) -> None:
        sheets_patch, gemini_patch = self.patched(
            sheet(overdue_row("INV-1"), row(invoice_id="PAID-1", due_date=iso(-3), status="PAID")), RoutingGemini()
        )
        out = StringIO()
        with sheets_patch, gemini_patch, GMAIL_FORBIDDEN:
            call_command("run_automation", stdout=out)

        result = AutomationRun.objects.get(kind="AUTOMATION")
        self.assertEqual((result.trigger, result.status), ("SCHEDULED", "COMPLETED"))
        self.assertEqual(result.sync_run.trigger, "SCHEDULED")
        output = out.getvalue()
        self.assertIn("drafts_generated=1", output)
        self.assertIn("not_unpaid=1", output)
        self.assertIn("awaiting approval", output)
        self.assertNotIn("fictional.test", output)

    def test_command_exits_nonzero_on_fatal_failure(self) -> None:
        sheets_patch, gemini_patch = self.patched(FakeSheetsClient(error="Google Sheets denied access."), RoutingGemini())
        with sheets_patch, gemini_patch, self.assertRaises(CommandError) as ctx:
            call_command("run_automation", stdout=StringIO())
        self.assertEqual(ctx.exception.returncode, 1)


class TransactionBoundaryTests(TransactionTestCase):
    def test_external_calls_happen_outside_transactions(self) -> None:
        observed: list[tuple[str, bool]] = []

        class ObservingSheets(FakeSheetsClient):
            def fetch_values(self):  # type: ignore[no-untyped-def]
                observed.append(("sheets", connection.in_atomic_block))
                return super().fetch_values()

        gemini = RoutingGemini(on_call=lambda _: observed.append(("gemini", connection.in_atomic_block)))
        result = run(ObservingSheets([*sheet(overdue_row("INV-1")).values]), gemini)

        self.assertEqual(result.status, AutomationRun.Status.COMPLETED)
        self.assertEqual(observed, [("sheets", False), ("gemini", False)])
