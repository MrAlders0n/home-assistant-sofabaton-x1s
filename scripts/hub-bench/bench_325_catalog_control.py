"""Facade adoption bench B1.4 / B2.5 / B3.1 (scripted parts): catalog reads,
refreshes and control through HA.

1. Catalog refresh (devices, activities): each answers, the counts match
   the cached catalog (B3.1).
2. Whole-hub cache refresh, then a per-entity refresh of one device and
   one activity: each succeeds, the cache generation moves, the Hub tab
   counts match the structural bundle (B2.5).
3. Control (B1.4): an activity on and off through the remote entity (its
   state follows), a key press with ``device`` (raw command) and without
   (a button name in the current activity), Find remote and Resync remote
   (buttons), the device power state read.
4. With --with-app: the stand-in app holds a proxy session (B3.1): a
   catalog refresh fails at once, the cached catalog stays; the session
   released, the hub comes back and a refresh works. Needs the dial-back
   to reach this box (Windows firewall: allow python on the private
   network); skipped with a note when it does not arrive.

The UI halves (remote card buttons, the Hub tab display, the X2 MQTT
flips) stay manual: see the runbook.

Usage: python bench_325_catalog_control.py [--hub X1] [--activity-name NAME] [--device 12] [--with-app]
"""

from __future__ import annotations

import argparse
import sys
import time

from ha_bench import (DOMAIN, FakeApp, Tally, log_mark, log_since, resolve_hub, rest, structural_bundle,
                      wait_for_ha, watch_operation, wire_sends)

parser = argparse.ArgumentParser()
parser.add_argument("--hub", default="X1")
parser.add_argument("--activity-name", default=None)
parser.add_argument("--device", type=int, default=12)
parser.add_argument("--with-app", action="store_true")
args = parser.parse_args()

ws = wait_for_ha()
hub = resolve_hub(ws, args.hub)
hub.wait_connected(ws)
tally = Tally(f"bench_325_{hub.version.lower()}")


def remote_state() -> dict:
    status, body, _ = rest(f"/api/states/{hub.remote}")
    return body if status == 200 and isinstance(body, dict) else {}


# -- 1: catalog refresh ---------------------------------------------------------------------------

tally.section("1: catalog refresh")
for kind in ("devices", "activities"):
    res, dt = ws.call({"type": f"{DOMAIN}/catalog/refresh", "entry_id": hub.entry_id, "kind": kind}, timeout=120)
    tally.check(f"{kind} refresh answered", bool(res.get("success")), f"{dt:.1f}s {res.get('error') or ''}")
state = hub.panel_state(ws)
bundle, gen0 = structural_bundle(ws, hub)
if bundle:
    tally.check("device count matches the cache", state.get("device_count") == len(bundle.get("devices") or []),
                f"{state.get('device_count')} vs {len(bundle.get('devices') or [])}")
    tally.check("activity count matches the cache",
                state.get("activity_count") == len(bundle.get("activities") or []),
                f"{state.get('activity_count')} vs {len(bundle.get('activities') or [])}")

# -- 2: refreshes ---------------------------------------------------------------------------------

tally.section("2: whole-hub and per-entity refresh")
op = ws.ok({"type": f"{DOMAIN}/cache/refresh_all", "entry_id": hub.entry_id})["operation_id"]
events = watch_operation(ws, op, timeout=1800)
tally.check("refresh all", (events[-1] if events else {}).get("status") == "success",
            str((events[-1] if events else {}).get("message")))
bundle, gen1 = structural_bundle(ws, hub)
activity_id = next((int((r.get("device") or {}).get("device_id") or 0) for r in (bundle or {}).get("activities") or []),
                   0)
for kind, target in (("device", args.device), ("activity", activity_id)):
    if not target:
        continue
    res, dt = ws.call({"type": f"{DOMAIN}/persistent_cache/refresh", "entry_id": hub.entry_id, "kind": kind,
                       "target_id": target}, timeout=300)
    tally.check(f"{kind} {target} refreshed", bool(res.get("success")), f"{dt:.1f}s {res.get('error') or ''}")
_, gen2 = structural_bundle(ws, hub)
tally.check("cache generation moved", gen0 != gen2, f"{gen0} -> {gen1} -> {gen2}")

# -- 3: control -----------------------------------------------------------------------------------

tally.section("3: control")
st = remote_state()
names = [a.get("name") for a in (st.get("attributes") or {}).get("activities") or []]
name = args.activity_name or (names[0] if names else None)
if name is None:
    tally.check("an activity to start", False, "the remote lists none")
else:
    status, _, dt = rest("/api/services/remote/turn_on", {"entity_id": hub.remote, "activity": name}, timeout=60)
    deadline = time.time() + 30
    while time.time() < deadline and (remote_state().get("attributes") or {}).get("current_activity") != name:
        time.sleep(1)
    tally.check(f"activity {name!r} on", status == 200
                and (remote_state().get("attributes") or {}).get("current_activity") == name, f"{dt:.1f}s")
    mark = log_mark()
    status, _, _ = rest("/api/services/remote/send_command", {"entity_id": hub.remote, "command": ["VOL_UP"]})
    tally.check("button name in the current activity", status == 200, str(status))
    status, _, _ = rest("/api/services/remote/send_command",
                        {"entity_id": hub.remote, "command": ["1"], "device": args.device})
    tally.check("raw command with device", status == 200, str(status))
    time.sleep(3)
    print(f"  wire: {wire_sends(log_since(mark), hub.entry_id)}", flush=True)
    status, _, dt = rest("/api/services/remote/turn_off", {"entity_id": hub.remote}, timeout=60)
    deadline = time.time() + 45
    while time.time() < deadline and remote_state().get("state") != "off":
        time.sleep(1)
    tally.check("activity off", status == 200 and remote_state().get("state") == "off", f"{dt:.1f}s")
for label, entity in (("find remote", hub.find_button), ("resync remote", hub.resync_button)):
    if entity:
        status, _, _ = rest("/api/services/button/press", {"entity_id": entity})
        tally.check(label, status == 200, str(status))
        time.sleep(3)
res, dt = ws.call({"type": f"{DOMAIN}/device/power_state", "entry_id": hub.entry_id, "device_id": args.device},
                  timeout=60)
tally.check("device power state read", bool(res.get("success")), f"{dt:.1f}s {res.get('result')}")

# -- 4: with the app connected --------------------------------------------------------------------

if args.with_app:
    tally.section("4: the stand-in app holds a session")
    app = FakeApp(hub)
    app.hold = True
    app.call_me()
    deadline = time.time() + 20
    while time.time() < deadline and not app.dialed:
        time.sleep(0.5)
    if not app.dialed:
        tally.note("HA's dial-back never reached this box (firewall?); step 4 skipped")
    else:
        time.sleep(2)
        tally.check("HA shows the app connected", bool(hub.panel_state(ws).get("proxy_client_connected")))
        before = hub.panel_state(ws)
        res, dt = ws.call({"type": f"{DOMAIN}/catalog/refresh", "entry_id": hub.entry_id, "kind": "devices"},
                          timeout=120)
        tally.check("catalog refresh fails at once", not res.get("success") and dt < 3.0,
                    f"{dt:.1f}s {res.get('error')}")
        after = hub.panel_state(ws)
        tally.check("cached catalog intact", after.get("device_count") == before.get("device_count"),
                    f"{before.get('device_count')} -> {after.get('device_count')}")
        app.release()
        hub.wait_connected(ws, 120)
        res, dt = ws.call({"type": f"{DOMAIN}/catalog/refresh", "entry_id": hub.entry_id, "kind": "devices"},
                          timeout=120)
        tally.check("refresh works again after the app left", bool(res.get("success")), f"{dt:.1f}s")
    app.close()

sys.exit(tally.finish())
