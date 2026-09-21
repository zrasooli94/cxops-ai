import pytest
import pytest_asyncio

# The production module creates the asyncpg engine at import time. In tests,
# pytest-asyncio defaults to a fresh event loop per test, so a globally created
# engine hands out connections bound to the first test's loop to later tests.
# We replace the module-level engine and session maker with lazy proxies that
# defer creation until first use inside the active event loop.
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core import database as _db_module
from app.core.config import settings

_engine: AsyncEngine | None = None


def _ensure_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        # NullPool prevents asyncpg connection reuse across pytest-asyncio's
        # per-test event loops, avoiding "attached to a different loop" errors.
        _engine = create_async_engine(
            settings.database_url,
            echo=settings.debug,
            poolclass=NullPool,
        )
    return _engine


class _LazyEngineProxy:
    def __getattr__(self, name: str):
        return getattr(_ensure_engine(), name)

    def __setattr__(self, name: str, value):
        setattr(_ensure_engine(), name, value)

    def connect(self, *args, **kwargs):
        return _ensure_engine().connect(*args, **kwargs)

    def begin(self, *args, **kwargs):
        return _ensure_engine().begin(*args, **kwargs)


class _LazySessionMaker:
    _maker: async_sessionmaker | None = None

    def __call__(self) -> AsyncSession:
        if self._maker is None:
            self._maker = async_sessionmaker(
                bind=_ensure_engine(),
                class_=AsyncSession,
                expire_on_commit=False,
            )
        return self._maker()


_db_module.engine = _LazyEngineProxy()
_db_module.AsyncSessionLocal = _LazySessionMaker()


def pytest_collection_modifyitems(items):
    """Run async tests in a single session-scoped event loop so the lazy
    NullPool engine is created once and reused across the suite.
    """
    for item in items:
        if hasattr(item.obj, "__code__") and item.obj.__code__.co_flags & 0x80:
            item.add_marker(pytest.mark.asyncio(loop_scope="session"))


@pytest_asyncio.fixture(scope="session", loop_scope="session", autouse=True)
async def _dispose_test_engine():
    """Dispose the lazy engine's pool after the test session to avoid leaking
    connections bound to the session loop.
    """
    yield
    if _engine is not None:
        await _engine.dispose()
