"""Audit reports for system administrators only; they show who did what, from where."""

from django.contrib.auth import get_user_model
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import AuditEvent
from .reporting import default_window, event_row, security_summary, usage_by_user


def _require_auditor(request):
    if not request.user.is_superuser:
        raise PermissionDenied("Only system administrators can read the audit log.")


def _window(request):
    try:
        return default_window(request.query_params)
    except ValueError as error:
        raise ValidationError({"detail": str(error)}) from None


class AuditOverviewView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        _require_auditor(request)
        start, end = _window(request)
        return Response({
            "from": start.isoformat(),
            "to": end.isoformat(),
            "security": security_summary(start=start, end=end),
            "users": usage_by_user(start=start, end=end),
        })


class AuditUserEventsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, user_id):
        _require_auditor(request)
        start, end = _window(request)
        user = get_object_or_404(get_user_model(), pk=user_id)
        events = AuditEvent.objects.filter(user=user, occurred_at__gte=start, occurred_at__lt=end)
        kind = request.query_params.get("kind")
        if kind:
            if kind not in AuditEvent.Kind.values:
                raise ValidationError({"detail": "Unknown event kind."})
            events = events.filter(kind=kind)
        try:
            page_size = min(max(int(request.query_params.get("page_size", 50)), 1), 200)
            number = max(int(request.query_params.get("page", 1)), 1)
        except ValueError:
            raise ValidationError({"detail": "page and page_size must be whole numbers."}) from None
        page = Paginator(events, page_size).get_page(number)
        return Response({
            "user": {"id": user.pk, "username": user.get_username(), "name": user.get_full_name(),
                     "is_active": user.is_active, "is_staff": user.is_staff, "is_superuser": user.is_superuser},
            "events": [event_row(event) for event in page.object_list],
            "pagination": {"page": page.number, "page_size": page_size,
                           "total_count": page.paginator.count, "total_pages": page.paginator.num_pages},
        })
