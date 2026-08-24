"""Formatting and exit-code policy for the local diagnostics command."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from lyriflux.application.ports import DiagnosticCheck, DiagnosticStatus


@dataclass(frozen=True, slots=True)
class DoctorReport:
    """A complete, presentation-ready local diagnostics report."""

    checks: tuple[DiagnosticCheck, ...]

    @property
    def exit_code(self) -> int:
        """Return nonzero when a required local prerequisite failed."""

        return int(
            any(
                check.required and check.status is DiagnosticStatus.FAILURE
                for check in self.checks
            )
        )

    def render(self) -> str:
        """Render stable, human-readable diagnostics suitable for a terminal."""

        lines = ["LyriFlux doctor"]
        lines.extend(
            f"[{check.status.value}] {check.name}: {check.message}"
            for check in self.checks
        )
        ok_count = sum(check.status is DiagnosticStatus.OK for check in self.checks)
        warning_count = sum(
            check.status is DiagnosticStatus.WARNING for check in self.checks
        )
        failure_count = sum(
            check.status is DiagnosticStatus.FAILURE for check in self.checks
        )
        lines.append(
            "Summary: "
            f"{ok_count} passed, "
            f"{warning_count} {'warning' if warning_count == 1 else 'warnings'}, "
            f"{failure_count} {'failure' if failure_count == 1 else 'failures'}"
        )
        return "\n".join(lines)


def build_doctor_report(checks: Iterable[DiagnosticCheck]) -> DoctorReport:
    """Apply doctor reporting policy to adapter-provided checks."""

    return DoctorReport(tuple(checks))
