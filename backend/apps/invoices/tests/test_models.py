from decimal import Decimal

from django.db import IntegrityError
from django.test import TestCase

from apps.invoices.models import Invoice

from .factories import days_from_today, make_invoice


class InvoiceOverdueTests(TestCase):
    def test_unpaid_past_due_is_overdue(self) -> None:
        invoice = make_invoice(due_date=days_from_today(-5))

        self.assertTrue(invoice.is_overdue)
        self.assertEqual(invoice.days_overdue, 5)

    def test_due_today_is_not_overdue(self) -> None:
        invoice = make_invoice(due_date=days_from_today(0))

        self.assertFalse(invoice.is_overdue)
        self.assertEqual(invoice.days_overdue, 0)

    def test_future_due_date_is_not_overdue(self) -> None:
        invoice = make_invoice(due_date=days_from_today(10))

        self.assertFalse(invoice.is_overdue)
        self.assertEqual(invoice.days_overdue, 0)

    def test_paid_invoice_is_never_overdue(self) -> None:
        invoice = make_invoice(due_date=days_from_today(-30), status=Invoice.Status.PAID)

        self.assertFalse(invoice.is_overdue)
        self.assertEqual(invoice.days_overdue, 0)


class InvoiceConstraintTests(TestCase):
    def test_database_rejects_non_positive_amount(self) -> None:
        with self.assertRaises(IntegrityError):
            make_invoice(amount=Decimal("0.00"))
