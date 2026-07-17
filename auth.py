from __future__ import annotations

import logging
import os
from urllib.parse import urlparse

from playwright.sync_api import BrowserContext, Page

from config import Config

log = logging.getLogger(__name__)

LOGIN_URL = "https://www.screener.in/login/"
# Requires auth - unauthenticated visitors get redirected to /login/?next=/watchlist/
PROBE_URL = "https://www.screener.in/watchlist/"
LOGIN_FAILURE_SCREENSHOT_NAME = "login_failure.png"


def new_context(browser, config: Config) -> BrowserContext:
    if os.path.exists(config.storage_state_path):
        log.info("Reusing existing session from %s", config.storage_state_path)
        return browser.new_context(storage_state=config.storage_state_path)
    log.info("No saved session found at %s - will log in fresh", config.storage_state_path)
    return browser.new_context()


def _probe_shows_logged_in(page: Page) -> bool:
    # Fail closed: only trust it if we actually stayed on the authenticated
    # page's path. Anonymous visitors get redirected elsewhere - historically
    # to /login/?next=..., but as observed in production, screener.in
    # currently sends them to /register/?next=/watchlist/ instead - and a
    # naive substring check on the full URL is fooled by that "next=" query
    # param containing "/watchlist/" itself. Compare the path only.
    return urlparse(page.url).path.rstrip("/") == "/watchlist"


def _still_on_login_page(page: Page) -> bool:
    return urlparse(page.url).path.rstrip("/") == "/login"


def ensure_logged_in(context: BrowserContext, page: Page, config: Config) -> None:
    page.goto(PROBE_URL, wait_until="domcontentloaded")
    if _probe_shows_logged_in(page):
        log.info("Session is valid, already logged in")
        return
    log.info("Not logged in (probe redirected to %s) - logging in", page.url)
    perform_login(context, page, config)


def perform_login(context: BrowserContext, page: Page, config: Config) -> None:
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

    context.storage_state(path=config.storage_state_path)
    log.info("Login successful, session saved to %s", config.storage_state_path)
