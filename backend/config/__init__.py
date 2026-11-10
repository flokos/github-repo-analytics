# Importing the Celery app here makes sure it is configured whenever Django starts, so that
# `@shared_task` functions bind to it in the web process as well as in the worker.
from .celery import app as celery_app

__all__ = ("celery_app",)
