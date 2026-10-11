"""Facade adoption bench B3.2: the persistent cache across 0.7.0 and the branch.

The store (.storage/sofabaton_x1s.persistent_cache on the Z: share) holds
one payload per entry: 0.7.0 writes the engine's bare export, the branch
writes the facade's state document (kind sofabaton_state, schema 1) and
reads either.

    inspect                 what the store holds per entry
    restart --expect warm   restart HA; every hub loads, the cache comes up
                            warm (the debug line "Restored persistent cache:
                            devices=N", N > 0), no integration errors; then
                            one cache refresh makes the entry persist again
                            and the store is inspected
    restart --expect cold   the same, but a cold cache is expected and must
                            be quiet (0.7.0 reading a document: devices=0,
                            no error, the entry loaded)

The sequence (runbook session D): deploy 0.7.0, inspect (legacy), deploy
the branch, restart --expect warm (warm from legacy, store now a document),
restart --expect warm (warm from the document), deploy 0.7.0, restart
--expect cold, deploy the branch, restart --expect warm.

Needs the integration logging at debug (the warm line is a debug line).

Usage: python bench_324_persistent_cache.py inspect | restart --expect warm|cold [--no-persist]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time

from ha_bench import CACHE_STORE, DOMAIN, Tally, errors_in, grep, log_mark, log_since, rest, wait_for_ha

parser = argparse.ArgumentParser()
parser.add_argument("mode", choices=["inspect", "restart"])
parser.add_argument("--expect", choices=["warm", "cold"], default="warm")
parser.add_argument("--no-persist", dest="persist", action="store_false",
                    help="skip the per-entity refresh that makes each entry persist again")
args = parser.parse_args()


def inspect() -> dict[str, str]:
    raw = json.loads(CACHE_STORE.read_text(encoding="utf-8"))
    data = raw.get("data") or {}
    print(f"store version {raw.get('version')}.{raw.get('minor_version')}, enabled={data.get('enabled')}", flush=True)
    kinds: dict[str, str] = {}
    for entry_id, payload in (data.get("hubs") or {}).items():
        if isinstance(payload, dict) and payload.get("kind") == "sofabaton_state":
            kinds[entry_id] = f"document schema {payload.get('schema')}"
        elif isinstance(payload, dict):
            kinds[entry_id] = "legacy payload (" + ", ".join(sorted(payload)[:6]) + ")"
        else:
            kinds[entry_id] = f"unreadable ({type(payload).__name__})"
        print(f"  {entry_id[:8]}: {kinds[entry_id]}", flush=True)
    return kinds


if args.mode == "inspect":
    inspect()
    sys.exit(0)

tally = Tally(f"bench_324_restart_{args.expect}")
tally.section("before")
before = inspect()
mark = log_mark()
status, _, _ = rest("/api/services/homeassistant/restart", {}, timeout=30)
print(f"restart requested (HTTP {status}); waiting for HA", flush=True)
time.sleep(30)
ws = wait_for_ha()
deadline = time.time() + 300
hubs: list[dict] = []
while time.time() < deadline:
    hubs = ws.ok({"type": f"{DOMAIN}/control_panel/state"}).get("hubs") or []
    if hubs and all(h.get("hub_connected") for h in hubs):
        break
    time.sleep(5)
time.sleep(5)
lines = log_since(mark)

tally.section("after the restart")
entries = {e["entry_id"]: e for e in ws.ok({"type": "config_entries/get", "domain": DOMAIN})}
for entry_id in before:
    entry = entries.get(entry_id) or {}
    if entry.get("disabled_by"):
        tally.note(f"{entry_id[:8]} is disabled; skipped")
        continue
    tally.check(f"{entry_id[:8]} loaded", entry.get("state") == "loaded", str(entry.get("state")))
    restored = grep(lines, entry_id, "Restored persistent cache")
    devices = [int(m.group(1)) for line in restored if (m := re.search(r"devices=(\d+)", line))]
    not_loaded = grep(lines, entry_id, "Persisted cache not loaded")
    if args.expect == "warm":
        tally.check(f"{entry_id[:8]} warm", bool(devices) and devices[-1] > 0 and not not_loaded,
                    f"devices={devices} not_loaded={len(not_loaded)}")
    else:
        tally.check(f"{entry_id[:8]} cold", not devices or devices[-1] == 0, f"devices={devices}")
        if not_loaded:
            print(f"    {not_loaded[0][:200]}", flush=True)
errors = errors_in(lines)
tally.check("no integration errors during setup", not errors, f"{len(errors)}")
for line in errors[:5]:
    print(f"    {line[:220]}", flush=True)

if args.persist:
    tally.section("a per-entity refresh persists each entry")
    for hub in hubs:
        bundle = ws.ok({"type": f"{DOMAIN}/cache/structural_bundle", "entry_id": hub["entry_id"]}).get("bundle") or {}
        first = next((int((r.get("device") or {}).get("device_id") or 0) for r in bundle.get("activities") or []), 0)
        if not first:
            tally.note(f"{hub['entry_id'][:8]}: no cached activity to refresh")
            continue
        res, _ = ws.call({"type": f"{DOMAIN}/persistent_cache/refresh", "entry_id": hub["entry_id"],
                          "kind": "activity", "target_id": first}, timeout=300)
        tally.check(f"{hub['entry_id'][:8]} activity {first} refreshed", bool(res.get("success")),
                    str(res.get("error") or ""))
    time.sleep(3)
    after = inspect()
    if args.expect == "warm":
        for entry_id, kind in after.items():
            if entry_id in entries and not entries[entry_id].get("disabled_by"):
                tally.check(f"{entry_id[:8]} stored as a document", kind.startswith("document"), kind)
sys.exit(tally.finish())
