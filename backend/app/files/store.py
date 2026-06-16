"""Server-side file storage for project artifacts.

Storage layout:
    SENTINEL_FILES_DIR/
        <project_id>/
            uploads/         # human uploads
            ncbi/            # NCBI fetches (assemblies, gene FASTAs)
            tools/<run_id>/  # tool outputs

All paths in the DB are RELATIVE to SENTINEL_FILES_DIR/<project_id>/.
"""
from __future__ import annotations

import hashlib
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator, BinaryIO

from app.config import get_settings


def _base_dir() -> Path:
    settings = get_settings()
    base = getattr(settings, "files_dir", None) or os.environ.get("SENTINEL_FILES_DIR")
    if not base:
        # Default for local dev.
        base = "./data/files"
    p = Path(base).resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def project_dir(project_id: str) -> Path:
    p = _base_dir() / project_id
    p.mkdir(parents=True, exist_ok=True)
    return p


def safe_join(project_id: str, relpath: str) -> Path:
    """Resolve relpath under the project directory, refusing traversal."""
    base = project_dir(project_id)
    target = (base / relpath).resolve()
    if base not in target.parents and target != base:
        raise ValueError(f"path traversal blocked: {relpath}")
    return target


def safe_name(name: str) -> str:
    """Sanitize a user-supplied filename for filesystem storage."""
    return "".join(c for c in name if c.isalnum() or c in "._- ").strip() or "file"


async def write_upload(
    project_id: str,
    upload: BinaryIO,
    subdir: str,
    filename: str,
    return_head: int = 0,
) -> dict:
    """Stream an upload to disk under <project>/<subdir>/<filename>.
       Computes size and sha256 on the fly. Returns metadata dict.

       If return_head > 0, also returns the first `return_head` bytes
       in the result dict under 'head_bytes' — useful for content sniffing
       at the route layer without re-reading the file."""
    safe = safe_name(filename)
    target_dir = project_dir(project_id) / subdir
    target_dir.mkdir(parents=True, exist_ok=True)

    # Don't overwrite — if exists, append a numeric suffix.
    target = target_dir / safe
    i = 1
    stem = target.stem
    suffix = target.suffix
    while target.exists():
        target = target_dir / f"{stem}_{i}{suffix}"
        i += 1

    sha = hashlib.sha256()
    size = 0
    head_bytes = bytearray() if return_head > 0 else None
    with target.open("wb") as out:
        while chunk := await upload.read(1024 * 1024):
            out.write(chunk)
            sha.update(chunk)
            size += len(chunk)
            if head_bytes is not None and len(head_bytes) < return_head:
                need = return_head - len(head_bytes)
                head_bytes.extend(chunk[:need])

    relpath = str(target.relative_to(project_dir(project_id)))
    result = {
        "name": safe,
        "storage_path": relpath,
        "size": size,
        "sha256": sha.hexdigest(),
    }
    if head_bytes is not None:
        result["head_bytes"] = bytes(head_bytes)
    return result


def write_bytes(
    project_id: str,
    subdir: str,
    filename: str,
    content: bytes,
) -> dict:
    """Synchronous bytes write. Used by tool worker for outputs."""
    safe = safe_name(filename)
    target_dir = project_dir(project_id) / subdir
    target_dir.mkdir(parents=True, exist_ok=True)

    target = target_dir / safe
    i = 1
    stem = target.stem
    suffix = target.suffix
    while target.exists():
        target = target_dir / f"{stem}_{i}{suffix}"
        i += 1

    sha = hashlib.sha256(content)
    target.write_bytes(content)

    return {
        "name": safe,
        "storage_path": str(target.relative_to(project_dir(project_id))),
        "size": len(content),
        "sha256": sha.hexdigest(),
    }


def read_text(project_id: str, relpath: str, max_bytes: int = 100 * 1024 * 1024) -> str:
    """Read a file as text. Refuses files over max_bytes."""
    p = safe_join(project_id, relpath)
    if not p.exists():
        raise FileNotFoundError(relpath)
    if p.stat().st_size > max_bytes:
        raise ValueError(
            f"File too large for in-memory read: {p.stat().st_size} bytes "
            f"(max {max_bytes}). Use streaming endpoint."
        )
    return p.read_text()


def read_bytes(project_id: str, relpath: str, max_bytes: int = 100 * 1024 * 1024) -> bytes:
    p = safe_join(project_id, relpath)
    if not p.exists():
        raise FileNotFoundError(relpath)
    if p.stat().st_size > max_bytes:
        raise ValueError(f"File too large for in-memory read")
    return p.read_bytes()


def absolute_path(project_id: str, relpath: str) -> Path:
    """Get the absolute Path for streaming responses. Verifies existence."""
    p = safe_join(project_id, relpath)
    if not p.exists():
        raise FileNotFoundError(relpath)
    return p


def delete(project_id: str, relpath: str) -> bool:
    p = safe_join(project_id, relpath)
    if not p.exists():
        return False
    if p.is_file():
        p.unlink()
        return True
    return False


def total_size(project_id: str) -> int:
    """Sum of all file sizes under the project. For displaying quota."""
    total = 0
    for f in project_dir(project_id).rglob("*"):
        if f.is_file():
            total += f.stat().st_size
    return total
