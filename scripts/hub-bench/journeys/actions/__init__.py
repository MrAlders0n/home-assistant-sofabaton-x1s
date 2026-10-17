"""Actions: one user-level operation each (plan section 6.3).

A journey yields :class:`Spec` objects. A spec says what to do, what the
step may change (its allowance) and what the user asked for (expectations
for the intent model). The UI drivers of phase 1 will build the same specs;
in phase 0 every action goes through the integration's websocket API, the
same calls the card makes.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Callable

import ha_bench as hb

from ..world import Snapshot, World

HUB_LAYERS = ("cache", "hub")


def on_entities(*patterns: str) -> list[str]:
    """Where a change may show in this hub's HA entities (``entities``
    layer, ``<entity_id>/state`` or ``<entity_id>/attrs/<name>``)."""

    return [f"entities/{p}" for p in patterns]


#: Every place an activity's or a device's name shows in the entities.
ACTIVITY_NAME_IN_ENTITIES = on_entities(
    "remote.*/attrs/activity_list", "remote.*/attrs/activities", "remote.*/attrs/current_activity",
    "select.*_activity/attrs/options", "select.*_activity/state", "sensor.*_activity/state",
)
DEVICE_NAME_IN_ENTITIES = on_entities("remote.*/attrs/devices")


def on_hub(*patterns: str) -> list[str]:
    """The same pattern in the cache layer and the hub layer: a hub write
    changes both (the cache through the write's read-back)."""

    return [f"{layer}/{p}" for p in patterns for layer in HUB_LAYERS]


@dataclass
class Ctx:
    ws: hb.HaWs
    hub: hb.Hub
    world: World
    snapshot: Snapshot
    step: int = 0
    #: The acting browser tab (``actor.Actor``) when the journey drives the UI.
    actor: Any = None
    #: Names a journey learned on the way (a new Wifi device's key, the hub
    #: id it was deployed to, ...), for the specs of later steps.
    known: dict[str, Any] = field(default_factory=dict)

    def bundle(self) -> dict[str, Any]:
        """A private copy of the latest structural bundle (the editors'
        baseline)."""

        return copy.deepcopy(self.snapshot.cache_bundle or {})

    def cmd(self, kind: str, **extra: Any) -> Any:
        return self.ws.ok({"type": f"{hb.DOMAIN}/{kind}", "entry_id": self.hub.entry_id, **extra})


@dataclass
class Spec:
    name: str
    args: dict[str, Any]
    do: Callable[[Ctx], dict[str, Any]]
    allowance: list[str] = field(default_factory=list)
    #: (path, value, why): the intent model's new expectations.
    expect: list[tuple[str, Any, str]] = field(default_factory=list)
    forget: list[str] = field(default_factory=list)
    #: Read the hub (refresh) after this step.
    checkpoint: bool = True
    #: A step that must send no writes to the hub.
    read_only: bool = False
    #: Allowance and expectations that only exist after the action (a new
    #: device's id, a deployed Wifi device's hub id): called with the
    #: context and the before/after layers, returns (allowance, expect).
    late: Callable[[Ctx, dict[str, Any], dict[str, Any]], tuple[list[str], list[tuple]]] | None = None
    #: Checks that do not fit one path (an order, a macro found by name):
    #: called with the context and the after layers, returns problems.
    post: Callable[[Ctx, dict[str, Any]], list[str]] | None = None
