from journeys.frame import allowed
from journeys.normalize import (
    generation_of,
    normalize_bundle,
    normalize_entities,
    normalize_persisted,
    project_like_persisted,
)
from journeys.uistate import LockTimeline, expected_view, lock_state, view_problems


def _ui(disabled: bool | None, names=("Watch TV", "Play"), ids=(102, 101)):
    controls = [{"label": "Hub", "disabled": False}, {"label": "Activities 2", "disabled": False}]
    if disabled is not None:
        controls += [{"label": "Edit activity", "disabled": disabled},
                     {"label": "Change order", "disabled": disabled},
                     {"label": "Add activity", "disabled": disabled}]
    return {"tab": "Hub", "sub": "Activities", "counts": {"Activities": 2, "Devices": 1},
            "entities": [{"name": n, "id": i} for n, i in zip(names, ids)], "controls": controls}


def test_lock_state_reads_only_write_controls():
    assert lock_state(_ui(True)) == "locked"
    assert lock_state(_ui(False)) == "unlocked"
    partial = _ui(False)
    partial["controls"][2]["disabled"] = True
    assert lock_state(partial) == "partial"
    assert lock_state(None) == "absent"
    assert lock_state(_ui(None)) == "none"


def _world():
    return {
        "activities": {"101": {"head": {"name": "Play"}}, "102": {"head": {"name": "Watch TV"}}},
        "devices": {"4": {"head": {"name": "Xbox"}}},
        "order": {"activities": [102, 101], "devices": [4]},
    }


def test_the_shown_list_must_match_the_cache_in_order():
    assert view_problems(_ui(False), expected_view(_world(), "Activities")) == []
    stale = _ui(False, names=("Watch TV", "Old name"))
    assert view_problems(stale, expected_view(_world(), "Activities"))
    wrong_order = _ui(False, names=("Play", "Watch TV"), ids=(101, 102))
    assert view_problems(wrong_order, expected_view(_world(), "Activities"))


def _timeline(states, running=(0.5, 3.0), end=3.2, step=0.25):
    tl = LockTimeline(action_end=end)
    t = 0.0
    for state in states:
        backend = "running" if running[0] <= t <= running[1] else "idle"
        tl.add(t, backend, {"Activities": state}, {"Activities": None})
        t += step
    return tl


def test_a_tab_that_locks_and_unlocks_is_clean():
    states = ["unlocked"] * 4 + ["locked"] * 10 + ["unlocked"] * 4
    report, problems = _timeline(states).analyze()
    assert problems == []
    page = report["pages"]["Activities"]
    # Busy 0.5..3.0 s, locked 1.0..3.25 s, unlocked at 3.5 s: one sample after idle.
    assert page["first_locked"] == 1.0 and page["lock_lag"] == 0.5 and page["unlock_lag"] == 0.25


def test_never_locked_and_stuck_locks_are_findings():
    _r, never = _timeline(["unlocked"] * 18).analyze()
    assert any("never locked" in p for p in never)
    _r, stuck = _timeline(["unlocked"] * 4 + ["locked"] * 14).analyze()
    assert any("stuck" in p for p in stuck)
    _r, partly = _timeline(["unlocked"] * 4 + ["partial"] * 10 + ["unlocked"] * 4).analyze()
    assert any("partly locked" in p for p in partly)


def test_a_short_operation_that_no_poll_saw_is_not_a_finding():
    _r, problems = _timeline(["unlocked"] * 8, running=(0.5, 1.0)).analyze()
    assert problems == []


def test_entities_keep_config_and_drop_pulses():
    states = [
        {"entity_id": "remote.r", "state": "on", "attributes": {"cache_generation": 7, "activity_list": ["A"]}},
        {"entity_id": "button.ok", "state": "2026-10-16T10:00:00", "attributes": {}},
        {"entity_id": "button.red", "state": "unavailable", "attributes": {}},
        {"entity_id": "sensor.x_wifi_commands", "state": "pulse", "attributes": {}},
        {"entity_id": "sensor.x_ir_intercept", "state": "idle", "attributes": {"recent": [1]}},
        {"entity_id": "light.other", "state": "on", "attributes": {}},
    ]
    ids = {"remote.r", "button.ok", "button.red", "sensor.x_wifi_commands", "sensor.x_ir_intercept"}
    out = normalize_entities(states, ids)
    assert out == {"remote.r": {"state": "on", "attrs": {"activity_list": ["A"]}},
                   "button.ok": {"state": "available", "attrs": {}},
                   "button.red": {"state": "unavailable", "attrs": {}},
                   "sensor.x_ir_intercept": {"state": "idle", "attrs": {}}}
    assert generation_of(states, "remote.r") == 7


def test_persisted_and_cache_project_to_the_same_paths():
    bundle = {"devices": [{"device": {"device_id": 4, "name": "Xbox"},
                           "commands": [{"command_id": 13, "name": "Ok"}],
                           "key_sort": {"msg_hex": None},
                           "button_bindings": [{"button_id": 176, "button_name": "OK", "device_id": None,
                                                "command_id": 13}]}],
              "activities": [{"device": {"device_id": 101, "name": "Play"},
                              "button_bindings": [{"button_id": 176, "device_id": 4, "command_id": 13}],
                              "favorite_slots": [{"button_id": 2, "device_id": 4, "command_id": 13}]}]}
    entry = {"devices": {"4": {"name": "Xbox"}}, "activities": {"101": {"name": "Play"}},
             "commands": {"4": {"13": "Ok"}}, "device_key_sorts": {"4": {"msg_hex": ""}},
             "button_details": {"4": {"176": {"device_id": 4, "command_id": 13}},
                                "101": {"176": {"device_id": 4, "command_id": 13}}},
             "activity_favorite_slots": {"101": [{"button_id": 2, "device_id": 4, "command_id": 13,
                                                  "source": "keymap"}]}}
    assert project_like_persisted(normalize_bundle(bundle)) == normalize_persisted(entry)


def test_segment_globs_match_entity_ids():
    path = ("entities", "select.souterrain_hub_activity", "attrs", "options")
    assert allowed(path, ["entities/select.*_activity/attrs/options"])
    assert not allowed(path, ["entities/select.*_version/attrs/options"])


def test_stale_lists_say_whether_the_old_banner_was_shown():
    stale = _ui(False, names=("Watch TV", "Old name"))
    pending = view_problems(stale, expected_view(_world(), "Activities"))
    assert pending and pending[0].startswith("not followed yet")
    stale["staleBanner"] = True
    flagged = view_problems(stale, expected_view(_world(), "Activities"))
    assert flagged and flagged[0].startswith("STALE BANNER")
