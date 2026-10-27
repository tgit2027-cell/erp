from django.contrib.auth import authenticate, login, logout
from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.middleware.csrf import get_token
from django.shortcuts import get_object_or_404
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny, IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from audit.models import AuditEvent
from audit.recording import record as audit_record

from .access import (
    stock_page_queryset,
    active_grants,
    apply_stock_search,
    context_units,
    permitted_units,
    resolve_unit,
    scoped_stock_queryset,
)
from .load_diagnostics import measure_stage
from .pagination import page_metadata, parse_page_parameters
from .models import (
    BusinessUnit,
    CustomBusinessUnitPalette,
    Location,
    UserActionGrant,
    UserFavorite,
)
from .palette_colors import build_custom_palette_colors, normalize_hex_color
from .reporting_services import can_manage_report_panels
from .serializers import StockExampleSerializer
from .warehouse_capacity_services import active_capacity_grants, has_capacity_workspace_access


def _custom_palette_data(palette):
    return {
        "id": palette.id,
        "value": f"custom:{palette.id}",
        "label": palette.name,
        "kind": "custom",
        "accent_color": palette.accent_color,
        "colors": build_custom_palette_colors(palette.accent_color),
    }


def _unit_palette_data(unit):
    custom = unit.custom_theme_palette if unit.custom_theme_palette_id else None
    return {
        "theme_palette": "custom" if custom else unit.theme_palette,
        "palette_value": f"custom:{custom.id}" if custom else unit.theme_palette,
        "custom_palette": _custom_palette_data(custom) if custom else None,
    }


def _unit_data(unit, include_company=False):
    data = {
        "id": unit.id,
        "code": unit.code,
        "name": unit.name,
        **_unit_palette_data(unit),
    }
    if include_company:
        data["company"] = unit.legal_entity.name
    return data


def _validated_custom_palette_input(payload, existing_palette=None):
    if not isinstance(payload, dict):
        raise ValidationError({"detail": "Send a palette name and accent color."})
    name = payload.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValidationError({"name": "A palette name is required."})
    name = name.strip()
    if len(name) > 80:
        raise ValidationError({"name": "Palette names must be 80 characters or fewer."})
    reserved_names = {label.casefold() for _value, label in BusinessUnit.Palette.choices}
    if name.casefold() in reserved_names:
        raise ValidationError({"name": "Choose a name distinct from the built-in palettes."})
    duplicate = CustomBusinessUnitPalette.objects.filter(name__iexact=name)
    if existing_palette is not None:
        duplicate = duplicate.exclude(pk=existing_palette.pk)
    if duplicate.exists():
        raise ValidationError({"name": "A custom palette with this name already exists."})

    try:
        accent_color = normalize_hex_color(payload.get("accent_color"))
        colors = build_custom_palette_colors(accent_color)
    except ValueError as error:
        raise ValidationError({"accent_color": str(error)}) from error
    return name, accent_color, colors


class CsrfView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    @method_decorator(ensure_csrf_cookie)
    def get(self, request):
        return Response({"csrfToken": get_token(request)})


def _login_failure_key(username):
    return f"login-failures:{username.strip().lower()}"


@method_decorator(csrf_protect, name="dispatch")
class LoginView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        username = request.data.get("username", "")
        password = request.data.get("password", "")
        if not isinstance(username, str) or not isinstance(password, str):
            return Response({"detail": "Invalid username or password."}, status=status.HTTP_400_BAD_REQUEST)
        failure_key = _login_failure_key(username)
        if cache.get(failure_key, 0) >= settings.LOGIN_FAILURE_LIMIT:
            audit_record(AuditEvent.Kind.SIGN_IN_LOCKED, request=request, username=username[:150],
                         status_code=status.HTTP_429_TOO_MANY_REQUESTS)
            return Response(
                {"detail": "Too many failed sign-in attempts. Try again later."},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )
        user = authenticate(request, username=username, password=password)
        if (
            user is None
            or not user.is_active
            or not (
                (settings.APP_ENV == "demo" and user.is_superuser)
                or active_grants(user).exists()
                or (settings.APP_ENV == "demo" and has_capacity_workspace_access(user))
            )
        ):
            if user is not None:
                # The password was right but the account has no access here.
                audit_record(AuditEvent.Kind.SIGN_IN_REFUSED, request=request, user=user,
                             status_code=status.HTTP_400_BAD_REQUEST)
            cache.add(failure_key, 0, settings.LOGIN_LOCKOUT_SECONDS)
            cache.incr(failure_key)
            return Response({"detail": "Invalid username or password."}, status=status.HTTP_400_BAD_REQUEST)
        cache.delete(failure_key)
        login(request, user)
        return Response(
            {
                "username": user.get_username(),
                "csrfToken": get_token(request),
                "is_staff": user.is_staff,
                "is_superuser": user.is_superuser,
                "can_manage_report_panels": can_manage_report_panels(user),
            }
        )


class LogoutView(APIView):
    def post(self, request):
        logout(request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class CurrentUserView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        if not request.user.is_authenticated:
            return Response({"authenticated": False})
        return Response(
            {
                "authenticated": True,
                "username": request.user.get_username(),
                "is_staff": request.user.is_staff,
                "is_superuser": request.user.is_superuser,
                "can_manage_report_panels": can_manage_report_panels(request.user),
            }
        )


class OrganizationView(APIView):
    def get(self, request):
        unit = resolve_unit(request)
        authorized_units = context_units(request.user).select_related(
            "legal_entity",
            "custom_theme_palette",
        )
        locations = Location.objects.filter(
            legal_entity=unit.legal_entity,
            business_units=unit,
            is_synthetic=True,
        ).select_related("parent").distinct()
        if not request.user.is_superuser and not active_grants(request.user).filter(
            business_unit=unit,
        ).exists():
            assigned_locations = active_capacity_grants(request.user).filter(
                location__location__business_units=unit,
            ).values("location__location_id")
            locations = locations.filter(pk__in=assigned_locations)
        return Response(
            {
                "company": {
                    "name": unit.legal_entity.name,
                    "code": unit.legal_entity.code,
                },
                "units": [_unit_data(item, include_company=True) for item in authorized_units],
                "selected_unit": _unit_data(unit),
                "locations": [
                    {
                        "id": location.id,
                        "code": location.code,
                        "name": location.name,
                        "parent_id": location.parent_id,
                        "kind": location.kind,
                        "area": location.area,
                    }
                    for location in locations
                ],
            }
        )


class BusinessUnitPaletteListView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        palettes = [
            {"value": value, "label": label, "kind": "built_in"}
            for value, label in BusinessUnit.Palette.choices
        ]
        palettes.extend(
            _custom_palette_data(palette)
            for palette in CustomBusinessUnitPalette.objects.all()
        )
        return Response(
            {
                "palettes": palettes,
                "units": [
                    _unit_data(unit)
                    for unit in BusinessUnit.objects.select_related("custom_theme_palette")
                ],
            }
        )

    def post(self, request):
        name, accent_color, _colors = _validated_custom_palette_input(request.data)
        try:
            with transaction.atomic():
                palette = CustomBusinessUnitPalette.objects.create(
                    name=name,
                    accent_color=accent_color,
                )
        except IntegrityError as error:
            raise ValidationError({"name": "A custom palette with this name already exists."}) from error
        return Response({"palette": _custom_palette_data(palette)}, status=status.HTTP_201_CREATED)


class CustomBusinessUnitPaletteDetailView(APIView):
    permission_classes = [IsAdminUser]

    def patch(self, request, palette_id):
        with transaction.atomic():
            palette = get_object_or_404(
                CustomBusinessUnitPalette.objects.select_for_update(),
                pk=palette_id,
            )
            name, accent_color, _colors = _validated_custom_palette_input(
                request.data,
                existing_palette=palette,
            )
            palette.name = name
            palette.accent_color = accent_color
            palette.save(update_fields=["name", "accent_color", "updated_at"])
        return Response({"palette": _custom_palette_data(palette)})


class BusinessUnitPaletteDetailView(APIView):
    permission_classes = [IsAdminUser]

    def patch(self, request, unit_id):
        payload = request.data
        if not isinstance(payload, dict):
            raise ValidationError({"theme_palette": "A palette value is required."})
        palette_value = payload.get("theme_palette")
        built_in_palettes = dict(BusinessUnit.Palette.choices)
        custom_palette_id = None
        if isinstance(palette_value, str) and palette_value.startswith("custom:"):
            raw_id = palette_value.partition(":")[2]
            if raw_id.isdigit():
                custom_palette_id = int(raw_id)
            else:
                custom_palette_id = -1
        elif not isinstance(palette_value, str) or palette_value not in built_in_palettes:
            raise ValidationError(
                {"theme_palette": "Choose one of the supported business-unit palettes."}
            )

        with transaction.atomic():
            unit = get_object_or_404(
                BusinessUnit.objects.select_for_update(),
                pk=unit_id,
            )
            if custom_palette_id is not None:
                custom_palette = get_object_or_404(
                    CustomBusinessUnitPalette.objects.select_for_update(),
                    pk=custom_palette_id,
                )
                unit.custom_theme_palette = custom_palette
                update_fields = ["custom_theme_palette"]
            else:
                unit.theme_palette = palette_value
                unit.custom_theme_palette = None
                update_fields = ["theme_palette", "custom_theme_palette"]
            unit.save(update_fields=update_fields)
            unit.refresh_from_db()
        return Response({"unit": _unit_data(unit)})


class SelectUnitView(APIView):
    def post(self, request, unit_id):
        if not context_units(request.user).filter(pk=unit_id).exists():
            raise PermissionDenied("The requested unit is not authorized.")
        request.session["selected_business_unit"] = unit_id
        unit = context_units(request.user).get(pk=unit_id)
        return Response({"selected_unit": {"id": unit.id, "code": unit.code, "name": unit.name}})


class StockListView(APIView):
    def get(self, request):
        unit = resolve_unit(request, UserActionGrant.Action.READ_STOCK)
        records = scoped_stock_queryset(unit, request.user)
        location = request.query_params.get("location")
        if location not in (None, ""):
            try:
                location_id = int(location)
            except (TypeError, ValueError):
                raise ValidationError({"location": "Location must be a numeric ID."}) from None
            if not Location.objects.filter(
                pk=location_id,
                legal_entity=unit.legal_entity,
                business_units=unit,
                is_synthetic=True,
            ).exists():
                raise PermissionDenied("The requested location is outside the selected unit's authorized scope.")
            records = records.filter(location_id=location_id)
        search = request.query_params.get("search", "").strip()
        if search:
            records = apply_stock_search(records, search)
        try:
            page, page_size = parse_page_parameters(request.query_params)
            with measure_stage("database_fetch"):
                total_count = records.count()
                pagination = page_metadata(total_count, page, page_size)
                records = list(stock_page_queryset(
                    records, page=page, page_size=page_size, total_count=total_count,
                ))
        except ValueError as error:
            raise ValidationError({"pagination": str(error)}) from error
        with measure_stage("python_transformation_aggregation"):
            data = StockExampleSerializer(records, many=True).data
        return Response({"results": data, "pagination": pagination})


class StockDetailView(APIView):
    def get(self, request, stock_id):
        unit = resolve_unit(request, UserActionGrant.Action.READ_STOCK)
        queryset = scoped_stock_queryset(unit, request.user)
        stock = queryset.filter(pk=stock_id).first()
        if stock is None:
            raise NotFound("Stock example not found.")
        return Response(StockExampleSerializer(stock).data)


class FavoriteDestinationsView(APIView):
    def _available_destinations(self, request):
        from .reporting_services import has_report_destination_access
        from .navigation_destinations import FUNCTION_DESTINATIONS, function_is_available

        unit = resolve_unit(request)
        can_read_stock = active_grants(
            request.user,
            UserActionGrant.Action.READ_STOCK,
        ).filter(business_unit=unit).exists()
        can_read_reports = has_report_destination_access(actor=request.user)
        manages_panels = can_manage_report_panels(request.user)
        sections = [
            {"destination": destination, "label": label}
            for destination, label in UserFavorite.Destination.choices
            if destination != UserFavorite.Destination.INVENTORY or can_read_stock
            if destination != UserFavorite.Destination.REPORTS or can_read_reports
        ]
        functions = [
            {"destination": destination, "label": label}
            for destination, label in FUNCTION_DESTINATIONS
            if function_is_available(
                destination,
                is_staff=request.user.is_staff,
                is_superuser=request.user.is_superuser,
                manages_panels=manages_panels,
                reads_stock=can_read_stock,
            )
        ]
        return sections + functions

    def get(self, request):
        available = self._available_destinations(request)
        available_keys = [item["destination"] for item in available]
        labels = {item["destination"]: item["label"] for item in available}
        favorites = UserFavorite.objects.filter(
            user=request.user,
            destination__in=available_keys,
        ).values_list("destination", flat=True)
        return Response(
            {
                "available_destinations": available,
                "favorites": [
                    {"destination": destination, "label": labels[destination]}
                    for destination in favorites
                ],
            }
        )

    def post(self, request):
        payload = request.data
        if not isinstance(payload, dict) or not isinstance(payload.get("destination"), str):
            raise ValidationError({"destination": "A destination key is required."})
        destination = payload["destination"]
        labels = dict(UserFavorite._meta.get_field("destination").choices)
        if destination not in labels:
            raise ValidationError({"destination": "Unknown favorite destination."})

        available = {
            item["destination"]: item["label"]
            for item in self._available_destinations(request)
        }
        if destination not in available:
            raise PermissionDenied("This destination is not available in the selected business unit.")

        favorite, created = UserFavorite.objects.get_or_create(
            user=request.user,
            destination=destination,
        )
        return Response(
            {
                "favorite": {
                    "destination": favorite.destination,
                    "label": available[favorite.destination],
                }
            },
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class FavoriteDestinationDetailView(APIView):
    def delete(self, request, destination):
        favorite = UserFavorite.objects.filter(
            user=request.user,
            destination=destination,
        ).first()
        if favorite is None:
            raise NotFound("Favorite destination not found.")
        favorite.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
