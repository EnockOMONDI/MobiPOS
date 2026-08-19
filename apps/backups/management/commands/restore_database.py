from django.core.management.base import BaseCommand, CommandError

from apps.backups.models import BackupRun
from apps.backups.services import BackupOperationError, restore_database_backup


class Command(BaseCommand):
    help = "Restore a backup into the isolated configured restore-drill database."

    def add_arguments(self, parser):
        parser.add_argument("backup_id")
        parser.add_argument("--confirm-project-ref", required=True)

    def handle(self, *args, **options):
        try:
            run = BackupRun.objects.get(pk=options["backup_id"])
            restore_database_backup(backup_run=run, confirm_project_ref=options["confirm_project_ref"])
        except (BackupRun.DoesNotExist, BackupOperationError) as error:
            raise CommandError(str(error)) from error
        self.stdout.write(self.style.SUCCESS(f"Restore drill completed: {run.id}"))
