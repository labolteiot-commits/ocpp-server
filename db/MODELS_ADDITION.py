# db/models_charging_profile_snapshot.py
"""
Modèle SQLAlchemy pour la persistance des profils de charge.

STEVE-4 : SteVe sauvegarde les profils en DB après chaque SetChargingProfile
          accepté (sauf TxProfile) pour les ré-appliquer au reconnect.
          Référence : SetChargingProfileTaskFromDB.java → chargingProfileRepository.setProfile()

À ajouter dans db/models.py :
  - Importer ChargingProfileSnapshot
  - Ajouter la relation optionnelle dans Charger si souhaité

À ajouter dans db/database.py :
  - La table sera créée automatiquement par init_db() → Base.metadata.create_all()
"""
from __future__ import annotations
from datetime import datetime

from sqlalchemy import (
    Column, String, Integer, DateTime, JSON, UniqueConstraint
)
from sqlalchemy.orm import relationship

# Import Base depuis votre db/models.py existant
# from db.models import Base  ← à faire dans models.py

# ── À ajouter dans la classe Base de db/models.py ────────────────────────────

CHARGING_PROFILE_SNAPSHOT_DDL = """
-- Table créée automatiquement par SQLAlchemy (Base.metadata.create_all)
-- Référence SteVe : chargingProfile table + connector_charging_profile association
CREATE TABLE IF NOT EXISTS charging_profile_snapshots (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    charger_id    TEXT NOT NULL REFERENCES chargers(id) ON DELETE CASCADE,
    connector_id  INTEGER NOT NULL,
    purpose       TEXT NOT NULL,    -- TxDefaultProfile | ChargePointMaxProfile | TxProfile
    profile_json  JSON NOT NULL,    -- Le profil OCPP complet sérialisé
    applied_at    DATETIME,
    UNIQUE (charger_id, connector_id, purpose)
);
"""

# ── Code à intégrer dans db/models.py ────────────────────────────────────────

MODELS_CODE_TO_ADD = '''
class ChargingProfileSnapshot(Base):
    """
    Profil de charge persisté — ré-appliqué au reconnect.

    STEVE-4 : SteVe sauvegarde les profils en DB dans SetChargingProfileTaskFromDB.java
    après chaque SetChargingProfile Accepted (sauf TxProfile).
    """
    __tablename__ = "charging_profile_snapshots"
    __table_args__ = (
        UniqueConstraint("charger_id", "connector_id", "purpose",
                         name="uq_charger_connector_purpose"),
    )

    id           = Column(Integer, primary_key=True, autoincrement=True)
    charger_id   = Column(String, ForeignKey("chargers.id", ondelete="CASCADE"), nullable=False)
    connector_id = Column(Integer, nullable=False)
    purpose      = Column(String, nullable=False)   # TxDefaultProfile | ChargePointMaxProfile
    profile_json = Column(JSON,   nullable=False)   # Profil OCPP complet
    applied_at   = Column(DateTime, default=datetime.utcnow)

    # Relation optionnelle (ne pas ajouter si ça complique le modèle existant)
    # charger = relationship("Charger", back_populates="charging_profile_snapshots")
'''

print("=== À ajouter dans db/models.py ===")
print()
print("1. Ajouter l'import: from sqlalchemy import UniqueConstraint")
print("   (si pas déjà présent)")
print()
print("2. Ajouter la classe suivante APRÈS la classe Charger :")
print()
print(MODELS_CODE_TO_ADD)
print()
print("=== La table sera créée automatiquement par init_db() ===")
print("   (Base.metadata.create_all appelle le DDL automatiquement)")
