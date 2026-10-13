from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings

engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    # Phase 1P.9R: Neon closes connections when its compute scales to zero
    # (free plan: after 5 idle minutes), and a cancelled request can leave a
    # terminated connection behind. Without a liveness check the next request
    # to check out such a connection fails. pre_ping detects it and
    # reconnects transparently, for one in-region round trip per checkout.
    pool_pre_ping=True,
)


AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
