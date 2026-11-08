"""Factory: jobs, materials, workshop hours, recipes and setup (Phase 2, behind a switch)."""

from rest_framework.exceptions import ValidationError
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .access import resolve_unit
from .factory import (
    create_order,
    decide_material_request,
    decide_materials,
    factory_overview,
    material_request_slip,
    order_action,
    purchase_action,
    read_boq_sheet,
    receive_materials,
    record_hours,
    request_purchase,
    save_raw_material,
    take_materials,
    update_order,
    update_settings,
)
from .factory_autoplan import plan_overview, replan_now, save_brief, set_arrival
from .factory_learning import apply_suggestion, learned_overview, suggest_for_line
from .factory_machines import (
    link_asset,
    machines_overview,
    record_service,
    save_machine,
    save_part,
)
from .factory_planning import (
    factory_dashboard,
    plan_line,
    planning_overview,
    save_tag,
    update_line,
    update_stage,
)
from .factory_team import save_worker, team_workload
from .purchasing_views import _payload, _run_service


class FactoryView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(_run_service(factory_overview, actor=request.user, business_unit=resolve_unit(request)))


class FactoryOrderListView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        return Response(_run_service(create_order, actor=request.user, business_unit=resolve_unit(request),
                                     data=_payload(request)), status=201)


class FactoryOrderView(APIView):
    permission_classes = [IsAuthenticated]

    def put(self, request, order_id):
        return Response(_run_service(update_order, actor=request.user, business_unit=resolve_unit(request),
                                     order_id=order_id, data=_payload(request)))

    def post(self, request, order_id):
        data = _payload(request)
        return Response(_run_service(order_action, actor=request.user, business_unit=resolve_unit(request),
                                     order_id=order_id, action=data.get("action"), note=data.get("note", ""),
                                     destination=data.get("destination"), location_id=data.get("location_id"),
                                     date=data.get("date")))


class FactoryMaterialsView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, order_id):
        return Response(_run_service(take_materials, actor=request.user, business_unit=resolve_unit(request),
                                     order_id=order_id, data=_payload(request)), status=201)


class FactoryMaterialDecisionView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, issue_id):
        data = _payload(request)
        return Response(_run_service(decide_materials, actor=request.user, business_unit=resolve_unit(request),
                                     issue_id=issue_id, action=data.get("action"), note=data.get("note", "")))


class FactoryHoursView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, order_id):
        return Response(_run_service(record_hours, actor=request.user, business_unit=resolve_unit(request),
                                     order_id=order_id, data=_payload(request)), status=201)


class FactoryPurchaseListView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, order_id):
        return Response(_run_service(request_purchase, actor=request.user, business_unit=resolve_unit(request),
                                     order_id=order_id, data=_payload(request)), status=201)


class FactoryPurchaseView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, purchase_id):
        return Response(_run_service(purchase_action, actor=request.user, business_unit=resolve_unit(request),
                                     purchase_id=purchase_id, data=_payload(request)))


class FactoryRawMaterialView(APIView):
    permission_classes = [IsAuthenticated]

    def put(self, request):
        return Response(_run_service(save_raw_material, actor=request.user, business_unit=resolve_unit(request),
                                     data=_payload(request)))


class FactorySettingsView(APIView):
    permission_classes = [IsAuthenticated]

    def put(self, request):
        return Response(_run_service(update_settings, actor=request.user, business_unit=resolve_unit(request),
                                     data=_payload(request)))


class FactoryBoqUploadView(APIView):
    """Read a joinery BOQ workbook and return its JD items to fill a job order (nothing is saved)."""

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        files = request.FILES.getlist("file")
        if len(files) != 1:
            raise ValidationError({"file": "Attach one Excel file."})
        upload = files[0]
        if upload.size > 5 * 1024 * 1024:
            raise ValidationError({"file": "The file is larger than 5 MB."})
        return Response(_run_service(read_boq_sheet, actor=request.user, business_unit=resolve_unit(request),
                                     file_name=upload.name, content=upload.read()))


class FactoryDeliveryView(APIView):
    """The factory confirms the warehouse's delivery of materials arrived."""

    permission_classes = [IsAuthenticated]

    def post(self, request, request_id):
        return Response(_run_service(receive_materials, actor=request.user, business_unit=resolve_unit(request),
                                     request_id=request_id))


class FactoryPlanningView(APIView):
    """The production schedule: every JD with its stages, team and dates (list and Gantt views)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(_run_service(planning_overview, actor=request.user, business_unit=resolve_unit(request)))


class FactoryDashboardView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(_run_service(factory_dashboard, actor=request.user, business_unit=resolve_unit(request)))


class FactoryStageView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, stage_id):
        return Response(_run_service(update_stage, actor=request.user, business_unit=resolve_unit(request),
                                     stage_id=stage_id, data=_payload(request)))


class FactoryBoqLineView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, line_id):
        return Response(_run_service(update_line, actor=request.user, business_unit=resolve_unit(request),
                                     line_id=line_id, data=_payload(request)))


class FactoryBoqLinePlanView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, line_id):
        return Response(_run_service(plan_line, actor=request.user, business_unit=resolve_unit(request),
                                     line_id=line_id, data=_payload(request)))


class FactoryTagView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        return Response(_run_service(save_tag, actor=request.user, business_unit=resolve_unit(request),
                                     data=_payload(request)))


class FactoryMaterialRequestView(APIView):
    """A material request slip: the Factory Manager approves or rejects it; once both approvals are in, it is printed."""

    permission_classes = [IsAuthenticated]

    def get(self, request, request_id):
        return Response(_run_service(material_request_slip, actor=request.user, business_unit=resolve_unit(request),
                                     request_id=request_id))

    def post(self, request, request_id):
        data = _payload(request)
        if data.get("action") == "print":
            return Response(_run_service(material_request_slip, actor=request.user, business_unit=resolve_unit(request),
                                         request_id=request_id, printing=True))
        return Response(_run_service(decide_material_request, actor=request.user, business_unit=resolve_unit(request),
                                     request_id=request_id, action=data.get("action"), note=data.get("note", "")))


class FactoryTeamView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(_run_service(team_workload, actor=request.user, business_unit=resolve_unit(request),
                                     person_id=request.query_params.get("person")))

    def post(self, request):
        return Response(_run_service(save_worker, actor=request.user, business_unit=resolve_unit(request),
                                     data=_payload(request)))


class FactoryMachinesView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(_run_service(machines_overview, actor=request.user, business_unit=resolve_unit(request)))

    def post(self, request):
        return Response(_run_service(save_machine, actor=request.user, business_unit=resolve_unit(request),
                                     data=_payload(request)))


class FactoryMachineServiceView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, machine_id):
        return Response(_run_service(record_service, actor=request.user, business_unit=resolve_unit(request),
                                     machine_id=machine_id, data=_payload(request)))


class FactoryMachineAssetView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, machine_id):
        return Response(_run_service(link_asset, actor=request.user, business_unit=resolve_unit(request),
                                     machine_id=machine_id, data=_payload(request)))


class FactoryPartsView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        return Response(_run_service(save_part, actor=request.user, business_unit=resolve_unit(request),
                                     data=_payload(request)))


class FactoryLearningView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(_run_service(learned_overview, actor=request.user, business_unit=resolve_unit(request)))


class FactoryLineSuggestionView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, line_id):
        return Response(_run_service(suggest_for_line, actor=request.user, business_unit=resolve_unit(request),
                                     line_id=line_id))

    def post(self, request, line_id):
        return Response(_run_service(apply_suggestion, actor=request.user, business_unit=resolve_unit(request),
                                     line_id=line_id, data=_payload(request)))


class FactoryPlanView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(_run_service(plan_overview, actor=request.user, business_unit=resolve_unit(request)))

    def post(self, request):
        return Response(_run_service(replan_now, actor=request.user, business_unit=resolve_unit(request),
                                     data=_payload(request)))


class FactoryPlanBriefView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, order_id):
        return Response(_run_service(save_brief, actor=request.user, business_unit=resolve_unit(request),
                                     order_id=order_id, data=_payload(request)))


class FactoryArrivalView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        return Response(_run_service(set_arrival, actor=request.user, business_unit=resolve_unit(request),
                                     data=_payload(request)))
