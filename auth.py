from __future__ import annotations

import logging
import os

from playwright.sync_api import BrowserContext, Page

from config import Config

log = logging.getLogger(__name__)

LOGIN_URL = "https://www.screener.in/login/"
# Requires auth - unauthenticated visitors get redirected to /login/?next=/watchlist/
PROBE_URL = "https://www.screener.in/watchlist/"


def new_context(browser, config: Config) -> BrowserContext:
    if os.path.exists(config.storage_state_path):
        log.info("Reusing existing session from %s", config.storage_state_path)
        return browser.new_context(storage_state=config.storage_state_path)
    log.info("No saved session found at %s - will log in fresh", config.storage_state_path)
    return browser.new_context()


def _looks_logged_out(page: Page) -> bool:
    return "/login/" in page.url


def ensure_logged_in(context: BrowserContext, page: Page, config: Config) -> None:
    page.goto(PROBE_URL, wait_until="domcontentloaded")
    if not _looks_logged_out(page):
        log.info("Session is valid, already logged in")
        return
    log.info("Session missing or expired - logging in")
    perform_login(context, page, config)


def perform_login(context: BrowserContext, page: Page, config: Config) -> None:
    page.goto(LOGIN_URL, wait_until="domcontentloaded")
    page.fill("#id_username", config.screener_email)
    page.fill("#id_password", config.screener_password)
    page.locator('form[action="/login/"] button[type=submit]').click()
    page.wait_for_load_state("domcontentloaded")

    if _looks_logged_out(page):
        error_text = ""
        try:
            error_text = page.locator(".errorlist, .alert, .non-field-errors").first.inner_text(timeout=1000)
        except Exception:
            pass
        raise RuntimeError(
            "Screener login failed - still on the login page after submitting credentials. "
            f"Check SCREENER_EMAIL/SCREENER_PASSWORD.{(' Site said: ' + error_text) if error_text else ''}"
        )

    context.storage_state(path=config.storage_state_path)
    log.info("Login successful, session saved to %s", config.storage_state_path)
