"""Facade adoption bench B7.1 / B7.2: the single-shot services as edit + sync (S1).

Each service runs once against one activity and one IR device. The result
is read from the integration's cache (cache/structural_bundle): what the
Hub tab and the editors show without a refresh. The run ends with an
undo: the activity and the device synced back to how they were (the
live editors' activity/sync and device/sync).

Steps:
 1. command_to_favorite with a slot_id: warning logged, favorite last;
 2. reorder_favorites: the full list reversed, a partial list, an
    unknown id (warning, skipped);
 3. delete_favorite: the favorite from step 1;
 4. delete_favorite of a macro (--macro-id, a disposable macro): the
    macro and any binding naming it go;
 5. command_to_button on a bound button with a long press, then without
    (the long press cleared);
 6. device_to_activity: a non-member with an input (when one has an
    input record), an existing member's input change (when its record has
    two), an existing member without an input (nothing written: the
    cache generation stays);
 7. persist_ir_blob: a copy of an existing command's raw blob under a new
    name (with --descriptor, also a ``P:`` descriptor; X2);
 8. B7.2: a service while a backup runs is refused (hub_busy);
 9. undo, then compare with the start.

Usage:
    python bench_322_services.py [--hub X1] [--activity 102] [--device 1] [--macro-id N]
                                 [--descriptor "P:NEC1 ..."]
"""

from __future__ import annotations

import argparse
import copy
import sys
import time

from ha_bench import (DOMAIN, Tally, bundle_activity, bundle_device, grep, log_mark, log_since, resolve_hub,
                      service, structural_bundle, wait_backup_state, wait_for_ha, watch_operation)

parser = argparse.ArgumentParser()
parser.add_argument("--hub", default="X1")
parser.add_argument("--activity", type=int, default=102)
parser.add_argument("--device", type=int, default=1, help="an IR device that is a member of the activity (favorite source, blob copy)")
parser.add_argument("--macro-id", type=int, default=None)
parser.add_argument("--descriptor", default=None)
args = parser.parse_args()

ws = wait_for_ha()
hub = resolve_hub(ws, args.hub)
hub.wait_connected(ws)
tally = Tally(f"bench_322_{hub.version.lower()}")
ACT = args.activity

start_bundle, start_gen = structural_bundle(ws, hub)
if not start_bundle:
    sys.exit("no structural bundle: turn the persistent cache on and open the Hub tab once")
start_activity = copy.deepcopy(bundle_activity(start_bundle, ACT))
start_device = copy.deepcopy(bundle_device(start_bundle, args.device))
if start_activity is None or start_device is None:
    sys.exit(f"activity {ACT} or device {args.device} is not in the cache; refresh them on the Hub tab")


def svc(name: str, data: dict, *, response: bool = False) -> tuple[int, object, list[str]]:
    mark = log_mark()
    status, body = service(name, {"device": hub.device, **data}, response=response)
    time.sleep(1.5)
    return status, body, log_since(mark)


def favorites() -> list[dict]:
    status, body, _ = svc("get_favorites", {"activity_id": ACT}, response=True)
    if status != 200 or not isinstance(body, dict):
        raise RuntimeError(f"get_favorites: HTTP {status}")
    return list(body.get("favorites") or [])


def fav_ids(rows: list[dict]) -> list[int]:
    return [int(r.get("fav_id") or 0) for r in rows]


def act_now() -> dict:
    bundle, _ = structural_bundle(ws, hub)
    return bundle_activity(bundle or {}, ACT) or {}


def binding(activity: dict, button_id: int) -> dict | None:
    return next((r for r in activity.get("button_bindings") or [] if int(r.get("button_id") or 0) == button_id), None)


commands = [int(c.get("command_id") or 0) for c in start_device.get("commands") or []]
if len(commands) < 2:
    sys.exit(f"device {args.device} needs two commands")

# -- 1: command_to_favorite -----------------------------------------------------------------------

tally.section("1: command_to_favorite (slot_id given)")
before = favorites()
existing = {(int(r.get("device_id") or 0), int(r.get("command_id") or 0)) for r in before if r.get("type") == "favorite"}
fav_command = next((c for c in commands if (args.device, c) not in existing), None)
if fav_command is None:
    sys.exit(f"every command of device {args.device} is already a favorite of activity {ACT}")
status, _, lines = svc("command_to_favorite", {"activity_id": ACT, "device_id": args.device,
                                               "command_id": fav_command, "slot_id": 3})
tally.check("accepted", status == 200, str(status))
tally.check("slot_id warning logged", bool(grep(lines, "slot_id 3 has no effect")))
after = favorites()
added = [r for r in after if int(r.get("fav_id") or 0) not in fav_ids(before)]
tally.check("one new favorite, last", len(added) == 1 and after[-1] is added[0],
            f"order {fav_ids(after)}; new {fav_ids(added)}")
tally.check("cache follows without a refresh",
            any(int(r.get("device_id") or 0) == args.device and int(r.get("command_id") or 0) == fav_command
                for r in act_now().get("favorite_slots") or []))
new_fav = int(added[0]["fav_id"]) if added else None

# -- 2: reorder_favorites -------------------------------------------------------------------------

tally.section("2: reorder_favorites")
order = fav_ids(after)
status, _, _ = svc("reorder_favorites", {"activity_id": ACT, "ordered_fav_ids": list(reversed(order))})
tally.check("full list reversed", status == 200 and fav_ids(favorites()) == list(reversed(order)),
            f"{fav_ids(favorites())}")
current = fav_ids(favorites())
status, _, _ = svc("reorder_favorites", {"activity_id": ACT, "ordered_fav_ids": [current[-1]]})
expected = [current[-1]] + current[:-1]
tally.check("partial list: named first, rest kept", status == 200 and fav_ids(favorites()) == expected,
            f"{fav_ids(favorites())} vs {expected}")
unknown = next(i for i in range(250, 1, -1) if i not in current)
status, _, lines = svc("reorder_favorites", {"activity_id": ACT, "ordered_fav_ids": [unknown] + order})
tally.check("unknown id skipped with a warning",
            status == 200 and bool(grep(lines, f"fav_id {unknown} is not in the activity"))
            and fav_ids(favorites()) == order, f"{fav_ids(favorites())}")

# -- 3/4: delete_favorite -------------------------------------------------------------------------

tally.section("3: delete_favorite (the new favorite)")
if new_fav is not None:
    status, _, _ = svc("delete_favorite", {"activity_id": ACT, "fav_id": new_fav})
    tally.check("favorite gone", status == 200 and new_fav not in fav_ids(favorites()), f"{fav_ids(favorites())}")
status, _, lines = svc("delete_favorite", {"activity_id": ACT, "fav_id": 198})
tally.check("a power macro id is refused", status != 200 and bool(grep(lines, "not a favorite or macro")), str(status))

if args.macro_id:
    tally.section(f"4: delete_favorite (macro {args.macro_id})")
    bound = [int(r.get("button_id") or 0) for r in act_now().get("button_bindings") or []
             if int(r.get("device_id") or 0) == ACT and int(r.get("command_id") or 0) == args.macro_id]
    status, _, _ = svc("delete_favorite", {"activity_id": ACT, "fav_id": args.macro_id})
    now = act_now()
    tally.check("macro gone", status == 200 and all(int(m.get("button_id") or 0) != args.macro_id
                                                    for m in now.get("macros") or []), str(status))
    tally.check("its bindings gone", not any(binding(now, b) for b in bound), f"were on {bound}")

# -- 5: command_to_button -------------------------------------------------------------------------

tally.section("5: command_to_button")
rows = [r for r in start_activity.get("button_bindings") or [] if int(r.get("device_id") or 0) != ACT]
if not rows:
    tally.note("the activity binds no device command to any button; step 5 skipped")
else:
    button = int(rows[0]["button_id"])
    short_cmd, long_cmd = commands[0], commands[1]
    status, _, _ = svc("command_to_button", {"activity_id": ACT, "button_id": button, "device_id": args.device,
                                             "command_id": short_cmd, "long_press_device_id": args.device,
                                             "long_press_command_id": long_cmd})
    row = binding(act_now(), button) or {}
    tally.check("bound with a long press", status == 200 and (row.get("device_id"), row.get("command_id"),
                row.get("long_press_device_id"), row.get("long_press_command_id"))
                == (args.device, short_cmd, args.device, long_cmd), str(row))
    status, _, _ = svc("command_to_button", {"activity_id": ACT, "button_id": button, "device_id": args.device,
                                             "command_id": long_cmd})
    row = binding(act_now(), button) or {}
    tally.check("rebound, long press cleared", status == 200 and row.get("command_id") == long_cmd
                and row.get("long_press_command_id") in (None, 0), str(row))

# -- 6: device_to_activity ------------------------------------------------------------------------

tally.section("6: device_to_activity")
members = {int(i) for i in start_activity.get("referenced_source_device_ids") or []}


def inputs_of(device: dict) -> list[int]:
    return [int(e.get("command_id") or 0) for e in (device.get("input_record") or {}).get("entries") or []]


candidates = [d for d in start_bundle.get("devices") or []
              if int((d.get("device") or {}).get("device_id") or 0) not in members and inputs_of(d)]
if candidates:
    newcomer = candidates[0]
    new_id = int(newcomer["device"]["device_id"])
    first_input = inputs_of(newcomer)[0]
    status, _, _ = svc("device_to_activity", {"activity_id": ACT, "device_id": new_id,
                                              "input_command_id": first_input})
    now = act_now()
    power_on = next((m for m in now.get("macros") or [] if int(m.get("button_id") or 0) == 198), {})
    tally.check(f"device {new_id} joined", status == 200 and new_id in
                {int(i) for i in now.get("referenced_source_device_ids") or []}, str(status))
    tally.check("its power-on steps are in POWER_ON",
                any(int(s.get("device_id") or 0) == new_id for s in power_on.get("steps") or []))
    if len(inputs_of(newcomer)) > 1:
        status, _, _ = svc("device_to_activity", {"activity_id": ACT, "device_id": new_id,
                                                  "input_command_id": inputs_of(newcomer)[1]})
        tally.check("an existing member's input changed", status == 200, str(status))
    else:
        tally.note(f"device {new_id} has one input; the input-change case is not exercised")
else:
    tally.note("no non-member device with an input record; the new-member case is not exercised")
member = sorted(members)[0] if members else None
if member is not None:
    _, gen_before = structural_bundle(ws, hub)
    status, _, lines = svc("device_to_activity", {"activity_id": ACT, "device_id": member})
    _, gen_after = structural_bundle(ws, hub)
    tally.check("an existing member without an input writes nothing",
                status == 200 and gen_before == gen_after, f"generation {gen_before} -> {gen_after}")

# -- 7: persist_ir_blob ---------------------------------------------------------------------------

tally.section("7: persist_ir_blob")
status, fetched, _ = svc("fetch_blob", {"device_id": args.device, "command_id": commands[0]}, response=True)
rows = (fetched or {}).get("commands") or [] if isinstance(fetched, dict) else []
blob = next((str(r.get("command_blob")) for r in rows if r.get("command_blob")), None)
if not blob:
    tally.check("a blob to copy", False, f"fetch_blob HTTP {status}")
else:
    status, _, _ = svc("persist_ir_blob", {"device_id": args.device, "command_name": "B322 Copy", "blob": blob})
    names = [str(c.get("name")) for c in (bundle_device(structural_bundle(ws, hub)[0] or {}, args.device) or {})
             .get("commands") or []]
    tally.check("raw blob persisted as a new command", status == 200 and "B322 Copy" in names, str(status))
if args.descriptor:
    status, _, _ = svc("persist_ir_blob", {"device_id": args.device, "command_name": "B322 Desc",
                                           "blob": args.descriptor})
    names = [str(c.get("name")) for c in (bundle_device(structural_bundle(ws, hub)[0] or {}, args.device) or {})
             .get("commands") or []]
    tally.check("descriptor persisted as a new command", status == 200 and "B322 Desc" in names, str(status))

# -- 8: B7.2 hub busy -----------------------------------------------------------------------------

tally.section("8: a service while a backup runs")
ws.ok({"type": f"{DOMAIN}/backup/export", "entry_id": hub.entry_id, "device_ids": [args.device]})
time.sleep(0.5)
status, _, lines = svc("reorder_favorites", {"activity_id": ACT, "ordered_fav_ids": order})
tally.check("refused with hub_busy", status != 200 and bool(grep(lines, "hub_busy")), f"HTTP {status}")
wait_backup_state(ws, hub, "backup_export")

# -- 9: undo --------------------------------------------------------------------------------------

tally.section("9: undo (activity and device synced back)")
now_bundle, _ = structural_bundle(ws, hub)
for kind, ent_id, original in (("activity", ACT, start_activity), ("device", args.device, start_device)):
    current = (bundle_activity if kind == "activity" else bundle_device)(now_bundle or {}, ent_id)
    edited = copy.deepcopy(now_bundle)
    key = "activities" if kind == "activity" else "devices"
    edited[key] = [original if int((r.get("device") or {}).get("device_id") or 0) == ent_id else r
                   for r in edited.get(key) or []]
    if current == original:
        continue
    res, _ = ws.call({"type": f"{DOMAIN}/{kind}/sync", "entry_id": hub.entry_id, f"{kind}_id": ent_id,
                      "baseline": now_bundle, "edited": edited}, timeout=900)
    if not tally.check(f"{kind} {ent_id} sync back accepted", bool(res.get("success")),
                       str(res.get("error") or "")):
        continue
    events = watch_operation(ws, (res.get("result") or {})["operation_id"], timeout=900)
    final = events[-1] if events else {}
    tally.check(f"{kind} {ent_id} synced back", final.get("status") == "success", str(final.get("message")))
    time.sleep(2)
    now_bundle, _ = structural_bundle(ws, hub)
end_activity = bundle_activity(now_bundle or {}, ACT) or {}
for field in ("referenced_source_device_ids", "button_bindings", "favorite_slots"):
    tally.check(f"activity {field} as at the start",
                len(end_activity.get(field) or []) == len(start_activity.get(field) or []),
                f"{len(start_activity.get(field) or [])} -> {len(end_activity.get(field) or [])}")
end_device = bundle_device(now_bundle or {}, args.device) or {}
tally.check("device commands as at the start",
            len(end_device.get("commands") or []) == len(start_device.get("commands") or []),
            f"{len(start_device.get('commands') or [])} -> {len(end_device.get('commands') or [])}")
if args.macro_id:
    tally.note(f"macro {args.macro_id} was recreated by the undo; its id may differ")
sys.exit(tally.finish())
