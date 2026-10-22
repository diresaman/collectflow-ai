from django.urls import path

from .views import HealthCheckView, IntegrationStatusView

urlpatterns = [
    path("health/", HealthCheckView.as_view(), name="health"),
    path("integrations/status/", IntegrationStatusView.as_view(), name="integration-status"),
]
