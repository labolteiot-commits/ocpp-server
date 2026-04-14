# db/database.py
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import text
from db.models import Base
from config import settings
from core.logging import log


engine = create_async_engine(
    settings.database.url,
    echo=settings.database.echo,
    connect_args={"check_same_thread": False},  # SQLite uniquement
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def init_db() -> None:
    """Crée toutes les tables si elles n'existent pas."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    log.info("Base de données initialisée")


async def get_db() -> AsyncSession:
    """Dependency FastAPI — fournit une session DB par requête."""
    async with AsyncSessionLocal() as session:
        yield session
