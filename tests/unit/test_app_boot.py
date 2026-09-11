"""Regression: app.main.create_app() must not crash when the demo/ static
directory doesn't exist (e.g. a deployed image built without it, or simply
DEMO_ENABLED=false) — this crash-looped the whole Railway deployment once,
because StaticFiles(directory=...) raises RuntimeError for a missing
directory and the mount used to run unconditionally at import time.
"""

from __future__ import annotations

from starlette.routing import Mount

from app.config import Settings


def _has_demo_static_mount(app) -> bool:
    return any(isinstance(r, Mount) and r.path == "/demo/static" for r in app.routes)


def test_create_app_does_not_crash_when_demo_dir_is_missing(monkeypatch, tmp_path):
    import app.main as main_mod

    missing_dir = tmp_path / "does-not-exist"
    monkeypatch.setattr(main_mod, "DEMO_STATIC_DIR", missing_dir)
    monkeypatch.setattr(main_mod, "get_settings", lambda: Settings(demo_enabled=False))

    app = main_mod.create_app()  # must not raise

    assert not _has_demo_static_mount(app)


def test_create_app_mounts_demo_static_only_when_enabled_and_present(monkeypatch, tmp_path):
    import app.main as main_mod

    present_dir = tmp_path / "demo"
    present_dir.mkdir()
    monkeypatch.setattr(main_mod, "DEMO_STATIC_DIR", present_dir)

    monkeypatch.setattr(main_mod, "get_settings", lambda: Settings(demo_enabled=False))
    assert not _has_demo_static_mount(main_mod.create_app())

    monkeypatch.setattr(main_mod, "get_settings", lambda: Settings(demo_enabled=True))
    assert _has_demo_static_mount(main_mod.create_app())
