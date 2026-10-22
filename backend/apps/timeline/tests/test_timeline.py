import datetime as dt

from django.db import DatabaseError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.testing import SessionApiTestCase
from apps.invoices.models import AutomationRun, Client, Invoice
from apps.invoices.sync import sync_invoices
from apps.invoices.tests.factories import HEADER, FakeSheetsClient, make_invoice, row
from apps.timeline import services as timeline
from apps.timeline.models import ActorType, EventType, TimelineEvent
from apps.timeline.testing import TimelineTransactionTestCase


def sync(*rows: list, actor: object = None) -> AutomationRun:  # type: ignore[type-arg]
    return sync_invoices(client=FakeSheetsClient([HEADER, *rows]), actor=actor)


def events(event_type: str) -> list[TimelineEvent]:
    return list(TimelineEvent.objects.filter(type=event_type).order_by("occurred_at"))


class AppendOnlyTests(TestCase):
    def test_database_rejects_updates_and_deletes(self) -> None:
        event = timeline.record(EventType.INVOICE_CREATED, actor=ActorType.SYSTEM, invoice=make_invoice())

        with self.assertRaisesMessage(DatabaseError, "append-only"), transaction.atomic():
            TimelineEvent.objects.filter(pk=event.pk).update(type=EventType.FOLLOW_UP_SENT)
        with self.assertRaisesMessage(DatabaseError, "append-only"), transaction.atomic():
            event.delete()
        with self.assertRaisesMessage(DatabaseError, "append-only"), transaction.atomic():
            event.payload = {"tampered": True}
            event.save()
        self.assertEqual(TimelineEvent.objects.get(pk=event.pk).payload, {})

    def test_database_rejects_truncate(self) -> None:
        timeline.record(EventType.INVOICE_CREATED, actor=ActorType.SYSTEM, invoice=make_invoice())
        with self.assertRaisesMessage(DatabaseError, "append-only"), transaction.atomic():
            with connection.cursor() as cursor:
                # Run the deferred FK checks of the insert above, as a commit would; PostgreSQL won't
                # TRUNCATE a table with pending trigger events.
                cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
                cursor.execute(f"TRUNCATE {TimelineEvent._meta.db_table} CASCADE")
        self.assertEqual(TimelineEvent.objects.count(), 1)

    def test_user_events_must_name_the_user(self) -> None:
        with self.assertRaises(DatabaseError), transaction.atomic():
            TimelineEvent.objects.create(type=EventType.DRAFT_EDITED, actor_type=ActorType.USER)


class SyncHistoryTests(TestCase):
    def test_import_and_field_changes_are_recorded(self) -> None:
        sync(row("INV-1", amount=100))
        sync(row("INV-1", amount=120, status="PAID"))

        created, = events(EventType.INVOICE_CREATED)
        self.assertEqual(created.payload["amount"], "100.00")
        self.assertEqual(created.actor_type, ActorType.SYSTEM)
        updated, = events(EventType.INVOICE_UPDATED)
        self.assertEqual(
            updated.payload["changes"],
            {"amount": {"from": "100.00", "to": "120.00"}, "status": {"from": "UNPAID", "to": "PAID"}},
        )
        # UNPAID -> PAID is queryable as a dated fact.
        paid = TimelineEvent.objects.filter(type=EventType.INVOICE_UPDATED, payload__changes__status__to="PAID")
        self.assertEqual(list(paid), [updated])

    def test_unchanged_rows_write_no_events(self) -> None:
        sync(row("INV-1"))
        sync(row("INV-1"))
        self.assertEqual(TimelineEvent.objects.count(), 1)

    def test_removed_rows_are_kept_marked_and_can_return(self) -> None:
        sync(row("INV-1"), row("INV-2"))
        run = sync(row("INV-1"))

        self.assertEqual(run.status, AutomationRun.Status.SUCCEEDED)
        removed = Invoice.objects.get(external_id="INV-2")
        self.assertFalse(removed.in_sheet)
        self.assertEqual([e.invoice for e in events(EventType.INVOICE_REMOVED_FROM_SHEET)], [removed])

        sync(row("INV-1"))  # still missing: no repeated event
        self.assertEqual(len(events(EventType.INVOICE_REMOVED_FROM_SHEET)), 1)

        sync(row("INV-1"), row("INV-2"))
        removed.refresh_from_db()
        self.assertTrue(removed.in_sheet)
        self.assertEqual(len(events(EventType.INVOICE_RETURNED_TO_SHEET)), 1)

    def test_rejected_sync_changes_nothing(self) -> None:
        sync(row("INV-1"), row("INV-2"))
        run = sync(row("INV-1", amount=0))

        self.assertEqual(run.status, AutomationRun.Status.FAILED)
        self.assertTrue(Invoice.objects.get(external_id="INV-2").in_sheet)
        self.assertEqual(TimelineEvent.objects.count(), 2)

    def test_clients_group_invoices_by_lowercased_email(self) -> None:
        sync(
            row("INV-1", client_name="Acme", client_email="Billing@Acme.test"),
            row("INV-2", client_name="Acme Ltd", client_email="billing@acme.test"),
            row("INV-3", client_name="Other", client_email="ap@other.test"),
        )

        acme = Client.objects.get(email="billing@acme.test")
        self.assertEqual(acme.display_name, "Acme Ltd")  # last row seen wins
        self.assertEqual(set(acme.invoices.values_list("external_id", flat=True)), {"INV-1", "INV-2"})
        self.assertEqual(Client.objects.count(), 2)
        self.assertEqual(TimelineEvent.objects.filter(client=acme).count(), 2)

    def test_email_change_moves_the_invoice_to_the_new_client(self) -> None:
        sync(row("INV-1", client_email="old@acme.test"))
        sync(row("INV-1", client_email="new@acme.test"))
        self.assertEqual(Invoice.objects.get().client.email, "new@acme.test")


class TimelineApiTests(SessionApiTestCase):
    def test_lists_events_newest_first_filtered_by_invoice_and_client(self) -> None:
        sync(row("INV-1", client_email="a@a.test"), row("INV-2", client_email="b@b.test"), actor=self.user)
        sync(row("INV-1", client_email="a@a.test", amount=5), row("INV-2", client_email="b@b.test"))
        invoice = Invoice.objects.get(external_id="INV-1")

        body = self.client.get(reverse("timeline"), {"invoice": str(invoice.pk)}).json()
        self.assertEqual(body["count"], 2)
        self.assertEqual([e["type"] for e in body["results"]], ["INVOICE_UPDATED", "INVOICE_CREATED"])
        self.assertEqual(body["results"][1]["actor_username"], self.user.username)

        by_client = self.client.get(reverse("timeline"), {"client": str(invoice.client_id)}).json()
        self.assertEqual(by_client["count"], 2)
        self.assertEqual(self.client.get(reverse("timeline"), {"invoice": "not-a-uuid"}).json()["count"], 0)
        limited = self.client.get(reverse("timeline"), {"limit": 1}).json()
        self.assertEqual((limited["count"], len(limited["results"])), (3, 1))


class BackfillMigrationTests(TimelineTransactionTestCase):
    """Runs migration invoices 0004 against data in the pre-migration schema."""

    def test_backfill_creates_clients_and_marks_rows_missing_from_the_last_sync(self) -> None:
        executor = MigrationExecutor(connection)
        before = [("invoices", "0003_workflow")]
        after = executor.loader.graph.leaf_nodes()
        executor.migrate(before)
        old_apps = executor.loader.project_state(before).apps
        OldInvoice = old_apps.get_model("invoices", "Invoice")
        OldRun = old_apps.get_model("invoices", "AutomationRun")

        now = timezone.now()
        common = {"amount": "10.00", "currency": "USD", "due_date": "2026-01-01", "status": "UNPAID"}
        OldInvoice.objects.create(external_id="KEPT", client_name="Acme", client_email="AP@acme.test",
                                  last_synced_at=now, **common)
        OldInvoice.objects.create(external_id="GONE", client_name="Acme Ltd", client_email="ap@acme.test",
                                  last_synced_at=now - dt.timedelta(days=2), **common)
        OldRun.objects.create(trigger="MANUAL", kind="SYNC", status="SUCCEEDED", started_at=now - dt.timedelta(minutes=1))

        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(after)

        self.assertEqual(Client.objects.count(), 1)
        client = Client.objects.get()
        self.assertEqual(client.email, "ap@acme.test")
        self.assertEqual(
            dict(Invoice.objects.values_list("external_id", "in_sheet")), {"KEPT": True, "GONE": False}
        )
        self.assertEqual(set(client.invoices.values_list("external_id", flat=True)), {"KEPT", "GONE"})
