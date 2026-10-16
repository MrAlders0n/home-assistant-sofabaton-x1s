"""The frame check: what changed that the step was not allowed to change.

A snapshot is a nested dict, one top-level key per layer (``ha``,
``cache``, ``hub``). It is flattened to ``path tuple -> leaf value``;
lists are leaves (an order is part of the value). A step's allowance is a
list of path patterns, segments separated by ``/``:

- ``*`` matches exactly one segment, ``**`` any number (also none); a
  segment may itself be a glob (``select.*_activity``);
- a pattern also covers everything below the path it matches, so
  ``cache/activities/101/bindings`` allows any binding change there.

Every change outside the allowance is a finding. Changes inside it are
reported too (as "allowed"), so a trace shows what each step really did.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import Any, Iterable

Path = tuple[str, ...]
_MISSING = object()


def flatten(value: Any, prefix: Path = ()) -> dict[Path, Any]:
    if isinstance(value, dict):
        out: dict[Path, Any] = {}
        for key, child in value.items():
            out.update(flatten(child, (*prefix, str(key))))
        if not value and prefix:
            out[prefix] = {}
        return out
    return {prefix: value}


@dataclass(frozen=True)
class Change:
    path: Path
    before: Any
    after: Any

    @property
    def text(self) -> str:
        return "/".join(self.path)

    def render(self, names: dict[str, str] | None = None) -> str:
        def show(value: Any) -> str:
            if value is _MISSING:
                return "(absent)"
            text = json.dumps(value, sort_keys=True, default=str)
            if names and isinstance(value, str) and value in names:
                text += f" [{names[value]}]"
            return text if len(text) <= 160 else text[:157] + "..."

        where = self.text
        if names:
            hints = [names[seg] for seg in self.path if seg in names]
            if hints:
                where += f" ({', '.join(dict.fromkeys(hints))})"
        return f"{where}: {show(self.before)} -> {show(self.after)}"


def diff(before: dict[str, Any], after: dict[str, Any]) -> list[Change]:
    a, b = flatten(before), flatten(after)
    changes = []
    for path in sorted(set(a) | set(b)):
        old, new = a.get(path, _MISSING), b.get(path, _MISSING)
        if old is _MISSING or new is _MISSING or _canon(old) != _canon(new):
            changes.append(Change(path, old, new))
    return changes


def _canon(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def _split(pattern: str) -> Path:
    return tuple(seg for seg in pattern.strip("/").split("/") if seg)


def _match(pattern: Path, path: Path) -> bool:
    """Does ``pattern`` match ``path`` or one of its ancestors?"""

    if not pattern:
        return True
    head, rest = pattern[0], pattern[1:]
    if head == "**":
        return any(_match(rest, path[i:]) for i in range(len(path) + 1))
    if not path:
        return False
    if head != "*" and not fnmatchcase(path[0], head):
        return False
    return _match(rest, path[1:])


def allowed(path: Path, allowance: Iterable[str]) -> bool:
    return any(_match(_split(p), path) for p in allowance)


@dataclass
class FrameResult:
    allowed: list[Change]
    findings: list[Change]

    @property
    def ok(self) -> bool:
        return not self.findings


def check(before: dict[str, Any], after: dict[str, Any], allowance: Iterable[str],
          layers: Iterable[str] | None = None) -> FrameResult:
    """Diff two snapshots over ``layers`` (all shared ones by default) and
    split the changes into allowed and findings."""

    allowance = list(allowance)
    names = set(before) & set(after) if layers is None else set(layers)
    result = FrameResult([], [])
    for layer in sorted(names):
        for change in diff({layer: before.get(layer, {})}, {layer: after.get(layer, {})}):
            (result.allowed if allowed(change.path, allowance) else result.findings).append(change)
    return result
