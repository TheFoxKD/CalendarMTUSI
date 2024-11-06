# src/scraping/schedule_scraper.py

from playwright.async_api import ElementHandle
from playwright.async_api import Page
from playwright.async_api import TimeoutError

from src.core.config import Settings
from src.core.exceptions import ApplicationError
from src.core.exceptions import ScrapingError
from src.core.logging import get_logger
from src.models.schedule import LessonType
from src.models.schedule import Location
from src.models.schedule import ScheduleEvent
from src.scraping.auth import AuthConfig
from src.scraping.auth import MTUCIAuthenticator

logger = get_logger(__name__)


class MTUCIScheduleScraper:
    """Scraper for MTUCI schedule website with improved error handling."""

    REQUIRED_SPANS_COUNT = 4

    def __init__(
        self,
        auth_config: AuthConfig,
        max_retries: int | None = None,
        timeout_ms: int | None = None,
    ):
        """Initialize scraper with configuration."""
        self.settings = Settings()
        self._logger = logger.bind(email=auth_config.email)
        self.authenticator = MTUCIAuthenticator(auth_config)
        self.schedule_url = f"{self.settings.mtuci_base_url}/student/schedule"

        # Initialize timeouts and retries
        self.max_retries = max_retries or self.settings.scraping_max_retries
        self.timeout_ms = timeout_ms or self.settings.scraping_timeout_ms

        self._logger.info(
            "Scraper initialized",
            max_retries=self.max_retries,
            timeout_ms=self.timeout_ms,
        )

    async def _navigate_to_schedule(self, page: Page) -> None:
        """Navigate to schedule page with proper error handling."""
        try:
            await page.goto(self.schedule_url, wait_until="networkidle")

            try:
                await page.wait_for_selector("#lessons", timeout=self.timeout_ms)
            except TimeoutError as e:
                raise ScrapingError(ScrapingError.SCHEDULE_NOT_FOUND) from e

        except Exception as e:
            if isinstance(e, ScrapingError):
                raise
            error_message = "Failed to load schedule page"
            raise ApplicationError(error_message, original_error=e) from e

    def _raise_scraping_error(self, message: str) -> None:
        """Helper method to raise scraping errors."""
        raise ScrapingError(message)

    async def _extract_lesson_data(self, lesson: ElementHandle) -> ScheduleEvent:
        """Extract data from lesson element with improved validation."""
        try:
            # Get subject
            subject_el = await lesson.query_selector("h4")
            if not subject_el:
                self._raise_scraping_error(ScrapingError.SUBJECT_NOT_FOUND)

            subject_text = await subject_el.text_content()

            # Get info div
            info_div = await lesson.query_selector(".text-gray")
            if not info_div:
                self._raise_scraping_error(ScrapingError.INFO_DIV_NOT_FOUND)

            # Extract and validate all required data
            spans = await info_div.query_selector_all("span")
            if len(spans) < self.REQUIRED_SPANS_COUNT:
                error_message = f"Expected 4 info spans, got {len(spans)}"
                self._raise_scraping_error(error_message)

            teacher = (await spans[0].text_content()).strip()
            lesson_type_text = (await spans[1].text_content()).strip()
            time_text = (await spans[2].text_content()).strip()
            location_text = (await spans[3].text_content()).strip()

            # Parse components with validation
            start_time, end_time = await self._parse_time(time_text)
            location = await self._parse_location(location_text)
            lesson_type = self._map_lesson_type(lesson_type_text)

            return ScheduleEvent(
                subject=subject_text.strip(),
                teacher=teacher,
                lesson_type=lesson_type,
                location=location,
                start_time=start_time,
                end_time=end_time,
                group=self.settings.scraping_default_group,
                subgroup=None,
            )

        except ScrapingError:
            raise
        except Exception as e:
            error_message = f"Failed to extract lesson data: {e}"
            raise ApplicationError(error_message, original_error=e) from e

    async def _find_lessons(self, page: Page) -> list[ElementHandle]:
        """Find lesson elements on the page."""
        lessons = await page.query_selector_all(".lesson")

        if not lessons:
            self._logger.warning("No lessons found, trying alternative selectors")
            for selector in [".schedule-item", "tr.lesson-row", ".timetable-item"]:
                lessons = await page.query_selector_all(selector)
                if lessons:
                    break

        if not lessons:
            self._logger.warning("No lessons found after trying all selectors")
            return []

        self._logger.info("Found %d lessons to parse", len(lessons))
        return lessons

    async def _parse_lessons(self, lessons: list[ElementHandle]) -> list[ScheduleEvent]:
        """Parse individual lesson elements into schedule events."""
        events = []
        for idx, lesson in enumerate(lessons, 1):
            try:
                event = await self._extract_lesson_data(lesson)
                events.append(event)
            except Exception as e:
                self._logger.exception(
                    "Failed to parse lesson", lesson_number=idx, error=str(e)
                )
                continue
        return events

    async def parse_schedule(self, page: Page, retries: int = 0) -> list[ScheduleEvent]:
        """Parse schedule with improved error handling and retry logic."""
        try:
            await self.authenticator.authenticate(page)
            await self._navigate_to_schedule(page)

            lessons = await self._find_lessons(page)
            if not lessons:
                return []

            return await self._parse_lessons(lessons)

        except ApplicationError:
            raise
        except Exception as e:
            if retries < self.max_retries:
                self._logger.warning(
                    "Retrying schedule parse", retry_count=retries + 1, error=str(e)
                )
                return await self.parse_schedule(page, retries + 1)

            error_message = "Failed to parse schedule"
            raise ApplicationError(error_message, original_error=e) from e

    async def _parse_location(self, location_text: str) -> Location:
        """Parse location with improved error handling."""
        try:
            room_text = location_text.replace("Аудитория:", "").strip()

            # Handle special locations
            special_locations = {
                "Зал аэробики": Location(
                    building=self.settings.scraping_default_building,
                    room="Зал аэробики",
                ),
                "Спортивный зал": Location(
                    building=self.settings.scraping_default_building, room="Спортзал"
                ),
                "Актовый зал": Location(
                    building=self.settings.scraping_default_building, room="Актовый зал"
                ),
            }

            for special_name, location in special_locations.items():
                if special_name.lower() in room_text.lower():
                    return location

            if "-" in room_text:
                building, room = room_text.split("-", 1)
                return Location(building=building.strip(), room=room.strip())

            return Location(
                building=self.settings.scraping_default_building, room=room_text.strip()
            )

        except Exception as e:
            error_message = f"Failed to parse location: {location_text}"
            raise ApplicationError(error_message, original_error=e) from e

    def _map_lesson_type(self, type_text: str) -> LessonType:
        """Map lesson type text to enum with validation."""
        type_map = {
            "Лекция": LessonType.LECTURE,
            "Практическое занятие": LessonType.PRACTICE,
            "Лабораторная работа": LessonType.LAB,
        }

        mapped_type = type_map.get(type_text.strip())
        if not mapped_type:
            self._logger.warning(
                "Unknown lesson type: %s, defaulting to LECTURE", type_text
            )
            return LessonType.LECTURE

        return mapped_type
