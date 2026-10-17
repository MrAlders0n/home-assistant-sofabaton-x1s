"""The acting browser tab: the one that drives the UI (plan P1b).

Playwright's sync API belongs to the thread that started it, and the
runner samples the passive tabs on the main thread while an action runs.
So the acting tab lives in its own thread with its own Playwright and
browser; an action hands it a callable and waits for the result.

The acting tab is a real user's tab: it is also read after each step
(question 3 for the tab that made the change).
"""

from __future__ import annotations

import queue
import threading
from concurrent.futures import Future
from pathlib import Path
from typing import Any, Callable

import ha_bench as hb
from playwright.sync_api import Page, sync_playwright

from .ui import UiObserver, new_context, open_panel

_STOP = object()


class Actor:
    def __init__(self, hub: hb.Hub, *, headed: bool = False) -> None:
        self.hub = hub
        self.headed = headed
        self._jobs: queue.Queue = queue.Queue()
        self._ready: Future = Future()
        self._thread = threading.Thread(target=self._main, name="actor", daemon=True)

    def start(self) -> Actor:
        self._thread.start()
        self._ready.result(timeout=120)
        return self

    def _main(self) -> None:
        try:
            pw = sync_playwright().start()
            browser, ctx = new_context(pw, headed=self.headed)
            page = ctx.new_page()
            page.goto(hb.BASE_URL + "/sofabaton-x/control-panel", wait_until="domcontentloaded", timeout=60000)
            page.locator("sofabaton-x-panel").wait_for(timeout=30000)
            open_panel(page, self.hub, "Hub", "Activities")
        except Exception as err:  # noqa: BLE001
            self._ready.set_exception(err)
            return
        self._ready.set_result(True)
        try:
            while True:
                job = self._jobs.get()
                if job is _STOP:
                    break
                fn, fut = job
                try:
                    fut.set_result(fn(page))
                except Exception as err:  # noqa: BLE001
                    # A failed step must not leave a dialog over the next one.
                    try:
                        from .actions.ui_wifi import dismiss_dialogs
                        dismiss_dialogs(page)
                    except Exception:  # noqa: BLE001
                        pass
                    fut.set_exception(err)
        finally:
            browser.close()
            pw.stop()

    def run(self, fn: Callable[[Page], Any], timeout: float = 900.0) -> Any:
        fut: Future = Future()
        self._jobs.put((fn, fut))
        return fut.result(timeout=timeout)

    def read(self) -> dict[str, Any] | None:
        return self.run(UiObserver.read_page, timeout=30)

    def screenshot(self, path: Path) -> None:
        self.run(lambda page: page.screenshot(path=str(path)), timeout=30)

    def close(self) -> None:
        if self._thread.is_alive():
            self._jobs.put(_STOP)
            self._thread.join(timeout=30)
