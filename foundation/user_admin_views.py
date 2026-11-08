"""System-administrator endpoints for users and permissions in one place."""

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .purchasing_views import _payload, _run_service
from .teams import change_team, create_team, team_detail, teams_overview
from .user_admin import add_person, change_person, copy_unit_access, people_overview, person_detail, reset_password


class PeopleView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(_run_service(people_overview, actor=request.user, search=request.query_params.get("search", "")))

    def post(self, request):
        data = _payload(request)
        return Response(_run_service(
            add_person, actor=request.user, username=data.get("username"), first_name=data.get("first_name", ""),
            last_name=data.get("last_name", ""), email=data.get("email", ""), password=data.get("password", ""),
            level=data.get("level") or "", unit_ids=data.get("unit_ids"),
        ), status=201)


class PersonView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, user_id):
        return Response(_run_service(person_detail, actor=request.user, user_id=user_id))

    def post(self, request, user_id):
        data = _payload(request)
        if "copy_from" in data:
            return Response(_run_service(copy_unit_access, actor=request.user, user_id=user_id,
                                         from_user_id=data.get("copy_from"), unit_id=data.get("unit_id")))
        if "password" in data:
            return Response(_run_service(reset_password, actor=request.user, user_id=user_id, password=data.get("password")))
        return Response(_run_service(change_person, actor=request.user, user_id=user_id, change=data))


class TeamsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(_run_service(teams_overview, actor=request.user))

    def post(self, request):
        data = _payload(request)
        member_ids = data.get("member_ids") or []
        return Response(_run_service(create_team, actor=request.user, name=data.get("name"),
                                     description=data.get("description", ""),
                                     member_ids=member_ids if isinstance(member_ids, list) else []), status=201)


class TeamView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, team_id):
        return Response(_run_service(team_detail, actor=request.user, team_id=team_id))

    def post(self, request, team_id):
        return Response(_run_service(change_team, actor=request.user, team_id=team_id, change=_payload(request)))
