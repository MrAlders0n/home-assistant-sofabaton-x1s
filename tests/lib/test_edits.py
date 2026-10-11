"""The pure edit helpers (lib/edits.py) produce exactly the plan steps the
sync planners emit for the intended change, and nothing else."""

from __future__ import annotations

import copy
import importlib
import importlib.util
import sys
import types
from pathlib import Path

import pytest

LIB_DIR = (
    Path(__file__).resolve().parents[2]
    / "custom_components"
    / "sofabaton_x1s"
    / "lib"
)


def _load_lib() -> types.ModuleType:
    name = "sofabaton_edits_test_pkg"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name, LIB_DIR / "__init__.py", submodule_search_locations=[str(LIB_DIR)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_pkg = _load_lib()
edits = importlib.import_module(f"{_pkg.__name__}.edits")
activity_sync = importlib.import_module(f"{_pkg.__name__}.activity_sync")

POWER_ON = int(_pkg.ButtonName.POWER_ON)
VOL_UP = int(_pkg.ButtonName.VOL_UP)


def _device(dev_id: int, name: str, commands: dict[int, str]) -> dict:
    return {
        "kind": "device_backup",
        "complete": True,
        "payload_profile": "structural",
        "device": {"device_id": dev_id, "name": name, "brand": "Acme",
                   "device_class": "tv", "idle_behavior": 0},
        "commands": [{"command_id": cid, "name": label} for cid, label in commands.items()],
        "key_sort": None,
        "input_record": None,
        "button_bindings": [],
        "macros": [],
    }


def _bundle() -> dict:
    return {
        "kind": "hub_bundle",
        "complete": True,
        "payload_profile": "structural",
        "hub": {"name": "Den"},
        "devices": [_device(5, "TV", {1: "Power", 2: "Mute"}), _device(7, "Amp", {3: "Vol+"})],
        "activities": [
            {
                "kind": "activity_backup",
                "complete": True,
                "device": {"device_id": 101, "name": "Watch TV", "entity_type": "activity"},
                "button_bindings": [
                    {"button_id": POWER_ON, "button_name": "POWER_ON", "device_id": 5, "command_id": 1,
                     "long_press_device_id": None, "long_press_command_id": None},
                ],
                "favorite_slots": [
                    {"button_id": 1, "device_id": 5, "command_id": 2},
                    {"button_id": 2, "device_id": 7, "command_id": 3},
                ],
                "favorites_order": [1, 2],
                "macros": [],
                "referenced_source_device_ids": [5, 7],
            }
        ],
    }


def _activity_plan(baseline, edited):
    return [(s.kind, s.payload) for s in activity_sync.build_activity_sync_plan(baseline, edited, 101)]


def _device_plan(baseline, edited, dev):
    return [(s.kind, s.payload) for s in activity_sync.build_device_sync_plan(baseline, edited, dev)]


def test_helpers_never_mutate_the_input() -> None:
    base = _bundle()
    before = copy.deepcopy(base)
    edits.rename_activity(base, 101, "Movie")
    edits.bind_button(base, 101, VOL_UP, 7, 3)
    edits.add_favorite(base, 101, 5, 1)
    edits.rename_command(base, 5, 1, "Standby")
    assert base == before


def test_rename_activity_plans_one_rename() -> None:
    base = _bundle()
    edited = edits.rename_activity(base, 101, " Movie ")
    assert _activity_plan(base, edited) == [
        ("activity_rename", {"activity_id": 101, "name": "Movie"}),
        ("remote_sync", {"activity_id": 101}),
    ]
    with pytest.raises(ValueError):
        edits.rename_activity(base, 101, "  ")
    with pytest.raises(KeyError):
        edits.rename_activity(base, 102, "x")


def test_bind_and_clear_button_plan_binding_steps() -> None:
    base = _bundle()
    edited = edits.bind_button(base, 101, VOL_UP, 7, 3, long_press=(5, 2))
    plan = _activity_plan(base, edited)
    assert plan[0] == ("binding_write", {
        "activity_id": 101, "button_id": VOL_UP, "device_id": 7, "command_id": 3,
        "long_press_device_id": 5, "long_press_command_id": 2,
    })
    assert [k for k, _ in plan] == ["binding_write", "remote_sync"]
    # Rebinding an existing button replaces it, clearing removes it.
    rebound = edits.bind_button(base, 101, POWER_ON, 7, 3)
    assert _activity_plan(base, rebound)[0][1]["device_id"] == 7
    cleared = edits.clear_button(base, 101, POWER_ON)
    assert _activity_plan(base, cleared)[0] == ("binding_delete", {"activity_id": 101, "button_id": POWER_ON})
    assert edits.clear_button(base, 101, VOL_UP) == base  # nothing bound: no change


def test_favorites_add_remove_reorder() -> None:
    base = _bundle()
    added = edits.add_favorite(base, 101, 5, 1, name="Power")
    plan = _activity_plan(base, added)
    assert plan[0] == ("favorite_add", {"activity_id": 101, "device_id": 5, "command_id": 1, "name": "Power"})
    assert [k for k, _ in plan] == ["favorite_add", "remote_sync"]
    with pytest.raises(ValueError):
        edits.add_favorite(base, 101, 5, 2)  # already there

    removed = edits.remove_favorite(base, 101, 5, 2)
    plan = _activity_plan(base, removed)
    assert plan[0] == ("favorite_delete", {"activity_id": 101, "button_id": 1, "device_id": 5, "command_id": 2})
    assert [k for k, _ in plan] == ["favorite_delete", "remote_sync"]
    with pytest.raises(ValueError):
        edits.remove_favorite(base, 101, 5, 1)

    reordered = edits.reorder_favorites(base, 101, [(7, 3), (5, 2)])
    plan = _activity_plan(base, reordered)
    assert plan[0] == ("favorite_order", {"activity_id": 101, "order": [
        {"kind": "favorite", "device_id": 7, "command_id": 3},
        {"kind": "favorite", "device_id": 5, "command_id": 2},
    ]})
    assert edits.reorder_favorites(base, 101, [(5, 2), (7, 3)]) == base
    with pytest.raises(ValueError):
        edits.reorder_favorites(base, 101, [(5, 2)])


def test_device_rename_command_rename_and_idle() -> None:
    base = _bundle()
    renamed = edits.rename_device(base, 5, "Big TV", brand="Sony")
    assert _device_plan(base, renamed, 5) == [
        ("device_rename", {"device_id": 5, "name": "Big TV", "brand": "Sony"}),
    ]
    command = edits.rename_command(base, 5, 1, "Standby")
    assert _device_plan(base, command, 5) == [
        ("command_rename", {"device_id": 5, "command_id": 1, "name": "Standby"}),
    ]
    with pytest.raises(KeyError):
        edits.rename_command(base, 5, 9, "x")
    idle = edits.set_idle_behavior(base, 5, 2)
    assert _device_plan(base, idle, 5) == [("idle_behavior", {"device_id": 5, "mode": 2})]
    with pytest.raises(ValueError):
        edits.set_idle_behavior(base, 5, 300)


def test_edits_stay_in_scope_for_the_planner() -> None:
    # An activity edit leaves the devices byte-identical, and vice versa,
    # so the planners' scope guards accept every helper's output.
    base = _bundle()
    edited = edits.bind_button(edits.add_favorite(base, 101, 5, 1), 101, VOL_UP, 7, 3)
    activity_sync.build_activity_sync_plan(base, edited, 101)
    edited = edits.set_idle_behavior(edits.rename_device(base, 5, "New"), 5, 1)
    activity_sync.build_device_sync_plan(base, edited, 5)


def test_payload_overwrite_and_add_command_plan_the_record_writes() -> None:
    base = _bundle()
    raw = _pkg.IrPayload.from_raw_timings([9000, 4500, 560, 560], 38000)
    edited = edits.set_command_payload(base, 5, 2, raw)
    plan = _device_plan(base, edited, 5)
    assert [k for k, _ in plan] == ["command_payload"]
    assert plan[0][1]["command_id"] == 2 and plan[0][1]["restore_data"]["data_hex"] == raw.blob.hex()
    with pytest.raises(KeyError):
        edits.set_command_payload(base, 5, 9, raw)

    desc = _pkg.IrPayload.from_descriptor("P:NEC1 D:4 S:5 F:21")
    added, slot = edits.add_command(base, 5, desc, "Input")
    assert slot == 3
    plan = _device_plan(base, added, 5)
    assert [k for k, _ in plan] == ["command_add"]
    assert plan[0][1]["command_id"] == 3 and plan[0][1]["command_name"] == "Input"
    assert plan[0][1]["restore_data"]["decoded"]["fields"]["descriptor"] == "P:NEC1 D:4 S:5 F:21"
    _, explicit = edits.add_command(base, 5, desc, "Input", command_id=40)
    assert explicit == 40
    with pytest.raises(ValueError):
        edits.add_command(base, 5, desc, "Input", command_id=1)


# ---------------------------------------------------------------------------
# Quick-access ids, macros and membership (services migration, S0)
# ---------------------------------------------------------------------------


def _with_macro_and_power(bundle: dict) -> dict:
    """Watch TV with a user macro (quick-access id 3, bound to VOL_UP) and
    power sequences naming its two members, as a hub read returns them."""

    activity = bundle["activities"][0]
    activity["macros"] = [
        {"button_id": 3, "name": "Movie", "steps": [
            {"device_id": 5, "command_id": 1, "button_code": 0, "duration": 0, "delay": 0}]},
        {"button_id": 198, "name": "POWER_ON", "steps": [
            {"device_id": 5, "command_id": 0xC6, "button_code": 0, "duration": 0, "delay": 0xFF},
            {"device_id": 5, "command_id": 0xC5, "button_code": 0, "duration": 0, "delay": 0xFF},
            {"device_id": 0xFF, "command_id": 0xFF, "button_code": 0, "duration": 0, "delay": 3},
            {"device_id": 7, "command_id": 0xC6, "button_code": 0, "duration": 0, "delay": 0xFF},
            {"device_id": 7, "command_id": 0xC5, "button_code": 0, "duration": 0, "delay": 0xFF},
        ]},
        {"button_id": 199, "name": "POWER_OFF", "steps": [
            {"device_id": 5, "command_id": 0xC7, "button_code": 0, "duration": 0, "delay": 0xFF},
            {"device_id": 7, "command_id": 0xC7, "button_code": 0, "duration": 0, "delay": 0xFF},
        ]},
    ]
    activity["favorites_order"] = [1, 2, 3]
    activity["button_bindings"].append(
        {"button_id": VOL_UP, "button_name": "VOL_UP", "device_id": 101, "command_id": 3,
         "long_press_device_id": None, "long_press_command_id": None}
    )
    return bundle


def test_reorder_by_quick_access_ids_interleaves_macros() -> None:
    base = _with_macro_and_power(_bundle())
    reordered = edits.reorder_favorites(base, 101, [3, 2, 1])
    assert reordered["activities"][0]["favorites_order"] == [3, 2, 1]
    plan = _activity_plan(base, reordered)
    assert plan[0] == ("favorite_order", {"activity_id": 101, "order": [
        {"kind": "macro", "button_id": 3},
        {"kind": "favorite", "device_id": 7, "command_id": 3},
        {"kind": "favorite", "device_id": 5, "command_id": 2},
    ]})
    assert edits.reorder_favorites(base, 101, [1, 2, 3]) == base
    for bad in ([1, 2], [1, 2, 3, 3], [1, 2, 3, 198], [1, 2, 9]):
        with pytest.raises(ValueError):
            edits.reorder_favorites(base, 101, bad)


def test_remove_macro_cascades_its_binding() -> None:
    base = _with_macro_and_power(_bundle())
    removed = edits.remove_macro(base, 101, 3)
    activity = removed["activities"][0]
    assert [row["button_id"] for row in activity["macros"]] == [198, 199]
    assert activity["favorites_order"] == [1, 2]
    assert all(row["button_id"] != VOL_UP for row in activity["button_bindings"])
    kinds = [kind for kind, _ in _activity_plan(base, removed)]
    assert "macro_delete" in kinds and "binding_delete" in kinds

    for bad in (198, 199, 9):
        with pytest.raises(ValueError):
            edits.remove_macro(base, 101, bad)


def test_remove_macro_clears_a_long_press_that_named_it() -> None:
    base = _with_macro_and_power(_bundle())
    base["activities"][0]["button_bindings"][0].update(long_press_device_id=101, long_press_command_id=3)
    removed = edits.remove_macro(base, 101, 3)
    power = next(r for r in removed["activities"][0]["button_bindings"] if r["button_id"] == POWER_ON)
    assert (power["device_id"], power["command_id"]) == (5, 1)
    assert power["long_press_device_id"] is None and power["long_press_command_id"] is None


def test_add_member_writes_the_power_references_in_member_order() -> None:
    base = _with_macro_and_power(_bundle())
    base["devices"].append(_device(6, "Lamp", {1: "On", 2: "HDMI 2"}))
    base["devices"][-1]["input_record"] = {"entries": [{"input_index": 1, "command_id": 2}]}

    added = edits.add_member(base, 101, 6, input_command_id=2)
    activity = added["activities"][0]
    power_on = next(r for r in activity["macros"] if r["button_id"] == 198)["steps"]
    # Device 6 goes between 5 (its group keeps its trailing wait) and 7.
    assert [(s["device_id"], s["command_id"]) for s in power_on] == [
        (5, 0xC6), (5, 0xC5), (0xFF, 0xFF), (6, 0xC6), (6, 0xC5), (7, 0xC6), (7, 0xC5),
    ]
    assert power_on[4]["duration"] == 1  # the input's ordinal in the device's input record
    power_off = next(r for r in activity["macros"] if r["button_id"] == 199)["steps"]
    assert [(s["device_id"], s["command_id"]) for s in power_off] == [(5, 0xC7), (6, 0xC7), (7, 0xC7)]
    assert activity["referenced_source_device_ids"] == [5, 6, 7]
    plan = _activity_plan(base, added)
    member = [payload for kind, payload in plan if kind == "member_replay"]
    assert member == [{"activity_id": 101, "device_id": 6, "input_cmd_id": 2}]

    assert edits.add_member(base, 101, 5) == base  # already a member, nothing to change
    with pytest.raises(ValueError):
        edits.add_member(base, 101, 6, input_command_id=1)  # not one of its inputs
    with pytest.raises(KeyError):
        edits.add_member(base, 101, 9)  # not in the bundle
    with pytest.raises(ValueError):
        edits.add_member(base, 101, 102)  # an activity id


def test_add_member_to_an_activity_without_power_sequences_creates_them() -> None:
    base = _bundle()
    base["devices"].append(_device(6, "Lamp", {1: "On"}))
    added = edits.add_member(base, 101, 6)
    macros = {row["button_id"]: row for row in added["activities"][0]["macros"]}
    assert macros[198]["name"] == "POWER_ON" and macros[199]["name"] == "POWER_OFF"
    assert [(s["device_id"], s["command_id"], s["duration"]) for s in macros[198]["steps"]] == [
        (6, 0xC6, 0), (6, 0xC5, 0),
    ]


def test_add_member_on_a_member_changes_the_input_it_selects() -> None:
    base = _with_macro_and_power(_bundle())
    base["devices"][1]["input_record"] = {"entries": [
        {"input_index": 1, "command_id": 3}, {"input_index": 2, "command_id": 9}]}
    changed = edits.add_member(base, 101, 7, input_command_id=9)
    power_on = next(r for r in changed["activities"][0]["macros"] if r["button_id"] == 198)["steps"]
    assert [(s["device_id"], s["command_id"], s["duration"]) for s in power_on if s["device_id"] == 7] == [
        (7, 0xC6, 0), (7, 0xC5, 2),
    ]
    plan = _activity_plan(base, changed)
    assert [kind for kind, _ in plan] == ["macro_write", "remote_sync"]
    assert base["activities"][0]["macros"][1]["steps"][4]["duration"] == 0  # input untouched
