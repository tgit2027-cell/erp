"""Owner-only report presentation preferences using existing authorization."""

from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import PersonalReportView, ReportingPanel
from .pagination import parse_page_parameters
from .reporting_services import (
    ReportingError,
    ReportingValidationError,
    _parse_filters,
    _require_panel,
    _require_synthetic_user,
    build_report,
)
from .reporting_views import _payload, _service


def _owned_view(actor, view_id):
    _require_synthetic_user(actor)
    return get_object_or_404(PersonalReportView, pk=view_id, user=actor, is_synthetic=True)


def _preferences(actor, payload):
    _require_synthetic_user(actor)
    if set(payload) != {"name", "panels", "filters"}:
        raise ReportingValidationError("Send only name, panels and filters; report data cannot be saved.")
    name = payload["name"]
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 100:
        raise ReportingValidationError("A view name of 1–100 characters is required.")
    panels = payload["panels"]
    if (
        not isinstance(panels, list) or not 1 <= len(panels) <= len(ReportingPanel.values)
        or any(not isinstance(panel, str) for panel in panels)
        or len(set(panels)) != len(panels)
    ):
        raise ReportingValidationError("Choose one or more distinct assigned report panels.")
    for panel in panels:
        _require_panel(actor, panel)
    filters = payload["filters"]
    allowed = {"unit", "company", "location", "period", "start", "end", "page", "page_size"}
    if not isinstance(filters, dict) or set(filters) - allowed:
        raise ReportingValidationError("Only existing report filters may be saved.")
    for key, value in filters.items():
        if isinstance(value, (bool, list, dict, float)) or value is None:
            raise ReportingValidationError(f"Invalid presentation filter: {key}.")
    parsed = _parse_filters(filters)
    if (parsed["unit_id"] is None) == (parsed["company_id"] is None):
        raise ReportingValidationError("Save one explicit business-unit or company scope.")
    for key in ("unit", "company", "location", "page", "page_size"):
        value = filters.get(key)
        if value is not None and (not str(value).isdigit() or int(value) <= 0):
            raise ReportingValidationError(f"{key} must be a positive integer.")
    try:
        page, page_size = parse_page_parameters(filters)
    except ValueError as error:
        raise ReportingValidationError(str(error)) from error
    canonical = {
        "period": parsed["period"],
        "start": parsed["start"].isoformat(),
        "end": parsed["end"].isoformat(),
        "page": page,
        "page_size": page_size,
    }
    for source, target in (("unit", "unit_id"), ("company", "company_id"), ("location", "location_id")):
        if parsed[target] is not None:
            canonical[source] = parsed[target]
    return {"name": name.strip(), "panels": panels, "filters": canonical}


def _serialize(view):
    return {
        "id": view.pk, "name": view.name, "panels": view.panels,
        "filters": view.filters, "updated_at": view.updated_at.isoformat(),
    }


@transaction.atomic
def save_view(*, actor, payload, view_id=None):
    view = _owned_view(actor, view_id) if view_id is not None else None
    preferences = _preferences(actor, payload)
    duplicate = PersonalReportView.objects.filter(user=actor, name=preferences["name"])
    if view is not None:
        duplicate = duplicate.exclude(pk=view.pk)
    if duplicate.exists():
        raise ReportingValidationError("You already have a view with this name.")
    try:
        with transaction.atomic():
            if view is None:
                view = PersonalReportView.objects.create(user=actor, **preferences)
            else:
                for key, value in preferences.items():
                    setattr(view, key, value)
                view.updated_at = timezone.now()
                view.save(update_fields=["name", "panels", "filters", "updated_at"])
    except IntegrityError as error:
        raise ReportingValidationError("You already have a view with this name.") from error
    return _serialize(view)


def load_view(*, actor, view_id):
    view = _owned_view(actor, view_id)
    result = _serialize(view)
    result["availability"] = []
    for panel in view.panels:
        try:
            # Reuse all existing panel/row/field checks, then discard results.
            # No report facts are returned or persisted by this preference API.
            build_report(actor=actor, panel=panel, query_params={**view.filters, "page": 1, "page_size": 1})
        except ReportingError as error:
            result["availability"].append({"panel": panel, "available": False, "reason": str(error)})
        else:
            result["availability"].append({"panel": panel, "available": True, "reason": ""})
    return result


@method_decorator(never_cache, name="dispatch")
class PersonalReportViewList(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        _service(_require_synthetic_user, user=request.user)
        return Response({"views": [
            _serialize(view)
            for view in PersonalReportView.objects.filter(user=request.user, is_synthetic=True)
        ]})

    def post(self, request):
        return Response(_service(save_view, actor=request.user, payload=_payload(request)), status=201)


@method_decorator(never_cache, name="dispatch")
class PersonalReportViewDetail(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, view_id):
        return Response(_service(load_view, actor=request.user, view_id=view_id))

    def put(self, request, view_id):
        return Response(_service(save_view, actor=request.user, payload=_payload(request), view_id=view_id))

    def delete(self, request, view_id):
        view = _service(_owned_view, actor=request.user, view_id=view_id)
        view.delete()
        return Response(status=204)
