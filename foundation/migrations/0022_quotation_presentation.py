from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("foundation", "0021_delivery_preparation_favorite")]

    operations = [
        migrations.AddField("item", "name_en", models.CharField(blank=True, max_length=240)),
        migrations.AddField("item", "name_ar", models.CharField(blank=True, max_length=240)),
        migrations.AddField("item", "description_ar", models.CharField(blank=True, max_length=240)),
        migrations.AddField("item", "category_ar", models.CharField(blank=True, max_length=80)),
        migrations.AddField("item", "image_path", models.CharField(blank=True, max_length=300)),
        migrations.AddField("syntheticcounterparty", "name_ar", models.CharField(blank=True, max_length=120)),
        migrations.AddField("syntheticcounterparty", "contact_en", models.CharField(blank=True, max_length=500)),
        migrations.AddField("syntheticcounterparty", "contact_ar", models.CharField(blank=True, max_length=500)),
        migrations.AddField("salesquote", "presentation_document", models.JSONField(default=dict, editable=False)),
    ]
