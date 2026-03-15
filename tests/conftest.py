import os
os.environ.setdefault("DATABASE_URL", "postgresql://rcuser:rcpassword@127.0.0.1/report_collector")

import asyncio
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config.settings import settings


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
async def engine():
    e = create_async_engine(settings.async_database_url, echo=False)
    yield e
    await e.dispose()


@pytest.fixture(scope="session")
async def session_factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
