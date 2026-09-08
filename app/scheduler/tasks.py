"""Celery task wrappers. Each just runs the matching async sweep under a lock."""

from __future__ import annotations

from app.scheduler import sweeps
from app.scheduler.celery_app import celery_app
from app.scheduler.runner import run_sweep


@celery_app.task(name="app.scheduler.tasks.expire_windows")
def expire_windows() -> dict:
    return run_sweep("expire_windows", sweeps.expire_windows)


@celery_app.task(name="app.scheduler.tasks.advance_engagement")
def advance_engagement() -> dict:
    return run_sweep("advance_engagement", sweeps.advance_engagement)


@celery_app.task(name="app.scheduler.tasks.send_in_window_nudges")
def send_in_window_nudges() -> dict:
    return run_sweep("send_in_window_nudges", sweeps.send_in_window_nudges)


@celery_app.task(name="app.scheduler.tasks.run_reengagement")
def run_reengagement() -> dict:
    return run_sweep("run_reengagement", sweeps.run_reengagement)
