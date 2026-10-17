"""J1: the generalized #306 journey, driven through the UI (plan 4.3).

Two Wifi devices, the same hard button (Vol +), different activities, then
the same activity:

1. create "J Amp"; slot 1 "J Vol Up" on Vol + in activity X; sync
2. create "J Soundbar"; slot 1 on Vol + in activity Y. Saving it must not
   touch J Amp's record (the #306 check, HA layer, before any sync); sync.
   X's Vol + must still run J Amp (the intent from step 1 holds).
3. J Soundbar's slot adds X: the takeover. J Amp's slot loses the button in
   X; X was its only activity, so it loses the button outright. Sync both;
   both devices must read "Synced" afterwards.
4. delete both; the hub must be back where it started.

X and Y are the first two activities (display order) that have Vol + free,
so the journey never overwrites an existing binding.
"""

from __future__ import annotations

from typing import Iterator

from ..actions import Ctx, Spec, on_hub
from ..actions import wifi_steps as wifi
from ..actions.ws import observe, put_binding_row, same_as_start

NEEDS_ACTOR = True
#: Tried in order: the first button free in two activities is used.
BUTTONS = ("volup", "red", "green", "yellow", "blue")


def journey(ctx: Ctx) -> Iterator[Spec]:
    cache = ctx.snapshot.before()["cache"]
    ctx.known["start_hub"] = ctx.snapshot.before()["cache"]
    for button in BUTTONS:
        button_id = wifi.BUTTON_IDS[button]
        free = [a for a in cache["order"]["activities"]
                if str(button_id) not in cache["activities"][str(a)]["bindings"]]
        if len(free) >= 2:
            break
    else:
        # A dense hub: free the last button in the first two activities
        # first (WS editor path), and put the rows back at the end.
        button = BUTTONS[-1]
        button_id = wifi.BUTTON_IDS[button]
        free = list(cache["order"]["activities"][:2])
    BUTTON, BUTTON_ID = button, button_id
    x_id, y_id = free[0], free[1]
    raw = {int(r["device"]["device_id"]): r for r in ctx.bundle().get("activities") or []}
    originals = {a: next((dict(b) for b in raw[a].get("button_bindings") or []
                          if int(b.get("button_id") or 0) == BUTTON_ID), None) for a in (x_id, y_id)}
    occupied = {a: row for a, row in originals.items() if row is not None}
    x = cache["activities"][str(x_id)]["head"]["name"]
    y = cache["activities"][str(y_id)]["head"]["name"]
    print(f"   J1: X = {x_id} {x!r}, Y = {y_id} {y!r}, button {BUTTON} ({BUTTON_ID})", flush=True)

    yield observe()
    for a in occupied:
        yield put_binding_row(a, BUTTON_ID, None)

    yield wifi.create("J Amp")
    yield wifi.slot(ctx, "J Amp", 0, name="J Vol Up", hard_button=BUTTON, activities=[x])
    yield wifi.sync(ctx, "J Amp", [{"index": 0, "hard_button": BUTTON, "activities": [x]}])

    yield wifi.create("J Soundbar")
    # The #306 check: J Amp's record is outside this step's allowance.
    yield wifi.slot(ctx, "J Soundbar", 0, name="J Vol Up", hard_button=BUTTON, activities=[y])
    yield wifi.sync(ctx, "J Soundbar", [{"index": 0, "hard_button": BUTTON, "activities": [y]}])

    # The takeover in the shared activity X: J Amp's slot may change, and
    # only its button and activities (plus its hash and sync state).
    amp = ctx.known["wifi:J Amp"]
    yield wifi.slot(ctx, "J Soundbar", 0, name="J Vol Up", hard_button=BUTTON, activities=[x, y],
                    also_allow=[f"ha/wifi/{amp}/slots/0/hard_button", f"ha/wifi/{amp}/slots/0/activities",
                                f"ha/wifi/{amp}/slots/0/long_press_enabled", f"ha/wifi/{amp}/commands_hash",
                                f"ha/wifi/{amp}/sync_needed", f"ha/wifi/{amp}/configured_slot_count"],
                    # The #306 rule: X was J Amp's only activity, so its slot
                    # loses the button outright (and its activities with it).
                    also_expect=[(f"wifi/{amp}/slots/0/hard_button", "", "takeover: J Amp loses Vol +", ("ha",)),
                                 (f"wifi/{amp}/slots/0/activities", [], "takeover: J Amp keeps no activity", ("ha",)),
                                 (f"wifi/{amp}/sync_needed", True, "takeover: J Amp needs a sync", ("ha",))])
    yield wifi.sync(ctx, "J Soundbar", [{"index": 0, "hard_button": BUTTON, "activities": [x, y]}])
    amp_dev = ctx.known.get("wifidev:J Amp")

    def amp_cleaned(ctx: Ctx, after: dict) -> list[str]:
        """J Amp's re-sync drops what it no longer owns, and nothing else:
        its membership in X and its own Vol + row go; X's Vol + stays
        J Soundbar's (checked by the intent from the J Soundbar sync)."""

        problems = []
        if amp_dev is None:
            return ["J Amp was never deployed"]
        if amp_dev in after["cache"]["activities"][str(x_id)]["members"]:
            problems.append(f"J Amp ({amp_dev}) is still a member of {x!r}")
        page = (after["cache"]["devices"].get(str(amp_dev)) or {}).get("bindings") or {}
        if str(BUTTON_ID) in page:
            problems.append(f"J Amp's own page still binds {BUTTON_ID}: {page[str(BUTTON_ID)]}")
        return problems

    yield wifi.sync(ctx, "J Amp", [], extra_activities=[x], post=amp_cleaned)
    yield wifi.pills(["J Amp", "J Soundbar"])

    yield wifi.delete(ctx, "J Soundbar", activities=[x, y], gone_bindings=[(x, BUTTON_ID), (y, BUTTON_ID)])
    yield wifi.delete(ctx, "J Amp", activities=[x])
    for a, row in occupied.items():
        yield put_binding_row(a, BUTTON_ID, row)
    yield observe()
    yield same_as_start()
