# src/scraping/auth.py
import asyncio

import structlog
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page
from playwright.async_api import TimeoutError
from pydantic import BaseModel
from pydantic import EmailStr

from src.core.exceptions import ApplicationError

logger = structlog.get_logger(__name__)


class AuthConfig(BaseModel):
    """Authentication configuration."""

    email: EmailStr
    password: str
    login_url: str = "https://lk.mtuci.ru/auth/login"


class AuthenticationError(Exception):
    """Raised when authentication fails."""

    SUBMIT_BUTTON_NOT_FOUND = "Submit button not found"
    FORM_ELEMENT_NOT_FOUND = "Login form element not found: {}"
    FAILED_USERNAME = "Failed to fill username"
    FAILED_AUTH_STATE = "Failed to verify authentication state"
    AUTH_TIMEOUT = "Authentication timeout"
    AUTH_FAILED = "Authentication failed"
    LOGIN_FAILED = "Login failed: {}"


class AuthValidationError(Exception):
    """Raised when form validation fails."""

    def __init__(self, field_name: str, error: Exception) -> None:
        self.field_name = field_name
        self.error = error
        super().__init__(f"Failed to validate {field_name}: {error}")


class MTUCIAuthenticator:
    """Handle authentication to MTUCI personal account."""

    def __init__(self, config: AuthConfig) -> None:
        """Initialize authenticator with config."""
        self.config = config
        self._logger = logger.bind(email=config.email)

    async def _show_status(self, page: Page, message: str) -> None:
        """Show status message on page."""
        js_code = """
            (message) => {
                let status = document.getElementById('auth-status');
                if (!status) {
                    status = document.createElement('div');
                    status.id = 'auth-status';
                    status.style.cssText = `
                        position: fixed;
                        top: 20px;
                        left: 20px;
                        background: rgba(0, 0, 0, 0.8);
                        color: white;
                        padding: 15px 20px;
                        border-radius: 5px;
                        z-index: 9999;
                        font-family: Arial, sans-serif;
                        font-size: 14px;
                        box-shadow: 0 2px 10px rgba(0,0,0,0.3);
                    `;
                    document.body.appendChild(status);
                }
                status.textContent = message;
            }
        """
        await page.evaluate(js_code, message)

    async def _check_login_form(self, page: Page) -> bool:
        """Check if login form is present."""
        login_form = await page.query_selector("#kc-form-login")
        return bool(login_form)

    async def _check_auth_elements(self, page: Page) -> bool:
        """Check for authentication elements."""
        auth_elements = [
            "#username",
            "#password",
            "#login-submit-button",
            "#kc-page-title",
        ]
        for selector in auth_elements:
            if await page.query_selector(selector):
                self._logger.debug("Auth element found", selector=selector)
                return True
        return False

    async def _check_success_indicators(self, page: Page) -> bool:
        """Check for successful authentication indicators."""
        indicators = [
            ".user-profile",
            ".logout-button",
            "#schedule-container",
            ".student-info",
        ]
        for indicator in indicators:
            if await page.query_selector(indicator):
                self._logger.debug("Success indicator found", indicator=indicator)
                return True
        return False

    async def _check_page_title(self, page: Page) -> bool:
        """Check if page title indicates authentication."""
        title = await page.title()
        auth_titles = ["Личный кабинет", "Расписание", "Профиль"]
        return any(x in title for x in auth_titles)

    async def _check_auth_state(self, page: Page) -> bool:
        """Check if already authenticated."""
        try:
            # First check - look for the main layout elements
            layout_elements = [
                "#side-menu",
                ".user-panel",
                "#main-menu",
            ]

            for selector in layout_elements:
                if await page.query_selector(selector):
                    self._logger.info(
                        "Found authenticated layout element", selector=selector
                    )
                    return True

            # Second check - verify we're not on the login page
            login_form = await page.query_selector("#kc-form-login")
            if login_form:
                self._logger.debug("Login form found, not authenticated")
                return False

            # Third check - look for the username in the header
            try:
                username_el = await page.query_selector(".user-panel h4")
                if username_el:
                    self._logger.info("Found username element")
                    return True
            except PlaywrightError as e:
                self._logger.warning("Error checking auth state", error=str(e))
                return False
            else:
                return False

        except PlaywrightError as e:
            self._logger.warning("Error checking auth state", error=str(e))
            return False

    async def navigate_to_schedule(self, page: Page) -> None:
        """Navigate to schedule page with retry logic."""
        max_retries = 5  # Increased from 3 to 5
        base_delay = 1  # seconds
        max_timeout = 60000  # Increased from 30000 to 60000 (60 seconds)

        for attempt in range(max_retries):
            try:
                self._logger.info(
                    f"Navigating to schedule page (attempt {attempt+1}/{max_retries})"
                )

                # Use progressively less strict wait conditions with each retry
                if attempt < 2:
                    wait_condition = "domcontentloaded"  # Fastest, least strict
                    current_timeout = max_timeout
                elif attempt < 4:
                    wait_condition = (
                        "load"  # Medium, waits for resources but not network idle
                    )
                    current_timeout = max_timeout
                else:
                    wait_condition = (
                        "networkidle"  # Strictest, waits for network to be idle
                    )
                    current_timeout = max_timeout * 2

                self._logger.info(
                    f"Using wait condition: {wait_condition} with timeout: {current_timeout}ms"
                )

                # Navigate to schedule page
                await page.goto(
                    "https://lk.mtuci.ru/student/schedule",
                    wait_until=wait_condition,
                    timeout=current_timeout,
                )

                # Add a small delay to allow for any dynamic content to load
                await asyncio.sleep(1)

                # Check if we're actually on the schedule page
                if await self._verify_schedule_page(page):
                    self._logger.info("Successfully navigated to schedule page")
                    return
                # Try a direct approach if verification fails
                if attempt >= 3:
                    self._logger.info("Trying direct element interaction approach")
                    try:
                        # Try clicking on schedule link if available
                        schedule_link = await page.query_selector(
                            "a[href='/student/schedule']"
                        )
                        if schedule_link:
                            await schedule_link.click()
                            await asyncio.sleep(2)
                            if await self._verify_schedule_page(page):
                                self._logger.info(
                                    "Successfully navigated to schedule page via link click"
                                )
                                return
                    except PlaywrightError as e:
                        self._logger.warning(f"Direct interaction failed: {e!s}")

                self._logger.warning(
                    f"Schedule page verification failed (attempt {attempt+1})"
                )

            except PlaywrightError as e:
                # Log the error and retry
                self._logger.warning(
                    f"Error navigating to schedule page (attempt {attempt+1}/{max_retries})",
                    error=str(e),
                )

                # If this is the last attempt, raise the error
                if attempt == max_retries - 1:
                    self._logger.error(
                        "Max retries exceeded for schedule navigation", error=str(e)
                    )
                    raise

            # Exponential backoff: wait longer with each retry
            delay = base_delay * (2**attempt)
            self._logger.info(f"Retrying in {delay} seconds...")
            await asyncio.sleep(delay)

        # If we've exhausted retries, raise an error
        raise AuthenticationError(AuthenticationError.AUTH_TIMEOUT)

    async def _verify_schedule_page(self, page: Page) -> bool:
        """Verify that we're on the schedule page."""
        try:
            # Look for elements that indicate we're on the schedule page
            schedule_indicators = [
                ".schedule-month",  # Month selector
                ".button-day",  # Day buttons
                ".schedule-lessons",  # Lessons container
                "schedule-page",  # The component itself
                "h4.current-day",  # Current day header
                ".lessons-tabs",  # Tabs for different schedule views
            ]

            for selector in schedule_indicators:
                element = await page.query_selector(selector)
                if element:
                    self._logger.debug(f"Found schedule indicator: {selector}")
                    return True

            # Check URL as a fallback
            current_url = page.url
            if "schedule" in current_url:
                self._logger.debug(f"URL contains 'schedule': {current_url}")
                return True

            # If we reach here, we didn't find any of the indicators
            self._logger.warning(
                "Schedule page verification failed, no indicators found"
            )
            return False

        except PlaywrightError as e:
            self._logger.warning("Error checking schedule page", error=str(e))
            return False

    async def _setup_page(self, page: Page) -> None:
        """Setup page and authenticate."""
        try:
            # Authenticate
            await self.authenticate(page)

            # Navigate to schedule with retry logic
            await self.navigate_to_schedule(page)

            # Additional waiting for dynamic content with more generous timeout
            try:
                await page.wait_for_load_state("networkidle", timeout=30000)
                self._logger.info("Page fully loaded (networkidle state reached)")
            except PlaywrightError as e:
                # Log but don't fail if this times out - the page might still be usable
                self._logger.warning(
                    "Networkidle state not reached, continuing anyway", error=str(e)
                )

        except Exception as e:
            error_message = "Failed to setup page"
            self._logger.exception(error_message, error=str(e))
            raise ApplicationError(error_message) from e

    def _raise_validation_error(self, field: str, error: Exception) -> None:
        """Raise validation error with context."""
        raise AuthValidationError(field, error)

    def _raise_auth_error(
        self, template: str, *args: str, error: Exception | None = None
    ) -> None:
        """Raise authentication error with formatted message."""
        message = template.format(*args)
        if error is not None:
            raise AuthenticationError(message) from error
        raise AuthenticationError(message)

    async def _fill_form_field(
        self,
        page: Page,
        selector: str,
        value: str,
        field_name: str,
        max_retries: int = 3,
    ) -> None:
        """Fill form field with retry logic."""
        for attempt in range(max_retries):
            try:
                field = await page.query_selector(selector)
                if field:
                    await field.click()
                    await page.keyboard.press("Control+A")
                    await page.keyboard.press("Backspace")
                    await field.fill(value)
                    return
            except PlaywrightError as e:
                if attempt == max_retries - 1:
                    self._raise_validation_error(field_name, e)
                await asyncio.sleep(1)

    async def _verify_form_elements(self, page: Page) -> None:
        """Verify all form elements are present."""
        form_elements = {
            "username": "#username",
            "password": "#password",
            "submit": "#login-submit-button",
        }

        for name, selector in form_elements.items():
            try:
                await page.wait_for_selector(selector, state="visible", timeout=10_000)
            except TimeoutError as e:
                self._raise_auth_error(
                    AuthenticationError.FORM_ELEMENT_NOT_FOUND, name, error=e
                )

    async def _check_error_messages(self, page: Page) -> str | None:
        """Check for error messages after login attempt."""
        error_selectors = [
            ".alert-error",
            ".alert-danger",
            "#error-message",
            ".kc-feedback-text",
        ]

        for selector in error_selectors:
            error_el = await page.query_selector(selector)
            if error_el:
                return await error_el.text_content()
        return None

    async def _handle_form_submission(self, page: Page) -> None:
        """Handle form submission and validation."""
        submit_button = await page.query_selector("#login-submit-button")
        if not submit_button:
            self._raise_auth_error(AuthenticationError.SUBMIT_BUTTON_NOT_FOUND)

        await submit_button.click()

    async def _validate_auth_result(self, page: Page) -> None:
        """Validate authentication result."""
        if error_msg := await self._check_error_messages(page):
            self._raise_auth_error(AuthenticationError.LOGIN_FAILED, error_msg)

        await asyncio.sleep(2)
        if not await self._check_auth_state(page):
            self._raise_auth_error(AuthenticationError.FAILED_AUTH_STATE)

    async def authenticate(self, page: Page) -> None:
        """
        Authenticate to MTUCI personal account.

        Args:
            page: Playwright page object

        Raises:
            AuthenticationError: If authentication fails
            AuthValidationError: If form validation fails
        """
        try:
            if await self._check_auth_state(page):
                self._logger.info("Already authenticated")
                return

            self._logger.info("Starting authentication")
            await self._show_status(page, "Начинаем процесс авторизации...")

            # Navigate and verify form
            await page.goto(self.config.login_url)
            await page.wait_for_load_state("networkidle")
            await self._verify_form_elements(page)

            # Fill form
            await self._fill_form_field(
                page, "#username", self.config.email, "username"
            )
            await self._fill_form_field(
                page, "#password", self.config.password, "password"
            )

            # Submit and validate
            async with page.expect_navigation(timeout=20_000) as navigation:
                await self._handle_form_submission(page)
                await navigation.value

            await self._validate_auth_result(page)

            await self._show_status(page, "Авторизация успешна!")
            self._logger.info("Authentication successful")

        except TimeoutError as e:
            self._logger.exception("Authentication timeout")
            await self._show_status(page, "Ошибка: превышено время ожидания")
            self._raise_auth_error(AuthenticationError.AUTH_TIMEOUT, error=e)

        except AuthValidationError:
            raise

        except Exception as e:
            self._logger.exception("Authentication failed")
            await self._show_status(page, "Ошибка авторизации")
            self._raise_auth_error(AuthenticationError.AUTH_FAILED, error=e)
