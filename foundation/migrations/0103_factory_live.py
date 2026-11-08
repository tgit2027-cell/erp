"""Switch the Factory section on for the live test (Sam's go, 7 Nov 2026).

Every company gets its factory settings with the switch on, so everyone with a factory permission sees the
Factory section. The Master Admin can still hide it again in Factory setup ("Hide it again").
"""

from django.db import migrations


def switch_on(apps, schema_editor):
    LegalEntity = apps.get_model("foundation", "LegalEntity")
    FactorySettings = apps.get_model("foundation", "FactorySettings")
    for entity in LegalEntity.objects.all():
        found, _ = FactorySettings.objects.get_or_create(legal_entity=entity)
        if not found.is_live:
            found.is_live = True
            found.save(update_fields=["is_live"])


class Migration(migrations.Migration):
    dependencies = [("foundation", "0102_factory_planner")]

    operations = [migrations.RunPython(switch_on, migrations.RunPython.noop)]
