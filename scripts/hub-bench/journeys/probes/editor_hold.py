"""Probe: an open editor waits while the hub works, then follows once.

Mid job the cache passes through partial states, so an open editor must
not rebase while the hub works for someone else; it follows once the job
has ended (the card reloads the hub's rows then). Lists inside a device
merge row by row, so an edit here and a different edit elsewhere both
stay (7e364b0e).

One plain device, two of its commands A and B. Another client (a second
websocket, what another tab or an automation would send) renames B with a
device sync, a job of several seconds:

- clean: the editor holds no edits;
- dirty: the editor has A renamed locally (through the editor's own
  bundle-change handler, the path a rename in the view takes); the other
  client renames B back.

Sampled every 0.1 s: the hub's runtime state as the editor sees it, the
cache version it captured, the card's loaded version. Pass: no follow
while the hub works, one follow after the end, B shows the other client's
name, and in the dirty case A keeps the local name with no "changed
elsewhere" line. Everything is put back.
"""

from __future__ import annotations

import argparse
import copy
import json
import threading
import time
from typing import Any

import ha_bench as hb
from playwright.sync_api import sync_playwright

from ..actions.ui_editor import open_device_editor
from ..ui import new_context, open_panel

STATE = """() => {
  const f = (r, sel) => { if (!r) return null; const h = r.querySelector(sel); if (h) return h;
    for (const e of r.querySelectorAll('*')) if (e.shadowRoot) { const x = f(e.shadowRoot, sel); if (x) return x; } return null; };
  const el = f(document, 'sofabaton-activities-tab');
  if (!el) return null;
  const row = ((el._working || {}).devices || []).find((d) => d.device && d.device.device_id === el._entityId) || {};
  const names = {};
  for (const c of row.commands || []) names[c.command_id] = c.name;
  return { stage: el._stage, running: ((el.hub || {}).runtime_state || {}).kind === 'operation_running',
           captured: el._capturedVersion, version: el.cacheVersion, dirty: el._dirty,
           elsewhere: el._changedElsewhere, names };
}"""

LOCAL_RENAME = """([cmd, name]) => {
  const f = (r, sel) => { if (!r) return null; const h = r.querySelector(sel); if (h) return h;
    for (const e of r.querySelectorAll('*')) if (e.shadowRoot) { const x = f(e.shadowRoot, sel); if (x) return x; } return null; };
  const el = f(document, 'sofabaton-activities-tab');
  const bundle = structuredClone(el._working);
  const row = bundle.devices.find((d) => d.device && d.device.device_id === el._entityId);
  row.commands.find((c) => c.command_id === cmd).name = name;
  el._handleBundleChange(new CustomEvent('bundle-change', { detail: { bundle } }));
  return el._dirty;
}"""

DISCARD = """() => {
  const f = (r, sel) => { if (!r) return null; const h = r.querySelector(sel); if (h) return h;
    for (const e of r.querySelectorAll('*')) if (e.shadowRoot) { const x = f(e.shadowRoot, sel); if (x) return x; } return null; };
  const el = f(document, 'sofabaton-activities-tab');
  if (el) el._resetToList();
}"""


def rename_command(hub: hb.Hub, device_id: int, command_id: int, name: str) -> dict[str, Any]:
    """The other client: a device sync renaming one command."""

    ws = hb.HaWs()
    try:
        baseline, _ = hb.structural_bundle(ws, hub)
        edited = copy.deepcopy(baseline)
        row = next(r for r in edited["devices"] if r["device"]["device_id"] == device_id)
        next(c for c in row["commands"] if int(c["command_id"]) == command_id)["name"] = name
        res, _secs = ws.call({"type": f"{hb.DOMAIN}/device/sync", "entry_id": hub.entry_id, "device_id": device_id,
                              "baseline": baseline, "edited": edited})
        if not res.get("success"):
            return {"status": "refused", "error": res.get("error")}
        events = hb.watch_operation(ws, res["result"]["operation_id"])
        return {"status": (events[-1] if events else {}).get("status")}
    finally:
        ws.close()


def run_case(page, hub: hb.Hub, device_id: int, b_id: int, b_name: str, tail: float = 8.0) -> dict[str, Any]:
    box: dict[str, Any] = {}

    def _job() -> None:
        box["result"] = rename_command(hub, device_id, b_id, b_name)
        box["ended"] = time.time()

    t0 = time.time()
    worker = threading.Thread(target=_job)
    worker.start()
    timeline, prev = [], None
    while worker.is_alive() or time.time() - box.get("ended", time.time()) < tail:
        state = page.evaluate(STATE)
        if state != prev:
            timeline.append([round(time.time() - t0, 2), state])
            prev = state
        time.sleep(0.1)
    worker.join()

    running = [t for t, s in timeline if s and s["running"]]
    start = timeline[0][1]["captured"] if timeline and timeline[0][1] else None
    follows = []  # (time, captured) each time the captured version moved
    last = start
    for t, s in timeline:
        if s and s["captured"] != last:
            follows.append((t, s["captured"]))
            last = s["captured"]
    busy_end = None
    if running:
        after = [t for t, s in timeline if t > running[-1] and s and not s["running"]]
        busy_end = after[0] if after else None
    during = [f for f in follows if running and running[0] <= f[0] < (busy_end or 1e9)]
    after_end = [f for f in follows if busy_end is not None and f[0] >= busy_end]
    final = prev or {}
    return {
        "job": box.get("result"), "job_s": round(box.get("ended", t0) - t0, 2),
        "busy": [running[0], running[-1]] if running else None, "busy_end_seen": busy_end,
        "follows_while_busy": during, "follows_after_end": after_end,
        "first_follow_after_end_s": round(after_end[0][0] - busy_end, 2) if after_end and busy_end else None,
        "final": final, "timeline": timeline,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hub", required=True)
    args = parser.parse_args()
    ws = hb.wait_for_ha()
    hub = hb.resolve_hub(ws, args.hub)
    bundle, _ = hb.structural_bundle(ws, hub)
    device = next(
        r for r in bundle["devices"]
        if len(r.get("commands") or []) >= 3
        and not str(r["device"].get("brand") or "").startswith("m3-")
        and not r["device"].get("hide")
    )
    device_id, device_name = int(device["device"]["device_id"]), device["device"]["name"]
    (a_id, a_name), (b_id, b_name) = [(int(c["command_id"]), c["name"]) for c in device["commands"][:2]]
    print(f"{hub.version}: device {device_id} {device_name!r}; A = {a_id} {a_name!r}, B = {b_id} {b_name!r}", flush=True)
    report: dict[str, Any] = {"hub": hub.version, "device": [device_id, device_name], "A": [a_id, a_name], "B": [b_id, b_name]}
    renamed_b = f"J {b_name}"[:20]
    with sync_playwright() as pw:
        browser, ctx = new_context(pw)
        page = ctx.new_page()
        try:
            page.goto(hb.BASE_URL + "/sofabaton-x/control-panel", wait_until="domcontentloaded", timeout=60000)
            page.locator("sofabaton-x-panel").wait_for(timeout=30000)
            open_panel(page, hub, "Hub", "Devices")
            open_device_editor(page, device_name)
            page.wait_for_timeout(1500)

            print("\n== clean editor; the other client renames B", flush=True)
            report["clean"] = run_case(page, hub, device_id, b_id, renamed_b)

            print("\n== dirty editor (A renamed here); the other client renames B back", flush=True)
            report["dirty_local_edit"] = page.evaluate(LOCAL_RENAME, [a_id, f"J {a_name}"[:20]])
            report["dirty"] = run_case(page, hub, device_id, b_id, b_name)
            page.evaluate(DISCARD)
        finally:
            browser.close()
            # Whatever happened, B gets its own name back.
            check = hb.HaWs()
            current, _ = hb.structural_bundle(check, hub)
            check.close()
            row = next(r for r in current["devices"] if r["device"]["device_id"] == device_id)
            now_b = next(c["name"] for c in row["commands"] if int(c["command_id"]) == b_id)
            if now_b != b_name:
                report["restore_b"] = rename_command(hub, device_id, b_id, b_name)

    for case in ("clean", "dirty"):
        r = report.get(case) or {}
        print(f"\n{case}: job {r.get('job')} in {r.get('job_s')} s; hub busy {r.get('busy')}; "
              f"follows while busy {len(r.get('follows_while_busy') or [])}; follows after the end "
              f"{len(r.get('follows_after_end') or [])} (first {r.get('first_follow_after_end_s')} s after)")
        final = r.get("final") or {}
        print(f"   editor: A={final.get('names', {}).get(str(a_id), final.get('names', {}).get(a_id))!r} "
              f"B={final.get('names', {}).get(str(b_id), final.get('names', {}).get(b_id))!r} "
              f"dirty={final.get('dirty')} changed-elsewhere={final.get('elsewhere')}")
    out = hb.OUT / "journeys" / f"probe_editor_hold_{hub.version.lower()}_{time.strftime('%Y%m%d_%H%M%S')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    print(f"\nreport {out.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
