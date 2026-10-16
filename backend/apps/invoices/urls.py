from django.urls import path

from . import views

urlpatterns = [
    path("sync/", views.SyncView.as_view(), name="sync"),
    path("invoices/", views.InvoiceListView.as_view(), name="invoice-list"),
    path("invoices/<uuid:pk>/", views.InvoiceDetailView.as_view(), name="invoice-detail"),
    path("dashboard/", views.DashboardView.as_view(), name="dashboard"),
    path("automation-runs/", views.AutomationRunListView.as_view(), name="automation-run-list"),
]
