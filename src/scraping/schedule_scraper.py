# src/scraping/schedule_scraper.py

from datetime import UTC
from datetime import datetime
from datetime import time

import structlog
from playwright.async_api import Page

from src.core.exceptions import ApplicationError
from src.models.schedule import LessonType
from src.models.schedule import Location
from src.models.schedule import ScheduleEvent
from src.scraping.auth import AuthConfig
from src.scraping.auth import MTUCIAuthenticator

logger = structlog.get_logger(__name__)


class ScrapingError(ApplicationError):
    """Base exception for scraping errors."""


class ScheduleParser:
    """Parser for MTUCI schedule page."""

    MIN_TIME_LOCATION_SPANS = 2
    MIN_TEACHER_TYPE_SPANS = 2
    MIN_FLEX_CONTAINERS = 2
    EXPECTED_DATE_PARTS = 2

    def __init__(self, page: Page):
        self.page = page
        self._logger = logger.bind(component="ScheduleParser")

    async def get_available_dates(self) -> list[datetime]:
        """Get list of available dates from the schedule."""
        try:
            date_buttons = await self.page.query_selector_all(".button-day")
            dates = []

            for button in date_buttons:
                date_text = await button.text_content()
                if not date_text:
                    continue

                try:
                    if "Сегодня" in date_text:
                        date = await self._get_current_date()
                    else:
                        # Parse date like "07.11"
                        date_part = date_text.split()[1]
                        day, month = date_part.split(".")
                        date = datetime(
                            datetime.now(UTC).year, int(month), int(day), tzinfo=UTC
                        )
                    dates.append(date)
                except (ValueError, IndexError) as e:
                    self._logger.warning(
                        "Failed to parse date button", text=date_text, error=str(e)
                    )
                    continue

            return sorted(dates)
        except Exception as e:
            error_message = "Failed to get available dates"
            raise ScrapingError(error_message) from e

    async def _get_current_date(self) -> datetime:
        """Get current date from page header."""
        try:
            header = await self.page.query_selector("h4.current-day")
            if not header:
                error_message = "No header"
                self._raise_parsing_error(error_message)

            date_text = await header.text_content()
            if not date_text:
                error_message = "No text"
                self._raise_parsing_error(error_message)

            # Parse date like "Среда, 13 ноября 2024 г."
            parts = date_text.split(",", 1)
            if len(parts) != self.EXPECTED_DATE_PARTS:
                error_message = f"Invalid date format: {date_text}"
                self._raise_parsing_error(error_message)

            date_str = parts[1].strip()
            # Convert month name to number
            ru_months = {
                "января": 1,
                "февраля": 2,
                "марта": 3,
                "апреля": 4,
                "мая": 5,
                "июня": 6,
                "июля": 7,
                "августа": 8,
                "сентября": 9,
                "октября": 10,
                "ноября": 11,
                "декабря": 12,
            }

            # Split date parts and clean up
            date_parts = date_str.split()
            day = int(date_parts[0])
            month = ru_months[date_parts[1].lower()]
            year = int(date_parts[2])

            return datetime(year, month, day, tzinfo=UTC)

        except Exception as e:
            error_message = f"Failed to parse current date: {e}"
            raise ScrapingError(error_message) from e

    async def navigate_to_date(self, target_date: datetime) -> None:
        """Navigate to specific date in schedule."""
        try:
            date_buttons = await self.page.query_selector_all(".button-day")
            for button in date_buttons:
                date_text = await button.text_content()
                if not date_text:
                    continue

                if "Сегодня" in date_text:
                    current_date = await self._get_current_date()
                    if current_date.date() == target_date.date():
                        await button.click()
                        await self.page.wait_for_load_state("networkidle")
                        return
                else:
                    # Parse button date
                    date_part = date_text.split()[1]
                    day, month = map(int, date_part.split("."))
                    button_date = datetime(target_date.year, month, day, tzinfo=UTC)

                    if button_date.date() == target_date.date():
                        await button.click()
                        await self.page.wait_for_load_state("networkidle")
                        return

            error_message = f"Date {target_date.date()} not found in available dates"
            self._raise_parsing_error(error_message)

        except Exception as e:
            error_message = f"Failed to navigate to date {target_date.date()}"
            raise ScrapingError(error_message) from e

    async def parse_day(self, date: datetime) -> list[ScheduleEvent]:
        """Parse schedule for specific date."""
        try:
            await self.navigate_to_date(date)

            lessons = await self.page.query_selector_all(".lesson")
            events = []

            for lesson in lessons:
                try:
                    event = await self._parse_lesson(lesson, date)
                    events.append(event)
                except ScrapingError as e:
                    self._logger.warning(
                        "Failed to parse lesson", date=date.date(), error=str(e)
                    )
                    continue
        except Exception as e:
            error_message = f"Failed to parse day {date.date()}"
            raise ScrapingError(error_message) from e
        else:
            return events

    def _raise_parsing_error(self, message: str) -> None:
        """Helper method to raise scraping errors."""
        raise ScrapingError(message)

    async def _parse_lesson(self, lesson_el, base_date: datetime) -> ScheduleEvent:
        """Parse single lesson element."""
        try:
            # Get subject
            subject_el = await lesson_el.query_selector("h4")
            if not subject_el:
                error_message = "Subject element not found"
                self._raise_parsing_error(error_message)
            subject = await subject_el.text_content()

            # Get info div
            info_div = await lesson_el.query_selector("div.text-gray")
            if not info_div:
                error_message = "Info div not found"
                self._raise_parsing_error(error_message)

            # Get teacher and lesson type
            flex_divs = await info_div.query_selector_all(".d-flex.flex-wrap")
            if len(flex_divs) < self.MIN_FLEX_CONTAINERS:
                error_message = "Missing flex containers"
                self._raise_parsing_error(error_message)

            teacher_type_spans = await flex_divs[0].query_selector_all("span")
            if len(teacher_type_spans) < self.MIN_TEACHER_TYPE_SPANS:
                error_message = "Missing teacher or type spans"
                self._raise_parsing_error(error_message)

            teacher = await teacher_type_spans[0].text_content()
            lesson_type_text = await teacher_type_spans[1].text_content()

            # Parse time and location
            time_loc_spans = await flex_divs[1].query_selector_all("span")
            if len(time_loc_spans) < self.MIN_TIME_LOCATION_SPANS:
                error_message = "Missing time or location spans"
                self._raise_parsing_error(error_message)

            time_text = await time_loc_spans[0].text_content()
            location_text = await time_loc_spans[1].text_content()

            # Parse time
            start_time, end_time = self._parse_time_range(time_text)

            # Parse location
            location = self._parse_location(location_text)

            return ScheduleEvent(
                subject=subject.strip(),
                teacher=teacher.strip(),
                lesson_type=self._parse_lesson_type(lesson_type_text.strip()),
                location=location,
                start_time=datetime.combine(base_date.date(), start_time),
                end_time=datetime.combine(base_date.date(), end_time),
                group="БИК2404",  # TODO: Make configurable
            )

        except Exception as e:
            error_message = f"Failed to parse lesson: {e}"
            raise ScrapingError(error_message) from e

    def _parse_time_range(self, time_text: str) -> tuple[time, time]:
        """Parse time range from text."""
        try:
            start_str, end_str = time_text.split("–")
            start_time = time.fromisoformat(start_str.strip())
            end_time = time.fromisoformat(end_str.strip())
        except Exception as e:
            error_message = f"Failed to parse time range '{time_text}'"
            raise ScrapingError(error_message) from e
        else:
            return start_time, end_time

    def _parse_location(self, location_text: str) -> Location:
        """Parse location from text."""
        try:
            # Remove prefix
            location_text = location_text.replace("Аудитория:", "").strip()

            # Handle special cases
            if location_text.lower() in ["онлайн", "online"]:
                return Location(building="Online", room="Online")

            if "зал" in location_text.lower():
                return Location(building="Н", room=location_text)

            # Parse standard format (e.g., "Н-226")
            if "-" in location_text:
                building, room = location_text.split("-", 1)
                return Location(building=building.strip(), room=room.strip())

            return Location(building="Н", room=location_text)

        except Exception as e:
            error_message = f"Failed to parse location '{location_text}'"
            raise ScrapingError(error_message) from e

    def _parse_lesson_type(self, type_text: str) -> LessonType:
        """Parse lesson type from text."""
        type_mapping = {
            "Лекция": LessonType.LECTURE,
            "Практическое занятие": LessonType.PRACTICE,
            "Лабораторная работа": LessonType.LAB,
        }

        try:
            return type_mapping[type_text]
        except KeyError:
            error_message = f"Unknown lesson type: {type_text}"
            raise ScrapingError(error_message) from None


class MTUCIScheduleScraper:
    """Main scraper class for MTUCI schedule."""

    def __init__(
        self, auth_config: AuthConfig, max_retries: int = 3, timeout_ms: int = 30000
    ):
        """Initialize scraper with configuration."""
        self.auth_config = auth_config
        self.max_retries = max_retries
        self.timeout_ms = timeout_ms
        self._logger = logger.bind(component="MTUCIScheduleScraper")

    async def _setup_page(self, page: Page) -> None:
        """Setup page and authenticate."""
        try:
            # Authenticate
            authenticator = MTUCIAuthenticator(self.auth_config)
            await authenticator.authenticate(page)

            # Navigate to schedule page
            await page.goto(
                "https://lk.mtuci.ru/student/schedule", timeout=self.timeout_ms
            )
            await page.wait_for_load_state("networkidle")

        except Exception as e:
            error_message = "Failed to setup page"
            self._logger.exception(error_message, error=str(e))
            raise ApplicationError(error_message) from e

    async def parse_schedule(self, page: Page) -> list[ScheduleEvent]:
        """Parse complete schedule."""
        try:
            # Setup page and authenticate
            await self._setup_page(page)

            # Create parser and get available dates
            parser = ScheduleParser(page)
            dates = await parser.get_available_dates()

            self._logger.info(
                "Found available dates", dates=[d.strftime("%Y-%m-%d") for d in dates]
            )

            # Parse schedule for each date
            all_events = []
            for date in dates:
                try:
                    events = await parser.parse_day(date)
                    self._logger.info(
                        "Parsed schedule",
                        date=date.strftime("%Y-%m-%d"),
                        events_count=len(events),
                    )
                    all_events.extend(events)
                except ScrapingError as e:
                    self._logger.exception(
                        "Failed to parse date",
                        date=date.strftime("%Y-%m-%d"),
                        error=str(e),
                    )
                    continue

            return sorted(all_events, key=lambda x: x.start_time)

        except Exception as e:
            error_message = "Schedule parsing failed"
            self._logger.exception(error_message, error=str(e))
            raise ApplicationError(error_message) from e
