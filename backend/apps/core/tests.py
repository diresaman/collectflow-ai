import uuid

from django.test import TestCase
from django.urls import URLPattern, URLResolver, get_resolver, reverse

from apps.accounts.testing import make_user

# Reachable without a session. Login and logout still enforce CSRF where it matters.
PUBLIC_ROUTES = {"health", "auth-csrf", "auth-login", "auth-logout"}


def api_routes(patterns: list | None = None, prefix: str = "") -> list[tuple[str, str]]:  # type: ignore[type-arg]
    """(name, concrete path) for every named /api/ route, with dummy values for path converters."""
    routes = []
    for pattern in patterns if patterns is not None else get_resolver().url_patterns:
        if isinstance(pattern, URLResolver):
            routes.extend(api_routes(pattern.url_patterns, prefix + str(pattern.pattern)))
        elif isinstance(pattern, URLPattern) and pattern.name and (prefix + str(pattern.pattern)).startswith("api/"):
            kwargs = {
                name: uuid.uuid4() if type(converter).__name__ == "UUIDConverter" else 1
                for name, converter in pattern.pattern.converters.items()
            }
            routes.append((pattern.name, reverse(pattern.name, kwargs=kwargs)))
    return routes


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


class ServerSideAuthorizationTests(TestCase):
    """Every business endpoint, including ones added later, must require a session and a role."""

    def test_every_api_route_except_public_ones_requires_a_session(self) -> None:
        routes = api_routes()
        self.assertGreater(len(routes), 20)
        for name, path in routes:
            if name in PUBLIC_ROUTES:
                continue
            for method in ("get", "post", "patch"):
                with self.subTest(route=name, method=method):
                    response = getattr(self.client, method)(path, content_type="application/json")
                    self.assertEqual(response.status_code, 401)
                    self.assertEqual(response.json()["code"], "not_authenticated")

    def test_every_api_route_refuses_a_user_without_a_role(self) -> None:
        self.client.force_login(make_user(username="no-role", role=None))
        for name, path in api_routes():
            if name in PUBLIC_ROUTES:
                continue
            for method in ("get", "post", "patch"):
                with self.subTest(route=name, method=method):
                    response = getattr(self.client, method)(path, content_type="application/json")
                    self.assertEqual(response.status_code, 403)

    def test_admin_is_disabled_by_default(self) -> None:
        self.assertEqual(self.client.get("/admin/").status_code, 404)

    def test_cors_is_not_granted_to_other_origins(self) -> None:
        response = self.client.get(reverse("health"), HTTP_ORIGIN="https://evil.example")
        self.assertNotIn("Access-Control-Allow-Origin", response)
