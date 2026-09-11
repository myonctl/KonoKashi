"""Typed, non-secret rendering values for local storage diagnostics."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LibraryScanDiagnostics:
    """Latest scan aggregates safe for CLI output and support exports."""

    status: str
    discovered: int
    processed: int
    unchanged: int
    moved: int
    missing: int
    review: int
    downloaded: int
    download_misses: int
    errors: int
    error_categories: Mapping[str, int]


@dataclass(frozen=True, slots=True)
class StorageCounts:
    """Safe aggregate counts that do not reveal private stored content."""

    source_identities: int = 0
    track_overrides: int = 0
    lyric_documents: int = 0
    lyrics_matches: int = 0
    lyric_match_rejections: int = 0
    provider_cache_entries: int = 0
    representation_candidates: int = 0
    representation_decisions: int = 0
    language_overrides: int = 0
    lyric_document_delays: int = 0
    lyric_line_corrections: int = 0
    audio_output_calibrations: int = 0
    library_roots: int = 0
    library_tracks: int = 0
    library_review_items: int = 0
    library_scan_runs: int = 0


@dataclass(frozen=True, slots=True)
class StorageStatus:
    """Read-only database status suitable for normal CLI diagnostics."""

    path: str
    exists: bool
    current_schema_version: int
    schema_version: int | None
    migration_status: str
    readable: bool
    writable: bool
    integrity_status: str
    counts: StorageCounts | None = None
    latest_library_scan: LibraryScanDiagnostics | None = None
    error: str | None = None

    @property
    def exit_code(self) -> int:
        """Return failure only for an existing but unusable/incompatible database."""

        return int(self.error is not None)


def render_storage_status(status: StorageStatus) -> str:
    """Render status without SQL internals, lyrics, or private media filenames."""

    lines = [
        "KonoKashi storage status",
        f"database path: {status.path}",
        f"exists: {'yes' if status.exists else 'no'}",
        "schema version: "
        + (
            "not initialized"
            if status.schema_version is None
            else str(status.schema_version)
        ),
        f"current schema version: {status.current_schema_version}",
        f"migration status: {status.migration_status}",
        f"readable: {'yes' if status.readable else 'no'}",
        f"writable: {'yes' if status.writable else 'no'}",
        f"integrity: {status.integrity_status}",
    ]
    if status.counts is not None:
        lines.extend(
            (
                f"source identities: {status.counts.source_identities}",
                f"approved track corrections: {status.counts.track_overrides}",
                f"lyric documents: {status.counts.lyric_documents}",
                f"lyrics matches: {status.counts.lyrics_matches}",
                f"lyric match rejections: {status.counts.lyric_match_rejections}",
                f"provider cache entries: {status.counts.provider_cache_entries}",
                f"representation candidates: {status.counts.representation_candidates}",
                f"representation decisions: {status.counts.representation_decisions}",
                f"lyric language overrides: {status.counts.language_overrides}",
                f"lyric document delays: {status.counts.lyric_document_delays}",
                f"lyric line corrections: {status.counts.lyric_line_corrections}",
                f"audio output calibrations: {status.counts.audio_output_calibrations}",
                f"library roots: {status.counts.library_roots}",
                f"library tracks: {status.counts.library_tracks}",
                f"library review items: {status.counts.library_review_items}",
                f"library scan runs: {status.counts.library_scan_runs}",
            )
        )
    if status.latest_library_scan is not None:
        scan = status.latest_library_scan
        lines.extend(
            (
                f"latest library scan status: {scan.status}",
                f"latest library scan discovered: {scan.discovered}",
                f"latest library scan processed: {scan.processed}",
                f"latest library scan unchanged: {scan.unchanged}",
                f"latest library scan moved: {scan.moved}",
                f"latest library scan missing: {scan.missing}",
                f"latest library scan review: {scan.review}",
                f"latest library scan downloaded: {scan.downloaded}",
                f"latest library scan download misses: {scan.download_misses}",
                f"latest library scan errors: {scan.errors}",
            )
        )
        lines.extend(
            f"latest library scan {category} errors: {count}"
            for category, count in sorted(scan.error_categories.items())
        )
    if status.error is not None:
        lines.append(f"error: {status.error}")
    return "\n".join(lines)
