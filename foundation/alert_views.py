"""Alerts: who receives which alerts and where, the log, and sending now."""

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .alerts import alerts_overview, send_now, set_preference
from .purchasing_views import _payload, _run_service


class AlertView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(_run_service(alerts_overview, actor=request.user))

    def post(self, request):
        data = _payload(request)
        if data.get("action") == "send":
            return Response(_run_service(send_now, actor=request.user))
        return Response(_run_service(set_preference, actor=request.user, user_id=data.get("user_id"), types=data.get("types"),
                                     email=data.get("email")))
