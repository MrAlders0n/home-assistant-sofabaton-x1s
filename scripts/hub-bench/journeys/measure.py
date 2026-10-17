"""What each layer costs, and whether reading is stable (plan section 11).

Read-only for the hub's configuration: a cache refresh and a backup export
read every entity, they write nothing.

Answers three phase-0 questions:

- the time of each layer (cheap reads repeated, hub refresh ``--repeat``
  times, the full export once with ``--full``);
- does ``cache/refresh_all`` really re-read every entity? (the sends it
  makes, per opcode, against the device and activity counts);
- is observation stable? Two hub reads in a row must agree, and the full
  export must agree with the structural hub read wherever both carry the
  same data.
"""

from __future__ import annotations

import json
import statistics
import time
from typing import Any

import ha_bench as hb

from .frame import check
from .world import Wire, World, configured_levels, restore_logging, set_debug_logging


def _stats(values: list[float]) -> dict[str, float]:
    return {"n": len(values), "min": round(min(values), 3), "median": round(statistics.median(values), 3),
            "max": round(max(values), 3)}


def measure(hub_which: str, *, repeat: int = 2, full: bool = False) -> int:
    ws = hb.wait_for_ha()
    hub = hb.resolve_hub(ws, hub_which)
    world = World(ws, hub)
    levels = configured_levels()
    set_debug_logging()
    report: dict[str, Any] = {"hub": hub.version, "hub_name": hub.name, "entry": hub.entry_id}
    problems: list[str] = []
    try:
        ha_t, cache_t = [], []
        for _ in range(5):
            t0 = time.time()
            world.read_ha()
            ha_t.append(time.time() - t0)
            t0 = time.time()
            bundle, _gen = world.read_cache_raw()
            cache_t.append(time.time() - t0)
        report["ha"] = _stats(ha_t)
        report["cache"] = _stats(cache_t)
        counts = {"devices": len((bundle or {}).get("devices") or []),
                  "activities": len((bundle or {}).get("activities") or [])}
        report["entities"] = counts
        print(f"ha {report['ha']}\ncache {report['cache']}\nentities {counts}", flush=True)

        wire = Wire(hub.entry_id)
        hub_t, hub_reads, wire_t = [], [], []
        for i in range(repeat):
            wire.mark()
            t0 = time.time()
            snap = world.snapshot(hub=True)
            hub_t.append(snap.timings["hub"])
            t0 = time.time()
            sends = wire.sends()
            wire_t.append(time.time() - t0)
            summary = Wire.summary(sends)
            hub_reads.append(snap.layers["hub"])
            report.setdefault("refresh_sends", []).append(summary)
            print(f"hub read {i + 1}: {snap.timings['hub']:.1f}s, sends {summary['total']} "
                  f"(writes {summary['writes']}) {summary['counts']}", flush=True)
            if summary["writes"]:
                problems.append(f"refresh {i + 1} sent writes: {summary['writes']}")
            stale = check({"x": snap.layers["cache"]}, {"x": snap.layers["hub"]}, [], layers=("x",))
            if stale.findings:
                problems.append(f"cache before refresh {i + 1} != hub: "
                                + "; ".join(c.render() for c in stale.findings[:10]))
        report["hub"] = _stats(hub_t)
        report["wire_parse"] = _stats(wire_t)
        for i in range(1, len(hub_reads)):
            drift = check({"hub": hub_reads[i - 1]}, {"hub": hub_reads[i]}, [], layers=("hub",))
            if drift.findings:
                problems.append(f"hub reads {i} and {i + 1} differ: "
                                + "; ".join(c.render() for c in drift.findings[:10]))

        if full:
            wire.mark()
            t0 = time.time()
            normalized, raw = world.full_export()
            report["full"] = {"seconds": round(time.time() - t0, 1), "bytes": len(json.dumps(raw))}
            summary = Wire.summary(wire.sends())
            report["full_sends"] = summary
            print(f"full export: {report['full']}, sends {summary['total']} (writes {summary['writes']})", flush=True)
            if summary["writes"]:
                problems.append(f"full export sent writes: {summary['writes']}")
            # Commands differ by design (the export carries the payloads);
            # activities, device heads and the order must agree.
            last = hub_reads[-1]
            allowance = ["x/devices/*/commands", "x/devices/*/key_sort", "x/devices/*/inputs"]
            agree = check({"x": last}, {"x": normalized}, allowance, layers=("x",))
            report["full_vs_hub"] = {"allowed": len(agree.allowed), "findings": [c.render() for c in agree.findings]}
            if agree.findings:
                problems.append(f"full export != hub read: "
                                + "; ".join(c.render() for c in agree.findings[:10]))
            (hb.OUT / "journeys").mkdir(parents=True, exist_ok=True)
            (hb.OUT / "journeys" / f"full_{hub.version.lower()}_{time.strftime('%Y%m%d_%H%M%S')}.json") \
                .write_text(json.dumps(raw), encoding="utf-8")
    finally:
        restore_logging(levels)
    report["problems"] = problems
    out = hb.OUT / "journeys" / f"measure_{hub.version.lower()}_{time.strftime('%Y%m%d_%H%M%S')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    print(f"\n{len(problems)} problems; report {out.name}", flush=True)
    for line in problems:
        print(f"  PROBLEM {line[:600]}", flush=True)
    return 1 if problems else 0
