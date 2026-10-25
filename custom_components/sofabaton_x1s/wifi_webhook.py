"""Wifi Commands over Home Assistant's own webhooks (X2).

An X2 Wifi Device deployed "Direct to Home Assistant" holds URL records
(code type 0x21) that POST to
``<scheme>://<address>:<port>/api/webhook/<id>?d=<device>&s=<slot>&p=<press>``.
The X2 does not check certificates, so the address is the LAN address the
hub already uses for this host (no domain name needed) and the scheme and
port are Home Assistant's own, unless the hub's local address override
names others (a remapped Docker port, a proxy in front).

One webhook per hub entry: a random id kept in the command store, made on
the first webhook deploy. Calls are accepted only from the local network
(``local_only``) and only from the hub's own address or a source a
delivery test arrived from (a NAT between the hub's network and Home
Assistant's). The id sits in the hub's records and in backups, so it is
never logged and never sent to the frontend.
"""

from __future__ import annotations

import ipaddress
import json
import logging
from typing import Any

from aiohttp import web
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .command_config import async_get_command_config_store

_LOGGER = logging.getLogger(__name__)

WEBHOOK_NAME = "Sofabaton Wifi Commands"
WEBHOOK_PATH = "/api/webhook/"
#: Sources a hub's webhook accepts besides the hub's own address.
MAX_WEBHOOK_SOURCES = 4
_SCHEMES = ("http", "https")
_REGISTERED_KEY = "wifi_webhooks"


def webhook_transport_ready(hass: HomeAssistant) -> bool:
    """Whether Home Assistant can take webhook calls: the webhook
    integration is loaded and its HTTP server reported where it listens."""

    config = getattr(hass, "config", None)
    components = getattr(config, "components", ()) or ()
    return "webhook" in components and getattr(config, "api", None) is not None


class LocalEndpointError(ValueError):
    """An invalid local address or port, identified without parsing prose."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def parse_local_endpoint(value: Any) -> tuple[str | None, int | None, str | None]:
    """The hub's local address override as typed: ``(address, port, scheme)``.

    Accepts ``<IPv4>``, ``<IPv4>:<port>`` and either with ``http://`` or
    ``https://`` in front; blank returns everything to automatic
    ``(None, None, None)``. The port and scheme only change the URL an X2
    calls Home Assistant's webhook on. Raises ``ValueError`` otherwise.
    """

    text = str(value or "").strip()
    if not text:
        return None, None, None
    scheme: str | None = None
    lowered = text.lower()
    for candidate in _SCHEMES:
        prefix = f"{candidate}://"
        if lowered.startswith(prefix):
            scheme = candidate
            text = text[len(prefix):].rstrip("/")
            break
    host, sep, port_text = text.partition(":")
    try:
        address = str(ipaddress.IPv4Address(host.strip()))
    except ipaddress.AddressValueError as err:
        raise LocalEndpointError("invalid_address", "expected an IPv4 address with an optional HTTP scheme and port") from err
    port: int | None = None
    if sep:
        if not port_text.isdigit() or not (0 < int(port_text) < 65536):
            raise LocalEndpointError("invalid_port", "the port must be 1..65535")
        port = int(port_text)
    return address, port, scheme


def format_local_endpoint(address: str | None, port: int | None, scheme: str | None) -> str:
    """The override as the user typed it: the inverse of :func:`parse_local_endpoint`."""

    if not address:
        return ""
    text = f"{address}:{port}" if port else str(address)
    return f"{scheme}://{text}" if scheme else text


def webhook_endpoint(hass: HomeAssistant, hub: Any, address: str) -> str:
    """``<scheme>://<address>:<port>`` the hub reaches Home Assistant on:
    Home Assistant's own scheme and port unless the hub overrides them."""

    api = getattr(getattr(hass, "config", None), "api", None)
    scheme = getattr(hub, "local_scheme", None) or ("https" if getattr(api, "use_ssl", False) else "http")
    port = getattr(hub, "local_port", None) or int(getattr(api, "port", 8123) or 8123)
    return f"{scheme}://{address}:{int(port)}"


async def async_webhook_base_url(hass: HomeAssistant, hub: Any, *, create: bool = False) -> str | None:
    """The base URL this hub's URL records call, or ``None`` when Home
    Assistant has no webhook endpoint (or no id yet and ``create`` is off).

    ``create`` makes (and stores) the hub's webhook id on first use and
    registers its handler.
    """

    if not webhook_transport_ready(hass):
        return None
    store = await async_get_command_config_store(hass)
    if create:
        webhook_id = await store.async_ensure_webhook_id(hub.entry_id)
        async_register_hub_webhook(hass, hub.entry_id, webhook_id)
    else:
        webhook_id = store.get_webhook_id(hub.entry_id)
    if not webhook_id:
        return None
    # An interface and routing lookup: kept off the event loop.
    address = await hass.async_add_executor_job(hub.local_address_in_use)
    return f"{webhook_endpoint(hass, hub, address)}{WEBHOOK_PATH}{webhook_id}"


def async_register_hub_webhook(hass: HomeAssistant, entry_id: str, webhook_id: str) -> None:
    """Register the handler for one hub's webhook id (idempotent)."""

    registered: dict[str, str] = hass.data.setdefault(DOMAIN, {}).setdefault(_REGISTERED_KEY, {})
    if registered.get(entry_id) == webhook_id:
        return
    from homeassistant.components import webhook

    if entry_id in registered:
        webhook.async_unregister(hass, registered.pop(entry_id))
    try:
        webhook.async_register(
            hass,
            DOMAIN,
            WEBHOOK_NAME,
            webhook_id,
            _make_handler(entry_id),
            local_only=True,
            allowed_methods=["POST", "PUT"],
        )
    except ValueError:
        # Registered by an earlier setup of this entry that never unloaded.
        _LOGGER.debug("[%s] Wifi Commands webhook already registered", entry_id)
    registered[entry_id] = webhook_id


def async_unregister_hub_webhook(hass: HomeAssistant, entry_id: str) -> None:
    registered: dict[str, str] = hass.data.get(DOMAIN, {}).get(_REGISTERED_KEY, {})
    webhook_id = registered.pop(entry_id, None)
    if webhook_id is None:
        return
    from homeassistant.components import webhook

    webhook.async_unregister(hass, webhook_id)


async def async_setup_hub_webhook(hass: HomeAssistant, entry_id: str) -> None:
    """Register a hub's webhook at setup when it has one (a webhook deploy
    made it); hubs that never deployed one register nothing."""

    if not webhook_transport_ready(hass):
        return
    store = await async_get_command_config_store(hass)
    webhook_id = store.get_webhook_id(entry_id)
    if webhook_id:
        async_register_hub_webhook(hass, entry_id, webhook_id)


def parse_webhook_press(query: Any, body: Any) -> tuple[int, int, str] | None:
    """``(device id, slot index, press)`` of a call, from the query
    (``d``, ``s``, ``p``) or else the JSON body the records also carry."""

    def _from(source: Any) -> tuple[int, int, str] | None:
        try:
            device_id = int(source.get("d"))
            index = int(source.get("s"))
        except (AttributeError, TypeError, ValueError):
            return None
        press = str(source.get("p") or "short").lower()
        if device_id < 0 or index < 0 or press not in ("short", "long"):
            return None
        return device_id, index, press

    found = _from(query)
    if found is not None:
        return found
    try:
        data = json.loads(body) if body else None
    except (TypeError, ValueError):
        return None
    return _from(data) if isinstance(data, dict) else None


def _make_handler(entry_id: str):
    async def _async_handle(hass: HomeAssistant, webhook_id: str, request: web.Request) -> web.Response:
        del webhook_id  # never logged: it is the secret
        try:
            body = await request.text()
        except Exception:  # noqa: BLE001 - a broken body still answers
            body = ""
        press = parse_webhook_press(request.query, body)
        hub = hass.data.get(DOMAIN, {}).get(entry_id)
        if press is None or hub is None or not hasattr(hub, "async_handle_wifi_webhook_press"):
            _LOGGER.debug("[%s] Wifi Commands webhook call without a press ignored", entry_id)
            return web.Response(text="ignored")
        device_id, index, press_type = press
        await hub.async_handle_wifi_webhook_press(
            device_id=device_id,
            command_index=index,
            press_type=press_type,
            source_ip=str(request.remote or ""),
        )
        # The X2 accepts any 2xx and closes the connection itself (W2/W3).
        return web.Response(text="OK")

    return _async_handle
