from journeys.normalize import display_order, labeler, normalize_bundle, normalize_ha


def _bundle(**overrides):
    activity = {
        "kind": "activity_backup", "captured_at": "t1", "fetched_at": "t2", "editable": True,
        "device": {"device_id": 101, "name": "Watch TV", "sort": 2, "power_state": 1, "icon": 3},
        "button_bindings": [
            {"button_id": 176, "button_name": "OK", "device_id": 4, "command_id": 13,
             "long_press_device_id": None, "long_press_command_id": None},
            # A no-op row (app residue) is dropped.
            {"button_id": 177, "button_name": "RIGHT", "device_id": 0, "command_id": 0,
             "long_press_device_id": None, "long_press_command_id": None},
        ],
        "favorite_slots": [{"button_id": 2, "device_id": 6, "command_id": 1},
                           {"button_id": 3, "device_id": 6, "command_id": 13}],
        "macros": [
            {"button_id": 1, "name": "combo", "steps": [
                {"device_id": 6, "command_id": 1, "button_code": 0x4E21, "duration": 0, "delay": 255}]},
            {"button_id": 198, "name": "POWER_ON", "steps": [
                {"device_id": 4, "command_id": 198, "button_code": 0, "duration": 7, "delay": 255},
                {"device_id": 4, "command_id": 197, "button_code": 0, "duration": 255, "delay": 255}]},
        ],
        "referenced_source_device_ids": [6, 4],
        "favorites_order": [3, 1, 2],
    }
    device = {
        "kind": "device_backup", "captured_at": "t1",
        "device": {"device_id": 4, "name": "Xbox", "sort": 1, "power_state": 0, "device_class": "ir"},
        "commands": [{"command_id": 13, "name": "OK", "restore_data": {"data_hex": "AA", "decoded": {"x": 1}}}],
        "key_sort": {"device_id": 4, "msg_hex": "01 02"},
        "input_record": {"device_id": 4, "entries": []},
        "button_bindings": [],
        "macros": [],
    }
    bundle = {"kind": "hub_bundle", "devices": [device], "activities": [activity]}
    bundle.update(overrides)
    return bundle


def test_ids_are_keys_and_volatile_fields_are_dropped():
    world = normalize_bundle(_bundle())
    act = world["activities"]["101"]
    assert act["head"] == {"name": "Watch TV", "icon": 3}
    assert act["members"] == [4, 6]
    assert act["bindings"] == {"176": {"button": "OK", "short": "4/13", "long": None}}
    assert "extra" not in act


def test_shortcuts_follow_the_order_table_and_mark_macros():
    act = normalize_bundle(_bundle())["activities"]["101"]
    assert act["shortcuts"] == ["fav:6/13", "macro:1", "fav:6/1"]
    assert act["favorites"] == {"2": "6/1", "3": "6/13"}


def test_shortcuts_flag_collisions_dangling_and_unordered():
    bundle = _bundle()
    act = bundle["activities"][0]
    act["favorite_slots"].append({"button_id": 1, "device_id": 6, "command_id": 5})
    act["favorites_order"] = [1, 9, 3]
    shortcuts = normalize_bundle(bundle)["activities"]["101"]["shortcuts"]
    assert shortcuts == ["collision:1", "dangling:9", "fav:6/13", "unordered:fav:6/1"]


def test_macro_steps_drop_hub_owned_bytes():
    macros = normalize_bundle(_bundle())["activities"]["101"]["macros"]
    assert macros["1"]["steps"] == [{"ref": "6/1", "duration": 0, "delay": 255}]
    power = macros["198"]["steps"]
    assert power[0] == {"ref": "4/198", "delay": 255}          # power-ref duration is the hub's
    assert power[1] == {"ref": "4/197", "duration": 0, "delay": 255}  # 255 == no input == 0


def test_command_payloads_become_digests_without_decoded():
    a = normalize_bundle(_bundle())["devices"]["4"]["commands"]["13"]
    other = _bundle()
    other["devices"][0]["commands"][0]["restore_data"]["decoded"] = {"x": 2}
    b = normalize_bundle(other)["devices"]["4"]["commands"]["13"]
    assert a == b and a["name"] == "OK" and len(a["data"]) == 12


def test_missing_and_empty_key_sort_are_the_same():
    a, b = _bundle(), _bundle()
    a["devices"][0]["key_sort"] = {"device_id": 4, "msg_hex": None}
    b["devices"][0]["key_sort"] = {"device_id": 4, "msg_hex": ""}
    assert normalize_bundle(a) == normalize_bundle(b)


def test_display_order_puts_unsorted_rows_last_by_id():
    rows = [{"device": {"device_id": 9, "sort": 0}}, {"device": {"device_id": 3, "sort": 2}},
            {"device": {"device_id": 5, "sort": 1}}, {"device": {"device_id": 4, "sort": 0}}]
    assert display_order(rows) == [5, 3, 4, 9]


def test_unknown_row_fields_are_kept():
    bundle = _bundle()
    bundle["activities"][0]["new_field"] = [1]
    assert normalize_bundle(bundle)["activities"]["101"]["extra"] == {"new_field": [1]}


def test_ha_layer_drops_progress_and_keys_slots_by_index():
    ha = normalize_ha(
        {"devices": [{"device_key": "ab", "device_name": "Amp", "status": "running", "message": "x",
                      "current_step": 2, "activity_labels": {"101": "TV"}, "sync_needed": True,
                      "commands": [{"name": "VOL+", "hard_button": "VOL+", "activities": ["101"]}]}]},
        {"events": [{"slot_index": 0, "name": "Lights", "deployed": True}], "device_id": 12,
         "mqtt_available": False},
        {"actions": {}},
    )
    assert ha["wifi"]["ab"] == {"device_name": "Amp", "sync_needed": True,
                                "slots": {"0": {"name": "VOL+", "hard_button": "VOL+", "activities": ["101"]}}}
    assert ha["events"] == {"record": {"device_id": 12}, "slots": {"0": {"name": "Lights", "deployed": True}}}


def test_labeler_names_entities_and_commands():
    names = labeler({"cache": normalize_bundle(_bundle())})
    assert names["101"] == "Watch TV" and names["4/13"] == "Xbox/OK"
