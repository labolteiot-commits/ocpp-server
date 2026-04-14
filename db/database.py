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
    """Crée toutes les tables si elles n'existent pas, et migre les colonnes manquantes."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        # Migration douce — ajoute les nouvelles colonnes si absentes (SQLite ne supporte pas IF NOT EXISTS sur ALTER)
        _new_charger_columns = [
            ("remote_start_delay", "REAL"),
            ("local_id_tag",       "VARCHAR"),
        ]
        for col_name, col_type in _new_charger_columns:
            try:
                await conn.execute(text(f"ALTER TABLE chargers ADD COLUMN {col_name} {col_type}"))
                log.info(f"Migration DB : colonne chargers.{col_name} ajoutée")
            except Exception:
                pass  # La colonne existe déjà

    log.info("Base de données initialisée")


async def get_db() -> AsyncSession:
    """Dependency FastAPI — fournit une session DB par requête."""
    async with AsyncSessionLocal() as session:
        yield session
