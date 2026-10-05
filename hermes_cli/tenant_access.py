"""Optional server-enforced dashboard tenant boundary for authenticated hosts.

The host owns identity verification and profile provisioning. This module never
derives identity from a profile query, a client header, or the dashboard token.
Unregistered endpoints are operator-only. Native PTY is admitted only when the
host explicitly vouches for an isolated per-tenant Hermes process.
"""
from __future__ import annotations

import json
import re
from urllib.parse import parse_qsl, urlencode

from fastapi import HTTPException
from starlette.requests import HTTPConnection
from starlette.responses import JSONResponse, RedirectResponse

from hermes_cli.tenant_context import TenantPrincipal, pin_profile, tenant_path, tenant_scope


# Explicit route contracts. A newly installed plugin/endpoint remains private
# until reviewed and enrolled here; hiding its menu is not an access check.
_PLAYER_ROUTES = (
    (r"/api/auth/(?:me|ws-ticket)", {"GET", "POST"}),
    (r"/api/tenant", {"GET"}),
    (r"/api/profiles", {"GET"}),
    (r"/api/profiles/active", {"GET"}),
    (r"/api/profiles/sessions(?:/search|/sidebar)?", {"GET"}),
    (r"/api/profiles/[^/]+/(?:soul|description)", {"GET", "PUT"}),
    (r"/api/sessions", {"GET"}),
    (r"/api/sessions/(?:search|stats|empty/count)", {"GET"}),
    (r"/api/sessions/(?:bulk-delete|prune)", {"POST"}),
    (r"/api/sessions/empty", {"DELETE"}),
    (r"/api/sessions/[^/]+", {"GET", "PATCH", "DELETE"}),
    (r"/api/sessions/[^/]+/(?:messages|messages/around|timeline|export|latest-descendant)", {"GET"}),
    (r"/api/memory", {"GET"}),
    (r"/api/memory/files/(?:memory|user)", {"GET", "PUT"}),
    (r"/api/memory/reset", {"POST"}),
    (r"/api/skills", {"GET", "POST"}),
    (r"/api/skills/content", {"GET", "PUT"}),
    (r"/api/skills/toggle", {"PUT"}),
    (r"/api/cron/jobs", {"GET", "POST"}),
    (r"/api/cron/jobs/[^/]+", {"GET", "PUT", "DELETE"}),
    (r"/api/cron/jobs/[^/]+/runs", {"GET"}),
    (r"/api/cron/jobs/[^/]+/(?:pause|resume|trigger)", {"POST"}),
    (r"/api/files", {"GET", "DELETE"}),
    (r"/api/files/(?:read|download|stream)", {"GET", "HEAD"}),
    (r"/api/files/(?:upload|upload-stream|mkdir)", {"POST"}),
    (r"/api/fs/(?:list|read-text|read-data-url|download|default-cwd)", {"GET"}),
    (r"/api/fs/write-text", {"POST"}),
    (r"/api/chat/image-upload", {"POST"}),
    (r"/api/chat/workspaces", {"GET"}),
    (r"/api/media", {"GET"}),
    (r"/api/dashboard/(?:themes|font)", {"GET"}),
    (r"/api/dashboard/plugins", {"GET"}),
    (r"/api/dashboard/(?:theme|font)", {"PUT"}),
    (r"/auth/(?:semind/)?logout", {"POST"}),
)
_COMPILED_ROUTES = tuple((re.compile(pattern), methods) for pattern, methods in _PLAYER_ROUTES)
_PLAYER_PAGES = {"/", "/chat", "/sessions", "/memory", "/profiles", "/skills", "/cron", "/files"}


def authorize_route(path: str, method: str, principal: TenantPrincipal, *, isolated_process: bool):
    if principal.is_operator:
        return
    if method == "WEBSOCKET":
        if isolated_process and path in {"/api/pty", "/api/events"}:
            return
        raise HTTPException(403, "This connection requires an isolated tenant process")
    if not isolated_process and path.startswith("/api/cron/") and method not in {"GET", "HEAD"}:
        raise HTTPException(403, "Background execution requires an isolated tenant process")
    if method == "GET" and (path in _PLAYER_PAGES or path.startswith(("/assets/", "/fonts/", "/fonts-terminal/", "/ds-assets/")) or
                            path in {"/favicon.ico", "/favicon.svg"}):
        return
    if not any(pattern.fullmatch(path) and method in methods for pattern, methods in _COMPILED_ROUTES):
        raise HTTPException(403, "Operator access required")
    match = re.fullmatch(r"/api/profiles/([^/]+)/(?:soul|description)", path)
    if match and match.group(1) != principal.profile:
        raise HTTPException(403, "Profile access denied")


def _pin_json_profiles(value):
    if isinstance(value, dict):
        return {key: pin_profile(item) if key == "profile" else _pin_json_profiles(item)
                for key, item in value.items()}
    if isinstance(value, list):
        return [_pin_json_profiles(item) for item in value]
    return value


def _validate_job_request(path: str, data):
    if not path.startswith("/api/cron/") or not isinstance(data, dict):
        return
    # Provider routing and tool grants belong to the operator. A player may
    # schedule work but may not redirect a provider credential or grow grants.
    for key in ("provider", "base_url", "enabled_toolsets", "interpreter"):
        if data.get(key) not in (None, "", []):
            raise HTTPException(403, "Job runtime settings are managed by the operator")
    for key in ("deliver", "failure_deliver"):
        if data.get(key) not in (None, "", "local", "origin"):
            raise HTTPException(403, "Job delivery target is not allowed")
    if data.get("workdir"):
        from pathlib import Path
        tenant_path(Path(data["workdir"]))


class TenantAccessMiddleware:
    def __init__(self, app, *, authenticate, public_origin: str, login_url: str,
                 isolated_process: bool = False, bound_profile: str | None = None, public_paths=frozenset(),
                 reauthenticate_websocket_frames: bool = True):
        if isolated_process and not bound_profile:
            raise ValueError("An isolated process requires its server-assigned bound_profile")
        self.app, self.authenticate = app, authenticate
        self.public_origin, self.login_url = public_origin, login_url
        self.isolated_process, self.public_paths = isolated_process, frozenset(public_paths)
        self.bound_profile = bound_profile
        self.reauthenticate_websocket_frames = reauthenticate_websocket_frames

    async def __call__(self, scope, receive, send):
        if scope["type"] not in {"http", "websocket"} or scope["path"] in self.public_paths:
            return await self.app(scope, receive, send)
        connection = HTTPConnection(scope)
        websocket = scope["type"] == "websocket"
        try:
            if websocket and self._native_publisher(connection):
                return await self.app(scope, receive, send)
            principal = await self.authenticate(connection)
            if principal is None:
                if not websocket and scope.get("method") == "GET" and not scope["path"].startswith("/api/"):
                    return await RedirectResponse(self.login_url, status_code=303)(scope, receive, send)
                raise HTTPException(401, "Authentication required")
            method = "WEBSOCKET" if websocket else scope["method"]
            if self.bound_profile and not principal.is_operator and principal.profile != self.bound_profile:
                raise HTTPException(403, "This runtime belongs to a different profile")
            if (websocket or method not in {"GET", "HEAD", "OPTIONS"}) and connection.headers.get("origin") != self.public_origin:
                raise HTTPException(403, "Origin not allowed")
            authorize_route(scope["path"], method, principal, isolated_process=self.isolated_process)
            with tenant_scope(principal):
                scope = self._pin_scope(scope, principal)
                receive = await self._pin_body(scope, receive, principal)
                if websocket and self.reauthenticate_websocket_frames:
                    receive = self._verified_frames(connection, principal, receive)
                if scope["path"] == "/api/tenant" and not websocket:
                    response = JSONResponse({"user_id": principal.user_id, "profile": principal.profile,
                                             "is_operator": principal.is_operator,
                                             "display_name": principal.display_name},
                                            headers={"Cache-Control": "no-store"})
                    return await response(scope, receive, send)
                return await self.app(scope, receive, send)
        except HTTPException as exc:
            if websocket:
                return await send({"type": "websocket.close", "code": 4401 if exc.status_code == 401 else 4403})
            return await JSONResponse({"detail": exc.detail}, status_code=exc.status_code,
                                      headers={"Cache-Control": "no-store"})(scope, receive, send)

    def _native_publisher(self, connection):
        """One isolated runtime's native TUI event feed; never an RPC/admin login."""
        if not self.isolated_process or connection.url.path != "/api/pub":
            return False
        if connection.client is None or connection.client.host not in {"127.0.0.1", "::1"}:
            return False
        from hermes_cli.dashboard_auth.ws_tickets import TicketInvalid, consume_internal_credential
        try:
            consume_internal_credential(connection.query_params.get("internal", ""))
        except TicketInvalid:
            return False
        return True

    def _verified_frames(self, connection, principal, receive):
        async def verified_receive():
            message = await receive()
            if message["type"] == "websocket.receive":
                latest = await self.authenticate(connection)
                if latest is None or (latest.user_id, latest.profile, latest.is_operator) != (
                        principal.user_id, principal.profile, principal.is_operator):
                    raise HTTPException(401, "Session is no longer authorized")
            return message
        return verified_receive

    @staticmethod
    def _pin_scope(scope, principal):
        from hermes_cli.dashboard_auth.base import Session
        updated = dict(scope)
        state = dict(scope.get("state") or {})
        state.update(tenant_principal=principal, token_authenticated=True,
                     session=Session(principal.user_id, "", principal.display_name, "", "tenant",
                                     principal.expires_at, "", ""))
        updated["state"] = state
        if not principal.is_operator:
            pairs = parse_qsl(scope.get("query_string", b"").decode("utf-8"), keep_blank_values=True)
            pairs = [(key, pin_profile(value) if key == "profile" else value) for key, value in pairs]
            if not any(key == "profile" for key, _ in pairs):
                pairs.append(("profile", principal.profile))
            updated["query_string"] = urlencode(pairs).encode("utf-8")
        return updated

    @staticmethod
    async def _pin_body(scope, receive, principal):
        headers = dict(scope.get("headers", []))
        content_type = headers.get(b"content-type", b"").split(b";", 1)[0].strip().lower()
        is_json = content_type == b"application/json" or content_type.endswith(b"+json")
        if principal.is_operator or scope["type"] != "http" or not is_json:
            return receive
        chunks = []
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                raise HTTPException(400, "Request disconnected")
            chunks.append(message.get("body", b""))
            if not message.get("more_body", False):
                break
        try:
            data = json.loads(b"".join(chunks))
            data = _pin_json_profiles(data)
            _validate_job_request(scope["path"], data)
            if isinstance(data, dict):
                data["profile"] = principal.profile
            body = json.dumps(data).encode("utf-8")
        except (ValueError, TypeError, UnicodeDecodeError) as exc:
            raise HTTPException(400, "Invalid request body") from exc
        scope["headers"] = [(key, value) for key, value in scope["headers"] if key != b"content-length"]
        scope["headers"].append((b"content-length", str(len(body)).encode()))
        consumed = False

        async def replay():
            nonlocal consumed
            if not consumed:
                consumed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()
        return replay
