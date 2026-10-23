from django.urls import path

from . import views

urlpatterns = [
    path("worklist/", views.WorklistView.as_view(), name="worklist"),
    path("clients/", views.ClientListView.as_view(), name="client-list"),
    path("clients/recompute/", views.RecomputeView.as_view(), name="client-recompute"),
    path("clients/<uuid:pk>/", views.ClientDetailView.as_view(), name="client-detail"),
    path("analytics/", views.AnalyticsView.as_view(), name="analytics"),
    path("analytics/<slug:kind>/", views.AnalyticsView.as_view(), name="analytics-kind"),
]
