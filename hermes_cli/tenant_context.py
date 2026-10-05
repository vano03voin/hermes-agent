"""Request-local tenant identity; importing this module needs no web extras."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
import re


@dataclass(frozen=True)
class TenantPrincipal:
    user_id: str
    profile: str
    profile_home: Path
    is_operator: bool = False
    display_name: str = ""
    expires_at: int = 0

    def __post_init__(self):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", self.profile):
            raise ValueError("Invalid assigned profile")
        home = Path(self.profile_home)
        if not home.is_absolute() or home.is_symlink():
            raise ValueError("Profile home must be an absolute non-symlink directory")
        object.__setattr__(self, "profile_home", home.resolve())


_principal: ContextVar[TenantPrincipal | None] = ContextVar("dashboard_tenant", default=None)


def current_tenant() -> TenantPrincipal | None:
    principal = _principal.get()
    return principal if principal and not principal.is_operator else None


def authenticated_principal() -> TenantPrincipal | None:
    return _principal.get()


@contextmanager
def tenant_scope(principal: TenantPrincipal):
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    token = _principal.set(principal)
    home_token = set_hermes_home_override(str(principal.profile_home)) if not principal.is_operator else None
    try:
        yield
    finally:
        if home_token is not None:
            reset_hermes_home_override(home_token)
        _principal.reset(token)


def pin_profile(requested: str | None) -> str | None:
    principal = current_tenant()
    if principal is None:
        return requested
    if requested not in (None, "", "current", "all", principal.profile):
        from fastapi import HTTPException
        raise HTTPException(403, "Profile access denied")
    return principal.profile


def tenant_path(path: Path, *, roots: tuple[Path, ...] | None = None) -> Path:
    """Reject traversal and links, in addition to the deployment's OS boundary.

    This check does not replace race-safe OS isolation for concurrently mutable
    shared host paths. Full tenant runtimes must have no foreign/host mounts.
    """
    principal = current_tenant()
    if principal is None:
        return path
    from fastapi import HTTPException
    roots = roots or (principal.profile_home / "workspace",)
    path = Path(path)
    if not path.is_absolute():
        path = roots[0] / path
    if ".." in path.parts:
        raise HTTPException(403, "Path access denied")
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise HTTPException(403, "Symlink access denied")
    resolved = path.resolve()
    if not any(resolved == root.resolve() or root.resolve() in resolved.parents for root in roots):
        raise HTTPException(403, "Path access denied")
    return resolved
