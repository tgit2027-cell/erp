import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("finance_allocation_lab", "0007_supplier_invoice_drafts")]
    operations = [
        migrations.CreateModel(
            name="SupplierAdjustmentDraft",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("payload", models.JSONField()),
                ("invoice_reference_snapshot", models.JSONField(null=True)),
                ("revision", models.PositiveIntegerField(default=1)),
                ("is_synthetic", models.BooleanField(default=True, editable=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("legal_entity", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="foundation.legalentity")),
                ("linked_invoice", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                                                     to="finance_allocation_lab.supplierinvoicedraft")),
            ],
            options={"default_permissions": (), "constraints": [
                models.CheckConstraint(condition=models.Q(is_synthetic=True), name="lab_supplier_adj_synthetic"),
                models.CheckConstraint(condition=models.Q(revision__gte=1), name="lab_supplier_adj_revision_positive"),
            ]},
        ),
        migrations.CreateModel(
            name="SupplierAdjustmentRevision",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("revision", models.PositiveIntegerField()),
                ("snapshot", models.JSONField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("draft", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                                           related_name="revisions", to="finance_allocation_lab.supplieradjustmentdraft")),
            ],
            options={"default_permissions": (), "constraints": [
                models.UniqueConstraint(fields=("draft", "revision"), name="lab_supplier_adj_revision_unique"),
            ]},
        ),
        migrations.RunSQL("""
            CREATE FUNCTION lab_supplier_adj_revision_immutable() RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'Synthetic supplier adjustment history is immutable';
            END;
            $$ LANGUAGE plpgsql;
            CREATE TRIGGER lab_supplier_adj_revision_guard
            BEFORE UPDATE OR DELETE ON finance_allocation_lab_supplieradjustmentrevision
            FOR EACH ROW EXECUTE FUNCTION lab_supplier_adj_revision_immutable();
        """, """
            DROP TRIGGER lab_supplier_adj_revision_guard ON finance_allocation_lab_supplieradjustmentrevision;
            DROP FUNCTION lab_supplier_adj_revision_immutable();
        """),
    ]
