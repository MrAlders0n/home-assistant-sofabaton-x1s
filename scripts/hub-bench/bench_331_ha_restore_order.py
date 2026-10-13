"""Issue #307 on the HA path (X1 ONLY, erases the hub): an erasing restore
through Home Assistant keeps the backup's display order and syncs the
remotes once, after everything.

DESTRUCTIVE: the X1 is erased and rebuilt from its own backup, saved to
out/ before anything is written. Never the X1S. Refuses to run on anything
but a hub that reports itself as X1, and without --yes-erase-x1.

Steps:
1. read the hub's display order (the persistent-cache view, or a backup
   when the cache store is off) and reverse both lists through HA's own
   reorder commands: an erased hub numbers what a restore creates in id
   order, so a hub already in id order would keep it by accident;
2. back up (saved), check the backup holds the reversed order;
3. erasing restore through HA (backup/restore, mode replace);
4. back up again and compare: names, classes and counts, and the display
   order of devices and activities (bench_compare.display_order_problems);
5. put the hub's own order back.

Checks on the restore: it succeeded; the engine reports the order
written for both lists; the progress ends hub > cache_warm > remote_sync
(the remotes are told last); in HA's debug log for this hub, the write
batch opened before the erase, exactly one REMOTE_SYNC went out, and
after SET_HUB_NAME. Wire checks need the integration and x1proxy at debug
(configuration.yaml); without the lines they are reported, not failed.

Recovery after a failed rebuild: restore the saved before-bundle from the
Backup tab.

Usage (system python, websocket-client):
    python bench_331_ha_restore_order.py --yes-erase-x1 [--no-scramble]
"""

from __future__ import annotations

import argparse
import json
import sys
import time

from bench_compare import display_order, display_order_problems
from ha_bench import (DOMAIN, OUT, Tally, bundle_summary, errors_in, hub_lines, log_mark, log_since, phases,
                      resolve_hub, run_backup, wait_for_ha, watch_operation, wire_sends)

parser = argparse.ArgumentParser()
parser.add_argument("--yes-erase-x1", action="store_true")
parser.add_argument("--no-scramble", action="store_true")
args = parser.parse_args()
if not args.yes_erase_x1:
    sys.exit("this erases the X1: pass --yes-erase-x1")

ws = wait_for_ha()
hub = resolve_hub(ws, "X1")
if hub.version.upper() != "X1":
    sys.exit(f"refusing: the resolved hub reports {hub.version!r}, not X1")
hub.wait_connected(ws)
tally = Tally("bench_331")
stamp = time.strftime("%Y%m%d_%H%M%S")
OUT.mkdir(exist_ok=True)


def ordered_entities() -> dict[str, list[tuple[int, str]]]:
    """``(id, name)`` per kind in display order, from the cache view (sort
    byte first, unsorted rows after them by id)."""

    contents = ws.ok({"type": f"{DOMAIN}/persistent_cache/contents"})
    mine = next((h for h in contents.get("hubs") or [] if h.get("entry_id") == hub.entry_id), None)
    if not contents.get("enabled") or mine is None:
        _meta, bundle = run_backup(ws, hub)
        return {kind: [(_id_by_name(bundle, kind)[name], name) for name in display_order(bundle, kind)]
                for kind in ("devices", "activities")}

    def _ordered(rows: list[dict]) -> list[tuple[int, str]]:
        def key(row: dict) -> tuple[int, int, int]:
            sort, entity_id = int(row.get("sort") or 0), int(row.get("id") or 0)
            return (0, sort, entity_id) if sort else (1, 0, entity_id)
        return [(int(r["id"]), str(r.get("name"))) for r in sorted(rows, key=key)]

    return {"devices": _ordered(mine.get("devices_list") or []), "activities": _ordered(mine.get("activities") or [])}


def _id_by_name(bundle: dict, kind: str) -> dict[str, int]:
    return {str((row.get("device") or {}).get("name")): int((row.get("device") or {}).get("device_id") or 0)
            for row in bundle.get(kind) or []}


def reorder(kind: str, ids: list[int]) -> None:
    command = "device/reorder" if kind == "devices" else "activity/reorder"
    ws.ok({"type": f"{DOMAIN}/{command}", "entry_id": hub.entry_id, "ordered_ids": ids}, timeout=120)


# -- 1: scramble ----------------------------------------------------------------------------------

hub_order: dict[str, list[str]] = {}
if not args.no_scramble:
    tally.section("1: reverse the display order")
    current = ordered_entities()
    hub_order = {kind: [name for _id, name in rows] for kind, rows in current.items()}
    print(f"  hub order: {json.dumps(hub_order)}", flush=True)
    for kind, rows in current.items():
        if len(rows) > 1:
            reorder(kind, [entity_id for entity_id, _name in reversed(rows)])

# -- 2: the backup --------------------------------------------------------------------------------

tally.section("2: backup (saved before anything is written)")
meta, bundle = run_backup(ws, hub)
if not tally.check("backup", meta.get("status") == "success" and bool(bundle), str(meta.get("message"))):
    sys.exit(tally.finish())
saved = OUT / f"bench_331_before_{stamp}.json"
saved.write_text(json.dumps(bundle, indent=2), encoding="utf-8")
print(f"  saved {saved.name}", flush=True)
if not bundle.get("activities"):
    sys.exit("the bundle has no activities: HA would merge instead of erase")
want_order = {kind: display_order(bundle, kind) for kind in ("devices", "activities")}
if hub_order:
    tally.check("the backup holds the reversed order",
                all(want_order[kind] == list(reversed(hub_order[kind])) for kind in want_order),
                json.dumps(want_order)[:400])

# -- 3: the erasing restore -----------------------------------------------------------------------

tally.section("3: erasing restore through HA")
mark = log_mark()
op_id = ws.ok({"type": f"{DOMAIN}/backup/restore", "entry_id": hub.entry_id, "backup": bundle,
               "mode": "replace"})["operation_id"]
events = watch_operation(ws, op_id, timeout=3600)
time.sleep(3)
lines = hub_lines(log_since(mark), hub.entry_id)
final = events[-1] if events else {}
result = final.get("result") or {}
seen = phases(events)
print(f"  phases: {' > '.join(seen)}", flush=True)
tally.check("restore succeeded", final.get("status") == "success",
            f"{final.get('message')} failed_at={result.get('failed_at')!r}")
tally.check("the engine wrote both display orders",
            result.get("display_order") == {"devices": "written", "activities": "written"},
            json.dumps(result.get("display_order")))
tail = [p for p in seen if p in ("hub", "cache_warm", "remote_sync")]
tally.check("progress: hub > cache_warm > remote_sync, the remotes last",
            tail == ["hub", "cache_warm", "remote_sync"] and seen.index("remote_sync") == len(
                [p for p in seen if p != "completed"]) - 1, " > ".join(seen))

sends = wire_sends(lines, hub.entry_id)
batch = [line[line.find("[BATCH]"):] for line in lines if "[BATCH]" in line]
erase_at = next((i for i, line in enumerate(lines) if "[ERASE] sending" in line), None)
opened_at = next((i for i, line in enumerate(lines) if "write batch opened" in line), None)
if not sends:
    tally.note("no [SEND] lines for this hub in the HA log: wire checks skipped (debug logging off?)")
else:
    syncs = [i for i, name in enumerate(sends) if name in ("REMOTE_SYNC", "X2_REMOTE_SYNC_ALL")]
    tally.check("exactly one remote-sync frame during the restore", len(syncs) == 1, f"{len(syncs)}")
    # The hub name goes out as a family frame whose opcode carries the
    # payload length (0x0630 for "X1 HUB"), so find it by its own log line.
    rename_at = [i for i, line in enumerate(lines) if "setting hub name" in line]
    sync_at = [i for i, line in enumerate(lines) if "[SEND] hub" in line and "REMOTE_SYNC" in line]
    tally.check("the remote sync after the hub name", bool(sync_at) and bool(rename_at) and sync_at[0] > rename_at[-1],
                f"sync at line {sync_at}, hub name at line {rename_at}")
tally.check("the write batch opened before the erase",
            opened_at is not None and erase_at is not None and opened_at < erase_at, f"{opened_at} < {erase_at}")
tally.check("the write batch closed with one sent trigger",
            any("write batch closed" in line and "-> sent" in line for line in batch), " | ".join(batch)[:400])
errors = errors_in(lines)
tally.check("no integration or library ERROR lines during the restore", not errors, f"{len(errors)}")

# -- 4: verify ------------------------------------------------------------------------------------

tally.section("4: verification backup")
meta, after_bundle = run_backup(ws, hub)
if tally.check("verification backup", meta.get("status") == "success", str(meta.get("message"))):
    OUT.joinpath(f"bench_331_after_{stamp}.json").write_text(json.dumps(after_bundle, indent=2), encoding="utf-8")
    before, after = bundle_summary(bundle), bundle_summary(after_bundle)
    tally.check("devices match (names, classes, counts)", after["devices"] == before["devices"])
    tally.check("activities match (counts)", after["activities"] == before["activities"])
    problems = display_order_problems(bundle, after_bundle)
    tally.check("display order kept (devices and activities)", not problems, " | ".join(problems)[:600])

    # -- 5: put the hub's own order back --------------------------------------------------------------
    if hub_order:
        tally.section("5: the hub's own order back")
        for kind, names in hub_order.items():
            ids = _id_by_name(after_bundle, kind)
            if sorted(ids) != sorted(names) or len(ids) != len(names):
                tally.note(f"{kind}: names changed or not unique, order not put back: {names}")
                continue
            if len(names) > 1:
                reorder(kind, [ids[name] for name in names])
        restored = ordered_entities()
        tally.check("hub order put back", {k: [n for _i, n in v] for k, v in restored.items()} == hub_order,
                    json.dumps({k: [n for _i, n in v] for k, v in restored.items()})[:400])

ws.close()
sys.exit(tally.finish())
