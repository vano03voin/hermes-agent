"""Operator-managed loopback control for a native backend's retirement fence.

Only the managed configuration can enable this listener. A private per-launch token
and generation bind its registry entry and every request; a user profile cannot opt
another process into a retirement protocol. Unknown work is never an idle permit.
"""
from __future__ import annotations

import atexit
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import logging
import os
from pathlib import Path
import re
import threading
import time


def work_snapshot() -> dict:
    from hermes_cli.web_server_idle_proof import idle_proof
    from tools.process_registry import process_registry

    result = idle_proof()
    if result["idle"] is True and (process_registry.has_any_active() or process_registry.pending_watchers):
        return {"idle": False, "reason": "local_process_work"}
    return result


class BackendControl:
    def __init__(self, generation: str, token: str, *, fence=None, probe=work_snapshot):
        from hermes_cli.backend_retirement import retirement
        self.generation, self.token = generation, token
        self.fence, self.probe = fence or retirement, probe
        self._lock = threading.Lock()
        self._request_id = self._permit = None
        self._expires = 0.0

    def dispatch(self, action: str, body: dict) -> dict:
        if body.get("generation") != self.generation:
            return {"ok": False, "error": "generation-mismatch"}
        with self._lock:
            if self._request_id and time.monotonic() >= self._expires:
                self.fence.cancel(self._permit)
                self._request_id = self._permit = None
            if action == "health":
                try:
                    snapshot = self.probe()
                except Exception:
                    logging.getLogger(__name__).warning("Backend work probe failed", exc_info=True)
                    snapshot = {"idle": None, "reason": "work-probe-unavailable"}
                return {"ok": True, "generation": self.generation,
                        "busy": snapshot.get("idle") is not True, "quiescing": bool(self._request_id)}
            request_id = body.get("request_id")
            if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", request_id):
                return {"ok": False, "error": "invalid-request"}
            if action == "resume":
                if request_id != self._request_id:
                    return {"ok": False, "error": "request-mismatch"}
                self.fence.cancel(self._permit)
                self._request_id = self._permit = None
                return {"ok": True, "generation": self.generation, "request_id": request_id}
            if action != "quiesce":
                return {"ok": False, "error": "unknown-action"}
            if self._request_id:
                return self._prepared(request_id)
            started, wall = time.monotonic(), time.time()
            result = self.fence.prepare()
            if result.get("ok") is not True:
                return {"ok": False, "busy": True}
            permit = result["token"]
            try:
                idle = self.probe().get("idle") is True
            except Exception:
                logging.getLogger(__name__).warning("Backend retirement proof failed", exc_info=True)
                idle = False
            if not idle:
                self.fence.cancel(permit)
                return {"ok": False, "busy": True}
            self._request_id, self._permit = request_id, permit
            self._expires, self._wall_expires = started + 30, wall + 30
            return self._prepared(request_id)

    def _prepared(self, request_id):
        same = request_id == self._request_id
        return {"ok": same, "quiesced": same, "busy": not same, "generation": self.generation,
                "request_id": request_id, "lease_expires_at": self._wall_expires}


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        # Headers, credentials and user data must not enter request logs.
        return

    def do_POST(self):
        control = self.server.control
        if not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + control.token):
            self.send_error(401)
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size <= 0 or size > 4096:
                raise ValueError("body-size")
            body = json.loads(self.rfile.read(size))
            if not isinstance(body, dict):
                raise ValueError("body-type")
            result = control.dispatch(self.path.removeprefix("/"), body)
        except (ValueError, TypeError):
            self.send_error(400)
            return
        encoded = json.dumps({"generation": control.generation, **result}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


_listener = None


def start_managed_control():
    """Start once before native RPC admission. Missing managed configuration is a no-op."""
    global _listener
    if _listener is not None:
        return _listener
    from hermes_cli.managed_scope import load_managed_config
    config = (load_managed_config() or {}).get("backend_retirement_control")
    if not config:
        return None
    directory = Path(config["directory"])
    generation = config["generation"]
    token = Path(config["token_file"]).read_text(encoding="utf-8-sig").strip()
    if not directory.is_absolute() or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", generation) or len(token) < 32:
        raise ValueError("Invalid managed backend retirement control")
    directory.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.daemon_threads = True
    server.control = BackendControl(generation, token)
    marker = directory / "retiring.json"
    if marker.exists():
        pending = json.loads(marker.read_text(encoding="utf-8-sig"))
        if pending.get("generation") == generation and pending.get("lease_expires_at", 0) > time.time():
            # A frontend may restart its backend after the supervisor's final PID
            # snapshot. Join the existing admission fence BEFORE exposing native RPC.
            result = server.control.dispatch("quiesce", pending)
            if result.get("quiesced") is not True:
                server.server_close()
                raise RuntimeError("Backend started while its supervisor is retiring")
    from agent.memory_provider import spawn_context_thread
    thread = spawn_context_thread(server.serve_forever, name="backend-retirement-control", daemon=True)
    thread.start()
    import psutil
    record = directory / f"{os.getpid()}.json"
    from utils import atomic_json_write
    atomic_json_write(record, {"pid": os.getpid(), "started_at": psutil.Process().create_time(),
                               "generation": generation, "port": server.server_port})
    _listener = server

    def close():
        server.shutdown()
        server.server_close()
        record.unlink(missing_ok=True)
    atexit.register(close)
    return server
