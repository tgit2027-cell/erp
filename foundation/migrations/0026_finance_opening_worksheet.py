from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("foundation", "0025_finance_recurring_proposals")]
    operations = [
        migrations.AddField(
            model_name="financedraftjournal", name="as_of_date",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="financedraftjournal", name="kind",
            field=models.CharField(
                choices=[("journal", "Journal draft"), ("recurring_template", "Recurring template proposal"),
                         ("opening_balance", "Opening-balance worksheet proposal")],
                default="journal", max_length=24,
            ),
        ),
        migrations.RemoveConstraint(model_name="financedraftjournal", name="finance_draft_proposal_kind"),
        migrations.AddConstraint(
            model_name="financedraftjournal",
            constraint=models.CheckConstraint(
                condition=(models.Q(kind__in=["journal", "opening_balance"], recurrence_proposal={})
                           | (models.Q(kind="recurring_template") & ~models.Q(recurrence_proposal={}))),
                name="finance_draft_proposal_kind",
            ),
        ),
        migrations.AddConstraint(
            model_name="financedraftjournal",
            constraint=models.CheckConstraint(
                condition=(models.Q(kind="opening_balance", as_of_date__isnull=False)
                           | (~models.Q(kind="opening_balance") & models.Q(as_of_date__isnull=True))),
                name="finance_draft_opening_date",
            ),
        ),
    ]
