"""Probe UI-1: does an open card follow a change made elsewhere, also
right after its own reload?

The old card marked a new cache generation as seen while it skipped the
stale banner (busy, or within 10 s of its own reload), so a change in
that window left it silently stale. The card now reloads a hub's rows
whenever the pushed cache version (``hub_state/subscribe``) is newer than
the rows it holds, with no banner at all.

Two runs on one activity (renamed and renamed back each time):

- A: reload the passive tab, then rename at once (the old blind window);
- B: reload, wait 12 s, then rename (the control).

For each: what the store holds (the rows' and the reported cache version,
whether the push is live), how long after the rename the list showed the
new name, and that no banner ever showed. Converging within
``CONVERGE_OK_S`` of the rename's end passes.
"""

from __future__ import annotations

import argparse
import copy
import json
import threading
import time

import ha_bench as hb

from ..normalize import normalize_bundle
from ..ui import UiObserver

#: The list must show the change this soon after the rename ended.
CONVERGE_OK_S = 2.0

STORE = """(entry) => {
  const f = (r, sel) => { if (!r) return null; const h = r.querySelector(sel); if (h) return h;
    for (const e of r.querySelectorAll('*')) if (e.shadowRoot) { const x = f(e.shadowRoot, sel); if (x) return x; } return null; };
  const card = f(document, 'sofabaton-control-panel');
  const store = card && card._store;
  if (!store) return null;
  const snap = store.snapshot;
  const row = ((snap.contents || {}).hubs || []).find((h) => h.entry_id === entry) || {};
  const hub = ((snap.state || {}).hubs || []).find((h) => h.entry_id === entry) || {};
  return { rows: [row.cache_epoch ?? null, row.cache_generation ?? null],
           reported: [hub.cache_epoch ?? null, hub.cache_generation ?? null],
           pushed: !!store._hubStateUnsub, running: (hub.runtime_state || {}).kind === 'operation_running',
           banner: !!f(card.shadowRoot || card, '.stale-banner') };
}"""


def rename(hub: hb.Hub, activity_id: int, name: str) -> str:
    ws = hb.HaWs()
    bundle, _ = hb.structural_bundle(ws, hub)
    edited = copy.deepcopy(bundle)
    next(r for r in edited["activities"] if r["device"]["device_id"] == activity_id)["device"]["name"] = name
    op = ws.ok({"type": f"{hb.DOMAIN}/activity/sync", "entry_id": hub.entry_id, "activity_id": activity_id,
                "baseline": bundle, "edited": edited})["operation_id"]
    status = hb.watch_operation(ws, op)[-1].get("status")
    ws.close()
    return str(status)


def run_case(ui: UiObserver, hub: hb.Hub, activity_id: int, new_name: str, wait_first: float) -> dict:
    page = ui.pages["Activities"]
    page.reload(wait_until="domcontentloaded")
    t_load = time.time()
    ui._open(page, "Activities")
    if wait_first:
        page.wait_for_timeout(int(wait_first * 1000))
    t0 = time.time()
    box: dict = {}

    def _rename() -> None:
        box["status"] = rename(hub, activity_id, new_name)
        box["ended"] = time.time()

    worker = threading.Thread(target=_rename)
    worker.start()
    timeline, prev, shown_at, banner = [], None, None, False
    while worker.is_alive() or time.time() - t0 < 15:
        state = page.evaluate(STORE, hub.entry_id)
        if state != prev:
            timeline.append([round(time.time() - t0, 2), state])
            prev = state
        banner = banner or bool((state or {}).get("banner"))
        if shown_at is None and new_name in [e["name"] for e in (ui.read_page(page) or {}).get("entities", [])]:
            shown_at = time.time()
        time.sleep(0.2)
    shown = [e["name"] for e in (ui.read_page(page) or {}).get("entities", [])]
    ended = box.get("ended")
    return {"wait_first_s": wait_first, "change_started_after_load_s": round(t0 - t_load, 2),
            "rename": box.get("status"), "rename_s": round(ended - t0, 2) if ended else None,
            "shown_after_rename_end_s": round(shown_at - ended, 2) if (shown_at and ended) else None,
            "banner_ever": banner, "timeline": timeline, "final": prev, "list_shows": shown}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hub", required=True)
    args = parser.parse_args()
    ws = hb.wait_for_ha()
    hub = hb.resolve_hub(ws, args.hub)
    bundle, _ = hb.structural_bundle(ws, hub)
    world = normalize_bundle(bundle)
    activity_id = world["order"]["activities"][-1]
    original = world["activities"][str(activity_id)]["head"]["name"]
    probe_name = f"J {original}"[:30]
    print(f"activity {activity_id} {original!r}", flush=True)
    ui = UiObserver(hub).start()
    results = {}
    try:
        for label, wait_first in (("A_inside_window", 0.0), ("B_outside_window", 12.0)):
            print(f"\n== {label}", flush=True)
            result = run_case(ui, hub, activity_id, probe_name, wait_first)
            results[label] = result
            print(f"   change started {result['change_started_after_load_s']} s after the reload; rename {result['rename']}")
            for t, state in result["timeline"]:
                print(f"   {t:6.2f}s {json.dumps(state)}")
            lag = result["shown_after_rename_end_s"]
            verdict = ("STALE (never showed the change)" if probe_name not in result["list_shows"] else
                       "BANNER shown (the old model)" if result["banner_ever"] else
                       f"converged {lag} s after the rename" if lag is not None and lag <= CONVERGE_OK_S else
                       f"converged, but SLOW ({lag} s after the rename)")
            result["verdict"] = verdict
            print(f"   list shows {result['list_shows']} -> {verdict}", flush=True)
            print("   (renaming back)", rename(hub, activity_id, original), flush=True)
            time.sleep(3)
    finally:
        ui.close()
        out = hb.OUT / "journeys" / f"probe_stale_grace_{hub.version.lower()}_{time.strftime('%Y%m%d_%H%M%S')}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(results, indent=1, default=str), encoding="utf-8")
        print(f"\nreport {out.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
