"""Re-plan every business unit's factory schedule for today (for a morning scheduled job).

The planner also re-plans by itself the first time anyone opens the schedule each day, so this is optional.
"""

from django.core.management.base import BaseCommand

from foundation.factory_autoplan import replan_if_due
from foundation.models import BusinessUnit


class Command(BaseCommand):
    help = "Re-plan the factory schedule of every business unit that has planned jobs, once a day."

    def handle(self, *args, **options):
        for unit in BusinessUnit.objects.all():
            run = replan_if_due(unit)
            if run is not None:
                self.stdout.write(f"{unit.code}: {run.changes} stage(s) planned or moved.")
