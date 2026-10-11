import asyncio
from types import SimpleNamespace

import pytest

import importlib
from custom_components.sofabaton_x1s.lib.commands import build_descriptive_ir_blob_body

integration = importlib.import_module("custom_components.sofabaton_x1s.__init__")
runtime_module = importlib.import_module("custom_components.sofabaton_x1s.runtime")


class _FakeConfigEntries:
    def __init__(self, entry):
        self._entry = entry

    def async_get_entry(self, entry_id):
        if self._entry and self._entry.entry_id == entry_id:
            return self._entry
        return None


class _FakeHass:
    def __init__(self, entry):
        self.config_entries = _FakeConfigEntries(entry)


class _FakeCall:
    def __init__(self, data: dict, hass=None):
        self.data = data
        self.hass = hass if hass is not None else _FakeHass(None)


class _FakeHub:
    def __init__(self) -> None:
        self.entry_id = "entry-1"
        self.version = "X1"
        self.calls: list[dict] = []
        self.favorite_order_result: list[tuple[int, int]] | None = None
        self.favorite_descriptions: list[dict] | None = None
        self.bundle = _service_bundle()

    async def async_create_wifi_device(
        self,
        *,
        device_name: str,
        commands: list[str],
        request_port: int,
        power_on_command_id: int | None = None,
        power_off_command_id: int | None = None,
        input_command_ids: list[int] | None = None,
    ):
        payload = {
            "device_name": device_name,
            "commands": commands,
            "request_port": request_port,
        }
        if power_on_command_id is not None:
            payload["power_on_command_id"] = power_on_command_id
        if power_off_command_id is not None:
            payload["power_off_command_id"] = power_off_command_id
        if input_command_ids is not None:
            payload["input_command_ids"] = input_command_ids
        self.calls.append(payload)
        return payload

    async def async_delete_device(self, *, device_id: int):
        payload = {"device_id": device_id}
        self.calls.append(payload)
        return payload

    async def async_dump_ir_commands(
        self,
        *,
        device_id: int,
        command_id: int | None = None,
        wait_timeout: float = 10.0,
    ):
        payload = {
            "device_id": device_id,
            "requested_command_id": command_id,
            "wait_timeout": wait_timeout,
            "commands": [],
            "complete": False,
        }
        self.calls.append(payload)
        return payload

    async def async_fetch_blob(
        self,
        *,
        device_id: int,
        command_id: int | None = None,
        wait_timeout: float = 10.0,
    ):
        payload = {
            "device_id": device_id,
            "requested_command_id": command_id,
            "wait_timeout": wait_timeout,
            "commands": [],
            "complete": False,
        }
        self.calls.append(payload)
        return payload

    async def async_backup_hub(
        self,
        *,
        device_ids=None,
        wait_timeout: float = 10.0,
    ):
        payload = {
            "device_ids": list(device_ids) if device_ids is not None else None,
            "wait_timeout": wait_timeout,
            "kind": "hub_bundle",
            "schema_version": 4,
            "devices": [],
            "activities": [],
            "complete": False,
        }
        self.calls.append(payload)
        return payload

    async def async_restore_backup(
        self,
        payload: dict,
        *,
        wifi_commands_request_port: int = 8060,
    ):
        result = {
            "status": "success",
            "bundle_kind": payload.get("kind"),
            "device_id_map": {},
            "restored_devices": [],
            "restored_activities": [],
        }
        self.calls.append({"kind": "restore_backup", "payload": payload})
        return result

    async def async_play_ir_blob(
        self,
        blob: bytes,
        *,
        inter_frame_delay: float = 0.08,
    ):
        payload = {
            "blob": blob,
            "inter_frame_delay": inter_frame_delay,
        }
        self.calls.append(payload)
        return True

    async def async_editable_bundle(self, kind: str, entity_id: int):
        import copy

        self.calls.append({"kind": "editable_bundle", "entity": (kind, entity_id)})
        return copy.deepcopy(self.bundle)

    async def async_refresh_entity_structure(self, *, kind: str, ent_id: int):
        self.calls.append({"kind": "refresh", "entity": (kind, ent_id)})

    def cached_favorites_order(self, activity_id: int):
        return self.favorite_order_result

    def _get_cached_device_class(self, device_id: int):
        return "ir"

    def describe_favorites_order(self, activity_id: int, order: list[tuple[int, int]]):
        self.calls.append({"activity_id": activity_id, "kind": "describe_favorites_order", "order": list(order)})
        if self.favorite_descriptions is not None:
            return self.favorite_descriptions
        return [
            {"fav_id": fav_id, "button_id": fav_id, "slot": slot, "type": "unknown", "name": None}
            for fav_id, slot in order
        ]


def test_create_wifi_device_requires_commands(monkeypatch) -> None:
    hub = _FakeHub()
    entry = SimpleNamespace(entry_id="entry-1", options={"roku_listen_port": 8060})
    hass = _FakeHass(entry)
    async def _resolve(hass, call):
        return hub

    monkeypatch.setattr(runtime_module, "_async_resolve_hub_from_call", _resolve)

    with pytest.raises(ValueError, match="commands requires between 1 and 10 entries"):
        asyncio.run(
            integration._async_handle_create_wifi_device(
                _FakeCall({"device_name": "Home Assistant", "commands": []}, hass)
            )
        )


def test_create_wifi_device_validates_device_name(monkeypatch) -> None:
    hub = _FakeHub()
    entry = SimpleNamespace(entry_id="entry-1", options={"roku_listen_port": 8060})
    hass = _FakeHass(entry)
    async def _resolve(hass, call):
        return hub

    monkeypatch.setattr(runtime_module, "_async_resolve_hub_from_call", _resolve)

    with pytest.raises(ValueError, match="device_name must contain only letters"):
        asyncio.run(
            integration._async_handle_create_wifi_device(
                _FakeCall({"device_name": "+++", "commands": ["Launch"]}, hass)
            )
        )


def test_create_wifi_device_validates_command_names(monkeypatch) -> None:
    hub = _FakeHub()
    entry = SimpleNamespace(entry_id="entry-1", options={"roku_listen_port": 8060})
    hass = _FakeHass(entry)
    async def _resolve(hass, call):
        return hub

    monkeypatch.setattr(runtime_module, "_async_resolve_hub_from_call", _resolve)

    with pytest.raises(ValueError, match="commands entries must contain only letters"):
        asyncio.run(
            integration._async_handle_create_wifi_device(
                _FakeCall({"device_name": "Living Room", "commands": ["Do_Thing"]}, hass)
            )
        )


def test_create_wifi_device_accepts_valid_input(monkeypatch) -> None:
    hub = _FakeHub()
    entry = SimpleNamespace(entry_id="entry-1", options={"roku_listen_port": 8060})
    hass = _FakeHass(entry)
    async def _resolve(hass, call):
        return hub

    monkeypatch.setattr(runtime_module, "_async_resolve_hub_from_call", _resolve)

    result = asyncio.run(
        integration._async_handle_create_wifi_device(
            _FakeCall({"device_name": "Living Room", "commands": ["Lights On", "Lights Off"]}, hass)
        )
    )

    assert result == {
        "device_name": "Living Room",
        "commands": ["Lights On", "Lights Off"],
        "request_port": 8060,
    }


def test_create_wifi_device_accepts_plus_on_x1s(monkeypatch) -> None:
    hub = _FakeHub()
    hub.version = "X1S"
    entry = SimpleNamespace(entry_id="entry-1", options={"roku_listen_port": 8060})
    hass = _FakeHass(entry)

    async def _resolve(hass, call):
        return hub

    monkeypatch.setattr(runtime_module, "_async_resolve_hub_from_call", _resolve)

    result = asyncio.run(
        integration._async_handle_create_wifi_device(
            _FakeCall({"device_name": "TV+", "commands": ["Vol +", "Input+1"]}, hass)
        )
    )

    assert result == {
        "device_name": "TV+",
        "commands": ["Vol +", "Input+1"],
        "request_port": 8060,
    }
    assert hub.calls == [result]


def test_create_wifi_device_uses_configured_roku_listener_port(monkeypatch) -> None:
    hub = _FakeHub()
    entry = SimpleNamespace(entry_id="entry-1", options={"roku_listen_port": 8765})
    hass = _FakeHass(entry)

    async def _resolve(hass, call):
        return hub

    monkeypatch.setattr(runtime_module, "_async_resolve_hub_from_call", _resolve)

    result = asyncio.run(
        integration._async_handle_create_wifi_device(
            _FakeCall({"device_name": "Living Room", "commands": ["Lights On"]}, hass)
        )
    )

    assert result["request_port"] == 8765


def test_create_wifi_device_accepts_input_command_ids(monkeypatch) -> None:
    hub = _FakeHub()
    entry = SimpleNamespace(entry_id="entry-1", options={"roku_listen_port": 8060})
    hass = _FakeHass(entry)

    async def _resolve(hass, call):
        return hub

    monkeypatch.setattr(runtime_module, "_async_resolve_hub_from_call", _resolve)

    result = asyncio.run(
        integration._async_handle_create_wifi_device(
            _FakeCall(
                {
                    "device_name": "Living Room",
                    "commands": ["Lights On", "Lights Off", "Input HDMI 1"],
                    "input_command_ids": [3, 1],
                },
                hass,
            )
        )
    )

    assert result["input_command_ids"] == [3, 1]


def test_create_wifi_device_rejects_out_of_range_input_command_ids(monkeypatch) -> None:
    hub = _FakeHub()
    entry = SimpleNamespace(entry_id="entry-1", options={"roku_listen_port": 8060})
    hass = _FakeHass(entry)

    async def _resolve(hass, call):
        return hub

    monkeypatch.setattr(runtime_module, "_async_resolve_hub_from_call", _resolve)

    with pytest.raises(ValueError, match="input_command_ids entries must each be between 1 and 2"):
        asyncio.run(
            integration._async_handle_create_wifi_device(
                _FakeCall(
                    {
                        "device_name": "Living Room",
                        "commands": ["Lights On", "Lights Off"],
                        "input_command_ids": [3],
                    },
                    hass,
                )
            )
        )


def test_device_to_activity_validates_activity_id(monkeypatch) -> None:
    hub = _FakeHub()

    async def _resolve(hass, call):
        return hub

    monkeypatch.setattr(runtime_module, "_async_resolve_hub_from_call", _resolve)

    with pytest.raises(ValueError, match="activity_id must be between 1 and 255"):
        asyncio.run(
            integration._async_handle_device_to_activity(
                _FakeCall({"activity_id": 0, "device_id": 6})
            )
        )





def test_command_to_favorite_validates_command_id(monkeypatch) -> None:
    hub = _FakeHub()

    async def _resolve(hass, call):
        return hub

    monkeypatch.setattr(runtime_module, "_async_resolve_hub_from_call", _resolve)

    with pytest.raises(ValueError, match="command_id must be between 1 and 255"):
        asyncio.run(
            integration._async_handle_command_to_favorite(
                _FakeCall({"activity_id": 101, "device_id": 6, "command_id": 0})
            )
        )







def test_command_to_button_validates_button_id(monkeypatch) -> None:
    hub = _FakeHub()

    async def _resolve(hass, call):
        return hub

    monkeypatch.setattr(runtime_module, "_async_resolve_hub_from_call", _resolve)

    with pytest.raises(ValueError, match="button_id must be between 1 and 255"):
        asyncio.run(
            integration._async_handle_command_to_button(
                _FakeCall({"activity_id": 101, "button_id": 0, "device_id": 5, "command_id": 2})
            )
        )





def test_delete_device_validates_device_id(monkeypatch) -> None:
    hub = _FakeHub()

    async def _resolve(hass, call):
        return hub

    monkeypatch.setattr(runtime_module, "_async_resolve_hub_from_call", _resolve)

    with pytest.raises(ValueError, match="device_id must be between 1 and 255"):
        asyncio.run(
            integration._async_handle_delete_device(
                _FakeCall({"device_id": 0})
            )
        )


def test_delete_device_accepts_valid_input(monkeypatch) -> None:
    hub = _FakeHub()

    async def _resolve(hass, call):
        return hub

    monkeypatch.setattr(runtime_module, "_async_resolve_hub_from_call", _resolve)

    result = asyncio.run(
        integration._async_handle_delete_device(
            _FakeCall({"device_id": 4})
        )
    )

    assert result == {"device_id": 4}
    assert hub.calls[-1] == result


def test_dump_ir_commands_validates_device_id(monkeypatch) -> None:
    hub = _FakeHub()

    async def _resolve(hass, call):
        return hub

    monkeypatch.setattr(runtime_module, "_async_resolve_hub_from_call", _resolve)

    with pytest.raises(ValueError, match="device_id must be between 1 and 255"):
        asyncio.run(
            integration._async_handle_dump_ir_commands(
                _FakeCall({"device_id": 0})
            )
        )


def test_dump_ir_commands_validates_command_id(monkeypatch) -> None:
    hub = _FakeHub()

    async def _resolve(hass, call):
        return hub

    monkeypatch.setattr(runtime_module, "_async_resolve_hub_from_call", _resolve)

    with pytest.raises(ValueError, match="command_id must be between 1 and 255"):
        asyncio.run(
            integration._async_handle_dump_ir_commands(
                _FakeCall({"device_id": 11, "command_id": 0})
            )
        )


def test_dump_ir_commands_returns_action_payload(monkeypatch) -> None:
    hub = _FakeHub()

    async def _resolve(hass, call):
        return hub

    monkeypatch.setattr(runtime_module, "_async_resolve_hub_from_call", _resolve)

    result = asyncio.run(
        integration._async_handle_dump_ir_commands(
            _FakeCall({"device_id": 11})
        )
    )

    assert result == {
        "device_id": 11,
        "requested_command_id": None,
        "wait_timeout": 10.0,
        "commands": [],
        "complete": False,
    }
    assert hub.calls[-1] == result


def test_dump_ir_commands_accepts_single_command_probe(monkeypatch) -> None:
    hub = _FakeHub()

    async def _resolve(hass, call):
        return hub

    monkeypatch.setattr(runtime_module, "_async_resolve_hub_from_call", _resolve)

    result = asyncio.run(
        integration._async_handle_dump_ir_commands(
            _FakeCall({"device_id": 11, "command_id": 9})
        )
    )

    assert result == {
        "device_id": 11,
        "requested_command_id": 9,
        "wait_timeout": 10.0,
        "commands": [],
        "complete": False,
    }
    assert hub.calls[-1] == result


def test_fetch_blob_validates_device_id(monkeypatch) -> None:
    hub = _FakeHub()

    async def _resolve(hass, call):
        return hub

    monkeypatch.setattr(runtime_module, "_async_resolve_hub_from_call", _resolve)

    with pytest.raises(ValueError, match="device_id must be between 1 and 255"):
        asyncio.run(
            integration._async_handle_fetch_blob(
                _FakeCall({"device_id": 0})
            )
        )


def test_fetch_blob_validates_command_id(monkeypatch) -> None:
    hub = _FakeHub()

    async def _resolve(hass, call):
        return hub

    monkeypatch.setattr(runtime_module, "_async_resolve_hub_from_call", _resolve)

    with pytest.raises(ValueError, match="command_id must be between 1 and 255"):
        asyncio.run(
            integration._async_handle_fetch_blob(
                _FakeCall({"device_id": 11, "command_id": 0})
            )
        )


def test_fetch_blob_returns_action_payload(monkeypatch) -> None:
    hub = _FakeHub()

    async def _resolve(hass, call):
        return hub

    monkeypatch.setattr(runtime_module, "_async_resolve_hub_from_call", _resolve)

    result = asyncio.run(
        integration._async_handle_fetch_blob(
            _FakeCall({"device_id": 11})
        )
    )

    assert result == {
        "device_id": 11,
        "requested_command_id": None,
        "wait_timeout": 10.0,
        "commands": [],
        "complete": False,
    }
    assert hub.calls[-1] == result


def test_backup_bundle_rejects_invalid_device_id(monkeypatch) -> None:
    hub = _FakeHub()

    async def _resolve(hass, call):
        return hub

    monkeypatch.setattr(runtime_module, "_async_resolve_hub_from_call", _resolve)

    with pytest.raises(ValueError, match="device_ids entries must be in 1..255"):
        asyncio.run(
            integration._async_handle_backup_bundle(
                _FakeCall({"device_ids": [0]})
            )
        )


def test_backup_bundle_rejects_non_list_device_ids(monkeypatch) -> None:
    hub = _FakeHub()

    async def _resolve(hass, call):
        return hub

    monkeypatch.setattr(runtime_module, "_async_resolve_hub_from_call", _resolve)

    with pytest.raises(ValueError, match="device_ids must be a list"):
        asyncio.run(
            integration._async_handle_backup_bundle(
                _FakeCall({"device_ids": "11"})
            )
        )


def test_backup_bundle_returns_action_payload_for_device_subset(
    monkeypatch,
) -> None:
    hub = _FakeHub()

    async def _resolve(hass, call):
        return hub

    monkeypatch.setattr(runtime_module, "_async_resolve_hub_from_call", _resolve)

    result = asyncio.run(
        integration._async_handle_backup_bundle(
            _FakeCall({"device_ids": [11, 12]})
        )
    )

    assert result["kind"] == "hub_bundle"
    assert result["schema_version"] == 4
    assert result["device_ids"] == [11, 12]
    assert hub.calls[-1] is result


def test_backup_bundle_returns_action_payload_for_whole_hub(monkeypatch) -> None:
    hub = _FakeHub()

    async def _resolve(hass, call):
        return hub

    monkeypatch.setattr(runtime_module, "_async_resolve_hub_from_call", _resolve)

    result = asyncio.run(
        integration._async_handle_backup_bundle(_FakeCall({}))
    )

    assert result["kind"] == "hub_bundle"
    assert result["device_ids"] is None


def test_restore_backup_requires_bundle_object(monkeypatch) -> None:
    hub = _FakeHub()

    async def _resolve(hass, call):
        return hub

    monkeypatch.setattr(runtime_module, "_async_resolve_hub_from_call", _resolve)

    with pytest.raises(
        Exception,
        match="backup must be an object payload returned by backup_bundle",
    ):
        asyncio.run(
            integration._async_handle_restore_backup(
                _FakeCall({"backup": "not-a-dict"})
            )
        )


def test_restore_backup_returns_action_payload(monkeypatch) -> None:
    hub = _FakeHub()

    async def _resolve(hass, call):
        return hub

    monkeypatch.setattr(runtime_module, "_async_resolve_hub_from_call", _resolve)

    backup = {
        "kind": "hub_bundle",
        "schema_version": 4,
        "devices": [],
        "activities": [],
    }
    result = asyncio.run(
        integration._async_handle_restore_backup(
            _FakeCall({"backup": backup})
        )
    )

    assert result["status"] == "success"
    assert result["bundle_kind"] == "hub_bundle"
    assert hub.calls[-1] == {"kind": "restore_backup", "payload": backup}


def test_play_ir_blob_accepts_hex_blob_body(monkeypatch) -> None:
    hub = _FakeHub()

    async def _resolve(hass, call):
        return hub

    monkeypatch.setattr(runtime_module, "_async_resolve_hub_from_call", _resolve)
    monkeypatch.setattr(runtime_module, "_raise_if_hub_operation_locked", lambda *args, **kwargs: None)

    result = asyncio.run(
        integration._async_handle_play_ir_blob(
            _FakeCall({"blob": "00 00 00 1f 00 00 11 00 94 70 50 3a 53 6f 6e 79 31 32 20 52 3a 34 30 30 30 30 20 44 3a 31 20 46 3a 31 38 20 4d 55 4c 3a 32 00 00 00 00"})
        )
    )

    assert result is None
    assert hub.calls[-1]["blob"] == bytes.fromhex(
        "00 00 00 1f 00 00 11 00 94 70 50 3a 53 6f 6e 79 31 32 20 52 3a 34 30 30 30 30 20 44 3a 31 20 46 3a 31 38 20 4d 55 4c 3a 32 00 00 00 00"
    )


def test_play_ir_blob_accepts_descriptor_string(monkeypatch) -> None:
    hub = _FakeHub()

    async def _resolve(hass, call):
        return hub

    monkeypatch.setattr(runtime_module, "_async_resolve_hub_from_call", _resolve)
    monkeypatch.setattr(runtime_module, "_raise_if_hub_operation_locked", lambda *args, **kwargs: None)

    descriptor = "P:Sony12 R:40000 D:1 F:18 MUL:2"
    result = asyncio.run(
        integration._async_handle_play_ir_blob(
            _FakeCall({"blob": descriptor})
        )
    )

    assert result is None
    assert hub.calls[-1]["blob"] == build_descriptive_ir_blob_body(descriptor)


def _service_bundle() -> dict:
    """Activity 101 with favorite 1 (device 4 command 6), macro shortcut 9,
    a VOL_UP binding to that macro and power sequences for device 4;
    device 6 has an input record."""

    def _device(dev_id, inputs=None):
        return {
            "kind": "device_backup", "complete": True,
            "device": {"device_id": dev_id, "name": f"Device {dev_id}", "device_class": "ir"},
            "commands": [{"command_id": c, "name": f"Command {c}"} for c in (1, 2, 4, 6)],
            "input_record": {"entries": inputs} if inputs else None,
            "button_bindings": [], "macros": [],
        }

    return {
        "kind": "hub_bundle", "schema_version": 5,
        "devices": [_device(4), _device(5), _device(6, [{"input_index": 1, "command_id": 2}])],
        "activities": [{
            "kind": "activity_backup", "complete": True,
            "device": {"device_id": 101, "name": "Watch TV", "entity_type": "activity"},
            "button_bindings": [
                {"button_id": 182, "device_id": 101, "command_id": 9,
                 "long_press_device_id": None, "long_press_command_id": None},
            ],
            "favorite_slots": [{"button_id": 1, "device_id": 4, "command_id": 6}],
            "favorites_order": [9, 1],
            "macros": [
                {"button_id": 9, "name": "Test Macro", "steps": [
                    {"device_id": 4, "command_id": 1, "button_code": 0, "duration": 0, "delay": 0}]},
                {"button_id": 198, "name": "POWER_ON", "steps": [
                    {"device_id": 4, "command_id": 0xC6, "button_code": 0, "duration": 0, "delay": 0xFF},
                    {"device_id": 4, "command_id": 0xC5, "button_code": 0, "duration": 0, "delay": 0xFF}]},
                {"button_id": 199, "name": "POWER_OFF", "steps": [
                    {"device_id": 4, "command_id": 0xC7, "button_code": 0, "duration": 0, "delay": 0xFF}]},
            ],
            "referenced_source_device_ids": [4],
        }],
    }


def _capture_syncs(monkeypatch) -> list[dict]:
    """The services' edit + sync path (services migration S1): record the
    bundle pair each one hands to the sync_from_snapshot gauntlet."""

    import custom_components.sofabaton_x1s.entity_sync as entity_sync_module

    syncs: list[dict] = []

    async def _prepare(hass, *, hub, entity_kind, sync_input, operation_label, **_kwargs):
        entity_id = sync_input[f"{entity_kind}_id"]
        return "op-1", sync_input["baseline"], sync_input["edited"], entity_id

    async def _run(hass, operation_id, *, hub, baseline, edited, entity_kind, entity_id):
        syncs.append({"kind": entity_kind, "id": entity_id, "baseline": baseline, "edited": edited})
        return {"status": "success", "completed_steps": 1, "total_steps": 1, "counters": {}}

    monkeypatch.setattr(entity_sync_module, "_async_prepare_entity_sync", _prepare)
    monkeypatch.setattr(entity_sync_module, "_run_entity_sync_operation", _run)
    return syncs


def _activity(bundle: dict) -> dict:
    return bundle["activities"][0]


def test_device_to_activity_accepts_valid_input(monkeypatch) -> None:
    hub = _FakeHub()
    syncs = _capture_syncs(monkeypatch)

    async def _resolve(hass, call):
        return hub

    monkeypatch.setattr(runtime_module, "_async_resolve_hub_from_call", _resolve)

    result = asyncio.run(
        integration._async_handle_device_to_activity(
            _FakeCall({"activity_id": 101, "device_id": 6, "input_command_id": 2})
        )
    )

    assert result["status"] == "success"
    assert hub.calls[-1] == {"kind": "editable_bundle", "entity": ("activity", 101)}
    power_on = next(m for m in _activity(syncs[0]["edited"])["macros"] if m["button_id"] == 198)["steps"]
    assert [(s["device_id"], s["command_id"], s["duration"]) for s in power_on if s["device_id"] == 6] == [
        (6, 0xC6, 0), (6, 0xC5, 1),
    ]


def test_command_to_favorite_accepts_valid_input(monkeypatch, caplog) -> None:
    hub = _FakeHub()
    syncs = _capture_syncs(monkeypatch)

    async def _resolve(hass, call):
        return hub

    monkeypatch.setattr(runtime_module, "_async_resolve_hub_from_call", _resolve)

    result = asyncio.run(
        integration._async_handle_command_to_favorite(
            _FakeCall({"activity_id": 101, "device_id": 6, "command_id": 4, "slot_id": 3})
        )
    )

    assert result["status"] == "success"
    rows = _activity(syncs[0]["edited"])["favorite_slots"]
    assert [(r["device_id"], r["command_id"]) for r in rows] == [(4, 6), (6, 4)]
    # The hub assigns the favorite's id: a slot_id is accepted and ignored.
    assert "slot_id 3 has no effect" in caplog.text


def test_command_to_favorite_omits_slot_when_not_provided(monkeypatch, caplog) -> None:
    hub = _FakeHub()
    syncs = _capture_syncs(monkeypatch)

    async def _resolve(hass, call):
        return hub

    monkeypatch.setattr(runtime_module, "_async_resolve_hub_from_call", _resolve)

    asyncio.run(
        integration._async_handle_command_to_favorite(
            _FakeCall({"activity_id": 101, "device_id": 6, "command_id": 4})
        )
    )

    assert len(syncs) == 1 and "has no effect" not in caplog.text
    # An existing favorite is refused before any sync.
    from homeassistant.exceptions import HomeAssistantError

    with pytest.raises(HomeAssistantError, match="already a favorite"):
        asyncio.run(
            integration._async_handle_command_to_favorite(
                _FakeCall({"activity_id": 101, "device_id": 4, "command_id": 6})
            )
        )
    assert len(syncs) == 1


def test_command_to_button_accepts_valid_input(monkeypatch) -> None:
    hub = _FakeHub()
    syncs = _capture_syncs(monkeypatch)

    async def _resolve(hass, call):
        return hub

    monkeypatch.setattr(runtime_module, "_async_resolve_hub_from_call", _resolve)

    asyncio.run(
        integration._async_handle_command_to_button(
            _FakeCall({"activity_id": 101, "button_id": 193, "device_id": 5, "command_id": 2,
                       "long_press_device_id": 5, "long_press_command_id": 4})
        )
    )

    row = next(r for r in _activity(syncs[0]["edited"])["button_bindings"] if r["button_id"] == 193)
    assert (row["device_id"], row["command_id"]) == (5, 2)
    assert (row["long_press_device_id"], row["long_press_command_id"]) == (5, 4)


def test_get_favorites_returns_explicit_fav_ids(monkeypatch) -> None:
    hub = _FakeHub()
    hub.favorite_order_result = [(0x09, 0x01), (0x01, 0x02)]
    hub.favorite_descriptions = [
        {"fav_id": 0x09, "button_id": 0x09, "slot": 0x01, "type": "macro", "name": "Test Macro"},
        {
            "fav_id": 0x01,
            "button_id": 0x01,
            "favorite_button_id": 0x01,
            "activity_map_button_id": 0x01,
            "slot": 0x02,
            "type": "favorite",
            "name": "Command 6",
            "device_id": 0x04,
            "command_id": 0x06,
        },
    ]

    async def _resolve(hass, call):
        return hub

    monkeypatch.setattr(runtime_module, "_async_resolve_hub_from_call", _resolve)

    result = asyncio.run(integration._async_handle_get_favorites(_FakeCall({"activity_id": 101})))

    assert result == {"favorites": hub.favorite_descriptions}
    # Read fresh through the facade's activity refresh, then the cached order.
    assert {"kind": "refresh", "entity": ("activity", 101)} in hub.calls
    assert hub.calls[-1] == {
        "activity_id": 101, "kind": "describe_favorites_order", "order": [(0x09, 0x01), (0x01, 0x02)],
    }


def test_get_favorites_can_include_cached_entries_missing_from_hub_order(monkeypatch) -> None:
    hub = _FakeHub()
    hub.favorite_order_result = None  # the refresh read no order

    async def _resolve(hass, call):
        return hub

    monkeypatch.setattr(runtime_module, "_async_resolve_hub_from_call", _resolve)

    with pytest.raises(ValueError, match="did not respond to favorites order request"):
        asyncio.run(integration._async_handle_get_favorites(_FakeCall({"activity_id": 101})))


def test_reorder_favorites_requires_explicit_fav_ids(monkeypatch) -> None:
    hub = _FakeHub()
    syncs = _capture_syncs(monkeypatch)

    async def _resolve(hass, call):
        return hub

    monkeypatch.setattr(runtime_module, "_async_resolve_hub_from_call", _resolve)

    asyncio.run(
        integration._async_handle_reorder_favorites(
            _FakeCall({"activity_id": 101, "ordered_fav_ids": [1, 9]})
        )
    )

    assert _activity(syncs[0]["edited"])["favorites_order"] == [1, 9]


def test_reorder_favorites_accepts_legacy_order_alias(monkeypatch, caplog) -> None:
    """A partial list (or one naming an id the activity lacks) is completed:
    the asked ids first, the rest in their current order; the hub's order
    table always lists every entry now."""

    hub = _FakeHub()
    syncs = _capture_syncs(monkeypatch)

    async def _resolve(hass, call):
        return hub

    monkeypatch.setattr(runtime_module, "_async_resolve_hub_from_call", _resolve)

    asyncio.run(
        integration._async_handle_reorder_favorites(_FakeCall({"activity_id": 101, "order": [1, 77]}))
    )

    assert _activity(syncs[0]["edited"])["favorites_order"] == [1, 9]
    assert "fav_id 77 is not in the activity" in caplog.text


def test_delete_favorite_requires_explicit_fav_id(monkeypatch) -> None:
    """A favorite's id removes the favorite."""

    hub = _FakeHub()
    syncs = _capture_syncs(monkeypatch)

    async def _resolve(hass, call):
        return hub

    monkeypatch.setattr(runtime_module, "_async_resolve_hub_from_call", _resolve)

    asyncio.run(integration._async_handle_delete_favorite(_FakeCall({"activity_id": 101, "fav_id": 1})))

    activity = _activity(syncs[0]["edited"])
    assert activity["favorite_slots"] == [] and activity["favorites_order"] == [9]


def test_delete_favorite_accepts_legacy_button_id_alias(monkeypatch) -> None:
    """A macro shortcut's id removes the macro (as the hub's delete always
    did), and the binding that named it."""

    hub = _FakeHub()
    syncs = _capture_syncs(monkeypatch)

    async def _resolve(hass, call):
        return hub

    monkeypatch.setattr(runtime_module, "_async_resolve_hub_from_call", _resolve)

    asyncio.run(integration._async_handle_delete_favorite(_FakeCall({"activity_id": 101, "button_id": 9})))

    activity = _activity(syncs[0]["edited"])
    assert [m["button_id"] for m in activity["macros"]] == [198, 199]
    assert activity["button_bindings"] == [] and activity["favorites_order"] == [1]

    from homeassistant.exceptions import HomeAssistantError

    with pytest.raises(HomeAssistantError, match="not a favorite or macro"):
        asyncio.run(integration._async_handle_delete_favorite(_FakeCall({"activity_id": 101, "fav_id": 198})))




def test_persist_ir_blob_adds_the_command_through_a_device_sync(monkeypatch) -> None:
    """persist_ir_blob runs as an add_command edit + a device sync (services
    migration S1); its response names the new command."""

    hub = _FakeHub()
    syncs = _capture_syncs(monkeypatch)

    async def _resolve(hass, call):
        return hub

    monkeypatch.setattr(runtime_module, "_async_resolve_hub_from_call", _resolve)

    result = asyncio.run(
        integration._async_handle_persist_ir_blob(
            _FakeCall({"device_id": 6, "command_name": "Learned", "blob": "P:NEC1 D:4 S:5 F:21"})
        )
    )

    assert result == {"status": "success", "device_id": 6, "command_id": 3, "command_name": "Learned"}
    assert syncs[0]["kind"] == "device" and syncs[0]["id"] == 6
    device = next(d for d in syncs[0]["edited"]["devices"] if d["device"]["device_id"] == 6)
    added = next(c for c in device["commands"] if c["command_id"] == 3)
    assert added["name"] == "Learned"
    assert added["restore_data"]["data_hex"] == build_descriptive_ir_blob_body("P:NEC1 D:4 S:5 F:21").hex()

    hub._get_cached_device_class = lambda device_id: "wifi_ip"
    from homeassistant.exceptions import HomeAssistantError

    with pytest.raises(HomeAssistantError, match="only supports IR devices"):
        asyncio.run(
            integration._async_handle_persist_ir_blob(
                _FakeCall({"device_id": 6, "command_name": "Learned", "blob": "P:NEC1 D:4 S:5 F:21"})
            )
        )
