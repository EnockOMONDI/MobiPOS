from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.notifications.emailing import send_daily_owner_activity_summary
from apps.organizations.models import Organization, OrganizationStatus


class Command(BaseCommand):
    help = "Send daily owner activity summary emails for active organizations."

    def add_arguments(self, parser):
        parser.add_argument("--organization", help="Organization slug to send for one tenant only.")
        parser.add_argument("--hours", type=int, default=24, help="Activity window in hours. Defaults to 24.")

    def handle(self, *args, **options):
        end = timezone.now()
        start = end - timedelta(hours=options["hours"])
        queryset = Organization.objects.filter(status=OrganizationStatus.ACTIVE).order_by("name")
        if options.get("organization"):
            queryset = queryset.filter(slug=options["organization"])

        total_sent = 0
        total_organizations = 0
        for organization in queryset:
            total_organizations += 1
            result = send_daily_owner_activity_summary(organization=organization, start=start, end=end)
            total_sent += result.sent_count
            self.stdout.write(
                f"{organization.slug}: sent {result.sent_count} email(s) to {len(result.recipients)} owner recipient(s)"
            )

        self.stdout.write(self.style.SUCCESS(f"Processed {total_organizations} organization(s); sent {total_sent} email(s)."))
