from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("payments", "0004_payment_unique_payment_provider_reference_per_org_method"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="payment",
            name="verification_source",
            field=models.CharField(
                blank=True,
                choices=[
                    ("manual", "Cashier entry"),
                    ("provider", "Provider callback"),
                    ("reconciliation", "Reconciliation"),
                ],
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="payment",
            name="verified_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="payment",
            name="verified_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="verified_payments",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
