"""Display-order probe (X1 / X1S): what the hub does with the order when we
create things, and whether ours keeps it (ordering audit, 2026-10-14).

Found with it on 2026-10-14 (before the fixes): the X1 appends a created
device whatever sort byte the create carries; the X1S stores that byte
verbatim (0 for an empty device, ties for a restored one) and lowers every
sort above a deleted device's; one X1S favorite add reset a custom shortcut
order to id order. The checks now assert the fixed behaviour.

P1, devices and activities (stored sort bytes, read back from the catalog):
  - create_device: the new device last, positions 1..N;
  - (hub behaviour) a raw restore_device carrying sort 1: where it lands;
  - a merged restore of one device (restore_hub_bundle): last, positions 1..N;
  - create_activity: the new activity last, positions 1..N;
  - after deleting all of them: the order as it was.
P2, shortcuts (on a COPY of --source, restore_activity appends it):
  - the restored copy keeps the source's shortcut order (compared by
    content: favorites as device/command, macros by name);
  - --reverse writes the copy's order reversed first (a source in id order
    cannot tell an id-order rewrite from a kept order) and restores a second
    copy from it, which must keep the reversed order;
  - adding ONE favorite (command_to_favorite, as the live editor calls it)
    keeps the order with the new one last, and lists every shortcut.

Everything created is deleted at the end (--keep leaves it for a look at the
remote; --cleanup deletes what a kept run left). The real devices and
activities are only read. Deleting a device sweeps activities with no
members, so the probe refuses to run when a real activity has none. The HA
entry of the hub must be disabled first (ha_entry.py) and re-enabled after.

Usage:
    python bench_332_order_probe.py <ip> <X1|X1S> --source 0x66 [--reverse] [--skip-p1] [--keep]
    python bench_332_order_probe.py <ip> <X1|X1S> --cleanup
"""

from __future__ import annotations

import sys
import time

from bench_common import connect, save_json, setup_logging
from x1slib.device_class_profiles import build_empty_device_payload  # noqa: E402
from x1slib.hub_versions import HUB_BUNDLE_SCHEMA_VERSION  # noqa: E402

HOST, HVER = sys.argv[1], sys.argv[2]
ARGS = sys.argv[3:]
DEV_A, DEV_B, DEV_C, ACT_C = "Bench order A", "Bench order B", "Bench order C", "Bench order act"
COPY_NAME, COPY_NAME_2 = "Bench QA order", "Bench QA order 2"
PROBE_NAMES = {DEV_A, DEV_B, DEV_C, ACT_C, COPY_NAME, COPY_NAME_2}
checks: list[tuple[str, bool, str]] = []
report: dict = {"host": HOST, "hub_version": HVER}


def check(name: str, ok: bool, detail: str = "") -> bool:
    checks.append((name, ok, detail))
    print(f"[{'YES ' if ok else 'NO  '}] {name}{': ' + detail if detail else ''}", flush=True)
    return ok


def sorts(proxy, kind: str) -> dict[int, tuple[str, int]]:
    """``{id: (name, stored sort byte)}`` from a fresh catalog read."""

    if kind == "devices":
        proxy._request_devices_and_wait()
        table = proxy.state.entities("device")
    else:
        proxy._request_activities_and_wait()
        table = proxy.state.entities("activity")
    out = {}
    for entity_id, row in table.items():
        if not isinstance(row, dict):
            continue
        raw = row.get("raw_body")
        sort = int(raw[6]) if isinstance(raw, (bytes, bytearray)) and len(raw) > 6 else -1
        out[int(entity_id) & 0xFF] = (str(row.get("name")), sort)
    return out


def shown(table: dict[int, tuple[str, int]]) -> list[str]:
    """Display order by the engine's rule: sort byte first, 0 after by id."""

    def key(item):
        entity_id, (_name, sort) = item
        return (0, sort, entity_id) if sort > 0 else (1, 0, entity_id)
    return [f"{name}({entity_id}:{sort})" for entity_id, (name, sort) in sorted(table.items(), key=key)]


def last_and_dense(table: dict[int, tuple[str, int]], entity_id: int) -> bool:
    """The entity is last in display order and the stored positions are
    exactly 1..N (no zeros, no ties)."""

    def key(item):
        other_id, (_name, sort) = item
        return (0, sort, other_id) if sort > 0 else (1, 0, other_id)
    ordered = sorted(table.items(), key=key)
    return bool(ordered) and ordered[-1][0] == entity_id and [
        sort for _i, (_n, sort) in ordered] == list(range(1, len(ordered) + 1))


def members(proxy, act: int) -> list[int]:
    proxy._activity_map_complete.discard(act)
    proxy._fetch_and_wait(f"activity_map:{act}", lambda: proxy.request_activity_mapping(act),
                          lambda: act in proxy._activity_map_complete, timeout=10.0)
    return proxy.state.get_activity_members(act)


def shortcuts(payload: dict) -> list[tuple]:
    """An activity backup's shortcuts in display order, by content."""

    entries: dict[int, tuple] = {}
    for row in payload.get("favorite_slots") or []:
        entries[int(row.get("button_id") or 0)] = ("fav", int(row.get("device_id") or 0), int(row.get("command_id") or 0))
    for row in payload.get("macros") or []:
        key = int(row.get("button_id") or 0)
        if key not in (0, 198, 199):
            entries[key] = ("macro", str(row.get("name")))
    order = [int(x) for x in payload.get("favorites_order") or []]
    return [entries[i] for i in order if i in entries] + [entries[i] for i in sorted(entries) if i not in order]


def ids_by_content(payload: dict) -> dict[tuple, int]:
    out: dict[tuple, int] = {}
    for row in payload.get("favorite_slots") or []:
        out[("fav", int(row.get("device_id") or 0), int(row.get("command_id") or 0))] = int(row.get("button_id") or 0)
    for row in payload.get("macros") or []:
        key = int(row.get("button_id") or 0)
        if key not in (0, 198, 199):
            out[("macro", str(row.get("name")))] = key
    return out


def cleanup(proxy) -> None:
    acts = sorts(proxy, "activities")
    for act, (name, _sort) in acts.items():
        if name in PROBE_NAMES:
            print(f"deleting activity {name!r} (0x{act:02X}):", proxy.delete_device(act))
    for dev, (name, _sort) in sorts(proxy, "devices").items():
        if name in PROBE_NAMES:
            print(f"deleting device {name!r} (0x{dev:02X}):", proxy.delete_device(dev))
    time.sleep(3)


def probe_devices_and_activities(proxy) -> None:
    base_dev, base_act = sorts(proxy, "devices"), sorts(proxy, "activities")
    report["p1_base"] = {"devices": shown(base_dev), "activities": shown(base_act)}
    print("devices:", shown(base_dev), "\nactivities:", shown(base_act), flush=True)

    a = proxy.create_device(DEV_A, device_class="ir")
    a_id = int((a or {}).get("device_id") or 0)
    after_a = sorts(proxy, "devices")
    report["p1_after_create_device"] = shown(after_a)
    print("after create_device:", shown(after_a), flush=True)
    check("create_device: the new device is last, positions 1..N", last_and_dense(after_a, a_id),
          f"{after_a.get(a_id)}")

    payload = build_empty_device_payload(DEV_B, "ir", hub_version=HVER)
    payload["device"]["sort"] = 1
    b = proxy.restore_device(payload)
    b_id = int((b or {}).get("device_id") or 0)
    after_b = sorts(proxy, "devices")
    report["p1_after_create_with_sort_1"] = shown(after_b)
    print("after a create carrying sort 1:", shown(after_b), flush=True)
    # Hub behaviour, not a product path: the raw create every product create
    # wraps (X1S keeps the byte, X1 appends). The merge below settles it.
    check("(hub) a raw create carrying sort 1 is stored at", True, f"{after_b.get(b_id)}")

    payload_c = build_empty_device_payload(DEV_C, "ir", hub_version=HVER)
    payload_c["device"]["device_id"] = 0x63
    payload_c["device"]["sort"] = 1
    merged = proxy.restore_hub_bundle({"kind": "hub_bundle", "schema_version": HUB_BUNDLE_SCHEMA_VERSION,
                                       "devices": [payload_c], "activities": []})
    c_dev = int(((merged or {}).get("restored_devices") or [{}])[0].get("device_id") or 0)
    after_merge = sorts(proxy, "devices")
    report["p1_after_merged_restore"] = {"result": merged, "devices": shown(after_merge)}
    print("after a merged restore of one device:", shown(after_merge), flush=True)
    check("a merged restore puts the device last, positions 1..N", last_and_dense(after_merge, c_dev),
          f"{(merged or {}).get('display_order')} {after_merge.get(c_dev)}")

    c = proxy.create_activity(ACT_C)
    c_id = int((c or {}).get("activity_id") or 0)
    after_c = sorts(proxy, "activities")
    report["p1_after_create_activity"] = shown(after_c)
    print("after create_activity:", shown(after_c), flush=True)
    check("create_activity: the new activity is last, positions 1..N", last_and_dense(after_c, c_id),
          f"{after_c.get(c_id)}")

    if "--keep" in ARGS:
        proxy.resync_remote()
        print("kept the probe device(s) and activity; look at the remote, then --cleanup")
        return
    cleanup(proxy)
    end_dev, end_act = sorts(proxy, "devices"), sorts(proxy, "activities")
    report["p1_after_cleanup"] = {"devices": shown(end_dev), "activities": shown(end_act)}
    check("after the deletes: device order as before", shown(end_dev) == shown(base_dev), str(shown(end_dev)))
    check("after the deletes: activity order as before", shown(end_act) == shown(base_act), str(shown(end_act)))


def probe_shortcuts(proxy, source: int) -> None:
    src = proxy.backup_activity(source)
    if not src:
        check(f"source activity 0x{source:02X} readable", False)
        return
    want = shortcuts(src)
    report["p2_source"] = {"favorites_order": src.get("favorites_order"), "shortcuts": want}
    print(f"source 0x{source:02X} shortcuts:", want, flush=True)
    payload = dict(src)
    payload["device"] = {**src["device"], "name": COPY_NAME}
    refs = [int(d) for d in src.get("referenced_source_device_ids") or [] if int(d) < 0x65]
    result = proxy.restore_activity(payload, device_id_map={d: d for d in refs}, send_remote_sync=False)
    copy = int((result or {}).get("activity_id") or 0)
    if not check("copy restored", bool(copy), str(result)):
        return
    restored = proxy.backup_activity(copy)
    got = shortcuts(restored)
    report["p2_restored"] = {"favorites_order": restored.get("favorites_order"), "shortcuts": got}
    check("restored copy keeps the source's shortcut order", got == want, f"{got}")

    # A source already in id order cannot tell an id-order rewrite from a
    # kept order: --reverse writes the copy's shortcuts reversed first.
    target = list(reversed(want)) if "--reverse" in ARGS else want
    id_of = ids_by_content(restored)
    desired = [id_of[item] for item in target if item in id_of]
    proxy.reorder_favorites(copy, desired, refresh_after_write=False)
    custom_payload = proxy.backup_activity(copy)
    custom = shortcuts(custom_payload)
    report["p2_custom"] = custom
    check("the custom order written onto the copy reads back", custom == target, f"{custom}")

    if "--reverse" in ARGS:
        # The restore check with teeth: a copy of the reordered copy.
        payload2 = dict(custom_payload)
        payload2["device"] = {**custom_payload["device"], "name": COPY_NAME_2}
        second = proxy.restore_activity(payload2, device_id_map={d: d for d in refs}, send_remote_sync=False)
        copy2 = int((second or {}).get("activity_id") or 0)
        second_order = shortcuts(proxy.backup_activity(copy2)) if copy2 else None
        report["p2_second_copy"] = second_order
        check("a restore of the reordered copy keeps its order", second_order == custom, f"{second_order}")

    favored = {(item[1], item[2]) for item in want if item[0] == "fav"}
    candidate = None
    for dev in refs:
        proxy._fetch_and_wait(f"commands:{dev}", lambda d=dev: proxy.get_commands_for_entity(d, fetch_if_missing=True),
                              lambda d=dev: d in proxy._commands_complete, timeout=10.0)
        for cmd in sorted(proxy.state.commands.get(dev) or {}):
            if (dev, int(cmd)) not in favored:
                candidate = (dev, int(cmd))
                break
        if candidate:
            break
    if not check("a member command that is not a favorite yet", candidate is not None):
        return
    added = proxy.command_to_favorite(copy, candidate[0], candidate[1], refresh_after_write=False)
    after = proxy.backup_activity(copy)
    after_order = shortcuts(after)
    report["p2_after_add"] = {"added": added, "favorites_order": after.get("favorites_order"), "shortcuts": after_order}
    print("after adding one favorite:", after_order, "raw order", after.get("favorites_order"), flush=True)
    expected = custom + [("fav", candidate[0], candidate[1])]
    check("adding one favorite keeps the order, new one last", after_order == expected, f"{after_order}")
    ranked = [int(x) for x in after.get("favorites_order") or []]
    unranked = sorted(set(ids_by_content(after).values()) - set(ranked))
    check("every shortcut is in the order table after the add", not unranked, f"unlisted ids {unranked}")


def main() -> int:
    log_path = setup_logging(f"order-probe-{HVER}")
    print(f"logging to {log_path}")
    proxy = connect(HOST, HVER)
    try:
        if "--cleanup" in ARGS:
            cleanup(proxy)
            proxy.resync_remote()
            return 0
        existing = {name for name, _s in sorts(proxy, "activities").values()} | {
            name for name, _s in sorts(proxy, "devices").values()}
        if existing & PROBE_NAMES:
            print(f"probe entities already on the hub ({existing & PROBE_NAMES}): run --cleanup first")
            return 2
        empty = [act for act in sorts(proxy, "activities") if not members(proxy, act)]
        if empty:
            print(f"activities without members {empty}: a device delete would sweep them; not probing")
            return 2
        if "--source" in ARGS:
            probe_shortcuts(proxy, int(ARGS[ARGS.index("--source") + 1], 0))
            if "--keep" not in ARGS:
                cleanup(proxy)  # the copy, before P1 reads its baseline
        if "--skip-p1" not in ARGS:
            probe_devices_and_activities(proxy)
        if "--keep" not in ARGS:
            cleanup(proxy)
            proxy.resync_remote()
    finally:
        proxy.stop()
        report["checks"] = [{"check": n, "yes": ok, "detail": d} for n, ok, d in checks]
        print(f"saved {save_json(f'order-probe-{HVER}', report)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
