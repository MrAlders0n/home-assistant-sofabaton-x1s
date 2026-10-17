"""Wifi Commands steps driven through the UI (plan P1b).

Allowances follow what each user action may legitimately change:

- creating a device or saving a slot: only that device's record in HA
  (``ha/wifi/<key>``), and nothing may be sent to the hub (``read_only``);
- a sync: that device's record, the hub device it deploys (new or the
  same id), the device order, the activities its slots name (members,
  power macros, bindings, favorites, shortcuts) and the entity attributes
  that mirror those;
- a delete: the same, plus the hub's cascade into every activity.

Command ids: slot ``i`` is command ``i + 1`` on every hub model
(docs/protocol/wifi-commands.md, the 1..20 command-table space).
"""

from __future__ import annotations

from typing import Any

from ..model import ABSENT
from . import Ctx, Spec, on_entities, on_hub
from . import ui_wifi

#: The slot editor's hard-button values and the remote's button ids (the
#: "Add button assignment" dialog's option values, 2026-10-16).
BUTTON_IDS = {"volup": 182, "red": 190, "green": 191, "yellow": 192, "blue": 193, "ok": 176}

_DEVICE_ENTITIES = ("remote.*/attrs/devices", "sensor.*_index/attrs/devices", "remote.*/attrs/favorite_keys",
                    "button.*/state")
#: Every Wifi device's row repeats the hub-wide list of managed hashes found
#: on the hub, so any deploy or delete changes it in all rows.
_HUB_WIDE_WIFI = ["ha/wifi/*/managed_command_hashes"]


def _key(ctx: Ctx, device: str) -> str:
    return ctx.known[f"wifi:{device}"]


def _ui_do(fn):
    def do(ctx: Ctx) -> dict[str, Any]:
        result = ctx.actor.run(fn)
        return {"status": "success", "result": result}
    return do


def _activity_ids(ctx: Ctx, names: list[str]) -> list[int]:
    acts = ctx.snapshot.before()["cache"]["activities"]
    by_name = {a["head"]["name"]: int(i) for i, a in acts.items()}
    return [by_name[n] for n in names]


def _activity_scope(act_ids: list[int]) -> list[str]:
    parts = []
    for a in act_ids:
        parts += on_hub(f"activities/{a}/members", f"activities/{a}/macros", f"activities/{a}/bindings",
                        f"activities/{a}/favorites", f"activities/{a}/shortcuts")
        parts += on_entities(f"remote.*/attrs/assigned_keys/{a}", f"sensor.*_index/attrs/activities/{a}")
    return parts


def create(device: str) -> Spec:
    def late(ctx: Ctx, before: dict, after: dict):
        new = sorted(set(after["ha"]["wifi"]) - set(before["ha"]["wifi"]))
        if len(new) == 1:
            ctx.known[f"wifi:{device}"] = new[0]
        return [f"ha/wifi/{k}" for k in new], [(f"wifi/{k}/device_name", device, f"create {device}", ("ha",))
                                              for k in new]

    return Spec("wifi_create", {"device": device}, _ui_do(lambda p: ui_wifi.create_device(p, device)),
                read_only=True, late=late)


def slot(ctx: Ctx, device: str, index: int, *, name: str, hard_button: str | None,
         activities: list[str], favorite: bool = False, also_allow: list[str] | None = None,
         also_expect: list[tuple] | None = None) -> Spec:
    key = _key(ctx, device)
    act_ids = _activity_ids(ctx, activities)
    expect = [
        (f"wifi/{key}/slots/{index}/name", name, f"name slot {index} of {device}", ("ha",)),
        (f"wifi/{key}/slots/{index}/activities", sorted(str(a) for a in act_ids),
         f"slot {index} of {device} in {activities}", ("ha",)),
        # A changed slot makes the device need a sync (what the list pill and
        # the Sync to Hub button show).
        (f"wifi/{key}/sync_needed", True, f"saving slot {index} of {device} needs a sync", ("ha",)),
    ]
    return Spec("wifi_slot", {"device": device, "slot": index, "name": name, "hard_button": hard_button,
                              "activities": activities, "favorite": favorite},
                _ui_do(lambda p: ui_wifi.edit_slot(p, device, index, name=name, hard_button=hard_button,
                                                   activities=activities, favorite=favorite)),
                allowance=[f"ha/wifi/{key}"] + list(also_allow or []), expect=expect + list(also_expect or []),
                read_only=True)


def sync(ctx: Ctx, device: str, slots: list[dict[str, Any]], *, extra_activities: list[str] = (),
         post=None) -> Spec:
    """``slots``: what the user configured, ``[{index, hard_button,
    activities}]``; the hub must end with those bindings."""

    key = _key(ctx, device)
    names = sorted({a for s in slots for a in s["activities"]} | set(extra_activities))
    act_ids = _activity_ids(ctx, names)

    def do(ctx: Ctx) -> dict[str, Any]:
        label = ctx.actor.run(lambda p: ui_wifi.sync_device(p, device))
        return {"status": "success" if label == "Up to date" else "failed", "message": f"sync button: {label}"}

    def late(ctx: Ctx, before: dict, after: dict):
        dev = (after["ha"]["wifi"].get(key) or {}).get("deployed_device_id")
        new_ids = sorted(set(after["cache"]["devices"]) - set(before["cache"]["devices"]))
        allow = on_hub("order/devices") + on_entities(*_DEVICE_ENTITIES)
        for d in {str(dev)} | set(new_ids):
            # The device itself, and its own button page (derived device-level
            # bindings) in the remote's assigned_keys.
            allow += on_hub(f"devices/{d}") + on_entities(f"remote.*/attrs/assigned_keys/{d}")
        if dev is not None:
            ctx.known[f"wifidev:{device}"] = int(dev)
        expect: list[tuple] = [(f"wifi/{key}/sync_needed", False, f"sync {device}", ("ha",))]
        for s in slots:
            button = BUTTON_IDS.get(s.get("hard_button") or "")
            if button is None or dev is None:
                continue
            for a in _activity_ids(ctx, s["activities"]):
                expect.append((f"activities/{a}/bindings/{button}/short", f"{dev}/{s['index'] + 1}",
                               f"{device} slot {s['index']} on button {button} in activity {a}"))
        return allow, expect

    # The buttons this sync binds: older expectations on them (a cleared
    # button, another device's binding) give way to this sync's.
    forget = [f"activities/{a}/bindings/{BUTTON_IDS[s['hard_button']]}"
              for s in slots if s.get("hard_button") in BUTTON_IDS for a in _activity_ids(ctx, s["activities"])]
    return Spec("wifi_sync", {"device": device, "activities": names}, do,
                allowance=[f"ha/wifi/{key}"] + _HUB_WIDE_WIFI + _activity_scope(act_ids), late=late, post=post,
                forget=forget)


def delete(ctx: Ctx, device: str, *, activities: list[str], gone_bindings: list[tuple[str, int]] = (),
           also_allow: list[str] = (), also_forget: list[str] = ()) -> Spec:
    """``gone_bindings``: (activity name, button id) the hub must clear
    with the device (its bindings cascade away)."""

    key = _key(ctx, device)
    dev = ctx.known.get(f"wifidev:{device}")
    act_ids = _activity_ids(ctx, activities)
    allow = [f"ha/wifi/{key}"] + _HUB_WIDE_WIFI + _activity_scope(act_ids) + on_hub("order/devices")         + on_entities(*_DEVICE_ENTITIES)
    if dev is not None:
        allow += on_hub(f"devices/{dev}") + on_entities(f"remote.*/attrs/assigned_keys/{dev}")
    allow += list(also_allow)
    expect = [(f"wifi/{key}", ABSENT, f"delete {device}", ("ha",))]
    if dev is not None:
        expect.append((f"devices/{dev}", ABSENT, f"delete {device} from the hub"))
    for act_name, button in gone_bindings:
        a = _activity_ids(ctx, [act_name])[0]
        expect.append((f"activities/{a}/bindings/{button}", ABSENT,
                       f"{device}'s button {button} leaves activity {a} with it"))
    return Spec("wifi_delete", {"device": device}, _ui_do(lambda p: ui_wifi.delete_device(p, device)),
                allowance=allow, expect=expect,
                forget=[f"activities/{a}/bindings" for a in act_ids] + [f"wifi/{key}"]
                + ([f"devices/{dev}"] if dev is not None else []) + list(also_forget))


def pills(devices: list[str]) -> Spec:
    """Read-only: every named device's row says "Synced" (question 3 for the
    acting tab: no spurious "Sync needed" after a sync)."""

    def do(ctx: Ctx) -> dict[str, Any]:
        shown = ctx.actor.run(lambda p: {d: ui_wifi.device_pill(p, d) for d in devices})
        bad = {d: s for d, s in shown.items() if s != "Synced"}
        return {"status": "success" if not bad else "failed", "message": f"pills {shown}"}

    return Spec("wifi_pills", {"devices": devices}, do, read_only=True)
