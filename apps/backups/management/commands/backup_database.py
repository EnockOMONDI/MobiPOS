from django.core.management.base import BaseCommand, CommandError

from apps.backups.services import BackupOperationError, create_database_backup


class Command(BaseCommand):
    help = "Create an encrypted PostgreSQL backup and store it outside the application database."

    def handle(self, *args, **options):
        try:
            run = create_database_backup()
        except BackupOperationError as error:
            raise CommandError(str(error)) from error
        self.stdout.write(self.style.SUCCESS(f"Backup completed: {run.id}"))
