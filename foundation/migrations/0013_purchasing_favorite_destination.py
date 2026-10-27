from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("foundation", "0012_personal_report_views"),
    ]

    operations = [
        migrations.AlterField(
            model_name="userfavorite",
            name="destination",
            field=models.CharField(
                choices=[
                    ("dashboard", "Overview"),
                    ("retail", "Retail & Sales"),
                    ("procurement", "Procurement & Imports"),
                    ("inventory", "Inventory & Warehouses"),
                    ("delivery", "Delivery & After-Sales"),
                    ("finance", "Finance & Accounting"),
                    ("hr", "HR & Payroll"),
                    ("governance", "Governance & Administration"),
                    ("reports", "Reporting & Dashboards"),
                    ("purchasing", "Purchasing Workbench"),
                ],
                max_length=32,
            ),
        ),
    ]
