from django.urls import path

from . import views

urlpatterns = [
    path("promises/", views.PromiseListView.as_view(), name="promise-list"),
    path("promises/<uuid:pk>/cancel/", views.PromiseCancelView.as_view(), name="promise-cancel"),
    path("disputes/", views.DisputeListView.as_view(), name="dispute-list"),
    path("disputes/<uuid:pk>/", views.DisputeDetailView.as_view(), name="dispute-detail"),
    path("disputes/<uuid:pk>/assign/", views.DisputeAssignView.as_view(), name="dispute-assign"),
    path("disputes/<uuid:pk>/notes/", views.DisputeNoteView.as_view(), name="dispute-notes"),
    path("disputes/<uuid:pk>/resolve/", views.DisputeResolveView.as_view(), name="dispute-resolve"),
]
