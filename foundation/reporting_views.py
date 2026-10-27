from django.contrib.auth import get_user_model
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from rest_framework import status
from rest_framework.exceptions import APIException, PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import (
    BusinessUnit,
    Item,
    LegalEntity,
    Location,
    ReportBudget,
    ReportDataGrant,
    ReportPanelGrant,
    ReportingPanel,
    ReportingRole,
    ReportingRoleMembership,
)
from .reporting_visuals import build_report_visual
from .reporting_exports import render_report_export, report_export_manifest
from .reporting_services import (
    ReportingError,
    ReportingNotConfigured,
    ReportingPermissionDenied,
    ReportingValidationError,
    add_reporting_role_member,
    assign_report_panel,
    available_report_panels,
    available_report_scopes,
    build_report,
    build_delivery_details,
    build_sales_examples,
    build_purchase_receipt_register,
    build_quote_reservations,
    configure_report_budget,
    create_reporting_role,
    grant_report_data,
    list_reporting_configuration,
    list_report_panel_management,
    record_report_export,
    require_reporting_admin,
    require_reporting_panel_manager,
    revoke_reporting_access,
    revoke_report_panel,
    set_item_category,
)


class ReportingConflict(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "The requested report configuration is unavailable."
    default_code = "report_conflict"


def _service(function, **kwargs):
    try:
        return function(**kwargs)
    except ReportingPermissionDenied as error:
        raise PermissionDenied(str(error)) from error
    except ReportingNotConfigured as error:
        raise ReportingConflict(str(error)) from error
    except ReportingValidationError as error:
        raise ValidationError({"detail": str(error)}) from error
    except ReportingError as error:
        raise ValidationError({"detail": str(error)}) from error


def _payload(request):
    if not isinstance(request.data, dict):
        raise ValidationError({"detail": "Send a JSON object."})
    return request.data


def _synthetic_user(user_id):
    user_model = get_user_model()
    return get_object_or_404(
        user_model.objects.filter(demo_marker__is_synthetic=True),
        pk=user_id,
    )


def _synthetic_role(role_id):
    return get_object_or_404(ReportingRole, pk=role_id, is_synthetic=True)


def _synthetic_unit(unit_id):
    return get_object_or_404(BusinessUnit, pk=unit_id, is_synthetic=True, is_active=True)


def _synthetic_entity(entity_id):
    return get_object_or_404(LegalEntity, pk=entity_id, is_synthetic=True)


@method_decorator(never_cache, name="dispatch")
class ReportPanelsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        panels = _service(available_report_panels, actor=request.user)
        scopes = _service(available_report_scopes, actor=request.user)
        return Response({"panels": panels, "scopes": scopes})


@method_decorator(never_cache, name="dispatch")
class ReportPanelView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, panel):
        report = _service(build_report, actor=request.user, panel=panel, query_params=request.query_params)
        return Response(report)


@method_decorator(never_cache, name="dispatch")
class ReportVisualView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, panel):
        return Response(_service(build_report_visual, actor=request.user, panel=panel, query_params=request.query_params))


@method_decorator(never_cache, name="dispatch")
class SalesExamplesView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        examples = _service(
            build_sales_examples,
            actor=request.user,
            query_params=request.query_params,
        )
        return Response(examples)


class PurchaseReceiptRegisterView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(
            _service(
                build_purchase_receipt_register,
                actor=request.user,
                query_params=request.query_params,
            )
        )


class PurchaseReceiptDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, receipt_id):
        return Response(
            _service(
                build_purchase_receipt_register,
                actor=request.user,
                query_params=request.query_params,
                receipt_id=receipt_id,
                paginate=False,
            )
        )


class QuoteReservationRegisterView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(
            _service(
                build_quote_reservations,
                actor=request.user,
                query_params=request.query_params,
            )
        )


class QuoteReservationDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, reservation_id):
        return Response(
            _service(
                build_quote_reservations,
                actor=request.user,
                query_params=request.query_params,
                reservation_id=reservation_id,
                paginate=False,
            )
        )


class DeliveryDetailRegisterView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(
            _service(
                build_delivery_details,
                actor=request.user,
                query_params=request.query_params,
            )
        )


class DeliveryLineDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, delivery_line_id):
        return Response(
            _service(
                build_delivery_details,
                actor=request.user,
                query_params=request.query_params,
                delivery_line_id=delivery_line_id,
                paginate=False,
            )
        )


class ReportExportView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, panel):
        payload = _payload(request)
        file_format = payload.get("format")
        if file_format not in ("xlsx", "docx"):
            raise ValidationError({"format": "Choose xlsx or docx."})
        report = _service(
            build_report,
            actor=request.user,
            panel=panel,
            query_params=payload,
            paginate=False,
        )
        content = render_report_export(report, file_format)
        manifest_fields, manifest_row_count = report_export_manifest(report)
        _service(
            record_report_export,
            user=request.user,
            panel=panel,
            file_format=file_format,
            report=report,
            filters=payload,
            fields=manifest_fields,
            row_count=manifest_row_count,
        )
        content_type = (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            if file_format == "xlsx"
            else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
        response = HttpResponse(content, content_type=content_type)
        response["Content-Disposition"] = f'attachment; filename="synthetic-{panel}-report.{file_format}"'
        response["Cache-Control"] = "private, no-store"
        return response


class ReportingConfigurationView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        config = _service(list_reporting_configuration, actor=request.user)
        config["locations"] = [
            {
                "id": location.pk,
                "name": location.name,
                "business_units": list(location.business_units.values_list("id", flat=True)),
            }
            for location in Location.objects.filter(is_synthetic=True).prefetch_related("business_units")
        ]
        config["items"] = [
            {
                "id": item.pk,
                "item_code": item.item_code,
                "description": item.description,
                "brand_name": item.brand.name,
                "category_label": item.category_label,
            }
            for item in Item.objects.filter(is_synthetic=True).select_related("brand").order_by("item_code")
        ]
        return Response(config)

    def post(self, request):
        payload = _payload(request)
        _service(require_reporting_admin, user=request.user)
        action = payload.get("action")
        if action == "create_role":
            role = _service(
                create_reporting_role,
                actor=request.user,
                code=payload.get("code", ""),
                name=payload.get("name", ""),
            )
            return Response({"role_id": role.pk, "code": role.code, "name": role.name}, status=201)
        if action == "add_member":
            role = _synthetic_role(payload.get("role_id"))
            user = _synthetic_user(payload.get("user_id"))
            membership, created = _service(
                add_reporting_role_member,
                actor=request.user,
                role=role,
                target_user=user,
            )
            return Response(
                {"membership_id": membership.pk, "created": created},
                status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
            )
        if action == "assign_panel":
            target_user, target_role = self._target(payload)
            grant, created = _service(
                assign_report_panel,
                actor=request.user,
                panel=payload.get("panel"),
                target_user=target_user,
                target_role=target_role,
            )
            return Response(
                {"grant_id": grant.pk, "panel": grant.panel, "created": created},
                status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
            )
        if action == "grant_data":
            target_user, target_role = self._target(payload)
            unit = _synthetic_unit(payload["business_unit_id"]) if payload.get("business_unit_id") else None
            entity = _synthetic_entity(payload["legal_entity_id"]) if payload.get("legal_entity_id") else None
            grant, created = _service(
                grant_report_data,
                actor=request.user,
                capability=payload.get("capability"),
                target_user=target_user,
                target_role=target_role,
                business_unit=unit,
                legal_entity=entity,
            )
            return Response(
                {"grant_id": grant.pk, "capability": grant.capability, "created": created},
                status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
            )
        if action == "revoke_panel":
            grant = get_object_or_404(ReportPanelGrant, pk=payload.get("grant_id"), is_synthetic=True)
            grant = _service(revoke_reporting_access, actor=request.user, grant=grant)
            return Response({"grant_id": grant.pk, "revoked": True})
        if action == "revoke_data":
            grant = get_object_or_404(ReportDataGrant, pk=payload.get("grant_id"), is_synthetic=True)
            grant = _service(revoke_reporting_access, actor=request.user, grant=grant)
            return Response({"grant_id": grant.pk, "revoked": True})
        if action == "revoke_member":
            membership = get_object_or_404(ReportingRoleMembership, pk=payload.get("membership_id"), is_synthetic=True)
            if membership.revoked_at is None:
                membership.revoked_at = timezone.now()
                membership.save(update_fields=["revoked_at"])
            return Response({"membership_id": membership.pk, "revoked": True})
        if action == "set_budget":
            unit = _synthetic_unit(payload.get("business_unit_id"))
            location = (
                get_object_or_404(Location, pk=payload["location_id"], is_synthetic=True)
                if payload.get("location_id")
                else None
            )
            budget = _service(
                configure_report_budget,
                actor=request.user,
                business_unit=unit,
                location=location,
                metric=payload.get("metric"),
                granularity=payload.get("granularity"),
                period_start=payload.get("period_start"),
                period_end=payload.get("period_end"),
                amount=payload.get("amount"),
            )
            return Response({"budget_id": budget.pk, "amount": format(budget.amount, "f")}, status=201)
        if action == "set_category":
            item = get_object_or_404(Item, pk=payload.get("item_id"), is_synthetic=True)
            item = _service(
                set_item_category,
                actor=request.user,
                item=item,
                category_label=payload.get("category_label", ""),
            )
            return Response({"item_id": item.pk, "category_label": item.category_label})
        raise ValidationError({"action": "Choose a supported report configuration action."})

    @staticmethod
    def _target(payload):
        has_user = payload.get("user_id") not in (None, "")
        has_role = payload.get("role_id") not in (None, "")
        if has_user == has_role:
            raise ValidationError({"detail": "Choose exactly one synthetic user or report role."})
        return (
            (_synthetic_user(payload["user_id"]), None)
            if has_user
            else (None, _synthetic_role(payload["role_id"]))
        )


class ReportingPanelManagementView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        config = _service(list_report_panel_management, actor=request.user)
        return Response(config)

    def post(self, request):
        payload = _payload(request)
        _service(require_reporting_panel_manager, user=request.user)
        action = payload.get("action")
        if action == "assign_panel":
            target_user, target_role = ReportingConfigurationView._target(payload)
            grant, created = _service(
                assign_report_panel,
                actor=request.user,
                panel=payload.get("panel"),
                target_user=target_user,
                target_role=target_role,
            )
            return Response(
                {"grant_id": grant.pk, "panel": grant.panel, "created": created},
                status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
            )
        if action == "revoke_panel":
            grant = get_object_or_404(ReportPanelGrant, pk=payload.get("grant_id"), is_synthetic=True)
            grant = _service(revoke_report_panel, actor=request.user, grant=grant)
            return Response({"grant_id": grant.pk, "revoked": True})
        raise ValidationError({"action": "Only report-panel assignment and revocation are available here."})
