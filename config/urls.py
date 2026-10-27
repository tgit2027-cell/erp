from django.urls import include, path

from .frontend_views import app_index
from .health_views import health_check

urlpatterns = [
    path("healthz/", health_check, name="health-check"),
    path("", app_index, name="app-index"),
    path("api/", include("foundation.urls")),
    path("api/", include("audit.urls")),
]
