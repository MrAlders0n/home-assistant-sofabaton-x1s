"""The X2 gate (plan section 7.2): a minimal sandbox round trip that must
leave the real configuration untouched.

Created, used and deleted again, all named ``J ``:

- two empty IR devices (WS, the Hub tab's Add device call);
- an activity "J Gate" (WS);
- two Wifi Commands devices through the UI (on an X2 they deploy over
  MQTT, the dialog's default), each with one slot in "J Gate" only: one as
  a favorite, one on the Red button.

Wifi Events are left out (adding and removing an event rewrites the one
shared events device).

Before and after: a full export with payloads. The real configuration and
the display order must come back identical (``same_as_start`` on the
structural read, ``full_same`` on the exports). The free capacity is in
the trace (the counts before the sandbox).
"""

from __future__ import annotations

from typing import Iterator

from ..actions import ACTIVITY_NAME_IN_ENTITIES, Ctx, Spec, on_entities, on_hub
from ..actions import sandbox as sb
from ..actions import wifi_steps as wifi
from ..actions.ws import observe, same_as_start

NEEDS_ACTOR = True
X2_ALLOWED = True
RED = wifi.BUTTON_IDS["red"]


def journey(ctx: Ctx) -> Iterator[Spec]:
    cache = ctx.snapshot.before()["cache"]
    ctx.known["start_hub"] = cache
    wifi_count = len(ctx.snapshot.before()["ha"]["wifi"])
    print(f"   gate: {len(cache['devices'])} devices, {len(cache['activities'])} activities, "
          f"{wifi_count} Wifi Commands devices before the sandbox", flush=True)

    yield observe()
    yield sb.full_export("before")
    yield sb.create_device("J Gate IR 1")
    yield sb.create_device("J Gate IR 2")
    yield sb.create_activity("J Gate")
    yield sb.ui_reload()

    yield wifi.create("J Gate A")
    yield wifi.slot(ctx, "J Gate A", 0, name="J Gate Fav", hard_button=None, activities=["J Gate"], favorite=True)
    yield wifi.sync(ctx, "J Gate A", [{"index": 0, "hard_button": None, "activities": ["J Gate"]}])
    yield wifi.create("J Gate B")
    yield wifi.slot(ctx, "J Gate B", 0, name="J Gate Red", hard_button="red", activities=["J Gate"])
    yield wifi.sync(ctx, "J Gate B", [{"index": 0, "hard_button": "red", "activities": ["J Gate"]}])
    yield wifi.pills(["J Gate A", "J Gate B"])

    act = ctx.known["act:J Gate"]
    # Deleting the last member may make the hub delete "J Gate" itself.
    gc = on_hub(f"activities/{act}", "order/activities") + ACTIVITY_NAME_IN_ENTITIES + on_entities(
        f"sensor.*_index/attrs/activities/{act}", f"remote.*/attrs/assigned_keys/{act}",
        "remote.*/attrs/macro_keys", "remote.*/attrs/favorite_keys")
    yield wifi.delete(ctx, "J Gate B", activities=["J Gate"], gone_bindings=[("J Gate", RED)])
    yield wifi.delete(ctx, "J Gate A", activities=["J Gate"], also_allow=gc, also_forget=[f"activities/{act}"])
    # The hub may instead leave "J Gate" empty and sweep it at the next
    # device delete (X2, 2026-10-16 17:18): allowed there too.
    yield sb.delete_device(ctx, "J Gate IR 1", also_allow=gc, also_forget=[f"activities/{act}"])
    yield sb.delete_device(ctx, "J Gate IR 2", also_allow=gc, also_forget=[f"activities/{act}"])
    yield sb.delete_activity(ctx, "J Gate")
    yield observe()
    yield same_as_start()
    yield sb.full_export("after")
    yield sb.full_same("before", "after")
