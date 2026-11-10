"""Celery application for Django's own background work: generating the daily report.

This is not the Celery application inside Airflow's CeleryExecutor. The two share a Redis
server and nothing else: a different logical Redis database, a different queue, different worker
processes and different task code. See docs/CELERY.md.

No periodic schedule is defined here (and Celery Beat is not used): the daily cadence belongs to
Airflow, which asks for a report after each successful collection.
"""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("analytics")

# Standard Django integration: every CELERY_* setting in config/settings.py configures this app,
# and tasks are discovered from the `tasks` module of each installed app.
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
