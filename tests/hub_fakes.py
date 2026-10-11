"""Shared fakes for the hub test modules (split from test_hub_commands.py, R6)."""

import asyncio
from types import SimpleNamespace

import custom_components.sofabaton_x1s.hub as hub_module


class FakeHass:
    def __init__(self, loop: asyncio.AbstractEventLoop):
        self.loop = loop
        self.data = {}
        self._entries = {}
        self.config_entries = SimpleNamespace(
            async_get_entry=self._async_get_entry,
            async_update_entry=self._async_update_entry,
        )

    async def async_add_executor_job(self, func, *args, **kwargs):  # pragma: no cover - passthrough
        return func(*args, **kwargs)

    def async_create_task(self, coro):  # pragma: no cover - passthrough
        return self.loop.create_task(coro)

    def _async_get_entry(self, entry_id):
        return self._entries.get(entry_id)

    def _async_update_entry(self, entry, *, data=None, options=None, title=None):
        if data is not None:
            entry.data = data
        if options is not None:
            entry.options = options
        if title is not None:
            entry.title = title


class FakeDeviceRegistry:
    def __init__(self, device=None):
        self.device = device
        self.updated = []

    def async_get_device(self, *, identifiers=None, connections=None):
        expected = {(hub_module.DOMAIN, "aa:bb:cc:dd:ee:ff")}
        if identifiers == expected:
            return self.device
        return None

    def async_update_device(self, device_id, **kwargs):
        self.updated.append((device_id, kwargs))


# ---------------------------------------------------------------------------
# Catalog reads through the facade (adoption plan L2): the engine is real,
# only the hub's reply is played in.
# ---------------------------------------------------------------------------


def make_controllable(proxy) -> None:
    """An unconnected engine that accepts requests: they enter its burst
    scheduler and reach no hub until a test lands the reply."""

    proxy.can_issue_commands = lambda: True


def land_catalog(proxy, kind: str, rows: dict) -> None:
    """Answer the in-flight ``devices`` / ``activities`` read with ``rows``
    ({id: row fields}; an activity row may carry ``payload`` bytes) and end
    the burst, as the frame thread would."""

    for idx, (ent_id, row) in enumerate(sorted(rows.items()), start=1):
        if kind == "devices":
            assert proxy.ingest_device_row(row_idx=idx, expected_rows=len(rows), dev_id=ent_id, device=dict(row))
        else:
            fields = {k: v for k, v in row.items() if k != "payload"}
            activity = {"id": ent_id, "active": False, "needs_confirm": False, **fields}
            assert proxy.ingest_activity_row(
                row_idx=idx, expected_rows=len(rows), act_id=ent_id, activity=activity,
                payload=row.get("payload"),
            )
    if not rows:
        if kind == "devices":
            proxy._device_pending_expected_rows = 0
        else:
            proxy._activity_pending_expected_rows = 0
    proxy._burst.finish(kind, can_issue=proxy.can_issue_commands, sender=proxy._send_cmd_frame)


async def read_answered(coro, proxy, kind: str, rows: dict):
    """Run a catalog read and answer it once its request is on the wire."""

    task = asyncio.ensure_future(coro)
    for _ in range(200):
        if task.done() or (proxy._burst.active and proxy._burst.kind == kind):
            break
        await asyncio.sleep(0.01)
    if not task.done():
        land_catalog(proxy, kind, rows)
    return await task
