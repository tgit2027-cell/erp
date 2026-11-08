"""Customer registration by QID, lookup and history."""

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .customers import customer_detail, customer_overview, register_customer, update_customer
from .purchasing_views import _payload, _run_service


class CustomerListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(_run_service(
            customer_overview, actor=request.user,
            search=request.query_params.get("search", ""), title=request.query_params.get("title", ""),
        ))

    def post(self, request):
        return Response(
            _run_service(register_customer, actor=request.user, data=_payload(request)),
            status=status.HTTP_201_CREATED,
        )


class CustomerDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, customer_id):
        return Response(_run_service(customer_detail, actor=request.user, customer_id=customer_id))

    def patch(self, request, customer_id):
        return Response(_run_service(update_customer, actor=request.user, customer_id=customer_id, data=_payload(request)))
