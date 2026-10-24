"""Candidate remote-sync forms (bench_333): the frame builders, and the
engine switches that pick them. The defaults must send exactly what the
write flows always sent."""

from __future__ import annotations

import importlib
import importlib.util
import sys
import types
from pathlib import Path

import pytest

LIB_DIR = Path(__file__).resolve().parents[2] / "custom_components" / "sofabaton_x1s" / "lib"


def _load_lib() -> types.ModuleType:
    name = "sofabaton_real_engine_test_pkg"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name, LIB_DIR / "__init__.py", submodule_search_locations=[str(LIB_DIR)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_pkg = _load_lib()
frames = importlib.import_module(f"{_pkg.__name__}.remote_sync_frames")
write_batch = importlib.import_module(f"{_pkg.__name__}.write_batch")
x1_proxy_mod = importlib.import_module(f"{_pkg.__name__}.x1_proxy")


class _Step:
    def __init__(self, ok: bool = True) -> None:
        self.ok = ok


def _engine(hub_version: str, monkeypatch) -> tuple[object, list[tuple[int, bytes]]]:
    engine = x1_proxy_mod.X1Proxy("127.0.0.1", hub_version=hub_version, proxy_enabled=False)
    sent: list[tuple[int, bytes]] = []

    def fake_send_step(*, step_name, family, payload, ack_opcode, **kw):
        sent.append((family, bytes(payload)))
        return _Step()

    monkeypatch.setattr(engine, "_send_step", fake_send_step)
    return engine, sent


# -- builders ---------------------------------------------------------------

def test_entity_sync_frame_forms() -> None:
    assert frames.entity_sync_frame(0x66, x2_form=False) == (0x0165, b"\x66")
    assert frames.entity_sync_frame(0x66, x2_form=True) == (0x0265, b"\x66\x01")
    assert frames.entity_sync_frame(0x1FF, x2_form=False) == (0x0165, b"\xff")


def test_x2_targets_put_the_slot_in_the_byte_of_its_row_type() -> None:
    assert frames.x2_target(0, 8) == bytes.fromhex("00 00 08")
    assert frames.x2_target(1, 4) == bytes.fromhex("00 04 00")
    assert frames.x2_target(2, 3) == bytes.fromhex("03 00 00")
    # An unknown type is treated like a remote.
    assert frames.x2_target(7, 5) == bytes.fromhex("00 00 05")


def test_x2_sync_frames() -> None:
    assert frames.x2_remote_sync_frame(bytes.fromhex("00 00 08")) == (0x0464, bytes.fromhex("00 00 08 01"))
    assert frames.x2_remote_sync_frame(frames.X2_TARGET_ALL) == (0x0464, bytes.fromhex("ff ff ff 01"))
    assert frames.x2_order_sync_frame() == (0x0464, bytes.fromhex("00 00 ff 00"))
    with pytest.raises(ValueError):
        frames.x2_remote_sync_frame(b"\x08")
    assert frames.order_sync_frame("X2") == (0x0464, bytes.fromhex("00 00 ff 00"))
    assert frames.order_sync_frame("X1S") is None and frames.order_sync_frame(None) is None


def test_parse_x2_paired_row_reads_the_slot_byte_of_its_type() -> None:
    row = bytes([1, 2, 3, 0, 0, 80, 3]) + bytes(4) + bytes([9, 2, 1, 0]) + bytes(6) + b"Rep\x00"
    parsed = frames.parse_x2_paired_row(row)
    assert parsed is not None
    assert (parsed.index, parsed.count, parsed.row_type, parsed.slot) == (1, 2, 2, 3)
    assert parsed.target == bytes.fromhex("03 00 00") and parsed.name == "Rep"
    assert frames.parse_x2_paired_row(bytes(12)) is None


# -- engine switches ----------------------------------------------------------

@pytest.mark.parametrize("hub_version", ["X1", "X1S", "X2"])
def test_entity_sync_default_is_the_legacy_frame_on_every_model(monkeypatch, hub_version) -> None:
    engine, sent = _engine(hub_version, monkeypatch)
    assert engine.entity_sync_form == write_batch.ENTITY_SYNC_LEGACY
    assert engine._send_entity_sync_step(0x66, "favorite-commit-65[act=0x66]") is True
    assert sent == [(0x65, b"\x66")]


@pytest.mark.parametrize("hub_version, payload", [("X1S", b"\x66"), ("X2", b"\x66\x01")])
def test_entity_sync_native_form_sends_the_x2_its_own_frame(monkeypatch, hub_version, payload) -> None:
    engine, sent = _engine(hub_version, monkeypatch)
    engine.entity_sync_form = write_batch.ENTITY_SYNC_NATIVE
    assert engine._send_entity_sync_step(0x66, "keymap-commit-65[act=0x66]") is True
    assert sent == [(0x65, payload)]


def test_entity_sync_none_sends_nothing_and_reports_success(monkeypatch) -> None:
    engine, sent = _engine("X2", monkeypatch)
    engine.entity_sync_form = write_batch.ENTITY_SYNC_NONE
    assert engine._send_entity_sync_step(0x66, "fav-delete-commit-65[act=0x66]") is True
    assert sent == []


def test_entity_sync_is_never_deferred_by_a_write_batch(monkeypatch) -> None:
    engine, sent = _engine("X2", monkeypatch)
    engine.begin_write_batch()
    assert engine._send_entity_sync_step(0x66, "favorite-commit-65[act=0x66]") is True
    summary = engine.end_write_batch()
    assert sent == [(0x65, b"\x66")]
    assert summary["remote_sync_requests"] == 0


@pytest.mark.parametrize("hub_version, payload", [("X1S", b""), ("X2", bytes.fromhex("00 00 ff 00"))])
def test_order_trigger_default_is_the_x2_order_sync(monkeypatch, hub_version, payload) -> None:
    engine, sent = _engine(hub_version, monkeypatch)
    assert engine.order_sync_form == write_batch.ORDER_SYNC_NATIVE
    assert engine._send_remote_sync_step("activity-sort-remote-sync", ack_first_byte=0x00, purpose="order")
    # The X2 gets its ~10 s order-only sync; the X1S has none and keeps the full trigger.
    assert sent == [(0x64, payload)]


@pytest.mark.parametrize("hub_version, payload", [("X1S", b""), ("X2", b"\xff\xff\xff")])
def test_order_trigger_full_form_sends_the_full_trigger(monkeypatch, hub_version, payload) -> None:
    engine, sent = _engine(hub_version, monkeypatch)
    engine.order_sync_form = write_batch.ORDER_SYNC_FULL
    assert engine._send_remote_sync_step("activity-sort-remote-sync", ack_first_byte=0x00, purpose="order")
    assert sent == [(0x64, payload)]


@pytest.mark.parametrize("hub_version, payload", [("X1S", b""), ("X2", bytes.fromhex("00 00 ff 00"))])
def test_order_trigger_native_form_sends_the_x2_its_order_sync(monkeypatch, hub_version, payload) -> None:
    engine, sent = _engine(hub_version, monkeypatch)
    engine.order_sync_form = write_batch.ORDER_SYNC_NATIVE
    assert engine._send_remote_sync_step("device-sort-remote-sync", ack_first_byte=0x00, purpose="order")
    # A trigger that is not a reorder's keeps the full form.
    assert engine._send_remote_sync_step("save-tail-remote-sync")
    full = b"\xff\xff\xff" if hub_version == "X2" else b""
    assert sent == [(0x64, payload), (0x64, full)]


def test_order_trigger_inside_a_batch_coalesces_into_the_full_trigger(monkeypatch) -> None:
    engine, sent = _engine("X2", monkeypatch)
    engine.order_sync_form = write_batch.ORDER_SYNC_NATIVE
    enqueued: list = []
    monkeypatch.setattr(engine, "enqueue_cmd", lambda opcode, payload=b"", **kw: enqueued.append((opcode, payload)) or True)
    engine.begin_write_batch()
    assert engine._send_remote_sync_step("activity-sort-remote-sync", ack_first_byte=0x00, purpose="order")
    assert sent == []
    assert engine.end_write_batch()["remote_sync"] == "sent"
    assert enqueued == [(0x0364, b"\xff\xff\xff")]


# -- per-item sync after a write plan, find-remote target ---------------------

class _PlanStep:
    def __init__(self, kind: str) -> None:
        self.kind = kind
        self.payload = {}


@pytest.mark.parametrize("kinds, expected", [
    (["command_rename", "command_rename"], (12,)),
    (["command_payload"], (12,)),
    (["command_rename", "favorite_add"], None),
    (["binding_write"], None),
    (["member_replay", "command_rename"], None),
    (["command_rename", "command_rename", "wifi_head_commit"], (12,)),
    (["wifi_head_commit"], (12,)),
    (["wifi_power_config"], None),
    ([], None),
])
def test_per_item_sync_targets_only_covers_command_record_rewrites(kinds, expected) -> None:
    assert frames.per_item_sync_targets([_PlanStep(k) for k in kinds], 0x10C) == expected


@pytest.mark.parametrize("hub_version", ["X1", "X1S", "X2"])
def test_sync_remote_entity_sends_the_per_item_frame(monkeypatch, hub_version) -> None:
    engine, sent = _engine(hub_version, monkeypatch)
    assert engine.sync_remote_entity(0x10C) is True
    assert sent == [(0x65, b"\x0c")]


def _paired_row(index: int, count: int, row_type: int, bit: int) -> bytes:
    slots = bytearray(3)
    slots[{2: 0, 1: 1}.get(row_type, 2)] = bit
    head = bytes([index, count]) + bytes(slots) + bytes(7) + bytes([row_type, 1, 0]) + bytes(6) + b"R"
    return head.ljust(0x33, b"\x00")


def test_x2_find_remote_targets_every_paired_remote(monkeypatch) -> None:
    engine = x1_proxy_mod.X1Proxy("127.0.0.1", hub_version="X2", proxy_enabled=False)
    for row in (_paired_row(1, 3, 0, 0x08), _paired_row(2, 3, 1, 0x01), _paired_row(3, 3, 0, 0x10)):
        engine.note_x2_paired_device(frames.parse_x2_paired_row(row))
    sent: list = []
    monkeypatch.setattr(engine, "enqueue_cmd", lambda opcode, payload=b"", **kw: sent.append((opcode, payload)) or True)
    assert engine.find_remote() is True
    # Remotes 1 and 2 OR together; the repeater (type 1) is left out.
    assert sent == [(0x0323, bytes.fromhex("00 00 18"))]


def test_x2_find_remote_falls_back_to_remote_one_without_a_list(monkeypatch) -> None:
    engine = x1_proxy_mod.X1Proxy("127.0.0.1", hub_version="X2", proxy_enabled=False)
    sent: list = []
    monkeypatch.setattr(engine, "enqueue_cmd", lambda opcode, payload=b"", **kw: sent.append((opcode, payload)) or True)
    # Not controllable here: no list request, the validated remote 1 target.
    assert engine.find_remote() is True
    assert sent == [(0x0323, bytes.fromhex("00 00 08"))]
