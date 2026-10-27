from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("foundation", "0026_finance_opening_worksheet")]
    operations = [
        migrations.AddField(
            model_name="financedraftjournal", name="adjustment_proposal",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AlterField(
            model_name="financedraftjournal", name="kind",
            field=models.CharField(
                choices=[("journal", "Journal draft"), ("recurring_template", "Recurring template proposal"),
                         ("opening_balance", "Opening-balance worksheet proposal"),
                         ("accrual_adjustment", "Accrual adjustment proposal"),
                         ("prepayment_adjustment", "Prepayment adjustment proposal")],
                default="journal", max_length=24,
            ),
        ),
        migrations.RemoveConstraint(model_name="financedraftjournal", name="finance_draft_proposal_kind"),
        migrations.AddConstraint(
            model_name="financedraftjournal",
            constraint=models.CheckConstraint(
                condition=(models.Q(kind__in=["journal", "opening_balance", "accrual_adjustment", "prepayment_adjustment"], recurrence_proposal={})
                           | (models.Q(kind="recurring_template") & ~models.Q(recurrence_proposal={}))),
                name="finance_draft_proposal_kind",
            ),
        ),
        migrations.AddConstraint(
            model_name="financedraftjournal",
            constraint=models.CheckConstraint(
                condition=((models.Q(kind__in=["accrual_adjustment", "prepayment_adjustment"]) & ~models.Q(adjustment_proposal={}))
                           | (~models.Q(kind__in=["accrual_adjustment", "prepayment_adjustment"]) & models.Q(adjustment_proposal={}))),
                name="finance_draft_adjustment_metadata",
            ),
        ),
    ]
