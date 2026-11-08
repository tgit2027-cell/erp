"""Attendance: month view, record a day, confirm or reopen a month."""

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .attendance import attendance_month, confirm_month, record_attendance
from .leave_views import _unit
from .purchasing_views import _payload, _run_service


class AttendanceView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(_run_service(attendance_month, actor=request.user, business_unit=_unit(request),
                                     month=request.query_params.get("month")))

    def post(self, request):
        return Response(_run_service(record_attendance, actor=request.user, data=_payload(request)))


class AttendanceConfirmView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        return Response(_run_service(confirm_month, actor=request.user, business_unit=_unit(request), data=_payload(request)))
