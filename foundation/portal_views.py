"""The HR Portal for all staff: posts, reading confirmations, files and the suggestions box.

While the portal is switched off (ERP_HR_PORTAL is not "on") every address here answers "not found".
"""

from django.http import HttpResponse
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .portal import (
    act_on_post, answer_suggestion, create_post, is_enabled, photo_file, portal_overview, post_detail, post_file, send_suggestion,
    suggestions_overview,
)
from .purchasing_views import _payload, _run_service as _run


class SwitchedView(APIView):
    permission_classes = [IsAuthenticated]

    def initial(self, request, *args, **kwargs):
        if not is_enabled():
            raise NotFound("The HR Portal is not switched on yet.")
        return super().initial(request, *args, **kwargs)


class PortalPostListView(SwitchedView):
    def get(self, request):
        return Response(_run(portal_overview, actor=request.user, section=request.query_params.get("section", "home")))

    def post(self, request):
        return Response(_run(create_post, actor=request.user, data=_payload(request)), status=201)


class PortalPostView(SwitchedView):
    def get(self, request, post_id):
        return Response(_run(post_detail, actor=request.user, post_id=post_id))

    def post(self, request, post_id):
        data = _payload(request)
        return Response(_run(act_on_post, actor=request.user, post_id=post_id, action=data.get("action"),
                             reason=data.get("reason", ""), photo=data.get("photo"), photo_id=data.get("photo_id")))


def _file_response(name, content_type, data):
    response = HttpResponse(data, content_type=content_type)
    disposition = "inline" if content_type.startswith("image/") else "attachment"
    response["Content-Disposition"] = f'{disposition}; filename="{name.replace(chr(34), "")}"'
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, no-store"
    return response


class PortalPhotoView(SwitchedView):
    def get(self, request, post_id, photo_id):
        return _file_response(*_run(photo_file, actor=request.user, post_id=post_id, photo_id=photo_id))


class PortalPostFileView(SwitchedView):
    def get(self, request, post_id):
        return _file_response(*_run(post_file, actor=request.user, post_id=post_id))


class SuggestionListView(SwitchedView):
    def get(self, request):
        return Response(_run(suggestions_overview, actor=request.user))

    def post(self, request):
        return Response(_run(send_suggestion, actor=request.user, data=_payload(request)), status=201)


class SuggestionView(SwitchedView):
    def post(self, request, suggestion_id):
        data = _payload(request)
        return Response(_run(answer_suggestion, actor=request.user, suggestion_id=suggestion_id, action=data.get("action"),
                             reply=data.get("reply", "")))


class PortalSignInView(SwitchedView):
    """HR, from a staff record: give the person their own portal sign-in, or reset its temporary password."""

    def post(self, request, employee_id):
        from .access import resolve_unit
        from .portal import give_portal_sign_in, reset_portal_password

        data = _payload(request)
        common = {"actor": request.user, "business_unit": resolve_unit(request), "employee_id": employee_id,
                  "password": data.get("password", "")}
        if data.get("action") == "reset":
            return Response(_run(reset_portal_password, **common))
        return Response(_run(give_portal_sign_in, **common, username=data.get("username", "")), status=201)


class MyPasswordView(SwitchedView):
    def post(self, request):
        from django.contrib.auth import update_session_auth_hash

        from .portal import change_my_password

        data = _payload(request)
        person = _run(change_my_password, actor=request.user, current=data.get("current", ""), new=data.get("new", ""))
        update_session_auth_hash(request, person)  # stay signed in on this device
        return Response({"done": True})
