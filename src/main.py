"""Main application module for MTUCI schedule synchronization."""

import asyncio
from pathlib import Path

from playwright.async_api import Browser
from playwright.async_api import Page
from playwright.async_api import async_playwright

from src.core.config import Settings
from src.core.exceptions import SYNC_FAILED
from src.core.exceptions import ApplicationError
from src.core.exceptions import BrowserSetupError
from src.core.exceptions import InitializationError
from src.core.logging import configure_logging
from src.core.logging import get_logger
from src.my_calendar.google import CalendarConfig
from src.my_calendar.google import GoogleCalendarService
from src.scraping.auth import AuthConfig
from src.scraping.schedule_scraper import MTUCIScheduleScraper

ROOT_DIR = Path(__file__).parent.parent
ENV_FILE = ROOT_DIR / ".env"

# Initialize logging
configure_logging()
logger = get_logger(__name__)


class ScheduleSyncApp:
    """
    Main application class for schedule synchronization.

    Handles initialization of all required services and coordinates
    the schedule synchronization process.
    """

    def __init__(self) -> None:
        """Initialize application with configuration and services."""
        try:
            self.settings = Settings(_env_file=ENV_FILE)
            self._init_configs()
            self._init_services()
        except Exception as e:
            logger.exception("Application initialization failed", error=str(e))
            raise InitializationError from e

    def _init_configs(self) -> None:
        """Initialize configuration objects for auth and calendar."""
        self.auth_config = AuthConfig(
            email=self.settings.mtuci.email,
            password=self.settings.mtuci.password,
            login_url=f"{self.settings.mtuci.base_url}/auth/login",
        )

        self.calendar_config = CalendarConfig(
            credentials_path=str(self.settings.google_calendar.credentials_path),
            calendar_id=self.settings.google_calendar.calendar_id,
            calendar_name=self.settings.google_calendar.calendar_name,
            token_path=str(self.settings.google_calendar.token_path),
        )

    def _init_services(self) -> None:
        """Initialize application services."""
        self.calendar_service = GoogleCalendarService(config=self.calendar_config)
        self.scraper = MTUCIScheduleScraper(
            auth_config=self.auth_config,
            max_retries=self.settings.scraping.max_retries,
            timeout_ms=self.settings.scraping.timeout_ms,
        )

    async def _setup_browser(self) -> tuple[Browser, Page]:
        """
        Setup browser with proper configuration.

        Returns:
            Tuple containing browser and page instances

        Raises:
            BrowserSetupError: If browser setup fails
        """
        try:
            playwright = await async_playwright().start()
            browser = await playwright.chromium.launch(
                headless=False,
                args=["--no-sandbox", "--disable-setuid-sandbox"],
                slow_mo=50,
            )
            context = await browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) " "AppleWebKit/537.36"
                ),
            )
            return browser, await context.new_page()

        except Exception as e:
            logger.exception("Browser setup failed", error=str(e))
            raise BrowserSetupError from e

    async def sync_schedule(self) -> None:
        """
        Main function to sync schedule with calendar.

        Coordinates the process of fetching schedule data and
        synchronizing it with the calendar service.

        Raises:
            ApplicationError: If sync process fails
        """
        browser = None
        try:
            # Initialize calendar service
            self.calendar_service.initialize()
            logger.info("Calendar service initialized successfully")

            # Start browser session
            browser, page = await self._setup_browser()
            logger.info("Browser session started successfully")

            # Parse schedule
            events = await self.scraper.parse_schedule(page)
            logger.info("Schedule parsed successfully", event_count=len(events))

            # Create calendar events
            event_ids = await self.calendar_service.create_events(events)
            logger.info(
                "Calendar events created successfully", event_count=len(event_ids)
            )

        except ApplicationError as e:
            logger.exception(
                "Application error during sync",
                error=str(e),
                original_error=str(e.original_error) if e.original_error else None,
            )
            raise
        except Exception as e:
            logger.exception("Unexpected error during sync")
            raise ApplicationError(SYNC_FAILED) from e
        finally:
            if browser:
                await browser.close()
                logger.info("Browser session closed")


async def main() -> None:
    """
    Application entry point.

    Initializes and runs the schedule synchronization process.
    """
    try:
        app = ScheduleSyncApp()
        await app.sync_schedule()
    except (ApplicationError, Exception) as e:
        logger.exception(
            "Application failed",
            error=str(e),
            original_error=getattr(e, "original_error", None),
        )
        raise SystemExit(1) from e


if __name__ == "__main__":
    asyncio.run(main())
