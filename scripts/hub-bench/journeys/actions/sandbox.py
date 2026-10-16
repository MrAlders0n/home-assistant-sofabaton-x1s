"""Sandbox steps: create and delete ``J `` entities over the integration's
websocket API (the calls the Hub tab's Add / Delete make), plus a full
export before and after (plan section 7.2, the X2 gate).

Allowances are scoped to the sandbox entity itself, the display order and
the entity mirrors. A real entity that moves is a finding; so is a
display order that does not come back.
"""

from __future__ import annotations

from typing import Any

from ..frame import check
from ..model import ABSENT
from . import ACTIVITY_NAME_IN_ENTITIES, DEVICE_NAME_IN_ENTITIES, Ctx, Spec, on_entities, on_hub


def _new_ids(before: dict, after: dict, kind: str) -> list[str]:
    return sorted(set(after["cache"][kind]) - set(before["cache"][kind]))


def create_device(name: str, device_class: str = "ir") -> Spec:
    def do(ctx: Ctx) -> dict[str, Any]:
        res = ctx.cmd("device/create", name=name, device_class=device_class)
        return {"status": str((res or {}).get("status")), "result": res}

    def late(ctx: Ctx, before: dict, after: dict):
        new = _new_ids(before, after, "devices")
        if len(new) == 1:
            ctx.known[f"dev:{name}"] = int(new[0])
        allow = on_hub("order/devices") + DEVICE_NAME_IN_ENTITIES + on_entities("sensor.*_index/attrs/devices")
        for d in new:
            allow += on_hub(f"devices/{d}") + on_entities(f"remote.*/attrs/assigned_keys/{d}")
        return allow, [(f"devices/{d}/head/name", name, f"create device {name}") for d in new]

    return Spec("create_device", {"name": name, "class": device_class}, do, late=late)


def create_activity(name: str) -> Spec:
    def do(ctx: Ctx) -> dict[str, Any]:
        res = ctx.cmd("activity/create", name=name)
        return {"status": str((res or {}).get("status")), "result": res}

    def late(ctx: Ctx, before: dict, after: dict):
        new = _new_ids(before, after, "activities")
        if len(new) == 1:
            ctx.known[f"act:{name}"] = int(new[0])
        allow = on_hub("order/activities") + ACTIVITY_NAME_IN_ENTITIES
        for a in new:
            allow += on_hub(f"activities/{a}") + on_entities(f"remote.*/attrs/assigned_keys/{a}",
                                                            f"sensor.*_index/attrs/activities/{a}")
        return allow, [(f"activities/{a}/head/name", name, f"create activity {name}") for a in new]

    return Spec("create_activity", {"name": name}, do, late=late)


def delete_device(ctx: Ctx, name: str) -> Spec:
    dev = ctx.known[f"dev:{name}"]

    def do(ctx: Ctx) -> dict[str, Any]:
        res = ctx.cmd("device/delete", device_id=dev)
        return {"status": str((res or {}).get("status")), "result": res}

    return Spec("delete_device", {"name": name, "id": dev}, do,
                allowance=on_hub(f"devices/{dev}", "order/devices") + DEVICE_NAME_IN_ENTITIES
                + on_entities("sensor.*_index/attrs/devices", f"remote.*/attrs/assigned_keys/{dev}"),
                expect=[(f"devices/{dev}", ABSENT, f"delete device {name}")], forget=[f"devices/{dev}"])


def delete_activity(ctx: Ctx, name: str) -> Spec:
    """Deletes the activity if it is still there: the hub deletes a
    sandbox activity on its own when its last member goes (single-member
    GC, docs/protocol/live-hub-testing.md)."""

    act = ctx.known[f"act:{name}"]

    def do(ctx: Ctx) -> dict[str, Any]:
        if str(act) not in ctx.snapshot.before()["cache"]["activities"]:
            return {"status": "success", "message": "already gone (hub GC after its last member left)"}
        res = ctx.cmd("activity/delete", activity_id=act)
        return {"status": str((res or {}).get("status")), "result": res}

    return Spec("delete_activity", {"name": name, "id": act}, do,
                allowance=on_hub(f"activities/{act}", "order/activities") + ACTIVITY_NAME_IN_ENTITIES
                + on_entities(f"remote.*/attrs/assigned_keys/{act}", f"sensor.*_index/attrs/activities/{act}",
                              "remote.*/attrs/macro_keys", "remote.*/attrs/favorite_keys"),
                expect=[(f"activities/{act}", ABSENT, f"delete activity {name}")], forget=[f"activities/{act}"])


def ui_reload() -> Spec:
    """Reload the acting tab (what a user does after a stale banner): the
    Wifi slot editor only offers activities its card has loaded."""

    def do(ctx: Ctx) -> dict[str, Any]:
        from ..ui import open_panel

        def run(page):
            page.reload(wait_until="domcontentloaded")
            page.locator("sofabaton-x-panel").wait_for(timeout=30000)
            open_panel(page, ctx.hub, "Hub", "Activities")
        ctx.actor.run(run)
        return {"status": "success"}

    return Spec("ui_reload", {}, do, read_only=True, checkpoint=False)


def full_export(tag: str) -> Spec:
    """A full export with payloads (layer 5), kept for :func:`full_same`."""

    def do(ctx: Ctx) -> dict[str, Any]:
        normalized, raw = ctx.world.full_export()
        ctx.known[f"full:{tag}"] = normalized
        return {"status": "success", "message": f"{len(raw.get('devices') or [])} devices, "
                                                 f"{len(raw.get('activities') or [])} activities"}

    return Spec("full_export", {"tag": tag}, do, read_only=True, checkpoint=False)


def full_same(before_tag: str, after_tag: str) -> Spec:
    """Read-only: the two full exports are equal, payloads and display
    order included."""

    def do(ctx: Ctx) -> dict[str, Any]:
        a, b = ctx.known.get(f"full:{before_tag}"), ctx.known.get(f"full:{after_tag}")
        if a is None or b is None:
            return {"status": "failed", "message": "an export is missing"}
        result = check({"x": a}, {"x": b}, [], layers=("x",))
        if result.ok:
            return {"status": "success", "message": "full exports equal (payloads and order included)"}
        return {"status": "failed", "message": " | ".join(c.render() for c in result.findings[:15])}

    return Spec("full_same", {"before": before_tag, "after": after_tag}, do, read_only=True, checkpoint=False)
