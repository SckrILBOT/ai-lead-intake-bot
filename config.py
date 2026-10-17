from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Required
    telegram_bot_token: str
    gemini_api_key: str

    # LLM
    gemini_model: str = "gemini-3.5-flash"
    history_limit: int = 15
    llm_max_retries: int = 3

    # Storage
    data_dir: Path = Path("data")
    timezone: str = "UTC"

    # Google Sheets (optional)
    google_service_account_json: str | None = None
    spreadsheet_id: str | None = None
    worksheet_name: str = "Leads"

    # SMTP (optional)
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None
    admin_email: str | None = None

    # Limits
    voice_max_seconds: int = 180
    throttle_seconds: float = 0.7

    @field_validator(
        "google_service_account_json",
        "spreadsheet_id",
        "smtp_host",
        "smtp_user",
        "smtp_password",
        "smtp_from",
        "admin_email",
        mode="before",
    )
    @classmethod
    def _empty_to_none(cls, v):
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @field_validator("smtp_port", mode="before")
    @classmethod
    def _port_default(cls, v):
        if isinstance(v, str) and not v.strip():
            return 587
        return v

    @property
    def sheets_enabled(self) -> bool:
        return bool(self.google_service_account_json and self.spreadsheet_id)

    @property
    def email_enabled(self) -> bool:
        return bool(self.smtp_host and self.smtp_user and self.smtp_password)

    @property
    def email_recipient(self) -> str | None:
        return self.admin_email or self.smtp_user

    @property
    def db_path(self) -> Path:
        return self.data_dir / "bot.db"

    @property
    def backup_path(self) -> Path:
        return self.data_dir / "leads_backup.json"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
