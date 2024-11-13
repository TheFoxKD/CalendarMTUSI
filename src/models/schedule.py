# src/models/schedule.py
from datetime import datetime
from enum import Enum

from pydantic import BaseModel
from pydantic import Field


class ModelValidationError(ValueError):
    """Base validation error."""

    EMPTY_BUILDING = "Building cannot be empty"
    EMPTY_ROOM = "Room cannot be empty"
    EMPTY_SUBJECT = "Subject cannot be empty"
    EMPTY_TEACHER = "Teacher cannot be empty"
    INVALID_END_TIME = "End time must be after start time"
    INVALID_WEEK = "Week number must be between 1 and 52"


class LessonType(str, Enum):
    """Type of the lesson."""

    LECTURE = "Лекция"
    PRACTICE = "Практическое занятие"
    LAB = "Лабораторная работа"


class Location(BaseModel):
    """Location of the lesson."""

    building: str = Field(..., description="Building number or name")
    room: str = Field(..., description="Room number")

    def __str__(self) -> str:
        """String representation of location."""
        return f"{self.building}, ауд. {self.room}"


class ScheduleEvent(BaseModel):
    """Schedule event model representing a single lesson."""

    subject: str = Field(..., description="Name of the subject")
    teacher: str = Field(..., description="Name of the teacher")
    lesson_type: LessonType = Field(..., description="Type of the lesson")
    location: Location = Field(..., description="Location of the lesson")
    start_time: datetime = Field(..., description="Start time of the lesson")
    end_time: datetime = Field(..., description="End time of the lesson")
    group: str = Field(..., description="Student group")
    subgroup: int | None = Field(None, description="Subgroup number if applicable")

    class Config:
        """Pydantic model configuration."""

        json_schema_extra = {
            "example": {
                "subject": "Высшая математика",
                "teacher": "Лакерник Александр Рафаилович",
                "lesson_type": "Лекция",
                "location": {"building": "Н", "room": "310"},
                "start_time": "2024-02-12T09:30:00",
                "end_time": "2024-02-12T11:05:00",
                "group": "БИК2404",
                "subgroup": 1,
            }
        }


class WeekSchedule(BaseModel):
    """Weekly schedule containing multiple events."""

    events: list[ScheduleEvent] = Field(default_factory=list)
    week_number: int = Field(..., ge=1, le=52, description="Week number")
    is_even_week: bool = Field(..., description="Whether this is an even week")

    def add_event(self, event: ScheduleEvent) -> None:
        """Add event to schedule."""
        self.events.append(event)
        # Sort events by start time
        self.events.sort(key=lambda x: x.start_time)

    def get_events_for_day(self, date: datetime) -> list[ScheduleEvent]:
        """Get all events for specific date."""
        return [
            event for event in self.events if event.start_time.date() == date.date()
        ]

    class Config:
        """Pydantic model configuration."""

        json_schema_extra = {
            "example": {"events": [], "week_number": 1, "is_even_week": False}
        }
