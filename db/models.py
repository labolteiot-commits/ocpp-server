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


class ChargerStatus(str, PyEnum):
    AVAILABLE="Available"; PREPARING="Preparing"; CHARGING="Charging"
    SUSPENDED_EV="SuspendedEV"; SUSPENDED_EVSE="SuspendedEVSE"
    FINISHING="Finishing"; RESERVED="Reserved"; UNAVAILABLE="Unavailable"
    FAULTED="Faulted"; OFFLINE="Offline"

class ConnectorStatus(str, PyEnum):
    AVAILABLE="Available"; PREPARING="Preparing"; CHARGING="Charging"
    FINISHING="Finishing"; RESERVED="Reserved"; UNAVAILABLE="Unavailable"; FAULTED="Faulted"

class SessionStatus(str, PyEnum):
    ACTIVE="Active"; COMPLETED="Completed"; FAULTED="Faulted"


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
    boot_lock        = Column(Boolean, default=True)
    default_max_amps = Column(Float,   nullable=True)
    remote_start_delay = Column(Float,  nullable=True)
    local_id_tag       = Column(String, nullable=True)
    # Sprint 25 A2 : override heartbeat_interval par borne (NULL = fallback profile.heartbeat_interval)
    heartbeat_interval = Column(Integer, nullable=True)
    # Sprint 31 Volet B : liste CSV des SupportedFeatureProfiles (Core,FirmwareManagement,...)
    supported_profiles = Column(String(200), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    connectors                 = relationship("Connector",               back_populates="charger", cascade="all, delete")
    sessions                   = relationship("Session",                 back_populates="charger")
    events                     = relationship("Event",                   back_populates="charger")
    charging_profile_snapshots = relationship("ChargingProfileSnapshot", back_populates="charger", cascade="all, delete")


class Connector(Base):
    __tablename__ = "connectors"
    id           = Column(Integer, primary_key=True, autoincrement=True)
    charger_id   = Column(String,  ForeignKey("chargers.id"), nullable=False)
    connector_id = Column(Integer, nullable=False)
    status       = Column(Enum(ConnectorStatus), default=ConnectorStatus.UNAVAILABLE)
    error_code   = Column(String,  nullable=True)
    updated_at   = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    charger  = relationship("Charger",  back_populates="connectors")
    sessions = relationship("Session",  back_populates="connector")


class Session(Base):
    __tablename__ = "sessions"
    __table_args__ = (UniqueConstraint("charger_id","transaction_id",name="uq_session_charger_tx"),)
    id             = Column(Integer, primary_key=True, autoincrement=True)
    transaction_id = Column(Integer, nullable=False)
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


class MeterValue(Base):
    __tablename__ = "meter_values"
    id          = Column(Integer, primary_key=True, autoincrement=True)
    session_id  = Column(Integer, ForeignKey("sessions.id"), nullable=True)
    charger_id  = Column(String, ForeignKey("chargers.id"), nullable=True, index=True)
    timestamp   = Column(DateTime, default=datetime.utcnow)
    energy_wh   = Column(Float,  nullable=True)
    power_w     = Column(Float,  nullable=True)
    current_a   = Column(Float,  nullable=True)
    voltage_v   = Column(Float,  nullable=True)
    soc_percent = Column(Float,  nullable=True)
    raw         = Column(JSON,   nullable=True)
    session = relationship("Session", back_populates="meter_values")


class Event(Base):
    __tablename__ = "events"
    id         = Column(Integer, primary_key=True, autoincrement=True)
    charger_id = Column(String,  ForeignKey("chargers.id"), nullable=False)
    timestamp  = Column(DateTime, default=datetime.utcnow)
    type       = Column(String,  nullable=False)
    payload    = Column(JSON,    nullable=True)
    notes      = Column(Text,    nullable=True)
    charger = relationship("Charger", back_populates="events")


class Vehicle(Base):
    __tablename__ = "vehicles"
    id                = Column(String,  primary_key=True)
    label             = Column(String,  nullable=True)
    charger_id        = Column(String,  ForeignKey("chargers.id"), nullable=True)
    battery_kwh       = Column(Float,   default=60.0)
    max_charge_amps   = Column(Float,   default=32.0)
    target_soc_pct    = Column(Float,   default=90.0)
    charge_efficiency = Column(Float,   default=0.92)
    notes             = Column(Text,    nullable=True)
    created_at        = Column(DateTime, default=datetime.utcnow)
    readings = relationship("OBD2Reading",  back_populates="vehicle", cascade="all, delete")
    plans    = relationship("ChargingPlan", back_populates="vehicle")


class OBD2Reading(Base):
    __tablename__ = "obd2_readings"
    id          = Column(Integer, primary_key=True, autoincrement=True)
    vehicle_id  = Column(String,  ForeignKey("vehicles.id"), nullable=False)
    timestamp   = Column(DateTime, nullable=False)
    soc_pct     = Column(Float,  nullable=True)
    speed_kmh   = Column(Float,  nullable=True)
    odometer_km = Column(Float,  nullable=True)
    latitude    = Column(Float,  nullable=True)
    longitude   = Column(Float,  nullable=True)
    is_moving   = Column(Boolean, default=False)
    raw         = Column(JSON,   nullable=True)
    vehicle = relationship("Vehicle", back_populates="readings")


class ChargingPlan(Base):
    __tablename__ = "charging_plans"
    id              = Column(Integer, primary_key=True, autoincrement=True)
    charger_id      = Column(String,  ForeignKey("chargers.id"), nullable=False)
    vehicle_id      = Column(String,  ForeignKey("vehicles.id"), nullable=True)
    created_at      = Column(DateTime, default=datetime.utcnow)
    planned_start   = Column(DateTime, nullable=False)
    planned_stop    = Column(DateTime, nullable=False)
    departure_time  = Column(DateTime, nullable=True)
    target_soc_pct  = Column(Float,   default=90.0)
    current_soc_pct = Column(Float,   nullable=True)
    required_amps   = Column(Float,   nullable=False)
    status          = Column(String,  default="pending")
    applied_at      = Column(DateTime, nullable=True)
    notes           = Column(Text,    nullable=True)
    vehicle = relationship("Vehicle", back_populates="plans")


class ChargingProfileSnapshot(Base):
    """Profils de charge persistés — ré-appliqués au reconnect (A9 + A10)."""
    __tablename__ = "charging_profile_snapshots"
    __table_args__ = (UniqueConstraint("charger_id","connector_id","purpose",name="uq_charger_connector_purpose"),)
    id           = Column(Integer, primary_key=True, autoincrement=True)
    charger_id   = Column(String,  ForeignKey("chargers.id", ondelete="CASCADE"), nullable=False)
    connector_id = Column(Integer, nullable=False)
    purpose      = Column(String,  nullable=False)
    profile_json = Column(JSON,    nullable=False)
    applied_at   = Column(DateTime, default=datetime.utcnow)
    # Sprint 24 A10 : marquer expiré plutôt que supprimer (audit trail + purge > 90j au boot)
    expired_at   = Column(DateTime, nullable=True)
    charger = relationship("Charger", back_populates="charging_profile_snapshots")


# ─── Sprint 28 A7 : Réservations ─────────────────────────────────────────────

class ReservationStatus(str, PyEnum):
    """Cycle de vie d'une réservation (indépendant du retour OCPP ReserveNow)."""
    ACTIVE    = "Active"     # acceptée, en attente du véhicule
    USED      = "Used"       # StartTransaction matché → consommée
    CANCELLED = "Cancelled"  # annulée via CancelReservation
    EXPIRED   = "Expired"    # expiry_date passée sans StartTransaction
    REJECTED  = "Rejected"   # borne a refusé ReserveNow (Occupied/Faulted/Unavailable)


class Reservation(Base):
    """A7 — Réservations persistées (ReserveNow côté serveur).

    Une réservation lie un `id_tag` à un `connector_id` (0 = toute la borne)
    jusqu'à `expiry_date` (UTC). Quand un `StartTransaction` arrive avec le
    même idTag sur ce connector (ou la borne entière), la réservation passe
    à `Used`. `reservation_id` est généré côté serveur (unique par borne).
    """
    __tablename__ = "reservations"
    id              = Column(Integer, primary_key=True, autoincrement=True)
    reservation_id  = Column(Integer, nullable=False)   # ID OCPP envoyé à la borne
    charger_id      = Column(String,  ForeignKey("chargers.id", ondelete="CASCADE"), nullable=False)
    connector_id    = Column(Integer, nullable=False)   # 0 = toute la borne
    id_tag          = Column(String,  nullable=False)
    parent_id_tag   = Column(String,  nullable=True)
    expiry_date     = Column(DateTime, nullable=False)
    status          = Column(Enum(ReservationStatus), default=ReservationStatus.ACTIVE, nullable=False)
    created_at      = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at      = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    ocpp_status     = Column(String,  nullable=True)    # réponse brute ReserveNow ("Accepted"/"Occupied"/...)
    notes           = Column(Text,    nullable=True)
    __table_args__  = (UniqueConstraint("charger_id", "reservation_id",
                                        name="uq_reservation_charger_rid"),)


# ─── Sprint 28 A7 : Log DataTransfer (audit vendor-specific) ─────────────────

class DataTransferLog(Base):
    """A7 — Audit trail des DataTransfer entrants + sortants.

    Les DataTransfer OCPP sont vendor-specific (Grizzl-E envoie des diagnostics,
    TechnoVE des télémétries custom, etc.). On loggue tout sans interpréter,
    l'opérateur peut inspecter via /api/data-transfer/logs.
    """
    __tablename__ = "data_transfer_logs"
    id          = Column(Integer, primary_key=True, autoincrement=True)
    charger_id  = Column(String,  ForeignKey("chargers.id", ondelete="CASCADE"), nullable=False)
    direction   = Column(String,  nullable=False)   # "in" = borne→serveur, "out" = serveur→borne
    vendor_id   = Column(String,  nullable=True)
    message_id  = Column(String,  nullable=True)
    data        = Column(Text,    nullable=True)    # payload brut (peut être JSON ou string)
    response    = Column(String,  nullable=True)    # status OCPP ("Accepted"/"Rejected"/"UnknownVendorId"/...)
    timestamp   = Column(DateTime, default=datetime.utcnow, nullable=False)


# ─── Sprint 29 Volet A : Whitelist idTag (Authorize) ────────────────────────

class OcppTag(Base):
    """Sprint 29 Volet A — Whitelist d'idTags pour Authorize/StartTransaction.

    Comportement à l'Authorize :
      * table vide → fallback permissif Accepted (retrocompat)
      * tag introuvable → Invalid
      * tag.active=False → Blocked
      * tag.expiry_date dépassée → Expired
      * sinon → Accepted (avec parentIdTag + expiryDate si définis)

    `max_active_tx` réservé pour usage futur (limiter les sessions
    parallèles par tag). `parent_id_tag` permet l'héritage d'attributs
    comme en OCPP 1.6J §9.1.
    """
    __tablename__ = "ocpp_tags"
    id             = Column(Integer, primary_key=True, autoincrement=True)
    id_tag         = Column(String(20), unique=True, nullable=False, index=True)
    parent_id_tag  = Column(String(20), nullable=True)
    active         = Column(Boolean, default=True, nullable=False)
    expiry_date    = Column(DateTime, nullable=True)
    max_active_tx  = Column(Integer, default=1)
    note           = Column(String(200), nullable=True)
    created_at     = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at     = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


# ─── Sprint 29 Volet B : GetDiagnostics + DiagnosticsStatusNotification ────

class DiagnosticsRequest(Base):
    """Sprint 29 Volet B — Demandes GetDiagnostics + suivi du statut d'upload.

    À la création : status='Requested'. Le handler
    DiagnosticsStatusNotification met à jour `status` (Idle/Uploading/
    Uploaded/UploadFailed). `filename_returned` = réponse OCPP du
    GetDiagnostics (optionnelle selon la borne).
    """
    __tablename__ = "diagnostics_requests"
    id                 = Column(Integer, primary_key=True, autoincrement=True)
    charger_id         = Column(String, ForeignKey("chargers.id", ondelete="CASCADE"),
                                nullable=False, index=True)
    location           = Column(String(500), nullable=False)
    retries            = Column(Integer, default=0)
    retry_interval     = Column(Integer, nullable=True)
    start_time         = Column(DateTime, nullable=True)
    stop_time          = Column(DateTime, nullable=True)
    requested_at       = Column(DateTime, default=datetime.utcnow, nullable=False)
    filename_returned  = Column(String(200), nullable=True)
    status             = Column(String(32), default="Requested", nullable=False)
    status_updated_at  = Column(DateTime, nullable=True)


# ─── Sprint 29 Volet D : Anti-cloud-override drift detection ────────────────

class ConfigSnapshot(Base):
    """Sprint 29 Volet D — Snapshot des clés GetConfiguration par borne.

    Détecte les drifts : un cloud fabricant (TechnoVE, Grizzl-E) qui
    rebascule une clé dans notre dos. Quand `observed_value != expected_value`,
    on incrémente `drift_count`, logge un warning, et re-push la valeur
    attendue via ChangeConfiguration (last_heal_at). Première capture =
    baseline (expected = observed pour les watched keys).

    Contrainte UNIQUE (charger_id, key) : 1 ligne par clé par borne.
    """
    __tablename__ = "config_snapshots"
    __table_args__ = (UniqueConstraint("charger_id", "key", name="uq_config_snapshot_charger_key"),)
    id              = Column(Integer, primary_key=True, autoincrement=True)
    charger_id      = Column(String, ForeignKey("chargers.id", ondelete="CASCADE"),
                             nullable=False, index=True)
    key             = Column(String(80), nullable=False)
    expected_value  = Column(String(500), nullable=True)
    observed_value  = Column(String(500), nullable=True)
    readonly        = Column(Boolean, default=False)
    drift_count     = Column(Integer, default=0)
    last_drift_at   = Column(DateTime, nullable=True)
    last_heal_at    = Column(DateTime, nullable=True)
    captured_at     = Column(DateTime, default=datetime.utcnow, nullable=False)


# ─── Sprint 30 Volet A : UpdateFirmware flow ────────────────────────────────

class FirmwareUpdate(Base):
    """Sprint 30 Volet A — Demandes UpdateFirmware + suivi du statut.

    Cycle de vie :
      * API POST /update-firmware  → status='Requested', ligne persistée
      * UpdateFirmware OCPP envoyé → call accepté (CALLRESULT vide)
      * Borne envoie FirmwareStatusNotification (Downloading/Downloaded/
        Installing/Installed/DownloadFailed/InstallationFailed)
        → on met à jour `status` et `status_updated_at`
      * BootNotification suivant (si status ∈ {Installing, Downloaded})
        → on capture `firmware_version_after` et on marque Installed
    """
    __tablename__ = "firmware_updates"
    id                       = Column(Integer, primary_key=True, autoincrement=True)
    charger_id               = Column(String, ForeignKey("chargers.id", ondelete="CASCADE"),
                                       nullable=False, index=True)
    location                 = Column(String(500), nullable=False)
    retrieve_date            = Column(DateTime, nullable=False)
    retries                  = Column(Integer, nullable=True)
    retry_interval           = Column(Integer, nullable=True)
    requested_at             = Column(DateTime, default=datetime.utcnow, nullable=False)
    status                   = Column(String(32), default="Requested", nullable=False)
    status_updated_at        = Column(DateTime, nullable=True)
    firmware_version_before  = Column(String(40), nullable=True)
    firmware_version_after   = Column(String(40), nullable=True)
    notes                    = Column(String(500), nullable=True)


# ─── Sprint 30 Volet C : anti-override Current.Import drift ─────────────────

# ─── Sprint 31 Volet A : LocalAuthList full sync (§5.3, §6.9, §6.11) ─────────

class LocalListVersion(Base):
    """Sprint 31 Volet A — État de synchronisation de la LocalAuthList par borne.

    Le CSMS (serveur) incrémente `server_version` à chaque SendLocalList
    (Full/Differential) pour que la borne puisse détecter un désynchro via
    GetLocalListVersion. Contrainte UNIQUE(charger_id) : 1 ligne par borne.
    """
    __tablename__ = "local_list_versions"
    id                = Column(Integer, primary_key=True, autoincrement=True)
    charger_id        = Column(String, ForeignKey("chargers.id", ondelete="CASCADE"),
                                unique=True, nullable=False, index=True)
    server_version    = Column(Integer, default=0, nullable=False)
    charger_version   = Column(Integer, nullable=True)   # dernière version lue via GetLocalListVersion
    last_sync_at      = Column(DateTime, nullable=True)
    last_sync_status  = Column(String(32), nullable=True)  # Accepted/Failed/VersionMismatch/NotSupported
    last_sync_type    = Column(String(16), nullable=True)  # Full/Differential
    tag_count_sent    = Column(Integer, nullable=True)


# ─── Sprint 31 Volet C : StatusNotification historization (§4.8) ────────────

class StatusNotificationLog(Base):
    """Sprint 31 Volet C — Historique complet des StatusNotification par borne.

    Permet l'audit (qui/quand/pourquoi la borne a changé de statut), le
    diagnostic de pannes intermittentes (alternance Faulted/Available) et
    les compliance reports. L'ancienne colonne `connectors.status` reste
    tenue à jour pour retrocompat UI — cette table est additive.
    """
    __tablename__ = "status_notifications"
    __table_args__ = (
        # Index composite pour queries "historique par borne"
        # Pas de UniqueConstraint : on insère aussi les duplicats "même statut"
    )
    id                 = Column(Integer, primary_key=True, autoincrement=True)
    charger_id         = Column(String, ForeignKey("chargers.id", ondelete="CASCADE"),
                                 nullable=False, index=True)
    connector_id       = Column(Integer, nullable=False)
    status             = Column(String(32), nullable=False)       # Available/Preparing/Charging/...
    error_code         = Column(String(40), nullable=True)        # NoError/ConnectorLockFailure/...
    info               = Column(String(200), nullable=True)       # Info libre fabricant
    vendor_id          = Column(String(100), nullable=True)
    vendor_error_code  = Column(String(100), nullable=True)
    timestamp          = Column(DateTime, nullable=True)          # timestamp borne (peut être NULL)
    received_at        = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)


class OverrideIncident(Base):
    """Sprint 30 Volet C — Incidents de drift courant observé vs profil attendu.

    Quand `observed_current > expected_limit + 2A` pendant 3 samples
    consécutifs (~3 min) pour une même session, on enregistre un incident,
    on log un WARNING, et le serveur ré-applique `SetChargingProfile` avec
    la limite attendue.

    Défense en profondeur : le pare-feu bloque déjà les overrides cloud
    manufacturier, donc cette détection ne devrait quasi jamais se
    déclencher en production. Utile surtout pour :
      * détection d'un firmware borne bogué
      * validation de la cascade SetChargingProfile (A9 replay)
      * audit forensique
    """
    __tablename__ = "override_incidents"
    id                = Column(Integer, primary_key=True, autoincrement=True)
    charger_id        = Column(String, ForeignKey("chargers.id", ondelete="CASCADE"),
                                nullable=False, index=True)
    connector_id      = Column(Integer, nullable=True)
    transaction_id    = Column(Integer, nullable=True)
    expected_amps     = Column(Float, nullable=False)
    observed_amps     = Column(Float, nullable=False)
    delta_amps        = Column(Float, nullable=False)
    samples_consecutive = Column(Integer, default=3, nullable=False)
    reapplied         = Column(Boolean, default=False, nullable=False)
    reapply_status    = Column(String(32), nullable=True)
    detected_at       = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    notes             = Column(String(500), nullable=True)


# ─── Sprint 32 Point 2 : Activity Log unifié + documentation messages OCPP ──────

class ChargerActivityLog(Base):
    """Sprint 32 — Journal unifié de TOUS les messages OCPP par borne.

    Capture chaque CALL/CALLRESULT/CALLERROR (in & out) parsé depuis le logger
    `ocpp` (MobilityHouse) via `core/activity_logger.py`. Source unique de
    vérité pour audit, debug, recherche, et UI activity timeline.

    Les anciens loggers spécialisés (StatusNotificationLog, DataTransferLog,
    OverrideIncident) restent en place pour les usages métier ciblés — cette
    table est ADDITIVE et capture le brut.

    Index :
      * (charger_id, timestamp DESC)  → timeline par borne
      * (action)                       → filtre par type de message
      * (transaction_id)               → audit session
      * (unique_id)                    → corrélation CALL ↔ CALLRESULT
    """
    __tablename__ = "charger_activity_log"
    id                = Column(Integer, primary_key=True, autoincrement=True)
    charger_id        = Column(String, ForeignKey("chargers.id", ondelete="CASCADE"),
                                nullable=False, index=True)
    direction         = Column(String(8), nullable=False)    # 'in' (CP→CSMS) ou 'out' (CSMS→CP)
    msg_type          = Column(Integer, nullable=False)      # 2=CALL, 3=CALLRESULT, 4=CALLERROR
    action            = Column(String(80), nullable=True, index=True)  # BootNotification, Heartbeat, ...
    unique_id         = Column(String(128), nullable=True, index=True) # corrélation OCPP
    payload           = Column(Text, nullable=True)          # JSON sérialisé
    error_code        = Column(String(40), nullable=True)
    error_description = Column(String(255), nullable=True)
    connector_id      = Column(Integer, nullable=True)       # extrait du payload si présent
    transaction_id    = Column(Integer, nullable=True, index=True)  # idem
    timestamp         = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)


class OcppMessageDoc(Base):
    """Sprint 32 — Documentation FR de chaque message OCPP 1.6J.

    Seedée au boot depuis core/ocpp_message_docs_seed.py. La structure est
    pensée pour rendre une page "doc message" claire et utile :
      * `summary_fr` : 1 phrase
      * `description_fr` : 2-4 phrases avec contexte d'usage
      * `fields_fr` : JSON {champ_OCPP: explication_FR}
      * `triggered_by` : quand la borne (ou le serveur) émet ce message
      * `cs_response`  : ce que le destinataire répond
      * `section_norm` : référence dans la spec OCPP 1.6J Edition 2 (juillet 2017)
      * `direction_norm` : 'CP→CSMS' / 'CSMS→CP' / 'both'
      * `profile`        : Core / FirmwareManagement / SmartCharging / Reservation /
                           RemoteTrigger / LocalAuthListManagement
    """
    __tablename__ = "ocpp_message_docs"
    name             = Column(String(80), primary_key=True)   # ex: 'BootNotification'
    direction_norm   = Column(String(16), nullable=False)
    profile          = Column(String(40), nullable=False)
    section_norm     = Column(String(16), nullable=True)      # ex: '§4.2'
    summary_fr       = Column(String(300), nullable=False)
    description_fr   = Column(Text, nullable=False)
    fields_fr        = Column(Text, nullable=True)            # JSON
    triggered_by     = Column(String(300), nullable=True)
    cs_response      = Column(String(300), nullable=True)
    updated_at       = Column(DateTime, default=datetime.utcnow,
                                onupdate=datetime.utcnow, nullable=False)

# Sprint 33 — Certification OCPP 1.6J — modèles DB
# À APPENDER à la fin de /home/lteiot/ocpp-server/db/models.py
# Les imports du fichier cible couvrent déjà Column/String/Integer/Float/
# Boolean/DateTime/ForeignKey/Text/JSON/PyEnum/Enum/datetime/relationship.


class CertificationRunStatus(str, PyEnum):
    RUNNING   = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED    = "failed"


class CertificationRun(Base):
    """Sprint 33 — Run de certification d'une borne.

    Persistance d'un passage complet (quick/standard/full) : métadonnées
    du run + synthèse (passed/failed/skipped) + rapports HTML et JSON
    sérialisés pour replay.
    """
    __tablename__ = "certification_runs"

    id                 = Column(Integer, primary_key=True, autoincrement=True)
    run_id             = Column(String(64), unique=True, nullable=False, index=True)
    charger_id         = Column(String(20), ForeignKey("chargers.id", ondelete="CASCADE"), nullable=False, index=True)
    suite              = Column(String(16), nullable=False)      # quick/standard/full
    status             = Column(String(16), nullable=False, default="running")
    technician_name    = Column(String(120), nullable=True)
    notes              = Column(String(500), nullable=True)
    supported_profiles = Column(String(200), nullable=True)      # CSV
    firmware_before    = Column(String(40), nullable=True)
    passed             = Column(Integer, nullable=False, default=0)
    failed             = Column(Integer, nullable=False, default=0)
    skipped            = Column(Integer, nullable=False, default=0)
    total              = Column(Integer, nullable=False, default=0)
    started_at         = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    finished_at        = Column(DateTime, nullable=True)
    duration_s         = Column(Float, nullable=True)
    report_html        = Column(Text, nullable=True)
    report_json        = Column(Text, nullable=True)              # JSON sérialisé


class CertificationTestResult(Base):
    """Résultat individuel d'un test dans un run de certification."""
    __tablename__ = "certification_test_results"

    id             = Column(Integer, primary_key=True, autoincrement=True)
    run_id         = Column(String(64), ForeignKey("certification_runs.run_id", ondelete="CASCADE"), nullable=False, index=True)
    test_name      = Column(String(80), nullable=False)
    category       = Column(String(40), nullable=False)
    title          = Column(String(160), nullable=True)
    status         = Column(String(16), nullable=False)   # passed/failed/skipped/error
    message        = Column(Text, nullable=True)
    details        = Column(Text, nullable=True)          # JSON
    recommendation = Column(Text, nullable=True)
    ocpp_ref       = Column(String(40), nullable=True)
    duration_s     = Column(Float, nullable=True)
    started_at     = Column(DateTime, default=datetime.utcnow, nullable=False)
