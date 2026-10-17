"""A committed catalog forgets what vanished entities left (journey bench W-8).

The hub reuses freed ids. On the X2, 2026-10-16, the hub deleted a sandbox
activity on its own when its last member went; the catalog re-read that
followed kept the activity's cached quick-access order, which survived in
the persisted cache, and the next activity created with that id showed it.
Every committed catalog now prunes, and a create forgets what the cache
held for the id it was handed.
"""

from __future__ import annotations

from custom_components.sofabaton_x1s.lib.entity_tables import forget_entity_detail
from custom_components.sofabaton_x1s.lib.hub_versions import HUB_VERSION_X1S
from custom_components.sofabaton_x1s.lib.x1_proxy import X1Proxy

DEV_KEPT, DEV_GONE = 0x05, 0x09
ACT_KEPT, ACT_GONE = 0x65, 0x6A


def _proxy() -> X1Proxy:
    proxy = X1Proxy("127.0.0.1", proxy_enabled=False, diag_dump=False, diag_parse=False, hub_version=HUB_VERSION_X1S)
    state = proxy.state
    state.devices = {DEV_KEPT: {"name": "TV"}, DEV_GONE: {"name": "Old Amp"}}
    state.activities = {ACT_KEPT: {"name": "Watch TV"}, ACT_GONE: {"name": "J Gate"}}
    state.commands = {DEV_KEPT: {1: "Power"}, DEV_GONE: {1: "Volume"}}
    state.device_key_sorts = {DEV_KEPT: {"device_id": DEV_KEPT, "msg_hex": "01"}, DEV_GONE: {"device_id": DEV_GONE, "msg_hex": "02"}}
    state.activity_favorites_order = {ACT_KEPT: [(1, 1)], ACT_GONE: [(1, 1)]}
    state.activity_favorite_slots = {ACT_KEPT: [{"button_id": 1, "device_id": DEV_KEPT, "command_id": 1}],
                                     ACT_GONE: [{"button_id": 1, "device_id": DEV_GONE, "command_id": 1}]}
    # The macro table holds devices' power macros too.
    state.activity_macros = {DEV_KEPT: [{"command_id": 198}], ACT_KEPT: [{"command_id": 2}], ACT_GONE: [{"command_id": 17}]}
    return proxy


def _commit_activities(proxy: X1Proxy, ids: list[int]) -> None:
    proxy._activity_pending_rows = {
        idx: {"id": act, "name": f"A{act}", "active": False, "needs_confirm": False}
        for idx, act in enumerate(ids, start=1)
    }
    proxy._activity_pending_payloads = {}
    proxy._commit_pending_activity_snapshot()


def _commit_devices(proxy: X1Proxy, ids: list[int]) -> None:
    proxy._device_pending_rows = {
        idx: {"id": dev, "name": f"D{dev}", "brand": "", "device_class": "ir", "device_class_code": 0x0D}
        for idx, dev in enumerate(ids, start=1)
    }
    proxy._commit_pending_device_snapshot()


def test_an_activity_the_catalog_no_longer_lists_leaves_nothing_behind() -> None:
    proxy = _proxy()

    _commit_activities(proxy, [ACT_KEPT])

    state = proxy.state
    assert ACT_GONE not in state.activity_favorites_order
    assert ACT_GONE not in state.activity_favorite_slots
    assert ACT_GONE not in state.activity_macros
    # The listed activity and the devices keep theirs, the device's power
    # macros included (one table holds both kinds).
    assert state.activity_favorites_order[ACT_KEPT] == [(1, 1)]
    assert DEV_KEPT in state.activity_macros and DEV_GONE in state.commands


def test_detail_without_a_catalog_row_is_pruned_too() -> None:
    # A leftover the persisted cache brought back: no catalog row before
    # the read either.
    proxy = _proxy()
    proxy.state.activities = {ACT_KEPT: {"name": "Watch TV"}}

    _commit_activities(proxy, [ACT_KEPT])

    assert ACT_GONE not in proxy.state.activity_favorites_order


def test_a_device_the_catalog_no_longer_lists_leaves_nothing_behind() -> None:
    proxy = _proxy()

    _commit_devices(proxy, [DEV_KEPT])

    state = proxy.state
    assert DEV_GONE not in state.commands
    assert DEV_GONE not in state.device_key_sorts
    assert state.commands[DEV_KEPT] == {1: "Power"}
    # Activities are another kind: untouched.
    assert ACT_GONE in state.activity_favorites_order


def test_an_unchanged_catalog_prunes_nothing() -> None:
    proxy = _proxy()

    _commit_activities(proxy, [ACT_KEPT, ACT_GONE])
    _commit_devices(proxy, [DEV_KEPT, DEV_GONE])

    assert set(proxy.state.activity_favorites_order) == {ACT_KEPT, ACT_GONE}
    assert set(proxy.state.commands) == {DEV_KEPT, DEV_GONE}


def test_a_reused_id_starts_without_the_old_detail_but_keeps_its_row() -> None:
    proxy = _proxy()

    forget_entity_detail(proxy, ACT_GONE, kind="activity")

    state = proxy.state
    assert ACT_GONE in state.activities
    assert ACT_GONE not in state.activity_favorites_order
    assert ACT_GONE not in state.activity_favorite_slots
    assert ACT_GONE not in state.activity_macros
