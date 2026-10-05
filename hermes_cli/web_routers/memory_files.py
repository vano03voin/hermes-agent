"""Edit the current profile's two built-in memory documents."""
import hashlib
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from hermes_cli.tenant_context import tenant_path
from hermes_cli.web_routers._common import config_scoped_to_thread
from hermes_constants import get_hermes_home

router = APIRouter()
MemoryTarget = Literal["memory", "user"]


class MemoryDocumentUpdate(BaseModel):
    content: str
    version: str


def _document_path(target: MemoryTarget) -> Path:
    root = get_hermes_home() / "memories"
    path = root / ("MEMORY.md" if target == "memory" else "USER.md")
    return tenant_path(path, roots=(root,))


def _read_document(path: Path) -> dict:
    raw = path.read_bytes() if path.exists() else b""
    return {"content": raw.decode("utf-8-sig"), "version": hashlib.sha256(raw).hexdigest()}


@router.get("/api/memory/files/{target}")
async def read_memory_document(target: MemoryTarget, profile: str | None = None):
    return await config_scoped_to_thread(profile, lambda: _read_document(_document_path(target)))


@router.put("/api/memory/files/{target}")
async def write_memory_document(target: MemoryTarget, body: MemoryDocumentUpdate, profile: str | None = None):
    def write():
        from tools.memory_tool_store import MemoryStore
        from utils import atomic_write_text
        path = _document_path(target)
        # Share the memory tool's lock: an agent edit after the browser loaded
        # the document must produce a conflict, not be silently overwritten.
        with MemoryStore._file_lock(path):
            if _read_document(path)["version"] != body.version:
                raise HTTPException(409, "Memory changed; reload before saving")
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(path, body.content, preserve_mode=True, create_mode=0o600)
            return _read_document(path)
    return await config_scoped_to_thread(profile, write)
