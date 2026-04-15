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


async def _migrate_sessions_unique_constraint(conn) -> None:
    """Remplace la contrainte UNIQUE globale sur sessions.transaction_id
    par une contrainte composite UNIQUE(charger_id, transaction_id).

    Nécessaire parce que SQLite ne supporte pas ALTER TABLE DROP CONSTRAINT :
    on recrée la table avec la bonne contrainte, copie les données, et renomme.
    """
    result = await conn.execute(text(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='sessions'"
    ))
    create_sql = (result.scalar_one_or_none() or "").upper()

    # Déjà migré si la contrainte composite existe
    if "CHARGER_ID" in create_sql and "TRANSACTION_ID" in create_sql and "UNIQUE" in create_sql:
        # Vérifier que ce n'est pas juste UNIQUE(transaction_id) seul
        if "UQ_SESSION_CHARGER_TX" in create_sql or (
            create_sql.count("UNIQUE") >= 1 and "CHARGER_ID" in create_sql
        ):
            return  # migration déjà faite

    log.info("Migration DB : sessions.transaction_id → UNIQUE(charger_id, transaction_id)")
    try:
        await conn.execute(text("DROP TABLE IF EXISTS sessions_new"))
        await conn.execute(text("""
            CREATE TABLE sessions_new (
                id             INTEGER NOT NULL PRIMARY KEY,
                transaction_id INTEGER NOT NULL,
                charger_id     VARCHAR NOT NULL REFERENCES chargers(id),
                connector_id   INTEGER REFERENCES connectors(id),
                id_tag         VARCHAR NOT NULL,
                status         VARCHAR,
                start_time     DATETIME,
                stop_time      DATETIME,
                energy_wh      FLOAT DEFAULT 0.0,
                meter_start    FLOAT DEFAULT 0.0,
                meter_stop     FLOAT,
                stop_reason    VARCHAR,
                UNIQUE(charger_id, transaction_id)
            )
        """))
        # INSERT OR IGNORE pour ignorer les doublons résiduels (charger_id, tx_id)
        await conn.execute(text(
            "INSERT OR IGNORE INTO sessions_new SELECT * FROM sessions"
        ))
        await conn.execute(text("DROP TABLE sessions"))
        await conn.execute(text("ALTER TABLE sessions_new RENAME TO sessions"))
        log.info("Migration sessions terminée")
    except Exception as e:
        log.error(f"Migration sessions échouée : {e}")


async def init_db() -> None:
    """Crée toutes les tables si elles n'existent pas, et migre les colonnes manquantes."""
    async with engine.begin() as conn:
        # Migration contrainte unique sessions (doit être AVANT create_all)
        await _migrate_sessions_unique_constraint(conn)

        await conn.run_sync(Base.metadata.create_all)

        # Migration douce — ajoute les nouvelles colonnes si absentes
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
