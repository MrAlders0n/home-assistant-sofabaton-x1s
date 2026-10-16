"""Reading the world, one layer at a time (plan section 3.2).

=====  =====================================================  ===========
layer  source                                                 cost
=====  =====================================================  ===========
ha        command_devices/list, wifi_event/list,              cheap
          hub_event_actions/get
cache     cache/structural_bundle (the integration's view)    cheap
entities  this hub's HA entity states (get_states)            cheap
          + the remote's cache_generation (what UIs watch)
persisted the persistent cache store on disk (Z: share)       cheap
wire      ``[SEND] hub`` lines in the HA log since a mark     cheap
hub    cache/refresh_all (reads every entity from the hub),   moderate
       then cache/structural_bundle
full   backup/export with blobs                               minutes
=====  =====================================================  ===========

``hub`` goes through the integration's own refresh path; the witness read
(plan layer 7) is not built yet.
"""

from __future__ import annotations

import copy
import json
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import ha_bench as hb

from .normalize import generation_of, normalize_bundle, normalize_entities, normalize_ha, normalize_persisted

LOGGERS = ("custom_components.sofabaton_x1s", "x1proxy")

#: Sends that only read. Everything else counts as a write (or a press:
#: REQ_ACTIVATE fires a command on the hub).
_READ_EXTRA = {"OP_0162", "ACTIVITY_DEVICE_CONFIRM"}


def is_read(opcode_name: str) -> bool:
    return (opcode_name.startswith("REQ_") and opcode_name != "REQ_ACTIVATE") or opcode_name in _READ_EXTRA


@dataclass
class Snapshot:
    """``layers``: read right after a step (ha, cache, entities; hub at a
    checkpoint). ``base``: the cheap layers read again after the checkpoint's
    hub refresh, which is the next step's "before" (a refresh may move them)."""

    layers: dict[str, Any]
    timings: dict[str, float] = field(default_factory=dict)
    cache_bundle: dict[str, Any] | None = None
    #: The integration's cache generation, from the structural bundle.
    generation: int | None = None
    #: The same counter as the open UIs see it: the remote entity's
    #: ``cache_generation`` attribute. The card reloads when it changes.
    entity_generation: int | None = None
    base: dict[str, Any] | None = None
    base_generation: int | None = None
    base_entity_generation: int | None = None

    def before(self) -> dict[str, Any]:
        return {**self.layers, **(self.base or {})}

    def gens(self) -> tuple[int | None, int | None]:
        if self.base is not None:
            return self.base_generation, self.base_entity_generation
        return self.generation, self.entity_generation


class World:
    def __init__(self, ws: hb.HaWs, hub: hb.Hub) -> None:
        self.ws = ws
        self.hub = hub
        self.entry = hub.entry_id
        registry = ws.ok({"type": "config/entity_registry/list"})
        self.entity_ids = {e["entity_id"] for e in registry
                           if e.get("config_entry_id") == self.entry and not e.get("disabled_by")}

    def _cmd(self, kind: str, **extra: Any) -> Any:
        return self.ws.ok({"type": f"{hb.DOMAIN}/{kind}", "entry_id": self.entry, **extra})

    # -- layers -----------------------------------------------------------------------------------

    def read_ha(self) -> dict[str, Any]:
        return normalize_ha(
            self._cmd("command_devices/list"),
            self._cmd("wifi_event/list"),
            self._cmd("hub_event_actions/get"),
        )

    def read_cache_raw(self) -> tuple[dict[str, Any] | None, int | None]:
        return hb.structural_bundle(self.ws, self.hub)

    def read_entities(self) -> tuple[dict[str, Any], int | None]:
        """This hub's HA entities, and the cache generation the open UIs see."""

        states = self.ws.ok({"type": "get_states"})
        return normalize_entities(states, self.entity_ids), generation_of(states, self.hub.remote)

    def read_persisted(self, attempts: int = 5) -> tuple[dict[str, Any] | None, int | None]:
        """This hub's entry in the persistent cache store on disk (what HA
        loads after a restart). A read that lands mid-write is retried."""

        for _ in range(attempts):
            try:
                data = json.loads(hb.CACHE_STORE.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                time.sleep(0.3)
                continue
            entry = ((data.get("data") or {}).get("hubs") or {}).get(self.entry)
            gen = (entry or {}).get("generation")
            return normalize_persisted(entry), (int(gen) if gen is not None else None)
        return None, None

    def refresh_hub(self) -> dict[str, Any]:
        """Re-read every entity from the hub through the integration
        (``cache/refresh_all``). Returns the operation's last event."""

        deadline = time.time() + 60
        while True:
            # A write's tail (read-back, remote sync) can still hold the hub
            # for a moment after its operation reported success.
            try:
                op_id = self._cmd("cache/refresh_all")["operation_id"]
                break
            except RuntimeError as err:
                if "busy" not in str(err) or time.time() > deadline:
                    raise
                time.sleep(3)
        events = hb.watch_operation(self.ws, op_id)
        final = events[-1] if events else {}
        if final.get("status") != "success":
            raise RuntimeError(f"cache refresh failed: {final.get('message') or final}")
        return final

    def full_export(self) -> tuple[dict[str, Any], dict[str, Any]]:
        final, bundle = hb.run_backup(self.ws, self.hub)
        if final.get("status") != "success":
            raise RuntimeError(f"backup export failed: {final.get('message') or final}")
        return normalize_bundle(bundle), bundle

    def snapshot(self, *, hub: bool = False) -> Snapshot:
        """``ha``, ``cache`` and ``entities`` always; ``hub`` (a full refresh
        first) when asked. The cache layer is read BEFORE the refresh, which
        is what makes a stale cache visible; ``hub`` is the cache read right
        after it."""

        timings: dict[str, float] = {}
        t0 = time.time()
        layers: dict[str, Any] = {"ha": self.read_ha()}
        timings["ha"] = time.time() - t0
        t0 = time.time()
        bundle, generation = self.read_cache_raw()
        layers["cache"] = normalize_bundle(bundle)
        timings["cache"] = time.time() - t0
        t0 = time.time()
        layers["entities"], entity_generation = self.read_entities()
        timings["entities"] = time.time() - t0
        snap = Snapshot(layers, timings, copy.deepcopy(bundle), generation, entity_generation)
        if hub:
            self.checkpoint(snap)
        return snap

    def checkpoint(self, snap: Snapshot) -> Snapshot:
        """Add the hub layer to ``snap`` (a full refresh, then the cache
        read), and re-read the cheap layers as the next step's base."""

        t0 = time.time()
        self.refresh_hub()
        fresh, snap.base_generation = self.read_cache_raw()
        snap.layers["hub"] = normalize_bundle(fresh)
        snap.timings["hub"] = time.time() - t0
        entities, snap.base_entity_generation = self.read_entities()
        snap.base = {"ha": self.read_ha(), "cache": snap.layers["hub"], "entities": entities}
        snap.cache_bundle = copy.deepcopy(fresh)
        return snap


# -- wire -----------------------------------------------------------------------------------------


_OPCODE = re.compile(r"\((0x[0-9A-Fa-f]{4})\)")


class Wire:
    """``[SEND] hub`` lines for one hub since a mark (debug logging)."""

    def __init__(self, entry_id: str) -> None:
        self.entry_id = entry_id
        self._mark = hb.log_mark()

    def mark(self) -> None:
        self._mark = hb.log_mark()

    def lines(self) -> list[str]:
        return hb.log_since(self._mark)

    def sends(self, lines: list[str] | None = None) -> list[str]:
        return hb.wire_sends(lines if lines is not None else self.lines(), self.entry_id)

    @staticmethod
    def summary(sends: list[str]) -> dict[str, Any]:
        counts = Counter(sends)
        writes = {name: n for name, n in counts.items() if not is_read(name)}
        return {"total": len(sends), "reads": sum(n for name, n in counts.items() if is_read(name)),
                "writes": writes, "counts": dict(counts)}


def set_debug_logging() -> None:
    """Integration and library loggers to debug (the wire layer needs the
    ``[SEND]`` lines). Idempotent."""

    hb.service("set_level", {name: "debug" for name in LOGGERS}, domain="logger")


def configured_levels() -> dict[str, str]:
    """The levels configuration.yaml gives our loggers (what a run puts
    back at the end)."""

    try:
        text = (hb.HA_CONFIG_DIR / "configuration.yaml").read_text(encoding="utf-8")
    except OSError:
        return {}
    levels = {}
    for name in LOGGERS:
        m = re.search(rf"^\s+{re.escape(name)}:\s*(\w+)\s*$", text, re.MULTILINE)
        if m:
            levels[name] = m.group(1)
    return levels


def restore_logging(levels: dict[str, str]) -> None:
    if levels:
        hb.service("set_level", levels, domain="logger")
