from django.urls import path

from . import views

urlpatterns = [
    path("automation/run/", views.RunAutomationView.as_view(), name="automation-run"),
    path("automation-runs/<int:pk>/", views.AutomationRunDetailView.as_view(), name="automation-run-detail"),
]
