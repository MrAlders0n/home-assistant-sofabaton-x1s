"""Shared helpers for the HA-path benches (bench_32x, the facade adoption run).

The deployed Home Assistant instance is driven through its own APIs (REST
and websocket, no direct hub connection); its log and storage are read on
the Z: share. Token from scripts/.ha-token, instance from
scripts/.ha-config.json (the same files ha_entry.py and deploy-ha.ps1 use).

Nothing here writes to a hub by itself: every write is a call a bench
script makes on purpose.
"""

from __future__ import annotations

import json
import socket
import ssl
import struct
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

import websocket

SCRIPTS = Path(__file__).resolve().parents[1]
OUT = Path(__file__).resolve().parent / "out"
_CONFIG = json.loads((SCRIPTS / ".ha-config.json").read_text(encoding="utf-8"))
TOKEN = (SCRIPTS / ".ha-token").read_text(encoding="utf-8").strip()
BASE_URL = _CONFIG["base_url"].rstrip("/")
WS_URL = BASE_URL.replace("https://", "wss://").replace("http://", "ws://") + "/api/websocket"
HA_CONFIG_DIR = Path("Z:/homeassistant/config")
HA_LOG = HA_CONFIG_DIR / "home-assistant.log"
CACHE_STORE = HA_CONFIG_DIR / ".storage" / "sofabaton_x1s.persistent_cache"
DOMAIN = "sofabaton_x1s"
APP_DISCOVERY_PORT = 8102


# -- checks ---------------------------------------------------------------------------------------


class Tally:
    """OK/FAIL lines as they happen, a summary and a JSON report at the end."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.rows: list[dict[str, Any]] = []
        self.notes: list[str] = []
        self.t0 = time.time()

    def check(self, label: str, ok: bool, detail: str = "") -> bool:
        self.rows.append({"label": label, "ok": bool(ok), "detail": detail})
        print(f"  {'OK  ' if ok else 'FAIL'} {label}" + (f" -- {detail}" if detail else ""), flush=True)
        return bool(ok)

    def note(self, text: str) -> None:
        self.notes.append(text)
        print(f"  NOTE {text}", flush=True)

    def section(self, title: str) -> None:
        print(f"\n== {title} ==", flush=True)

    def finish(self) -> int:
        failed = [r for r in self.rows if not r["ok"]]
        OUT.mkdir(exist_ok=True)
        path = OUT / f"{self.name}_{time.strftime('%Y%m%d_%H%M%S')}.json"
        path.write_text(json.dumps({"rows": self.rows, "notes": self.notes,
                                    "seconds": round(time.time() - self.t0, 1)}, indent=2), encoding="utf-8")
        print(f"\n{len(self.rows) - len(failed)}/{len(self.rows)} checks passed; report {path.name}", flush=True)
        for row in failed:
            print(f"  FAILED: {row['label']} -- {row['detail']}", flush=True)
        return 1 if failed else 0


# -- websocket ------------------------------------------------------------------------------------


class HaWs:
    """One authenticated connection. Results and subscription events are
    buffered by message id, so calls and subscriptions can interleave."""

    def __init__(self) -> None:
        self.ws = websocket.create_connection(WS_URL, sslopt={"cert_reqs": ssl.CERT_NONE}, timeout=30)
        assert json.loads(self.ws.recv())["type"] == "auth_required"
        self.ws.send(json.dumps({"type": "auth", "access_token": TOKEN}))
        assert json.loads(self.ws.recv())["type"] == "auth_ok"
        self._id = 0
        self._results: dict[int, dict] = {}
        self._events: dict[int, list[dict]] = {}

    def _pump_one(self, timeout: float) -> bool:
        self.ws.settimeout(max(0.05, timeout))
        try:
            raw = json.loads(self.ws.recv())
        except websocket.WebSocketTimeoutException:
            return False
        if raw.get("type") == "result":
            self._results[raw["id"]] = raw
        elif raw.get("type") == "event":
            self._events.setdefault(raw["id"], []).append(raw.get("event") or {})
        return True

    def send(self, payload: dict) -> int:
        self._id += 1
        self.ws.send(json.dumps({"id": self._id, **payload}))
        return self._id

    def result(self, msg_id: int, timeout: float = 900.0) -> dict:
        deadline = time.time() + timeout
        while msg_id not in self._results:
            if time.time() > deadline:
                raise TimeoutError(f"no result for message {msg_id}")
            self._pump_one(min(1.0, deadline - time.time()))
        return self._results.pop(msg_id)

    def call(self, payload: dict, timeout: float = 900.0) -> tuple[dict, float]:
        t0 = time.time()
        res = self.result(self.send(payload), timeout)
        return res, time.time() - t0

    def ok(self, payload: dict, timeout: float = 900.0) -> Any:
        res, _ = self.call(payload, timeout)
        if not res.get("success"):
            raise RuntimeError(f"{payload.get('type')} failed: {res.get('error')}")
        return res.get("result")

    def subscribe(self, payload: dict) -> int:
        msg_id = self.send(payload)
        res = self.result(msg_id, 30)
        if not res.get("success"):
            raise RuntimeError(f"{payload.get('type')} subscribe failed: {res.get('error')}")
        return msg_id

    def pump(self, seconds: float) -> None:
        deadline = time.time() + seconds
        while time.time() < deadline:
            self._pump_one(deadline - time.time())

    def events(self, sub_id: int) -> list[dict]:
        """Drain the events buffered for one subscription."""
        return self._events.pop(sub_id, [])

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:  # noqa: BLE001
            pass


def wait_for_ha(timeout: float = 900.0) -> HaWs:
    deadline = time.time() + timeout
    while True:
        try:
            ws = HaWs()
            ws.ok({"type": f"{DOMAIN}/control_panel/state"})
            return ws
        except Exception as err:  # noqa: BLE001
            if time.time() > deadline:
                raise
            print(f"  HA not ready yet ({err}); waiting...", flush=True)
            time.sleep(15)


# -- REST -----------------------------------------------------------------------------------------


def rest(path: str, payload: dict | None = None, *, timeout: float = 120.0,
         method: str | None = None) -> tuple[int, Any, float]:
    req = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
        method=method or ("POST" if payload is not None else "GET"),
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, context=ssl.create_default_context(), timeout=timeout) as resp:
            body = resp.read().decode()
            try:
                return resp.status, json.loads(body or "null"), time.time() - t0
            except json.JSONDecodeError:
                return resp.status, body, time.time() - t0
    except urllib.error.HTTPError as err:
        return err.code, err.read().decode(errors="replace"), time.time() - t0
    except TimeoutError:
        return 0, None, time.time() - t0


def service(name: str, data: dict, *, response: bool = False, domain: str = DOMAIN,
            timeout: float = 600.0) -> tuple[int, Any]:
    """Call a service. HA's REST API answers a HomeAssistantError with a
    bare 500; the reason is in the log (log_since)."""

    path = f"/api/services/{domain}/{name}" + ("?return_response" if response else "")
    status, body, _ = rest(path, data, timeout=timeout)
    if response and status == 200 and isinstance(body, dict):
        return status, body.get("service_response")
    return status, body


# -- the HA log -----------------------------------------------------------------------------------


def log_mark() -> int:
    return HA_LOG.stat().st_size


def log_since(mark: int) -> list[str]:
    with HA_LOG.open("rb") as handle:
        handle.seek(mark)
        return handle.read().decode("utf-8", errors="replace").splitlines()


def grep(lines: list[str], *needles: str) -> list[str]:
    return [line for line in lines if all(n in line for n in needles)]


def errors_in(lines: list[str]) -> list[str]:
    """ERROR lines of the integration and of the library (whose loggers are
    ``x1proxy.*``, each line tagged with the entry id)."""

    return [line for line in lines
            if (" ERROR " in line or "Traceback" in line) and (DOMAIN in line or "[x1proxy" in line)]


def hub_lines(lines: list[str], entry_id: str) -> list[str]:
    """One hub's lines: the library and the hub object tag them with the
    entry id."""

    return [line for line in lines if entry_id in line]


def wire_sends(lines: list[str], entry_id: str) -> list[str]:
    """The opcode names sent to one hub (``[SEND] hub NAME (0x....)``, debug)."""

    out = []
    for line in hub_lines(lines, entry_id):
        marker = line.find("[SEND] hub ")
        if marker >= 0:
            out.append(line[marker + len("[SEND] hub "):].split(" ", 1)[0])
    return out


# -- hubs -----------------------------------------------------------------------------------------


class Hub:
    """One loaded entry and the entities and ids the benches need."""

    def __init__(self, ws: HaWs, state: dict) -> None:
        self.state = state
        self.entry_id: str = state["entry_id"]
        self.name: str = state.get("name") or ""
        self.version: str = str(state.get("version") or "")
        entities = [e for e in ws.ok({"type": "config/entity_registry/list"})
                    if e.get("config_entry_id") == self.entry_id]
        by_suffix = {str(e.get("unique_id", "")).rsplit("_", 1)[-1]: e["entity_id"] for e in entities}
        self.remote = next(e["entity_id"] for e in entities if e["entity_id"].startswith("remote."))
        remote_uid = next(str(e.get("unique_id")) for e in entities if e["entity_id"] == self.remote)
        self.mac = remote_uid[: -len("_remote")] if remote_uid.endswith("_remote") else ""
        self.resync_button = next((e["entity_id"] for e in entities
                                   if str(e.get("unique_id", "")).endswith("_resync_remote")), None)
        self.find_button = next((e["entity_id"] for e in entities
                                 if str(e.get("unique_id", "")).endswith("_find_remote")), None)
        self.emitter = next((e["entity_id"] for e in entities if e["entity_id"].startswith("infrared.")), None)
        self.wifi_sensor = next((e["entity_id"] for e in entities
                                 if e["entity_id"].startswith("sensor.") and e["entity_id"].endswith("_wifi_commands")),
                                None)
        self.entities = by_suffix
        devices = ws.ok({"type": "config/device_registry/list"})
        self.device = next(d["id"] for d in devices if self.entry_id in (d.get("config_entries") or [])
                           and not d.get("via_device_id"))

    @property
    def mac_bytes(self) -> bytes:
        try:
            raw = bytes.fromhex(self.mac.replace(":", "").replace("-", ""))
        except ValueError:
            raw = b""
        return raw[:6].ljust(6, b"\x00")

    def panel_state(self, ws: HaWs) -> dict:
        for hub in ws.ok({"type": f"{DOMAIN}/control_panel/state"}).get("hubs") or []:
            if hub.get("entry_id") == self.entry_id:
                return hub
        raise RuntimeError(f"entry {self.entry_id[:8]} is not loaded")

    def wait_connected(self, ws: HaWs, timeout: float = 300.0) -> dict:
        deadline = time.time() + timeout
        while True:
            state = self.panel_state(ws)
            if state.get("hub_connected") and not state.get("proxy_client_connected"):
                return state
            if time.time() > deadline:
                raise TimeoutError(f"{self.name} not connected")
            time.sleep(3)


def resolve_hub(ws: HaWs, which: str) -> Hub:
    """``which`` is a hub model (X1, X1S, X2) or an entry id prefix."""

    hubs = ws.ok({"type": f"{DOMAIN}/control_panel/state"}).get("hubs") or []
    want = which.strip().upper()
    matches = [h for h in hubs if str(h.get("version") or "").upper() == want] or \
              [h for h in hubs if str(h.get("entry_id") or "").upper().startswith(want)]
    if len(matches) != 1:
        found = ", ".join(f"{h.get('version')} {h.get('entry_id', '')[:8]} {h.get('name')!r}" for h in hubs)
        sys.exit(f"hub {which!r}: {len(matches)} matches among loaded entries ({found})")
    hub = Hub(ws, matches[0])
    print(f"{hub.version} {hub.name!r} entry {hub.entry_id[:8]} ({hub.remote})", flush=True)
    return hub


# -- registry operations (backup, restore, cache refresh) -----------------------------------------


def wait_backup_state(ws: HaWs, hub: Hub, kind: str, timeout: float = 1800.0) -> dict:
    """Poll backup/state until no operation runs and ``kind`` (backup_export,
    backup_restore) has ended."""

    deadline = time.time() + timeout
    while time.time() < deadline:
        result = ws.ok({"type": f"{DOMAIN}/backup/state", "entry_id": hub.entry_id})
        op = result.get(kind) or {}
        if result.get("active_operation") is None and op.get("status") in ("success", "failed"):
            return op
        time.sleep(2)
    raise TimeoutError(f"{kind} did not end")


def watch_operation(ws: HaWs, operation_id: str, *, during: Callable[[dict], None] | None = None,
                    timeout: float = 1800.0) -> list[dict]:
    """Every progress event of a registry operation until it ends;
    ``during`` runs on each event (fire side traffic from it)."""

    sub = ws.subscribe({"type": f"{DOMAIN}/backup/progress_subscribe", "operation_id": operation_id})
    seen: list[dict] = []
    deadline = time.time() + timeout
    while time.time() < deadline:
        ws.pump(0.5)
        for event in ws.events(sub):
            event = {**event, "_t": round(time.time(), 2)}
            seen.append(event)
            if during is not None:
                during(event)
            if str(event.get("status") or "") in ("success", "failed"):
                return seen
    raise TimeoutError(f"operation {operation_id} did not end")


def phases(events: list[dict]) -> list[str]:
    """The distinct phases in order (consecutive repeats folded)."""

    out: list[str] = []
    for event in events:
        phase = str(event.get("phase") or "")
        if phase and (not out or out[-1] != phase):
            out.append(phase)
    return out


def download_backup(operation_id: str) -> dict:
    status, body, _ = rest(f"/api/{DOMAIN}/backup/download/{operation_id}", timeout=300)
    if status != 200 or not isinstance(body, dict):
        raise RuntimeError(f"backup download {operation_id}: HTTP {status}")
    return body


def run_backup(ws: HaWs, hub: Hub, device_ids: list[int] | None = None) -> tuple[dict, dict]:
    payload: dict[str, Any] = {"type": f"{DOMAIN}/backup/export", "entry_id": hub.entry_id}
    if device_ids:
        payload["device_ids"] = device_ids
    op_id = ws.ok(payload)["operation_id"]
    events = watch_operation(ws, op_id)
    final = events[-1] if events else {}
    bundle = download_backup(op_id) if final.get("status") == "success" else {}
    return {**final, "operation_id": op_id, "events": events}, bundle


def structural_bundle(ws: HaWs, hub: Hub) -> tuple[dict | None, int | None]:
    res = ws.ok({"type": f"{DOMAIN}/cache/structural_bundle", "entry_id": hub.entry_id})
    return res.get("bundle"), res.get("generation")


def bundle_activity(bundle: dict, activity_id: int) -> dict | None:
    for row in bundle.get("activities") or []:
        if int((row.get("device") or {}).get("device_id") or 0) == activity_id:
            return row
    return None


def bundle_device(bundle: dict, device_id: int) -> dict | None:
    for row in bundle.get("devices") or []:
        if int((row.get("device") or {}).get("device_id") or 0) == device_id:
            return row
    return None


def bundle_summary(bundle: dict) -> dict:
    """Names, classes and per-entity counts: what an erasing restore must
    give back (ids may change)."""

    devices = {}
    for row in bundle.get("devices") or []:
        block = row.get("device") or {}
        devices[str(block.get("name"))] = {
            "class": block.get("device_class"),
            "commands": len(row.get("commands") or []),
            "bindings": len(row.get("button_bindings") or []),
            "macros": len(row.get("macros") or []),
            "inputs": len((row.get("input_record") or {}).get("entries") or []),
        }
    activities = {}
    for row in bundle.get("activities") or []:
        block = row.get("device") or {}
        activities[str(block.get("name"))] = {
            "members": len(row.get("referenced_source_device_ids") or []),
            "bindings": len(row.get("button_bindings") or []),
            "favorites": len(row.get("favorite_slots") or []),
            "macros": len(row.get("macros") or []),
        }
    return {"hub": (bundle.get("hub") or {}).get("name"), "devices": devices, "activities": activities}


# -- response shapes ------------------------------------------------------------------------------


def _merge_shapes(a: Any, b: Any) -> Any:
    """One shape covering both: dict keys united, scalar types joined
    (``null`` folds into any other type)."""

    if isinstance(a, dict) and isinstance(b, dict):
        return {k: _merge_shapes(a[k], b[k]) if k in a and k in b else a.get(k, b.get(k))
                for k in sorted(set(a) | set(b))}
    if isinstance(a, list) and isinstance(b, list):
        if not a or not b:
            return a or b
        return [_merge_shapes(a[0], b[0])]
    if a == b:
        return a
    names = {n for part in (a, b) if isinstance(part, str) for n in part.split("|")}
    if not isinstance(a, str) or not isinstance(b, str):
        return a if isinstance(a, (dict, list)) else b
    if len(names) > 1:
        names.discard("null")
    return "|".join(sorted(names))


def shape(value: Any, depth: int = 0) -> Any:
    """A value's type skeleton: dict keys kept, list items merged into one
    shape, scalars as type names. Two releases answering alike have equal
    shapes."""

    if depth > 12:
        return "..."
    if isinstance(value, dict):
        return {str(k): shape(v, depth + 1) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, list):
        merged: Any = None
        for item in value[:200]:
            item_shape = shape(item, depth + 1)
            merged = item_shape if merged is None else _merge_shapes(merged, item_shape)
        return [] if merged is None else [merged]
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    return type(value).__name__


def shape_diff(a: Any, b: Any, path: str = "$") -> list[str]:
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for key in sorted(set(a) | set(b)):
            if key not in a:
                out.append(f"{path}.{key}: only in the second")
            elif key not in b:
                out.append(f"{path}.{key}: only in the first")
            else:
                out.extend(shape_diff(a[key], b[key], f"{path}.{key}"))
        return out
    if isinstance(a, list) and isinstance(b, list):
        if a and b:
            return shape_diff(a[0], b[0], f"{path}[]")
        return []
    if isinstance(a, str) and isinstance(b, str):
        left, right = set(a.split("|")) - {"null"}, set(b.split("|")) - {"null"}
        if left and right and left != right:
            return [f"{path}: {a} vs {b}"]
        return []
    if a != b:
        return [f"{path}: {a} vs {b}"]
    return []


# -- a stand-in vendor app (CALL_ME) --------------------------------------------------------------


def local_ip_towards(host: str) -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.connect((host, 9))
        return probe.getsockname()[0]


class FakeApp:
    """Sends the vendor app's UDP CALL_ME to HA's proxy and listens for the
    dial-back. HA's log says whether the CALL_ME was taken
    (``APP CALL_ME from ... -> app tcp``) or gated (``... ignored (hub
    busy``); a dial-back that reaches this box is closed at once (or held
    open with ``hold=True`` to stand in for a connected app)."""

    def __init__(self, hub: Hub, port: int = 8193) -> None:
        self.hub = hub
        self.ha_host = socket.gethostbyname(urllib.parse.urlparse(BASE_URL).hostname or "")
        self.my_ip = local_ip_towards(self.ha_host)
        self.port = port
        self.dialed: list[float] = []
        self._held: list[socket.socket] = []
        self.hold = False
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind(("0.0.0.0", port))
        self._server.listen(4)
        self._server.settimeout(0.5)
        self._stop = threading.Event()
        threading.Thread(target=self._accept_loop, daemon=True).start()

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._server.accept()
            except (TimeoutError, socket.timeout, OSError):
                continue
            self.dialed.append(time.time())
            if self.hold:
                self._held.append(conn)
                threading.Thread(target=self._drain, args=(conn,), daemon=True).start()
            else:
                conn.close()

    @staticmethod
    def _drain(conn: socket.socket) -> None:
        try:
            while conn.recv(4096):
                pass
        except OSError:
            pass

    def call_me(self) -> None:
        frame = bytes([0xA5, 0x5A, 0x0C, 0xC3]) + self.hub.mac_bytes + socket.inet_aton(self.my_ip) \
            + struct.pack(">H", self.port)
        frame += bytes([sum(frame) & 0xFF])
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
            udp.sendto(frame, (self.ha_host, APP_DISCOVERY_PORT))

    def release(self) -> None:
        for conn in self._held:
            try:
                conn.close()
            except OSError:
                pass
        self._held.clear()

    def close(self) -> None:
        self._stop.set()
        self.release()
        self._server.close()


def call_me_verdict(lines: list[str], my_ip: str) -> str:
    """``gated``, ``taken`` or ``unseen`` for the CALL_ME sent from this box."""

    if grep(lines, "APP CALL_ME from", my_ip, "ignored (hub busy"):
        return "gated"
    if grep(lines, "APP CALL_ME from", my_ip, "-> app tcp"):
        return "taken"
    return "unseen"


# -- IR -------------------------------------------------------------------------------------------


def nec_pronto(address: int, command: int) -> str:
    """A pronto hex NEC frame (38 kHz): something harmless to emit."""

    def unit(us: float) -> int:
        return round(us / 26.3)

    pairs = [(unit(9000), unit(4500))]
    for byte in (address, address ^ 0xFF, command, command ^ 0xFF):
        for bit in range(8):
            pairs.append((unit(560), unit(1690 if (byte >> bit) & 1 else 560)))
    pairs.append((unit(560), unit(40000)))
    words = [0x0000, 0x006D, len(pairs), 0x0000] + [w for pair in pairs for w in pair]
    return " ".join(f"{w:04X}" for w in words)
