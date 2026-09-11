"""Bounded local lyric exchange files with atomic non-destructive writes."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

MAX_LYRIC_EXCHANGE_BYTES = 2_000_000


def read_lyric_exchange(path: Path) -> str:
    """Read one regular UTF-8 lyric file within the parser's public size bound."""

    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise ValueError("lyric import must be an existing regular file")
    if resolved.stat().st_size > MAX_LYRIC_EXCHANGE_BYTES:
        raise ValueError("lyric import exceeds the supported size")
    payload = resolved.read_bytes()
    if len(payload) > MAX_LYRIC_EXCHANGE_BYTES:
        raise ValueError("lyric import exceeds the supported size")
    try:
        return payload.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("lyric import must be UTF-8 text") from error


def write_lyric_exchange(path: Path, content: str, *, overwrite: bool = False) -> Path:
    """Atomically write a private UTF-8 export, refusing replacement by default."""

    payload = content.encode("utf-8")
    if len(payload) > MAX_LYRIC_EXCHANGE_BYTES:
        raise ValueError("lyric export exceeds the supported size")
    resolved = path.expanduser().resolve()
    if resolved.exists() and not overwrite:
        raise FileExistsError("lyric export destination already exists")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{resolved.name}.", suffix=".tmp", dir=resolved.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(0o600)
        if overwrite:
            temporary.replace(resolved)
        else:
            # Hard-link publication is atomic and cannot replace a destination
            # created after the initial existence check.
            os.link(temporary, resolved)
            temporary.unlink()
    except OSError:
        temporary.unlink(missing_ok=True)
        raise
    return resolved
