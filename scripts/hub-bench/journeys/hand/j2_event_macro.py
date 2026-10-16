"""J2: Wifi Events in shortcuts, a button and a macro, then one Sync
(Marcel's example, plan 4.3), driven through the activity editor.

On activity A (first in display order):

1. Add shortcut -> new Wifi Event "J Lights" (staged in HA at once)
2. Customize buttons -> a free color button -> new Wifi Event "J Dim"
3. move "J Lights" to the top of the shortcuts
4. new macro "J Movie": a member device's command (wait 1.5 s), then the
   "J Lights" event
5. Sync: the events deploy first (on a hub without the Wifi Events device
   it is created), then the activity. After it, on the hub: the first
   shortcut is J Lights, the button runs J Dim, the macro holds the command
   then J Lights; and the editor says "Up to date".
6. clean up through the UI: delete the macro, the shortcut and the button,
   sync; delete both events in the Wifi Events device editor, sync; on a hub
   that had no Wifi Events device before, delete that device too. The hub
   must end as it started.

Every edit before Sync must leave HA (except the staged events), the cache
and the hub untouched.
"""

from __future__ import annotations

from typing import Any, Iterator

from ..actions import Ctx, Spec
from ..actions import editor_steps as ed
from ..actions.ws import observe, same_as_start

NEEDS_ACTOR = True
COLOR_BUTTONS = [("red", 190, "Red"), ("green", 191, "Green"), ("yellow", 192, "Yellow"), ("blue", 193, "Blue")]


def _plain_member(cache: dict[str, Any], activity: dict[str, Any]) -> tuple[int, int]:
    for dev_id in activity["members"]:
        dev = cache["devices"].get(str(dev_id)) or {}
        head = dev.get("head") or {}
        if head.get("hide") or str(head.get("brand") or "").startswith("m3-") or not dev.get("commands"):
            continue
        return int(dev_id), int(sorted(dev["commands"], key=int)[0])
    raise SystemExit("J2 needs a plain member device with commands")


def journey(ctx: Ctx) -> Iterator[Spec]:
    cache = ctx.snapshot.before()["cache"]
    ctx.known["start_hub"] = cache
    a_id = cache["order"]["activities"][0]
    act = cache["activities"][str(a_id)]
    a = act["head"]["name"]
    free = [b for b in COLOR_BUTTONS if str(b[1]) not in act["bindings"]]
    if not free:
        raise SystemExit(f"J2 needs a free color button in {a!r}")
    _value, button_id, button_label = free[0]
    dev, cmd = _plain_member(cache, act)
    events_device_before = ((ctx.snapshot.before()["ha"].get("events") or {}).get("record") or {}).get("device_id")
    print(f"   J2: activity {a_id} {a!r}, button {button_label} ({button_id}), macro command {dev}/{cmd}, "
          f"Wifi Events device before: {events_device_before}", flush=True)

    def after_sync(ctx: Ctx, after: dict) -> list[str]:
        problems = []
        events = ed.events_by_name(after["ha"])
        lights, dim = events.get("J Lights"), events.get("J Dim")
        if not lights or not dim:
            return [f"events after sync: {sorted(events)}"]
        ev = lights.get("device_id")
        hub_act = after["cache"]["activities"][str(a_id)]
        want_first = f"fav:{ev}/{lights.get('command_id')}"
        if not hub_act["shortcuts"] or hub_act["shortcuts"][0] != want_first:
            problems.append(f"first shortcut is {hub_act['shortcuts'][:1]}, want {want_first}")
        got = (hub_act["bindings"].get(str(button_id)) or {}).get("short")
        if got != f"{ev}/{dim.get('command_id')}":
            problems.append(f"button {button_id} runs {got}, want {ev}/{dim.get('command_id')} (J Dim)")
        movie = [m for m in hub_act["macros"].values() if m.get("name") == "J Movie"]
        if len(movie) != 1:
            problems.append(f"{len(movie)} macros named J Movie")
        else:
            # A wait is a step of its own on the hub: 255/255 (a delay row).
            refs = [s["ref"] for s in movie[0]["steps"]]
            want = [f"{dev}/{cmd}", "255/255", f"{ev}/{lights.get('command_id')}"]
            if refs != want:
                problems.append(f"J Movie steps {refs}, want {want} (command, the 1.5 s wait, J Lights)")
        return problems

    yield observe()
    yield ed.new_event_shortcut(a, "J Lights")
    yield ed.new_event_binding(a, str(button_id), "J Dim")
    yield ed.move_to_top(a, "J Lights")
    yield ed.macro(a, "J Movie", [{"kind": "command", "device": dev, "command": cmd, "wait": 1.5},
                                  {"kind": "wifi_event", "event": "J Lights"}])
    yield ed.sync(a_id, a, post=after_sync)
    yield ed.up_to_date(a)

    # Clean up through the UI.
    yield ed.delete_row(a, "J Movie")
    yield ed.delete_row(a, "J Lights")
    yield ed.delete_binding(a, button_label)
    yield ed.sync(a_id, a)
    yield ed.delete_events(["J Lights", "J Dim"])
    if events_device_before is None:
        ev_now = ((ctx.snapshot.before()["ha"].get("events") or {}).get("record") or {}).get("device_id")
        yield ed.delete_hub_device("Wifi Events", int(ev_now) if ev_now is not None else None)
    yield observe()
    yield same_as_start()
