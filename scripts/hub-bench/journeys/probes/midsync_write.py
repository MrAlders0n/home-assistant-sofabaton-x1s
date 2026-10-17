"""Probe UI-3's consequence: a write from a tab that is not locked yet,
while another write is still running.

Passive tabs learn about an operation from a 5 s poll, so for the first
seconds of a sync another tab still offers every write. What happens if
someone uses it?

1. Start a paged write: rename a user macro through ``activity/sync``
   (a macro save is several pages; the X1 refuses a record page when
   another request lands inside one, finding F4).
2. About 1 s in, from a second connection (what an unlocked tab sends):
   - I1 reorder the activities,
   - I2 rename another activity (``activity/sync``),
   - I3 rename a plain device (``device/sync``);
   and, in a passive tab, click Edit on another activity and record what
   opens.
3. After everything settles: did the macro rename land exactly; what did
   each interfering write do (refused, queued, or landed); does the cache
   still match a fresh hub read; any errors in the log.
4. Undo everything that landed.
"""

from __future__ import annotations

import argparse
import copy
import json
import threading
import time
from typing import Any

import ha_bench as hb

from ..frame import check
from ..normalize import labeler
from ..ui import UiObserver
from ..uistate import lock_state
from ..world import World

P = "sofabaton-x-panel sofabaton-control-panel"


def _bundle(ws: hb.HaWs, hub: hb.Hub) -> dict:
    return hb.structural_bundle(ws, hub)[0]


def _sync(ws: hb.HaWs, hub: hb.Hub, kind: str, entity_id: int, edit) -> dict[str, Any]:
    """Start an entity sync; returns the start result (or the refusal)."""

    baseline = _bundle(ws, hub)
    edited = copy.deepcopy(baseline)
    plural = "activities" if kind == "activity" else "devices"
    edit(next(r for r in edited[plural] if r["device"]["device_id"] == entity_id))
    key = "activity_id" if kind == "activity" else "device_id"
    res, secs = ws.call({"type": f"{hb.DOMAIN}/{kind}/sync", "entry_id": hub.entry_id, key: entity_id,
                         "baseline": baseline, "edited": edited})
    out: dict[str, Any] = {"accepted": bool(res.get("success")), "seconds": round(secs, 2)}
    if not res.get("success"):
        out["error"] = res.get("error")
        return out
    events = hb.watch_operation(ws, res["result"]["operation_id"])
    final = events[-1] if events else {}
    out.update(status=final.get("status"), message=final.get("message"), phases=hb.phases(events))
    return out


def _backend_running(ws: hb.HaWs, hub: hb.Hub) -> bool:
    for h in ws.ok({"type": f"{hb.DOMAIN}/control_panel/state"}).get("hubs") or []:
        if h.get("entry_id") == hub.entry_id:
            return (h.get("runtime_state") or {}).get("kind") == "operation_running"
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hub", required=True)
    args = parser.parse_args()
    ws = hb.wait_for_ha()
    hub = hb.resolve_hub(ws, args.hub)
    world = World(ws, hub)
    before = world.snapshot(hub=True)
    cache = before.layers["hub"]
    order = list(cache["order"]["activities"])

    # The macro: the first activity (display order) with a user macro.
    target = None
    for a in order:
        user = [k for k in cache["activities"][str(a)]["macros"] if k not in ("198", "199")]
        if user:
            target = (a, int(user[0]), cache["activities"][str(a)]["macros"][user[0]]["name"])
            break
    if target is None:
        raise SystemExit("no activity with a user macro")
    act, macro_key, macro_name = target
    other = next(a for a in order if a != act)
    other_name = cache["activities"][str(other)]["head"]["name"]
    plain = next(int(d) for d, dev in cache["devices"].items()
                 if not (dev["head"].get("hide") or str(dev["head"].get("brand") or "").startswith("m3-")))
    plain_name = cache["devices"][str(plain)]["head"]["name"]
    new_macro = f"J {macro_name}"[:20]
    print(f"{hub.version}: macro {macro_key} {macro_name!r} in activity {act}; other activity {other} "
          f"{other_name!r}; device {plain} {plain_name!r}", flush=True)

    ui = UiObserver(hub).start()
    mark = hb.log_mark()
    report: dict[str, Any] = {"hub": hub.version, "macro": [act, macro_key, macro_name], "interference": {}}
    try:
        main_ws, side = hb.HaWs(), hb.HaWs()

        def rename_macro(row: dict) -> None:
            for m in row.get("macros") or []:
                if int(m.get("button_id") or 0) == macro_key:
                    m["name"] = new_macro

        box: dict[str, Any] = {}
        t0 = time.time()
        worker = threading.Thread(target=lambda: box.setdefault(
            "O", _sync(main_ws, hub, "activity", act, rename_macro)))
        worker.start()
        time.sleep(1.0)
        report["backend_running_at_1s"] = _backend_running(side, hub)
        page = ui.pages["Activities"]
        report["passive_tab_lock_at_1s"] = lock_state(ui.read_page(page))

        # I1: reorder (an immediate write).
        res, secs = side.call({"type": f"{hb.DOMAIN}/activity/reorder", "entry_id": hub.entry_id,
                               "ordered_ids": list(reversed(order))})
        report["interference"]["I1_reorder"] = {"t": round(time.time() - t0, 2), "accepted": bool(res.get("success")),
                                                "error": res.get("error"), "seconds": round(secs, 2)}
        # I2: another activity's rename.
        report["interference"]["I2_activity_rename"] = {"t": round(time.time() - t0, 2), **_sync(
            side, hub, "activity", other, lambda r: r["device"].__setitem__("name", f"J {other_name}"[:30]))}
        # I3: a device rename.
        report["interference"]["I3_device_rename"] = {"t": round(time.time() - t0, 2), **_sync(
            side, hub, "device", plain, lambda r: r["device"].__setitem__("name", f"J {plain_name}"[:30]))}
        # The passive tab: click Edit on the other activity, see what opens.
        try:
            row = page.locator(f"{P} .entity-block", has=page.locator(".entity-name-label", has_text=other_name)).first
            btn = row.locator(".entity-meta button[title='Edit activity']")
            report["passive_edit_enabled"] = btn.is_enabled()
            if btn.is_enabled():
                btn.click()
                page.wait_for_timeout(2500)
                shot = hb.OUT / "journeys" / f"probe_midsync_{hub.version.lower()}_edit.png"
                page.screenshot(path=str(shot))
                guard = page.locator(f"{P} .guard-title, {P} .capture-error-title, {P} .detail-title").first
                report["passive_edit_shows"] = guard.inner_text().strip() if guard.count() else None
                back = page.locator(f"{P} sofabaton-edit-detail-view .back-btn")
                if back.count():
                    back.first.click()
        except Exception as err:  # noqa: BLE001
            report["passive_edit_error"] = str(err).splitlines()[0]

        worker.join(timeout=600)
        report["O_macro_rename"] = {**box.get("O", {}), "ended_after_s": round(time.time() - t0, 2)}

        # Settle, then judge against a fresh hub read.
        time.sleep(5)
        after = world.snapshot(hub=True)
        allowance = [f"cache/activities/{act}/macros/{macro_key}/name", f"hub/activities/{act}/macros/{macro_key}/name"]
        landed = []
        if report["interference"]["I1_reorder"]["accepted"]:
            landed.append("I1")
            allowance += ["cache/order/activities", "hub/order/activities"]
        if report["interference"]["I2_activity_rename"].get("status") == "success":
            landed.append("I2")
            allowance += [f"cache/activities/{other}/head/name", f"hub/activities/{other}/head/name"]
        if report["interference"]["I3_device_rename"].get("status") == "success":
            landed.append("I3")
            allowance += [f"cache/devices/{plain}/head/name", f"hub/devices/{plain}/head/name"]
        names = labeler(before.layers) | labeler(after.layers)
        frame = check({"cache": before.layers["hub"], "hub": before.layers["hub"]},
                      {"cache": after.layers["cache"], "hub": after.layers["hub"]}, allowance)
        report["changed"] = [c.render(names) for c in frame.allowed]
        report["unexpected"] = [c.render(names) for c in frame.findings]
        stale = check({"x": after.layers["cache"]}, {"x": after.layers["hub"]}, [], layers=("x",))
        report["cache_vs_hub"] = [c.render(names) for c in stale.findings]
        hub_macro = after.layers["hub"]["activities"][str(act)]["macros"].get(str(macro_key), {})
        report["macro_on_hub"] = hub_macro.get("name")
        report["macro_steps_unchanged"] = (hub_macro.get("steps") ==
                                           cache["activities"][str(act)]["macros"][str(macro_key)]["steps"])
        report["log_errors"] = [line.strip()[:240] for line in hb.errors_in(hb.log_since(mark))][:10]
        report["landed"] = landed
    finally:
        ui.close()
        # Undo what landed (and the macro), in reverse.
        try:
            undo = hb.HaWs()
            if report.get("O_macro_rename", {}).get("status") == "success" or report.get("macro_on_hub") == new_macro:
                def back(row: dict) -> None:
                    for m in row.get("macros") or []:
                        if int(m.get("button_id") or 0) == macro_key:
                            m["name"] = macro_name
                report["undo_macro"] = _sync(undo, hub, "activity", act, back).get("status")
            if "I3" in report.get("landed", []):
                report["undo_I3"] = _sync(undo, hub, "device", plain,
                                          lambda r: r["device"].__setitem__("name", plain_name)).get("status")
            if "I2" in report.get("landed", []):
                report["undo_I2"] = _sync(undo, hub, "activity", other,
                                          lambda r: r["device"].__setitem__("name", other_name)).get("status")
            if "I1" in report.get("landed", []):
                res, _ = undo.call({"type": f"{hb.DOMAIN}/activity/reorder", "entry_id": hub.entry_id,
                                    "ordered_ids": order})
                report["undo_I1"] = bool(res.get("success"))
        except Exception as err:  # noqa: BLE001
            report["undo_error"] = str(err)
        out = hb.OUT / "journeys" / f"probe_midsync_{hub.version.lower()}_{time.strftime('%Y%m%d_%H%M%S')}.json"
        out.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
        print(json.dumps({k: v for k, v in report.items() if k not in ("changed",)}, indent=1, default=str)[:6000])
        print(f"\nreport {out.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
