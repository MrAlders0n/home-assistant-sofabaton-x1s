"""Page objects for the Automation tab -> Wifi Commands (plan P1b).

Every function runs in the acting tab's thread (``actor.Actor.run``) and
takes the page. Selectors were read from the live panel on 2026-10-16
(``ui_outline.js`` dumps): the device list (``.device-card``), the Add
dialog, the slot grid (``.command-grid > .slot-btn``), the slot editor
(name field, "Set as favorite" switch, "Physical button assignment"
dropdown, ``.activity-chip`` toggles, Save) and the detail sync button.
"""

from __future__ import annotations

import time

from playwright.sync_api import Page

P = "sofabaton-x-panel sofabaton-control-panel"
W = f"{P} sofabaton-wifi-commands-tab"
DIALOG = f"{P} .dialog"


class UiStepError(RuntimeError):
    """The UI did not do what the step needed (a missing control, a
    timeout): the step failed, which is itself a result."""


def _wait(page: Page, predicate, timeout: float, what: str, poll: float = 0.25):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        page.wait_for_timeout(int(poll * 1000))
    raise UiStepError(f"timed out after {timeout:.0f} s waiting for {what}")


def stable_label(page: Page, selector: str, hold: float = 1.5, timeout: float = 10.0) -> str:
    """The element's text once it has not changed for ``hold`` seconds."""

    deadline = time.time() + timeout
    last, since = None, time.time()
    while time.time() < deadline:
        el = page.locator(selector).first
        text = el.inner_text().strip() if el.count() else ""
        if text != last:
            last, since = text, time.time()
        elif time.time() - since >= hold:
            return text
        page.wait_for_timeout(250)
    return last or ""


def goto_list(page: Page) -> None:
    """The Wifi Commands device list, from wherever the tab is."""

    page.locator(f"{P} .tab-btn", has_text="Automation").first.click()
    page.wait_for_timeout(300)
    back = page.locator(f"{W} .detail-view .back-btn")
    if back.count():
        back.first.click()
        page.wait_for_timeout(300)
    sub = page.locator(f"{W} .secondary-tab-btn", has_text="Wifi Commands")
    if sub.count():
        sub.first.click()
    _wait(page, lambda: page.locator(f"{W} .list-header").count(), 15, "the Wifi Commands list")


def device_names(page: Page) -> list[str]:
    goto_list(page)
    return [t.strip() for t in page.locator(f"{W} .device-card .device-card-name").all_inner_texts()]


def device_pill(page: Page, name: str) -> str:
    """The status pill of a device row ("Synced", "Sync needed", ...)."""

    goto_list(page)
    card = page.locator(f"{W} .device-card", has=page.locator(".device-card-name", has_text=name)).first
    return card.locator(".device-status-pill-label").inner_text().strip()


def create_device(page: Page, name: str) -> None:
    goto_list(page)
    page.locator(f"{W} .list-header-action .quick-access-add-btn").first.click()
    _wait(page, lambda: page.locator(DIALOG).count(), 10, "the Add Wifi Device dialog")
    field = page.locator(f"{DIALOG} #sb-new-device-name input").first
    field.fill(name)
    field.press("Tab")
    page.locator(f"{DIALOG} .dialog-btn-primary", has_text="Create").first.click()
    _wait(page, lambda: not page.locator(DIALOG).count(), 30, "the Add dialog to close")
    # The new device opens in the detail view.
    page.wait_for_timeout(800)
    if page.locator(f"{W} .detail-view .detail-title", has_text=name).count():
        return
    _wait(page, lambda: name in device_names(page), 15, f"device {name!r} in the list")


def open_device(page: Page, name: str) -> None:
    if page.locator(f"{W} .detail-view .detail-title", has_text=name).count():
        return
    goto_list(page)
    page.locator(f"{W} .device-card", has=page.locator(".device-card-name", has_text=name)).first.click()
    _wait(page, lambda: page.locator(f"{W} .detail-view .detail-title", has_text=name).count(), 15,
          f"the {name!r} detail view")


def _switch_on(row) -> bool:
    return "active" in (row.get_attribute("class") or "")


def edit_slot(page: Page, device: str, slot: int | None, *, name: str, hard_button: str | None,
              activities: list[str], favorite: bool = False) -> None:
    """Open slot ``slot`` (0-based; None = the first empty one), set its
    fields, Save (which writes to HA at once). ``hard_button`` is the
    selector value (``volup``, ``red``, ``__none__``; None = leave it).

    Order matters: the activity chips stay disabled until a button or the
    favorite is set, and the last selected chip cannot be deselected, so
    the wanted chips are selected first and the rest deselected after."""

    open_device(page, device)
    if slot is None:
        target = page.locator(f"{W} .command-grid > .slot-btn.slot-empty").first
    else:
        target = page.locator(f"{W} .command-grid > .slot-btn").nth(slot)
    if "slot-empty" in (target.get_attribute("class") or ""):
        target.click()
    else:
        target.locator(".slot-main").first.click()
    _wait(page, lambda: page.locator(f"{DIALOG} .activity-chip-row").count(), 15, "the slot editor")

    name_input = page.locator(f"{DIALOG} #sb-command-display-name input").first
    name_input.fill(name)
    name_input.press("Tab")  # the draft commits on change

    if hard_button is not None:
        page.locator(f"{DIALOG} .config-block > .config-group:nth-child(2) > ha-selector").first.evaluate(
            "(el, v) => el.dispatchEvent(new CustomEvent('value-changed', {detail: {value: v}, bubbles: true, composed: true}))",
            hard_button)
        page.wait_for_timeout(300)

    fav_row = page.locator(f"{DIALOG} .checkbox-row", has_text="Set as favorite").first
    if _switch_on(fav_row) != favorite:
        fav_row.click()
        page.wait_for_timeout(200)

    chips = page.locator(f"{DIALOG} .activity-chip")
    for want_on in (True, False):
        for i in range(chips.count()):
            chip = chips.nth(i)
            on = "active" in (chip.get_attribute("class") or "")
            if (chip.inner_text().strip() in activities) == want_on and on != want_on:
                chip.click()
                page.wait_for_timeout(150)
    shown = sorted(chips.nth(i).inner_text().strip() for i in range(chips.count())
                   if "active" in (chips.nth(i).get_attribute("class") or ""))
    if shown != sorted(activities):
        raise UiStepError(f"activity chips show {shown}, wanted {sorted(activities)}")

    page.locator(f"{DIALOG} .dialog-footer .dialog-btn-primary", has_text="Save").first.click()
    def closed():
        if not page.locator(f"{DIALOG} .activity-chip-row").count():
            return True
        note = page.locator(f"{DIALOG} .dialog-footer-note").first
        if note.count() and note.inner_text().strip():
            raise UiStepError(f"slot save refused: {note.inner_text().strip()}")
        return False
    _wait(page, closed, 30, "the slot editor to close")


def sync_device(page: Page, device: str, timeout: float = 600) -> str:
    """Press "Sync to Hub" and wait until the button settles; returns its
    final label ("Up to date" on success) plus the dock line when not."""

    open_device(page, device)
    btn = page.locator(f"{W} .detail-view .detail-sync-btn").first
    # Right after another operation the tab can still say "Busy" until its
    # next state poll (UI-3); a user would wait, so the bench waits too.
    _wait(page, lambda: btn.inner_text().strip() != "Busy", 30, "the sync button to leave Busy")
    # Opening a device shows a default "Up to date" until its sync state
    # has loaded: wait for the label to hold still before deciding.
    label = stable_label(page, f"{W} .detail-view .detail-sync-btn")
    if btn.is_disabled():
        return label
    btn.click()
    page.wait_for_timeout(1500)

    def settled():
        b = page.locator(f"{W} .detail-view .detail-sync-btn").first
        if not b.count():
            return None
        text = b.inner_text().strip()
        if text in ("Up to date", "Sync to Hub"):
            return text
        return None

    final = _wait(page, settled, timeout, f"the {device!r} sync to finish", poll=1.0)
    if final != "Up to date":
        dock = page.locator(f"{P} .card-bottom-dock-status").first
        final += f" | dock: {dock.inner_text().strip() if dock.count() else ''}"
    return final


def delete_device(page: Page, device: str) -> None:
    goto_list(page)
    card = page.locator(f"{W} .device-card", has=page.locator(".device-card-name", has_text=device)).first
    card.locator(".device-delete-btn").click()
    _wait(page, lambda: page.locator(DIALOG).count(), 10, "the delete confirmation")
    page.locator(f"{DIALOG} .dialog-footer-actions .dialog-btn-primary", has_text="Delete").first.click()
    _wait(page, lambda: device not in device_names(page), 600, f"device {device!r} to disappear", poll=1.0)


def dismiss_dialogs(page: Page) -> None:
    """After a failed step: close whatever dialog is open (Cancel, else the
    close button, else Escape), so the next step starts from a clean page."""

    for _ in range(3):
        if not page.locator(f"{P} .dialog").count():
            return
        cancel = page.locator(f"{P} .dialog .dialog-footer button", has_text="Cancel")
        close = page.locator(f"{P} .dialog .dialog-close")
        try:
            if cancel.count():
                cancel.first.click(timeout=3000)
            elif close.count():
                close.first.click(timeout=3000)
            else:
                page.keyboard.press("Escape")
        except Exception:  # noqa: BLE001
            page.keyboard.press("Escape")
        page.wait_for_timeout(500)
