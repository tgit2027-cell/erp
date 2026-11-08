"""Delete chat messages and shared files older than the keeping period (default: 3 years, as decided by the business owner)."""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from chat.models import ChatAttachment, Message

DEFAULT_KEEP_DAYS = 1095


class Command(BaseCommand):
    help = "Delete chat messages older than --days (default 1095, about three years)."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=DEFAULT_KEEP_DAYS)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, days, dry_run, **options):
        if days < 30:
            self.stderr.write("Refusing to keep chat for less than 30 days.")
            return
        old = Message.objects.filter(created_at__lt=timezone.now() - timedelta(days=days))
        count = old.count()
        files = list(ChatAttachment.objects.filter(message__in=old).values_list("file", flat=True))
        if not dry_run:
            old.delete()
            storage = ChatAttachment._meta.get_field("file").storage
            for name in files:
                # Only after the rows are gone, so a failure never leaves a message pointing at a missing file.
                storage.delete(name)
        self.stdout.write(f"{'Would delete' if dry_run else 'Deleted'} {count} chat message(s) and {len(files)} "
                          f"shared file(s) older than {days} days.")
