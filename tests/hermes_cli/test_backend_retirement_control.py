import threading
import httpx
import pytest

from hermes_cli.backend_retirement import RetirementFence
from hermes_cli.backend_retirement_control import BackendControl, _Handler
from http.server import ThreadingHTTPServer


def test_native_control_lease_fences_rpc_admission_and_rejects_other_generation(monkeypatch):
    from hermes_cli import web_server_idle_proof
    monkeypatch.setattr(web_server_idle_proof, "idle_proof", lambda: {"idle": True})
    fence = RetirementFence()
    probe = {"idle": True}
    control = BackendControl("first", "x" * 40, fence=fence, probe=lambda: probe)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.control = control
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{server.server_port}", trust_env=False) as client:
            body = {"generation": "first", "request_id": "request"}
            assert client.post("/health", json=body).status_code == 401
            client.headers["Authorization"] = "Bearer " + "x" * 40
            assert client.post("/quiesce", json={**body, "generation": "old"}).json()["ok"] is False
            with fence.work() as admitted:
                assert admitted
                assert client.post("/quiesce", json=body).json()["ok"] is False
            assert client.post("/quiesce", json=body).json()["quiesced"]
            assert not fence.acquire()
            assert not client.post("/resume", json={**body, "request_id": "wrong"}).json()["ok"]
            assert client.post("/resume", json=body).json()["ok"]
            assert fence.acquire()
            fence.release()
            probe["idle"] = None
            assert not client.post("/quiesce", json=body).json()["ok"]
            assert fence.acquire()
            fence.release()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_native_control_ignores_personal_config(tmp_path, monkeypatch):
    from hermes_cli import backend_retirement_control, managed_scope
    monkeypatch.setattr(backend_retirement_control, "_listener", None)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "config.yaml").write_text("backend_retirement_control: {directory: /tmp/untrusted}\n")
    monkeypatch.setattr(managed_scope, "load_managed_config", lambda: {})
    assert backend_retirement_control.start_managed_control() is None
