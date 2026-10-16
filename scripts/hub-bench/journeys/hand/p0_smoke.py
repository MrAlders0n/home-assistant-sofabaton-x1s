"""Phase 0 smoke journey: WS-driven, every step a checkpoint.

Proves the observation stack on a real hub rather than hunting: renames,
a rebind, a reorder, and the same button rebound in a second activity
(the #306 shape at the backend level: step 3's binding must survive step
5). Then every change is undone, so the hub ends as it started.

Entities are picked from what the hub has: the first two activities in
display order, a member device of each, a hard button already bound in the
first activity (OK when there is one).
"""

from __future__ import annotations

from typing import Any, Iterator

from ..actions import Ctx, Spec
from ..actions.ws import observe, put_binding_row, rename_activity, rename_device, reorder, set_binding

OK_BUTTON = 176


def _rows(bundle: dict[str, Any], kind: str) -> dict[int, dict[str, Any]]:
    return {int(r["device"]["device_id"]): r for r in bundle.get(kind) or []}


def _plain_member(bundle: dict[str, Any], activity: dict[str, Any], avoid: int | None = None) -> int:
    """A member device with commands that is neither hidden nor a managed
    Wifi device (renaming one of those starts a coordinated rename)."""

    devices = _rows(bundle, "devices")
    best = None
    for device_id in activity.get("referenced_source_device_ids") or []:
        row = devices.get(int(device_id))
        if row is None or device_id == avoid:
            continue
        block = row["device"]
        if block.get("hide") or str(block.get("brand") or "").startswith("m3-"):
            continue
        count = len(row.get("commands") or [])
        if count >= 3 and (best is None or count > best[0]):
            best = (count, int(device_id))
    if best is None:
        raise SystemExit("no plain member device with 3+ commands in the activity")
    return best[1]


def _other_command(bundle: dict[str, Any], device_id: int, not_id: int | None) -> int:
    commands = [int(c["command_id"]) for c in _rows(bundle, "devices")[device_id].get("commands") or []]
    return next(c for c in commands[1:] + commands[:1] if c != not_id)


def _binding(activity: dict[str, Any], button_id: int) -> dict[str, Any] | None:
    return next((dict(b) for b in activity.get("button_bindings") or []
                 if int(b.get("button_id") or 0) == button_id), None)


def _j(name: str) -> str:
    return (f"{name} J" if name.startswith("J ") else f"J {name}")[:30]


def journey(ctx: Ctx) -> Iterator[Spec]:
    bundle = ctx.bundle()
    order = ctx.snapshot.layers["cache"]["order"]["activities"]
    if len(order) < 2:
        raise SystemExit("the hub needs at least two activities")
    acts = _rows(bundle, "activities")
    a_id, b_id = order[0], order[1]
    act_a, act_b = acts[a_id], acts[b_id]
    bound = [int(b["button_id"]) for b in act_a.get("button_bindings") or [] if b.get("command_id")]
    button = OK_BUTTON if OK_BUTTON in bound else (bound[0] if bound else OK_BUTTON)
    orig_a, orig_b = _binding(act_a, button), _binding(act_b, button)
    button_name = (orig_a or orig_b or {}).get("button_name") or "OK"
    dev_a = _plain_member(bundle, act_a)
    dev_b = _plain_member(bundle, act_b, avoid=dev_a) if len(act_b.get("referenced_source_device_ids") or []) > 1 \
        else _plain_member(bundle, act_b)
    cmd_a = _other_command(bundle, dev_a, (orig_a or {}).get("command_id"))
    cmd_b = _other_command(bundle, dev_b, (orig_b or {}).get("command_id"))
    name_a = act_a["device"]["name"]
    name_dev = _rows(bundle, "devices")[dev_a]["device"]["name"]
    print(f"   picked activities {a_id} {name_a!r} + {b_id} {act_b['device']['name']!r}, button {button}, "
          f"A -> {dev_a}/{cmd_a}, B -> {dev_b}/{cmd_b}, rename device {dev_a} {name_dev!r}", flush=True)

    yield observe()
    yield rename_activity(a_id, _j(name_a))
    yield set_binding(a_id, button, (dev_a, cmd_a), button_name)
    yield reorder("activities", list(reversed(order)))
    yield set_binding(b_id, button, (dev_b, cmd_b), button_name)
    yield rename_device(dev_a, _j(name_dev))
    # Undo, in reverse.
    yield rename_device(dev_a, name_dev)
    yield put_binding_row(b_id, button, orig_b)
    yield put_binding_row(a_id, button, orig_a)
    yield reorder("activities", list(order))
    yield rename_activity(a_id, name_a)
    yield observe()
