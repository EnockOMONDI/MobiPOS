from celery import shared_task

from .services import refresh_subscription_lifecycle


@shared_task
def refresh_subscriptions():
    return refresh_subscription_lifecycle()
