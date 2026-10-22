from django.urls import path

from . import views

urlpatterns = [
    path("tasks/", views.TaskListView.as_view(), name="task-list"),
    path("tasks/<uuid:pk>/done/", views.TaskDoneView.as_view(), name="task-done"),
    path("replies/", views.ReplyListView.as_view(), name="reply-list"),
    path("replies/summary/", views.ReplySummaryView.as_view(), name="reply-summary"),
    path("replies/<uuid:pk>/", views.ReplyDetailView.as_view(), name="reply-detail"),
    path("replies/<uuid:pk>/classify/", views.ReplyClassifyView.as_view(), name="reply-classify"),
    path("replies/<uuid:pk>/release-hold/", views.ReplyReleaseHoldView.as_view(), name="reply-release-hold"),
    path("replies/<uuid:pk>/retry-classification/", views.ReplyRetryView.as_view(), name="reply-retry"),
]
