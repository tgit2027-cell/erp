"""Staff-only read of the scenario reconciliation; it shows cost and margin."""
from django.shortcuts import get_object_or_404
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import BusinessUnit
from .purchasing_services import _require_staff_demo
from .purchasing_views import _query_id, _run_service
from .scenario_reconciliation import build_scenario_reconciliation


class ScenarioReconciliationView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        _run_service(_require_staff_demo, user=request.user)
        unit = get_object_or_404(
            BusinessUnit.objects.filter(is_synthetic=True, is_active=True),
            pk=_query_id(request.query_params, "business_unit_id", required=True),
        )
        return Response(build_scenario_reconciliation(unit))
