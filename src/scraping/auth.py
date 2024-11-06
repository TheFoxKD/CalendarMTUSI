# src/scraping/auth.py
import asyncio

import structlog
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page
from playwright.async_api import TimeoutError
from pydantic import BaseModel
from pydantic import EmailStr

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
        """
        Check if already authenticated by analyzing page content.

        Args:
            page: Playwright page object

        Returns:
            bool: True if authenticated, False otherwise
        """
        try:
            if await self._check_login_form(page):
                self._logger.debug("Login form found")
                return False

            if await self._check_auth_elements(page):
                return False

            if await self._check_success_indicators(page):
                return True

            if await self._check_page_title(page):
                self._logger.debug("Authenticated page title found")
                return True

            self._logger.warning("Ambiguous auth state")

        except PlaywrightError as e:
            self._logger.warning("Failed to check auth state", error=str(e))
            return False
        else:
            return False

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
