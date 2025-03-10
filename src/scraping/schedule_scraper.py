# src/scraping/schedule_scraper.py

import asyncio
from datetime import UTC
from datetime import datetime
from datetime import time

import structlog
from playwright.async_api import Error as PlaywrightError
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
            # Add a small delay to ensure the page is ready
            await asyncio.sleep(1)

            date_buttons = await self.page.query_selector_all(".button-day")
            self._logger.debug(f"Found {len(date_buttons)} date buttons")

            for button in date_buttons:
                try:
                    date_text = await button.text_content()
                    if not date_text:
                        continue

                    self._logger.debug(f"Checking date button: {date_text}")

                    if "Сегодня" in date_text:
                        current_date = await self._get_current_date()
                        self._logger.debug(
                            f"Current date is {current_date.date()}, target is {target_date.date()}"
                        )
                        if current_date.date() == target_date.date():
                            await button.click()
                            # Use a less strict wait condition with timeout
                            try:
                                await self.page.wait_for_load_state(
                                    "load", timeout=30000
                                )
                            except Exception as e:
                                self._logger.warning(
                                    f"Wait for load state failed after clicking today button: {e!s}"
                                )
                            return
                    else:
                        # Parse button date
                        try:
                            # Handle different date formats
                            parts = date_text.split()
                            if len(parts) >= 2:
                                date_part = parts[1]
                                if "." in date_part:
                                    day, month = map(int, date_part.split("."))
                                    button_date = datetime(
                                        target_date.year, month, day, tzinfo=UTC
                                    )

                                    self._logger.debug(
                                        f"Button date is {button_date.date()}, target is {target_date.date()}"
                                    )

                                    if button_date.date() == target_date.date():
                                        await button.click()
                                        # Use a less strict wait condition with timeout
                                        try:
                                            await self.page.wait_for_load_state(
                                                "load", timeout=30000
                                            )
                                        except Exception as e:
                                            self._logger.warning(
                                                f"Wait for load state failed after clicking date button: {e!s}"
                                            )
                                        return
                        except Exception as e:
                            self._logger.warning(
                                f"Failed to parse date from button text: {date_text}, error: {e!s}"
                            )
                            continue
                except Exception as e:
                    self._logger.warning(f"Error processing date button: {e!s}")
                    continue

            # Take a screenshot for debugging
            try:
                screenshot_path = f"debug_screenshot_{target_date.date()}.png"
                await self.page.screenshot(path=screenshot_path)
                self._logger.info(f"Saved debug screenshot to {screenshot_path}")
            except Exception as e:
                self._logger.warning(f"Failed to take debug screenshot: {e!s}")

            error_message = f"Date {target_date.date()} not found in available dates"
            self._raise_parsing_error(error_message)

        except Exception as e:
            error_message = f"Failed to navigate to date {target_date.date()}"
            self._logger.exception(error_message, error=str(e))
            raise ScrapingError(error_message) from e

    async def parse_day(self, date: datetime) -> list[ScheduleEvent]:
        """Parse schedule for specific date."""
        try:
            await self.navigate_to_date(date)

            # Add a small delay to ensure the page has updated
            await asyncio.sleep(2)

            # Try to find lessons with a more generous timeout
            try:
                await self.page.wait_for_selector(".lesson", timeout=10000)
            except Exception as e:
                self._logger.warning(f"Wait for lessons failed: {e!s}")

            lessons = await self.page.query_selector_all(".lesson")
            self._logger.info(f"Found {len(lessons)} lessons for {date.date()}")

            events = []

            for lesson in lessons:
                try:
                    event = await self._parse_lesson(lesson, date)
                    events.append(event)
                    self._logger.debug(f"Successfully parsed lesson: {event.subject}")
                except Exception as e:
                    self._logger.warning(
                        "Failed to parse lesson", date=date.date(), error=str(e)
                    )
                    continue

            return events
        except Exception as e:
            error_message = f"Failed to parse day {date.date()}"
            self._logger.exception(error_message, error=str(e))
            raise ScrapingError(error_message) from e

    def _raise_parsing_error(self, message: str) -> None:
        """Helper method to raise scraping errors."""
        raise ScrapingError(message)

    async def _parse_lesson(self, lesson_el, base_date: datetime) -> ScheduleEvent:
        """Parse single lesson element."""
        try:
            # Take a screenshot of the lesson element for debugging
            try:
                await lesson_el.screenshot(path=f"lesson_debug_{base_date.date()}.png")
            except Exception as e:
                self._logger.debug(f"Failed to take lesson screenshot: {e!s}")

            # Get HTML content for debugging
            try:
                html_content = await lesson_el.evaluate("el => el.outerHTML")
                self._logger.debug(f"Lesson HTML: {html_content}")
            except Exception as e:
                self._logger.debug(f"Failed to get lesson HTML: {e!s}")

            # Get subject
            subject_el = await lesson_el.query_selector("h4")
            if not subject_el:
                error_message = "Subject element not found"
                self._logger.warning(error_message)
                self._raise_parsing_error(error_message)
            subject = await subject_el.text_content() or "Неизвестный предмет"

            # Get info div
            info_div = await lesson_el.query_selector(
                "div.lesson-info"
            ) or await lesson_el.query_selector("div.text-gray")
            if not info_div:
                error_message = "Info div not found"
                self._logger.warning(error_message)
                self._raise_parsing_error(error_message)

            # Get teacher and lesson type
            flex_divs = await info_div.query_selector_all(".d-flex.flex-wrap")
            if len(flex_divs) < self.MIN_FLEX_CONTAINERS:
                # Try alternative selectors
                flex_divs = await info_div.query_selector_all("div")
                if len(flex_divs) < self.MIN_FLEX_CONTAINERS:
                    error_message = f"Missing flex containers, found {len(flex_divs)}"
                    self._logger.warning(error_message)
                    self._raise_parsing_error(error_message)

            # Get teacher and lesson type
            teacher = "Неизвестный преподаватель"
            lesson_type_text = "Лекция"  # Default to lecture

            try:
                teacher_type_spans = await flex_divs[0].query_selector_all("span")
                if len(teacher_type_spans) >= self.MIN_TEACHER_TYPE_SPANS:
                    teacher = await teacher_type_spans[0].text_content() or teacher
                    lesson_type_text = (
                        await teacher_type_spans[1].text_content() or lesson_type_text
                    )
                else:
                    # Try to get at least the teacher
                    first_span = await flex_divs[0].query_selector("span")
                    if first_span:
                        teacher = await first_span.text_content() or teacher
            except Exception as e:
                self._logger.warning(f"Error parsing teacher/type: {e!s}")

            # Parse time and location
            time_text = "00:00 – 00:00"  # Default time
            location_text = "Н-000"  # Default location

            try:
                time_loc_spans = await flex_divs[1].query_selector_all("span")
                if len(time_loc_spans) >= self.MIN_TIME_LOCATION_SPANS:
                    time_span = time_loc_spans[0]
                    loc_span = time_loc_spans[1]

                    if time_span:
                        time_content = await time_span.text_content()
                        if time_content and "–" in time_content:
                            time_text = time_content

                    if loc_span:
                        loc_content = await loc_span.text_content()
                        if loc_content:
                            location_text = loc_content
            except Exception as e:
                self._logger.warning(f"Error parsing time/location: {e!s}")

            # Parse time
            try:
                start_time, end_time = self._parse_time_range(time_text)
            except Exception as e:
                self._logger.warning(f"Failed to parse time range '{time_text}': {e!s}")
                # Use default times
                start_time = time(9, 0)
                end_time = time(10, 30)

            # Parse location
            try:
                location = self._parse_location(location_text)
            except Exception as e:
                self._logger.warning(
                    f"Failed to parse location '{location_text}': {e!s}"
                )
                # Use default location
                location = Location(building="Н", room="000")

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
            self._logger.exception(error_message)
            raise ScrapingError(error_message) from e

    def _parse_time_range(self, time_text: str) -> tuple[time, time]:
        """Parse time range from text."""
        try:
            # Clean up the text
            time_text = time_text.strip().replace(" ", "")

            # Handle different separators
            if "–" in time_text:
                separator = "–"
            elif "-" in time_text:
                separator = "-"
            elif "—" in time_text:
                separator = "—"
            else:
                raise ValueError(f"No time separator found in '{time_text}'")

            start_str, end_str = time_text.split(separator)

            # Clean up the time strings
            start_str = start_str.strip()
            end_str = end_str.strip()

            # Try different time formats
            try:
                start_time = time.fromisoformat(start_str)
            except ValueError:
                # Try to parse HH:MM format
                if ":" in start_str:
                    hours, minutes = map(int, start_str.split(":"))
                    start_time = time(hours, minutes)
                else:
                    # Default to 9:00 if parsing fails
                    self._logger.warning(
                        f"Could not parse start time '{start_str}', using default"
                    )
                    start_time = time(9, 0)

            try:
                end_time = time.fromisoformat(end_str)
            except ValueError:
                # Try to parse HH:MM format
                if ":" in end_str:
                    hours, minutes = map(int, end_str.split(":"))
                    end_time = time(hours, minutes)
                else:
                    # Default to 10:30 if parsing fails
                    self._logger.warning(
                        f"Could not parse end time '{end_str}', using default"
                    )
                    end_time = time(10, 30)

            # Validate that end time is after start time
            if end_time <= start_time:
                self._logger.warning(
                    f"End time {end_time} is not after start time {start_time}, using default"
                )
                start_time = time(9, 0)
                end_time = time(10, 30)

            return start_time, end_time

        except Exception as e:
            error_message = f"Failed to parse time range '{time_text}': {e!s}"
            self._logger.warning(error_message)
            # Return default times
            return time(9, 0), time(10, 30)

    def _parse_location(self, location_text: str) -> Location:
        """Parse location from text."""
        try:
            # Clean up the text
            location_text = location_text.strip()

            # Remove prefix if present
            prefixes = ["Аудитория:", "Ауд.", "Ауд:", "Аудитория", "Аудитория "]
            for prefix in prefixes:
                if location_text.startswith(prefix):
                    location_text = location_text.replace(prefix, "", 1).strip()
                    break

            # Handle special cases
            if location_text.lower() in ["онлайн", "online"]:
                return Location(building="Online", room="Online")

            if "зал" in location_text.lower():
                return Location(building="Н", room=location_text)

            if "спортивный" in location_text.lower():
                return Location(building="Н", room="Спортивный зал")

            # Try to parse building and room
            # Common format is "Н-123" or "А-123"
            if "-" in location_text:
                building, room = location_text.split("-", 1)
                building = building.strip()
                room = room.strip()

                # Validate building
                if building not in ["Н", "А"]:
                    building = "Н"  # Default to Н

                return Location(building=building, room=room)
            # If no building specified, assume it's in the default building
            return Location(building="Н", room=location_text)

        except Exception as e:
            error_message = f"Failed to parse location '{location_text}': {e!s}"
            self._logger.warning(error_message)
            # Return default location
            return Location(building="Н", room="000")

    def _parse_lesson_type(self, type_text: str) -> LessonType:
        """Parse lesson type from text."""
        # Clean up the text
        type_text = type_text.strip()

        # Direct mapping
        type_mapping = {
            "Лекция": LessonType.LECTURE,
            "Практическое занятие": LessonType.PRACTICE,
            "Практика": LessonType.PRACTICE,
            "Лабораторная работа": LessonType.LAB,
            "Лабораторная": LessonType.LAB,
        }

        # Try direct match first
        if type_text in type_mapping:
            return type_mapping[type_text]

        # Try partial matching
        type_text_lower = type_text.lower()
        if "лекц" in type_text_lower:
            return LessonType.LECTURE
        if "практ" in type_text_lower:
            return LessonType.PRACTICE
        if "лаб" in type_text_lower:
            return LessonType.LAB

        # Log warning and default to lecture
        self._logger.warning(f"Unknown lesson type: {type_text}, defaulting to lecture")
        return LessonType.LECTURE


class MTUCIScheduleScraper:
    """Main scraper class for MTUCI schedule."""

    def __init__(
        self, auth_config: AuthConfig, max_retries: int = 5, timeout_ms: int = 60000
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

            # Use the authenticator's navigate_to_schedule method which has improved retry logic
            await authenticator.navigate_to_schedule(page)

            # Additional waiting for dynamic content with a more generous timeout
            try:
                # Try a less strict wait condition first
                await page.wait_for_load_state("load", timeout=self.timeout_ms)
                self._logger.info("Page load state reached")

                # Then try networkidle, but don't fail if it times out
                try:
                    await page.wait_for_load_state(
                        "networkidle", timeout=self.timeout_ms // 2
                    )
                    self._logger.info("Page fully loaded (networkidle state reached)")
                except PlaywrightError as e:
                    # Log but don't fail if this times out - the page might still be usable
                    self._logger.warning(
                        "Networkidle state not reached, continuing anyway", error=str(e)
                    )

                # Verify we can see schedule elements
                schedule_elements = [
                    ".schedule-month",
                    ".button-day",
                    ".schedule-lessons",
                    "h4.current-day",
                ]

                for selector in schedule_elements:
                    try:
                        element = await page.query_selector(selector)
                        if element:
                            self._logger.debug(f"Found schedule element: {selector}")
                            break
                    except PlaywrightError:
                        continue

            except PlaywrightError as e:
                # Even if waiting for load state fails, try to continue
                self._logger.warning(
                    "Load state not reached, attempting to continue", error=str(e)
                )

                # Add a small delay to allow for any dynamic content to load
                await asyncio.sleep(2)

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
