"""Application configuration module."""

import os
from pathlib import Path

from pydantic import BaseModel
from pydantic import EmailStr
from pydantic import Field
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict

# Define constants for Cyrillic values
# ruff: noqa: RUF001
BUILDING_NORTH = "Н"
GROUP_BIK2024 = "БИК2404"
CALENDAR_NAME = "МТУСИ Расписание"


class GoogleCalendarSettings(BaseModel):
    """Google Calendar specific settings."""

    calendar_id: EmailStr
    credentials_path: Path
    token_path: Path = Field(default=Path("token.json"))
    calendar_name: str = CALENDAR_NAME


class MTUCISettings(BaseModel):
    """MTUCI specific settings."""

    email: EmailStr
    password: str
    base_url: str = "https://lk.mtuci.ru"
    schedule_url: str = "https://lk.mtuci.ru/student/schedule"


class ScrapingSettings(BaseModel):
    """
    Scraping specific settings.

    Attributes:
        max_retries: Maximum number of retry attempts
        timeout_ms: Timeout in milliseconds
        default_building: Default building code
        default_group: Default group code
    """

    max_retries: int = 3
    timeout_ms: int = 15000
    default_building: str = BUILDING_NORTH
    default_group: str = GROUP_BIK2024


class Settings(BaseSettings):
    """Application settings."""

    mtuci: MTUCISettings
    google_calendar: GoogleCalendarSettings
    scraping: ScrapingSettings | None = Field(default_factory=ScrapingSettings)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        case_sensitive=False,
    )

    @classmethod
    def from_env(cls, env_file: Path) -> "Settings":
        """Create settings from environment file."""
        return cls(
            _env_file=env_file,
            mtuci=MTUCISettings(
                email=os.getenv("MTUCI_EMAIL"),
                password=os.getenv("MTUCI_PASSWORD"),
                base_url=os.getenv("MTUCI_BASE_URL", "https://lk.mtuci.ru"),
            ),
            google_calendar=GoogleCalendarSettings(
                calendar_id=os.getenv("GOOGLE_CALENDAR_ID"),
                credentials_path=Path(
                    os.getenv("GOOGLE_CREDENTIALS_PATH", "credentials.json")
                ),
                token_path=Path(os.getenv("GOOGLE_TOKEN_PATH", "token.json")),
                calendar_name=os.getenv("CALENDAR_NAME", CALENDAR_NAME),
            ),
            scraping=ScrapingSettings(
                max_retries=int(os.getenv("MAX_RETRIES", "3")),
                timeout_ms=int(os.getenv("TIMEOUT_MS", "15000")),
                default_building=os.getenv("DEFAULT_BUILDING", BUILDING_NORTH),
                default_group=os.getenv("DEFAULT_GROUP", GROUP_BIK2024),
            ),
        )
