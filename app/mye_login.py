"""MyE-invoice login: reuse the session, autofill user/password, the human types the sum."""
from __future__ import annotations

import logging
import re
from typing import Callable

from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PWTimeout

from app import config

log = logging.getLogger("doc_soporte")


def mye_login(page: Page, notify: Callable[[str], None], wait_ms: int) -> bool:
    """Reuse the open session; otherwise fill user/password and wait for the human.

    The captcha is the only manual step: the cursor is left in the next field after
    the password, so the user just types the sum and presses Enter. The app detects
    the session by itself (no extra click in the app).
    """
    if "entrada.jsp" in page.url:
        return True
    page.goto(config.MYE_LOGIN_URL, wait_until="domcontentloaded", timeout=90_000)
    if "entrada.jsp" in page.url:
        return True
    try:
        pwd = page.locator("input[type=password]:visible").first
        pwd.wait_for(timeout=15_000)
        user = page.locator("input[type=text]:visible, input[type=email]:visible").first
        user.fill(config.get_credential("MYE_USER"))
        pwd.fill(config.get_credential("MYE_PASSWORD"))
        # Leave the cursor in the captcha field (the human types the answer).
        captcha = page.locator(
            "input.jCaptcha:visible, input[placeholder*='Ingresa el resultado']:visible").first
        if captcha.count():
            captcha.scroll_into_view_if_needed()
            captcha.focus()
        else:
            pwd.press("Tab")
        filled = True
    except Exception as exc:  # never stop the batch for autofill: the human can type it
        log.warning("MyE login autofill skipped: %s", exc)
        filled = False
    page.bring_to_front()
    notify("MyE-invoice: escriba el resultado de la suma y pulse Enter en el navegador."
           if filled else
           "MyE-invoice: inicie sesión en el navegador (usuario, contraseña y suma).")
    try:
        page.wait_for_url(re.compile(r"entrada\.jsp"), timeout=wait_ms)
    except PWTimeout as exc:
        log.warning("MyE login not detected: %s", exc)
        return False
    no_thanks = page.get_by_role("button", name=re.compile("No gracias", re.I))
    try:
        no_thanks.first.wait_for(state="visible", timeout=5_000)
        no_thanks.first.click()
    except PWTimeout:
        pass  # the notice does not always appear
    return True
