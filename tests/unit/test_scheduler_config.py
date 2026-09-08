from app.config import Settings


def test_celery_app_and_beat_schedule():
    from app.scheduler.celery_app import celery_app

    assert celery_app.main == "leadbot"
    tasks = set(celery_app.conf.beat_schedule)
    assert tasks == {
        "send-consent-asks",
        "expire-windows",
        "advance-engagement",
        "send-in-window-nudges",
        "run-reengagement",
    }


def test_beat_schedule_empty_when_disabled(monkeypatch):
    import importlib

    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    from app import config

    config.get_settings.cache_clear()
    import app.scheduler.celery_app as mod

    importlib.reload(mod)
    assert not mod.celery_app.conf.beat_schedule
    config.get_settings.cache_clear()
    importlib.reload(mod)


def test_broker_defaults_to_redis_url():
    s = Settings(redis_url="redis://x:6379/2", celery_broker_url="")
    assert s.broker_url == "redis://x:6379/2"
    assert s.result_backend == "redis://x:6379/2"


def test_reengagement_spacing_parsing():
    s = Settings(reengagement_spacing_hours="10, 24 ,72")
    assert s.reengagement_spacing == [10.0, 24.0, 72.0]
    assert s.reengagement_delay_hours(0) == 10.0
    assert s.reengagement_delay_hours(5) == 72.0  # clamps to last


def test_all_tasks_registered():
    import app.scheduler.tasks  # noqa: F401
    from app.scheduler.celery_app import celery_app

    for name in (
        "app.scheduler.tasks.send_consent_asks",
        "app.scheduler.tasks.expire_windows",
        "app.scheduler.tasks.advance_engagement",
        "app.scheduler.tasks.send_in_window_nudges",
        "app.scheduler.tasks.run_reengagement",
    ):
        assert name in celery_app.tasks
