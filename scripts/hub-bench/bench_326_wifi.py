"""Facade adoption bench B6.1 / B6.3 / B6.4: Wifi Commands through HA after W-B / W-C.

A bench-only Wifi Commands record ("Bench B326") on the given hub, its
slot 1 a favorite of the given (bench) activity, so the device is a
member and its power hooks fire. Cleanup: a zero-slot sync deletes the
device, then the record.

B6.1 (defect 1: clearing a power hook in place)
  - power ON = slot 1, power OFF = slot 2, deployed (the create);
  - positive control: the activity on and off fires slot 2's callback;
  - power OFF cleared, synced in place: the log shows
    ``wifi-power[... btn=0xC7]`` (an X1 skips the clear: a note, not a
    failure), the next sync plans nothing;
  - the activity on and off again: slot 2 no longer fires.
B6.4 (the baseline reads): each sync's window shows an activity catalog
  read (REQ_ACTIVITIES, the facade's best-effort read) ahead of the
  baseline reads; the in-place / replace decision lines are printed.
B6.3 (--events): a Wifi Event created and deployed, made a favorite of
  the activity, then deleted: a command_delete in the log, the event gone
  from the list and its favorite gone from the activity.

The callback check reads WIFI_HTTP lines (HTTP transport). On an X2
device on MQTT it is skipped with a note.

Usage: python bench_326_wifi.py --hub X1S --activity <bench activity id> [--events] [--skip-power]
"""

from __future__ import annotations

import argparse
import sys
import time

from ha_bench import (DOMAIN, Tally, bundle_activity, grep, hub_lines, log_mark, log_since, resolve_hub, rest,
                      service, structural_bundle, wait_for_ha, wire_sends)

parser = argparse.ArgumentParser()
parser.add_argument("--hub", required=True)
parser.add_argument("--activity", type=int, required=True, help="a bench activity: it is started and stopped")
parser.add_argument("--events", action="store_true")
parser.add_argument("--skip-power", action="store_true")
args = parser.parse_args()

ws = wait_for_ha()
hub = resolve_hub(ws, args.hub)
hub.wait_connected(ws)
sensor = hub.wifi_sensor
if sensor is None:
    sys.exit("this hub has no Wifi Commands sensor")
tally = Tally(f"bench_326_{hub.version.lower()}")
ACT = args.activity
BENCH = "Bench B326"
bundle, _ = structural_bundle(ws, hub)
act_row = bundle_activity(bundle or {}, ACT)
if act_row is None:
    sys.exit(f"activity {ACT} is not in the cache")
act_name = str((act_row.get("device") or {}).get("name") or "")


def slot(n: int, **extra) -> dict:
    return {"name": f"Command {n}", "add_as_favorite": False, "hard_button": "", "long_press_enabled": False,
            "input_activity_id": "", "activities": [], **extra}


def record() -> dict | None:
    for dev in ws.ok({"type": f"{DOMAIN}/command_devices/list", "entity_id": sensor}).get("devices") or []:
        if str(dev.get("device_name") or "") == BENCH:
            return dev
    return None


def progress(key: str) -> dict:
    return ws.ok({"type": f"{DOMAIN}/command_sync/progress", "entity_id": sensor, "device_key": key})


def run_sync(key: str, timeout: float = 900.0) -> tuple[dict, list[str]]:
    mark = log_mark()
    before = progress(key)
    rest(f"/api/services/{DOMAIN}/sync_command_config", {"device": hub.device, "device_key": key}, timeout=5)
    deadline = time.time() + timeout
    while time.time() < deadline:
        prog = progress(key)
        changed = any(prog.get(k) != before.get(k) for k in ("status", "message", "commands_hash", "current_step"))
        if changed and prog.get("status") in ("success", "failed"):
            time.sleep(2)
            return prog, log_since(mark)
        time.sleep(2)
    raise TimeoutError("sync did not finish")


def baseline_read_report(label: str, lines: list[str]) -> None:
    sends = wire_sends(lines, hub.entry_id)
    writes = [i for i, name in enumerate(sends) if not name.startswith("REQ_")]
    reads = [i for i, name in enumerate(sends) if name == "REQ_ACTIVITIES"]
    if not sends:
        tally.note(f"{label}: no [SEND] lines (integration not at debug)")
        return
    tally.check(f"B6.4 {label}: an activity catalog read ahead of the writes",
                bool(reads) and (not writes or reads[0] < writes[0]), f"reads at {reads[:3]}, first write {writes[:1]}")
    for line in grep(lines, "in-place sync")[:3]:
        print(f"    {line[:200]}", flush=True)


def config(slots: list[dict], **power) -> None:
    ws.ok({"type": f"{DOMAIN}/command_config/set", "entity_id": sensor, "commands": slots, "device_key": key, **power})


def presses(lines: list[str], dev: int, index: int) -> int:
    return len([line for line in lines if "WIFI_HTTP" in line and f"/{dev}/{index}/" in line
                and "accepted listener request" in line])


def cycle_activity() -> list[str]:
    mark = log_mark()
    rest("/api/services/remote/turn_on", {"entity_id": hub.remote, "activity": act_name}, timeout=60)
    time.sleep(15)
    rest("/api/services/remote/turn_off", {"entity_id": hub.remote}, timeout=60)
    time.sleep(15)
    return log_since(mark)


key = str((record() or {}).get("device_key") or "") or str(
    ws.ok({"type": f"{DOMAIN}/command_device/create", "entity_id": sensor, "device_name": BENCH}).get("device_key") or "")
print(f"bench record {key!r}, activity {ACT} {act_name!r}", flush=True)
slots = [slot(n) for n in range(1, 11)]
slots[0] = slot(1, name="B326 On", add_as_favorite=True, activities=[str(ACT)])
slots[1] = slot(2, name="B326 Off")

try:
    if not args.skip_power:
        tally.section("B6.1: deploy with power ON and OFF")
        config(slots, power_on_command_id=1, power_off_command_id=2)
        prog, lines = run_sync(key)
        tally.check("deployed", prog.get("status") == "success", str(prog.get("message")))
        baseline_read_report("create", lines)
        wifi_dev = (record() or {}).get("deployed_device_id")
        members = {int(i) for i in (bundle_activity(structural_bundle(ws, hub)[0] or {}, ACT) or {})
                   .get("referenced_source_device_ids") or []}
        tally.check("the device is a member of the activity", wifi_dev in members, f"dev={wifi_dev} members={members}")
        http = hub.version.upper() != "X2" or str((record() or {}).get("deployed_transport") or "http").lower() != "mqtt"
        if isinstance(wifi_dev, int) and http:
            fired = presses(cycle_activity(), wifi_dev, 1)
            tally.check("positive control: stopping the activity fires power OFF", fired >= 1, f"{fired} presses")

        tally.section("B6.1: clear power OFF, sync in place")
        config(slots, power_on_command_id=1)
        prog, lines = run_sync(key)
        tally.check("synced", prog.get("status") == "success", str(prog.get("message")))
        clears = [line for line in hub_lines(lines, hub.entry_id) if "wifi-power[" in line and "btn=0xC7" in line]
        if hub.version.upper() == "X1":
            tally.note(f"X1: the clear is skipped by design ({len(clears)} wifi-power C7 lines)")
        else:
            tally.check("wifi-power[... btn=0xC7] written", bool(clears), f"{len(clears)} lines")
        baseline_read_report("in place", lines)
        prog, lines = run_sync(key)
        tally.check("the next sync plans nothing", prog.get("status") == "success"
                    and not [line for line in lines if "wifi-power[" in line], str(prog.get("message")))
        if isinstance(wifi_dev, int) and http and hub.version.upper() != "X1":
            fired = presses(cycle_activity(), wifi_dev, 1)
            tally.check("stopping the activity no longer fires power OFF", fired == 0, f"{fired} presses")
        elif not http:
            tally.note("MQTT transport: callback checks skipped (watch the MQTT log by hand)")

    if args.events:
        tally.section("B6.3: a Wifi Event deleted")
        state = ws.ok({"type": f"{DOMAIN}/wifi_event/create", "entity_id": sensor, "name": "B326 Event"})
        events = state.get("events") or []
        mine = next((e for e in events if str(e.get("name")) == "B326 Event"), None)
        res, _ = ws.call({"type": f"{DOMAIN}/wifi_event/sync", "entity_id": sensor}, timeout=900)
        tally.check("events deployed", bool(res.get("success")), str(res.get("error") or ""))
        state = (res.get("result") or {}) if res.get("success") else state
        mine = next((e for e in state.get("events") or [] if str(e.get("name")) == "B326 Event"), mine)
        ev_dev = state.get("device_id") or (mine or {}).get("device_id")
        slot_index = int((mine or {}).get("slot_index", -1))
        if mine is None or slot_index < 0 or not isinstance(ev_dev, int):
            tally.check("the new event and its device", False, str(mine))
        else:
            status, _ = service("command_to_favorite", {"device": hub.device, "activity_id": ACT,
                                                        "device_id": ev_dev, "command_id": slot_index + 1})
            tally.check("event made a favorite", status == 200, str(status))
            mark = log_mark()
            res, _ = ws.call({"type": f"{DOMAIN}/wifi_event/delete", "entity_id": sensor,
                              "slot_index": slot_index}, timeout=900)
            time.sleep(3)
            lines = log_since(mark)
            tally.check("delete answered", bool(res.get("success")), str(res.get("error") or ""))
            deletes = grep(hub_lines(lines, hub.entry_id), "[STEP] wifi-command-delete[")
            tally.check("one wifi-command-delete step (the plan walk)", len(deletes) >= 1, f"{len(deletes)} tx lines")
            names = [str(e.get("name")) for e in ((res.get("result") or {}).get("events") or [])]
            tally.check("event gone from the list", "B326 Event" not in names, str(names))
            favs = (bundle_activity(structural_bundle(ws, hub)[0] or {}, ACT) or {}).get("favorite_slots") or []
            tally.check("its favorite cascaded away",
                        not any(int(f.get("device_id") or 0) == ev_dev and int(f.get("command_id") or 0) == slot_index + 1
                                for f in favs))
finally:
    tally.section("cleanup")
    config([slot(n) for n in range(1, 11)])
    prog, _ = run_sync(key)
    tally.check("zero-slot sync removed the device", prog.get("status") == "success", str(prog.get("message")))
    try:
        ws.ok({"type": f"{DOMAIN}/command_device/delete", "entity_id": sensor, "device_key": key})
    except RuntimeError as err:
        tally.note(f"bench record delete: {err}")
sys.exit(tally.finish())
