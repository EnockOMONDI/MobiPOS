import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("transfers", "0002_alter_stocktransfer_status_transferdiscrepancy"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="TransferTransition",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("transition", models.CharField(max_length=80)),
                ("organization", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="organizations.organization")),
                ("performed_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="transfer_transitions", to=settings.AUTH_USER_MODEL)),
                ("transfer", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="transitions", to="transfers.stocktransfer")),
            ],
        ),
        migrations.AddConstraint(
            model_name="transfertransition",
            constraint=models.UniqueConstraint(fields=("transfer", "transition"), name="unique_transition_per_transfer"),
        ),
    ]
