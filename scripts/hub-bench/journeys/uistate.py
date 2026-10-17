"""What the passive tabs show, judged: locks and convergence (plan 1a).

Pure functions over what ``ui_reader.js`` returns, so they test without a
browser.

Lock: the write controls of the visible Hub tab (Add ..., Change order,
Edit ..., Refresh ...) are ``locked`` when all are disabled, ``unlocked``
when none are, ``partial`` otherwise. The tab buttons are navigation, not
writes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

WRITE_PREFIXES = ("Add ", "Change order", "Edit ", "Refresh")

#: A lock that shows up later than this after the backend reported the
#: operation is a finding. The backend pushes its state (hub_state, at
#: most 0.5 s behind); the sampler adds up to one interval.
MAX_LOCK_LAG_S = 1.0
#: A lock still on this long after the backend went idle is stuck.
MAX_UNLOCK_LAG_S = 2.0
#: Operations shorter than this may legitimately end before any poll sees
#: them, so a tab that never locked is not a finding for them.
MIN_VISIBLE_OP_S = 1.5


def write_controls(ui: dict[str, Any] | None) -> list[dict[str, Any]]:
    return [c for c in (ui or {}).get("controls") or [] if str(c.get("label") or "").startswith(WRITE_PREFIXES)]


def lock_state(ui: dict[str, Any] | None) -> str:
    if not ui or ui.get("tab") is None:
        return "absent"
    controls = write_controls(ui)
    if not controls:
        return "none"
    disabled = sum(1 for c in controls if c.get("disabled"))
    if disabled == len(controls):
        return "locked"
    return "unlocked" if disabled == 0 else "partial"


def expected_view(world: dict[str, Any], sub: str) -> dict[str, Any]:
    """What the Hub tab's ``sub`` list (Activities / Devices) should show,
    from a cache or hub layer."""

    kind = "activities" if sub == "Activities" else "devices"
    ids = list((world.get("order") or {}).get(kind) or [])
    entities = world.get(kind) or {}
    return {
        "ids": ids,
        "names": [((entities.get(str(i)) or {}).get("head") or {}).get("name") for i in ids],
        "counts": {"Activities": len(world.get("activities") or {}), "Devices": len(world.get("devices") or {})},
    }


def shown_view(ui: dict[str, Any] | None) -> dict[str, Any]:
    entities = (ui or {}).get("entities") or []
    return {
        "ids": [e.get("id") for e in entities],
        "names": [e.get("name") for e in entities],
        "counts": dict((ui or {}).get("counts") or {}),
    }


def view_problems(ui: dict[str, Any] | None, expected: dict[str, Any]) -> list[str]:
    """Differences between a tab's list and the cache. A tab that shows old
    data says whether it at least shows the stale banner (the user has to
    press Refresh) or nothing at all (silently stale)."""

    shown = shown_view(ui)
    problems = []
    if shown["names"] != expected["names"] or shown["ids"] != expected["ids"]:
        problems.append(f"list shows {list(zip(shown['ids'], shown['names']))}, "
                        f"cache has {list(zip(expected['ids'], expected['names']))}")
    for label, n in expected["counts"].items():
        if label in shown["counts"] and shown["counts"][label] != n:
            problems.append(f"{label} count shows {shown['counts'][label]}, cache has {n}")
    if problems:
        tag = "STALE BANNER shown (the old model)" if (ui or {}).get("staleBanner") else "not followed yet"
        problems = [f"{tag}: {p}" for p in problems]
    return problems


@dataclass
class LockTimeline:
    """Samples taken while one step ran: backend runtime state and each
    passive tab's lock state, at seconds since the action started."""

    samples: list[tuple[float, str, dict[str, str], dict[str, str | None]]] = field(default_factory=list)
    action_end: float | None = None

    def add(self, t: float, backend: str, locks: dict[str, str], docks: dict[str, str | None]) -> None:
        self.samples.append((round(t, 2), backend, dict(locks), dict(docks)))

    def changes(self) -> list[tuple[float, str, dict[str, str], dict[str, str | None]]]:
        out, prev = [], None
        for sample in self.samples:
            if prev is None or sample[1:] != prev[1:]:
                out.append(sample)
            prev = sample
        return out

    def analyze(self) -> tuple[dict[str, Any], list[str]]:
        running = [t for t, backend, _l, _d in self.samples if backend == "running"]
        report: dict[str, Any] = {"backend_running": [running[0], running[-1]] if running else None,
                                  "action_end": self.action_end, "pages": {}}
        problems: list[str] = []
        pages = sorted({p for _t, _b, locks, _d in self.samples for p in locks})
        idle_after = next((t for t, backend, _l, _d in self.samples
                           if running and t > running[-1] and backend != "running"), None)
        for page in pages:
            states = [(t, locks.get(page)) for t, _b, locks, _d in self.samples]
            first_lock = next((t for t, s in states if s == "locked"), None)
            partial = [t for t, s in states if s == "partial"]
            last = states[-1][1] if states else None
            unlocked_at = None
            if idle_after is not None:
                unlocked_at = next((t for t, s in states if t >= idle_after and s == "unlocked"), None)
            info = {"first_locked": first_lock, "partial_at": partial[:3], "final": last,
                    "lock_lag": round(first_lock - running[0], 2) if (first_lock is not None and running) else None,
                    "unlock_lag": round(unlocked_at - idle_after, 2) if (unlocked_at is not None and idle_after is not None) else None}
            report["pages"][page] = info
            span = (running[-1] - running[0]) if running else 0.0
            if running and span >= MIN_VISIBLE_OP_S and first_lock is None:
                problems.append(f"{page} tab never locked while the hub was busy for {span:.1f} s")
            if info["lock_lag"] is not None and info["lock_lag"] > MAX_LOCK_LAG_S:
                problems.append(f"{page} tab locked {info['lock_lag']} s after the operation started")
            if last in ("locked", "partial"):
                problems.append(f"{page} tab still {last} at the end of the watch (stuck lock)")
            elif info["unlock_lag"] is not None and info["unlock_lag"] > MAX_UNLOCK_LAG_S:
                problems.append(f"{page} tab unlocked {info['unlock_lag']} s after the hub went idle")
            if partial and running:
                problems.append(f"{page} tab partly locked during the operation (some write controls stayed enabled)")
        return report, problems
