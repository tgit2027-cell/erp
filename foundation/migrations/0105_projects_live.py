"""Switch the Projects & Fit-Out section on for the live test (Sam's go, 7 Nov 2026).

Every company gets its project settings with the switch on, so everyone with a project permission sees
Projects & Fit-Out. The Master Admin can still hide it again in Project setup ("Hide it again").
"""

from django.db import migrations


def switch_on(apps, schema_editor):
    LegalEntity = apps.get_model("foundation", "LegalEntity")
    ProjectSettings = apps.get_model("foundation", "ProjectSettings")
    for entity in LegalEntity.objects.all():
        found, _ = ProjectSettings.objects.get_or_create(legal_entity=entity)
        if not found.is_live:
            found.is_live = True
            found.save(update_fields=["is_live"])


class Migration(migrations.Migration):
    dependencies = [("foundation", "0104_projects")]

    operations = [migrations.RunPython(switch_on, migrations.RunPython.noop)]
