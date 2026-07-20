from __future__ import annotations

import logging
import os
from urllib.parse import urlparse

from playwright.sync_api import Page

from config import Config

log = logging.getLogger(__name__)

LOGIN_URL = "https://www.screener.in/login/"
LOGIN_FAILURE_SCREENSHOT_NAME = "login_failure.png"


def _still_on_login_page(page: Page) -> bool:
    return urlparse(page.url).path.rstrip("/") == "/login"


def perform_login(page: Page, config: Config) -> None:
    page.goto(LOGIN_URL, wait_until="domcontentloaded")
    page.fill("#id_username", config.screener_email)
    page.fill("#id_password", config.screener_password)
    page.locator('form[action="/login/"] button[type=submit]').click()
    page.wait_for_load_state("domcontentloaded")

    if _still_on_login_page(page):
        error_text = ""
        try:
            error_text = page.locator(".errorlist, .alert, .non-field-errors").first.inner_text(timeout=1000)
        except Exception:
            pass

        screenshot_path = os.path.join(config.data_dir, LOGIN_FAILURE_SCREENSHOT_NAME)
        try:
            os.makedirs(config.data_dir, exist_ok=True)
            page.screenshot(path=screenshot_path, full_page=True)
            log.error("Login failure screenshot saved to %s", screenshot_path)
        except Exception as screenshot_error:
            log.warning("Could not save login failure screenshot: %s", screenshot_error)

        raise RuntimeError(
            "Screener login failed - still on the login page after submitting credentials. "
            f"Check SCREENER_EMAIL/SCREENER_PASSWORD. Screenshot saved to {screenshot_path}."
            f"{(' Site said: ' + error_text) if error_text else ''}"
        )

    log.info("Login successful")
