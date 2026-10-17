"""Actions over the integration's websocket API (phase 0).

The editor actions build what the card builds: the latest structural
bundle as the baseline, a copy with one entity edited, sent to
``activity/sync`` or ``device/sync``.
"""

from __future__ import annotations

from typing import Any, Callable

import ha_bench as hb

from ..model import ABSENT
from . import ACTIVITY_NAME_IN_ENTITIES, DEVICE_NAME_IN_ENTITIES, Ctx, Spec, on_entities, on_hub


def _row(bundle: dict[str, Any], kind: str, entity_id: int) -> dict[str, Any]:
    for row in bundle.get(kind) or []:
        if int((row.get("device") or {}).get("device_id") or 0) == entity_id:
            return row
    raise KeyError(f"{kind[:-1]} {entity_id} is not in the bundle")


def _entity_sync(kind: str, entity_id: int, edit: Callable[[dict[str, Any]], None]) -> Callable[[Ctx], dict]:
    plural = "activities" if kind == "activity" else "devices"

    def do(ctx: Ctx) -> dict[str, Any]:
        baseline = ctx.bundle()
        edited = ctx.bundle()
        edit(_row(edited, plural, entity_id))
        key = "activity_id" if kind == "activity" else "device_id"
        op_id = ctx.cmd(f"{kind}/sync", **{key: entity_id, "baseline": baseline, "edited": edited})["operation_id"]
        events = hb.watch_operation(ctx.ws, op_id)
        final = events[-1] if events else {}
        return {"status": final.get("status"), "message": final.get("message"),
                "phases": hb.phases(events)}

    return do


def _binding_entities(activity_id: int) -> list[str]:
    return on_entities(f"remote.*/attrs/assigned_keys/{activity_id}", "button.*/state")


# -- the actions ----------------------------------------------------------------------------------


def observe() -> Spec:
    """Nothing: two reads in a row must agree, and a hub refresh must not
    change anything."""

    return Spec("observe", {}, lambda ctx: {"status": "success"}, read_only=True)


def rename_activity(activity_id: int, name: str) -> Spec:
    def edit(row: dict[str, Any]) -> None:
        row["device"]["name"] = name

    path = f"activities/{activity_id}/head/name"
    return Spec("rename_activity", {"activity_id": activity_id, "name": name},
                _entity_sync("activity", activity_id, edit),
                allowance=on_hub(path) + ACTIVITY_NAME_IN_ENTITIES
                + on_entities(f"sensor.*_index/attrs/activities/{activity_id}/name"),
                expect=[(path, name, f"rename activity {activity_id}")])


def rename_device(device_id: int, name: str) -> Spec:
    def edit(row: dict[str, Any]) -> None:
        row["device"]["name"] = name

    path = f"devices/{device_id}/head/name"
    return Spec("rename_device", {"device_id": device_id, "name": name},
                _entity_sync("device", device_id, edit),
                allowance=on_hub(path) + DEVICE_NAME_IN_ENTITIES
                + on_entities(f"sensor.*_index/attrs/devices/{device_id}/name"),
                expect=[(path, name, f"rename device {device_id}")])


def set_binding(activity_id: int, button_id: int, target: tuple[int, int] | None,
                button_name: str | None = None) -> Spec:
    """Bind a hard button in an activity to (device, command), or clear it
    (``target=None``). Short press only."""

    def edit(row: dict[str, Any]) -> None:
        rows = [b for b in row.get("button_bindings") or [] if int(b.get("button_id") or 0) != button_id]
        if target is not None:
            rows.append({
                "button_id": button_id, "button_name": button_name,
                "device_id": target[0], "command_id": target[1],
                "long_press_device_id": None, "long_press_command_id": None,
            })
            rows.sort(key=lambda b: int(b.get("button_id") or 0))
        row["button_bindings"] = rows

    path = f"activities/{activity_id}/bindings/{button_id}"
    if target is None:
        expect = [(path, ABSENT, f"clear button {button_id} in activity {activity_id}")]
    else:
        expect = [(f"{path}/short", f"{target[0]}/{target[1]}",
                   f"bind button {button_id} in activity {activity_id}")]
    return Spec("set_binding", {"activity_id": activity_id, "button_id": button_id,
                                "target": list(target) if target else None},
                _entity_sync("activity", activity_id, edit),
                allowance=on_hub(path) + _binding_entities(activity_id), expect=expect, forget=[path])


def put_binding_row(activity_id: int, button_id: int, row: dict[str, Any] | None) -> Spec:
    """Put one binding row back verbatim (long press included), or remove
    the button's row when ``row`` is None. The undo of :func:`set_binding`."""

    def edit(activity: dict[str, Any]) -> None:
        rows = [b for b in activity.get("button_bindings") or [] if int(b.get("button_id") or 0) != button_id]
        if row is not None:
            rows.append(dict(row))
            rows.sort(key=lambda b: int(b.get("button_id") or 0))
        activity["button_bindings"] = rows

    path = f"activities/{activity_id}/bindings/{button_id}"
    if row is None or not (row.get("command_id") or row.get("long_press_command_id")):
        expect = [(path, ABSENT, f"restore button {button_id} in activity {activity_id} (unbound)")]
    else:
        short = f"{row['device_id']}/{row['command_id']}" if row.get("command_id") else None
        long = (f"{row['long_press_device_id']}/{row['long_press_command_id']}"
                if row.get("long_press_command_id") else None)
        expect = [(f"{path}/short", short, f"restore button {button_id} in activity {activity_id}"),
                  (f"{path}/long", long, f"restore button {button_id} in activity {activity_id}")]
    return Spec("put_binding_row", {"activity_id": activity_id, "button_id": button_id, "row": row},
                _entity_sync("activity", activity_id, edit),
                allowance=on_hub(path) + _binding_entities(activity_id), expect=expect, forget=[path])


def reorder(kind: str, ordered_ids: list[int]) -> Spec:
    """``kind`` is ``activities`` or ``devices``."""

    def do(ctx: Ctx) -> dict[str, Any]:
        command = "activity/reorder" if kind == "activities" else "device/reorder"
        result = ctx.cmd(command, ordered_ids=list(ordered_ids))
        return {"status": str((result or {}).get("status")), "result": result}

    path = f"order/{kind}"
    names = ACTIVITY_NAME_IN_ENTITIES if kind == "activities" else DEVICE_NAME_IN_ENTITIES
    return Spec(f"reorder_{kind}", {"ordered_ids": list(ordered_ids)}, do,
                allowance=on_hub(path) + names,
                expect=[(path, list(ordered_ids), f"reorder {kind}")])


def same_as_start(ignore: list[str] = ()) -> Spec:
    """Read-only: the hub (as of the last checkpoint) equals the hub at the
    journey's start, apart from ``ignore`` patterns. The clean-exit check
    (plan 3.4): every delete path must have cleaned up after itself."""

    def do(ctx: Ctx) -> dict[str, Any]:
        from ..frame import check

        start = ctx.known.get("start_hub")
        now = ctx.snapshot.before().get("cache")
        if start is None or now is None:
            return {"status": "failed", "message": "no start snapshot recorded"}
        result = check({"hub": start}, {"hub": now}, [f"hub/{p}" for p in ignore], layers=("hub",))
        if result.ok:
            return {"status": "success", "message": f"equal to start ({len(result.allowed)} ignored changes)"}
        lines = [c.render() for c in result.findings[:15]]
        return {"status": "failed", "message": "differs from start: " + " | ".join(lines)}

    return Spec("same_as_start", {"ignore": list(ignore)}, do, read_only=True)
