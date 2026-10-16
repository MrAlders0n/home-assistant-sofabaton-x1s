"""Runs a journey: steps, the three questions, checkpoints, the trace.

Per step (plan section 1a):

1. mark the HA log; run the action in a worker thread while the main
   thread samples, every 250 ms, the backend's ``runtime_state`` for the hub
   and the lock state of each passive tab; keep sampling after the action
   until the backend is idle and the tabs are unlocked (at most 15 s);
2. read ``ha``, ``cache`` and ``entities``;
3. question 1, the cache: the frame check over ``ha``, ``cache`` and
   ``entities`` against the step's allowance; the cache generation the open
   UIs see must have moved when the cache did; the persisted store must
   catch up with the cache (within 10 s);
4. question 2, the lock: the timeline judged by ``uistate.LockTimeline``;
5. question 3, the shown state: each passive tab must show the cache's
   lists (within 15 s), without a reload;
6. at a checkpoint: the hub refresh, the hub frame check, cache == hub;
7. intent, wire and log checks as in phase 0.

A failed action or any finding is recorded and the journey continues; the
exit code says whether anything was found.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any, Callable, Iterator

import ha_bench as hb

from .actions import Ctx, Spec
from .frame import check
from .model import IntentModel
from .normalize import labeler, project_like_persisted
from .uistate import LockTimeline, expected_view, lock_state, view_problems
from .world import Snapshot, Wire, World, configured_levels, restore_logging, set_debug_logging

Journey = Callable[[Ctx], Iterator[Spec]]
OUT = hb.OUT / "journeys"

SAMPLE_S = 0.25
LOCK_TAIL_S = 15.0
PERSIST_WAIT_S = 10.0
CONVERGE_WAIT_S = 15.0
#: Converging slower than this (after the snapshot was read) is a finding:
#: the push and the contents reload take about a second.
CONVERGE_OK_S = 2.0


def _jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


class Runner:
    def __init__(self, name: str, hub_which: str, *, ui: bool = True, headed: bool = False,
                 actor: bool = False) -> None:
        self.name = name
        self.ws = hb.wait_for_ha()
        self.hub = hb.resolve_hub(self.ws, hub_which)
        self.world = World(self.ws, self.hub)
        self.model = IntentModel()
        self.sampler_ws = hb.HaWs()
        self.ui_enabled = ui
        self.headed = headed
        self.ui = None
        self.actor_enabled = actor
        self.actor = None
        stamp = time.strftime("%Y%m%d_%H%M%S")
        self.dir = OUT / f"{stamp}_{name}_{self.hub.version.lower()}"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.records: list[dict[str, Any]] = []
        self.findings = 0

    def _write(self, name: str, payload: Any) -> None:
        (self.dir / name).write_text(json.dumps(_jsonable(payload), indent=1), encoding="utf-8")

    # -- the run ----------------------------------------------------------------------------------

    def run(self, journey: Journey) -> int:
        levels = configured_levels()
        set_debug_logging()
        wire = Wire(self.hub.entry_id)
        print(f"\n== {self.name} on {self.hub.version} {self.hub.name!r}: start snapshot (with hub read)", flush=True)
        snap = self.world.snapshot(hub=True)
        self._write("snapshot_000.json", snap.before())
        ctx = Ctx(self.ws, self.hub, self.world, snap)
        if self.actor_enabled:
            from .actor import Actor
            print("   opening the acting tab", flush=True)
            self.actor = ctx.actor = Actor(self.hub, headed=self.headed).start()
        last_hub = snap.layers["hub"]
        pending_hub_allowance: list[str] = []
        start_problems: list[str] = []
        try:
            if self.ui_enabled:
                from .ui import UiObserver
                print("   opening the passive tabs", flush=True)
                self.ui = UiObserver(self.hub, headed=self.headed).start()
                _ok, start_problems, waited = self._converge(snap.before()["cache"])
                print(f"   passive tabs {'agree with' if not start_problems else 'DIFFER from'} the cache "
                      f"({waited:.1f} s)", flush=True)
                for line in start_problems:
                    print(f"   FINDING  start: {line}", flush=True)
            for spec in journey(ctx):
                ctx.step += 1
                self._step(ctx, spec, wire, last_hub, pending_hub_allowance)
                if "hub" in ctx.snapshot.layers:
                    last_hub = ctx.snapshot.layers["hub"]
                    pending_hub_allowance = []
                else:
                    pending_hub_allowance += spec.allowance
        finally:
            restore_logging(levels)
            if self.actor:
                self.actor.close()
            if self.ui:
                self.ui.close()
            self._write("trace.json", {"journey": self.name, "hub": self.hub.version,
                                       "hub_name": self.hub.name, "entry": self.hub.entry_id,
                                       "start_problems": start_problems, "steps": self.records})
        if start_problems:
            self.findings += 1
        print(f"\n{len(self.records)} steps, {self.findings} with findings; trace in {self.dir}", flush=True)
        return 1 if self.findings else 0

    # -- sampling ---------------------------------------------------------------------------------

    def _backend(self) -> str:
        try:
            for h in self.sampler_ws.ok({"type": f"{hb.DOMAIN}/control_panel/state"}).get("hubs") or []:
                if h.get("entry_id") == self.hub.entry_id:
                    kind = (h.get("runtime_state") or {}).get("kind")
                    return "running" if kind == "operation_running" else str(kind or "idle")
        except Exception:  # noqa: BLE001
            return "unknown"
        return "unknown"

    def _sample(self, timeline: LockTimeline, t0: float) -> tuple[str, dict[str, str]]:
        backend = self._backend()
        states = self.ui.read() if self.ui else {}
        locks = {name: lock_state(state) for name, state in states.items()}
        docks = {name: (state or {}).get("dock") for name, state in states.items()}
        timeline.add(time.time() - t0, backend, locks, docks)
        return backend, locks

    def _run_sampled(self, ctx: Ctx, spec: Spec) -> tuple[dict[str, Any], float, LockTimeline]:
        timeline = LockTimeline()
        box: dict[str, Any] = {}

        def work() -> None:
            try:
                box["info"] = spec.do(ctx)
            except Exception as err:  # noqa: BLE001 - a failed action is a result, not a crash
                box["info"] = {"status": "error", "message": f"{type(err).__name__}: {err}"}

        t0 = time.time()
        worker = threading.Thread(target=work, daemon=True)
        worker.start()
        while worker.is_alive():
            self._sample(timeline, t0)
            time.sleep(SAMPLE_S)
        worker.join()
        action_s = time.time() - t0
        timeline.action_end = round(action_s, 2)
        # The tail: until the backend is idle and no tab is (partly) locked.
        tail_end = time.time() + LOCK_TAIL_S
        while time.time() < tail_end:
            backend, locks = self._sample(timeline, t0)
            if backend != "running" and all(s not in ("locked", "partial") for s in locks.values()):
                break
            time.sleep(SAMPLE_S)
        return box.get("info") or {"status": "error"}, action_s, timeline

    def _quiet_cache(self, hold: float = 3.0, limit: float = 20.0) -> float:
        """Wait until the cache generation has held still for ``hold``
        seconds: work the integration finishes in the background after an
        action (a re-read, a cache prune) gets its chance before the cache
        is judged. A cache that is still wrong after that is stale."""

        t0 = time.time()
        last, since = None, time.time()
        while time.time() - t0 < limit:
            _bundle, gen = self.world.read_cache_raw()
            if gen != last:
                last, since = gen, time.time()
            elif time.time() - since >= hold:
                break
            time.sleep(0.5)
        return time.time() - t0

    def _settle(self) -> tuple[float, list[str]]:
        """Before an action: wait until the backend is idle and no tab is
        (partly) locked, so the lock the action causes is measured fresh."""

        if not self.ui:
            return 0.0, []
        t0 = time.time()
        while True:
            backend = self._backend()
            locks = {name: lock_state(state) for name, state in self.ui.read().items()}
            if backend != "running" and all(s not in ("locked", "partial") for s in locks.values()):
                return time.time() - t0, []
            if time.time() - t0 > LOCK_TAIL_S:
                return time.time() - t0, [f"before the step, still {locks} with the backend {backend} "
                                          f"after {LOCK_TAIL_S:.0f} s (stuck lock)"]
            time.sleep(SAMPLE_S)

    def _converge(self, cache: dict[str, Any]) -> tuple[bool, list[str], float]:
        """Wait until every passive tab shows the cache's list."""

        t0 = time.time()
        problems: list[str] = []
        while True:
            problems = []
            for name, state in (self.ui.read() if self.ui else {}).items():
                problems += [f"{name} tab: {p}" for p in view_problems(state, expected_view(cache, name))]
            if not problems or time.time() - t0 > CONVERGE_WAIT_S:
                return not problems, problems, time.time() - t0
            time.sleep(0.5)

    def _persisted(self, cache: dict[str, Any]) -> tuple[list[str], float, int | None]:
        """Wait until the persisted store carries what the cache carries."""

        t0 = time.time()
        want = {"x": project_like_persisted(cache)}
        while True:
            persisted, gen = self.world.read_persisted()
            diff = check(want, {"x": persisted or {}}, [], layers=("x",)) if persisted is not None else None
            if diff is not None and diff.ok:
                return [], time.time() - t0, gen
            if time.time() - t0 > PERSIST_WAIT_S:
                lines = [c.render() for c in diff.findings[:10]] if diff else ["the store could not be read"]
                return lines, time.time() - t0, gen
            time.sleep(1.0)

    # -- one step ---------------------------------------------------------------------------------

    def _step(self, ctx: Ctx, spec: Spec, wire: Wire, last_hub: dict[str, Any],
              pending_hub_allowance: list[str]) -> None:
        print(f"\n-- step {ctx.step}: {spec.name} {json.dumps(spec.args, default=str)}", flush=True)
        before_snap: Snapshot = ctx.snapshot
        before = before_snap.before()
        gen_before, entity_gen_before = before_snap.gens()
        settle_s, settle_problems = self._settle()
        wire.mark()
        info, action_s, timeline = self._run_sampled(ctx, spec)
        quiet_s = self._quiet_cache()

        after = self.world.snapshot(hub=False)
        if spec.late is not None:
            try:
                late_allow, late_expect = spec.late(ctx, before, after.layers)
            except Exception as err:  # noqa: BLE001
                late_allow, late_expect = [], []
                info = {**info, "late_error": f"{type(err).__name__}: {err}"}
            spec.allowance = list(spec.allowance) + list(late_allow)
            spec.expect = list(spec.expect) + list(late_expect)
        names = labeler(before) | labeler(after.layers)
        record: dict[str, Any] = {
            "step": ctx.step, "action": spec.name, "args": spec.args, "info": info,
            "seconds": {"action": round(action_s, 2), **{k: round(v, 2) for k, v in after.timings.items()}},
            "allowance": spec.allowance,
        }
        record["seconds"]["cache_quiet"] = round(quiet_s, 2)
        record["seconds"]["settle_before"] = round(settle_s, 2)
        problems: list[str] = [f"lock: {line}" for line in settle_problems]
        if str(info.get("status")) != "success":
            problems.append(f"action did not succeed: {info.get('status')} {info.get('message') or ''}".strip())

        # Question 1: the cache, and everything that follows it.
        frame = check(before, after.layers, spec.allowance, layers=("ha", "cache", "entities"))
        record["changed"] = [c.render(names) for c in frame.allowed]
        record["frame_findings"] = [c.render(names) for c in frame.findings]
        problems += [f"frame: {line}" for line in record["frame_findings"]]

        cache_moved = any(c.path[0] == "cache" for c in frame.allowed + frame.findings)
        record["generation"] = {"cache": [gen_before, after.generation],
                                "entity": [entity_gen_before, after.entity_generation]}
        if cache_moved and after.generation == gen_before:
            problems.append(f"generation: the cache changed but its generation stayed {gen_before} "
                            "(open cards are never told)")

        persist_lines, persist_s, persisted_gen = self._persisted(after.layers["cache"])
        record["persisted"] = {"seconds": round(persist_s, 2), "generation": persisted_gen,
                               "differences": persist_lines}
        problems += [f"persisted: {line}" for line in persist_lines]

        # Question 2: the lock.
        if self.ui:
            lock_report, lock_problems = timeline.analyze()
            record["lock"] = lock_report
            record["lock_timeline"] = timeline.changes()
            problems += [f"lock: {line}" for line in lock_problems]

            # Question 3: what the passive tabs show.
            converged, view_lines, waited = self._converge(after.layers["cache"])
            record["ui"] = {"converged": converged, "seconds": round(waited, 2), "problems": view_lines}
            problems += [f"ui: {line}" for line in view_lines]
            if converged and waited > CONVERGE_OK_S:
                # Every tab got there, but the user waited for it.
                problems.append(f"ui: the open tabs took {waited:.1f} s to show the change "
                                f"(more than {CONVERGE_OK_S:.0f} s)")
            if view_lines:
                record["ui"]["screenshots"] = self.ui.screenshot(self.dir, f"step_{ctx.step:03d}")

        # Checkpoint: the hub itself.
        if spec.checkpoint:
            t0 = time.time()
            self.world.checkpoint(after)
            record["seconds"]["hub"] = round(time.time() - t0, 2)
            hub_frame = check({"hub": last_hub}, {"hub": after.layers["hub"]},
                              pending_hub_allowance + spec.allowance, layers=("hub",))
            record["hub_changed"] = [c.render(names) for c in hub_frame.allowed]
            record["hub_findings"] = [c.render(names) for c in hub_frame.findings]
            problems += [f"hub frame: {line}" for line in record["hub_findings"]]
            stale = check({"x": after.layers["cache"]}, {"x": after.layers["hub"]}, [], layers=("x",))
            record["cache_vs_hub"] = [c.render(names) for c in stale.findings]
            problems += [f"cache != hub: {line}" for line in record["cache_vs_hub"]]
        ctx.snapshot = after

        # Forget first: a step that ends old expectations may set new ones
        # under the same prefix (a delete expects its bindings gone).
        for prefix in spec.forget:
            self.model.forget(prefix)
        for item in spec.expect:
            path, value, why = item[:3]
            layers = item[3] if len(item) > 3 else ("cache", "hub")
            self.model.expect(path, value, step=ctx.step, why=why, layers=layers)
        if spec.post is not None:
            try:
                post_lines = spec.post(ctx, after.layers)
            except Exception as err:  # noqa: BLE001
                post_lines = [f"post check crashed: {type(err).__name__}: {err}"]
            record["post"] = post_lines
            problems += [f"post: {line}" for line in post_lines]
        misses = self.model.check(after.layers, ("ha", "cache", "hub"))
        record["intent_misses"] = [m.render() for m in misses]
        problems += [f"intent: {line}" for line in record["intent_misses"]]

        lines = wire.lines()
        record["wire"] = Wire.summary(wire.sends(lines))
        if spec.read_only and record["wire"]["writes"]:
            problems.append(f"read-only step sent writes: {record['wire']['writes']}")
        errors = hb.errors_in(lines)
        record["log_errors"] = errors[:20]
        problems += [f"log: {line.strip()[:200]}" for line in errors[:5]]

        if problems and self.actor:
            try:
                shot = self.dir / f"step_{ctx.step:03d}_acting.png"
                self.actor.screenshot(shot)
                record.setdefault("screenshots", []).append(shot.name)
            except Exception:  # noqa: BLE001
                pass
        record["problems"] = problems
        self.records.append(record)
        self._write(f"snapshot_{ctx.step:03d}.json", after.before())
        self._print(record)

    def _print(self, record: dict[str, Any]) -> None:
        t = record["seconds"]
        print(f"   {record['info'].get('status')} in {t['action']}s"
              + (f"; hub read {t['hub']}s" if "hub" in t else "")
              + f"; sends {record['wire']['total']} (writes {sum(record['wire']['writes'].values())})"
              + f"; generation {record['generation']['cache'][0]}->{record['generation']['cache'][1]}"
              + f"; persisted in {record['persisted']['seconds']}s", flush=True)
        if "lock" in record:
            pages = record["lock"]["pages"]
            span = record["lock"]["backend_running"]
            print(f"   lock: backend busy {span}; "
                  + "; ".join(f"{p} locked at {v['first_locked']} (lag {v['lock_lag']}), unlock lag {v['unlock_lag']}"
                              for p, v in pages.items()), flush=True)
        if "ui" in record:
            print(f"   ui: {'converged' if record['ui']['converged'] else 'NOT converged'} "
                  f"in {record['ui']['seconds']}s", flush=True)
        for line in record["changed"]:
            print(f"   changed  {line}", flush=True)
        for line in record.get("hub_changed", []):
            print(f"   hub      {line}", flush=True)
        if record["problems"]:
            self.findings += 1
            for line in record["problems"]:
                print(f"   FINDING  {line}", flush=True)
        else:
            print("   clean", flush=True)
