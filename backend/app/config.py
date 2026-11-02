"""Runtime configuration. Defaults are safe for local Docker and are listed in the README."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Warranty Card Scanner"
    app_secret: str = "dev-only-change-me-please-32b"
    database_url: str = "sqlite:///./warranty.db"
    redis_url: str = "redis://localhost:6379/0"
    storage_dir: str = "./storage"
    worker_inline: bool = True
    auto_scan: bool = True
    seed_demo_users: bool = True
    jwt_exp_minutes: int = 60 * 12
    render_dpi: int = 300
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    firebase_database_url: str = ""
    firebase_project_id: str = ""
    firebase_credentials_file: str = "./firebase-service-account.json"

    @property
    def storage_path(self) -> Path:
        path = Path(self.storage_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]


settings = Settings()
