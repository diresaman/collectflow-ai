from django.urls import path

from . import views

urlpatterns = [
    path("invoices/<uuid:pk>/analyze/", views.AnalyzeInvoiceView.as_view(), name="invoice-analyze"),
    path("follow-ups/", views.FollowUpListView.as_view(), name="follow-up-list"),
    path("follow-ups/<uuid:pk>/", views.FollowUpDetailView.as_view(), name="follow-up-detail"),
    path("follow-ups/<uuid:pk>/approve/", views.ApproveFollowUpView.as_view(), name="follow-up-approve"),
    path("follow-ups/<uuid:pk>/reject/", views.RejectFollowUpView.as_view(), name="follow-up-reject"),
]
