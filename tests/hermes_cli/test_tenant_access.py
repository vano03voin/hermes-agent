"""Tenant dashboard contracts against native profile, cron and file handlers."""
import pytest
from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from hermes_cli.tenant_access import TenantAccessMiddleware
from hermes_cli.tenant_context import TenantPrincipal, tenant_scope


@pytest.fixture
def tenants(tmp_path):
    result = []
    for name in ("alice", "bob"):
        home = tmp_path / name
        (home / "workspace").mkdir(parents=True)
        (home / "skills").mkdir()
        (home / "SOUL.md").write_text(name, encoding="utf-8")
        result.append(TenantPrincipal(name, name, home, expires_at=2000000000))
    return result


def guarded_app(principal, *, isolated=False):
    app = FastAPI()

    async def authenticate(connection):
        return principal if connection.cookies.get("test-session") == "valid" else None

    app.add_middleware(TenantAccessMiddleware, authenticate=authenticate,
                       public_origin="https://hermes.test", login_url="/login",
                       isolated_process=isolated, bound_profile=principal.profile if isolated else None)
    return app


def client_for(app):
    client = TestClient(app, base_url="https://hermes.test")
    client.cookies.set("test-session", "valid")
    client.headers["Origin"] = "https://hermes.test"
    return client


def test_real_profile_routes_never_enumerate_or_modify_foreign_profile(tenants):
    from hermes_cli.web_routers.profiles import router
    alice, bob = tenants
    app = guarded_app(alice)
    app.include_router(router)
    client = client_for(app)

    response = client.get("/api/profiles")
    assert [p["name"] for p in response.json()["profiles"]] == ["alice"]
    assert client.get("/api/profiles/alice/soul").json()["content"] == "alice"
    assert client.get("/api/profiles/bob/soul").status_code == 403
    assert client.put("/api/profiles/bob/soul", json={"content": "changed"}).status_code == 403
    assert (bob.profile_home / "SOUL.md").read_text() == "bob"
    assert client.put("/api/profiles/alice/soul", json={"content": "new persona"}).status_code == 200
    assert (alice.profile_home / "SOUL.md").read_text() == "new persona"


def test_queries_bodies_and_unknown_routes_fail_closed(tenants):
    app = guarded_app(tenants[0])

    @app.patch("/api/sessions/example")
    async def body(request: Request):
        return {"body": await request.json(), "profile": request.query_params.get("profile")}

    @app.post("/api/future-plugin")
    async def future_route():
        raise AssertionError("An unenrolled route must not execute")

    client = client_for(app)
    assert client.patch("/api/sessions/example?profile=bob", json={}).status_code == 403
    assert client.patch("/api/sessions/example", json={"profile": "bob"}).status_code == 403
    assert client.patch("/api/sessions/example", json={"nested": {"profile": "bob"}}).status_code == 403
    response = client.patch("/api/sessions/example?profile=all", json={"title": "ok"})
    assert response.json() == {"body": {"title": "ok", "profile": "alice"}, "profile": "alice"}
    assert client.post("/api/future-plugin").status_code == 403
    assert client.get("/api/config").status_code == 403
    assert client.post("/api/cron/jobs", json={"prompt": "work"}).status_code == 403
    assert client.patch("/api/sessions/example", json={}, headers={"Origin": "https://evil.test"}).status_code == 403


def test_real_cron_store_default_all_and_foreign_lookup_are_pinned(tenants):
    from cron import jobs
    from hermes_cli.web_routers.cron import router
    alice, bob = tenants
    saved = []
    for tenant in tenants:
        with jobs.use_cron_store(tenant.profile_home):
            saved.append(jobs.create_job("test", "0 0 * * *", name=tenant.profile, paused=True))
    app = guarded_app(alice)
    app.include_router(router)
    client = client_for(app)
    response = client.get("/api/cron/jobs")
    assert response.status_code == 200, response.text
    assert "alice" in response.text and "bob" not in response.text
    assert client.get(f"/api/cron/jobs/{saved[1]['id']}").status_code == 404
    assert client.get(f"/api/cron/jobs/{saved[1]['id']}/runs").status_code == 404


def test_real_file_resolvers_confine_to_own_workspace(tenants):
    from hermes_cli.web_server_files import _fs_path, _resolve_managed_path
    alice, bob = tenants
    scope = {"type": "http", "method": "GET", "path": "/api/files", "headers": []}
    request = Request(scope)
    own = alice.profile_home / "workspace" / "note.txt"
    own.write_text("private")
    with tenant_scope(alice):
        assert _fs_path(str(own)) == own
        assert _resolve_managed_path("note.txt", request)[1] == own
        for path in (bob.profile_home, alice.profile_home / "config.yaml", own.parent / ".." / "SOUL.md"):
            with pytest.raises(HTTPException) as error:
                _fs_path(str(path))
            assert error.value.status_code == 403


@pytest.mark.platforms("posix")
def test_symlink_to_other_tenant_is_rejected(tenants):
    from hermes_cli.web_server_files import _fs_path
    alice, bob = tenants
    link = alice.profile_home / "workspace" / "link"
    link.symlink_to(bob.profile_home, target_is_directory=True)
    with tenant_scope(alice), pytest.raises(HTTPException):
        _fs_path(str(link / "SOUL.md"))


def test_websocket_and_operator_contract(tenants):
    app = guarded_app(tenants[0])

    @app.websocket("/api/ws")
    async def rpc(socket: WebSocket):
        raise AssertionError("Player must not reach unrestricted RPC")

    client = client_for(app)
    with pytest.raises(WebSocketDisconnect) as error:
        with client.websocket_connect("/api/ws", headers={"Origin": "https://hermes.test"}):
            pass
    assert error.value.code == 4403
    operator = TenantPrincipal("operator", "operator", tenants[0].profile_home, is_operator=True)
    admin_app = guarded_app(operator)

    @admin_app.get("/api/config")
    async def operator_route():
        return {"admin": True}

    assert client_for(admin_app).get("/api/config").json() == {"admin": True}


def test_sidebar_response_cache_separates_tenant_identity(tenants):
    from hermes_cli.web_routers.profiles import _sidebar_singleflight_cache
    from hermes_cli.tenant_context import current_tenant

    @_sidebar_singleflight_cache
    def read():
        return {"profile": current_tenant().profile}

    for tenant in tenants:
        with tenant_scope(tenant):
            assert read() == {"profile": tenant.profile}


def test_memory_editor_owns_profile_and_rejects_stale_updates(tenants):
    from hermes_cli.web_routers.memory_files import router
    alice, bob = tenants
    app = guarded_app(alice)
    app.include_router(router)
    client = client_for(app)
    loaded = client.get("/api/memory/files/memory").json()
    payload = {"content": "Player's saved note", "version": loaded["version"]}
    assert client.put("/api/memory/files/memory", json=payload).status_code == 200
    assert client.put("/api/memory/files/memory", json=payload).status_code == 409
    assert client.get("/api/memory/files/memory?profile=bob").status_code == 403
    assert not (bob.profile_home / "memories" / "MEMORY.md").exists()
    assert (alice.profile_home / "memories" / "MEMORY.md").read_text() == payload["content"]


@pytest.mark.platforms("posix")
@pytest.mark.parametrize("document,route", [("SOUL.md", "/api/profiles/alice/soul"),
                                          ("memories/MEMORY.md", "/api/memory/files/memory")])
def test_personal_editors_reject_linked_documents(tenants, document, route):
    from hermes_cli.web_routers.profiles import router as profiles_router
    from hermes_cli.web_routers.memory_files import router as memory_router
    alice, bob = tenants
    document_path = alice.profile_home / document
    document_path.parent.mkdir(parents=True, exist_ok=True)
    document_path.unlink(missing_ok=True)
    document_path.symlink_to(bob.profile_home / "SOUL.md")
    app = guarded_app(alice)
    app.include_router(profiles_router)
    app.include_router(memory_router)
    client = client_for(app)
    assert client.get(route).status_code == 403
    assert client.put(route, json={"content": "changed", "version": "stale"}).status_code == 403
    assert (bob.profile_home / "SOUL.md").read_text() == "bob"
