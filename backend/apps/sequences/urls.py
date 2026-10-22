from django.urls import path

from . import views

urlpatterns = [
    path("sequences/", views.SequenceListView.as_view(), name="sequence-list"),
    path("sequences/<uuid:pk>/", views.SequenceDetailView.as_view(), name="sequence-detail"),
    path("enrollments/", views.EnrollmentListView.as_view(), name="enrollment-list"),
    path("enrollments/<uuid:pk>/pause/", views.PauseEnrollmentView.as_view(), name="enrollment-pause"),
    path("enrollments/<uuid:pk>/resume/", views.ResumeEnrollmentView.as_view(), name="enrollment-resume"),
    path("enrollments/<uuid:pk>/review/", views.ReviewEnrollmentView.as_view(), name="enrollment-review"),
]
