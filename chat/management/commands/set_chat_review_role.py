"""Give or remove the chat review roles by name (they are never implied by being an administrator)."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.management.base import BaseCommand, CommandError

from audit.models import AuditEvent
from audit.recording import record

ROLES = {"requester": "request_chat_access", "approver": "approve_chat_access"}


class Command(BaseCommand):
    help = "Give (or with --remove, take away) a chat review role: requester or approver."

    def add_arguments(self, parser):
        parser.add_argument("--username", required=True)
        parser.add_argument("--role", required=True, choices=sorted(ROLES))
        parser.add_argument("--remove", action="store_true")

    def handle(self, *args, username, role, remove, **options):
        try:
            user = get_user_model().objects.get(username=username)
        except get_user_model().DoesNotExist:
            raise CommandError(f"No account named {username}.") from None
        permission = Permission.objects.get(content_type__app_label="chat", codename=ROLES[role])
        if remove:
            user.user_permissions.remove(permission)
        else:
            user.user_permissions.add(permission)
        record(AuditEvent.Kind.ACCOUNT_CHANGED, user=user, detail={
            "chat_review_role": role, "change": "removed" if remove else "given",
        })
        self.stdout.write(f"{'Removed' if remove else 'Gave'} chat review {role} role {'from' if remove else 'to'} {username}.")
