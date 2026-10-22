import datetime as dt

from django.test import override_settings
from django.utils import timezone

from apps.accounts.models import Role
from apps.accounts.testing import SessionApiTestCase, make_user
from apps.cases import disputes, promises
from apps.cases.models import PromiseToPay
from apps.cases.tests.helpers import chased, reply, today_plus
from apps.followups.tests.helpers import make_follow_up
from apps.inbox import services as tasks


@override_settings(GEMINI_API_KEY="k", GEMINI_MODEL="m", GOOGLE_SHEETS_SPREADSHEET_ID="", GMAIL_READ_ENABLED=False)
class TodayApiTests(SessionApiTestCase):
    role = Role.OPERATOR

    def sections(self) -> dict[str, list[dict]]:  # type: ignore[type-arg]
        data = self.client.get("/api/today/").json()
        return {s["key"]: s["items"] for s in data["sections"]}

    def test_empty_but_well_formed(self) -> None:
        data = self.client.get("/api/today/").json()
        self.assertEqual([s["key"] for s in data["sections"]][:3], ["integrations", "replies", "disputes"])
        self.assertEqual(data["role"], "OPERATOR")

    def test_collects_actionable_work_in_deterministic_order(self) -> None:
        operator = self.user
        first, second = chased("INV-1", "thread-1"), chased("INV-2", "thread-2")
        make_follow_up(first, subject="Reminder A")
        make_follow_up(second, subject="Reminder B", tone="FIRM")
        triaged = reply(first, "hmm maybe", label="OTHER")
        disputes.open_case(second.pk, reason="AMOUNT", description="x", actor=operator)
        broken = promises.record(first.pk, promised_date=today_plus(0), actor=operator)
        PromiseToPay.objects.filter(pk=broken.pk).update(status="BROKEN", resolved_at=timezone.now())
        tasks.create(first.pk, title="Late task", due_at=(timezone.now() - dt.timedelta(days=1)).isoformat(), actor=operator)
        tasks.create(first.pk, title="Later task", due_at=(timezone.now() + dt.timedelta(days=5)).isoformat(), actor=operator)

        sections = self.sections()
        self.assertEqual([i["key"] for i in sections["replies"]], [f"reply:{triaged.pk}"])
        self.assertTrue(sections["replies"][0]["link"].startswith("/inbox?reply="))
        self.assertEqual(len(sections["disputes"]), 1)
        self.assertIn("approver", sections["disputes"][0]["who_can_act"].lower())
        self.assertEqual(sections["drafts"], [])  # the promise withdrew A; the dispute withdrew B
        self.assertEqual(len(sections["broken_promises"]), 1)
        self.assertEqual([i["title"] for i in sections["tasks"]], ["Late task"])  # not the later one
        self.assertTrue(sections["tasks"][0]["overdue"])
        held = {i["invoice_external_id"]: i for i in sections["held"]}
        self.assertEqual(set(held), {"INV-1", "INV-2"})
        self.assertTrue(all(i["detail"] for i in held.values()))  # every hold says who ends it

    def test_draft_explains_who_can_approve(self) -> None:
        invoice = chased("INV-3", "thread-3")
        make_follow_up(invoice, subject="Firm reminder", tone="FIRM")
        item = self.sections()["drafts"][0]
        self.assertEqual(item["who_can_act"], "Approver")
        self.assertIn("approver", item["blocked"].lower())
        self.client.force_login(make_user("boss", Role.APPROVER))
        self.assertEqual(self.sections()["drafts"][0]["blocked"], "")

    def test_requires_a_role(self) -> None:
        self.client.force_login(make_user("nobody", role=None))
        self.assertEqual(self.client.get("/api/today/").status_code, 403)


class DisputeListApiTests(SessionApiTestCase):
    role = Role.OPERATOR

    def test_filters_and_explanations(self) -> None:
        open_case = disputes.open_case(chased("INV-1", "thread-1").pk, reason="AMOUNT", description="x", actor=self.user)
        approver = make_user("boss", Role.APPROVER)
        resolved = disputes.open_case(chased("INV-2", "thread-2").pk, reason="QUALITY", description="y", actor=self.user)
        disputes.resolve(resolved.pk, outcome="INVALID", resolution="Fine", actor=approver)

        rows = self.client.get("/api/disputes/").json()
        self.assertEqual([r["id"] for r in rows], [str(open_case.pk), str(resolved.pk)])  # open first
        self.assertEqual(rows[0]["can_resolve"], {"allowed": False, "reason": "Only an approver can resolve a dispute."})
        self.assertFalse(rows[0]["chasing"]["resumes"])
        self.assertIn("invoice_disputed", [b["code"] for b in rows[0]["chasing"]["blocked_by"]])
        self.assertEqual([r["id"] for r in self.client.get("/api/disputes/?status=RESOLVED").json()], [str(resolved.pk)])
        self.assertEqual(len(self.client.get("/api/disputes/?unassigned=true").json()), 2)
        self.assertEqual(self.client.get("/api/disputes/?owner=me").json(), [])
        self.client.force_login(approver)
        self.assertTrue(self.client.get(f"/api/disputes/{open_case.pk}/").json()["can_resolve"]["allowed"])


class CsrfAndRoleTests(SessionApiTestCase):
    """The M11/M12 write endpoints use session auth with CSRF, and role checks in the services."""

    role = Role.OPERATOR

    def test_writes_without_a_csrf_token_are_refused(self) -> None:
        from django.test import Client

        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.user)
        invoice = chased("INV-1", "thread-1")
        for url, body in (("/api/disputes/", {"invoice_id": str(invoice.pk), "reason": "AMOUNT", "description": "x"}),
                          ("/api/promises/", {"invoice_id": str(invoice.pk), "promised_date": today_plus(3).isoformat()}),
                          ("/api/tasks/", {"invoice_id": str(invoice.pk), "title": "x"})):
            response = strict.post(url, data=body, content_type="application/json")
            self.assertEqual((response.status_code, response.json()["code"]), (403, "csrf_failed"), url)
        from apps.cases.models import DisputeCase, PromiseToPay

        self.assertFalse(DisputeCase.objects.exists() or PromiseToPay.objects.exists())

    def test_operator_cannot_resolve_or_cancel_through_the_api(self) -> None:
        import json

        invoice = chased("INV-1", "thread-1")
        case = disputes.open_case(invoice.pk, reason="AMOUNT", description="x", actor=self.user)
        response = self.client.post(f"/api/disputes/{case.pk}/resolve/", data=json.dumps(
            {"outcome": "INVALID", "resolution": "x"}), content_type="application/json")
        self.assertEqual((response.status_code, response.json()["code"]), (403, "approver_required"))
