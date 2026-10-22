from django.urls import path

from . import views

urlpatterns = [
    path("tasks/", views.TaskListView.as_view(), name="task-list"),
    path("tasks/<uuid:pk>/done/", views.TaskDoneView.as_view(), name="task-done"),
]
