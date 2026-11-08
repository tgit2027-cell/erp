"""Internal chat API. Every call is limited to conversations the caller is in."""

from django.http import FileResponse
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from . import services


class ChatView(APIView):
    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        services.require_chat_user(request.user)


class PeopleView(ChatView):
    def get(self, request):
        people = services.chat_people().exclude(pk=request.user.pk)
        query = (request.query_params.get("q") or "").strip()
        if query:
            from django.db.models import Q

            for word in query.split()[:4]:
                people = people.filter(
                    Q(username__icontains=word) | Q(first_name__icontains=word) | Q(last_name__icontains=word)
                )
        return Response({"people": [services.person_row(person) for person in people[:100]]})


class ConversationsView(ChatView):
    def get(self, request):
        return Response({"conversations": services.conversation_list(request.user)})

    def post(self, request):
        kind = request.data.get("kind")
        if kind == "direct":
            conversation = services.open_direct(request.user, request.data.get("user_id"))
        elif kind == "group":
            conversation = services.create_group(request.user, request.data.get("title"), request.data.get("member_ids"))
        else:
            return Response({"detail": "kind must be direct or group."}, status=status.HTTP_400_BAD_REQUEST)
        return Response(services.conversation_detail(request.user, conversation.pk), status=status.HTTP_201_CREATED)


class ConversationView(ChatView):
    def get(self, request, conversation_id):
        return Response(services.conversation_detail(request.user, conversation_id))

    def patch(self, request, conversation_id):
        services.rename_group(request.user, conversation_id, request.data.get("title"))
        return Response(services.conversation_detail(request.user, conversation_id))


class MessagesView(ChatView):
    def get(self, request, conversation_id):
        return Response(services.messages_page(
            request.user, conversation_id,
            after=request.query_params.get("after"), before=request.query_params.get("before"),
        ))

    def post(self, request, conversation_id):
        message = services.send_message(request.user, conversation_id, request.data.get("body"),
                                        request.FILES.getlist("files"))
        return Response(services.message_row(message), status=status.HTTP_201_CREATED)


class ReadView(ChatView):
    def post(self, request, conversation_id):
        seen = services.mark_read(request.user, conversation_id, request.data.get("message_id"))
        return Response({"last_read_message_id": seen, "unread": services.unread_summary(request.user)})


class MembersView(ChatView):
    def post(self, request, conversation_id):
        services.add_members(request.user, conversation_id, request.data.get("member_ids"))
        return Response(services.conversation_detail(request.user, conversation_id))


class MemberView(ChatView):
    def delete(self, request, conversation_id, user_id):
        services.remove_member(request.user, conversation_id, user_id)
        if user_id == request.user.pk:
            return Response(status=status.HTTP_204_NO_CONTENT)
        return Response(services.conversation_detail(request.user, conversation_id))


class UnreadView(ChatView):
    def get(self, request):
        return Response(services.unread_summary(request.user))


from . import access_review  # noqa: E402


class AccessCapabilitiesView(APIView):
    def get(self, request):
        return Response(access_review.capabilities(request.user))


class AccessRequestsView(APIView):
    def get(self, request):
        rows = access_review.visible_requests(request.user)[:200]
        return Response({"requests": [access_review.request_row(row, request.user) for row in rows],
                         **access_review.capabilities(request.user)})

    def post(self, request):
        access = access_review.create_request(request.user, request.data, request)
        return Response(access_review.request_row(access, request.user), status=status.HTTP_201_CREATED)


class AccessDecisionView(APIView):
    def post(self, request, access_id):
        decision = request.data.get("decision")
        if decision not in ("approve", "reject"):
            return Response({"detail": "decision must be approve or reject."}, status=status.HTTP_400_BAD_REQUEST)
        access = access_review.decide(request.user, access_id, decision == "approve", request.data.get("note"), request)
        return Response(access_review.request_row(access, request.user))


class AccessWithdrawView(APIView):
    def post(self, request, access_id):
        access = access_review.withdraw(request.user, access_id, request)
        return Response(access_review.request_row(access, request.user))


class AccessLogView(APIView):
    def get(self, request, access_id):
        return Response({"log": access_review.access_log(request.user, access_id)})


class AccessConversationsView(APIView):
    def get(self, request, access_id):
        return Response(access_review.reviewed_conversations(request.user, access_id, request))


class AccessMessagesView(APIView):
    def get(self, request, access_id, conversation_id):
        return Response(access_review.reviewed_messages(request.user, access_id, conversation_id, request))


def file_response(attachment, request):
    from .attachments import INLINE_TYPES

    inline = request.query_params.get("inline") == "1" and attachment.content_type in INLINE_TYPES
    response = FileResponse(attachment.file.open("rb"), as_attachment=not inline,
                            filename=attachment.original_name, content_type=attachment.content_type)
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, no-store"
    response["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return response


class AttachmentView(ChatView):
    def get(self, request, attachment_id):
        return file_response(services.attachment_for_member(request.user, attachment_id), request)


class AccessAttachmentView(APIView):
    def get(self, request, access_id, attachment_id):
        return file_response(access_review.reviewed_attachment(request.user, access_id, attachment_id, request), request)
