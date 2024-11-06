"""Configuration module for the application."""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # MTUCI Configuration
    mtuci_email: str = Field(..., description="MTUCI account email")
    mtuci_password: str = Field(..., description="MTUCI account password")
    mtuci_base_url: str = Field(..., description="MTUCI base URL")
    mtuci_schedule_url: str = Field(..., description="MTUCI schedule page URL")

    # Google Calendar Configuration
    google_calendar_id: str = Field(..., description="Google Calendar ID")
    google_credentials_path: Path = Field(
        default=Path("credentials.json"),
        description="Path to Google API credentials file",
    )
    google_token_path: Path = Field(
        default=Path("token.json"),
        description="Path to Google API token file",
    )
    google_calendar_name: str = Field(
        default="МТУСИ Расписание",
        description="Name of the Google Calendar",
    )

    # Scraping Configuration
    scraping_max_retries: int = Field(
        default=3,
        description="Maximum number of scraping retries",
    )
    scraping_timeout_ms: int = Field(
        default=30000,
        description="Scraping timeout in milliseconds",
    )
    scraping_default_building: str = Field(
        description="Default building for schedule",
    )
    scraping_default_group: str = Field(
        description="Default group for schedule",
    )
