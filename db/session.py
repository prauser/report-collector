from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config.settings import settings

engine = create_async_engine(
    settings.async_database_url,
    echo=False,
    pool_size=20,
    max_overflow=10,
    pool_timeout=60,            # 커넥션 대기 타임아웃 (기본 30s → 60s)
    pool_pre_ping=True,      # 사용 전 커넥션 유효성 검사
    pool_recycle=300,         # 5분마다 커넥션 재생성 (Railway idle timeout 대응)
    connect_args={"ssl": False},
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session
