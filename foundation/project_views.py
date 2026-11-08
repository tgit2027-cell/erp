"""Projects & Fit-Out: projects from the signed BOQ, orders and budget, schedule, money, tenders and bonds."""

from rest_framework.exceptions import ValidationError
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .access import resolve_unit
from .projects import (
    add_variation,
    claim_action,
    create_order,
    create_project,
    order_action,
    prepare_claim,
    project_action,
    project_detail,
    projects_overview,
    read_boq,
    record_receipt,
    send_to_factory,
    update_project,
    update_schedule,
    update_settings,
    variation_action,
)
from .purchasing_views import _payload, _run_service
from .tenders import (
    add_bond,
    bonds_overview,
    create_tender,
    tenders_overview,
    update_bond,
    update_tender,
)


def _call(service, request, status=200, **kwargs):
    return Response(_run_service(service, actor=request.user, business_unit=resolve_unit(request), **kwargs),
                    status=status)


def _list(read, create):
    class View(APIView):
        permission_classes = [IsAuthenticated]

        def get(self, request):
            return _call(read, request)

        def post(self, request):
            return _call(create, request, status=201, data=_payload(request))

    return View


def _one(service, key):
    class View(APIView):
        permission_classes = [IsAuthenticated]

        def post(self, request, **kwargs):
            return _call(service, request, **{key: kwargs[key]}, data=_payload(request))

    return View


def _child(service, status=200):
    class View(APIView):
        permission_classes = [IsAuthenticated]

        def post(self, request, project_id):
            return _call(service, request, status=status, project_id=project_id, data=_payload(request))

    return View


ProjectsView = _list(projects_overview, create_project)
TendersView = _list(tenders_overview, create_tender)
BondsView = _list(bonds_overview, add_bond)


class ProjectView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, project_id):
        return _call(project_detail, request, project_id=project_id)

    def put(self, request, project_id):
        return _call(update_project, request, project_id=project_id, data=_payload(request))

    def post(self, request, project_id):
        return _call(project_action, request, project_id=project_id, data=_payload(request))


ProjectOrderListView = _child(create_order, 201)
ProjectScheduleView = _child(update_schedule)
ProjectReceiptView = _child(record_receipt, 201)
ProjectVariationListView = _child(add_variation, 201)
ProjectFactoryView = _child(send_to_factory, 201)
ProjectClaimListView = _child(prepare_claim, 201)
ProjectOrderView = _one(order_action, "order_id")
ProjectVariationView = _one(variation_action, "variation_id")
ProjectClaimView = _one(claim_action, "claim_id")
TenderView = _one(update_tender, "tender_id")
BondView = _one(update_bond, "bond_id")


class ProjectSettingsView(APIView):
    permission_classes = [IsAuthenticated]

    def put(self, request):
        return _call(update_settings, request, data=_payload(request))


class ProjectBoqUploadView(APIView):
    """Read a signed BOQ workbook and return its items, prices, costs and discount (nothing is saved)."""

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        files = request.FILES.getlist("file")
        if len(files) != 1:
            raise ValidationError({"file": "Attach one Excel file."})
        upload = files[0]
        if upload.size > 5 * 1024 * 1024:
            raise ValidationError({"file": "The file is larger than 5 MB."})
        return _call(read_boq, request, file_name=upload.name, content=upload.read())
