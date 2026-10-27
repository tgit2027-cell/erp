import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("finance_allocation_lab", "0001_initial")]
    operations = [
        migrations.CreateModel(
            name="BudgetScenario",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("payload", models.JSONField()),
                ("revision", models.PositiveIntegerField(default=1)),
                ("is_synthetic", models.BooleanField(default=True, editable=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("legal_entity", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="foundation.legalentity")),
            ],
            options={"default_permissions": (), "constraints": [
                models.CheckConstraint(condition=models.Q(is_synthetic=True), name="lab_budget_synthetic"),
                models.CheckConstraint(condition=models.Q(revision__gte=1), name="lab_budget_revision_positive"),
            ]},
        ),
        migrations.CreateModel(
            name="BudgetRevision",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("revision", models.PositiveIntegerField()),
                ("snapshot", models.JSONField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("scenario", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                                               related_name="revisions", to="finance_allocation_lab.budgetscenario")),
            ],
            options={"default_permissions": (), "constraints": [
                models.UniqueConstraint(fields=("scenario", "revision"), name="lab_budget_revision_unique"),
            ]},
        ),
        migrations.RunSQL("""
            CREATE FUNCTION lab_budget_revision_immutable() RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'Synthetic budget revision history is immutable';
            END;
            $$ LANGUAGE plpgsql;
            CREATE TRIGGER lab_budget_revision_guard
            BEFORE UPDATE OR DELETE ON finance_allocation_lab_budgetrevision
            FOR EACH ROW EXECUTE FUNCTION lab_budget_revision_immutable();
        """, """
            DROP TRIGGER lab_budget_revision_guard ON finance_allocation_lab_budgetrevision;
            DROP FUNCTION lab_budget_revision_immutable();
        """),
    ]
