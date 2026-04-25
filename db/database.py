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

    Détection fiable : cherche l'index nommé 'uq_session_charger_tx' dans sqlite_master.
    L'ancienne approche (parser le SQL CREATE TABLE) donnait de faux positifs parce que
    charger_id et UNIQUE apparaissent déjà dans l'ancienne définition de table.
    """
    # Vérification fiable : l'index composite a-t-il déjà été créé ?
    result = await conn.execute(text(
        "SELECT COUNT(*) FROM sqlite_master "
        "WHERE type='index' AND name='uq_session_charger_tx'"
    ))
    if result.scalar() > 0:
        return  # Déjà migré

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
                CONSTRAINT uq_session_charger_tx UNIQUE(charger_id, transaction_id)
            )
        """))
        # INSERT OR IGNORE : ignore les doublons (charger_id, tx_id) résiduels
        await conn.execute(text(
            "INSERT OR IGNORE INTO sessions_new SELECT * FROM sessions"
        ))
        await conn.execute(text("DROP TABLE sessions"))
        await conn.execute(text("ALTER TABLE sessions_new RENAME TO sessions"))
        log.info("Migration sessions terminée — contrainte composite en place")
    except Exception as e:
        log.error(f"Migration sessions échouée : {e}")


async def init_db() -> None:
    """Crée toutes les tables si elles n'existent pas, et migre les colonnes manquantes."""
    async with engine.begin() as conn:
        # Migration contrainte unique sessions (doit être AVANT create_all)
        await _migrate_sessions_unique_constraint(conn)

        # Crée toutes les tables (Vehicle, OBD2Reading, ChargingPlan si absentes)
        await conn.run_sync(Base.metadata.create_all)

        # Migration douce — ajoute les nouvelles colonnes si absentes
        _new_charger_columns = [
            ("remote_start_delay", "REAL"),
            ("local_id_tag",       "VARCHAR"),
            # Sprint 25 A2 : override heartbeat_interval par borne
            ("heartbeat_interval", "INTEGER"),
        ]
        for col_name, col_type in _new_charger_columns:
            try:
                await conn.execute(text(f"ALTER TABLE chargers ADD COLUMN {col_name} {col_type}"))
                log.info(f"Migration DB : colonne chargers.{col_name} ajoutée")
            except Exception:
                pass  # La colonne existe déjà

        # Sprint 24 A10 : colonne expired_at sur charging_profile_snapshots
        _new_snapshot_columns = [
            ("expired_at", "DATETIME"),
        ]
        for col_name, col_type in _new_snapshot_columns:
            try:
                await conn.execute(text(
                    f"ALTER TABLE charging_profile_snapshots ADD COLUMN {col_name} {col_type}"
                ))
                log.info(f"Migration DB : colonne charging_profile_snapshots.{col_name} ajoutée")
            except Exception:
                pass

        # Sprint 31 Volet B : colonne supported_profiles sur chargers
        # (Core,FirmwareManagement,LocalAuthListManagement,Reservation,SmartCharging,RemoteTrigger)
        _new_charger_cols_s31 = [
            ("supported_profiles", "VARCHAR(200)"),
        ]
        for col_name, col_type in _new_charger_cols_s31:
            try:
                await conn.execute(text(
                    f"ALTER TABLE chargers ADD COLUMN {col_name} {col_type}"
                ))
                log.info(f"Migration DB : colonne chargers.{col_name} ajoutée (S31)")
            except Exception:
                pass

    log.info("Base de données initialisée")

    # Sprint 32 Point 2 — seed des docs FR messages OCPP (idempotent)
    try:
        from core.seed_message_docs import seed_message_docs
        n = await seed_message_docs()
        if n:
            log.info("[S32-msgdocs] Documentation OCPP seedée", count=n)
    except Exception as e:
        log.warning("[S32-msgdocs] seed_message_docs échoué au boot", error=str(e))


async def purge_expired_snapshots(days: int = 90) -> int:
    """Sprint 24 A10 — supprime les snapshots expirés depuis plus de N jours.

    Appelée au démarrage du serveur OCPP. Conserve les snapshots sans expired_at
    (actifs) et ceux expirés récemment (audit trail court).
    """
    from datetime import datetime, timezone, timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    async with AsyncSessionLocal() as db:
        r = await db.execute(text(
            "DELETE FROM charging_profile_snapshots "
            "WHERE expired_at IS NOT NULL AND expired_at < :cutoff"
        ), {"cutoff": cutoff})
        await db.commit()
        deleted = r.rowcount or 0
        if deleted:
            log.info("Snapshots expirés purgés", count=deleted, cutoff_days=days)
        return deleted


async def expire_stale_reservations() -> int:
    """Sprint 28 A7 — marque EXPIRED les réservations ACTIVE dont expiry_date < now.

    Appelée périodiquement (tous les 60s) par la boucle de fond du serveur,
    et une fois au boot. Non-destructif : garde la ligne pour audit.
    """
    from datetime import datetime, timezone
    from db.models import Reservation, ReservationStatus
    from sqlalchemy import update as sa_update
    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as db:
        r = await db.execute(
            sa_update(Reservation)
            .where(
                Reservation.status == ReservationStatus.ACTIVE,
                Reservation.expiry_date < now,
            )
            .values(status=ReservationStatus.EXPIRED, updated_at=now)
        )
        await db.commit()
        expired = r.rowcount or 0
        if expired:
            log.info("Réservations expirées (auto)", count=expired)
        return expired


async def get_db() -> AsyncSession:
    """Dependency FastAPI — fournit une session DB par requête."""
    async with AsyncSessionLocal() as session:
        yield session
