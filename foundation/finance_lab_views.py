"""HTTP adapter exclusively for verified disposable synthetic databases."""
import json
import hashlib
from functools import wraps
from pathlib import Path
import re

from django.conf import settings
from django.contrib.auth import get_user_model, login
from django.core.exceptions import ValidationError
from django.db import connection, IntegrityError, transaction
from django.http import JsonResponse, FileResponse
from django.middleware.csrf import get_token
from django.views.decorators.http import require_http_methods

from .finance_drafts import (
    FinanceDraftConflict, FinanceDraftValidationError, _require_synthetic_demo,
    create_finance_draft_account, create_finance_draft_dimension,
    create_finance_draft_dimension_value, create_finance_draft_journal,
    update_finance_draft_journal, finance_draft_snapshot,
)
from .models import (
    LegalEntity, BusinessUnit, FinanceDraftAccount, FinanceDraftDimension,
    FinanceDraftDimensionValue, FinanceDraftJournal,
)


def _target():
    expected = getattr(settings, "FINANCE_LAB_DATA_DIRECTORY", "")
    if (
        not getattr(settings, "FINANCE_LAB_ENABLED", False)
        or not expected.startswith(("/tmp/tivoli-finance-lab.", "/tmp/tivoli-retail-acceptance."))
        or Path(expected).name != "data"
        or connection.settings_dict["HOST"] != "127.0.0.1"
    ):
        raise FinanceDraftValidationError("Isolated synthetic Finance lab is unavailable.")
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_setting('data_directory')")
        actual = cursor.fetchone()[0]
    if actual != expected:
        raise FinanceDraftValidationError("Disposable database provenance check failed.")
    entity = LegalEntity.objects.get(code=getattr(settings, "FINANCE_LAB_ENTITY_CODE", "test-finance-lab"))
    _require_synthetic_demo(entity)
    return entity


def guarded(*, session_only=False):
    def decorate(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            try:
                entity = _target()
                if not session_only and (
                    not request.user.is_authenticated
                    or request.user.username != getattr(settings, "FINANCE_LAB_ACTOR", "")
                    or not request.user.is_active
                    or request.user.is_staff or request.user.is_superuser
                    or request.user.has_usable_password()
                ):
                    return JsonResponse({"detail": "Open the explicit isolated synthetic test session."}, status=403)
                if request.body and len(request.body) > 131072:
                    return JsonResponse({"detail": "Draft request exceeds the lab input limit."}, status=400)
                request.finance_entity = entity
                from django.apps import apps
                if (request.method == "POST" and request.headers.get("X-Finance-Request-ID")
                        and apps.is_installed("finance_allocation_lab")):
                    from finance_allocation_lab.recovery import RESOURCES, create_with_identity
                    resource = request.path.removeprefix("/lab/").removesuffix("/")
                    response = (create_with_identity(request, view, args, kwargs) if resource in RESOURCES
                                else view(request, *args, **kwargs))
                else:
                    response = view(request, *args, **kwargs)
            except FinanceDraftConflict as error:
                response = JsonResponse({"detail": error.messages[0], "current_revision": error.current_revision}, status=409)
            except (ValidationError, ValueError, TypeError, KeyError) as error:
                detail = error.messages[0] if isinstance(error, ValidationError) else "Invalid draft input; review the supplied fields."
                response = JsonResponse({"detail": detail}, status=400)
            except (LegalEntity.DoesNotExist, FinanceDraftJournal.DoesNotExist):
                response = JsonResponse({"detail": "Synthetic draft or lab context is unavailable."}, status=404)
            except IntegrityError:
                response = JsonResponse({"detail": "Conflicting draft setup code; reload choices and use a distinct synthetic code."}, status=409)
            response["Cache-Control"] = "no-store"
            return response
        return wrapped
    return decorate


def _body(request):
    data = json.loads(request.body or b"{}")
    if not isinstance(data, dict):
        raise ValueError("Object required")
    return data


@require_http_methods(["GET", "POST"])
@guarded(session_only=True)
def session(request):
    if request.method == "POST":
        actor = get_user_model().objects.filter(username=settings.FINANCE_LAB_ACTOR, is_active=True).first()
        if actor is None or actor.has_usable_password() or actor.is_staff or actor.is_superuser:
            raise FinanceDraftValidationError("Lab actor must be synthetic, nonstaff and have no usable password.")
        login(request, actor, backend="django.contrib.auth.backends.ModelBackend")
    return JsonResponse({
        "synthetic_only": True, "csrf_token": get_token(request),
        "authenticated": request.user.is_authenticated and request.user.username == settings.FINANCE_LAB_ACTOR,
        "recovery_scope": (hashlib.sha256(
            f"{settings.FINANCE_LAB_DATA_DIRECTORY}:{request.user.pk}:{request.finance_entity.pk}".encode()
        ).hexdigest()
                           if request.user.is_authenticated and request.user.username == settings.FINANCE_LAB_ACTOR
                           else ""),
    })


@require_http_methods(["GET"])
@guarded(session_only=True)
def ui(request, filename=None):
    if filename is not None and not re.fullmatch(r"[A-Za-z0-9_-]+\.(?:js|css)", filename):
        return JsonResponse({"detail": "Unknown lab asset."}, status=404)
    root = settings.BASE_DIR / "generated-artifacts" / "finance-draft-prototype"
    path = root / "assets" / filename if filename else root / "index.html"
    if not path.is_file():
        return JsonResponse({"detail": "Build the isolated Finance prototype first."}, status=404)
    types = {".html": "text/html", ".js": "text/javascript", ".css": "text/css"}
    return FileResponse(path.open("rb"), content_type=types[path.suffix])


@require_http_methods(["GET"])
@guarded()
def context(request):
    entity = request.finance_entity
    return JsonResponse({
        "legal_entity_label": entity.name,
        "business_units": list(BusinessUnit.objects.filter(legal_entity=entity).order_by("code").values("id", "code", "name")),
        "accounts": list(FinanceDraftAccount.objects.filter(legal_entity=entity).values("id", "code", "name_en", "name_ar")),
        "dimensions": list(FinanceDraftDimension.objects.filter(legal_entity=entity).values("id", "code", "label_en", "label_ar")),
        "dimension_values": list(FinanceDraftDimensionValue.objects.filter(dimension__legal_entity=entity)
                                .values("id", "dimension_id", "code", "label_en", "label_ar")),
    })


@require_http_methods(["POST"])
@guarded()
def accounts(request):
    row = create_finance_draft_account(legal_entity=request.finance_entity, **_body(request))
    return JsonResponse({"id": row.pk}, status=201)


@require_http_methods(["POST"])
@guarded()
def dimensions(request):
    row = create_finance_draft_dimension(legal_entity=request.finance_entity, **_body(request))
    return JsonResponse({"id": row.pk}, status=201)


@require_http_methods(["POST"])
@guarded()
def dimension_values(request):
    data = _body(request)
    dimension = FinanceDraftDimension.objects.filter(pk=data.pop("dimension_id", None), legal_entity=request.finance_entity).first()
    if not dimension:
        raise FinanceDraftValidationError("Select a draft dimension in the current synthetic entity.")
    row = create_finance_draft_dimension_value(dimension=dimension, **data)
    return JsonResponse({"id": row.pk}, status=201)


def _journal_input(data, entity):
    if not isinstance(data.get("lines"), list) or len(data["lines"]) > 200:
        raise FinanceDraftValidationError("Provide a list of at most 200 draft lines.")
    prepared = []
    for line in data["lines"]:
        if not isinstance(line, dict):
            raise FinanceDraftValidationError("Each draft line must be an object; no draft was saved.")
        account = FinanceDraftAccount.objects.filter(pk=line.get("account_id"), legal_entity=entity).first()
        if not account:
            raise FinanceDraftValidationError("Select an account in the current synthetic entity.")
        unit_id = line.get("business_unit_id")
        unit = BusinessUnit.objects.filter(pk=unit_id, legal_entity=entity).first() if unit_id is not None else None
        if unit_id is not None and unit is None:
            raise FinanceDraftValidationError("Selected BU is outside the current synthetic entity.")
        ids = line.get("dimension_value_ids", [])
        if not isinstance(ids, list) or any(isinstance(value, bool) or not isinstance(value, int) for value in ids) or len(ids) != len(set(ids)):
            raise FinanceDraftValidationError("Choose distinct draft dimension values.")
        values = list(FinanceDraftDimensionValue.objects.filter(pk__in=ids, dimension__legal_entity=entity))
        if len(values) != len(ids):
            raise FinanceDraftValidationError("A dimension value is outside the current synthetic entity.")
        prepared.append({
            "account": account, "business_unit": unit, "dimensions": values,
            "debit": line.get("debit", ""), "credit": line.get("credit", ""),
            "memo_en": line.get("memo_en", ""), "memo_ar": line.get("memo_ar", ""),
        })
    return {"legal_entity": entity, "title_en": data.get("title_en"), "title_ar": data.get("title_ar", ""), "lines": prepared,
            "kind": data.get("kind", "journal"), "recurrence_proposal": data.get("recurrence_proposal"),
            "as_of_date": data.get("as_of_date"),
            "adjustment_proposal": data.get("adjustment_proposal")}


def _detail(row):
    data = finance_draft_snapshot(row)
    data["history"] = list(row.revisions.order_by("revision").values("revision", "created_at", "snapshot"))
    return data


@require_http_methods(["GET", "POST"])
@guarded()
def journals(request):
    entity = request.finance_entity
    if request.method == "GET":
        return JsonResponse({"drafts": list(FinanceDraftJournal.objects.filter(legal_entity=entity)
                                          .order_by("-updated_at", "-pk").values("id", "title_en", "title_ar", "revision", "kind", "as_of_date"))})
    with transaction.atomic():
        row = create_finance_draft_journal(**_journal_input(_body(request), entity))
        return JsonResponse(_detail(row), status=201)


@require_http_methods(["GET", "PUT"])
@guarded()
def journal(request, journal_id):
    with transaction.atomic():
        # A row lock also makes detail/revision reads coherent with edits.
        row = FinanceDraftJournal.objects.select_for_update().get(pk=journal_id, legal_entity=request.finance_entity)
        if request.method == "PUT":
            data = _body(request)
            row = update_finance_draft_journal(
                journal_id=row.pk, expected_revision=data.get("expected_revision"),
                **_journal_input(data, request.finance_entity),
            )
        return JsonResponse(_detail(row))


@require_http_methods(["GET"])
@guarded()
def recurring_preview(request, journal_id):
    from .finance_recurrence import preview_recurring_template
    with transaction.atomic():
        row = FinanceDraftJournal.objects.select_for_update().get(pk=journal_id, legal_entity=request.finance_entity)
        return JsonResponse(preview_recurring_template(row))
