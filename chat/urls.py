from django.urls import path

from . import views

urlpatterns = [
    path("chat/people/", views.PeopleView.as_view(), name="chat-people"),
    path("chat/unread/", views.UnreadView.as_view(), name="chat-unread"),
    path("chat/conversations/", views.ConversationsView.as_view(), name="chat-conversations"),
    path("chat/conversations/<int:conversation_id>/", views.ConversationView.as_view(), name="chat-conversation"),
    path("chat/conversations/<int:conversation_id>/messages/", views.MessagesView.as_view(), name="chat-messages"),
    path("chat/conversations/<int:conversation_id>/read/", views.ReadView.as_view(), name="chat-read"),
    path("chat/conversations/<int:conversation_id>/members/", views.MembersView.as_view(), name="chat-members"),
    path("chat/conversations/<int:conversation_id>/members/<int:user_id>/", views.MemberView.as_view(),
         name="chat-member"),
    path("chat/attachments/<int:attachment_id>/", views.AttachmentView.as_view(), name="chat-attachment"),
    path("chat/access-review/<int:access_id>/attachments/<int:attachment_id>/", views.AccessAttachmentView.as_view(),
         name="chat-access-attachment"),
    path("chat/access-review/me/", views.AccessCapabilitiesView.as_view(), name="chat-access-capabilities"),
    path("chat/access-review/", views.AccessRequestsView.as_view(), name="chat-access-requests"),
    path("chat/access-review/<int:access_id>/decision/", views.AccessDecisionView.as_view(), name="chat-access-decision"),
    path("chat/access-review/<int:access_id>/withdraw/", views.AccessWithdrawView.as_view(), name="chat-access-withdraw"),
    path("chat/access-review/<int:access_id>/log/", views.AccessLogView.as_view(), name="chat-access-log"),
    path("chat/access-review/<int:access_id>/conversations/", views.AccessConversationsView.as_view(),
         name="chat-access-conversations"),
    path("chat/access-review/<int:access_id>/conversations/<int:conversation_id>/messages/",
         views.AccessMessagesView.as_view(), name="chat-access-messages"),
]
