"""Engine-side write batch (phase 4 plan, H2 / decision 8).

A batch is the engine's "do not talk to the remotes yet" mode. While one
is open, every request for a physical remote sync (the ``0x64`` trigger
on the X1 and X1S, the ``0x0364`` broadcast on the X2) is recorded
instead of sent, whichever code path asks for it: ``resync_remote``
itself, the ``_send_remote_sync_step`` the reorder writes and the Wifi
create save tail send, or a create sequence's terminal step. Closing the batch sends **one** trigger when
at least one was requested, and none otherwise: an unchanged document
produces zero triggers. The batch does not add triggers, it coalesces
the ones the participating writes would have sent.

The per-entity syncs never ask for one (the hub pushes their writes to
the remotes on its own; the "remote_sync" plan step is the favorites
mapping read that settles the cache), so a batch of pure entity edits
ends with no trigger, exactly as the same edits do one by one.

:class:`WriteBatchMixin` carries the trigger itself (``resync_remote``)
and the batch methods; :class:`X1Proxy` mixes it in and the asyncio
facade wraps them in ``AsyncXProxy.batch_writes()``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import threading
from typing import Any, TYPE_CHECKING

from .hub_logging import LogTag
from .hub_versions import HUB_VERSION_X2, classify_hub_version
from .protocol_const import OP_REMOTE_SYNC, OP_X2_REMOTE_SYNC_ALL
from .remote_sync_frames import FAMILY_ENTITY_SYNC, entity_sync_frame, order_sync_frame

if TYPE_CHECKING:
    from .proxy_host import _ProxyHost

#: The family / opcode byte of the physical remote-sync trigger.
REMOTE_SYNC_FAMILY = 0x64

#: ``entity_sync_form`` values: the frame a write sends after a favorites,
#: keymap or favorites-order change to the one entity it touched.
#: ``legacy`` is ``0x0165 [id]`` on every model (what has always been sent);
#: ``native`` sends the X2 its own ``0x0265 [id][0x01]`` (validated,
#: bench_333 C4) and the X1/X1S ``0x0165 [id]``; ``none`` sends nothing, for
#: benches that make the edit and send the sync themselves.
ENTITY_SYNC_LEGACY = "legacy"
ENTITY_SYNC_NATIVE = "native"
ENTITY_SYNC_NONE = "none"
ENTITY_SYNC_FORMS = (ENTITY_SYNC_LEGACY, ENTITY_SYNC_NATIVE, ENTITY_SYNC_NONE)

#: ``order_sync_form`` values: the trigger closing a device or activity
#: reorder. ``native`` (the default) sends the X2 its order-only sync
#: (~10 s instead of a ~4.5 min full sync, bench_333 C3) and the X1/X1S the
#: full trigger (they have no order-only form, C5); ``full`` sends the full
#: trigger on every model.
ORDER_SYNC_FULL = "full"
ORDER_SYNC_NATIVE = "native"
ORDER_SYNC_FORMS = (ORDER_SYNC_FULL, ORDER_SYNC_NATIVE)


@dataclass
class EngineWriteBatch:
    """Bookkeeping of one open batch."""

    remote_sync_requests: int = 0
    origins: list[str] = field(default_factory=list)

    def defer(self, origin: str) -> None:
        self.remote_sync_requests += 1
        self.origins.append(origin)

    @property
    def remote_sync_pending(self) -> bool:
        return self.remote_sync_requests > 0


class WriteBatchMixin(_ProxyHost if TYPE_CHECKING else object):
    """Physical remote-sync trigger and the write batch that coalesces it.

    Uses the host's ``_log``, ``hub_version``, ``mdns_txt`` and
    ``enqueue_cmd`` (declared by ``_ProxyHost``).
    """

    def _init_write_batch(self) -> None:
        # While set, physical remote-sync triggers are recorded instead of
        # sent; see begin_write_batch.
        self._write_batch: EngineWriteBatch | None = None
        self._write_batch_lock = threading.Lock()
        # Which sync forms the write flows use (see the form constants).
        self.entity_sync_form: str = ENTITY_SYNC_LEGACY
        self.order_sync_form: str = ORDER_SYNC_NATIVE

    def _remote_sync_frame(self, hub_version: str | None = None) -> tuple[int, bytes] | None:
        """``(opcode, payload)`` of the physical remote-sync trigger for
        this hub, or None when the hub version cannot be resolved."""

        version = hub_version or self.hub_version
        if not version:
            try:
                version = classify_hub_version(self.mdns_txt)
            except ValueError:
                self._log.warning(
                    "%s sync: hub_version unknown; cannot pick opcode.", LogTag.REMOTE
                )
                return None
        self.hub_version = version

        if version == HUB_VERSION_X2:
            # All-remotes broadcast, bench-validated live 2026-08-27: this
            # is the only form that actually starts a sync. The historical
            # remote-list + OP_X2_REMOTE_SYNC [id:3][0x01] flow is ACKed
            # 0x00 by the hub but starts nothing (accepted no-op), and a
            # bare 0x0064 was never seen to start one either. The hub
            # serializes triggers, so re-sending while a sync runs simply
            # queues one more pass.
            return OP_X2_REMOTE_SYNC_ALL, b"\xff\xff\xff"

        return OP_REMOTE_SYNC, b""

    def resync_remote(self, hub_version: str | None = None) -> bool:
        """Force a physical remote sync with the hub.

        Inside a write batch (:meth:`begin_write_batch`) the trigger is
        recorded and sent once when the batch closes; the call reports
        success so the writes that ask for it proceed unchanged.
        """
        if self._defer_remote_sync("resync_remote"):
            return True
        frame = self._remote_sync_frame(hub_version)
        if frame is None:
            return False
        return self.enqueue_cmd(*frame)

    def _send_remote_sync_step(
        self, step_name: str, *, ack_first_byte: int | None = None, purpose: str = "full"
    ) -> bool:
        """Send the remote-sync trigger as an acked step of a write sequence.

        The frame is the one :meth:`resync_remote` sends for this hub (the
        all-remotes broadcast on the X2, never a bare ``0x0064`` there),
        but sent through ``_send_step`` so its STATUS_ACK is consumed
        before the sequence's next step waits for its own. Inside a write
        batch the trigger is deferred like any other and the call reports
        success.

        ``purpose="order"`` marks a reorder's trigger: with
        ``order_sync_form`` set to ``native`` the X2 gets its order-only
        sync instead of the full broadcast. A batch still coalesces it into
        the one full trigger, which covers the order.
        """

        if self._defer_remote_sync(step_name):
            return True
        frame = self._remote_sync_frame()
        if frame is None:
            return False
        if purpose == "order" and self.order_sync_form == ORDER_SYNC_NATIVE:
            frame = order_sync_frame(self.hub_version) or frame
        # The family frame derives its opcode from the payload length, so
        # the remote-sync family plus this payload is exactly the opcode
        # resync_remote sends (0x0064 empty, 0x0364 FF FF FF).
        _opcode, payload = frame
        return self._send_step(
            step_name=step_name,
            family=REMOTE_SYNC_FAMILY,
            payload=payload,
            ack_opcode=0x0103,
            ack_first_byte=ack_first_byte,
        ).ok

    def _send_entity_sync_step(self, entity_id: int, step_name: str) -> bool:
        """Send the per-entity sync closing a favorites/keymap write.

        The frame follows ``entity_sync_form``. It is not a remote-sync
        trigger, so a write batch never defers or counts it.
        """

        form = self.entity_sync_form
        if form == ENTITY_SYNC_NONE:
            self._log.info("%s %s: per-entity sync skipped (entity_sync_form=none)", LogTag.REMOTE, step_name)
            return True
        x2_form = form == ENTITY_SYNC_NATIVE and self.hub_version == HUB_VERSION_X2
        _opcode, payload = entity_sync_frame(entity_id, x2_form=x2_form)
        # The family frame derives the opcode from the payload length.
        return self._send_step(
            step_name=step_name,
            family=FAMILY_ENTITY_SYNC,
            payload=payload,
            ack_opcode=0x0103,
        ).ok

    def sync_remote_entity(self, entity_id: int) -> bool:
        """Per-item remote sync of one device or activity: the remotes
        re-read that one item (a 1/1 sync on the X2, seconds) instead of
        rebuilding everything. Covers every pending change of that item
        (bench_333 C4-C6, all models); it cannot add or remove an item, so
        creates and deletes still need :meth:`resync_remote`. Not deferred
        by a write batch.
        """

        entity = int(entity_id) & 0xFF
        return self._send_entity_sync_step(entity, f"entity-sync[0x{entity:02X}]")

    # ------------------------------------------------------------------
    # Write batch (phase 4 plan, H2 / decision 8)
    # ------------------------------------------------------------------
    def begin_write_batch(self) -> None:
        """Open a write batch: defer every physical remote-sync trigger.

        While the batch is open, ``resync_remote``,
        ``_send_remote_sync_step`` and a create sequence's terminal step
        all record a request instead of sending. Close it with
        :meth:`end_write_batch`, which sends one trigger when any was
        requested. One batch at a time; a second ``begin`` raises.
        """

        with self._write_batch_lock:
            if self._write_batch is not None:
                raise RuntimeError("a write batch is already open")
            self._write_batch = EngineWriteBatch()
        self._log.info("[BATCH] write batch opened; remote-sync triggers deferred")

    def end_write_batch(self, *, send_remote_sync: bool = True) -> dict[str, Any]:
        """Close the batch and send the one coalesced trigger.

        Returns ``{"remote_sync": "sent" | "failed" | "not_needed" |
        "skipped", "remote_sync_requests": n, "origins": [...]}``.
        ``not_needed`` means no participating write asked for a trigger
        (nothing that needs one was written). ``failed`` means the
        trigger could not be enqueued (the hub is not writable); the
        configuration writes themselves are unaffected and the caller may
        retry the trigger alone with ``resync_remote``. ``skipped`` means
        writes asked for one but ``send_remote_sync=False`` dropped it:
        the remotes are not up to date.
        """

        with self._write_batch_lock:
            batch = self._write_batch
            if batch is None:
                raise RuntimeError("no write batch is open")
            self._write_batch = None
        status = "not_needed"
        if batch.remote_sync_pending:
            if not send_remote_sync:
                status = "skipped"
            else:
                status = "sent" if self.resync_remote() else "failed"
        self._log.info(
            "[BATCH] write batch closed: %d remote-sync request(s) from %s -> %s",
            batch.remote_sync_requests, batch.origins or "nothing", status,
        )
        return {
            "remote_sync": status,
            "remote_sync_requests": batch.remote_sync_requests,
            "origins": list(batch.origins),
        }

    @property
    def write_batch_open(self) -> bool:
        return self._write_batch is not None

    def _defer_remote_sync(self, origin: str) -> bool:
        """True when a batch is open and the trigger was recorded instead."""

        batch = self._write_batch
        if batch is None:
            return False
        batch.defer(origin)
        self._log.debug("[BATCH] remote sync from %s deferred to the batch end", origin)
        return True
