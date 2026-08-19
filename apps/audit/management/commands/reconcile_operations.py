import json

from django.core.management.base import BaseCommand, CommandError

from apps.audit.reconciliation import run_reconciliation
from apps.organizations.models import Organization


class Command(BaseCommand):
    help = "Compare operational balances with durable evidence without changing business data."

    def add_arguments(self, parser):
        parser.add_argument("--organization", help="Limit checks to one organization slug.")
        parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
        parser.add_argument("--strict", action="store_true", help="Exit unsuccessfully when mismatches are found.")

    def handle(self, *args, **options):
        organization = None
        if options["organization"]:
            organization = Organization.objects.filter(slug=options["organization"]).first()
            if not organization:
                raise CommandError("Organization not found.")

        issues = run_reconciliation(organization=organization)
        if options["json"]:
            self.stdout.write(json.dumps({"issue_count": len(issues), "issues": [issue.as_dict() for issue in issues]}))
        elif issues:
            self.stdout.write(self.style.WARNING(f"Reconciliation found {len(issues)} issue(s)."))
            for issue in issues:
                self.stdout.write(
                    f"[{issue.area}] {issue.record_type} {issue.record_id}: {issue.message} "
                    f"expected={issue.expected or '-'} actual={issue.actual or '-'}"
                )
        else:
            self.stdout.write(self.style.SUCCESS("Reconciliation passed with no mismatches."))

        if issues and options["strict"]:
            raise CommandError(f"Reconciliation failed with {len(issues)} issue(s).")
