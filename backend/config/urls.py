from django.conf import settings
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("api/", include("apps.core.urls")),
    path("api/", include("apps.accounts.urls")),
    path("api/", include("apps.timeline.urls")),
    path("api/", include("apps.invoices.urls")),
    path("api/", include("apps.followups.urls")),
    path("api/", include("apps.sequences.urls")),
    path("api/", include("apps.inbox.urls")),
    path("api/", include("apps.automation.urls")),
]

if settings.ENABLE_ADMIN:
    urlpatterns.append(path("admin/", admin.site.urls))
