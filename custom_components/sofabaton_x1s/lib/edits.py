"""Pure edit helpers over a snapshot bundle (phase 3 plan, W4).

Every function takes a ``hub_bundle`` (``HubSnapshot.bundle``) and returns
an **edited copy**; the input is never touched. Feed the pair to
``AsyncXProxy.sync_activity`` / ``sync_device`` and the planner turns the
difference into the hub writes. Nothing here talks to a hub: these are the
common intents the planner already understands, spelled out so a CLI, a
REST layer or a script does not have to know the bundle's row shapes.
Anything not covered is "edit the bundle yourself"; the planner's scope
guard keeps that safe.

Ids are the hub's: devices below 101, activities from 101 up, buttons by
their button code (``ButtonName``), commands by command id. Favorites are
identified by content, the ``(device_id, command_id)`` pair; a macro by its
id, which is also its quick-access id (favorites and macro shortcuts share
that id space: the ``button_id`` a favorite or macro row carries).
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Optional, Sequence, cast

from .payloads import CommandPayload, CommandRecord, NetworkCommand
from .protocol_const import (
    BUTTONNAME_BY_CODE,
    DEVICE_CLASS_BLUETOOTH,
    DEVICE_CLASS_WIFI_HUE,
    DEVICE_CLASS_WIFI_IP,
    DEVICE_CLASS_WIFI_MQTT,
    DEVICE_CLASS_WIFI_ROKU,
    DEVICE_CLASS_WIFI_SONOS,
    normalize_device_class,
)

_NON_IR_DEVICE_CLASSES = frozenset({
    DEVICE_CLASS_BLUETOOTH,
    DEVICE_CLASS_WIFI_IP,
    DEVICE_CLASS_WIFI_ROKU,
    DEVICE_CLASS_WIFI_HUE,
    DEVICE_CLASS_WIFI_SONOS,
    DEVICE_CLASS_WIFI_MQTT,
})

__all__ = [
    "rename_activity",
    "rename_device",
    "bind_button",
    "clear_button",
    "add_favorite",
    "remove_favorite",
    "reorder_favorites",
    "remove_macro",
    "add_member",
    "rename_command",
    "set_idle_behavior",
    "set_command_payload",
    "add_command",
]

# Highest quick-access id a new favorite may take; 198/199 are the power macros.
_MAX_QUICK_ACCESS_ID = 197
_POWER_ON_MACRO_ID = 198
_POWER_OFF_MACRO_ID = 199
_POWER_MACRO_IDS = frozenset({_POWER_ON_MACRO_ID, _POWER_OFF_MACRO_ID})
_ACTIVITY_ID_BASE = 101
# A device's references inside the power sequences: input, power on, power off.
_INPUT_REF = 0xC5
_POWER_ON_REF = 0xC6
_POWER_OFF_REF = 0xC7
_POWER_REFS = frozenset({_INPUT_REF, _POWER_ON_REF, _POWER_OFF_REF})
_POWER_STEP_DELAY = 0xFF
_DELAY_SENTINEL = 0xFF


def _find(bundle: dict[str, Any], kind: str, entity_id: int) -> dict[str, Any]:
    key = "devices" if kind == "device" else "activities"
    want = int(entity_id) & 0xFF
    for payload in bundle.get(key) or []:
        block = payload.get("device") or {}
        try:
            if int(block.get("device_id", -1)) & 0xFF == want:
                return payload
        except (TypeError, ValueError):
            continue
    raise KeyError(f"{kind} {want} is not in the bundle")


def _edit(bundle: dict[str, Any], kind: str, entity_id: int) -> tuple[dict[str, Any], dict[str, Any]]:
    edited = deepcopy(bundle)
    return edited, _find(edited, kind, entity_id)


def _clean_name(name: str, what: str) -> str:
    clean = str(name or "").strip()
    if not clean:
        raise ValueError(f"{what} needs a name")
    return clean


def rename_activity(bundle: dict[str, Any], activity_id: int, name: str) -> dict[str, Any]:
    """Rename an activity (a ``sync_activity`` on ``activity_id``)."""

    edited, activity = _edit(bundle, "activity", activity_id)
    activity["device"]["name"] = _clean_name(name, "an activity")
    return edited


def rename_device(
    bundle: dict[str, Any], device_id: int, name: str, *, brand: Optional[str] = None
) -> dict[str, Any]:
    """Rename a device, optionally its brand (a ``sync_device`` on ``device_id``)."""

    edited, device = _edit(bundle, "device", device_id)
    device["device"]["name"] = _clean_name(name, "a device")
    if brand is not None:
        device["device"]["brand"] = str(brand).strip()
    return edited


def bind_button(
    bundle: dict[str, Any],
    activity_id: int,
    button: int,
    device_id: int,
    command_id: int,
    *,
    long_press: Optional[tuple[int, int]] = None,
) -> dict[str, Any]:
    """Bind a remote button on an activity to a device command.

    ``button`` is the button code (``ButtonName.POWER_ON`` ...). An
    existing binding on that button is replaced. ``long_press`` is an
    optional ``(device_id, command_id)`` for the held press; omitting it
    clears any long press the button had. A ``sync_activity`` on
    ``activity_id`` writes it.
    """

    edited, activity = _edit(bundle, "activity", activity_id)
    button_id = int(button) & 0xFF
    row: dict[str, Any] = {
        "button_id": button_id,
        "button_name": BUTTONNAME_BY_CODE.get(button_id),
        "device_id": int(device_id) & 0xFF,
        "command_id": int(command_id) & 0xFF,
        "long_press_device_id": None,
        "long_press_command_id": None,
    }
    if long_press is not None:
        lp_device, lp_command = long_press
        row["long_press_device_id"] = int(lp_device) & 0xFF
        row["long_press_command_id"] = int(lp_command) & 0xFF
    rows = [r for r in activity.get("button_bindings") or [] if int(r.get("button_id", -1)) != button_id]
    rows.append(row)
    rows.sort(key=lambda r: int(r.get("button_id", 0)))
    activity["button_bindings"] = rows
    return edited


def clear_button(bundle: dict[str, Any], activity_id: int, button: int) -> dict[str, Any]:
    """Remove whatever an activity's button is bound to."""

    edited, activity = _edit(bundle, "activity", activity_id)
    button_id = int(button) & 0xFF
    activity["button_bindings"] = [
        r for r in activity.get("button_bindings") or [] if int(r.get("button_id", -1)) != button_id
    ]
    return edited


def _quick_access_ids(activity: dict[str, Any]) -> set[int]:
    ids: set[int] = set()
    for key in ("favorite_slots", "macros"):
        for row in activity.get(key) or []:
            try:
                ids.add(int(row.get("button_id", 0)) & 0xFF)
            except (TypeError, ValueError):
                continue
    return ids


def add_favorite(
    bundle: dict[str, Any], activity_id: int, device_id: int, command_id: int, *, name: str = ""
) -> dict[str, Any]:
    """Add a device command to an activity's favorites (quick access), at the end."""

    edited, activity = _edit(bundle, "activity", activity_id)
    content = (int(device_id) & 0xFF, int(command_id) & 0xFF)
    for row in activity.get("favorite_slots") or []:
        if (int(row.get("device_id", 0)) & 0xFF, int(row.get("command_id", 0)) & 0xFF) == content:
            raise ValueError(f"device {content[0]} command {content[1]} is already a favorite")
    used = _quick_access_ids(activity)
    slot = max((i for i in used if i <= _MAX_QUICK_ACCESS_ID), default=0) + 1
    if slot > _MAX_QUICK_ACCESS_ID:
        raise ValueError("the activity's quick-access list is full")
    row: dict[str, Any] = {"button_id": slot, "device_id": content[0], "command_id": content[1]}
    if str(name or "").strip():
        row["name"] = str(name).strip()
    activity.setdefault("favorite_slots", []).append(row)
    return edited


def remove_favorite(
    bundle: dict[str, Any], activity_id: int, device_id: int, command_id: int
) -> dict[str, Any]:
    """Remove a device command from an activity's favorites."""

    edited, activity = _edit(bundle, "activity", activity_id)
    content = (int(device_id) & 0xFF, int(command_id) & 0xFF)
    before = activity.get("favorite_slots") or []
    kept = [
        row for row in before
        if (int(row.get("device_id", 0)) & 0xFF, int(row.get("command_id", 0)) & 0xFF) != content
    ]
    if len(kept) == len(before):
        raise ValueError(f"device {content[0]} command {content[1]} is not a favorite")
    activity["favorite_slots"] = kept
    removed_ids = {int(r.get("button_id", 0)) & 0xFF for r in before} - {
        int(r.get("button_id", 0)) & 0xFF for r in kept
    }
    activity["favorites_order"] = [
        i for i in activity.get("favorites_order") or [] if int(i) & 0xFF not in removed_ids
    ]
    return edited


def reorder_favorites(
    bundle: dict[str, Any],
    activity_id: int,
    ordered: Sequence[tuple[int, int]] | Sequence[int],
) -> dict[str, Any]:
    """Put an activity's quick-access list in order.

    ``ordered`` is either ``(device_id, command_id)`` pairs, listing every
    favorite exactly once (macro shortcuts keep their place after the
    favorites, in their current order), or quick-access ids, listing every
    favorite and macro shortcut exactly once in any interleaving (the ids
    a favorite or macro row carries as ``button_id``, the hub's favorite
    ids).
    """

    if ordered and all(isinstance(item, int) and not isinstance(item, bool) for item in ordered):
        return _reorder_quick_access(bundle, activity_id, [int(cast(int, item)) for item in ordered])
    edited, activity = _edit(bundle, "activity", activity_id)
    rows = activity.get("favorite_slots") or []
    by_content = {
        (int(r.get("device_id", 0)) & 0xFF, int(r.get("command_id", 0)) & 0xFF): r for r in rows
    }
    wanted = [(int(d) & 0xFF, int(c) & 0xFF) for d, c in cast(Sequence[tuple[int, int]], ordered)]
    if len(set(wanted)) != len(wanted) or set(wanted) != set(by_content):
        raise ValueError("the order must list every favorite exactly once")
    favorite_ids = [int(by_content[c].get("button_id", 0)) & 0xFF for c in wanted]
    rest = [
        int(i) & 0xFF for i in activity.get("favorites_order") or []
        if int(i) & 0xFF not in set(favorite_ids)
    ]
    activity["favorites_order"] = favorite_ids + rest
    return edited


def _reorder_quick_access(bundle: dict[str, Any], activity_id: int, ids: list[int]) -> dict[str, Any]:
    edited, activity = _edit(bundle, "activity", activity_id)
    wanted = [i & 0xFF for i in ids]
    present = _quick_access_ids(activity) - _POWER_MACRO_IDS
    if len(set(wanted)) != len(wanted) or set(wanted) != present:
        raise ValueError("the order must list every favorite and macro shortcut exactly once")
    activity["favorites_order"] = wanted
    return edited


def remove_macro(bundle: dict[str, Any], activity_id: int, button_id: int) -> dict[str, Any]:
    """Remove one of an activity's macros, its quick-access entry included.

    ``button_id`` is the macro's id (its quick-access id). A button bound to
    the macro loses the binding (the long press alone is cleared when only
    it named the macro). The power macros (198 / 199) are not removable:
    they carry the activity's start and end sequence.
    """

    want = int(button_id) & 0xFF
    if want in _POWER_MACRO_IDS:
        raise ValueError("the power on / power off macros cannot be removed")
    edited, activity = _edit(bundle, "activity", activity_id)
    act_id = int(activity_id) & 0xFF
    macros = activity.get("macros") or []
    kept = [row for row in macros if int(row.get("button_id", 0)) & 0xFF != want]
    if len(kept) == len(macros):
        raise ValueError(f"activity {act_id} has no macro {want}")
    activity["macros"] = kept
    bindings: list[dict[str, Any]] = []
    for row in activity.get("button_bindings") or []:
        if int(row.get("device_id", 0)) & 0xFF == act_id and int(row.get("command_id", 0)) & 0xFF == want:
            continue
        if (
            int(row.get("long_press_device_id") or 0) & 0xFF == act_id
            and int(row.get("long_press_command_id") or 0) & 0xFF == want
        ):
            row = {**row, "long_press_device_id": None, "long_press_command_id": None}
        bindings.append(row)
    activity["button_bindings"] = bindings
    activity["favorites_order"] = [
        i for i in activity.get("favorites_order") or [] if int(i) & 0xFF != want
    ]
    return edited


def add_member(
    bundle: dict[str, Any],
    activity_id: int,
    device_id: int,
    *,
    input_command_id: Optional[int] = None,
) -> dict[str, Any]:
    """Add a device to an activity: the hub powers it on and off with it.

    Writes the device's power references into the activity's power on and
    power off sequences (as the vendor app's "add device" does), placed
    among the other members by device id. ``input_command_id`` makes one of
    the device's inputs (a command its input record lists) the one the
    activity selects at start; without it no input is selected. A
    ``sync_activity`` on ``activity_id`` writes it. Favorites and buttons
    for the device come afterwards, through the other helpers.
    """

    dev = int(device_id) & 0xFF
    act_id = int(activity_id) & 0xFF
    if not 0 < dev < _ACTIVITY_ID_BASE:
        raise ValueError(f"{dev} is not a device id")
    device = _find(bundle, "device", dev)
    ordinal = 0
    if input_command_id is not None:
        want = int(input_command_id) & 0xFF
        entries = (device.get("input_record") or {}).get("entries") or []
        ordinal = next(
            (
                int(entry.get("input_index") or 0)
                for entry in entries
                if isinstance(entry, dict) and int(entry.get("command_id") or 0) & 0xFF == want
            ),
            0,
        )
        if ordinal <= 0:
            raise ValueError(f"command {want} is not one of device {dev}'s inputs")
    edited, activity = _edit(bundle, "activity", act_id)
    if dev in _power_members(activity):
        raise ValueError(f"device {dev} is already in activity {act_id}")
    members = sorted(_power_members(activity) | {dev})
    macros = list(activity.get("macros") or [])
    for button_id, name, refs in (
        (_POWER_ON_MACRO_ID, "POWER_ON", ((_POWER_ON_REF, 0), (_INPUT_REF, ordinal))),
        (_POWER_OFF_MACRO_ID, "POWER_OFF", ((_POWER_OFF_REF, 0),)),
    ):
        index = next(
            (i for i, row in enumerate(macros) if int(row.get("button_id", 0)) & 0xFF == button_id), None
        )
        row = dict(macros[index]) if index is not None else {"button_id": button_id, "name": name}
        steps = list(row.get("steps") or [])
        for ref, duration in refs:
            steps.insert(_power_ref_insert_index(steps, members, dev, ref), _power_step(dev, ref, duration))
        row["steps"] = steps
        if index is None:
            macros.append(row)
        else:
            macros[index] = row
    activity["macros"] = macros
    activity["referenced_source_device_ids"] = sorted(
        {int(i) & 0xFF for i in activity.get("referenced_source_device_ids") or []} | {dev}
    )
    return edited


def _power_step(device_id: int, command_id: int, duration: int) -> dict[str, Any]:
    return {
        "device_id": device_id,
        "command_id": command_id,
        "button_code": 0,
        "duration": duration & 0xFF,
        "delay": _POWER_STEP_DELAY,
    }


def _is_delay_step(step: dict[str, Any]) -> bool:
    return int(step.get("device_id", 0)) == _DELAY_SENTINEL or int(step.get("command_id", 0)) == _DELAY_SENTINEL


def _power_members(activity: dict[str, Any]) -> set[int]:
    """Devices with a power reference in the activity's power sequences."""

    members: set[int] = set()
    for row in activity.get("macros") or []:
        if int(row.get("button_id", 0)) & 0xFF not in _POWER_MACRO_IDS:
            continue
        for step in row.get("steps") or []:
            if not _is_delay_step(step) and int(step.get("command_id", 0)) in _POWER_REFS:
                members.add(int(step.get("device_id", 0)) & 0xFF)
    return members


def _power_ref_insert_index(steps: list[dict[str, Any]], members: list[int], device_id: int, ref: int) -> int:
    """Where a new power reference goes: the input reference right after the
    device's power-on group (its trailing waits included), every other one
    before the first reference of a later member, else at the end (the
    vendor app's and the control panel's ordering)."""

    if ref == _INPUT_REF:
        for i, step in enumerate(steps):
            if (
                not _is_delay_step(step)
                and int(step.get("device_id", 0)) & 0xFF == device_id
                and int(step.get("command_id", 0)) == _POWER_ON_REF
            ):
                i += 1
                while i < len(steps) and _is_delay_step(steps[i]):
                    i += 1
                return i
    rank = {member: i for i, member in enumerate(members)}
    mine = rank.get(device_id, len(members))
    for i, step in enumerate(steps):
        if _is_delay_step(step) or int(step.get("command_id", 0)) not in _POWER_REFS:
            continue
        other = rank.get(int(step.get("device_id", 0)) & 0xFF)
        if other is not None and other > mine:
            return i
    return len(steps)


def rename_command(
    bundle: dict[str, Any], device_id: int, command_id: int, name: str
) -> dict[str, Any]:
    """Rename one of a device's commands (a ``sync_device`` on ``device_id``)."""

    edited, device = _edit(bundle, "device", device_id)
    want = int(command_id) & 0xFF
    for row in device.get("commands") or []:
        if int(row.get("command_id", -1)) & 0xFF == want:
            row["name"] = _clean_name(name, "a command")
            return edited
    raise KeyError(f"device {int(device_id) & 0xFF} has no command {want}")


def set_idle_behavior(bundle: dict[str, Any], device_id: int, mode: int) -> dict[str, Any]:
    """Set a device's automatic-power (idle) behaviour byte."""

    edited, device = _edit(bundle, "device", device_id)
    value = int(mode)
    if not 0 <= value <= 0xFF:
        raise ValueError("idle behaviour is one byte")
    device["device"]["idle_behavior"] = value
    return edited


def _check_payload_class(device: dict[str, Any], payload: CommandPayload) -> None:
    """A payload only goes on a device of its class: IR payloads on IR
    devices, a network command or a command record on a device of exactly
    its class. The hub would store a mismatched record and replay garbage."""

    block = device.get("device") or {}
    device_class = normalize_device_class(block.get("device_class"))
    if isinstance(payload, (NetworkCommand, CommandRecord)):
        if device_class != payload.device_class:
            raise ValueError(
                f"a {payload.device_class or 'class-less'} command cannot go on a "
                f"{device_class or 'device of unknown class'} device"
            )
        return
    # An IR payload is refused only on a device known to be a non-IR
    # class; bundles from tests and older captures carry looser class
    # words ("tv") that are not evidence of a mismatch.
    if device_class in _NON_IR_DEVICE_CLASSES:
        raise ValueError(f"an IR payload cannot go on a {device_class} device")


def set_command_payload(
    bundle: dict[str, Any], device_id: int, command_id: int, payload: CommandPayload
) -> dict[str, Any]:
    """Replace the stored payload of an existing command (an in-place overwrite).

    The command's label, library type and button code are preserved by the
    engine; only the bytes change. ``payload`` is an :class:`IrPayload` on
    an IR device, or a :class:`NetworkCommand` / :class:`CommandRecord` of
    the device's class (the executor re-encodes a network command's
    structured form through the canonical writer; a record's bytes are
    written as they are). A ``sync_device`` on ``device_id`` writes it.
    """

    edited, device = _edit(bundle, "device", device_id)
    _check_payload_class(device, payload)
    want = int(command_id) & 0xFF
    for row in device.get("commands") or []:
        if int(row.get("command_id", -1)) & 0xFF != want:
            continue
        previous = row.get("restore_data") if isinstance(row.get("restore_data"), dict) else {}
        if isinstance(payload, NetworkCommand):
            row["restore_data"] = {
                "transport": "hub_code_record",
                "library_type": int(previous.get("library_type", payload.library_type)) & 0xFF,
                "button_code": int(previous.get("button_code", 0)) & 0xFFFFFFFFFFFF,
                "data_hex": payload.blob.hex(),
                "decoded": payload.decoded,
            }
            return edited
        default_type = payload.library_type if isinstance(payload, CommandRecord) else 0x0D
        row["restore_data"] = {
            "transport": "hub_code_record",
            "library_type": int(previous.get("library_type", default_type)) & 0xFF,
            "button_code": int(previous.get("button_code", 0)) & 0xFFFFFFFFFFFF,
            "data_hex": payload.blob.hex(),
            "edited": True,
        }
        return edited
    raise KeyError(f"device {int(device_id) & 0xFF} has no command {want}")


def add_command(
    bundle: dict[str, Any], device_id: int, payload: CommandPayload, name: str,
    *, command_id: Optional[int] = None,
) -> tuple[dict[str, Any], int]:
    """Add a command with ``payload`` to a device; returns ``(edited, command_id)``.

    ``payload`` is an :class:`IrPayload` on an IR device, or a
    :class:`NetworkCommand` / :class:`CommandRecord` of the device's class
    (what :meth:`AsyncXProxy.read_payload` returned). Without ``command_id``
    the next free slot is taken. A ``sync_device`` on ``device_id``
    persists the record (the planner recognises the ``restore_data.new``
    marker).
    """

    edited, device = _edit(bundle, "device", device_id)
    _check_payload_class(device, payload)
    rows = device.setdefault("commands", [])
    used = {int(r.get("command_id", 0)) & 0xFF for r in rows}
    if command_id is None:
        slot = next((i for i in range(1, 256) if i not in used), None)
        if slot is None:
            raise ValueError("the device already uses all 255 command ids")
    else:
        slot = int(command_id) & 0xFF
        if slot < 1 or slot in used:
            raise ValueError(f"command id {slot} is not free on device {int(device_id) & 0xFF}")
    rows.append(payload.to_command_row(slot, _clean_name(name, "a command")))
    return edited, slot
