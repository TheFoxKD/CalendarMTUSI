# src/calendar/google.py

from http import HTTPStatus
from pathlib import Path
from typing import Any

import structlog
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import Resource
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from pydantic import BaseModel
from pydantic import EmailStr

from src.core.exceptions import ApplicationError
from src.models.schedule import LessonType
from src.models.schedule import ScheduleEvent

logger = structlog.get_logger(__name__)


class CalendarErrors:
    SERVICE_NOT_INITIALIZED = "Service not initialized"
    CALENDAR_SETUP_FAILED = "Calendar setup failed"


class CalendarConfig(BaseModel):
    """Google Calendar configuration."""

    credentials_path: str
    token_path: str = "token.json"
    calendar_id: EmailStr
    calendar_name: str = "МТУСИ Расписание"


class GoogleCalendarService:
    """Service for Google Calendar integration."""

    SCOPES = ["https://www.googleapis.com/auth/calendar"]

    def __init__(self, config: CalendarConfig):
        """Initialize calendar service."""
        self.config = config
        self.service: Resource | None = None
        self._logger = logger.bind(calendar_id=config.calendar_id)
        self._calendar_id = config.calendar_id

    @property
    def calendar_id(self) -> str:
        """Get current calendar ID."""
        return self._calendar_id

    def initialize(self) -> None:
        """Initialize the Calendar API service and ensure calendar exists."""
        try:
            creds = None
            token_path = Path(self.config.token_path)

            # Try to load existing token
            if token_path.exists():
                try:
                    creds = Credentials.from_authorized_user_file(
                        str(token_path), self.SCOPES
                    )
                    self._logger.info("Loaded existing credentials")
                except (ValueError, FileNotFoundError) as e:
                    self._logger.warning("Failed to load token", error=str(e))

            # If no valid credentials available, let the user log in
            if not creds or not creds.valid:
                if creds and creds.expired and creds.refresh_token:
                    self._logger.info("Refreshing expired credentials")
                    creds.refresh(Request())
                else:
                    self._logger.info("Starting OAuth2 authorization flow")
                    flow = InstalledAppFlow.from_client_secrets_file(
                        self.config.credentials_path,
                        self.SCOPES,
                        redirect_uri="urn:ietf:wg:oauth:2.0:oob",  # Desktop app flow
                    )
                    creds = flow.run_local_server(port=0)
                    self._logger.info("Authorization completed")

                # Save the credentials for the next run
                with token_path.open("w") as token:
                    token.write(creds.to_json())
                    self._logger.info("Saved new credentials")

            self.service = build("calendar", "v3", credentials=creds)
            self._logger.info("Google Calendar service initialized")

            # Ensure calendar exists
            self._ensure_calendar_exists()

        except Exception as e:
            self._logger.exception(
                "Failed to initialize calendar service", error=str(e)
            )
            raise ApplicationError(
                message="Failed to initialize calendar service", original_error=e
            ) from e

    def _ensure_calendar_exists(self) -> None:
        """Ensure calendar exists, create if it doesn't."""
        if not self.service:
            raise ApplicationError(CalendarErrors.SERVICE_NOT_INITIALIZED)
        try:
            self.service.calendars().get(calendarId=self.calendar_id).execute()
            self._logger.info("Found existing calendar")

        except HttpError as error:
            if error.resp.status == HTTPStatus.NOT_FOUND.value:
                self._logger.info("Calendar not found, creating new one")

                # Create calendar
                calendar_body = {
                    "summary": self.config.calendar_name,
                    "timeZone": "Europe/Moscow",
                }

                created_calendar = (
                    self.service.calendars().insert(body=calendar_body).execute()
                )

                # Update calendar ID
                self._calendar_id = created_calendar["id"]
                self._logger.info("Created new calendar", calendar_id=self._calendar_id)
            else:
                raise
        except Exception as e:
            self._logger.exception("Failed to ensure calendar exists")
            raise ApplicationError(
                CalendarErrors.CALENDAR_SETUP_FAILED, original_error=e
            ) from e

    async def create_event(self, event: ScheduleEvent) -> str:
        """Create a calendar event."""
        if not self.service:
            raise ApplicationError(CalendarErrors.SERVICE_NOT_INITIALIZED)
        try:
            event_body = self._create_event_body(event)
            result = (
                self.service.events()
                .insert(calendarId=self.calendar_id, body=event_body)
                .execute()
            )

            self._logger.info(
                "Created calendar event", event_id=result["id"], subject=event.subject
            )

            return result["id"]

        except HttpError as e:
            self._logger.exception(
                "Failed to create event", subject=event.subject, error=str(e)
            )
            error_message = f"Failed to create event: {e}"
            raise ApplicationError(error_message, original_error=e) from e

    async def create_events(self, events: list[ScheduleEvent]) -> list[str]:
        """Create multiple calendar events."""
        if not events:
            self._logger.warning("No events to create")
            return []

        event_ids = []
        for event in events:
            try:
                event_id = await self.create_event(event)
                event_ids.append(event_id)
            except Exception as e:
                self._logger.exception(
                    "Failed to create event", subject=event.subject, error=str(e)
                )
                continue

        return event_ids

    def _create_event_body(self, event: ScheduleEvent) -> dict[str, Any]:
        """
        Create Google Calendar event body from ScheduleEvent.

        Args:
            event: Schedule event to convert

        Returns:
            Dictionary containing event data in Google Calendar API format

        Example:
            >>> event = ScheduleEvent(
            ...     subject="Math",
            ...     teacher="John Doe",
            ...     lesson_type=LessonType.LECTURE,
            ...     location=Location(building="A", room="101"),
            ...     start_time=datetime(2024, 2, 1, 9, 30),
            ...     end_time=datetime(2024, 2, 1, 11, 0),
            ...     group="BIK2404"
            ... )
            >>> body = calendar_service._create_event_body(event)
            >>> assert "summary" in body
            >>> assert "location" in body
        """
        # Format event summary
        summary = f"{event.subject} ({event.lesson_type.value})"
        if event.subgroup:
            summary += f" - Подгруппа {event.subgroup}"

        # Format description with additional details
        description = (
            f"Преподаватель: {event.teacher}\n"
            f"Тип занятия: {event.lesson_type.value}\n"
            f"Группа: {event.group}"
        )

        # Format location string
        location = str(event.location)

        # Create event body according to Google Calendar API spec
        # https://developers.google.com/calendar/api/v3/reference/events#resource
        event_body = {
            "summary": summary,
            "location": location,
            "description": description,
            "start": {
                "dateTime": event.start_time.isoformat(),
                "timeZone": "Europe/Moscow",
            },
            "end": {
                "dateTime": event.end_time.isoformat(),
                "timeZone": "Europe/Moscow",
            },
            "reminders": {
                "useDefault": False,
                "overrides": [
                    {"method": "popup", "minutes": 15},
                ],
            },
            # Add color based on lesson type
            # https://developers.google.com/calendar/api/v3/reference/colors/get
            "colorId": self._get_event_color(event.lesson_type),
            # Add extended properties for better tracking
            "extendedProperties": {
                "private": {
                    "teacher": event.teacher,
                    "group": event.group,
                    "lessonType": event.lesson_type.value,
                    "subgroup": str(event.subgroup) if event.subgroup else "",
                    "source": "mtuci_sync",
                }
            },
        }

        self._logger.debug(
            "created_event_body",
            summary=summary,
            start=event.start_time.isoformat(),
            end=event.end_time.isoformat(),
        )

        return event_body

    def _get_event_color(self, lesson_type: LessonType) -> str:
        """
        Get Google Calendar color ID for lesson type.

        Args:
            lesson_type: Type of the lesson

        Returns:
            Google Calendar color ID (1-11)

        Note:
            Color IDs reference:
            1: Lavender
            2: Sage
            3: Grape
            4: Flamingo
            5: Banana
            6: Tangerine
            7: Peacock
            8: Graphite
            9: Blueberry
            10: Basil
            11: Tomato
        """
        color_map = {
            LessonType.LECTURE: "9",  # Blueberry
            LessonType.PRACTICE: "10",  # Basil
            LessonType.LAB: "7",  # Peacock
        }
        return color_map.get(lesson_type, "1")
