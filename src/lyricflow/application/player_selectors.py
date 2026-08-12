"""Explicit matching semantics for configured MPRIS player selectors."""

from __future__ import annotations

from enum import Enum

from lyricflow.domain.models import PlayerSnapshot

_MPRIS_BUS_PREFIX = "org.mpris.MediaPlayer2."


class PlayerSelectorScope(Enum):
    """Supported selector scopes; bare selectors use SERVICE_FAMILY."""

    SERVICE = "service"
    SERVICE_FAMILY = "family"
    IDENTITY = "identity"
    DESKTOP_ENTRY = "desktop-entry"


def _service_suffix(value: str) -> str:
    if value.casefold().startswith(_MPRIS_BUS_PREFIX.casefold()):
        return value[len(_MPRIS_BUS_PREFIX) :]
    return value


def _parse(selector: str) -> tuple[PlayerSelectorScope, str]:
    scope_name, separator, value = selector.strip().partition(":")
    if separator:
        try:
            return PlayerSelectorScope(scope_name.casefold()), value.strip()
        except ValueError:
            pass
    return PlayerSelectorScope.SERVICE_FAMILY, selector.strip()


def _service_matches(actual: str, expected: str, *, include_instances: bool) -> bool:
    actual_key = _service_suffix(actual).casefold()
    expected_key = _service_suffix(expected).casefold()
    if not expected_key:
        return False
    if actual_key == expected_key:
        return True
    return include_instances and actual_key.startswith(f"{expected_key}.instance")


def player_selector_matches(snapshot: PlayerSnapshot, selector: str) -> bool:
    """Match one explicit selector without fuzzy descriptive-field crossover.

    Bare selectors are service-family selectors. For example, ``firefox``
    matches ``firefox`` and ``firefox.instance_*`` services. Typed selectors
    make exact service, identity, or desktop-entry matching deliberate.
    """

    scope, expected = _parse(selector)
    if scope is PlayerSelectorScope.SERVICE:
        return _service_matches(
            snapshot.service_name, expected, include_instances=False
        )
    if scope is PlayerSelectorScope.SERVICE_FAMILY:
        return _service_matches(snapshot.service_name, expected, include_instances=True)
    if scope is PlayerSelectorScope.IDENTITY:
        return (
            bool(expected)
            and snapshot.identity is not None
            and snapshot.identity.casefold() == expected.casefold()
        )
    return (
        bool(expected)
        and snapshot.desktop_entry is not None
        and snapshot.desktop_entry.casefold() == expected.casefold()
    )
