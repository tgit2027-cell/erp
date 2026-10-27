from django.urls import path

from . import views

urlpatterns = [
    path("audit/", views.AuditOverviewView.as_view(), name="audit-overview"),
    path("audit/users/<int:user_id>/", views.AuditUserEventsView.as_view(), name="audit-user-events"),
]
