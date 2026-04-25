# config.py
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).parent


class OCPPSettings(BaseSettings):
    host: str = "0.0.0.0"
    port: int = 9000
    ws_path: str = "/ocpp"

    # Protocole supporté : OCPP 1.6J uniquement.
    # Les variantes syntaxiques (ocpp1.6.0, ocpp16, ocpp1.6j) sont normalisées
    # dans ocpp_server.py via _OCPP16_VARIANTS — pas besoin de les lister ici.
    supported_protocols: list[str] = ["ocpp1.6"]

    # ping_interval=None : désactive les WebSocket pings côté serveur.
    # TechnoVE et Grizzl-E ne répondent pas aux WS pings pendant le traitement
    # de commandes, ce qui déclenche des déconnexions 1011 prématurées.
    # Le keepalive est assuré par OCPP Heartbeat (interval=30s, côté borne).
    ping_interval: Optional[int] = None
    ping_timeout:  int = 60


class APISettings(BaseSettings):
    host: str = "0.0.0.0"
    port: int = 8000
    secret_key: str = Field(..., min_length=32)
    access_token_expire_minutes: int = 60
    allowed_origins: list[str] = ["http://localhost:3000"]


class DatabaseSettings(BaseSettings):
    url: str = f"sqlite+aiosqlite:///{BASE_DIR}/data/ocpp.db"
    echo: bool = False


class OBD2Settings(BaseSettings):
    enabled: bool = False
    server_url: str = "http://localhost:8080"
    poll_interval: int = 5
    api_key: str = ""


class LogSettings(BaseSettings):
    level: str = "INFO"
    file: Path = BASE_DIR / "logs" / "ocpp.log"
    rotation: str = "10 MB"
    retention: str = "30 days"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        case_sensitive=False,
    )

    app_name: str = "OCPP Server"
    environment: str = "development"
    timezone: str = "America/Toronto"

    ocpp: OCPPSettings = OCPPSettings()
    api: APISettings = APISettings(secret_key="changeme-32chars-minimum-secret!")
    database: DatabaseSettings = DatabaseSettings()
    obd2: OBD2Settings = OBD2Settings()
    log: LogSettings = LogSettings()


settings = Settings()
