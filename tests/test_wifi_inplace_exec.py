"""Orchestration tests for the in-place wifi re-sync walker.

The four new wire drivers (``command_delete``, ``wifi_power_config``,
``membership_remove``, ``wifi_head_commit``) are gated by the live-hub
bench program (chunks 1–4). These tests verify the walker orchestration —
dispatch, ordering, ack-failure stop, the progress contract, and that each
new step kind resolves to a real driver method — against a fake proxy.
"""

from __future__ import annotations

import logging

from custom_components.sofabaton_x1s.lib.proxy_activity_sync import ActivitySyncMixin
from custom_components.sofabaton_x1s.lib.wifi_inplace_plan import (
    ManagedWifiSnapshot,
    WifiActivityRefs,
    WifiCommandSlot,
    build_wifi_inplace_plan,
)

DEV = 8


class FakeProxy(ActivitySyncMixin):
    def __init__(self, *, fail_kind: str | None = None) -> None:
        self._log = logging.getLogger("test.wifi_inplace_exec")
        self._fail_kind = fail_kind
        self.dispatched: list[str] = []
        self.can_issue = True

    def can_issue_commands(self) -> bool:
        return self.can_issue

    def _dispatch_activity_sync_step(self, step) -> bool:
        self.dispatched.append(step.kind)
        return step.kind != self._fail_kind


def _slot(cid: int, label: str) -> WifiCommandSlot:
    return WifiCommandSlot(command_id=cid, label=label, payload_key=f"slot:{cid}")


def _snap(**over) -> ManagedWifiSnapshot:
    base = dict(
        device_id=DEV,
        device_name="Wifi",
        brand="m3-key-a",
        power_on_command_id=1,
        power_off_command_id=2,
        slots={1: _slot(1, "One"), 2: _slot(2, "Two")},
        activities={0x65: WifiActivityRefs(0x65, member_count=3)},
    )
    base.update(over)
    return ManagedWifiSnapshot(**base)


def _multi_kind_plan():
    baseline = _snap(
        slots={1: _slot(1, "One"), 2: _slot(2, "Two"), 6: _slot(6, "Del")},
        activities={0x65: WifiActivityRefs(0x65, member_count=3), 0x66: WifiActivityRefs(0x66, member_count=4)},
    )
    desired = _snap(
        brand="m3-key-b",
        power_on_command_id=7,
        slots={1: _slot(1, "One"), 2: _slot(2, "Two"), 5: _slot(5, "Add")},
        activities={0x65: WifiActivityRefs(0x65, member_count=3)},
    )
    return build_wifi_inplace_plan(baseline, desired)


def test_walker_runs_all_steps_in_order():
    plan = _multi_kind_plan()
    proxy = FakeProxy()
    result = proxy.run_wifi_inplace_plan(plan)
    assert result["status"] == "success"
    assert result["completed_steps"] == len(plan.steps)
    assert proxy.dispatched == [s.kind for s in plan.steps]
    assert proxy.dispatched == [
        "command_add", "command_delete", "wifi_power_config",
        "membership_remove", "wifi_head_commit",
    ]
    assert result["counters"]["command_add"] == 1


def test_walker_stops_on_first_rejection_after_one_retry():
    plan = _multi_kind_plan()
    proxy = FakeProxy(fail_kind="wifi_power_config")
    result = proxy.run_wifi_inplace_plan(plan)
    assert result["status"] == "failed"
    assert result["failed_at"] == "wifi_power_config"
    # the failing step is retried once (idempotent rewrites); nothing after
    assert proxy.dispatched == [
        "command_add", "command_delete", "wifi_power_config", "wifi_power_config",
    ]
    assert "wifi_head_commit" not in proxy.dispatched


def test_walker_reports_progress():
    plan = _multi_kind_plan()
    proxy = FakeProxy()
    events: list[dict] = []
    proxy.run_wifi_inplace_plan(plan, progress_callback=lambda **d: events.append(d))
    assert events[0]["phase"] == "writing"
    assert events[-1]["phase"] == "completed"
    assert events[-1]["completed_steps"] == len(plan.steps)


def test_walker_refuses_when_hub_unavailable():
    proxy = FakeProxy()
    proxy.can_issue = False
    result = proxy.run_wifi_inplace_plan(_multi_kind_plan())
    assert result["status"] == "failed"
    assert result["failed_at"] == "unavailable"
    assert proxy.dispatched == []


def test_empty_plan_is_success_noop():
    proxy = FakeProxy()
    result = proxy.run_wifi_inplace_plan(build_wifi_inplace_plan(_snap(), _snap()))
    assert result["status"] == "success"
    assert result["completed_steps"] == 0
    assert proxy.dispatched == []


def test_new_step_kinds_resolve_to_drivers():
    for kind in (
        "command_delete",
        "wifi_power_config",
        "wifi_input_config",
        "membership_remove",
        "wifi_head_commit",
    ):
        assert callable(getattr(ActivitySyncMixin, f"_sync_step_{kind}", None)), kind


class _LiveFavProxy(FakeProxy):
    """Dispatch resolves favorites through the per-run live cache, the way
    ``_sync_step_favorite_delete`` does, against a hub map that can change
    between walks."""

    def __init__(self) -> None:
        super().__init__()
        self.live_map: dict[tuple[int, int], int] = {}
        self.live_reads = 0
        self.resolved: list[int | None] = []

    def _activity_sync_current_favorite_fav_ids(self, activity_id: int):
        self.live_reads += 1
        return dict(self.live_map)

    def _dispatch_activity_sync_step(self, step) -> bool:
        live = self._activity_sync_live_favorite_fav_ids(0x65)
        self.resolved.append(live.get((DEV, 2)))
        return super()._dispatch_activity_sync_step(step)


def test_walker_does_not_reuse_the_live_favorite_cache_across_runs():
    """CR-L4b-1: a second walk must re-read the hub's favorites, not resolve
    against the first walk's cached fav ids."""
    plan = _multi_kind_plan()
    proxy = _LiveFavProxy()

    proxy.live_map = {(DEV, 1): 6}
    assert proxy.run_wifi_inplace_plan(plan)["status"] == "success"
    assert proxy.live_reads == 1

    # Between the runs the hub's favorites changed (the first run's own
    # writes, or a vendor-app edit).
    proxy.live_map = {(DEV, 2): 7}
    proxy.resolved.clear()
    assert proxy.run_wifi_inplace_plan(plan)["status"] == "success"
    assert proxy.live_reads == 2
    assert set(proxy.resolved) == {7}
    # Run state never outlives the walk.
    assert proxy._activity_sync_live_fav_cache == {}


# ── clearing a power hook (real engine, wire stubbed) ────────────────────


def _power_engine(hub_version):
    from types import SimpleNamespace

    from custom_components.sofabaton_x1s.lib.x1_proxy import X1Proxy

    engine = X1Proxy("127.0.0.1", hub_version=hub_version, proxy_enabled=False)
    sent: list = []

    def _send_step(*, step_name, family, payload, **_kwargs):
        sent.append((step_name, family, bytes(payload)))
        return SimpleNamespace(ok=True)

    engine._send_step = _send_step
    return engine, sent


def test_a_cleared_power_hook_writes_the_unbound_row():
    from custom_components.sofabaton_x1s.lib.protocol_const import ButtonName

    engine, sent = _power_engine("X1S")
    payload = {"device_id": 7, "power_on_command_id": 3, "power_off_command_id": None,
               "clear_power_on": False, "clear_power_off": True}

    assert engine._sync_step_wifi_power_config(payload) is True

    assert [s[0] for s in sent] == ["wifi-power[dev=0x07 btn=0xC6]", "wifi-power[dev=0x07 btn=0xC7]"]
    assert all(family == 0x12 for _, family, _ in sent)
    unbound = engine._build_device_power_binding_payload(
        device_id=7, button_id=ButtonName.POWER_OFF, command_id=None
    )
    assert sent[1][2] == unbound  # the empty key sequence the create uses


def test_an_unflagged_none_hook_and_the_x1_write_no_clear():
    engine, sent = _power_engine("X1S")
    engine._sync_step_wifi_power_config(
        {"device_id": 7, "power_on_command_id": 3, "power_off_command_id": None}
    )
    assert [s[0] for s in sent] == ["wifi-power[dev=0x07 btn=0xC6]"]

    # The spec model ignores hooks on an X1, so its baseline can name a
    # command the desired side never does: never clear there.
    x1, x1_sent = _power_engine("X1")
    assert x1._sync_step_wifi_power_config(
        {"device_id": 7, "power_on_command_id": None, "power_off_command_id": None,
         "clear_power_on": True, "clear_power_off": True}
    ) is True
    assert x1_sent == []
