# config.py
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
from pathlib import Path

BASE_DIR = Path(__file__).parent


class OCPPSettings(BaseSettings):
    host: str = "0.0.0.0"
    port: int = 9000
    ws_path: str = "/ocpp"
    ping_interval: int = 30          # secondes
    ping_timeout: int = 10
    supported_protocols: list[str] = ["ocpp1.6", "ocpp2.0.1"]


class APISettings(BaseSettings):
    host: str = "0.0.0.0"
    port: int = 8000
    secret_key: str = Field(..., min_length=32)   # obligatoire
    access_token_expire_minutes: int = 60
    allowed_origins: list[str] = ["http://localhost:3000"]


class DatabaseSettings(BaseSettings):
    url: str = f"sqlite+aiosqlite:///{BASE_DIR}/data/ocpp.db"
    echo: bool = False               # True = log toutes les queries SQL


class OBD2Settings(BaseSettings):
    enabled: bool = False
    server_url: str = "http://localhost:8080"
    poll_interval: int = 5           # secondes
    api_key: str = ""


class LogSettings(BaseSettings):
    level: str = "INFO"              # DEBUG | INFO | WARNING | ERROR
    file: Path = BASE_DIR / "logs" / "ocpp.log"
    rotation: str = "10 MB"
    retention: str = "30 days"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",   # ex: OCPP__PORT=9000
        case_sensitive=False,
    )

    app_name: str = "OCPP Server"
    environment: str = "development"  # development | production
    timezone: str = "America/Toronto"   # UTC-4/UTC-5 selon DST

    ocpp: OCPPSettings = OCPPSettings()
    api: APISettings = APISettings(secret_key="changeme-32chars-minimum-secret!")
    database: DatabaseSettings = DatabaseSettings()
    obd2: OBD2Settings = OBD2Settings()
    log: LogSettings = LogSettings()


# Singleton — importé partout dans le projet
settings = Settings()
