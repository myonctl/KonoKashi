"""Standard-library local path canonicalization adapter."""

from __future__ import annotations

import os
from pathlib import Path

from lyriflux.application.ports import LocalPathResolution


class FilesystemLocalPathCanonicalizer:
    """Canonicalize paths best-effort without reading or hashing media data."""

    def canonicalize(self, path: str) -> LocalPathResolution:
        """Resolve existing symlinks and retain a lexical path on access errors."""

        original = Path(path)
        lexical = Path(os.path.abspath(os.path.normpath(path)))
        try:
            canonical = original.resolve(strict=False)
            exists: bool | None = canonical.exists()
            return LocalPathResolution(
                canonical_path=str(canonical),
                exists=exists,
                symlink_resolved=canonical != lexical,
            )
        except OSError as error:
            return LocalPathResolution(
                canonical_path=str(lexical),
                exists=None,
                symlink_resolved=False,
                warning=f"path canonicalization was limited: {error}",
            )
