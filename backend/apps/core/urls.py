from django.urls import path

from .views import HealthCheckView, IntegrationStatusView, TodayView

urlpatterns = [
    path("health/", HealthCheckView.as_view(), name="health"),
    path("integrations/status/", IntegrationStatusView.as_view(), name="integration-status"),
    path("today/", TodayView.as_view(), name="today"),
]
