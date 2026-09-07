"""Production integer clocks for interpolation and Linux suspend detection."""

from __future__ import annotations

import time


class LinuxClock:
    """Expose monotonic and, where supported, suspend-aware boottime clocks."""

    def monotonic_ns(self) -> int:
        """Return the non-wall-clock interpolation timestamp."""

        return time.monotonic_ns()

    def boottime_ns(self) -> int | None:
        """Return CLOCK_BOOTTIME on Linux without fabricating a fallback."""

        clock_id = getattr(time, "CLOCK_BOOTTIME", None)
        if clock_id is None:
            return None
        try:
            return time.clock_gettime_ns(clock_id)
        except (OSError, ValueError):
            return None
