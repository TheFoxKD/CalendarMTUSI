# src/core/exceptions.py


class ApplicationError(Exception):
    """Base exception for all application errors."""

    def __init__(self, message: str, original_error: Exception | None = None):
        self.message = message
        self.original_error = original_error
        super().__init__(self.message)


class ScrapingError(ApplicationError):
    """Errors related to web scraping."""

    SCHEDULE_NOT_FOUND = "Schedule container not found on page"
    SUBJECT_NOT_FOUND = "Subject element not found in lesson"
    INFO_DIV_NOT_FOUND = "Information div not found in lesson"


class ValidationError(ApplicationError):
    """Data validation errors."""

    EMPTY_FIELD = "Required field cannot be empty: {field}"
    INVALID_RANGE = "{field} must be between {min} and {max}"


class InitializationError(ApplicationError):
    """Raised when application initialization fails."""

    def __init__(self, original_error: Exception | None = None) -> None:
        """Initialize initialization error."""
        super().__init__("Application initialization failed", original_error)


class BrowserSetupError(ApplicationError):
    """Raised when browser setup fails."""

    def __init__(self, original_error: Exception | None = None) -> None:
        """Initialize browser setup error."""
        super().__init__("Browser setup failed", original_error)


# Error messages
SETUP_FAILED = "Failed to setup browser"
SYNC_FAILED = "Schedule sync failed"
