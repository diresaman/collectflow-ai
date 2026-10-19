import uuid

from django.test import TestCase, override_settings
from django.urls import URLPattern, URLResolver, get_resolver, reverse

PUBLIC_ROUTES = {"health"}


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


@override_settings(OPERATOR_TOKEN="test-operator-token")
class ServerSideAuthorizationTests(TestCase):
    """Every business endpoint, including ones added later, must require the operator token."""

    def test_every_api_route_except_health_requires_the_operator_token(self) -> None:
        routes = api_routes()
        self.assertGreater(len(routes), 15)
        for name, path in routes:
            if name in PUBLIC_ROUTES:
                continue
            for method in ("get", "post", "patch"):
                for headers in ({}, {"HTTP_AUTHORIZATION": "Bearer wrong-token"}):
                    with self.subTest(route=name, method=method, auth=bool(headers)):
                        response = getattr(self.client, method)(path, content_type="application/json", **headers)
                        self.assertEqual(response.status_code, 401)

    @override_settings(OPERATOR_TOKEN="")
    def test_unconfigured_token_rejects_every_request(self) -> None:
        response = self.client.get(reverse("dashboard"), HTTP_AUTHORIZATION="Bearer ")
        self.assertEqual(response.status_code, 401)
        response = self.client.get(reverse("dashboard"), HTTP_AUTHORIZATION="Bearer anything")
        self.assertEqual(response.status_code, 401)

    def test_cors_is_not_granted_to_other_origins(self) -> None:
        response = self.client.get(reverse("health"), HTTP_ORIGIN="https://evil.example")
        self.assertNotIn("Access-Control-Allow-Origin", response)
