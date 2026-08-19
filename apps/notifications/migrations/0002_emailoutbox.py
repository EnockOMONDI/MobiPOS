import uuid
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("notifications", "0001_initial"), ("organizations", "0009_agentprofile_verification_notes")]
    operations = [
        migrations.CreateModel(
            name="EmailOutbox",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("recipient", models.EmailField(db_index=True, max_length=254)),
                ("subject", models.CharField(max_length=255)),
                ("text_body", models.TextField(blank=True)),
                ("html_body", models.TextField(blank=True)),
                ("from_email", models.CharField(max_length=255)),
                ("status", models.CharField(choices=[("pending", "Pending"), ("processing", "Processing"), ("sent", "Sent"), ("failed", "Failed"), ("dead", "Dead")], db_index=True, default="pending", max_length=20)),
                ("attempts", models.PositiveSmallIntegerField(default=0)),
                ("next_attempt_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("provider_message_id", models.CharField(blank=True, max_length=255)),
                ("last_error", models.TextField(blank=True)),
                ("sent_at", models.DateTimeField(blank=True, null=True)),
                ("content_redacted_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("organization", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="email_deliveries", to="organizations.organization")),
            ],
            options={"ordering": ("-created_at",)},
        ),
        migrations.AddIndex(model_name="emailoutbox", index=models.Index(fields=["status", "next_attempt_at"], name="email_retry_due_idx")),
    ]
