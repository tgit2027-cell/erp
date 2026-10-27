"""Location-scoped synthetic warehouse capacity APIs."""

from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import (
    DemoUserMarker,
    LocationCapacity,
    LocationCapacityGrant,
)
from .pagination import page_metadata, parse_page_parameters
from .warehouse_capacity_services import (
    CapacityInputError,
    CapacityPermissionDenied,
    _require_demo_environment,
    _require_synthetic_manager,
    _require_capacity_admin,
    active_capacity_grants,
    capacity_audit_event_data,
    capacity_audit_queryset,
    admin_capacity_configuration,
    assign_capacity,
    can_edit_location_capacity,
    can_view_location_capacity,
    designate_capacity_location,
    location_capacity_data,
    revoke_capacity_assignment,
    set_location_capacity,
)


def _translate_error(error):
    if isinstance(error, CapacityPermissionDenied):
        raise PermissionDenied(str(error)) from error
    if isinstance(error, CapacityInputError):
        raise ValidationError({error.field: str(error)}) from error
    raise error


class WarehouseCapacityListView(APIView):
    def get(self, request):
        try:
            _require_demo_environment()
        except CapacityPermissionDenied as error:
            _translate_error(error)
        if not DemoUserMarker.objects.filter(user=request.user, is_synthetic=True).exists():
            return Response({"results": []})
        grants = active_capacity_grants(request.user).select_related(
            "location__location__legal_entity",
        )

        effective = {}
        for grant in grants:
            current = effective.get(grant.location_id)
            if current is None or grant.capability == LocationCapacityGrant.Capability.EDIT:
                effective[grant.location_id] = grant
        profiles = LocationCapacity.objects.filter(
            pk__in=effective.keys(),
            is_synthetic=True,
            location__is_synthetic=True,
            location__legal_entity__is_synthetic=True,
        ).select_related("location").order_by("location__code")
        return Response({
            "results": [
                location_capacity_data(profile, effective[profile.pk].capability)
                for profile in profiles
            ],
        })


class WarehouseCapacityDetailView(APIView):
    def get(self, request, location_id):
        try:
            _require_demo_environment()
            _require_synthetic_manager(request.user)
        except CapacityPermissionDenied as error:
            _translate_error(error)
        profile = LocationCapacity.objects.filter(
            location_id=location_id,
            is_synthetic=True,
            location__is_synthetic=True,
            location__legal_entity__is_synthetic=True,
        ).select_related("location").first()
        if profile is None:
            raise NotFound("Physical location capacity profile not found.")
        if not can_view_location_capacity(request.user, location_id):
            raise PermissionDenied("An explicit capacity assignment for this physical location is required.")
        capability = (
            LocationCapacityGrant.Capability.EDIT
            if can_edit_location_capacity(request.user, location_id)
            else LocationCapacityGrant.Capability.VIEW
        )
        return Response(location_capacity_data(profile, capability))

    def put(self, request, location_id):
        try:
            profile = set_location_capacity(
                actor=request.user,
                location_id=location_id,
                payload=request.data,
            )
        except (CapacityInputError, CapacityPermissionDenied) as error:
            _translate_error(error)
        return Response(location_capacity_data(profile, LocationCapacityGrant.Capability.EDIT))


class WarehouseCapacityAdminView(APIView):
    """ERP/system-admin-only physical profile and per-location grant management."""

    def get(self, request):
        try:
            _require_capacity_admin(request.user)
        except CapacityPermissionDenied as error:
            _translate_error(error)
        return Response(admin_capacity_configuration())

    def post(self, request):
        try:
            _require_capacity_admin(request.user)
        except CapacityPermissionDenied as error:
            _translate_error(error)
        location_id = request.data.get("location_id")
        try:
            location_id = int(location_id)
            profile, created = designate_capacity_location(
                actor=request.user,
                location_id=location_id,
            )
        except (TypeError, ValueError):
            raise ValidationError({"location_id": "Choose a valid synthetic location."}) from None
        except (CapacityInputError, CapacityPermissionDenied) as error:
            _translate_error(error)
        return Response(
            {
                "location": {
                    "id": profile.location_id,
                    "code": profile.location.code,
                    "name": profile.location.name,
                },
                "physical_capacity_configured": True,
                "created": created,
            },
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class WarehouseCapacityAuditView(APIView):
    """Read-only synthetic audit metadata for ERP/system administrators."""

    http_method_names = ["get", "head", "options"]

    def get(self, request):
        try:
            _require_capacity_admin(request.user)
        except CapacityPermissionDenied as error:
            _translate_error(error)

        try:
            page, page_size = parse_page_parameters(request.query_params)
            queryset = capacity_audit_queryset()
            total_count = queryset.count()
            pagination = page_metadata(total_count, page, page_size)
        except ValueError as error:
            raise ValidationError({"pagination": str(error)}) from error

        start = (page - 1) * page_size
        events = list(queryset[start:start + page_size])
        user_ids = {
            user_id
            for event in events
            for user_id in (event.actor_id, event.target_user_id)
            if user_id is not None
        }
        synthetic_user_ids = set(
            DemoUserMarker.objects.filter(
                user_id__in=user_ids,
                is_synthetic=True,
            ).values_list("user_id", flat=True)
        )
        return Response({
            "results": [
                capacity_audit_event_data(event, synthetic_user_ids)
                for event in events
            ],
            "pagination": pagination,
        })


class WarehouseCapacityAssignmentListView(APIView):
    def post(self, request):
        try:
            _require_capacity_admin(request.user)
        except CapacityPermissionDenied as error:
            _translate_error(error)
        try:
            user_id = int(request.data.get("user_id"))
            location_id = int(request.data.get("location_id"))
        except (TypeError, ValueError):
            raise ValidationError({"detail": "Choose a synthetic user and configured physical location."}) from None
        try:
            grant = assign_capacity(
                actor=request.user,
                user_id=user_id,
                location_id=location_id,
                capability=request.data.get("capability"),
            )
        except (CapacityInputError, CapacityPermissionDenied) as error:
            _translate_error(error)
        return Response(
            {
                "assignment": {
                    "id": grant.id,
                    "user_id": grant.user_id,
                    "username": grant.user.get_username(),
                    "location_id": grant.location.location_id,
                    "capability": grant.capability,
                    "created_at": grant.created_at.isoformat(),
                },
            },
            status=status.HTTP_201_CREATED,
        )


class WarehouseCapacityAssignmentDetailView(APIView):
    def delete(self, request, assignment_id):
        try:
            grant = revoke_capacity_assignment(actor=request.user, grant_id=assignment_id)
        except (CapacityInputError, CapacityPermissionDenied) as error:
            _translate_error(error)
        return Response(
            {
                "revoked": True,
                "assignment_id": grant.id,
                "location_id": grant.location.location_id,
                "username": grant.user.get_username(),
                "capability": grant.capability,
            },
        )
