"""Facade adoption bench B2.1 / B2.6 / B4.6 (X1 ONLY, erases the hub): erasing
restore through HA, with side traffic during the rebuild.

DESTRUCTIVE: the X1 is erased and rebuilt from its own backup, taken first
and saved to out/ before anything is written. Never the X1S. Refuses to
run on anything but a hub that reports itself as X1, and without
--yes-erase-x1.

What runs during the rebuild (the device/activity phases):
- a second websocket polls control_panel/state and cache/structural_bundle
  every 2 s (the second browser tab on the Hub tab, B2.1);
- the stand-in app sends a CALL_ME every 10 s: each must be ignored (B2.6);
- the side traffic of --arm (B4.6), every 4 s:
    send  a key press (remote.send_command, command 1) to the device the
          rebuild finished last (its new id: the erased hub numbers
          devices from 1 in bundle order);
    play  an IR emit through the infrared emitter (send_pronto, a NEC
          frame to address 0xEE): the path the facade does not hold;
    both  alternating (cheaper, but a failure is then not attributable);
    none  no side traffic (a clean B2.1).
  B4.6 asks: does the X1 refuse the next record page after one of these
  (STATUS_ACK 0x04, the September defect)? A refused page fails the
  restore; the side traffic timeline is printed against the failure.

Afterwards: the phases in order (erase, device/activity, cache_warm, hub),
the result, ids contiguous, the hub name and the entry title, a fresh
backup compared with the first one (names, classes, counts), the wire
opcodes the restore sent (run-length, for the "no catalog reads between
page writes" check by eye).

Recovery after a failed rebuild: run again with --bundle <the saved
before-bundle> --arm none.

Usage:
    python bench_323_x1_erase_restore.py --yes-erase-x1 --arm none|send|play|both [--bundle out/<file>.json]
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time

from ha_bench import (DOMAIN, OUT, FakeApp, HaWs, Tally, bundle_summary, call_me_verdict, errors_in, hub_lines,
                      log_mark, log_since, nec_pronto, phases, resolve_hub, rest, run_backup, wait_for_ha,
                      watch_operation, wire_sends)

parser = argparse.ArgumentParser()
parser.add_argument("--yes-erase-x1", action="store_true")
parser.add_argument("--arm", choices=["none", "send", "play", "both"], required=True)
parser.add_argument("--bundle", default=None, help="restore this saved bundle instead of a fresh backup")
parser.add_argument("--no-verify-backup", action="store_true")
args = parser.parse_args()
if not args.yes_erase_x1:
    sys.exit("this erases the X1: pass --yes-erase-x1")

ws = wait_for_ha()
hub = resolve_hub(ws, "X1")
if hub.version.upper() != "X1":
    sys.exit(f"refusing: the resolved hub reports {hub.version!r}, not X1")
hub.wait_connected(ws)
tally = Tally(f"bench_323_{args.arm}")
stamp = time.strftime("%Y%m%d_%H%M%S")

# -- 1: the bundle --------------------------------------------------------------------------------

tally.section("1: backup (saved before anything is written)")
if args.bundle:
    bundle = json.loads(open(args.bundle, encoding="utf-8").read())
    print(f"  restoring the saved bundle {args.bundle}", flush=True)
else:
    meta, bundle = run_backup(ws, hub)
    if not tally.check("backup", meta.get("status") == "success" and bool(bundle), str(meta.get("message"))):
        sys.exit(tally.finish())
    OUT.mkdir(exist_ok=True)
    saved = OUT / f"bench_323_before_{stamp}.json"
    saved.write_text(json.dumps(bundle, indent=2), encoding="utf-8")
    print(f"  saved {saved.name}", flush=True)
before = bundle_summary(bundle)
print(f"  {len(before['devices'])} devices, {len(before['activities'])} activities, hub {before['hub']!r}", flush=True)
if not bundle.get("activities"):
    sys.exit("the bundle has no activities: HA would merge instead of erase")
new_id_of = {int((row.get("device") or {}).get("device_id") or 0): i + 1
             for i, row in enumerate(r for r in bundle.get("devices") or []
                                     if int((r.get("device") or {}).get("device_id") or 0))}

# -- 2: side traffic ------------------------------------------------------------------------------

rebuilding = threading.Event()
finished = threading.Event()
last_device: list[int] = []
side: list[dict] = []
poll_stats = {"ok": 0, "failed": 0, "errors": []}
call_mes: list[tuple[float, str]] = []
t_start = time.time()
app = FakeApp(hub)
pronto = nec_pronto(0xEE, 0x11)


def fire(kind: str) -> None:
    t = round(time.time() - t_start, 1)
    if kind == "send":
        if not last_device:
            return
        status, body, dt = rest("/api/services/remote/send_command",
                                {"entity_id": hub.remote, "command": ["1"], "device": last_device[-1],
                                 "delay_secs": 0}, timeout=30)
        side.append({"t": t, "kind": "send", "device": last_device[-1], "status": status, "dt": round(dt, 2)})
    else:
        if hub.emitter is None:
            return
        status, body, dt = rest(f"/api/services/{DOMAIN}/send_pronto",
                                {"entity_id": hub.emitter, "pronto": pronto}, timeout=30)
        side.append({"t": t, "kind": "play", "status": status, "dt": round(dt, 2)})


def side_loop() -> None:
    kinds = {"send": ["send"], "play": ["play"], "both": ["send", "play"], "none": []}[args.arm]
    n = 0
    next_call_me = 0.0
    while not finished.is_set():
        if rebuilding.is_set():
            if kinds:
                fire(kinds[n % len(kinds)])
                n += 1
            if time.time() >= next_call_me:
                mark = log_mark()
                app.call_me()
                time.sleep(2)
                call_mes.append((round(time.time() - t_start, 1), call_me_verdict(log_since(mark), app.my_ip)))
                next_call_me = time.time() + 10
            time.sleep(2)
        else:
            time.sleep(0.5)


def poll_loop() -> None:
    tab = HaWs()
    try:
        while not finished.is_set():
            for payload in ({"type": f"{DOMAIN}/control_panel/state"},
                            {"type": f"{DOMAIN}/cache/structural_bundle", "entry_id": hub.entry_id}):
                res, _ = tab.call(payload, timeout=60)
                if res.get("success"):
                    poll_stats["ok"] += 1
                else:
                    poll_stats["failed"] += 1
                    poll_stats["errors"].append(str(res.get("error"))[:120])
            time.sleep(2)
    finally:
        tab.close()


def on_event(event: dict) -> None:
    phase = str(event.get("phase") or "")
    if phase in ("device", "activity"):
        rebuilding.set()
    elif phase in ("cache_warm", "hub", "completed", "failed"):
        rebuilding.clear()
    # Progress names the bundle's (source) id; the erased hub hands out ids
    # from 1 in bundle order, so a restored device's new id is its position.
    device = event.get("current_device_id")
    if phase == "device" and isinstance(device, int) and str(event.get("message") or "").startswith("Restored"):
        last_device.append(new_id_of.get(device, device))


# -- 3: the erasing restore -----------------------------------------------------------------------

tally.section(f"2: erasing restore, side traffic: {args.arm}")
mark = log_mark()
threading.Thread(target=side_loop, daemon=True).start()
threading.Thread(target=poll_loop, daemon=True).start()
op_id = ws.ok({"type": f"{DOMAIN}/backup/restore", "entry_id": hub.entry_id, "backup": bundle,
               "mode": "replace"})["operation_id"]
events = watch_operation(ws, op_id, during=on_event, timeout=3600)
finished.set()
rebuilding.clear()
time.sleep(3)
lines = log_since(mark)
final = events[-1] if events else {}
result = final.get("result") or {}
seen = phases(events)
print(f"  phases: {' > '.join(seen)}", flush=True)
tally.check("restore succeeded", final.get("status") == "success",
            f"{final.get('message')} failed_at={result.get('failed_at')!r}")


def order_ok(seq: list[str]) -> bool:
    want = ["erase", "cache_warm", "hub"]
    idx = [seq.index(p) for p in want if p in seq]
    rebuilt = [i for i, p in enumerate(seq) if p in ("device", "activity")]
    return len(idx) == 3 and idx == sorted(idx) and bool(rebuilt) and idx[0] < min(rebuilt) and max(rebuilt) < idx[1]


tally.check("B2.1: erase > device/activity > cache_warm > hub", order_ok(seen), " > ".join(seen))
tally.check("B2.1: the second tab's polls answered", poll_stats["failed"] == 0,
            f"ok={poll_stats['ok']} failed={poll_stats['failed']} {poll_stats['errors'][:3]}")
verdicts = [v for _, v in call_mes]
tally.check("B2.6: every CALL_ME during the rebuild ignored", bool(verdicts) and set(verdicts) <= {"gated"},
            f"{call_mes}")

if args.arm != "none":
    print("  side traffic (t from restore start):", flush=True)
    for row in side:
        print(f"    {row}", flush=True)
    tally.check(f"B4.6: side traffic fired ({args.arm})", bool(side), f"{len(side)} calls")
# A refused page: "[ACK] STATUS_ACK (0x0103) status=0x04" or "[STEP] ... hub rejected status=0x04".
refused = [line for line in hub_lines(lines, hub.entry_id) if "status=0x04" in line]
tally.check("B4.6: no STATUS_ACK 0x04 during the restore", not refused, f"{len(refused)} lines")
for line in refused[:10]:
    print(f"    {line[:220]}", flush=True)

sends = wire_sends(lines, hub.entry_id)
rle: list[list] = []
for name in sends:
    if rle and rle[-1][0] == name:
        rle[-1][1] += 1
    else:
        rle.append([name, 1])
print("  wire sends (run-length): " + ", ".join(f"{n}x{c}" for n, c in rle[:80]), flush=True)
if not sends:
    tally.note("no [SEND] hub lines: the integration logs at info; set debug to see the wire")

# -- 4: after -------------------------------------------------------------------------------------

tally.section("3: the rebuilt hub")
hub.wait_connected(ws, 300)
state = hub.panel_state(ws)
tally.check("hub name restored", state.get("name") == before["hub"], f"{state.get('name')!r} vs {before['hub']!r}")
entries = ws.ok({"type": "config_entries/get", "domain": DOMAIN})
title = next((e.get("title") for e in entries if e.get("entry_id") == hub.entry_id), None)
tally.check("entry title follows the hub name", title == before["hub"], f"{title!r}")
tally.check("hub name step reported", bool(result.get("hub_name_restored", "hub" in seen)), str(result.get("hub_name_restored")))
if not args.no_verify_backup:
    meta, after_bundle = run_backup(ws, hub)
    if tally.check("verification backup", meta.get("status") == "success", str(meta.get("message"))):
        after = bundle_summary(after_bundle)
        tally.check("devices match (names, classes, counts)", after["devices"] == before["devices"],
                    json.dumps({k: (before["devices"].get(k), after["devices"].get(k))
                                for k in set(before["devices"]) | set(after["devices"])
                                if before["devices"].get(k) != after["devices"].get(k)})[:600])
        tally.check("activities match (counts)", after["activities"] == before["activities"],
                    json.dumps({k: (before["activities"].get(k), after["activities"].get(k))
                                for k in set(before["activities"]) | set(after["activities"])
                                if before["activities"].get(k) != after["activities"].get(k)})[:600])
        dev_ids = sorted(int((r.get("device") or {}).get("device_id") or 0) for r in after_bundle.get("devices") or [])
        tally.check("device ids contiguous from 1", dev_ids == list(range(1, len(dev_ids) + 1)), str(dev_ids))
        OUT.joinpath(f"bench_323_after_{stamp}.json").write_text(json.dumps(after_bundle, indent=2), encoding="utf-8")
errors = errors_in(lines)
tally.check("no integration or library ERROR lines during the restore", not errors, f"{len(errors)}")
for line in errors[:5]:
    print(f"    {line[:220]}", flush=True)
app.close()
sys.exit(tally.finish())
