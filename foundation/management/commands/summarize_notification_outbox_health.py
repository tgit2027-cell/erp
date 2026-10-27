import json

from django.core.management.base import BaseCommand

from foundation.outbox_health import summarize_notification_outbox_health


class Command(BaseCommand):
    help = "Read-only local synthetic outbox counts/attempts; never processes or sends notifications."

    def handle(self, *args, **options):
        self.stdout.write(json.dumps(summarize_notification_outbox_health(), sort_keys=True))
