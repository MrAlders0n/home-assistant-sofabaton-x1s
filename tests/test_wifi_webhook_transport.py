"""Wifi Commands over Home Assistant's own webhook (X2), the deploy's
delivery test and the hub's MQTT broker link, Home Assistant side
(docs/internal/wifi-webhook-transport-plan.md, wave 2)."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from homeassistant.components import webhook as ha_webhook

import custom_components.sofabaton_x1s.hub_mqtt_link as hub_mqtt_link
import custom_components.sofabaton_x1s.wifi_deploy as wifi_deploy_module
import custom_components.sofabaton_x1s.wifi_ingress as wifi_ingress_module
import custom_components.sofabaton_x1s.wifi_webhook as wifi_webhook_module
from custom_components.sofabaton_x1s.command_config import (
    CommandConfigStore,
    hash_listen_port_for_transport,
    normalize_requested_transport,
    normalize_wifi_transport,
    url_origin,
    wifi_device_requires_listener,
    wifi_transport_switch_pending,
)
from custom_components.sofabaton_x1s.const import DOMAIN
from custom_components.sofabaton_x1s.hub import SofabatonHub
from custom_components.sofabaton_x1s.lib.blob_decoders import parse_wifi_https_blob_body
from custom_components.sofabaton_x1s.lib.protocol_const import WIFI_URL_RECORD_CODE
from custom_components.sofabaton_x1s.wifi_webhook import (
    format_local_endpoint,
    parse_local_endpoint,
    parse_webhook_press,
    webhook_endpoint,
    webhook_transport_ready,
)
from tests.hub_fakes import FakeHass

HA_ADDRESS = "192.168.2.50"
HUB_IP = "192.168.2.123"
WEBHOOK_ID = "0123456789abcdef0123456789abcdef"


def _run(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _store() -> CommandConfigStore:
    store = CommandConfigStore(SimpleNamespace())
    _run(store.async_load())
    return store


@pytest.fixture(autouse=True)
def _clean_webhook_registry():
    ha_webhook.registered.clear()
    yield
    ha_webhook.registered.clear()


# ── Pure helpers ─────────────────────────────────────────────────────────


def test_local_endpoint_override_forms() -> None:
    assert parse_local_endpoint("") == (None, None, None)
    assert parse_local_endpoint(" 192.168.2.50 ") == ("192.168.2.50", None, None)
    assert parse_local_endpoint("192.168.2.50:8124") == ("192.168.2.50", 8124, None)
    assert parse_local_endpoint("HTTPS://192.168.2.50:8443/") == ("192.168.2.50", 8443, "https")
    assert parse_local_endpoint("http://192.168.2.50") == ("192.168.2.50", None, "http")
    for text in ("192.168.2.50", "192.168.2.50:8124", "https://192.168.2.50:8443", "http://192.168.2.50"):
        assert format_local_endpoint(*parse_local_endpoint(text)) == text
    assert format_local_endpoint(None, 8124, "https") == ""


@pytest.mark.parametrize(
    "text",
    ["ha.local", "192.168.2.50:0", "192.168.2.50:70000", "192.168.2.50:x", "ftp://192.168.2.50", "::1"],
)
def test_local_endpoint_override_refuses(text: str) -> None:
    with pytest.raises(ValueError):
        parse_local_endpoint(text)


def test_webhook_press_from_query_then_body() -> None:
    assert parse_webhook_press({"d": "9", "s": "0", "p": "short"}, "") == (9, 0, "short")
    assert parse_webhook_press({"d": "9", "s": "3"}, "") == (9, 3, "short")
    # The records carry the same facts in a JSON body too.
    assert parse_webhook_press({}, json.dumps({"d": 9, "s": 2, "p": "long"})) == (9, 2, "long")
    assert parse_webhook_press({}, "") is None
    assert parse_webhook_press({"d": "9", "s": "-1"}, "") is None
    assert parse_webhook_press({"d": "9", "s": "1", "p": "double"}, "") is None
    assert parse_webhook_press({}, "not json") is None


def test_webhook_endpoint_follows_home_assistant_unless_overridden() -> None:
    hass = SimpleNamespace(config=SimpleNamespace(components={"webhook"}, api=SimpleNamespace(use_ssl=True, port=8123)))
    hub = SimpleNamespace(local_port=None, local_scheme=None)
    assert webhook_transport_ready(hass)
    assert webhook_endpoint(hass, hub, HA_ADDRESS) == "https://192.168.2.50:8123"
    hass.config.api = SimpleNamespace(use_ssl=False, port=8123)
    assert webhook_endpoint(hass, hub, HA_ADDRESS) == "http://192.168.2.50:8123"
    # A proxy in front: its own scheme and port.
    hub = SimpleNamespace(local_port=443, local_scheme="https")
    assert webhook_endpoint(hass, hub, HA_ADDRESS) == "https://192.168.2.50:443"
    # No HTTP server reported (or no webhook integration): no webhook transport.
    assert not webhook_transport_ready(SimpleNamespace(config=SimpleNamespace(components={"webhook"}, api=None)))
    assert not webhook_transport_ready(SimpleNamespace(config=SimpleNamespace(components=set(), api=object())))


def test_url_origin_drops_the_webhook_id() -> None:
    assert url_origin(f"https://192.168.2.50:8123/api/webhook/{WEBHOOK_ID}") == "https://192.168.2.50:8123"
    assert url_origin("http://192.168.2.50/api/webhook/x") == "http://192.168.2.50:80"
    assert url_origin("") is None


# ── Transport rules ──────────────────────────────────────────────────────


def test_webhook_is_direct_like_the_listener() -> None:
    assert normalize_wifi_transport("webhook") == "webhook"
    assert normalize_wifi_transport("bogus") == "http"
    # A user only ever asks for Direct or MQTT.
    assert normalize_requested_transport("webhook") == "http"
    # Listener <-> webhook is never a transport switch...
    assert not wifi_transport_switch_pending(
        {"deployed_device_id": 9, "deployed_transport": "webhook", "requested_transport": "http"},
        mqtt_available=True,
    )
    # ...webhook <-> MQTT is.
    assert wifi_transport_switch_pending(
        {"deployed_device_id": 9, "deployed_transport": "webhook", "requested_transport": "mqtt"},
        mqtt_available=True,
    )
    # Only listener records need the listener.
    assert wifi_device_requires_listener({"deployed_device_id": 9, "deployed_transport": "http"})
    assert not wifi_device_requires_listener({"deployed_device_id": 9, "deployed_transport": "webhook"})
    # Webhook records hash like the listener's (a move keeps the hash).
    assert hash_listen_port_for_transport("webhook", 8060) == 8060
    assert hash_listen_port_for_transport("http", 8060) == 8060
    assert hash_listen_port_for_transport("mqtt", 8060) == 0


def _selector(version: str, components: set[str]):
    fake = SimpleNamespace(
        version=version,
        mac="FC:01:2C:39:D3:D0",
        banner_mac="FC012C39D3D0",
        entry_id="entry-1",
        hass=SimpleNamespace(
            config=SimpleNamespace(components=components, api=SimpleNamespace(use_ssl=True, port=8123))
        ),
    )
    fake._wifi_mqtt_mac = lambda: SofabatonHub._wifi_mqtt_mac(fake)
    fake.wifi_mqtt_available = lambda: SofabatonHub.wifi_mqtt_available(fake)
    fake.wifi_webhook_available = lambda: SofabatonHub.wifi_webhook_available(fake)
    fake._direct_wifi_transport = lambda: SofabatonHub._direct_wifi_transport(fake)
    return lambda payload, **kw: SofabatonHub._select_wifi_command_transport(fake, payload, **kw)


def test_direct_follows_the_hub_model() -> None:
    x2 = _selector("X2", {"webhook", "mqtt"})
    x1s = _selector("X1S", {"webhook", "mqtt"})
    fresh = {"deployed_device_id": None, "requested_transport": "http"}
    assert x2(fresh) == "webhook"
    assert x1s(fresh) == "http"
    assert x2({**fresh, "requested_transport": "mqtt"}) == "mqtt"
    # Existing X2 listener devices stay on the listener until moved.
    on_listener = {"deployed_device_id": 9, "deployed_transport": "http", "requested_transport": "http"}
    assert x2(on_listener) == "http"
    assert x2(on_listener, move_to_direct=True) == "webhook"
    assert x1s(on_listener, move_to_direct=True) == "http"
    # A move never overrides a pending switch to MQTT.
    assert x2({**on_listener, "requested_transport": "mqtt"}, move_to_direct=True) == "mqtt"
    # Switching an MQTT device to Direct on an X2 lands on the webhook.
    assert x2({"deployed_device_id": 9, "deployed_transport": "mqtt", "requested_transport": "http"}) == "webhook"
    # A webhook device keeps it.
    assert x2({"deployed_device_id": 9, "deployed_transport": "webhook", "requested_transport": "http"}) == "webhook"


# ── Store ────────────────────────────────────────────────────────────────


def test_store_webhook_id_sources_and_delivery() -> None:
    store = _store()
    assert store.get_webhook_id("hub-1") is None
    webhook_id = _run(store.async_ensure_webhook_id("hub-1"))
    assert len(webhook_id) == 32 and int(webhook_id, 16) >= 0
    assert _run(store.async_ensure_webhook_id("hub-1")) == webhook_id
    assert store.get_webhook_id("hub-2") is None

    _run(store.async_set_webhook_sources("hub-1", ["10.0.0.1", " 10.0.0.1", ""]))
    assert store.get_webhook_sources("hub-1") == ["10.0.0.1"]

    device = _run(store.async_create_hub_device("hub-1", "Lights"))
    key = device["device_key"]
    url = f"https://192.168.2.50:8123/api/webhook/{webhook_id}"
    _run(
        store.async_save_deployed_wifi_commands(
            "hub-1", key, [{"name": "On"}], deployed_device_id=9, commands_hash="h",
            deployed_transport="webhook", deployed_url=url,
        )
    )
    payload = _run(store.async_get_hub_config("hub-1", device_key=key))
    # The payload names where the records call, never the webhook id.
    assert payload["deployed_target"] == "https://192.168.2.50:8123"
    assert webhook_id not in json.dumps(payload)
    assert store.get_deployed_url("hub-1", key) == url

    # A failed test lights the warning; a real press clears it, once.
    _run(store.async_set_delivery("hub-1", key, {"status": "not_delivered", "transport": "webhook"}))
    revision = store.revision
    assert _run(store.async_mark_delivered("hub-1", 9, transport="webhook", source_ip=HUB_IP))
    assert store.revision == revision + 1
    assert not _run(store.async_mark_delivered("hub-1", 9, transport="webhook", source_ip=HUB_IP))
    assert store.revision == revision + 1
    delivery = _run(store.async_get_hub_config("hub-1", device_key=key))["delivery"]
    assert delivery["status"] == "delivered" and delivery["via"] == "press"

    # Back to undeployed: transport, URL and delivery go with it.
    _run(store.async_save_deployed_wifi_commands("hub-1", key, [], deployed_device_id=None, commands_hash=""))
    payload = _run(store.async_get_hub_config("hub-1", device_key=key))
    assert payload["deployed_transport"] is None
    assert payload["deployed_target"] is None
    assert payload["delivery"] is None


# ── Webhook registration and handler ─────────────────────────────────────


class _Request:
    def __init__(self, query, body="", remote=HUB_IP):
        self.query = query
        self._body = body
        self.remote = remote

    async def text(self):
        return self._body


def test_webhook_registers_local_only_and_hands_presses_to_the_hub() -> None:
    hass = SimpleNamespace(data={DOMAIN: {}})
    wifi_webhook_module.async_register_hub_webhook(hass, "hub-1", WEBHOOK_ID)
    # Idempotent for the same id.
    wifi_webhook_module.async_register_hub_webhook(hass, "hub-1", WEBHOOK_ID)
    registration = ha_webhook.registered[WEBHOOK_ID]
    assert registration["local_only"] is True
    assert registration["domain"] == DOMAIN

    presses: list[dict] = []

    async def _press(**kwargs):
        presses.append(kwargs)

    hass.data[DOMAIN]["hub-1"] = SimpleNamespace(async_handle_wifi_webhook_press=_press)
    handler = registration["handler"]
    response = _run(handler(hass, WEBHOOK_ID, _Request({"d": "9", "s": "2", "p": "long"})))
    assert response.status == 200
    assert presses == [{"device_id": 9, "command_index": 2, "press_type": "long", "source_ip": HUB_IP}]
    # A call without a press answers and does nothing.
    _run(handler(hass, WEBHOOK_ID, _Request({})))
    assert len(presses) == 1

    wifi_webhook_module.async_unregister_hub_webhook(hass, "hub-1")
    assert WEBHOOK_ID not in ha_webhook.registered


class _IngressStore:
    def __init__(self, sources=()):
        self.sources = list(sources)
        self.marked: list[tuple] = []

    def get_webhook_sources(self, entry_id):
        return list(self.sources)

    def get_deployed_wifi_commands(self, entry_id, *, hub_device_id=None, device_key=None):
        return [{"name": "Lights On"}, {"name": "Lights Off"}] if hub_device_id == 9 else []

    def is_wifi_events_hub_device(self, entry_id, hub_device_id):
        return False

    async def async_mark_delivered(self, entry_id, hub_device_id, *, transport, source_ip=""):
        self.marked.append((hub_device_id, transport, source_ip))
        return True


def _ingress_hub(monkeypatch, loop, store):
    hass = FakeHass(loop)
    hub = SofabatonHub(hass, "entry-id", "hub-name", HUB_IP, 1234, {}, 9999, 10000, True, False, version="X2")
    monkeypatch.setattr(wifi_ingress_module, "async_get_command_config_store", lambda _hass: asyncio.sleep(0, result=store))
    actions: list[tuple] = []

    async def _run_action(**kwargs):
        actions.append((kwargs["command_index"], kwargs["press_type"]))

    monkeypatch.setattr(hub, "_async_maybe_run_live_wifi_slot_action", _run_action)
    return hub, actions


def test_webhook_press_runs_only_from_the_hub_or_a_learned_source(monkeypatch) -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    store = _IngressStore(sources=["10.20.0.1"])
    hub, actions = _ingress_hub(monkeypatch, loop, store)

    def press(source_ip, index=0):
        loop.run_until_complete(
            hub.async_handle_wifi_webhook_press(
                device_id=9, command_index=index, press_type="short", source_ip=source_ip
            )
        )

    press("192.168.2.99")  # another device with the leaked id
    assert actions == []
    press(HUB_IP, index=1)
    assert actions == [(1, "short")]
    record = hub.get_last_ip_command()
    assert record["transport"] == "webhook" and record["command_label"] == "Lights Off"
    assert record["path"] == ""  # the webhook id never lands in a record
    # A real press proves the path.
    assert store.marked == [(9, "webhook", HUB_IP)]
    press("10.20.0.1")  # a NAT a delivery test arrived from
    assert actions == [(1, "short"), (0, "short")]
    loop.close()


def test_a_test_press_is_claimed_on_every_ingress_and_runs_no_action(monkeypatch) -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    store = _IngressStore()
    hub, actions = _ingress_hub(monkeypatch, loop, store)

    for transport, deliver in (
        ("webhook", lambda: hub.async_handle_wifi_webhook_press(
            device_id=9, command_index=0, press_type="short", source_ip="10.9.9.9")),
        ("http", lambda: hub.async_handle_roku_http_post(
            path="/launch/fc012c39d3d0/9/0/short", headers={}, body=b"", source_ip=HUB_IP)),
    ):
        future = loop.create_future()
        hub._wifi_probes[9] = future
        loop.run_until_complete(deliver())
        assert future.done() and future.result()["transport"] == transport
        hub._wifi_probes.clear()
    # The test press ran no Action, wrote no sensor record, marked nothing.
    assert actions == []
    assert hub.get_last_ip_command() is None
    assert store.marked == []
    # A repeat right after the test ended is still the test's...
    slot_1 = hub.async_handle_wifi_webhook_press(device_id=9, command_index=0, press_type="short", source_ip=HUB_IP)
    loop.run_until_complete(slot_1)
    assert actions == []
    # ...a press after the window is a real one.
    hub._wifi_probe_tails[9] = 0.0
    loop.run_until_complete(
        hub.async_handle_wifi_webhook_press(device_id=9, command_index=0, press_type="short", source_ip=HUB_IP)
    )
    assert actions == [(0, "short")]
    actions.clear()
    # Only slot 1 short is the test's: a long press goes through.
    hub._wifi_probes[9] = loop.create_future()
    loop.run_until_complete(
        hub.async_handle_wifi_webhook_press(device_id=9, command_index=0, press_type="long", source_ip=HUB_IP)
    )
    assert not hub._wifi_probes[9].done()
    assert actions == [(0, "long")]
    loop.close()


# ── The delivery test ────────────────────────────────────────────────────


class _ProbeStore:
    def __init__(self):
        self.delivery: dict | None = None
        self.sources: list[str] = []

    async def async_set_delivery(self, entry_id, device_key, delivery):
        self.delivery = delivery

    def get_webhook_sources(self, entry_id):
        return list(self.sources)

    async def async_set_webhook_sources(self, entry_id, sources):
        self.sources = list(sources)


def _probe_hub(monkeypatch, loop, store, *, arrives_from=None, sent=True):
    hass = FakeHass(loop)
    hub = SofabatonHub(hass, "entry-id", "hub-name", HUB_IP, 1234, {}, 9999, 10000, True, False, version="X2")
    monkeypatch.setattr(wifi_deploy_module, "async_get_command_config_store", lambda _hass: asyncio.sleep(0, result=store))
    pressed: list[tuple] = []

    def _send_command(ent_id, key_code):
        pressed.append((ent_id, key_code))
        if sent and arrives_from is not None:
            loop.call_soon(
                lambda: hub._claim_wifi_probe(
                    ent_id, 0, "short", transport="webhook", source_ip=arrives_from
                )
            )
        return sent

    monkeypatch.setattr(hub._proxy, "send_command", _send_command)
    return hub, pressed


def test_probe_presses_slot_1_and_reports_delivery(monkeypatch) -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    store = _ProbeStore()
    hub, pressed = _probe_hub(monkeypatch, loop, store, arrives_from=HUB_IP)
    delivery = loop.run_until_complete(
        hub.async_probe_wifi_delivery(
            device_key="lights", hub_device_id=9, transport="webhook", target="https://192.168.2.50:8123"
        )
    )
    assert pressed == [(9, 1)]
    assert delivery["status"] == "delivered"
    assert delivery["hub_ip"] == HUB_IP and delivery["source_ip"] == HUB_IP
    assert delivery["target"] == "https://192.168.2.50:8123"
    assert isinstance(delivery["latency_s"], float)
    assert store.delivery == delivery
    # The hub's own address is always accepted: nothing learned.
    assert store.sources == []
    assert hub._wifi_probes == {}
    loop.close()


def test_probe_learns_a_nat_source_for_the_webhook(monkeypatch) -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    store = _ProbeStore()
    hub, _pressed = _probe_hub(monkeypatch, loop, store, arrives_from="10.20.0.1")
    loop.run_until_complete(hub.async_probe_wifi_delivery(device_key="lights", hub_device_id=9, transport="webhook"))
    assert store.sources == ["10.20.0.1"]
    loop.close()


def test_probe_that_never_arrives_is_reported_not_raised(monkeypatch) -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    store = _ProbeStore()
    hub, _pressed = _probe_hub(monkeypatch, loop, store, arrives_from=None)
    delivery = loop.run_until_complete(
        hub.async_probe_wifi_delivery(device_key="lights", hub_device_id=9, transport="http", timeout=0.05)
    )
    assert delivery["status"] == "not_delivered" and delivery["latency_s"] is None
    assert hub._wifi_probes == {}

    hub, _pressed = _probe_hub(monkeypatch, loop, store, sent=False)
    delivery = loop.run_until_complete(
        hub.async_probe_wifi_delivery(device_key="lights", hub_device_id=9, transport="mqtt", timeout=0.05)
    )
    assert delivery["status"] == "not_sent"
    loop.close()


# ── A webhook deploy ─────────────────────────────────────────────────────


class _DeployStore:
    def __init__(self, *, deployed_url=None):
        self.saved: list[dict] = []
        self.delivery: dict | None = None
        self.deployed_url = deployed_url

    def get_webhook_id(self, entry_id):
        return WEBHOOK_ID

    async def async_ensure_webhook_id(self, entry_id):
        return WEBHOOK_ID

    def get_deployed_url(self, entry_id, device_key):
        return self.deployed_url

    def get_webhook_sources(self, entry_id):
        return []

    async def async_set_webhook_sources(self, entry_id, sources):
        return None

    def get_deployed_wifi_commands(self, entry_id, *, hub_device_id=None, device_key=None):
        return [{"name": "Command 1"}]

    async def async_list_hub_devices(self, entry_id, **_kwargs):
        return []

    async def async_set_delivery(self, entry_id, device_key, delivery):
        self.delivery = delivery

    async def async_save_deployed_wifi_commands(self, entry_id, device_key, commands, **kwargs):
        self.saved.append({"device_key": device_key, **kwargs})


def _x2_deploy_hub(monkeypatch, loop, store, calls):
    hass = FakeHass(loop)
    hass.config = SimpleNamespace(components={"webhook"}, api=SimpleNamespace(use_ssl=True, port=8123))
    hub = SofabatonHub(hass, "entry-id", "hub-name", HUB_IP, 1234, {}, 9999, 10000, True, False, version="X2")
    hub.roku_server_enabled = False
    hub.activities = {}
    for module in (wifi_deploy_module, wifi_webhook_module):
        monkeypatch.setattr(module, "async_get_command_config_store", lambda _hass: asyncio.sleep(0, result=store))
    monkeypatch.setattr(hub, "local_address_in_use", lambda: HA_ADDRESS)

    async def _snapshot(*_a, **_k):
        return {}

    async def _create(**kwargs):
        calls.append("create")
        return {"device_id": 9, "status": "success"}

    def _rewrite(device_id, bodies):
        calls.append("rewrite")
        hub._rewritten = (device_id, bodies)
        return {"device_id": device_id, "rewritten": sorted(bodies)}

    def _send_command(ent_id, key_code):
        calls.append("probe")
        loop.call_soon(
            lambda: hub._claim_wifi_probe(ent_id, 0, "short", transport="webhook", source_ip=HUB_IP)
        )
        return True

    async def _resync():
        calls.append("resync")

    remote_syncs: list[dict] = []

    async def _sync_after(**kwargs):
        calls.append("sync")
        remote_syncs.append(kwargs)
        return True

    async def _noop(*_a, **_k):
        return None

    async def _enable_listener(enable):
        calls.append(f"listener:{enable}")

    monkeypatch.setattr(hub, "_async_refresh_devices_snapshot", _snapshot)
    monkeypatch.setattr(hub, "async_create_wifi_device", _create)
    monkeypatch.setattr(hub._proxy, "rewrite_wifi_records", _rewrite)
    monkeypatch.setattr(hub._proxy, "send_command", _send_command)
    monkeypatch.setattr(hub._proxy, "backup_device", lambda *_a, **_k: None)
    monkeypatch.setattr(hub, "async_resync_remote", _resync)
    monkeypatch.setattr(hub, "async_sync_remotes_after_write", _sync_after)
    hub._remote_syncs = remote_syncs
    monkeypatch.setattr(hub, "async_fetch_device_commands", _noop)
    monkeypatch.setattr(hub, "_async_persist_cache_if_enabled", _noop)
    monkeypatch.setattr(hub, "async_set_roku_server_enabled", _enable_listener)
    return hub


def test_x2_deploy_writes_url_records_tests_delivery_then_syncs_the_remote(monkeypatch) -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    calls: list[str] = []
    store = _DeployStore()
    hub = _x2_deploy_hub(monkeypatch, loop, store, calls)
    payload = {
        "commands": [{"name": "Command 1", "add_as_favorite": False, "hard_button": "", "activities": [], "action": {}}],
        "commands_hash": "abc",
        "requested_transport": "http",
    }
    result = loop.run_until_complete(hub.async_sync_command_config(command_payload=payload, request_port=8060))

    # Records pointed at the webhook before anything else, the test press
    # before the remote sync, and the listener never enabled.
    assert calls == ["create", "rewrite", "probe", "sync"]
    # A create: the remote sync for a new device (order-only + per-item on the X2), never the full resync.
    assert hub._remote_syncs[0]["created"] == (9,)
    device_id, bodies = hub._rewritten
    assert device_id == 9
    assert sorted(bodies) == list(range(1, 21))
    code_type, body = bodies[11]
    assert code_type == WIFI_URL_RECORD_CODE
    record = parse_wifi_https_blob_body(body)
    assert record["url"] == f"https://192.168.2.50:8123/api/webhook/{WEBHOOK_ID}?d=9&s=0&p=long"
    assert record["method"] == "POST"
    assert json.loads(record["body"]) == {"d": 9, "s": 0, "p": "long"}
    # The handler is registered, local only.
    assert ha_webhook.registered[WEBHOOK_ID]["local_only"] is True

    saved = store.saved[-1]
    assert saved["deployed_transport"] == "webhook"
    assert saved["deployed_url"] == f"https://192.168.2.50:8123/api/webhook/{WEBHOOK_ID}"
    assert saved["request_port"] is None
    assert result["delivery"]["status"] == "delivered"
    assert result["delivery"]["target"] == "https://192.168.2.50:8123"
    assert hub.get_command_sync_progress("default")["delivery"]["status"] == "delivered"
    loop.close()


def test_x1s_deploy_keeps_the_listener(monkeypatch) -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    calls: list[str] = []
    store = _DeployStore()
    hub = _x2_deploy_hub(monkeypatch, loop, store, calls)
    hub.version = "X1S"

    def _send_command(ent_id, key_code):
        calls.append("probe")
        loop.call_soon(lambda: hub._claim_wifi_probe(ent_id, 0, "short", transport="http", source_ip=HUB_IP))
        return True

    monkeypatch.setattr(hub._proxy, "send_command", _send_command)
    monkeypatch.setattr(wifi_deploy_module.WifiDeployMixin, "_async_wifi_listener_needed", lambda self: asyncio.sleep(0, result=True), raising=False)

    class _Listener:
        def get_last_start_error(self):
            return None

    import custom_components.sofabaton_x1s.roku_listener as roku_listener_module

    monkeypatch.setattr(roku_listener_module, "async_get_roku_listener", lambda _hass: asyncio.sleep(0, result=_Listener()))
    payload = {
        "commands": [{"name": "Command 1", "add_as_favorite": False, "hard_button": "", "activities": [], "action": {}}],
        "commands_hash": "abc",
    }
    result = loop.run_until_complete(hub.async_sync_command_config(command_payload=payload, request_port=8060))
    assert calls == ["listener:True", "create", "probe", "sync"]
    assert store.saved[-1]["deployed_transport"] == "http"
    assert store.saved[-1]["request_port"] == 8060
    assert "deployed_url" not in store.saved[-1]
    assert result["delivery"]["target"] == f"http://{HA_ADDRESS}:8060"
    loop.close()


# ── MQTT broker link ─────────────────────────────────────────────────────


def _mqtt_hass(loop, *, broker="core-mosquitto", port=1883):
    hass = FakeHass(loop)
    hass.config = SimpleNamespace(components={"mqtt"})
    entry = SimpleNamespace(data={"broker": broker, "port": port, "username": "ha", "password": "secret"})
    hass.config_entries.async_entries = lambda domain: [entry] if domain == "mqtt" else []
    return hass


def test_broker_link_state_maps_the_add_on_to_the_lan_address_and_hides_the_password() -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    hass = _mqtt_hass(loop)
    hub = SimpleNamespace(
        version="X2",
        local_address_in_use=lambda: HA_ADDRESS,
        _proxy=SimpleNamespace(query_hub_mqtt_status=lambda: False),
    )
    state = loop.run_until_complete(hub_mqtt_link.async_broker_link_state(hass, hub))
    assert state["suggested_host"] == HA_ADDRESS
    assert state["suggested_port"] == 1883
    assert state["ha_broker"] == {"host": "core-mosquitto", "port": 1883, "tls": False}
    assert state["hub_connected"] is False
    assert state["ha_username"] == "ha" and state["ha_login_available"]
    assert "secret" not in json.dumps(state)

    for host, expected in (("127.0.0.1", HA_ADDRESS), ("192.168.2.7", "192.168.2.7"), ("fe80::1", "")):
        assert loop.run_until_complete(hub_mqtt_link.async_hub_broker_address(hass, hub, host)) == expected
    # Not an X2: the hub is never asked.
    assert loop.run_until_complete(hub_mqtt_link.async_hub_mqtt_status(hass, SimpleNamespace(version="X1S"))) is None
    loop.close()


def test_pointing_the_hub_at_the_broker_sends_ha_login_by_default(monkeypatch) -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    hass = _mqtt_hass(loop)
    sent: list[dict] = []

    def _set_broker(**kwargs):
        sent.append(kwargs)
        return {"connected": True}

    hub = SimpleNamespace(_proxy=SimpleNamespace(set_hub_mqtt_broker=_set_broker), _hub_mqtt_connected_at=None)
    monkeypatch.setattr(hub_mqtt_link, "HUB_MQTT_SETTLE_S", 0)
    connected = loop.run_until_complete(
        hub_mqtt_link.async_point_hub_at_broker(hass, hub, host=HA_ADDRESS, port=1883)
    )
    assert connected is True
    assert sent == [{"broker_ip": HA_ADDRESS, "broker_port": 1883, "username": "ha", "password": "secret"}]
    assert hub._hub_mqtt_connected_at is not None
    loop.run_until_complete(
        hub_mqtt_link.async_point_hub_at_broker(
            hass, hub, host=HA_ADDRESS, port=1883, use_ha_login=False, username="hub", password="pw"
        )
    )
    assert sent[-1]["username"] == "hub" and sent[-1]["password"] == "pw"
    loop.close()


def test_mqtt_deploy_refuses_when_the_hub_has_no_broker(monkeypatch) -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    calls: list[str] = []
    store = _DeployStore()
    hub = _x2_deploy_hub(monkeypatch, loop, store, calls)
    hub.wifi_mqtt_available = lambda: True
    monkeypatch.setattr(hub_mqtt_link, "ha_mqtt_connected", lambda _hass: True)
    monkeypatch.setattr(hub._proxy, "query_hub_mqtt_status", lambda: False)
    payload = {
        "commands": [{"name": "Command 1", "add_as_favorite": False, "hard_button": "", "activities": [], "action": {}}],
        "commands_hash": "abc",
        "requested_transport": "mqtt",
    }
    with pytest.raises(wifi_deploy_module.WifiSyncError) as err:
        loop.run_until_complete(hub.async_sync_command_config(command_payload=payload, request_port=8060))
    assert err.value.code == "hub_mqtt_not_connected"
    # Nothing was written.
    assert calls == []
    assert hub.get_command_sync_progress("default")["error_code"] == "hub_mqtt_not_connected"
    loop.close()


# ── Move to direct delivery (in place) ───────────────────────────────────


MOVE_DEV_ID = 11


class _Listener:
    def get_last_start_error(self):
        return None


def _move_hub(monkeypatch, loop, store, calls, *, deployed_url=None):
    import custom_components.sofabaton_x1s.roku_listener as roku_listener_module

    hub = _x2_deploy_hub(monkeypatch, loop, store, calls)
    store.deployed_url = deployed_url
    monkeypatch.setattr(
        roku_listener_module, "async_get_roku_listener", lambda _hass: asyncio.sleep(0, result=_Listener())
    )

    async def _snapshot(*_a, **_k):
        return {MOVE_DEV_ID: {"brand": "m3-default-oldhash", "name": "Home Assistant"}}

    device_entry = {
        "device": {"device_id": MOVE_DEV_ID, "name": "Home Assistant", "brand": "m3-default-oldhash"},
        "commands": [
            {"command_id": 1, "command_label": "Command 1"},
            {"command_id": 11, "command_label": "Command 1 Long Press"},
        ],
        "input_record": {"entries": []},
        "macros": [{"button_id": 198, "steps": []}, {"button_id": 199, "steps": []}],
        "button_bindings": [],
    }

    def _run_plan(plan, progress_callback=None):
        calls.append(f"plan:{len(plan.steps)}")
        return {"status": "success", "completed_steps": len(plan.steps), "total_steps": len(plan.steps), "counters": {}}

    monkeypatch.setattr(hub, "_async_refresh_devices_snapshot", _snapshot)
    monkeypatch.setattr(hub._proxy, "backup_device", lambda *_a, **_k: device_entry)
    monkeypatch.setattr(hub._proxy, "backup_activity", lambda *_a, **_k: {})
    monkeypatch.setattr(hub._proxy, "run_wifi_inplace_plan", _run_plan)
    return hub


def _move_payload(**extra):
    return {
        "commands": [{"name": "Command 1", "add_as_favorite": False, "hard_button": "", "long_press_enabled": False,
                      "input_activity_id": "", "activities": [], "action": {}}],
        "commands_hash": "oldhash",
        "deployed_commands_hash": "oldhash",
        "deployed_device_id": MOVE_DEV_ID,
        "deployed_request_port": 8060,
        "deployed_transport": "http",
        "requested_transport": "http",
        **extra,
    }


def test_move_to_direct_rewrites_the_records_in_place(monkeypatch) -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    calls: list[str] = []
    store = _DeployStore()
    hub = _move_hub(monkeypatch, loop, store, calls)
    result = loop.run_until_complete(
        hub.async_sync_command_config(command_payload=_move_payload(), request_port=8060, move_to_direct=True)
    )
    assert result["inplace"] is True
    assert "create" not in calls
    # The rewrite, then the test press.
    assert calls.index("rewrite") < calls.index("probe")
    device_id, bodies = hub._rewritten
    assert device_id == MOVE_DEV_ID
    assert all(code == WIFI_URL_RECORD_CODE for code, _body in bodies.values())
    saved = store.saved[-1]
    assert saved["deployed_transport"] == "webhook"
    assert saved["deployed_url"] == f"https://192.168.2.50:8123/api/webhook/{WEBHOOK_ID}"
    assert saved["request_port"] is None
    assert result["delivery"]["status"] == "delivered"
    loop.close()


def test_a_listener_device_is_not_moved_without_asking(monkeypatch) -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    calls: list[str] = []
    store = _DeployStore()
    hub = _move_hub(monkeypatch, loop, store, calls)
    loop.run_until_complete(hub.async_sync_command_config(command_payload=_move_payload(), request_port=8060))
    assert "rewrite" not in calls
    assert "deployed_transport" not in store.saved[-1]
    assert store.saved[-1]["request_port"] == 8060
    loop.close()


def test_a_webhook_device_follows_a_new_address_in_place(monkeypatch) -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    calls: list[str] = []
    store = _DeployStore()
    hub = _move_hub(
        monkeypatch, loop, store, calls, deployed_url=f"https://192.168.2.40:8123/api/webhook/{WEBHOOK_ID}"
    )
    payload = _move_payload(deployed_transport="webhook", deployed_request_port=None)
    loop.run_until_complete(hub.async_sync_command_config(command_payload=payload, request_port=8060))
    assert "rewrite" in calls
    assert store.saved[-1]["deployed_url"] == f"https://192.168.2.50:8123/api/webhook/{WEBHOOK_ID}"
    # Same transport: only the URL is stamped.
    assert "deployed_transport" not in store.saved[-1]

    # Already calling the current URL: nothing rewritten.
    calls.clear()
    store.deployed_url = f"https://192.168.2.50:8123/api/webhook/{WEBHOOK_ID}"
    loop.run_until_complete(hub.async_sync_command_config(command_payload=payload, request_port=8060))
    assert "rewrite" not in calls
    loop.close()


# ── WS commands ──────────────────────────────────────────────────────────


def _ws_setup(monkeypatch, *, hub_version="X2", webhook=True):
    from tests.test_wifi_events_ws import _setup

    store, hub = _setup(monkeypatch, hub_version=hub_version)
    hub.host = HUB_IP
    hub.roku_server_enabled = False
    hub.wifi_webhook_available = lambda: webhook and hub_version == "X2"
    key = str(_run(store.async_create_hub_device("entry-1", "Lights", roku_listen_port=8060))["device_key"])
    return store, hub, key


def test_ws_delivery_names_the_path_never_the_webhook_id(monkeypatch) -> None:
    from tests.test_wifi_events_ws import _Conn, _msg

    store, hub, key = _ws_setup(monkeypatch)
    url = f"https://192.168.2.50:8123/api/webhook/{WEBHOOK_ID}"
    _run(
        store.async_save_deployed_wifi_commands(
            "entry-1", key, [{"name": "On"}], deployed_device_id=9, commands_hash="h",
            deployed_transport="webhook", deployed_url=url,
        )
    )
    _run(store.async_set_delivery("entry-1", key, {"status": "not_delivered", "transport": "webhook"}))
    conn = _Conn()
    _run(integration_module()._ws_get_wifi_delivery(None, conn, _msg(device_key=key)))
    result = conn.result[1]
    assert result["transport"] == "webhook"
    assert result["target"] == "https://192.168.2.50:8123"
    assert result["hub_ip"] == HUB_IP
    assert result["delivery"]["status"] == "not_delivered"
    assert result["move_available"] is False
    assert WEBHOOK_ID not in json.dumps(result)


def test_ws_move_to_direct_only_for_an_x2_listener_device(monkeypatch) -> None:
    from tests.test_wifi_events_ws import _Conn, _msg

    store, hub, key = _ws_setup(monkeypatch, hub_version="X1S")
    _run(
        store.async_save_deployed_wifi_commands(
            "entry-1", key, [{"name": "On"}], deployed_device_id=9, commands_hash="h",
            request_port=8060, deployed_transport="http",
        )
    )
    conn = _Conn()
    _run(integration_module()._ws_move_wifi_device_to_direct(None, conn, _msg(device_key=key)))
    assert conn.error[1] == "not_supported"
    assert hub.sync_calls == []

    store, hub, key = _ws_setup(monkeypatch, hub_version="X2")
    _run(
        store.async_save_deployed_wifi_commands(
            "entry-1", key, [{"name": "On"}], deployed_device_id=9, commands_hash="h",
            request_port=8060, deployed_transport="http",
        )
    )
    moves: list[dict] = []

    async def _sync(**kwargs):
        moves.append(kwargs)
        return {"status": "success", "inplace": True}

    hub.async_sync_command_config = _sync
    conn = _Conn()
    _run(integration_module()._ws_move_wifi_device_to_direct(None, conn, _msg(device_key=key)))
    assert conn.error is None
    assert moves[0]["move_to_direct"] is True


def test_ws_hub_broker_setup_is_x2_only_and_reports_bad_values(monkeypatch) -> None:
    from tests.test_wifi_events_ws import _Conn, _msg

    _store_, hub, _key = _ws_setup(monkeypatch, hub_version="X1S")
    conn = _Conn()
    _run(integration_module()._ws_set_hub_mqtt_broker(None, conn, _msg(host=HA_ADDRESS, port=1883)))
    assert conn.error[1] == "not_supported"

    _store_, hub, _key = _ws_setup(monkeypatch, hub_version="X2")

    async def _refuse(*_a, **_k):
        raise ValueError("the broker address must be an IPv4 address")

    monkeypatch.setattr(hub_mqtt_link, "async_point_hub_at_broker", _refuse)
    conn = _Conn()
    _run(integration_module()._ws_set_hub_mqtt_broker(None, conn, _msg(host="mqtt.lan", port=1883)))
    assert conn.error[1] == "invalid_format"


def test_ws_device_list_says_whether_direct_is_the_webhook(monkeypatch) -> None:
    from tests.test_wifi_events_ws import _Conn, _msg

    for version, expected in (("X2", True), ("X1S", False)):
        _store_, hub, _key = _ws_setup(monkeypatch, hub_version=version)
        hub.get_managed_command_hashes = lambda *_a, **_k: []
        hub.get_command_sync_progress = lambda *_a, **_k: {"status": "idle"}
        conn = _Conn()
        _run(integration_module()._ws_list_command_devices(None, conn, _msg()))
        assert conn.result[1]["webhook_available"] is expected


def integration_module():
    import importlib

    return importlib.import_module("custom_components.sofabaton_x1s.__init__")
