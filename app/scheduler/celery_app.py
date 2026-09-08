"""Celery application + beat schedule for the scheduler sweeps."""

from __future__ import annotations

from celery import Celery

from app.config import get_settings

_settings = get_settings()

celery_app = Celery(
    "leadbot",
    broker=_settings.broker_url,
    backend=_settings.result_backend,
    include=["app.scheduler.tasks", "app.scheduler.conversation_tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    result_expires=3600,
    worker_max_tasks_per_child=200,
    broker_connection_retry_on_startup=True,
)

if _settings.scheduler_enabled:
    _iv = _settings.sweep_interval_seconds
    celery_app.conf.beat_schedule = {
        "send-consent-asks": {
            "task": "app.scheduler.tasks.send_consent_asks",
            "schedule": float(_iv),
        },
        "expire-windows": {
            "task": "app.scheduler.tasks.expire_windows",
            "schedule": float(_iv),
        },
        "advance-engagement": {
            "task": "app.scheduler.tasks.advance_engagement",
            "schedule": float(_iv),
        },
        "send-in-window-nudges": {
            "task": "app.scheduler.tasks.send_in_window_nudges",
            "schedule": float(_iv),
        },
        "run-reengagement": {
            "task": "app.scheduler.tasks.run_reengagement",
            "schedule": float(_iv),
        },
    }
