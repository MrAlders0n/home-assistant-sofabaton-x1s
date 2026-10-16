"""Passive browser tabs on the HA sidebar panel (plan 1a).

Headless Chromium with the bench's long-lived token in the frontend's
``hassTokens`` storage (no password). Each tab opens the Control Panel on
the hub under test and is then only READ (``ui_reader.js``); the only
interactions are picking the hub and the Activities/Devices sub-tab.

Service workers are blocked: on a first visit HA installs its service
worker, which takes over a few seconds later and reloads the page (seen
2026-10-16; the reload comes from HA's own app.js, not from us). A real
user's browser has it installed already.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import ha_bench as hb
from playwright.sync_api import Browser, Page, Playwright, sync_playwright

READER = (Path(__file__).with_name("ui_reader.js")).read_text(encoding="utf-8")
PAGES = ("Activities", "Devices")


def _init_script() -> str:
    origin = hb.BASE_URL
    tokens = {"hassUrl": origin, "clientId": origin + "/", "expires": 4102444800000, "expires_in": 31536000,
              "refresh_token": "", "access_token": hb.TOKEN, "token_type": "Bearer"}
    return ("try { if (!localStorage.getItem('hassTokens')) localStorage.setItem('hassTokens', %s); } catch (e) {}"
            % json.dumps(json.dumps(tokens)))


def new_context(pw: Playwright, *, headed: bool = False) -> tuple[Browser, Any]:
    browser = pw.chromium.launch(headless=not headed)
    ctx = browser.new_context(ignore_https_errors=True, viewport={"width": 1200, "height": 900},
                              service_workers="block")
    ctx.add_init_script(_init_script())
    return browser, ctx


_SELECT_HUB = """(entry) => { const f = (r) => { if (!r) return null; const h = r.querySelector('sofabaton-x-panel');
   if (h) return h; for (const e of r.querySelectorAll('*')) if (e.shadowRoot) { const x = f(e.shadowRoot); if (x) return x; }
   return null; }; const p = f(document); if (p && p.selectedHub !== entry) p.selectHub(entry); }"""


def open_panel(page: Page, hub: hb.Hub, tab: str, sub: str | None) -> None:
    """Pick the hub (the panel's own ``selectHub``), the Control Panel, the
    main ``tab`` (Hub / Automation) and the ``sub`` tab; wait until it has
    rendered."""

    deadline = time.time() + 45
    while time.time() < deadline:
        page.evaluate(_SELECT_HUB, hub.entry_id)
        state = UiObserver.read_page(page)
        if state and state.get("view") != "Control Panel":
            page.locator("sofabaton-x-panel .tab.panel").click()
        elif state and state.get("tab") and state.get("tab") != tab:
            page.locator("sofabaton-x-panel sofabaton-control-panel .tab-btn", has_text=tab).first.click()
        elif state and sub and state.get("sub") and state.get("sub") != sub:
            page.locator("sofabaton-x-panel sofabaton-control-panel .secondary-tab-btn", has_text=sub).first.click()
        elif state and state.get("hub") == hub.name and state.get("tab") == tab and (
                sub is None or (state.get("sub") == sub and (tab != "Hub" or state.get("entities")))):
            return
        page.wait_for_timeout(500)
    raise RuntimeError(f"tab did not reach {hub.name} / {tab} / {sub}")


class UiObserver:
    def __init__(self, hub: hb.Hub, *, headed: bool = False) -> None:
        self.hub = hub
        self.headed = headed
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self.pages: dict[str, Page] = {}

    def start(self) -> UiObserver:
        self._pw = sync_playwright().start()
        self._browser, ctx = new_context(self._pw, headed=self.headed)
        for sub in PAGES:
            page = ctx.new_page()
            page.goto(hb.BASE_URL + "/sofabaton-x/control-panel", wait_until="domcontentloaded", timeout=60000)
            page.locator("sofabaton-x-panel").wait_for(timeout=30000)
            self._open(page, sub)
            self.pages[sub] = page
        return self

    def _open(self, page: Page, sub: str) -> None:
        open_panel(page, self.hub, "Hub", sub)

    @staticmethod
    def read_page(page: Page) -> dict[str, Any] | None:
        try:
            return page.evaluate(READER)
        except Exception:  # noqa: BLE001 - a page mid-render reads as absent
            return None

    def read(self) -> dict[str, dict[str, Any] | None]:
        return {name: self.read_page(page) for name, page in self.pages.items()}

    def screenshot(self, folder: Path, stem: str) -> list[str]:
        paths = []
        for name, page in self.pages.items():
            path = folder / f"{stem}_{name.lower()}.png"
            try:
                page.screenshot(path=str(path))
                paths.append(path.name)
            except Exception:  # noqa: BLE001
                pass
        return paths

    def close(self) -> None:
        try:
            if self._browser:
                self._browser.close()
        finally:
            if self._pw:
                self._pw.stop()
