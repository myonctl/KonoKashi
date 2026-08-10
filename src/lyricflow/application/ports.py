"""Application ports and local-diagnostic value types."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class DiagnosticStatus(Enum):
    """Machine-readable outcome for one diagnostic check."""

    OK = "OK"
    WARNING = "WARN"
    FAILURE = "FAIL"


@dataclass(frozen=True, slots=True)
class DiagnosticCheck:
    """One local prerequisite result produced by a diagnostic adapter."""

    name: str
    status: DiagnosticStatus
    message: str
    required: bool = True
