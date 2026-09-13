from typing import Final

plugin_path: Final[str]

def probe() -> tuple[int, int]: ...
def configure_locked_overlay(
    window_address: int,
    left: int,
    top: int,
    width: int,
    height: int,
) -> bool: ...
