# src/calendar/google.py

from http import HTTPStatus
from pathlib import Path

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
