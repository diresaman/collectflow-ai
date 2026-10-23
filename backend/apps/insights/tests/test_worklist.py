import json

from django.test import Client as HttpClient
from django.test import override_settings

from apps.accounts.models import Role
from apps.accounts.testing import SessionApiTestCase, make_user
from apps.automation.models import OutboxJob
from apps.cases import disputes
from apps.cases.tests.helpers import chased, reply
from apps.core import today
from apps.followups.eligibility import ineligibility
from apps.followups.tests.helpers import make_follow_up
from apps.insights import jobs, risk

from .helpers import client, dispute, inv, send

SETTINGS = {"GEMINI_API_KEY": "k", "GEMINI_MODEL": "m", "GOOGLE_SHEETS_SPREADSHEET_ID": "", "GMAIL_READ_ENABLED": False}


def rows(data: dict, currency: str) -> list[dict]:  # type: ignore[type-arg]
    return next(g["rows"] for g in data["groups"] if g["currency"] == currency)


@override_settings(**SETTINGS)
class WorklistTests(SessionApiTestCase):
    role = Role.OPERATOR

    def worklist(self) -> dict:  # type: ignore[type-arg]
        response = self.client.get("/api/worklist/")
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_ranked_by_score_times_outstanding_within_each_currency(self) -> None:
        inv("A-1", days=90, email="a@client.test", amount="100.00")      # score 35, priority 3,500
        inv("B-1", days=9, email="b@client.test", amount="5000.00")      # score 4, priority 20,000
        inv("C-1", days=45, email="c@client.test", amount="400.00")      # score 18, priority 7,200
        inv("C-2", days=10, email="c@client.test", amount="900.00", currency="EUR")
        inv("D-1", days=-5, email="d@client.test", amount="2000.00")     # not due: score 0, priority 0
        risk.recompute()
        data = self.worklist()
        self.assertEqual([g["currency"] for g in data["groups"]], ["EUR", "USD"])  # never one mixed ranking
        usd = rows(data, "USD")
        self.assertEqual([(r["client_email"], r["risk_score"], r["priority"], r["rank"]) for r in usd], [
            ("b@client.test", 4, "20000.00", 1), ("c@client.test", 18, "7200.00", 2),
            ("a@client.test", 35, "3500.00", 3), ("d@client.test", 0, "0.00", 4)])
        eur = rows(data, "EUR")
        self.assertEqual([(r["client_email"], r["outstanding"], r["priority"]) for r in eur],
                         [("c@client.test", "900.00", "16200.00")])  # same score, EUR amount only
        top = usd[2]["top_factors"][0]
        self.assertEqual((top["key"], top["points"]), ("oldest_overdue", 35))
        self.assertEqual(usd[0]["invoices"][0]["link"], f"/?invoice={usd[0]['invoices'][0]['id']}")
        self.assertEqual(self.worklist()["groups"], data["groups"])  # deterministic

    def test_ties_break_on_score_then_days_then_email(self) -> None:
        inv("X-1", days=30, email="z@client.test", amount="300.00")
        inv("Y-1", days=30, email="y@client.test", amount="300.00")
        risk.recompute()
        self.assertEqual([r["client_email"] for r in rows(self.worklist(), "USD")], ["y@client.test", "z@client.test"])

    def test_unscored_clients_are_listed_not_hidden(self) -> None:
        inv("A-1", days=10)
        data = self.worklist()
        self.assertEqual(len(data["unscored"]), 1)
        self.assertIsNone(rows(data, "USD")[0]["risk_score"])

    def test_high_risk_never_suggests_chasing_a_blocked_invoice(self) -> None:
        invoice = chased("INV-1", "thread-1", days=95)
        reply(invoice, "We will pay next week", label="PROMISE_TO_PAY")  # held for a person
        stale = make_follow_up(invoice, subject="Stale draft")  # a draft that predates nothing: still blocked
        for n in range(3):
            send(invoice, days_ago=10 + n)
        risk.recompute()
        self.assertGreaterEqual(client(invoice.client_email).risk_score, 35)
        row = rows(self.worklist(), "USD")[0]
        self.assertNotIn(row["next_action"]["code"], ("drafts", "approved"))
        self.assertEqual(row["next_action"]["code"], "replies")  # review the reply first
        self.assertIn("on_hold", [b["code"] for b in row["blockers"]])
        self.assertTrue(row["invoices"][0]["chasing_blocked"])
        # The backend still refuses chasing: the score changes nothing.
        self.client.force_login(make_user("boss", Role.APPROVER))
        response = self.client.post(f"/api/follow-ups/{stale.pk}/approve/", data="{}", content_type="application/json")
        self.assertEqual(response.status_code, 409)
        self.assertTrue(ineligibility(invoice))

    def test_dispute_suggests_resolution_and_explains_who(self) -> None:
        invoice = chased("INV-1", "thread-1", days=40)
        disputes.open_case(invoice.pk, reason="AMOUNT", description="x", actor=self.user)
        risk.recompute()
        row = rows(self.worklist(), "USD")[0]
        self.assertEqual(row["next_action"]["code"], "disputes")
        self.assertIn("approver", row["next_action"]["who_can_act"].lower())
        self.assertIn("invoice_disputed", [b["code"] for b in row["blockers"]])
        self.assertIn("open_dispute", [f["key"] for f in row["top_factors"]])

    def test_eligible_draft_is_suggested_with_the_approval_rule(self) -> None:
        invoice = chased("INV-1", "thread-1", days=20)
        make_follow_up(invoice, subject="Firm reminder", tone="FIRM")
        risk.recompute()
        action = rows(self.worklist(), "USD")[0]["next_action"]
        self.assertEqual(action["code"], "drafts")
        self.assertIn("approver", action["blocked"].lower())  # the operator is told they cannot approve it

    def test_urgent_operational_work_is_never_hidden(self) -> None:
        invoice = chased("INV-1", "thread-1", days=20)
        reply(invoice, "hmm", label="OTHER", confidence=0.3)
        data = self.worklist()
        self.assertIn("replies", [u["key"] for u in data["urgent"]])

    def test_today_is_unchanged(self) -> None:
        invoice = chased("INV-1", "thread-1", days=20)
        dispute(inv("INV-2", days=60))
        make_follow_up(invoice, subject="Draft")
        before = self.client.get("/api/today/").json()
        risk.recompute()
        after = self.client.get("/api/today/").json()
        self.assertEqual([s["key"] for s in after["sections"]], [key for key, _ in today.SECTIONS])
        self.assertEqual(len(after["sections"]), 11)
        self.assertEqual(before["sections"], after["sections"])  # scoring adds or reorders nothing
        self.assertNotIn("risk", json.dumps(after).lower())


@override_settings(**SETTINGS)
class ClientApiTests(SessionApiTestCase):
    role = Role.OPERATOR

    def test_detail_explains_the_score(self) -> None:
        a = inv("INV-1", days=30)
        inv("INV-2", days=5, client_name="Renamed Co")
        dispute(a)
        risk.recompute()
        c = client()
        data = self.client.get(f"/api/clients/{c.pk}/").json()
        self.assertEqual(data["risk_score"], sum(f["points"] for f in data["factors"]))
        self.assertEqual([f["key"] for f in data["factors"]],
                         ["oldest_overdue", "broken_promises", "days_late", "no_reply", "open_dispute"])
        self.assertEqual(data["invoice_names"], ["Fictional Studio", "Renamed Co"])
        self.assertEqual(data["disputes"][0]["link"], f"/disputes?case={data['disputes'][0]['id']}")
        self.assertEqual(len(data["invoices"]), 2)
        self.assertIsNotNone(data["computed_at"])
        listed = self.client.get("/api/clients/").json()
        self.assertEqual(listed[0]["id"], str(c.pk))

    def test_unknown_client_and_roles(self) -> None:
        self.assertEqual(self.client.get("/api/clients/00000000-0000-0000-0000-000000000000/").status_code, 404)
        self.client.force_login(make_user("nobody", role=None))
        for url in ("/api/worklist/", "/api/clients/"):
            self.assertEqual(self.client.get(url).status_code, 403)

    def test_recompute_request_is_queued_once_and_needs_csrf(self) -> None:
        first = self.client.post("/api/clients/recompute/")
        second = self.client.post("/api/clients/recompute/")
        self.assertEqual((first.status_code, second.status_code), (202, 202))
        self.assertEqual(first.json()["job_id"], second.json()["job_id"])
        self.assertEqual(OutboxJob.objects.filter(kind=jobs.RECOMPUTE_JOB_KIND).count(), 1)
        strict = HttpClient(enforce_csrf_checks=True)
        strict.force_login(self.user)
        response = strict.post("/api/clients/recompute/")
        self.assertEqual((response.status_code, response.json()["code"]), (403, "csrf_failed"))
