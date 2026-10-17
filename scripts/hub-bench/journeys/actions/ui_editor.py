"""Page objects for the activity editor and the device editor (plan P1b).

Hub tab -> Activities -> wrench. Selectors from the code map (2026-10-16)
and the live panel: sections carry ``data-edit-section``; the shortcut Add
dialog's kind segments are ``#sb-add-shortcut-kind``; the step editor's
``#sb-step-*``; the button-assignment dialog's ``#sb-binding-*``. Every
edit is local to the editor's working copy until Sync, except a NEW Wifi
Event, which goes to the HA store at once.
"""

from __future__ import annotations

from playwright.sync_api import Page

from .ui_wifi import DIALOG, P, UiStepError, _wait

E = f"{P} sofabaton-activities-tab sofabaton-edit-detail-view"
SHORTCUTS = f"{E} [data-edit-section='quick_access']"


def _commit(locator, value: str) -> None:
    locator.fill(value)
    locator.press("Tab")


def _close_dialog_with(page: Page, label: str) -> None:
    page.locator(f"{DIALOG} .dialog-footer .dialog-btn-primary", has_text=label).first.click()

    def closed():
        if not page.locator(DIALOG).count():
            return True
        note = page.locator(f"{DIALOG} .dialog-footer-note").first
        if note.count() and note.inner_text().strip():
            raise UiStepError(f"dialog refused: {note.inner_text().strip()}")
        return False

    _wait(page, closed, 30, "the dialog to close")


def leave_failure_panel(page: Page) -> None:
    """A failed sync replaces the editor with a failure panel; "Keep
    editing" returns to the editor (its working copy intact)."""

    keep = page.locator(f"{P} sofabaton-activities-tab .btn", has_text="Keep editing")
    if keep.count():
        keep.first.click()
        page.wait_for_timeout(800)


def title(page: Page) -> str | None:
    t = page.locator(f"{E} .detail-title").first
    return t.inner_text().strip() if t.count() else None


def open_activity(page: Page, name: str) -> None:
    leave_failure_panel(page)
    if title(page) == name and page.locator(SHORTCUTS).count():
        return
    page.locator(f"{P} .tab-btn", has_text="Hub").first.click()
    page.wait_for_timeout(300)
    while page.locator(f"{E} .back-btn").count() and title(page) != name:
        page.locator(f"{E} .back-btn").first.click()
        page.wait_for_timeout(500)
    if title(page) == name and page.locator(SHORTCUTS).count():
        return
    page.locator(f"{P} .secondary-tab-btn", has_text="Activities").first.click()
    row = page.locator(f"{P} .entity-block", has=page.locator(".entity-name-label", has_text=name)).first
    row.locator(".entity-meta button[title='Edit activity']").click()
    _wait(page, lambda: title(page) == name and page.locator(SHORTCUTS).count(), 30, f"the {name!r} editor")


def back_to_activity(page: Page, name: str) -> None:
    """From a sub-view (step editor, individual buttons) back to the activity."""

    for _ in range(4):
        if title(page) == name and page.locator(SHORTCUTS).count():
            return
        page.locator(f"{E} .back-btn").first.click()
        page.wait_for_timeout(600)
    raise UiStepError(f"could not get back to the {name!r} editor")


def shortcut_labels(page: Page) -> list[str]:
    return [t.strip() for t in page.locator(f"{SHORTCUTS} .quick-access-sortable-item .quick-access-label").all_inner_texts()]


def _open_add_shortcut(page: Page, kind: str) -> None:
    page.locator(f"{SHORTCUTS} .quick-access-add-btn:not(#sb-copy-shortcuts)").first.click()
    _wait(page, lambda: page.locator(f"{DIALOG} #sb-add-shortcut-kind").count(), 10, "the Add to shortcuts dialog")
    page.locator(f"{DIALOG} #sb-add-shortcut-kind .kind-seg-btn[value='{kind}']").first.click()
    page.wait_for_timeout(300)


def add_shortcut_new_event(page: Page, activity: str, event: str) -> None:
    open_activity(page, activity)
    _open_add_shortcut(page, "wifi_event")
    picker = page.locator(f"{DIALOG} #sb-add-fav-wifi-event")
    if picker.count():
        picker.first.click()
        page.locator(f"{DIALOG} .macro-picker-option[data-value='__new__']").first.click()
    _commit(page.locator(f"{DIALOG} #sb-add-fav-wifi-event-name").first, event)
    _close_dialog_with(page, "Add")
    _wait(page, lambda: event in shortcut_labels(page), 15, f"shortcut {event!r} in the list")


def move_shortcut_to_top(page: Page, activity: str, label: str) -> None:
    open_activity(page, activity)
    labels = shortcut_labels(page)
    if label not in labels:
        raise UiStepError(f"no shortcut {label!r} in {labels}")
    handle = page.locator(f"{SHORTCUTS} .quick-access-drag[aria-label='Reorder {label} (arrow keys)']").first
    for _ in range(labels.index(label)):
        handle = page.locator(f"{SHORTCUTS} .quick-access-drag[aria-label='Reorder {label} (arrow keys)']").first
        handle.focus()
        handle.press("ArrowUp")
        page.wait_for_timeout(300)
    labels = shortcut_labels(page)
    if labels[0] != label:
        raise UiStepError(f"shortcut {label!r} did not reach the top: {labels}")


def add_binding_new_event(page: Page, activity: str, button_code: str, event: str) -> None:
    open_activity(page, activity)
    page.locator(f"{E} .edit-selection-row--footer").first.click()
    _wait(page, lambda: page.locator(f"{E} .quick-access-head .quick-access-add-btn").count(), 10,
          "the individual buttons view")
    page.locator(f"{E} .quick-access-head .quick-access-add-btn").first.click()
    _wait(page, lambda: page.locator(f"{DIALOG} #sb-binding-button").count(), 10, "the assignment dialog")
    page.locator(f"{DIALOG} #sb-binding-button").first.select_option(button_code)
    page.locator(f"{DIALOG} #sb-binding-kind .kind-seg-btn[value='wifi_event']").first.click()
    page.wait_for_timeout(300)
    picker = page.locator(f"{DIALOG} #sb-binding-wifi-event")
    if picker.count():
        picker.first.click()
        page.locator(f"{DIALOG} .macro-picker-option[data-value='__new__']").first.click()
    _commit(page.locator(f"{DIALOG} #sb-binding-wifi-event-name").first, event)
    _close_dialog_with(page, "Add")
    back_to_activity(page, activity)


def add_macro(page: Page, activity: str, name: str, steps: list[dict]) -> None:
    """``steps``: ``{"kind": "command", "device": id, "command": id}`` or
    ``{"kind": "wifi_event", "event": name}``, each with an optional
    ``"wait"`` (seconds after the step)."""

    open_activity(page, activity)
    _open_add_shortcut(page, "action")
    _commit(page.locator(f"{DIALOG} #sb-add-action-name").first, name)
    _close_dialog_with(page, "Add")
    _wait(page, lambda: title(page) == name, 15, f"the step editor of {name!r}")
    for step in steps:
        page.locator(f"{E} .quick-access-head-actions .quick-access-add-btn", has_text="Add step").first.click()
        _wait(page, lambda: page.locator(f"{DIALOG} #sb-step-kind").count(), 10, "the Add step dialog")
        page.locator(f"{DIALOG} #sb-step-kind .kind-seg-btn[value='{step['kind']}']").first.click()
        page.wait_for_timeout(300)
        if step["kind"] == "command":
            page.locator(f"{DIALOG} #sb-step-device").first.select_option(str(step["device"]))
            page.wait_for_timeout(200)
            page.locator(f"{DIALOG} #sb-step-command").first.select_option(str(step["command"]))
        else:
            page.locator(f"{DIALOG} #sb-step-wifi-event").first.click()
            page.locator(f"{DIALOG} .macro-picker-option", has_text=step["event"]).first.click()
        _close_dialog_with(page, "Add")
    waits = page.locator(f"{E} .step-wait-input")
    for i, step in enumerate(steps):
        if step.get("wait") and i < waits.count():
            _commit(waits.nth(i), str(step["wait"]))
    back_to_activity(page, activity)


def sync(page: Page, activity: str, timeout: float = 600) -> str:
    """Press the editor's Sync; returns "Up to date" or the failure text."""

    open_activity(page, activity)
    btn = page.locator(f"{E} .detail-sync-btn").first
    _wait(page, lambda: btn.inner_text().strip() != "Busy", 30, "the sync button to leave Busy")
    if btn.is_disabled():
        return btn.inner_text().strip()
    btn.click()
    page.wait_for_timeout(1500)

    def settled():
        err = page.locator(f"{P} sofabaton-activities-tab .capture-error-title").first
        if err.count():
            sub = page.locator(f"{P} sofabaton-activities-tab .guard-sub").first
            return f"{err.inner_text().strip()}: {sub.inner_text().strip() if sub.count() else ''}"
        b = page.locator(f"{E} .detail-sync-btn").first
        if b.count() and not page.locator(f"{P} .progress-shell").count():
            text = b.inner_text().strip()
            if text in ("Up to date", "Sync to Hub"):
                return text
        return None

    result = _wait(page, settled, timeout, f"the {activity!r} sync to finish", poll=1.0)
    retry = page.locator(f"{P} sofabaton-activities-tab .btn-primary", has_text="Retry sync")
    if result not in ("Up to date", "Sync to Hub") and retry.count():
        # What a user does next: retry once. The first failure stays in
        # the result (a false failure is a finding of its own).
        retry.first.click()
        page.wait_for_timeout(1500)
        second = _wait(page, settled, timeout, f"the {activity!r} retry to finish", poll=1.0)
        return f"{result} || retry: {second}"
    return result


def delete_row(page: Page, activity: str, label: str) -> None:
    """Delete a shortcut or macro row (local until Sync)."""

    open_activity(page, activity)
    row = page.locator(f"{SHORTCUTS} .quick-access-sortable-item",
                       has=page.locator(".quick-access-label", has_text=label)).first
    row.locator("button[aria-label='Delete shortcut'], button[aria-label='Delete macro']").first.click()
    _wait(page, lambda: page.locator(DIALOG).count(), 10, "the delete confirmation")
    page.locator(f"{DIALOG} .dialog-footer button", has_text="Delete").last.click()
    _wait(page, lambda: label not in shortcut_labels(page), 15, f"row {label!r} to go")


def delete_binding(page: Page, activity: str, button_label: str) -> None:
    open_activity(page, activity)
    page.locator(f"{E} .edit-selection-row--footer").first.click()
    _wait(page, lambda: page.locator(f"{E} [data-kind='binding']").count(), 10, "the individual buttons view")
    row = page.locator(f"{E} [data-kind='binding']",
                       has=page.locator(".quick-access-label", has_text=button_label)).first
    row.locator("button[aria-label='Delete assignment']").click()
    _wait(page, lambda: page.locator(DIALOG).count(), 10, "the delete confirmation")
    page.locator(f"{DIALOG} .dialog-footer button", has_text="Delete").last.click()
    page.wait_for_timeout(500)
    back_to_activity(page, activity)


def delete_hub_device(page: Page, name: str) -> None:
    """Hub -> Devices -> the device's editor -> Delete device (immediate)."""

    page.locator(f"{P} .tab-btn", has_text="Hub").first.click()
    page.wait_for_timeout(300)
    while page.locator(f"{E} .back-btn").count():
        page.locator(f"{E} .back-btn").first.click()
        page.wait_for_timeout(500)
        if page.locator(DIALOG).count():
            raise UiStepError("leaving the editor asked about unsynced changes")
    page.locator(f"{P} .secondary-tab-btn", has_text="Devices").first.click()
    row = page.locator(f"{P} .entity-block", has=page.locator(".entity-name-label", has_text=name)).first
    row.locator(".entity-meta button[title='Edit device']").click()
    _wait(page, lambda: title(page) == name, 30, f"the {name!r} device editor")
    page.locator(f"{E} .icon-btn--danger[aria-label='Delete device']").first.click()
    _wait(page, lambda: page.locator(DIALOG).count(), 10, "the delete confirmation")
    page.locator(f"{DIALOG} .dialog-footer button", has_text="Delete").last.click()
    _wait(page, lambda: not page.locator(f"{P} .entity-block", has=page.locator(".entity-name-label", has_text=name)).count(),
          120, f"device {name!r} to leave the list", poll=1.0)


def open_device_editor(page: Page, name: str) -> None:
    leave_failure_panel(page)
    if title(page) == name and page.locator(f"{E} [data-edit-section='commands']").count():
        return
    page.locator(f"{P} .tab-btn", has_text="Hub").first.click()
    page.wait_for_timeout(300)
    while page.locator(f"{E} .back-btn").count():
        page.locator(f"{E} .back-btn").first.click()
        page.wait_for_timeout(500)
        if page.locator(DIALOG).count():
            raise UiStepError("leaving the editor asked about unsynced changes")
    page.locator(f"{P} .secondary-tab-btn", has_text="Devices").first.click()
    row = page.locator(f"{P} .entity-block", has=page.locator(".entity-name-label", has_text=name)).first
    row.locator(".entity-meta button[title='Edit device']").click()
    _wait(page, lambda: title(page) == name and page.locator(f"{E} [data-edit-section='commands']").count(), 30,
          f"the {name!r} device editor")


def delete_device_commands(page: Page, device: str, labels: list[str]) -> None:
    """Delete commands in the device editor (local until its Sync)."""

    open_device_editor(page, device)
    section = f"{E} [data-edit-section='commands']"
    for label in labels:
        row = page.locator(f"{section} [data-kind='command']",
                           has=page.locator(".quick-access-label", has_text=label)).first
        row.locator("button[aria-label='Delete command']").click()
        _wait(page, lambda: page.locator(DIALOG).count(), 10, "the delete confirmation")
        page.locator(f"{DIALOG} .dialog-footer button", has_text="Delete").last.click()
        page.wait_for_timeout(500)


def sync_device_editor(page: Page, device: str, timeout: float = 600) -> str:
    open_device_editor(page, device)
    btn = page.locator(f"{E} .detail-sync-btn").first
    _wait(page, lambda: btn.inner_text().strip() != "Busy", 30, "the sync button to leave Busy")
    if btn.is_disabled():
        return btn.inner_text().strip()
    btn.click()
    page.wait_for_timeout(1500)

    def settled():
        err = page.locator(f"{P} sofabaton-activities-tab .capture-error-title").first
        if err.count():
            sub = page.locator(f"{P} sofabaton-activities-tab .guard-sub").first
            return f"{err.inner_text().strip()}: {sub.inner_text().strip() if sub.count() else ''}"
        b = page.locator(f"{E} .detail-sync-btn").first
        if b.count() and not page.locator(f"{P} .progress-shell").count():
            text = b.inner_text().strip()
            if text in ("Up to date", "Sync to Hub"):
                return text
        return None

    return _wait(page, settled, timeout, f"the {device!r} device sync to finish", poll=1.0)


def editor_sync_label(page: Page, activity: str) -> str:
    open_activity(page, activity)
    btn = page.locator(f"{E} .detail-sync-btn").first
    _wait(page, lambda: btn.inner_text().strip() != "Busy", 30, "the sync button to leave Busy")
    return btn.inner_text().strip()
