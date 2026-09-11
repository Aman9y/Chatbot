from __future__ import annotations

import os

import pytest

# --- environment must be set before app modules import settings -----------
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("META_APP_SECRET", "test-app-secret")
os.environ.setdefault("META_VERIFY_TOKEN", "test-verify-token")
os.environ.setdefault("OUTREACH_REQUIRE_VERIFIED_CONSENT", "true")
os.environ.setdefault("MINOR_DEFAULT_POLICY", "pending_review")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")

# Tests are hermetic: HARD-set (not setdefault — env vars win over the .env file,
# and a developer's local .env may point at real infra) everything that would
# otherwise reach out over the network — the offline LLM + WhatsApp fakes,
# in-request conversation dispatch (no Celery/Redis), and blank provider keys.
os.environ["LLM_PROVIDER"] = "fake"
os.environ["WHATSAPP_CLIENT"] = "fake"
os.environ["WEBHOOK_CONVERSATION_DISPATCH"] = "inline"
os.environ["CONSENT_ASK_SWEEP_ENABLED"] = "false"
# a developer's local .env may have DEMO_ENABLED=true for their own demoing —
# tests must always see the shipped default regardless (they opt in per test).
os.environ["DEMO_ENABLED"] = "false"
for _k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY"):
    os.environ[_k] = ""

import fakeredis.aioredis  # noqa: E402
import httpx  # noqa: E402
import pytest_asyncio  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app import models  # noqa: E402,F401
from app.config import get_settings  # noqa: E402
from app.db.base import Base  # noqa: E402


@pytest.fixture(scope="session")
def db_url(tmp_path_factory) -> str:
    path = tmp_path_factory.mktemp("db") / "test.db"
    url = f"sqlite+aiosqlite:///{path}"
    os.environ["DATABASE_URL"] = url
    get_settings.cache_clear()
    return url


@pytest_asyncio.fixture(scope="session")
async def engine(db_url):
    eng = create_async_engine(db_url, connect_args={"check_same_thread": False})
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def session(engine):
    maker = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with maker() as s:
        yield s
    # wipe between tests
    async with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            await conn.execute(table.delete())


@pytest_asyncio.fixture
async def redis_client():
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield client
    await client.flushall()
    await client.aclose()


@pytest.fixture
def settings():
    get_settings.cache_clear()
    s = get_settings()
    yield s
    get_settings.cache_clear()


@pytest.fixture
def wa_client():
    from app.services.whatsapp.fake import FakeWhatsAppClient

    return FakeWhatsAppClient()


@pytest.fixture
def llm_client():
    from app.services.llm.fake import FakeLLMClient

    return FakeLLMClient()


@pytest.fixture(scope="session")
def knowledge_base():
    from app.services.knowledge.yaml_kb import load_knowledge_base

    return load_knowledge_base("app/knowledge/kb.yaml", strict=True)


def _build_api_client(engine, redis_client, wa_client, *, llm=None, kb=None):
    from app.api import deps
    from app.main import create_app

    application = create_app()
    maker = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

    async def _get_db():
        async with maker() as s:
            yield s

    application.dependency_overrides[deps.get_db] = _get_db
    application.state.redis = redis_client
    application.state.wa_client = wa_client
    application.state.settings = get_settings()
    if llm is not None:
        application.state.llm_client = llm
    if kb is not None:
        application.state.knowledge_base = kb
    return application


@pytest_asyncio.fixture
async def api_client(engine, redis_client, wa_client):
    """ASGI client with the conversation engine NOT wired (Phase 2 surface)."""

    application = _build_api_client(engine, redis_client, wa_client)
    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        client.app = application  # type: ignore[attr-defined]
        yield client


@pytest_asyncio.fixture
async def conversation_api_client(engine, redis_client, wa_client, llm_client, knowledge_base):
    """ASGI client WITH the conversation engine wired (Phase 3/4 surface)."""

    application = _build_api_client(
        engine, redis_client, wa_client, llm=llm_client, kb=knowledge_base
    )
    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        client.app = application  # type: ignore[attr-defined]
        yield client
