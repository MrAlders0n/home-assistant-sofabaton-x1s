"""X2 paired devices: the rows of the hub's paired-device list (remotes,
repeaters, single-button RF devices), the remote target cached from them,
and the find-remote target built from them. Row layout and target bit field:
:mod:`remote_sync_frames`."""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING

from .protocol_const import OP_X2_REMOTE_LIST
from .remote_sync_frames import X2PairedDevice

if TYPE_CHECKING:
    from .proxy_host import _ProxyHost

#: The find-remote target when no paired remote is known: remote 1.
X2_FIND_REMOTE_FALLBACK = b"\x00\x00\x08"


class X2RemotesMixin(_ProxyHost if TYPE_CHECKING else object):
    """Paired-device rows of an X2 (uses the host's ``can_issue_commands``
    and ``enqueue_cmd``)."""

    def _init_x2_remotes(self) -> None:
        self._x2_remote_sync_id_lock = threading.Lock()
        self._x2_remote_sync_id: bytes | None = None
        self._x2_remote_sync_id_event = threading.Event()
        # Paired-device rows by (row type, bit), from the last list reply.
        self._x2_paired_devices: dict[tuple[int, int], X2PairedDevice] = {}

    def update_x2_remote_sync_id(self, remote_id: bytes) -> None:
        with self._x2_remote_sync_id_lock:
            self._x2_remote_sync_id = bytes(remote_id[:3])
            self._x2_remote_sync_id_event.set()

    def wait_for_x2_remote_sync_id(self, timeout: float = 2.0) -> bytes | None:
        self._x2_remote_sync_id_event.wait(timeout)
        with self._x2_remote_sync_id_lock:
            return self._x2_remote_sync_id

    def note_x2_paired_device(self, row: X2PairedDevice) -> None:
        """Record one decoded X2 paired-device row (list reply handler).

        Row 1 starts a new list, so devices unpaired since the last request
        drop out.
        """

        with self._x2_remote_sync_id_lock:
            if row.index <= 1:
                self._x2_paired_devices.clear()
            self._x2_paired_devices[(row.row_type, row.slot)] = row

    def x2_paired_devices(self) -> list[X2PairedDevice]:
        """The X2 paired devices from the last list reply, in row order."""

        with self._x2_remote_sync_id_lock:
            return sorted(self._x2_paired_devices.values(), key=lambda row: row.index)

    def _x2_find_remote_target(self) -> bytes:
        """The X2 find-remote target: every paired remote's bit, from the
        paired-device list (read once, stored rows, if none is cached yet).
        Falls back to remote 1 when no remote row comes back.
        """

        def remotes() -> list[X2PairedDevice]:
            return [row for row in self.x2_paired_devices() if row.is_remote]

        found = remotes()
        if not found and self.can_issue_commands():
            self.enqueue_cmd(OP_X2_REMOTE_LIST, b"\x00")
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                rows = self.x2_paired_devices()
                if rows and rows[-1].index >= rows[-1].count:
                    break
                time.sleep(0.05)
            found = remotes()
        target = bytearray(3)
        for row in found:
            for index, bit in enumerate(row.target):
                target[index] |= bit
        return bytes(target) if any(target) else X2_FIND_REMOTE_FALLBACK


__all__ = ["X2_FIND_REMOTE_FALLBACK", "X2RemotesMixin"]
