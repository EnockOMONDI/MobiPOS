from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
import sentry_sdk


class Command(BaseCommand):
    help = "Send one PII-free operational test event to the configured Sentry project."

    def add_arguments(self, parser):
        parser.add_argument(
            "--confirm",
            action="store_true",
            help="Required acknowledgement that a real Sentry event will be created.",
        )

    def handle(self, *args, **options):
        if not options["confirm"]:
            raise CommandError("Pass --confirm to create the controlled Sentry verification event.")
        if not getattr(settings, "SENTRY_REQUIRED", False) or not sentry_sdk.get_client().is_active():
            raise CommandError("Sentry is not active. Configure SENTRY_DSN in this environment first.")

        event_id = sentry_sdk.capture_message(
            "MobiPOS production monitoring verification",
            level="info",
        )
        sentry_sdk.flush(timeout=5)
        self.stdout.write(self.style.SUCCESS(f"Sentry verification event sent: {event_id}"))
