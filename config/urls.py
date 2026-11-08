from django.urls import include, path

urlpatterns = [
    path("api/", include("foundation.urls")),
    path("api/", include("audit.urls")),
    path("api/", include("chat.urls")),
]
