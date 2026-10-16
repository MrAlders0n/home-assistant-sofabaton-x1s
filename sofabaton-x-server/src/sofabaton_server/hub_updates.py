"""Cache versions and the ``hub_update`` stream message (server panel cache
freshness, 2026-10-16; the HA integration's ``hub_state/subscribe``).

A hub's cache version is ``(epoch, generation, snapshot_id)``
(:class:`~.models.CacheVersion`): ``epoch`` is when this server first saw
the hub's current proxy (ms; a re-enable or a server restart is a new
proxy, so a newer epoch outranks any generation of an older one),
``generation`` the library's cache generation, ``snapshot_id`` the content
id of the projection at that generation. The library moves the generation
on every hub read that ends, so the id is only projected again when the
generation moved: a read that changed nothing keeps the id, and clients
never re-read for it.

Clients that open ``/api/v1/events?hub_updates=true`` get one
``hub_update`` (the hub's whole view, ``cache`` included) per hub right
after ``hello``, then one whenever something about the hub changed: its
library events, its jobs (queued, progress, end), its server events, and
its snapshot id moving without an event (another client's first look at a
device, the vendor app's session), which a watcher finds by reading the
generation every 0.25 s while such a client is connected. Changes are
coalesced per hub: the view goes out 0.15 s after the last change, never
later than 0.5 s after the first, one build at a time.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Optional

from .manager import HubManager, HubNotFound
from .models import CacheVersion
from .ws import EventRelay, WsHubUpdate

if TYPE_CHECKING:  # pragma: no cover
    from .jobs import JobRunner

log = logging.getLogger(__name__)

DEBOUNCE_S = 0.15
MAX_WAIT_S = 0.5
WATCH_S = 0.25
# While a job runs on the hub (a whole-hub refresh moves the generation on
# every read), the id is projected at most this often; the job's end
# always projects again.
BUSY_PROJECT_S = 1.0


def _read_generation(proxy: Any) -> Optional[int]:
    """The proxy's cache generation, or None when its library predates it."""

    try:
        value = proxy.cache_generation
    except Exception:  # noqa: BLE001  (an older library, a proxy going away)
        return None
    return int(value) if isinstance(value, int) else None


@dataclass
class _Entry:
    proxy: Any
    epoch: int
    generation: Optional[int] = None
    snapshot_id: Optional[str] = None
    projected_at: float = 0.0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def version(self) -> Optional[CacheVersion]:
        if self.generation is None or self.snapshot_id is None:
            return None
        return CacheVersion(epoch=self.epoch, generation=self.generation, snapshot_id=self.snapshot_id)


class CacheVersions:
    """The cache version of every running hub, projected only when the
    library's generation moved."""

    def __init__(self, manager: HubManager, *, clock_ms: Optional[Callable[[], int]] = None,
                 busy_project_s: float = BUSY_PROJECT_S) -> None:
        self._manager = manager
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1000))
        self._busy_project_s = busy_project_s
        self._entries: dict[str, _Entry] = {}
        manager.on_rekey(self._on_rekey)
        manager.on_server_event(self._on_server_event)

    def _on_rekey(self, old_id: str, new_id: str) -> None:
        # Same proxy, new id: the epoch stays.
        entry = self._entries.pop(old_id, None)
        if entry is not None:
            self._entries[new_id] = entry

    def _on_server_event(self, hub_id: str, kind: str) -> None:
        if kind == "hub_removed":
            self._entries.pop(hub_id, None)

    def _entry(self, hub_id: str, proxy: Any) -> _Entry:
        entry = self._entries.get(hub_id)
        if entry is None or entry.proxy is not proxy:
            # Strictly later than the proxy before it, even within one ms.
            epoch = self._clock_ms()
            if entry is not None:
                epoch = max(epoch, entry.epoch + 1)
            entry = _Entry(proxy=proxy, epoch=epoch)
            self._entries[hub_id] = entry
        return entry

    def known(self, hub_id: str) -> Optional[CacheVersion]:
        """The last version worked out for the hub, without looking again."""

        entry = self._entries.get(hub_id)
        return entry.version() if entry is not None else None

    def moved(self, hub_id: str) -> bool:
        """True when the hub's generation (or its proxy) changed since the
        last version was worked out: a cheap check, no projection."""

        proxy = self._manager.running_proxy(hub_id)
        if proxy is None:
            return False
        entry = self._entries.get(hub_id)
        if entry is None or entry.proxy is not proxy or entry.snapshot_id is None:
            return True
        generation = _read_generation(proxy)
        return generation is not None and generation != entry.generation

    async def version(self, hub_id: str) -> Optional[CacheVersion]:
        """The hub's current cache version; None while no proxy runs (or
        the library cannot say)."""

        proxy = self._manager.running_proxy(hub_id)
        if proxy is None:
            return None
        entry = self._entry(hub_id, proxy)
        generation = _read_generation(proxy)
        if generation is None:
            return None
        if generation == entry.generation and entry.snapshot_id is not None:
            return entry.version()
        busy = self._job_running(hub_id) and time.monotonic() - entry.projected_at < self._busy_project_s
        if busy and entry.snapshot_id is not None:
            return entry.version()                   # a job is moving it: the last pair, still a true one
        async with entry.lock:
            generation = _read_generation(proxy)
            if generation is not None and (generation != entry.generation or entry.snapshot_id is None):
                peek = getattr(proxy, "peek_snapshot", None)
                if peek is None:
                    return None
                try:
                    snap = await peek()
                except Exception:  # noqa: BLE001  (a proxy stopping under us)
                    log.debug("hub %s: cache version not projected", hub_id, exc_info=True)
                    return entry.version()
                # One projection gives both halves, so they always belong together.
                entry.generation = int(snap.engine_generation)
                entry.snapshot_id = str(snap.snapshot_id)
                entry.projected_at = time.monotonic()
        return entry.version()

    def _job_running(self, hub_id: str) -> bool:
        jobs = self._manager.jobs
        return jobs is not None and jobs.active(hub_id) is not None


@dataclass
class _Pending:
    first: float
    last: float


class HubUpdates:
    """Coalesces everything that changes a hub into ``hub_update`` frames
    for the subscribers that asked for them, and watches the cache
    generation while there are any."""

    def __init__(self, manager: HubManager, relay: EventRelay, versions: CacheVersions, *,
                 jobs: Optional["JobRunner"] = None, debounce_s: float = DEBOUNCE_S,
                 max_wait_s: float = MAX_WAIT_S, watch_s: float = WATCH_S) -> None:
        self._manager = manager
        self._relay = relay
        self._versions = versions
        self._debounce_s = debounce_s
        self._max_wait_s = max_wait_s
        self._watch_s = watch_s
        self._seq = 0
        self._pending: dict[str, _Pending] = {}
        self._flushers: dict[str, asyncio.Task] = {}
        self._watcher: Optional[asyncio.Task] = None
        self._stopped = False
        manager.on_hub_event(lambda hub_id, _event: self.poke(hub_id))
        manager.on_server_event(lambda hub_id, _kind: self.poke(hub_id))
        if jobs is not None:
            jobs.on_job_event(lambda hub_id, _job: self.poke(hub_id))

    def next_seq(self) -> int:
        self._seq += 1
        return self._seq

    # -- changes ---------------------------------------------------------------

    def poke(self, hub_id: str) -> None:
        """Something about the hub changed: send its view soon."""

        if not hub_id or self._stopped or not self._relay.hub_update_subscribers:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:                         # not on the loop: nothing listens there
            return
        now = loop.time()
        pending = self._pending.get(hub_id)
        if pending is None:
            self._pending[hub_id] = _Pending(first=now, last=now)
        else:
            pending.last = now
        if hub_id not in self._flushers:
            self._flushers[hub_id] = loop.create_task(self._flush(hub_id))

    async def _flush(self, hub_id: str) -> None:
        loop = asyncio.get_running_loop()
        try:
            while True:
                pending = self._pending.get(hub_id)
                if pending is None:
                    return
                due = min(pending.last + self._debounce_s, pending.first + self._max_wait_s)
                delay = due - loop.time()
                if delay > 0:
                    await asyncio.sleep(delay)
                    continue
                del self._pending[hub_id]
                await self._publish(hub_id)
        finally:
            self._flushers.pop(hub_id, None)

    async def _publish(self, hub_id: str) -> None:
        try:
            view = await self._manager.view(hub_id)
        except HubNotFound:
            return                                   # removed: hub_removed told the clients
        except Exception:  # noqa: BLE001
            log.debug("hub %s: hub update not built", hub_id, exc_info=True)
            return
        # Nothing awaited between the view's job fields and the offer, so
        # the frame queues behind every job_event it already reflects.
        self._relay.publish_hub_update(WsHubUpdate(hub_id=hub_id, seq=self.next_seq(), hub=view))

    # -- the generation watcher --------------------------------------------------------

    def watch(self) -> None:
        """Run the watcher while hub-update subscribers exist (it ends by itself after the last)."""

        if self._stopped or (self._watcher is not None and not self._watcher.done()):
            return
        self._watcher = asyncio.get_running_loop().create_task(self._watch())

    async def _watch(self) -> None:
        # The first views were just built with their versions: look again a tick later.
        await asyncio.sleep(self._watch_s)
        while not self._stopped and self._relay.hub_update_subscribers:
            for hub_id in list(self._manager.ids()):
                try:
                    if not self._versions.moved(hub_id):
                        continue
                    before = self._versions.known(hub_id)
                    after = await self._versions.version(hub_id)
                except Exception:  # noqa: BLE001  (one hub must not stop the others)
                    log.debug("hub %s: cache watch failed", hub_id, exc_info=True)
                    continue
                if after is not None and (before is None or after.snapshot_id != before.snapshot_id
                                          or after.epoch != before.epoch):
                    self.poke(hub_id)
            await asyncio.sleep(self._watch_s)

    async def stop(self) -> None:
        self._stopped = True
        tasks = [t for t in (self._watcher, *self._flushers.values()) if t is not None]
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._pending.clear()
