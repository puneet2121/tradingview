from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("dashboard", "0008_import_trade_activity")]

    operations = [
        migrations.CreateModel(
            name="PineIndicator",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=80, unique=True)),
                ("source", models.TextField()),
                ("overlay", models.BooleanField(default=True)),
                ("revision", models.PositiveIntegerField(default=1)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["name"]},
        ),
    ]
