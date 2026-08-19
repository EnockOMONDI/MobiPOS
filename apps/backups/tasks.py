from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from .models import BackupRun, BackupStatus
from .services import create_database_backup, delete_backup


@shared_task
def run_scheduled_backup():
    if not settings.BACKUP_ENABLED:
        return "disabled"
    return str(create_database_backup().id)


@shared_task
def apply_backup_retention():
    if not settings.BACKUP_ENABLED:
        return 0
    cutoff = timezone.now() - timedelta(days=settings.BACKUP_RETENTION_DAYS)
    runs = BackupRun.objects.filter(
        created_at__lt=cutoff,
        status__in=[BackupStatus.COMPLETED, BackupStatus.RESTORED],
    )
    count = 0
    for run in runs.iterator():
        delete_backup(run)
        count += 1
    return count
