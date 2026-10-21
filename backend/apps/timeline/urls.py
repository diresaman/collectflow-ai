from django.urls import path

from .views import TimelineView

urlpatterns = [
    path("timeline/", TimelineView.as_view(), name="timeline"),
]
