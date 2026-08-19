import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("purchasing", "0005_purchaseorder_extracted_text_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="PurchaseReceipt",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("request_id", models.UUIDField()),
                ("quantity", models.DecimalField(decimal_places=3, max_digits=14)),
                ("damaged_quantity", models.DecimalField(decimal_places=3, default=0, max_digits=14)),
                ("line", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="receipts", to="purchasing.purchaseorderline")),
                ("organization", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="organizations.organization")),
                ("received_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="purchase_receipts", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddConstraint(
            model_name="purchasereceipt",
            constraint=models.UniqueConstraint(fields=("organization", "request_id"), name="unique_purchase_receipt_request_per_org"),
        ),
    ]
