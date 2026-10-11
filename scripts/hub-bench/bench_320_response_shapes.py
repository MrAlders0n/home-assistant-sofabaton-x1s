"""Facade adoption bench B2.3 / B4.4 / B7.1: service response shapes, 0.7.0 vs the branch.

Run ``capture`` once with 0.7.0 deployed and once with the branch, then
``diff`` the two. Each capture calls the same services the same way and
keeps each answer's type skeleton (ha_bench.shape) plus its HTTP status
and, for a refused call, the reason HA logged.

Calls (X1 by default; every write is undone by the capture itself):
- backup_bundle, one device, then the whole hub;
- get_favorites for the activity;
- export_snapshot;
- sync_from_snapshot: a rename of the activity (success), the rename
  back, a stale generation (refused), a stale baseline (refused);
- with --with-restore (X1 only): restore_backup of a device entry of the
  wrong kind (the preflight refuses it before any write) and a merge
  restore of the device-only bundle (a copy of the device), the copy
  deleted after. (A binding to a missing command is no probe: 0.7.0
  restores the device and drops the row with a warning.)

Usage:
    python bench_320_response_shapes.py capture <tag> [--hub X1] [--activity 102] [--device 7] [--with-restore]
    python bench_320_response_shapes.py diff <tagA> <tagB> [--hub X1]
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
import time
from pathlib import Path

from ha_bench import (OUT, Tally, bundle_activity, errors_in, log_mark, log_since, resolve_hub, service, shape,
                      shape_diff, wait_for_ha)

parser = argparse.ArgumentParser()
parser.add_argument("mode", choices=["capture", "diff"])
parser.add_argument("tags", nargs="+")
parser.add_argument("--hub", default="X1")
parser.add_argument("--activity", type=int, default=102)
parser.add_argument("--device", type=int, default=7, help="an IR device in no activity (its copy is restored, then deleted)")
parser.add_argument("--with-restore", action="store_true")
args = parser.parse_args()


def capture_path(tag: str) -> Path:
    return OUT / f"bench_320_{tag}_{args.hub.lower()}.json"


if args.mode == "diff":
    if len(args.tags) != 2:
        sys.exit("diff takes two tags")
    first, second = (json.loads(capture_path(t).read_text(encoding="utf-8")) for t in args.tags)
    tally = Tally("bench_320_diff")
    for call in sorted(set(first) | set(second)):
        a, b = first.get(call), second.get(call)
        if a is None or b is None:
            tally.check(f"{call}: captured by both", False, "missing in " + (args.tags[0] if a is None else args.tags[1]))
            continue
        diffs = shape_diff(a["shape"], b["shape"])
        same_status = a["status"] == b["status"]
        tally.check(f"{call}: same status", same_status, f"{a['status']} vs {b['status']}")
        tally.check(f"{call}: same shape", not diffs, "; ".join(diffs[:8]) + (" ..." if len(diffs) > 8 else ""))
        if a.get("reason") or b.get("reason"):
            print(f"       reasons: {a.get('reason')!r}\n            vs: {b.get('reason')!r}", flush=True)
    sys.exit(tally.finish())

# -- capture --------------------------------------------------------------------------------------

if len(args.tags) != 1:
    sys.exit("capture takes one tag")
if args.with_restore and args.hub.upper() != "X1":
    sys.exit("--with-restore writes devices: X1 only")

ws = wait_for_ha()
hub = resolve_hub(ws, args.hub)
hub.wait_connected(ws)
tally = Tally(f"bench_320_capture_{args.tags[0]}")
mark0 = log_mark()
captured: dict[str, dict] = {}


def record(call: str, status: int, body, mark: int | None = None) -> None:
    reason = None
    if status != 200 and mark is not None:
        time.sleep(1)
        lines = log_since(mark)
        # The exception's own line ("homeassistant.exceptions.HomeAssistantError:
        # ..."), not aiohttp's "Error handling request" header above it.
        reason = next((line.strip()[:300] for line in lines
                       if re.match(r"^[\w.]*(Error|Exception): ", line.strip())), None) or next(
            (line.split(" ERROR ", 1)[-1][:300] for line in lines if " ERROR " in line), None)
    captured[call] = {"status": status, "shape": shape(body), "reason": reason}
    # Saved after every call: a crash later in the run keeps what ran.
    OUT.mkdir(exist_ok=True)
    capture_path(args.tags[0]).write_text(json.dumps(captured, indent=2), encoding="utf-8")
    print(f"  {call}: HTTP {status}" + (f" ({reason})" if reason else ""), flush=True)


def call(name: str, data: dict, *, response: bool = True, label: str | None = None) -> tuple[int, object]:
    mark = log_mark()
    status, body = service(name, {"device": hub.device, **data}, response=response)
    record(label or name, status, body, mark)
    return status, body


tally.section("backup_bundle")
status, device_bundle = call("backup_bundle", {"device_ids": [args.device]}, label="backup_bundle[device]")
tally.check("device backup answered", status == 200 and isinstance(device_bundle, dict), str(status))
status, _ = call("backup_bundle", {}, label="backup_bundle[hub]")
tally.check("whole-hub backup answered", status == 200, str(status))

tally.section("get_favorites")
status, _ = call("get_favorites", {"activity_id": args.activity})
tally.check("get_favorites answered", status == 200, str(status))

tally.section("export_snapshot / sync_from_snapshot")
status, snap = call("export_snapshot", {})
if status != 200 or not isinstance(snap, dict) or not snap.get("bundle"):
    tally.check("export_snapshot answered (persistent cache on, Hub tab opened once)", False, str(status))
    sys.exit(tally.finish())
baseline, generation = snap["bundle"], snap.get("generation")
activity = bundle_activity(baseline, args.activity)
if activity is None:
    sys.exit(f"activity {args.activity} is not in the snapshot")
original_name = str((activity.get("device") or {}).get("name") or "")
edited = copy.deepcopy(baseline)
bundle_activity(edited, args.activity)["device"]["name"] = (original_name[:10] + " B320").strip()
sync = {"entity_kind": "activity", "entity_id": args.activity}

status, _ = call("sync_from_snapshot", {**sync, "baseline": baseline, "edited": edited},
                 label="sync_from_snapshot[success]")
tally.check("rename synced", status == 200, str(status))
status, snap2 = service("export_snapshot", {"device": hub.device}, response=True)
back_baseline = snap2["bundle"] if status == 200 and isinstance(snap2, dict) else edited
back_edited = copy.deepcopy(back_baseline)
bundle_activity(back_edited, args.activity)["device"]["name"] = original_name
status, _ = service("sync_from_snapshot", {"device": hub.device, **sync, "baseline": back_baseline,
                                           "edited": back_edited}, response=True)
tally.check("rename undone", status == 200, str(status))

status, snap3 = service("export_snapshot", {"device": hub.device}, response=True)
current = snap3["bundle"] if status == 200 and isinstance(snap3, dict) else back_edited
stale_gen = int((snap3 or {}).get("generation") or 1) - 1
probe = copy.deepcopy(current)
bundle_activity(probe, args.activity)["device"]["name"] = original_name[:10] + " B320s"
status, _ = call("sync_from_snapshot", {**sync, "baseline": current, "edited": probe,
                                         "expected_generation": stale_gen},
                 label="sync_from_snapshot[stale_generation]")
tally.check("a stale generation is refused", status != 200, str(status))
stale_base = copy.deepcopy(current)
bundle_activity(stale_base, args.activity)["device"]["name"] = original_name[:10] + " B320x"
status, _ = call("sync_from_snapshot", {**sync, "baseline": stale_base, "edited": probe},
                 label="sync_from_snapshot[stale_baseline]")
tally.check("a stale baseline is refused", status != 200, str(status))

# A probe the hub took anyway (a check above failed) leaves the activity
# renamed: put the name back.
status, snap4 = service("export_snapshot", {"device": hub.device}, response=True)
if status == 200 and isinstance(snap4, dict):
    now = bundle_activity(snap4["bundle"], args.activity) or {}
    if str((now.get("device") or {}).get("name") or "") != original_name:
        fixed = copy.deepcopy(snap4["bundle"])
        bundle_activity(fixed, args.activity)["device"]["name"] = original_name
        status, _ = service("sync_from_snapshot", {"device": hub.device, **sync, "baseline": snap4["bundle"],
                                                   "edited": fixed}, response=True)
        tally.check("activity name put back after a probe went through", status == 200, str(status))

if args.with_restore:
    tally.section("restore_backup (X1)")
    if not isinstance(device_bundle, dict) or device_bundle.get("activities"):
        sys.exit("the device-only bundle carries activities: a restore of it would erase the hub")

    def restore_and_clean(label: str, bundle: dict) -> int:
        status, result = call("restore_backup", {"backup": bundle}, label=label)
        new_ids = [int(r.get("device_id") or 0) for r in (result or {}).get("restored_devices") or []
                   if isinstance(r, dict)] if isinstance(result, dict) else []
        for new_id in new_ids:
            if new_id and new_id != args.device:
                res, _ = ws.call({"type": "sofabaton_x1s/device/delete", "entry_id": hub.entry_id,
                                  "device_id": new_id})
                tally.check(f"{label}: restored copy {new_id} deleted", bool(res.get("success")),
                            str(res.get("error")))
        if status == 200 and not new_ids:
            tally.note(f"{label}: the result named no restored device; delete any copy by hand (Hub tab)")
        return status

    # A device entry of the wrong kind: the restore preflight refuses it
    # before any write, in merge mode (no activities, so never an erase).
    bad = copy.deepcopy(device_bundle)
    for row in bad.get("devices") or []:
        row["kind"] = "not_a_device_backup"
    tally.check("a bad device entry is refused", restore_and_clean("restore_backup[bad_device_kind]", bad) != 200)
    tally.check("merge restore of the device answered",
                restore_and_clean("restore_backup[merge]", device_bundle) == 200)

OUT.mkdir(exist_ok=True)
capture_path(args.tags[0]).write_text(json.dumps(captured, indent=2), encoding="utf-8")
print(f"\ncaptured {len(captured)} calls -> {capture_path(args.tags[0]).name}", flush=True)
mark_errors = errors_in(log_since(mark0))
if mark_errors:
    tally.note(f"{len(mark_errors)} integration ERROR lines during the capture (the refusals above log some)")
sys.exit(tally.finish())
