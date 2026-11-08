"""Send email alerts: approvals waiting (run every 15 minutes) and documents about to expire (run once a day)."""

from django.core.management.base import BaseCommand

from foundation.alerts import send_alerts


class Command(BaseCommand):
    help = "Email people what is waiting for their approval and which documents expire soon. Each thing is sent once."

    def add_arguments(self, parser):
        parser.add_argument("--only", choices=["approvals", "expiring", "portal"], help="Send only one kind of alert.")

    def handle(self, *args, **options):
        only = options.get("only")
        emails = send_alerts(approvals=only in (None, "approvals"), expiring=only in (None, "expiring"))
        if only in (None, "portal"):
            from foundation.portal import send_portal_emails
            emails += send_portal_emails()
        self.stdout.write(f"{len(emails)} alert emails recorded.")
