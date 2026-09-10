"""DATABASE_URL normalisation — hosts inject plain URLs; the app needs async drivers."""

import pytest

from app.config import Settings


@pytest.mark.parametrize(
    "given,expected",
    [
        # the case that broke Railway: plain postgresql:// -> async driver
        ("postgresql://u:p@host:5432/db", "postgresql+asyncpg://u:p@host:5432/db"),
        # legacy scheme (Heroku/older Railway)
        ("postgres://u:p@host:5432/db", "postgresql+asyncpg://u:p@host:5432/db"),
        # sync driver explicitly named -> swap to asyncpg
        ("postgresql+psycopg2://u:p@h/db", "postgresql+asyncpg://u:p@h/db"),
        ("postgresql+psycopg://u:p@h/db", "postgresql+asyncpg://u:p@h/db"),
        # already correct -> unchanged (idempotent)
        ("postgresql+asyncpg://u:p@h/db", "postgresql+asyncpg://u:p@h/db"),
        # sqlite convenience
        ("sqlite:///./x.db", "sqlite+aiosqlite:///./x.db"),
        ("sqlite+aiosqlite:///./x.db", "sqlite+aiosqlite:///./x.db"),
        # libpq-only query params asyncpg would choke on
        ("postgresql://u:p@h/db?sslmode=require", "postgresql+asyncpg://u:p@h/db?ssl=true"),
        (
            "postgres://u:p@h:6543/railway?sslmode=require&channel_binding=require",
            "postgresql+asyncpg://u:p@h:6543/railway?ssl=true",
        ),
        ("postgresql://u:p@h/db?sslmode=disable", "postgresql+asyncpg://u:p@h/db"),
    ],
)
def test_database_url_is_normalised(given, expected):
    assert Settings(database_url=given).database_url == expected


def test_normalisation_reaches_the_engine_factory(monkeypatch):
    import app.db.session as session_mod

    captured = {}

    def _fake_engine(url, **kw):
        captured["url"] = url
        return object()

    monkeypatch.setattr(session_mod, "create_async_engine", _fake_engine)
    monkeypatch.setattr(
        session_mod, "get_settings",
        lambda: Settings(database_url="postgres://u:p@h:5432/db"),
    )
    session_mod.get_engine.cache_clear()
    session_mod.get_engine()
    session_mod.get_engine.cache_clear()
    assert captured["url"] == "postgresql+asyncpg://u:p@h:5432/db"
