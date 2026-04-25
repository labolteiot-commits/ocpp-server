# db/models.py
from datetime import datetime
from enum import Enum as PyEnum
from sqlalchemy import (
    Column, String, Integer, Float, Boolean,
    DateTime, ForeignKey, Enum, Text, JSON, UniqueConstraint
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


# ─── Énumérations ────────────────────────────────────────

class ChargerStatus(str, PyEnum):
    AVAILABLE      = "Available"
    PREPARING      = "Preparing"
    CHARGING       = "Charging"
    SUSPENDED_EV   = "SuspendedEV"
    SUSPENDED_EVSE = "SuspendedEVSE"
    FINISHING      = "Finishing"
    RESERVED       = "Reserved"
    UNAVAILABLE    = "Unavailable"
    FAULTED        = "Faulted"
    OFFLINE        = "Offline"

class ConnectorStatus(str, PyEnum):
    AVAILABLE      = "Available"
    PREPARING      = "Preparing"
    CHARGING       = "Charging"
    FINISHING      = "Finishing"
    RESERVED       = "Reserved"
    UNAVAILABLE    = "Unavailable"
    FAULTED        = "Faulted"

class SessionStatus(str, PyEnum):
    ACTIVE    = "Active"
    COMPLETED = "Completed"
    FAULTED   = "Faulted"


# ─── Borne de recharge ───────────────────────────────────

class Charger(Base):
    __tablename__ = "chargers"

    id               = Column(String,  primary_key=True)
    description      = Column(String,  nullable=True)
    manufacturer     = Column(String,  nullable=True)
    model            = Column(String,  nullable=True)
    serial_number    = Column(String,  nullable=True)
    firmware_version = Column(String,  nullable=True)
    ocpp_protocol    = Column(String,  default="ocpp1.6")
    status           = Column(Enum(ChargerStatus), default=ChargerStatus.OFFLINE)
    last_heartbeat   = Column(DateTime, nullable=True)
    ip_address       = Column(String,  nullable=True)
    is_enabled       = Column(Boolean, default=True)
    auth_password    = Column(String,  nullable=True)
    notes            = Column(Text,    nullable=True)

    # Comportement au boot
    boot_lock        = Column(Boolean, default=True)   # verrouiller au démarrage
    default_max_amps = Column(Float,   nullable=True)  # courant imposé (A)

    # Quirks par fabricant — None = valeur par défaut calculée automatiquement
    remote_start_delay = Column(Float,  nullable=True)  # délai (s) entre ChangeAvailability et RemoteStart
    local_id_tag       = Column(String, nullable=True)  # idTag pour RemoteStart et liste locale OCPP

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    connectors = relationship("Connector", back_populates="charger", cascade="all, delete")
    sessions   = relationship("Session",   back_populates="charger")
    events     = relationship("Event",     back_populates="charger")


# ─── Connecteur ──────────────────────────────────────────

class Connector(Base):
    __tablename__ = "connectors"

    id           = Column(Integer, primary_key=True, autoincrement=True)
    charger_id   = Column(String,  ForeignKey("chargers.id"), nullable=False)
    connector_id = Column(Integer, nullable=False)
    status       = Column(Enum(ConnectorStatus), default=ConnectorStatus.UNAVAILABLE)
    error_code   = Column(String,  nullable=True)
    updated_at   = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    charger  = relationship("Charger",   back_populates="connectors")
    sessions = relationship("Session",   back_populates="connector")


# ─── Session de charge ───────────────────────────────────

class Session(Base):
    __tablename__ = "sessions"
    # transaction_id est unique PAR borne, pas globalement.
    # Chaque borne repart à 1 après un reboot → collision entre bornes sans cette contrainte composite.
    __table_args__ = (
        UniqueConstraint('charger_id', 'transaction_id', name='uq_session_charger_tx'),
    )

    id             = Column(Integer, primary_key=True, autoincrement=True)
    transaction_id = Column(Integer, nullable=False)           # unique PAR charger_id (voir __table_args__)
    charger_id     = Column(String,  ForeignKey("chargers.id"), nullable=False)
    connector_id   = Column(Integer, ForeignKey("connectors.id"), nullable=False)
    id_tag         = Column(String,  nullable=False)
    status         = Column(Enum(SessionStatus), default=SessionStatus.ACTIVE)
    start_time     = Column(DateTime, default=datetime.utcnow)
    stop_time      = Column(DateTime, nullable=True)
    energy_wh      = Column(Float,   default=0.0)
    meter_start    = Column(Float,   default=0.0)
    meter_stop     = Column(Float,   nullable=True)
    stop_reason    = Column(String,  nullable=True)

    charger      = relationship("Charger",    back_populates="sessions")
    connector    = relationship("Connector",  back_populates="sessions")
    meter_values = relationship("MeterValue", back_populates="session")


# ─── MeterValues ─────────────────────────────────────────

class MeterValue(Base):
    __tablename__ = "meter_values"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    session_id  = Column(Integer, ForeignKey("sessions.id"), nullable=True)  # nullable: hors transaction
    timestamp   = Column(DateTime, default=datetime.utcnow)
    energy_wh   = Column(Float,  nullable=True)
    power_w     = Column(Float,  nullable=True)
    current_a   = Column(Float,  nullable=True)
    voltage_v   = Column(Float,  nullable=True)
    soc_percent = Column(Float,  nullable=True)
    raw         = Column(JSON,   nullable=True)

    session = relationship("Session", back_populates="meter_values")


# ─── Événements ──────────────────────────────────────────

class Event(Base):
    __tablename__ = "events"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    charger_id = Column(String,  ForeignKey("chargers.id"), nullable=False)
    timestamp  = Column(DateTime, default=datetime.utcnow)
    type       = Column(String,  nullable=False)
    payload    = Column(JSON,    nullable=True)
    notes      = Column(Text,    nullable=True)

    charger = relationship("Charger", back_populates="events")
