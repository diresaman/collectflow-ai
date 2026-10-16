from django.test import TestCase
from django.urls import reverse


class HealthCheckTests(TestCase):
    def test_health_returns_ok(self) -> None:
        response = self.client.get(reverse("health"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "service": "collectflow-api"})

    def test_health_url_path(self) -> None:
        self.assertEqual(reverse("health"), "/api/health/")

    def test_health_allows_cors_from_frontend_origin(self) -> None:
        response = self.client.get(reverse("health"), HTTP_ORIGIN="http://localhost:3000")

        self.assertEqual(response["Access-Control-Allow-Origin"], "http://localhost:3000")
