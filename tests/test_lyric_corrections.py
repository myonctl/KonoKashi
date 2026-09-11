"""Immutable-source lyric correction, validation, persistence, and exchange tests."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from konokashi.application.lyric_corrections import (
    LyricCorrectionError,
    LyricCorrectionService,
)
from konokashi.application.lyric_exchange import (
    LyricExchangeFormat,
    document_lyric_edits,
    serialize_lyric_edits,
)
from konokashi.application.representations import original_lines
from konokashi.domain.lyric_corrections import LyricLineCorrection, LyricLineEdit
from konokashi.domain.lyrics import (
    ContentProvenance,
    LyricDocumentKind,
    LyricTimingLevel,
    TimingProvenance,
)
from konokashi.infrastructure.lyrics.documents import build_lyric_document
from konokashi.infrastructure.lyrics.files import (
    read_lyric_exchange,
    write_lyric_exchange,
)
from konokashi.infrastructure.lyrics.lrc import parse_lyrics_text
from konokashi.infrastructure.storage.bootstrap import open_storage

NOW = datetime(2026, 9, 11, 20, tzinfo=UTC)


def _document(text: str, *, duration_ms: int = 180_000):
    parsed = parse_lyrics_text(text, duration_ms=duration_ms)
    return build_lyric_document(
        parsed,
        source_name="TestProvider",
        provenance=ContentProvenance.PROVIDER,
        provider_record_id="immutable-record",
        retrieved_at=NOW,
        duration_ms=duration_ms,
    )


def _service(path: Path) -> tuple[LyricCorrectionService, object]:
    storage = open_storage(path)
    return (
        LyricCorrectionService(
            storage.lyric_corrections,
            now=lambda: NOW,
            parser=lambda text, duration: parse_lyrics_text(text, duration_ms=duration),
        ),
        storage,
    )


def test_text_and_timestamp_edits_persist_without_replacing_source(
    tmp_path: Path,
) -> None:
    service, storage = _service(tmp_path / "corrections.sqlite3")
    source = _document(
        "[00:01.000]<00:01.000>Hello <00:01.500>world\n[00:02.000]<00:02.000>Second"
    )
    storage.lyrics.put(source)  # type: ignore[attr-defined]
    lines = original_lines(source)

    saved = service.replace(
        source,
        (
            LyricLineEdit(lines[0].line_id, "Hello earth", 1_250),
            LyricLineEdit(lines[1].line_id, "Second", 2_250),
        ),
    )

    assert saved == 2
    restarted = LyricCorrectionService(
        open_storage(tmp_path / "corrections.sqlite3").lyric_corrections
    )
    projection = restarted.project(source)
    effective = original_lines(projection.document)
    assert effective[0].text == "Hello earth"
    assert effective[0].start_ms == 1_250
    assert effective[0].end_ms == 1_750
    assert effective[0].timing_segments == ()
    assert effective[1].start_ms == 2_250
    assert effective[1].timing_segments[0].start_ms == 2_250
    assert effective[1].timing_provenance is TimingProvenance.USER_EDITED
    assert projection.document.raw_text_checksum is None
    assert projection.document.representations[0].provenance is ContentProvenance.USER
    assert storage.lyrics.get(source.document_id) == source  # type: ignore[attr-defined]


def test_reset_reverts_complete_projection_to_source(tmp_path: Path) -> None:
    service, storage = _service(tmp_path / "reset.sqlite3")
    source = _document("[00:01.000]First\n[00:02.000]Second")
    storage.lyrics.put(source)  # type: ignore[attr-defined]
    lines = original_lines(source)
    service.replace(
        source,
        (
            LyricLineEdit(lines[0].line_id, "Changed", 1_000),
            LyricLineEdit(lines[1].line_id, "Second", 2_000),
        ),
    )

    assert service.reset(source) == 1
    projection = service.project(source)
    assert projection.document is source
    assert projection.applied_corrections == 0


def test_plain_tap_sync_requires_every_line_and_becomes_line_timed(
    tmp_path: Path,
) -> None:
    service, storage = _service(tmp_path / "plain.sqlite3")
    source = _document("First\nSecond")
    storage.lyrics.put(source)  # type: ignore[attr-defined]
    lines = original_lines(source)

    with pytest.raises(LyricCorrectionError, match="every line"):
        service.replace(
            source,
            (
                LyricLineEdit(lines[0].line_id, "First", 1_000),
                LyricLineEdit(lines[1].line_id, "Second", None),
            ),
        )

    service.replace(
        source,
        (
            LyricLineEdit(lines[0].line_id, "First", 1_000),
            LyricLineEdit(lines[1].line_id, "Second", 2_000),
        ),
    )
    effective = service.project(source).document
    assert effective.kind is LyricDocumentKind.SYNCED
    assert effective.timing_level is LyricTimingLevel.LINE
    assert tuple(line.start_ms for line in original_lines(effective)) == (1_000, 2_000)


def test_editor_rejects_reversed_out_of_duration_and_incomplete_snapshots(
    tmp_path: Path,
) -> None:
    service, storage = _service(tmp_path / "validation.sqlite3")
    source = _document("[00:01.000]First\n[00:02.000]Second", duration_ms=3_000)
    storage.lyrics.put(source)  # type: ignore[attr-defined]
    lines = original_lines(source)

    with pytest.raises(LyricCorrectionError, match="source order"):
        service.replace(
            source,
            (LyricLineEdit(lines[0].line_id, "First", 1_000),),
        )
    with pytest.raises(LyricCorrectionError, match="reverse"):
        service.replace(
            source,
            (
                LyricLineEdit(lines[0].line_id, "First", 2_500),
                LyricLineEdit(lines[1].line_id, "Second", 2_000),
            ),
        )
    with pytest.raises(LyricCorrectionError, match="duration"):
        service.replace(
            source,
            (
                LyricLineEdit(lines[0].line_id, "First", 1_000),
                LyricLineEdit(lines[1].line_id, "Second", 5_001),
            ),
        )


def test_stale_baseline_is_retained_as_evidence_but_never_applied(
    tmp_path: Path,
) -> None:
    service, storage = _service(tmp_path / "stale.sqlite3")
    source = _document("First\nSecond")
    storage.lyrics.put(source)  # type: ignore[attr-defined]
    line = original_lines(source)[0]
    storage.lyric_corrections.replace(  # type: ignore[attr-defined]
        source.document_id,
        (
            LyricLineCorrection(
                source.document_id,
                line.line_id,
                "outdated source text",
                None,
                "unsafe stale edit",
                None,
                NOW,
                NOW,
            ),
        ),
    )

    projection = service.project(source)
    assert projection.stale_corrections == 1
    assert original_lines(projection.document)[0].text == "First"
    assert "stale" in projection.diagnostics[0]


def test_plain_and_lrc_exchange_round_trip_into_same_overlay(tmp_path: Path) -> None:
    service, storage = _service(tmp_path / "exchange.sqlite3")
    source = _document("First\nSecond")
    storage.lyrics.put(source)  # type: ignore[attr-defined]

    assert service.import_text(source, "[00:01.250]Uno\n[00:02.500]Dos\n") == 2
    effective = service.project(source).document
    edits = document_lyric_edits(effective)
    assert serialize_lyric_edits(edits, LyricExchangeFormat.PLAIN) == "Uno\nDos\n"
    assert serialize_lyric_edits(edits, LyricExchangeFormat.LRC) == (
        "[00:01.250]Uno\n[00:02.500]Dos\n"
    )

    assert service.import_text(source, "Three\nFour\n") == 2
    plain = service.project(source).document
    assert tuple(line.start_ms for line in original_lines(plain)) == (None, None)
    assert tuple(line.text for line in original_lines(plain)) == ("Three", "Four")


def test_import_rejects_ambiguous_line_count_without_changing_existing_layer(
    tmp_path: Path,
) -> None:
    service, storage = _service(tmp_path / "import-atomic.sqlite3")
    source = _document("First\nSecond")
    storage.lyrics.put(source)  # type: ignore[attr-defined]
    service.import_text(source, "Changed\nStill second\n")

    with pytest.raises(LyricCorrectionError, match="exactly one line"):
        service.import_text(source, "Only one\n")

    assert tuple(
        line.text for line in original_lines(service.project(source).document)
    ) == (
        "Changed",
        "Still second",
    )


def test_exchange_files_are_bounded_private_atomic_and_refuse_replacement(
    tmp_path: Path,
) -> None:
    target = tmp_path / "portable.lrc"
    assert write_lyric_exchange(target, "[00:01.000]One\n") == target
    assert target.stat().st_mode & 0o777 == 0o600
    assert read_lyric_exchange(target) == "[00:01.000]One\n"

    with pytest.raises(FileExistsError, match="already exists"):
        write_lyric_exchange(target, "replacement")
    assert read_lyric_exchange(target) == "[00:01.000]One\n"
    write_lyric_exchange(target, "replacement\n", overwrite=True)
    assert read_lyric_exchange(target) == "replacement\n"

    oversized = tmp_path / "oversized.txt"
    oversized.write_bytes(b"x" * 2_000_001)
    with pytest.raises(ValueError, match="supported size"):
        read_lyric_exchange(oversized)
