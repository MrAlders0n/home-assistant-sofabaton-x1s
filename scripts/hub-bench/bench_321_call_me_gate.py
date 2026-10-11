"""Facade adoption bench B0.1 / B2.2 / B2.6: the vendor app's CALL_ME is kept out
while HA works the hub, and let in right after.

A stand-in app on this box sends the app's UDP CALL_ME to HA's proxy
(ha_bench.FakeApp) every few seconds while an operation runs. HA's log
says per CALL_ME whether it was taken (``APP CALL_ME from <ip> -> app
tcp``) or gated (``... ignored (hub busy``). One taken while the
operation still runs is a failure. A dial-back that reaches this box is
closed at once (the Windows firewall may drop it; the log verdict does
not depend on it).

Operations (each checked during and right after):
1. a whole-hub backup (registry operation, facade hold);
2. a whole-hub cache refresh (registry operation);
3. a Wifi Commands sync (a bench-only record "Bench B321": one slot, no
   activities; the cleanup sync deletes the device, then the record);
4. with --restore: a merge restore of one device's backup (B2.2: no
   erase, no hub-name step, one new device, the others untouched); the
   copy is deleted after.

Needs: the hub's proxy on (Settings), the integration logging at info or
debug, HA's UDP 8102 reachable from this box.

Usage: python bench_321_call_me_gate.py [--hub X1] [--restore --device 12] [--skip-wifi]
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import Callable

from ha_bench import (DOMAIN, FakeApp, Tally, bundle_summary, call_me_verdict, log_mark, log_since, phases,
                      resolve_hub, rest, run_backup, wait_for_ha, watch_operation)

parser = argparse.ArgumentParser()
parser.add_argument("--hub", default="X1")
parser.add_argument("--restore", action="store_true")
parser.add_argument("--device", type=int, default=12)
parser.add_argument("--skip-wifi", action="store_true")
parser.add_argument("--port", type=int, default=8193)
args = parser.parse_args()

ws = wait_for_ha()
hub = resolve_hub(ws, args.hub)
state = hub.wait_connected(ws)
if not (state.get("settings") or {}).get("proxy_enabled"):
    sys.exit(f"{hub.name}: the proxy is off; turn it on in the hub's settings first")
tally = Tally(f"bench_321_{hub.version.lower()}")
app = FakeApp(hub, args.port)
print(f"stand-in app {app.my_ip}:{app.port} -> HA {app.ha_host}:8102, hint {hub.mac_bytes.hex(':')}", flush=True)


def one_call_me(wait: float = 2.0) -> str:
    mark = log_mark()
    app.call_me()
    time.sleep(wait)
    return call_me_verdict(log_since(mark), app.my_ip)


def busy() -> bool:
    return hub.panel_state(ws).get("active_backup_operation") is not None


def gate_during(label: str, start: Callable[[], Callable[[], bool]], *, every: float = 3.0,
                limit: int = 40) -> None:
    """``start`` kicks the operation off and returns its done() check."""

    done = start()
    time.sleep(1.0)
    verdicts: list[tuple[str, bool]] = []
    while not done() and len(verdicts) < limit:
        verdict = one_call_me()
        verdicts.append((verdict, not done()))
        time.sleep(max(0.0, every - 2.0))
    while not done():
        time.sleep(2)
    during = [v for v, running in verdicts if running]
    tally.check(f"{label}: CALL_MEs sent while it ran", bool(during), f"{len(during)} sent")
    tally.check(f"{label}: none taken while it ran", "taken" not in during,
                f"gated={during.count('gated')} taken={during.count('taken')} unseen={during.count('unseen')}")
    if "unseen" in during:
        tally.note(f"{label}: {during.count('unseen')} CALL_MEs left no log line (logging level? hint?)")
    time.sleep(3)
    verdict = one_call_me()
    tally.check(f"{label}: taken right after", verdict == "taken", verdict)
    time.sleep(2)
    hub.wait_connected(ws, 120)


# -- 1: backup ------------------------------------------------------------------------------------

tally.section("1: whole-hub backup")


def start_backup() -> Callable[[], bool]:
    op_id = ws.ok({"type": f"{DOMAIN}/backup/export", "entry_id": hub.entry_id})["operation_id"]
    print(f"  backup {op_id}", flush=True)
    return lambda: not busy()


gate_during("backup", start_backup)

# -- 2: cache refresh -----------------------------------------------------------------------------

tally.section("2: whole-hub cache refresh")


def start_refresh() -> Callable[[], bool]:
    op_id = ws.ok({"type": f"{DOMAIN}/cache/refresh_all", "entry_id": hub.entry_id})["operation_id"]
    print(f"  refresh {op_id}", flush=True)
    return lambda: not busy()


gate_during("cache refresh", start_refresh)

# -- 3: Wifi Commands sync ------------------------------------------------------------------------

if not args.skip_wifi:
    tally.section("3: Wifi Commands sync (bench record)")
    sensor = hub.wifi_sensor
    if sensor is None:
        tally.note("no Wifi Commands sensor on this hub; skipped")
    else:
        bench_name = "Bench B321"

        def slot(n: int, name: str | None = None) -> dict:
            return {"name": name or f"Command {n}", "add_as_favorite": False, "hard_button": "",
                    "long_press_enabled": False, "input_activity_id": "", "activities": []}

        def record() -> dict | None:
            for dev in ws.ok({"type": f"{DOMAIN}/command_devices/list", "entity_id": sensor}).get("devices") or []:
                if str(dev.get("device_name") or "") == bench_name:
                    return dev
            return None

        def progress(key: str) -> dict:
            return ws.ok({"type": f"{DOMAIN}/command_sync/progress", "entity_id": sensor, "device_key": key})

        key = str((record() or {}).get("device_key") or "") or str(
            ws.ok({"type": f"{DOMAIN}/command_device/create", "entity_id": sensor,
                   "device_name": bench_name}).get("device_key") or "")

        def start_sync(slots: list[dict]) -> Callable[[], Callable[[], bool]]:
            def _start() -> Callable[[], bool]:
                ws.ok({"type": f"{DOMAIN}/command_config/set", "entity_id": sensor, "commands": slots,
                       "device_key": key})
                rest(f"/api/services/{DOMAIN}/sync_command_config", {"device": hub.device, "device_key": key},
                     timeout=5)
                deadline = time.time() + 20
                while time.time() < deadline and progress(key).get("status") not in ("running", "pending"):
                    time.sleep(0.5)
                return lambda: progress(key).get("status") in ("success", "failed", "idle", None)
            return _start

        slots = [slot(n) for n in range(1, 11)]
        slots[0]["name"] = "B321 One"
        gate_during("Wifi Commands sync", start_sync(slots), every=2.5)
        prog = progress(key)
        tally.check("Wifi Commands sync succeeded", prog.get("status") == "success", str(prog.get("message")))
        print("  cleanup: zero-slot sync + delete the bench record", flush=True)
        start_sync([slot(n) for n in range(1, 11)])()
        deadline = time.time() + 600
        while time.time() < deadline and progress(key).get("status") in ("running", "pending"):
            time.sleep(2)
        tally.check("cleanup sync removed the device", progress(key).get("status") == "success",
                    str(progress(key).get("message")))
        try:
            ws.ok({"type": f"{DOMAIN}/command_device/delete", "entity_id": sensor, "device_key": key})
        except RuntimeError as err:
            tally.note(f"bench record delete: {err}")

# -- 4: merge restore of one device ---------------------------------------------------------------

if args.restore:
    tally.section(f"4: merge restore of device {args.device} (B2.2 + B2.6)")
    meta, bundle = run_backup(ws, hub, [args.device])
    if meta.get("status") != "success" or not bundle:
        tally.check("device backup", False, str(meta.get("message")))
    elif bundle.get("activities"):
        sys.exit("the device-only bundle carries activities: its restore would erase the hub")
    else:
        before = hub.panel_state(ws)
        holder: dict = {}

        def start_restore() -> Callable[[], bool]:
            holder["op"] = ws.ok({"type": f"{DOMAIN}/backup/restore", "entry_id": hub.entry_id,
                                  "backup": bundle, "mode": "merge"})["operation_id"]
            return lambda: not busy()

        gate_during("merge restore", start_restore)
        events = watch_operation(ws, holder["op"], timeout=60)
        seen = phases(events)
        final = events[-1] if events else {}
        tally.check("merge restore succeeded", final.get("status") == "success", str(final.get("message")))
        tally.check("B2.2: no erase, no hub-name step", "erase" not in seen and "hub" not in seen, " > ".join(seen))
        after = hub.panel_state(ws)
        tally.check("B2.2: exactly one device more", after.get("device_count") == (before.get("device_count") or 0) + 1,
                    f"{before.get('device_count')} -> {after.get('device_count')}")
        result = final.get("result") or {}
        new_ids = [int(r.get("device_id") or 0) for r in result.get("restored_devices") or [] if isinstance(r, dict)]
        tally.check("B2.2: the copy got a new id", bool(new_ids) and args.device not in new_ids, str(new_ids))
        print(f"  summary of the copy: {bundle_summary(bundle)['devices']}", flush=True)
        for new_id in new_ids:
            if new_id and new_id != args.device:
                res, _ = ws.call({"type": f"{DOMAIN}/device/delete", "entry_id": hub.entry_id, "device_id": new_id})
                tally.check(f"copy {new_id} deleted", bool(res.get("success")), str(res.get("error")))
        tally.check("device count back", hub.panel_state(ws).get("device_count") == before.get("device_count"),
                    str(hub.panel_state(ws).get("device_count")))

app.close()
sys.exit(tally.finish())
