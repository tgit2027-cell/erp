"""System-administrator endpoints for stock transfer permissions."""

from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .purchasing_views import _payload, _run_service
from .transaction_services import TransactionServiceError
from .transfer_permissions import lookup_unit, set_transfer_permission, transfer_permission_overview


def _unit(value):
    try:
        unit_id = int(value)
    except (TypeError, ValueError):
        raise ValidationError({"unit": "Choose a business unit."}) from None
    try:
        return lookup_unit(unit_id)
    except TransactionServiceError as error:
        raise ValidationError({"unit": str(error)}) from error


class TransferPermissionView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        unit = _unit(request.query_params.get("unit"))
        return Response(_run_service(transfer_permission_overview, actor=request.user, business_unit=unit))

    def post(self, request):
        data = _payload(request)
        unit = _unit(data.get("unit_id"))
        user_id = data.get("user_id")
        if type(user_id) is not int:
            raise ValidationError({"user_id": "Choose a person."})
        return Response(_run_service(
            set_transfer_permission,
            actor=request.user,
            business_unit=unit,
            user_id=user_id,
            action=data.get("action"),
            enabled=data.get("enabled"),
        ))
