"""The integration's Home Assistant services (R6, CR-H2-13).

The 20 service handlers; they resolve their hub and guard it through
runtime.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN
from .lib import edits
from .lib.errors import HubBusyError, HubNotConnectedError
from .lib.payloads import IrPayload
from .lib.protocol_const import DEVICE_CLASS_IR
from .command_config import (
    CommandConfigStore,
    is_wifi_events_device_key,
    normalize_command_id_list,
    normalize_power_command_id,
)
from . import runtime
from . import entity_sync
from .ws_backup import (
    _validate_backup_device_ids,
)
from .ws_ir import (
    _validate_ir_command_name,
    _parse_play_ir_blob_input,
)
from .ws_wifi import (
    _validate_wifi_name_for_hub,
)

_LOGGER = logging.getLogger(__name__)


@runtime._hub_write_service()
async def _async_handle_fetch_device_commands(call: ServiceCall):
    hass = call.hass
    hub = await runtime._async_resolve_hub_from_call(hass, call)
    if hub is None:
        raise ValueError("Could not resolve Sofabaton hub from service call")

    ent_id = call.data["ent_id"]
    await hub.async_fetch_device_commands(ent_id)


@runtime._hub_write_service(persist=False)
async def _async_handle_dump_ir_commands(call: ServiceCall):
    hass = call.hass
    hub = await runtime._async_resolve_hub_from_call(hass, call)
    if hub is None:
        raise ValueError("Could not resolve Sofabaton hub from service call")


    device_id = int(call.data["device_id"])
    if device_id < 1 or device_id > 255:
        raise ValueError("device_id must be between 1 and 255")

    raw_command_id = call.data.get("command_id")
    command_id: int | None = None
    if raw_command_id is not None:
        command_id = int(raw_command_id)
        if command_id < 1 or command_id > 255:
            raise ValueError("command_id must be between 1 and 255")

    result = await hub.async_dump_ir_commands(device_id=device_id, command_id=command_id)
    if result is None:
        if command_id is None:
            raise ValueError(f"Hub did not respond to IR dump request for device {device_id}")
        raise ValueError(
            f"Hub did not respond to IR dump request for device {device_id}, command {command_id}"
        )
    return result


@runtime._hub_write_service(persist=False)
async def _async_handle_fetch_blob(call: ServiceCall):
    hass = call.hass
    hub = await runtime._async_resolve_hub_from_call(hass, call)
    if hub is None:
        raise ValueError("Could not resolve Sofabaton hub from service call")


    device_id = int(call.data["device_id"])
    if device_id < 1 or device_id > 255:
        raise ValueError("device_id must be between 1 and 255")

    raw_command_id = call.data.get("command_id")
    command_id: int | None = None
    if raw_command_id is not None:
        command_id = int(raw_command_id)
        if command_id < 1 or command_id > 255:
            raise ValueError("command_id must be between 1 and 255")

    result = await hub.async_fetch_blob(device_id=device_id, command_id=command_id)
    if result is None:
        if command_id is None:
            raise ValueError(f"Hub did not respond to blob fetch request for device {device_id}")
        raise ValueError(
            f"Hub did not respond to blob fetch request for device {device_id}, command {command_id}"
        )
    return result


@runtime._hub_write_service(persist=False)
async def _async_handle_backup_bundle(call: ServiceCall):
    """Service handler for ``sofabaton_x1s.backup_bundle``.

    ``device_ids`` (optional list of 1..255 integers) selects a
    device-only bundle; omit it (or pass an empty list) to back up
    everything (all devices + all activities). The return is a
    ``hub_bundle`` payload (see :meth:`SofabatonHub.async_backup_hub`).
    """

    hass = call.hass
    hub = await runtime._async_resolve_hub_from_call(hass, call)
    if hub is None:
        raise ValueError("Could not resolve Sofabaton hub from service call")

    device_ids = _validate_backup_device_ids(call.data.get("device_ids"))
    return await hub.async_backup_hub(device_ids=device_ids)


@runtime._hub_write_service(persist=False)
async def _async_handle_restore_backup(call: ServiceCall):
    """Service handler for ``sofabaton_x1s.restore_backup``.

    Accepts a ``hub_bundle`` payload (schema_version 5). Devices in
    the bundle are restored first; activities are restored second,
    after the hub has been erased
    (see :meth:`SofabatonHub.async_restore_backup`).
    """

    hass = call.hass
    hub = await runtime._async_resolve_hub_from_call(hass, call)
    if hub is None:
        raise ValueError("Could not resolve Sofabaton hub from service call")


    payload = call.data.get("backup")
    if not isinstance(payload, dict):
        raise ValueError(
            "backup must be an object payload returned by backup_bundle"
        )

    wifi_commands_request_port = runtime._resolve_roku_listen_port(hass, hub.entry_id)

    try:
        result = await hub.async_restore_backup(
            payload,
            wifi_commands_request_port=wifi_commands_request_port,
        )
    except ValueError as exc:
        raise HomeAssistantError(f"restore_backup validation failed: {exc}") from exc
    except Exception as exc:
        raise HomeAssistantError(f"restore_backup failed: {exc}") from exc
    if result is None:
        raise HomeAssistantError("Hub did not accept the restore transaction")
    return result


@runtime._hub_write_service(persist=False)
async def _async_handle_play_ir_blob(call: ServiceCall):
    hass = call.hass
    hub = await runtime._async_resolve_hub_from_call(hass, call)
    if hub is None:
        raise ValueError("Could not resolve Sofabaton hub from service call")


    blob_bytes = _parse_play_ir_blob_input(call.data.get("blob"))

    ok = await hub.async_play_ir_blob(blob_bytes)
    if not ok:
        raise HomeAssistantError("Hub is not ready to play IR blob (proxy client connected?)")


@runtime._hub_write_service(persist=False)
async def _async_handle_set_ir_learn_mode(call: ServiceCall):
    hass = call.hass
    hub = await runtime._async_resolve_hub_from_call(hass, call)
    if hub is None:
        raise ValueError("Could not resolve Sofabaton hub from service call")


    enabled = call.data.get("enabled")
    if not isinstance(enabled, bool):
        raise ValueError("enabled must be a boolean")

    ok = await hub.async_set_ir_learn_mode(enabled)
    if not ok:
        raise HomeAssistantError(
            "Hub did not accept the IR learn-mode toggle (proxy client connected?)"
        )


@runtime._hub_write_service(persist=False)
async def _async_handle_ir_learn_command(call: ServiceCall):
    hass = call.hass
    hub = await runtime._async_resolve_hub_from_call(hass, call)
    if hub is None:
        raise ValueError("Could not resolve Sofabaton hub from service call")


    timeout = call.data.get("timeout", 60)
    try:
        timeout = float(timeout)
    except (TypeError, ValueError) as exc:
        raise ValueError("timeout must be a number of seconds") from exc
    if timeout < 5 or timeout > 120:
        raise ValueError("timeout must be between 5 and 120 seconds")

    result = await hub.async_ir_learn_command(timeout=timeout)
    if result is None:
        raise HomeAssistantError(
            "Hub did not accept the IR learn-mode arm (proxy client connected?)"
        )
    return result


# The activity's power sequences: not quick-access entries.
_POWER_MACRO_IDS = frozenset({198, 199})


def _bundle_activity(bundle: dict[str, Any], activity_id: int) -> dict[str, Any]:
    for payload in bundle.get("activities") or []:
        if int((payload.get("device") or {}).get("device_id") or 0) & 0xFF == activity_id & 0xFF:
            return payload
    raise KeyError(f"activity {activity_id & 0xFF} is not in the bundle")


async def _async_service_entity_edit(
    hass: HomeAssistant,
    hub: Any,
    *,
    entity_kind: str,
    entity_id: int,
    edit: Callable[[dict[str, Any]], dict[str, Any]],
    label: str,
) -> dict[str, Any] | None:
    """A single-shot service as an edit of the cached bundle plus a sync
    (services migration S1): the one write path the live editors use.

    Refused while the hub is busy. The baseline is the facade's snapshot,
    the entity refreshed first when its cached copy is incomplete (read as
    hub work). The edit then runs through the ``sync_from_snapshot``
    service's gauntlet and engine entry point: payload validation, the
    operation the card's dock shows, the facade's sync (hold, stale
    preflight, read-back), the persist. An edit that changes nothing writes
    nothing (None). A rejected edit or a failed sync raises
    ``HomeAssistantError`` with its message.
    """

    runtime._raise_if_hub_operation_locked(hass, hub, label)
    async with runtime._hub_work(hub):
        baseline = await hub.async_editable_bundle(entity_kind, entity_id)
    try:
        edited = edit(baseline)
    except (ValueError, KeyError) as err:
        raise HomeAssistantError(f"{label}: {err.args[0] if err.args else err}") from err
    if edited == baseline:
        return None
    sync_input = {"baseline": baseline, "edited": edited, f"{entity_kind}_id": entity_id}
    try:
        operation_id, baseline, edited, entity_id = await entity_sync._async_prepare_entity_sync(
            hass,
            hub=hub,
            entity_kind=entity_kind,
            sync_input=sync_input,
            operation_label=label,
        )
    except entity_sync._EntitySyncRejected as err:
        raise HomeAssistantError(str(err)) from err
    result = await entity_sync._run_entity_sync_operation(
        hass,
        operation_id,
        hub=hub,
        baseline=baseline,
        edited=edited,
        entity_kind=entity_kind,
        entity_id=entity_id,
    )
    if isinstance(result, dict) and str(result.get("status") or "") == "failed":
        raise HomeAssistantError(f"{label}: {result.get('message') or 'Sync failed'}")
    return result


async def _async_handle_persist_ir_blob(call: ServiceCall):
    """A new command with a learned or built IR body on an IR device: the
    command added to the device's cached bundle, a device sync writes it
    (services migration S1)."""

    hass = call.hass
    hub = await runtime._async_resolve_hub_from_call(hass, call)
    if hub is None:
        raise ValueError("Could not resolve Sofabaton hub from service call")

    device_id = int(call.data["device_id"])
    if device_id < 1 or device_id > 255:
        raise ValueError("device_id must be between 1 and 255")

    command_name = _validate_ir_command_name(call.data.get("command_name"))
    blob_bytes = _parse_play_ir_blob_input(call.data.get("blob"))
    device_class = hub._get_cached_device_class(device_id)
    if device_class is not None and device_class != DEVICE_CLASS_IR:
        raise HomeAssistantError(
            f"persist_ir_blob only supports IR devices; device {device_id} is {device_class}"
        )

    added: dict[str, int] = {}

    def _edit(bundle: dict[str, Any]) -> dict[str, Any]:
        edited, command_id = edits.add_command(
            bundle, device_id, IrPayload.from_bytes(blob_bytes), command_name
        )
        added["command_id"] = command_id
        return edited

    await _async_service_entity_edit(
        hass, hub, entity_kind="device", entity_id=device_id, edit=_edit, label="persist_ir_blob"
    )
    return {
        "status": "success",
        "device_id": device_id,
        "command_id": added["command_id"],
        "command_name": command_name,
    }


@runtime._hub_write_service()
async def _async_handle_create_wifi_device(call: ServiceCall):
    hass = call.hass
    hub = await runtime._async_resolve_hub_from_call(hass, call)
    if hub is None:
        raise ValueError("Could not resolve Sofabaton hub from service call")


    device_name = _validate_wifi_name_for_hub(hub, call.data.get("device_name", "Home Assistant"), field_name="device_name")
    raw_commands = call.data.get("commands")
    if not isinstance(raw_commands, list):
        raise ValueError("commands must be a list of strings")
    if not raw_commands:
        raise ValueError("commands requires between 1 and 10 entries")
    if len(raw_commands) > 10:
        raise ValueError("commands requires between 1 and 10 entries")

    commands: list[str] = []
    for command in raw_commands:
        command_name = str(command).strip()
        if not command_name:
            raise ValueError("commands entries must not be empty")
        _validate_wifi_name_for_hub(hub, command_name, field_name="commands entries")
        commands.append(command_name)

    max_command_id = len(commands)
    raw_power_on_command_id = call.data.get("power_on_command_id")
    raw_power_off_command_id = call.data.get("power_off_command_id")
    raw_input_command_ids = call.data.get("input_command_ids")
    power_on_command_id = normalize_power_command_id(
        raw_power_on_command_id,
        max_command_id=max_command_id,
    )
    power_off_command_id = normalize_power_command_id(
        raw_power_off_command_id,
        max_command_id=max_command_id,
    )
    if raw_power_on_command_id is not None and power_on_command_id is None:
        raise ValueError(f"power_on_command_id must be between 1 and {max_command_id}")
    if raw_power_off_command_id is not None and power_off_command_id is None:
        raise ValueError(f"power_off_command_id must be between 1 and {max_command_id}")
    input_command_ids = normalize_command_id_list(
        raw_input_command_ids,
        max_command_id=max_command_id,
    )
    if raw_input_command_ids is not None and input_command_ids is None:
        raise ValueError(f"input_command_ids entries must each be between 1 and {max_command_id}")

    request_port = runtime._resolve_roku_listen_port(hass, hub.entry_id)

    return await hub.async_create_wifi_device(
        device_name=device_name,
        commands=commands,
        request_port=request_port,
        power_on_command_id=power_on_command_id,
        power_off_command_id=power_off_command_id,
        input_command_ids=input_command_ids,
    )


async def _async_handle_device_to_activity(call: ServiceCall):
    """Add a device to an activity, or change the input an existing member
    selects: the activity's power sequences edited, an activity sync writes
    them (services migration S1)."""

    hass = call.hass
    hub = await runtime._async_resolve_hub_from_call(hass, call)
    if hub is None:
        raise ValueError("Could not resolve Sofabaton hub from service call")

    activity_id = int(call.data["activity_id"])
    device_id = int(call.data["device_id"])
    input_command_id: int | None = call.data.get("input_command_id")
    if input_command_id is not None:
        input_command_id = int(input_command_id)

    if activity_id < 1 or activity_id > 255:
        raise ValueError("activity_id must be between 1 and 255")
    if device_id < 1 or device_id > 255:
        raise ValueError("device_id must be between 1 and 255")

    return await _async_service_entity_edit(
        hass,
        hub,
        entity_kind="activity",
        entity_id=activity_id,
        edit=lambda bundle: edits.add_member(
            bundle, activity_id, device_id, input_command_id=input_command_id
        ),
        label="device_to_activity",
    )


@runtime._hub_write_service()
async def _async_handle_delete_device(call: ServiceCall):
    hass = call.hass
    hub = await runtime._async_resolve_hub_from_call(hass, call)
    if hub is None:
        raise ValueError("Could not resolve Sofabaton hub from service call")


    device_id = int(call.data["device_id"])
    if device_id < 1 or device_id > 255:
        raise ValueError("device_id must be between 1 and 255")

    return await hub.async_delete_device(device_id=device_id)


async def _async_handle_command_to_favorite(call: ServiceCall):
    """Add a device command to an activity's favorites: the activity's
    cached bundle edited, an activity sync writes it (services migration
    S1). The hub assigns the favorite's id, so ``slot_id`` has no effect."""

    hass = call.hass
    hub = await runtime._async_resolve_hub_from_call(hass, call)
    if hub is None:
        raise ValueError("Could not resolve Sofabaton hub from service call")

    activity_id = int(call.data["activity_id"])
    device_id = int(call.data["device_id"])
    command_id = int(call.data["command_id"])
    raw_slot_id = call.data.get("slot_id")
    slot_id = int(raw_slot_id) if raw_slot_id is not None else None

    if activity_id < 1 or activity_id > 255:
        raise ValueError("activity_id must be between 1 and 255")
    if device_id < 1 or device_id > 255:
        raise ValueError("device_id must be between 1 and 255")
    if command_id < 1 or command_id > 255:
        raise ValueError("command_id must be between 1 and 255")
    if slot_id is not None and (slot_id < 0 or slot_id > 255):
        raise ValueError("slot_id must be between 0 and 255")
    if slot_id:
        _LOGGER.warning(
            "command_to_favorite: slot_id %s has no effect (the hub assigns the favorite's id)",
            slot_id,
        )

    return await _async_service_entity_edit(
        hass,
        hub,
        entity_kind="activity",
        entity_id=activity_id,
        edit=lambda bundle: edits.add_favorite(bundle, activity_id, device_id, command_id),
        label="command_to_favorite",
    )


@runtime._hub_write_service(persist=False)
async def _async_handle_get_favorites(call: ServiceCall):
    """The activity's quick-access list (favorites and macro shortcuts) in
    display order, read fresh through the facade's activity refresh."""

    hass = call.hass
    hub = await runtime._async_resolve_hub_from_call(hass, call)
    if hub is None:
        raise ValueError("Could not resolve Sofabaton hub from service call")

    activity_id = int(call.data["activity_id"])
    if activity_id < 1 or activity_id > 255:
        raise ValueError("activity_id must be between 1 and 255")

    try:
        await hub.async_refresh_entity_structure(kind="activity", ent_id=activity_id)
    except (TimeoutError, HubBusyError, HubNotConnectedError) as err:
        raise ValueError(
            f"Hub did not respond to favorites order request for activity {activity_id}"
        ) from err
    order = hub.cached_favorites_order(activity_id)
    if order is None:
        raise ValueError(f"Hub did not respond to favorites order request for activity {activity_id}")

    return {"favorites": hub.describe_favorites_order(activity_id, order)}


def _complete_quick_access_order(activity: dict[str, Any], ordered_ids: list[int]) -> list[int]:
    """Every quick-access id once: the ones asked for in that order (ids the
    activity does not have are skipped with a warning), then the rest in
    their current order. The engine wrote a partial list as given; the hub
    table always lists every entry now."""

    present: list[int] = []
    for row in activity.get("favorite_slots") or []:
        present.append(int(row.get("button_id", 0)) & 0xFF)
    for row in activity.get("macros") or []:
        macro_id = int(row.get("button_id", 0)) & 0xFF
        if macro_id not in _POWER_MACRO_IDS:
            present.append(macro_id)
    known = set(present)
    asked: list[int] = []
    for fav_id in ordered_ids:
        fav_lo = int(fav_id) & 0xFF
        if fav_lo not in known:
            _LOGGER.warning("reorder_favorites: fav_id %s is not in the activity; skipped", fav_lo)
            continue
        if fav_lo not in asked:
            asked.append(fav_lo)
    current = [int(i) & 0xFF for i in activity.get("favorites_order") or []]
    rest = [i for i in current if i in known and i not in asked]
    rest += [i for i in present if i not in asked and i not in rest]
    return asked + rest


async def _async_handle_reorder_favorites(call: ServiceCall):
    """Reorder an activity's quick-access list (favorites and macro
    shortcuts by their fav_id): the cached bundle's order edited, an
    activity sync writes it (services migration S1)."""

    hass = call.hass
    hub = await runtime._async_resolve_hub_from_call(hass, call)
    if hub is None:
        raise ValueError("Could not resolve Sofabaton hub from service call")

    activity_id = int(call.data["activity_id"])
    raw_order = call.data.get("ordered_fav_ids", call.data.get("order"))
    if raw_order is None:
        raise ValueError("ordered_fav_ids is required")
    ordered_fav_ids = [int(x) for x in raw_order]

    if activity_id < 1 or activity_id > 255:
        raise ValueError("activity_id must be between 1 and 255")
    if not ordered_fav_ids:
        raise ValueError("ordered_fav_ids must be a non-empty list of fav_ids")

    def _edit(bundle: dict[str, Any]) -> dict[str, Any]:
        activity = _bundle_activity(bundle, activity_id)
        return edits.reorder_favorites(
            bundle, activity_id, _complete_quick_access_order(activity, ordered_fav_ids)
        )

    return await _async_service_entity_edit(
        hass, hub, entity_kind="activity", entity_id=activity_id, edit=_edit,
        label="reorder_favorites",
    )


async def _async_handle_delete_favorite(call: ServiceCall):
    """Delete a quick-access entry by its fav_id: a favorite, or the macro a
    macro shortcut names (as the hub's delete always did); the cached
    bundle edited, an activity sync writes it (services migration S1)."""

    hass = call.hass
    hub = await runtime._async_resolve_hub_from_call(hass, call)
    if hub is None:
        raise ValueError("Could not resolve Sofabaton hub from service call")

    activity_id = int(call.data["activity_id"])
    raw_fav_id = call.data.get("fav_id", call.data.get("button_id"))
    if raw_fav_id is None:
        raise ValueError("fav_id is required")
    fav_id = int(raw_fav_id)

    if activity_id < 1 or activity_id > 255:
        raise ValueError("activity_id must be between 1 and 255")
    if fav_id < 1 or fav_id > 255:
        raise ValueError("fav_id must be between 1 and 255")

    def _edit(bundle: dict[str, Any]) -> dict[str, Any]:
        activity = _bundle_activity(bundle, activity_id)
        for row in activity.get("favorite_slots") or []:
            if int(row.get("button_id", 0)) & 0xFF == fav_id:
                return edits.remove_favorite(
                    bundle, activity_id, int(row.get("device_id", 0)), int(row.get("command_id", 0))
                )
        if fav_id not in _POWER_MACRO_IDS and any(
            int(row.get("button_id", 0)) & 0xFF == fav_id for row in activity.get("macros") or []
        ):
            return edits.remove_macro(bundle, activity_id, fav_id)
        raise ValueError(f"fav_id {fav_id} is not a favorite or macro of activity {activity_id}")

    return await _async_service_entity_edit(
        hass, hub, entity_kind="activity", entity_id=activity_id, edit=_edit,
        label="delete_favorite",
    )


async def _async_handle_command_to_button(call: ServiceCall):
    """Bind an activity button to a device command (with an optional long
    press): the cached bundle edited, an activity sync writes it (services
    migration S1). Without a complete long press any long press the button
    had is cleared, as the hub's keymap write always did."""

    hass = call.hass
    hub = await runtime._async_resolve_hub_from_call(hass, call)
    if hub is None:
        raise ValueError("Could not resolve Sofabaton hub from service call")

    activity_id = int(call.data["activity_id"])
    button_id = int(call.data["button_id"])
    device_id = int(call.data["device_id"])
    command_id = int(call.data["command_id"])

    if activity_id < 1 or activity_id > 255:
        raise ValueError("activity_id must be between 1 and 255")
    if button_id < 1 or button_id > 255:
        raise ValueError("button_id must be between 1 and 255")
    if device_id < 1 or device_id > 255:
        raise ValueError("device_id must be between 1 and 255")
    if command_id < 1 or command_id > 255:
        raise ValueError("command_id must be between 1 and 255")

    long_press_device_id = call.data.get("long_press_device_id")
    long_press_command_id = call.data.get("long_press_command_id")
    if long_press_device_id is not None:
        long_press_device_id = int(long_press_device_id)
        if long_press_device_id < 1 or long_press_device_id > 255:
            raise ValueError("long_press_device_id must be between 1 and 255")
    if long_press_command_id is not None:
        long_press_command_id = int(long_press_command_id)
        if long_press_command_id < 1 or long_press_command_id > 255:
            raise ValueError("long_press_command_id must be between 1 and 255")
    long_press = (
        (long_press_device_id, long_press_command_id)
        if long_press_device_id is not None and long_press_command_id is not None
        else None
    )

    return await _async_service_entity_edit(
        hass,
        hub,
        entity_kind="activity",
        entity_id=activity_id,
        edit=lambda bundle: edits.bind_button(
            bundle, activity_id, button_id, device_id, command_id, long_press=long_press
        ),
        label="command_to_button",
    )


async def _async_pick_wifi_device_key(
    store: CommandConfigStore, entry_id: str, wifi_device: str, *, roku_listen_port: int
) -> str | None:
    """The record a sync_command_config call means (CR-X2-6).

    ``wifi_device`` names it as the Wifi Commands tab shows it. Without a
    name, a hub with one Wifi Device deploys that one, and a hub with
    several refuses instead of silently deploying the first.
    """

    records = [
        record
        for record in await store.async_list_hub_devices(entry_id, roku_listen_port=roku_listen_port)
        if not is_wifi_events_device_key(record.get("device_key"))
    ]
    names = sorted(str(record.get("device_name") or "") for record in records)
    if wifi_device:
        wanted = wifi_device.casefold()
        matches = [
            record for record in records
            if str(record.get("device_name") or "").strip().casefold() == wanted
        ]
        if len(matches) != 1:
            raise HomeAssistantError(
                f"No single Wifi Device is named {wifi_device!r} (Wifi Devices: {', '.join(names)})"
            )
        return str(matches[0].get("device_key") or "") or None
    if len(records) > 1:
        raise HomeAssistantError(
            "This hub has several Wifi Devices; name one with wifi_device "
            f"(Wifi Devices: {', '.join(names)})"
        )
    return None


@runtime._hub_write_service(persist=False)
async def _async_handle_sync_command_config(call: ServiceCall):
    hass = call.hass
    hub = await runtime._async_resolve_hub_from_call(hass, call)
    if hub is None:
        raise ValueError("Could not resolve Sofabaton hub from service call")

    store = await runtime._async_get_command_config_store(hass)
    device_key = str(call.data.get("device_key") or "").strip() or None
    roku_listen_port = runtime._resolve_roku_listen_port(hass, hub.entry_id)
    if device_key is None:
        device_key = await _async_pick_wifi_device_key(
            store,
            hub.entry_id,
            str(call.data.get("wifi_device") or "").strip(),
            roku_listen_port=roku_listen_port,
        )

    payload = await store.async_get_hub_config(
        hub.entry_id,
        device_key=device_key,
        roku_listen_port=roku_listen_port,
    )
    request_port = roku_listen_port
    device_name = str(call.data.get("device_name") or payload.get("device_name") or "Home Assistant").strip() or "Home Assistant"

    return await hub.async_sync_command_config(
        command_payload=payload,
        request_port=request_port,
        device_key=str(payload.get("device_key") or device_key or ""),
        device_name=device_name,
    )


async def _async_handle_export_snapshot(call: ServiceCall):
    """Service handler for ``sofabaton_x1s.export_snapshot``.

    Wraps :meth:`SofabatonHub.async_get_structural_bundle` — the exact
    projection the live Control Panel editor reads (WS command
    ``sofabaton_x1s/cache/structural_bundle``, see
    ``_ws_get_structural_bundle``). It is a pure read from the cached
    proxy state: no hub I/O and no per-command IR blob dump, so it is
    cheap enough to call before every ``sync_from_snapshot`` and safe to
    call while the vendor app owns the hub.

    Deliberately not ``backup_bundle``: that action always does a live
    round trip to the hub (and, for a whole-hub backup, dumps every
    command's IR blob) — see ``docs/protocol/write-flows.md``.
    ``export_snapshot`` only returns what the integration already has
    cached; open the Control Panel's Hub or Activities tab once (or run
    a whole-hub cache refresh) to populate the cache on a fresh install.

    Response is ``{bundle, generation}`` — the same shape the WS command
    sends. ``generation`` is the cache generation the snapshot was
    exported at; pass it back as ``sync_from_snapshot``'s
    ``expected_generation`` to refuse a stale baseline loudly.
    """

    hass = call.hass
    hub = await runtime._async_resolve_hub_from_call(hass, call)
    if hub is None:
        raise ValueError("Could not resolve Sofabaton hub from service call")

    store = await runtime._async_get_persistent_cache_store(hass)
    if not store.enabled:
        raise HomeAssistantError(
            "export_snapshot requires the persistent cache to be enabled "
            "(Sofabaton X1S integration options)."
        )

    bundle = await hub.async_get_structural_bundle()
    if not bundle:
        raise HomeAssistantError(
            "No cached structural snapshot is available yet. Open the "
            "Control Panel's Hub or Activities tab once (or run a "
            "whole-hub cache refresh) to populate it, then retry."
        )
    return {"bundle": bundle, "generation": hub.cache_generation}


async def _async_handle_sync_from_snapshot(call: ServiceCall):
    """Service handler for ``sofabaton_x1s.sync_from_snapshot``.

    Script-callable counterpart of the live Control Panel editor's save
    action. Accepts the same ``baseline``/``edited`` hub_bundle pair the
    WS ``activity/sync`` and ``device/sync`` commands accept (see
    ``_validate_entity_sync_inputs``), routes through the identical
    pre-write gauntlet (``_async_prepare_entity_sync``: busy guard, lock
    guard, payload validation, operation-registry bookkeeping) and the
    identical engine entry point (``_run_entity_sync_operation`` ->
    ``hub.async_sync_activity``/``async_sync_device`` ->
    ``ActivitySyncMixin.sync_activity``/``sync_device``) the WS handler
    uses — so paging, label-slot reuse, and ack tolerance all apply
    exactly as they do from the Control Panel.

    Typical flow: call ``export_snapshot`` to get a bundle, keep the
    ``generation`` it returns, edit the two POWER_ON/POWER_OFF steps for
    one device in the returned JSON, then call this with the untouched
    export as ``baseline``, the edited copy as ``edited``, and that
    generation as ``expected_generation`` — the sync then refuses loudly
    if the hub cache has moved since the export instead of failing later
    (or writing against a rebased diff).
    """

    hass = call.hass
    hub = await runtime._async_resolve_hub_from_call(hass, call)
    if hub is None:
        raise ValueError("Could not resolve Sofabaton hub from service call")

    entity_kind = str(call.data.get("entity_kind") or "").strip().lower()
    if entity_kind not in ("activity", "device"):
        raise ValueError("entity_kind must be 'activity' or 'device'")

    baseline = call.data.get("baseline")
    edited = call.data.get("edited")
    if not isinstance(baseline, dict) or not isinstance(edited, dict):
        raise ValueError("baseline and edited must be hub_bundle objects")

    raw_generation = call.data.get("expected_generation")
    expected_generation: int | None = None
    if raw_generation is not None:
        try:
            expected_generation = int(raw_generation)
        except (TypeError, ValueError) as exc:
            raise ValueError("expected_generation must be an integer") from exc

    sync_input = {
        "baseline": baseline,
        "edited": edited,
        f"{entity_kind}_id": call.data.get("entity_id"),
    }

    try:
        operation_id, baseline, edited, entity_id = await entity_sync._async_prepare_entity_sync(
            hass,
            hub=hub,
            entity_kind=entity_kind,
            sync_input=sync_input,
            operation_label=f"sync_from_snapshot[{entity_kind}]",
            expected_generation=expected_generation,
        )
    except entity_sync._EntitySyncRejected as err:
        raise HomeAssistantError(str(err)) from err

    result = await entity_sync._run_entity_sync_operation(
        hass,
        operation_id,
        hub=hub,
        baseline=baseline,
        edited=edited,
        entity_kind=entity_kind,
        entity_id=entity_id,
    )
    if isinstance(result, dict) and str(result.get("status") or "") == "failed":
        raise HomeAssistantError(str(result.get("message") or "Sync failed"))
    return result


#: Every service: (name, handler, returns a response), in registration order.
#: One table for registration (first hub set up) and removal (last hub
#: unloaded); the two hand-kept 20-name lists had to change in lockstep.
SERVICES: tuple[tuple[str, Callable[[ServiceCall], Any], bool], ...] = (
    ("fetch_device_commands", _async_handle_fetch_device_commands, False),
    ("dump_ir_commands", _async_handle_dump_ir_commands, True),
    ("fetch_blob", _async_handle_fetch_blob, True),
    ("backup_bundle", _async_handle_backup_bundle, True),
    ("restore_backup", _async_handle_restore_backup, True),
    ("play_ir_blob", _async_handle_play_ir_blob, False),
    ("set_ir_learn_mode", _async_handle_set_ir_learn_mode, False),
    ("ir_learn_command", _async_handle_ir_learn_command, True),
    ("persist_ir_blob", _async_handle_persist_ir_blob, True),
    ("create_wifi_device", _async_handle_create_wifi_device, False),
    ("device_to_activity", _async_handle_device_to_activity, False),
    ("delete_device", _async_handle_delete_device, False),
    ("command_to_favorite", _async_handle_command_to_favorite, False),
    ("get_favorites", _async_handle_get_favorites, True),
    ("reorder_favorites", _async_handle_reorder_favorites, False),
    ("delete_favorite", _async_handle_delete_favorite, False),
    ("command_to_button", _async_handle_command_to_button, False),
    ("sync_command_config", _async_handle_sync_command_config, False),
    ("export_snapshot", _async_handle_export_snapshot, True),
    ("sync_from_snapshot", _async_handle_sync_from_snapshot, True),
)


def async_register_services(hass: HomeAssistant) -> None:
    """Register every service that is not registered yet."""

    for name, handler, responds in SERVICES:
        if hass.services.has_service(DOMAIN, name):
            continue
        if responds:
            hass.services.async_register(DOMAIN, name, handler, supports_response=SupportsResponse.OPTIONAL)
        else:
            hass.services.async_register(DOMAIN, name, handler)


def async_remove_services(hass: HomeAssistant) -> None:
    """Remove every service (the last hub unloaded)."""

    for name, _handler, _responds in SERVICES:
        hass.services.async_remove(DOMAIN, name)
