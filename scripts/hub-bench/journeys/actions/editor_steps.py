"""Activity editor steps driven through the UI (plan P1b).

Edits stay in the editor's working copy until Sync, so an edit step may
change nothing at all (``read_only``, no checkpoint): not HA, not the
cache, not the entities. The one exception is a NEW Wifi Event, which the
editor stages in the HA store at once (``ha/events``).

A sync may change the activity, the Wifi Events device (created on the
first event, else its commands), the device order and the entity mirrors.
"""

from __future__ import annotations

from typing import Any

from ..model import ABSENT
from . import Ctx, Spec, on_entities, on_hub
from . import ui_editor

_SYNC_ENTITIES = ("remote.*/attrs/devices", "remote.*/attrs/favorite_keys", "remote.*/attrs/macro_keys",
                  "sensor.*_index/attrs/devices", "button.*/state")


def _ui(fn):
    def do(ctx: Ctx) -> dict[str, Any]:
        return {"status": "success", "result": ctx.actor.run(fn)}
    return do


def events_by_name(ha: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {e.get("name"): e for e in ((ha.get("events") or {}).get("slots") or {}).values()}


def _event_post(event: str):
    def post(ctx: Ctx, after: dict) -> list[str]:
        return [] if event in events_by_name(after["ha"]) else [f"Wifi Event {event!r} is not in the HA store"]
    return post


def new_event_shortcut(activity: str, event: str) -> Spec:
    return Spec("editor_event_shortcut", {"activity": activity, "event": event},
                _ui(lambda p: ui_editor.add_shortcut_new_event(p, activity, event)),
                allowance=["ha/events"], read_only=True, checkpoint=False, post=_event_post(event))


def new_event_binding(activity: str, button_code: str, event: str) -> Spec:
    """``button_code``: the assignment dialog's option value (Red = "190")."""

    return Spec("editor_event_binding", {"activity": activity, "button": button_code, "event": event},
                _ui(lambda p: ui_editor.add_binding_new_event(p, activity, button_code, event)),
                allowance=["ha/events"], read_only=True, checkpoint=False, post=_event_post(event))


def move_to_top(activity: str, label: str) -> Spec:
    return Spec("editor_move_to_top", {"activity": activity, "label": label},
                _ui(lambda p: ui_editor.move_shortcut_to_top(p, activity, label)),
                read_only=True, checkpoint=False)


def macro(activity: str, name: str, steps: list[dict]) -> Spec:
    return Spec("editor_macro", {"activity": activity, "name": name, "steps": steps},
                _ui(lambda p: ui_editor.add_macro(p, activity, name, steps)), read_only=True, checkpoint=False)


def delete_row(activity: str, label: str) -> Spec:
    return Spec("editor_delete_row", {"activity": activity, "label": label},
                _ui(lambda p: ui_editor.delete_row(p, activity, label)), read_only=True, checkpoint=False)


def delete_binding(activity: str, button_label: str) -> Spec:
    return Spec("editor_delete_binding", {"activity": activity, "button": button_label},
                _ui(lambda p: ui_editor.delete_binding(p, activity, button_label)), read_only=True, checkpoint=False)


def _ev_device(after: dict) -> int | None:
    dev = ((after["ha"].get("events") or {}).get("record") or {}).get("device_id")
    return int(dev) if dev is not None else None


def sync(activity_id: int, activity: str, post=None) -> Spec:
    def do(ctx: Ctx) -> dict[str, Any]:
        label = ctx.actor.run(lambda p: ui_editor.sync(p, activity))
        return {"status": "success" if label == "Up to date" else "failed", "message": f"editor: {label}"}

    def late(ctx: Ctx, before: dict, after: dict):
        allow = on_hub("order/devices") + on_entities(*_SYNC_ENTITIES)
        for d in set(after["cache"]["devices"]) - set(before["cache"]["devices"]):
            allow += on_hub(f"devices/{d}")
        ev = _ev_device(after)
        if ev is not None:
            allow += on_hub(f"devices/{ev}")
        return allow, []

    return Spec("editor_sync", {"activity": activity}, do,
                allowance=on_hub(f"activities/{activity_id}") + ["ha/events", "ha/wifi/*/managed_command_hashes"]
                + on_entities(f"remote.*/attrs/assigned_keys/{activity_id}", f"sensor.*_index/attrs/activities/{activity_id}"),
                late=late, post=post)


def up_to_date(activity: str) -> Spec:
    """Read-only: after a sync the editor has nothing left to sync."""

    def do(ctx: Ctx) -> dict[str, Any]:
        label = ctx.actor.run(lambda p: ui_editor.editor_sync_label(p, activity))
        return {"status": "success" if label == "Up to date" else "failed", "message": f"editor: {label}"}

    return Spec("editor_up_to_date", {"activity": activity}, do, read_only=True)


def delete_events(events: list[str], device_name: str = "Wifi Events") -> Spec:
    """Delete Wifi Events in the Wifi Events device editor, then sync it."""

    def do(ctx: Ctx) -> dict[str, Any]:
        def run(p):
            ui_editor.delete_device_commands(p, device_name, events)
            return ui_editor.sync_device_editor(p, device_name)
        label = ctx.actor.run(run)
        return {"status": "success" if label == "Up to date" else "failed", "message": f"device editor: {label}"}

    def late(ctx: Ctx, before: dict, after: dict):
        ev = _ev_device(before)
        allow = on_hub("order/devices") + on_entities(*_SYNC_ENTITIES, "remote.*/attrs/assigned_keys",
                                                      "sensor.*_index/attrs/activities")
        if ev is not None:
            allow += on_hub(f"devices/{ev}")
        return allow, []

    def post(ctx: Ctx, after: dict) -> list[str]:
        left = [e for e in events if e in events_by_name(after["ha"])]
        return [f"Wifi Events still in the HA store after the delete: {left}"] if left else []

    return Spec("delete_events", {"events": events}, do, allowance=["ha/events", "ha/wifi/*/managed_command_hashes"],
                late=late, post=post)


def delete_hub_device(name: str, device_id: int | None) -> Spec:
    allow = on_hub("order/devices", "activities") + ["ha/events", "ha/wifi/*/managed_command_hashes"] + on_entities(*_SYNC_ENTITIES, "remote.*/attrs/assigned_keys",
                                                                              "sensor.*_index/attrs/activities")
    expect = []
    if device_id is not None:
        allow += on_hub(f"devices/{device_id}")
        expect.append((f"devices/{device_id}", ABSENT, f"delete device {name}"))
    return Spec("delete_hub_device", {"name": name}, _ui(lambda p: ui_editor.delete_hub_device(p, name)),
                allowance=allow, expect=expect)
