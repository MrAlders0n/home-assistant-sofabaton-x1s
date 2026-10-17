"""Open panels follow the cache (docs/internal/journey-bench-plan.md, fixes).

The push channel (``hub_state/subscribe``), the cache version the panel
compares (generation + epoch), and the backend gaps the journey bench
found: reorders that never bumped the generation (UI-2), the in-place
re-sync that wiped the device's command table (W-3) and key sort (W-4),
and plain hub writes no other tab could see (W-6's lock gap).
"""

from __future__ import annotations

import asyncio
import importlib
from types import SimpleNamespace

import pytest

from custom_components.sofabaton_x1s.const import SIGNAL_PANEL_STORE, signal_panel_state
from custom_components.sofabaton_x1s.hub import SofabatonHub
from tests.hub_fakes import FakeHass

ws_panel_module = importlib.import_module("custom_components.sofabaton_x1s.ws_panel")
runtime_module = importlib.import_module("custom_components.sofabaton_x1s.runtime")
hub_module = importlib.import_module("custom_components.sofabaton_x1s.hub")
hub_ops_module = importlib.import_module("custom_components.sofabaton_x1s.hub_ops")
command_config_module = importlib.import_module("custom_components.sofabaton_x1s.command_config")


@pytest.fixture
def loop():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield loop
    loop.close()


def _hub(loop) -> SofabatonHub:
    return SofabatonHub(
        FakeHass(loop), "entry-id", "hub-name", "127.0.0.1", 1234, {}, 9999, 10000, True, False,
    )


# ── hub_state/subscribe ────────────────────────────────────────────────


class _SubConn:
    def __init__(self):
        self.subscriptions: dict = {}
        self.messages: list = []
        self.result = None
        self.error = None

    def send_message(self, message):
        self.messages.append(message)

    def send_result(self, msg_id, result=None):
        self.result = (msg_id, result)

    def send_error(self, msg_id, code, message):
        self.error = (msg_id, code, message)

    @property
    def events(self):
        return [message["event"] for message in self.messages]


def _subscribe_env(monkeypatch, loop, hub):
    listeners: dict = {}

    def _connect(_hass, signal, target):
        listeners[signal] = target

        def _unsub():
            listeners.pop(signal, None)

        return _unsub

    async def _payload(_hass, hub, *, persistent_cache_enabled):
        return {"entry_id": hub.entry_id, "runtime_state": {"kind": hub.kind}}

    async def _cache_store(_hass):
        return SimpleNamespace(enabled=True)

    config_store = SimpleNamespace(revision=3)

    async def _config_store(_hass):
        return config_store

    monkeypatch.setattr(ws_panel_module, "async_dispatcher_connect", _connect)
    monkeypatch.setattr(ws_panel_module, "_async_build_control_panel_hub_payload", _payload)
    monkeypatch.setattr(runtime_module, "_async_get_persistent_cache_store", _cache_store)
    monkeypatch.setattr(runtime_module, "_async_get_command_config_store", _config_store)
    monkeypatch.setattr(
        runtime_module, "_get_hub_by_entry_id",
        lambda _hass, entry_id: hub if entry_id == hub.entry_id else None,
    )
    hass = SimpleNamespace(async_create_task=loop.create_task)
    return hass, listeners, config_store


def test_hub_state_pushes_at_once_then_once_per_burst(monkeypatch, loop):
    hub = SimpleNamespace(entry_id="entry-1", cache_generation=4, kind="idle")
    hass, listeners, config_store = _subscribe_env(monkeypatch, loop, hub)
    conn = _SubConn()

    loop.run_until_complete(
        ws_panel_module._ws_subscribe_hub_state(hass, conn, {"id": 5, "entry_id": "entry-1"})
    )

    assert conn.result == (5, None)
    assert conn.events == [{
        "hub": {"entry_id": "entry-1", "runtime_state": {"kind": "idle"}},
        "generation": 4,
        "config_revision": 3,
    }]
    assert signal_panel_state("entry-1") in listeners
    assert SIGNAL_PANEL_STORE in listeners

    # A burst: a sync starts, the cache moves, the store saves.
    hub.cache_generation = 9
    hub.kind = "operation_running"
    config_store.revision = 4
    listeners[signal_panel_state("entry-1")]()
    listeners[signal_panel_state("entry-1")]()
    listeners[SIGNAL_PANEL_STORE]()
    loop.run_until_complete(asyncio.sleep(0.3))

    assert len(conn.events) == 2
    assert conn.events[-1] == {
        "hub": {"entry_id": "entry-1", "runtime_state": {"kind": "operation_running"}},
        "generation": 9,
        "config_revision": 4,
    }

    # Unsubscribing drops every listener; a late change sends nothing.
    conn.subscriptions[5]()
    assert listeners == {}
    loop.run_until_complete(asyncio.sleep(0.3))
    assert len(conn.events) == 2


def test_hub_state_steady_changes_still_push_within_the_max_wait(monkeypatch, loop):
    hub = SimpleNamespace(entry_id="entry-1", cache_generation=0, kind="idle")
    hass, listeners, _config_store = _subscribe_env(monkeypatch, loop, hub)
    conn = _SubConn()
    loop.run_until_complete(
        ws_panel_module._ws_subscribe_hub_state(hass, conn, {"id": 6, "entry_id": "entry-1"})
    )

    async def _stream():
        # A change every 0.1 s for 0.8 s: the 0.15 s quiet never comes.
        for generation in range(1, 9):
            hub.cache_generation = generation
            listeners[signal_panel_state("entry-1")]()
            await asyncio.sleep(0.1)

    loop.run_until_complete(_stream())
    # The max wait (0.5 s) forced a push during the stream.
    assert len(conn.events) >= 2


def test_hub_state_unknown_hub_is_not_found(monkeypatch, loop):
    hub = SimpleNamespace(entry_id="entry-1", cache_generation=0, kind="idle")
    hass, listeners, _config_store = _subscribe_env(monkeypatch, loop, hub)
    conn = _SubConn()

    loop.run_until_complete(
        ws_panel_module._ws_subscribe_hub_state(hass, conn, {"id": 7, "entry_id": "nope"})
    )

    assert conn.error and conn.error[1] == "not_found"
    assert listeners == {}


# ── generation, epoch, contents ─────────────────────────────────────────


def test_cache_epoch_is_set_per_hub_object(loop):
    first = _hub(loop)
    assert first.cache_epoch > 0
    assert first.cache_generation == 0


def test_cache_contents_carry_the_generation_read_before_the_export(monkeypatch, loop):
    hub = _hub(loop)

    async def _export():
        # A change lands while the export runs.
        hub._bump_cache_generation()
        return {}

    monkeypatch.setattr(hub, "async_export_cache_state", _export)
    monkeypatch.setattr(hub, "_build_cache_activity_list", lambda _data: [])
    monkeypatch.setattr(hub, "_build_cache_activity_favorites", lambda: {})
    monkeypatch.setattr(hub, "_build_cache_devices_list", lambda _data: [])

    contents = loop.run_until_complete(hub.async_get_cache_contents())

    # The rows are labelled with the older generation, so an open card
    # sees the newer one and reloads once more.
    assert contents["cache_generation"] == 0
    assert hub.cache_generation == 1
    assert contents["cache_epoch"] == hub.cache_epoch


def test_hub_work_announces_its_start_and_end(monkeypatch, loop):
    hub = _hub(loop)
    sent: list[str] = []
    monkeypatch.setattr(hub_module, "async_dispatcher_send", lambda _hass, signal, *_a: sent.append(signal))

    async def _work():
        async with hub.async_hub_work():
            assert hub.hub_write_active
            async with hub.async_hub_work():  # nested: no extra announcement
                pass
        assert not hub.hub_write_active

    loop.run_until_complete(_work())
    assert sent == [signal_panel_state("entry-id"), signal_panel_state("entry-id")]


def test_runtime_payload_reports_plain_hub_work(monkeypatch, loop):
    hub = SimpleNamespace(
        entry_id="entry-1", client_connected=False, hub_write_active=True,
    )

    async def _config_store(_hass):
        async def _list(_entry_id, roku_listen_port=None):
            return []

        return SimpleNamespace(async_list_hub_devices=_list)

    monkeypatch.setattr(runtime_module, "_async_get_command_config_store", _config_store)
    monkeypatch.setattr(runtime_module, "_resolve_roku_listen_port", lambda _hass, _entry_id: 8060)
    hass = SimpleNamespace(data={})

    payload = loop.run_until_complete(
        ws_panel_module._async_build_control_panel_runtime_payload(hass, hub)
    )

    assert payload["kind"] == "operation_running"
    assert payload["operation"] == "hub_write"
    # The outcomes ride along, so an operation that just ended under it
    # is still announced.
    assert payload["last_operation"] is None
    assert payload["last_wifi_deploys"] == {}

    hub.hub_write_active = False
    payload = loop.run_until_complete(
        ws_panel_module._async_build_control_panel_runtime_payload(hass, hub)
    )
    assert payload["kind"] == "idle"


# ── UI-2: reorders move the generation ─────────────────────────────────


@pytest.mark.parametrize("kind", ["activities", "devices"])
def test_reorder_bumps_the_generation(monkeypatch, loop, kind):
    hub = _hub(loop)
    sent: list[str] = []
    monkeypatch.setattr(hub_ops_module, "async_dispatcher_send", lambda _hass, signal, *_a: sent.append(signal))
    monkeypatch.setattr(
        hub._proxy, f"reorder_{kind}", lambda ids: {"status": "success", "ordered_ids": list(ids)}
    )
    before = hub.cache_generation

    result = loop.run_until_complete(getattr(hub, f"async_reorder_{kind}")([2, 1]))

    assert result["status"] == "success"
    assert hub.cache_generation == before + 1
    assert sent, "the Hub tab listeners hear of the new order"


def test_failed_reorder_leaves_the_generation(monkeypatch, loop):
    hub = _hub(loop)
    monkeypatch.setattr(hub._proxy, "reorder_activities", lambda ids: None)
    before = hub.cache_generation

    loop.run_until_complete(hub.async_reorder_activities([2, 1]))

    assert hub.cache_generation == before


# ── W-3 / W-4: the in-place re-read ────────────────────────────────────


def _step(kind, **payload):
    return SimpleNamespace(kind=kind, payload=payload)


def test_inplace_rewarm_never_reads_the_device_as_an_activity(monkeypatch, loop):
    dev_id = 11
    hub = _hub(loop)
    acts: list[int] = []
    devices: list[int] = []

    async def _act(act_id):
        acts.append(int(act_id))

    async def _dev(dev, *_a, **_k):
        devices.append(int(dev))

    async def _order(_act_id):
        return None

    monkeypatch.setattr(hub, "_async_fetch_activity_commands", _act)
    monkeypatch.setattr(hub, "async_fetch_device_commands", _dev)
    monkeypatch.setattr(hub, "async_request_favorites_order", _order)
    plan = SimpleNamespace(steps=[
        # A dropped hard button: the device page's own binding.
        _step("binding_delete", activity_id=dev_id, button_id=0xBE),
        _step("binding_write", activity_id=0x65, button_id=0xBE, device_id=dev_id, command_id=1),
    ])

    touched = loop.run_until_complete(hub._async_rewarm_after_inplace(plan, dev_id, set()))

    assert touched == [0x65]
    assert acts == [0x65]
    # The device's own record is read whole once (commands, keymap, key sort).
    assert devices == [dev_id]


def test_device_command_reread_takes_the_whole_device_record(monkeypatch, loop):
    hub = _hub(loop)
    calls: list = []

    def _backup(device_id, **kwargs):
        calls.append(("backup_device", device_id, kwargs))
        return {"device": {"device_id": device_id}}

    def _clear(*args, **kwargs):
        calls.append(("clear", args, kwargs))

    monkeypatch.setattr(hub._proxy, "backup_device", _backup)
    monkeypatch.setattr(hub._proxy, "clear_entity_cache", _clear)

    loop.run_until_complete(hub._async_fetch_device_commands(11))

    # No commands-only clear: it forgot the key sort and the keymap.
    assert calls == [("backup_device", 11, {"include_blobs": False, "refresh_catalog": False})]


def test_device_command_reread_falls_back_for_an_uncatalogued_device(monkeypatch, loop):
    hub = _hub(loop)
    calls: list = []
    monkeypatch.setattr(hub._proxy, "backup_device", lambda *_a, **_k: None)
    monkeypatch.setattr(hub._proxy, "clear_entity_cache", lambda *args, **kwargs: calls.append(("clear", args)))
    monkeypatch.setattr(
        hub._proxy, "get_commands_for_entity",
        lambda ent_id, fetch_if_missing=False: calls.append(("commands", ent_id)) or ({}, True),
    )

    loop.run_until_complete(hub._async_fetch_device_commands(11))

    # The commands group only: the keymap stays.
    assert calls == [("clear", (11, False, False, False)), ("commands", 11)]


# ── the command-config store's revision ────────────────────────────────


def test_command_config_saves_move_the_revision(monkeypatch, loop):
    sent: list[str] = []
    monkeypatch.setattr(command_config_module, "async_dispatcher_send", lambda _hass, signal, *_a: sent.append(signal))
    store = command_config_module.CommandConfigStore.__new__(command_config_module.CommandConfigStore)
    store._hass = object()
    store.revision = 0
    store._data = {}
    saved: list = []

    async def _save(data):
        saved.append(data)

    store._store = SimpleNamespace(async_save=_save)

    loop.run_until_complete(store._save())
    loop.run_until_complete(store._save())

    assert store.revision == 2
    assert len(saved) == 2
    assert sent == [SIGNAL_PANEL_STORE, SIGNAL_PANEL_STORE]
