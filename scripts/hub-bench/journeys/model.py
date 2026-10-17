"""The intent model: what the user asked for, written down as they asked it.

Deliberately naive. An action records expectations ("activity 101's OK
button runs 4/13", "activity 101 is called J Watch TV"), and every
expectation holds until a later action replaces or forgets it. So step 5
breaking what step 2 asked for is caught at step 5, which is the #306
shape.

Never import production code here: a rule shared with the code under
test hides the bug it should reveal. The rules this model may encode are
the documented product rules only (plan, section 5).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable

from .frame import Path, _split

#: Expect a path to be gone (a deleted binding, a removed member).
ABSENT = "<absent>"
_NOTHING = object()

DEFAULT_LAYERS = ("cache", "hub")


@dataclass
class Expectation:
    path: Path
    value: Any
    step: int
    why: str
    layers: tuple[str, ...] = DEFAULT_LAYERS


@dataclass
class Miss:
    layer: str
    expectation: Expectation
    actual: Any

    def render(self) -> str:
        e = self.expectation
        actual = "(absent)" if self.actual is _NOTHING else json.dumps(self.actual, default=str)
        return (f"{self.layer}/{'/'.join(e.path)}: expected {json.dumps(e.value, default=str)}, "
                f"found {actual} (asked at step {e.step}: {e.why})")


def lookup(tree: Any, path: Path) -> Any:
    node = tree
    for seg in path:
        if not isinstance(node, dict) or seg not in node:
            return _NOTHING
        node = node[seg]
    return node


class IntentModel:
    def __init__(self) -> None:
        self.expectations: dict[Path, Expectation] = {}

    def expect(self, path: str, value: Any, *, step: int, why: str,
               layers: Iterable[str] = DEFAULT_LAYERS) -> None:
        key = _split(path)
        self.expectations[key] = Expectation(key, value, step, why, tuple(layers))

    def forget(self, prefix: str) -> None:
        """Drop every expectation at or below ``prefix`` (an action that
        legitimately ends them, such as deleting the entity)."""

        cut = _split(prefix)
        for key in [k for k in self.expectations if k[: len(cut)] == cut]:
            del self.expectations[key]

    def check(self, snapshot: dict[str, Any], layers: Iterable[str]) -> list[Miss]:
        misses = []
        for layer in layers:
            tree = snapshot.get(layer)
            if tree is None:
                continue
            for e in self.expectations.values():
                if layer not in e.layers:
                    continue
                actual = lookup(tree, e.path)
                if e.value == ABSENT:
                    if actual is not _NOTHING and actual is not None:
                        misses.append(Miss(layer, e, actual))
                elif actual is _NOTHING or _canon(actual) != _canon(e.value):
                    misses.append(Miss(layer, e, actual))
        return misses


def _canon(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)
