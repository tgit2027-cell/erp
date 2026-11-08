"""Staff records: list, add, change, record leaving, identity documents."""

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .access import resolve_unit
from .purchasing_views import _payload, _run_service
from .staff_records import add_document, record_leaving, remove_document, save_employee, staff_detail, staff_overview


class StaffListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        query = request.query_params
        return Response(_run_service(staff_overview, actor=request.user, business_unit=resolve_unit(request),
                                     status=query.get("status", "current"), search=query.get("search", ""),
                                     department=query.get("department", "")))

    def post(self, request):
        return Response(_run_service(save_employee, actor=request.user, business_unit=resolve_unit(request),
                                     data=_payload(request)), status=201)


class StaffView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, employee_id):
        return Response(_run_service(staff_detail, actor=request.user, business_unit=resolve_unit(request),
                                     employee_id=employee_id))

    def post(self, request, employee_id):
        unit, data = resolve_unit(request), _payload(request)
        action = data.get("action")
        if action == "save":
            return Response(_run_service(save_employee, actor=request.user, business_unit=unit, data=data,
                                         employee_id=employee_id))
        if action == "leaving":
            return Response(_run_service(record_leaving, actor=request.user, business_unit=unit, employee_id=employee_id,
                                         last_day=data.get("last_day"), reason=data.get("reason", ""),
                                         cancel=data.get("cancel") is True))
        if action == "add_document":
            return Response(_run_service(add_document, actor=request.user, business_unit=unit, employee_id=employee_id,
                                         data=data), status=201)
        if action == "remove_document":
            return Response(_run_service(remove_document, actor=request.user, business_unit=unit,
                                         employee_id=employee_id, document_id=data.get("document_id")))
        return Response({"detail": "Choose save, leaving, add_document or remove_document."}, status=400)
