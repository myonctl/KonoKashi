"""XDG desktop portal boundary regressions."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, cast

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtDBus import QDBusConnection, QDBusMessage, QDBusVariant
from PySide6.QtWidgets import QApplication, QWidget

from konokashi.infrastructure import desktop_portal
from konokashi.infrastructure.desktop_portal import (
    DesktopPortalError,
    PortalDirectoryRequest,
    is_flatpak_session,
    portal_parent_identifier,
    selected_directory,
)


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    application = QApplication.instance() or QApplication(["portal-test"])
    assert isinstance(application, QApplication)
    return application


@dataclass(slots=True)
class _PendingResult:
    arguments: tuple[object, ...] = ()
    error: _FakeError | None = None


class _FakeError:
    def __init__(self, name: str = "", message: str = "") -> None:
        self._name = name
        self._message = message

    def name(self) -> str:
        return self._name

    def message(self) -> str:
        return self._message


class _FakeReply:
    def __init__(self, arguments: tuple[object, ...]) -> None:
        self._arguments = arguments

    def arguments(self) -> tuple[object, ...]:
        return self._arguments


class _ControlledWatcher(QObject):
    finished = Signal()
    instances: ClassVar[list[_ControlledWatcher]] = []

    def __init__(self, pending: _PendingResult, parent: QObject) -> None:
        super().__init__(parent)
        self._pending = pending
        self._finished = False
        self.instances.append(self)

    def isFinished(self) -> bool:
        return self._finished

    def isError(self) -> bool:
        return self._pending.error is not None

    def error(self) -> _FakeError:
        return self._pending.error or _FakeError()

    def reply(self) -> _FakeReply:
        return _FakeReply(self._pending.arguments)

    def complete(self) -> None:
        self._finished = True
        self.finished.emit()


class _FakeConnection:
    def __init__(self, *, connected: bool = True) -> None:
        self.connected = connected
        self.calls: list[QDBusMessage] = []
        self.signal_paths: list[str] = []
        self.receiver: object | None = None
        self.reply = _PendingResult()

    def isConnected(self) -> bool:
        return self.connected

    def baseService(self) -> str:
        return ":1.42"

    def connect(self, *arguments: object) -> bool:
        self.signal_paths.append(cast(str, arguments[1]))
        self.receiver = arguments[4]
        return True

    def disconnect(self, *arguments: object) -> bool:
        return True

    def lastError(self) -> _FakeError:
        return _FakeError()

    def asyncCall(self, message: QDBusMessage, _timeout_ms: int) -> _PendingResult:
        self.calls.append(message)
        return self.reply


def _request(
    connection: _FakeConnection, parent: QObject | None = None
) -> PortalDirectoryRequest:
    return PortalDirectoryRequest(
        parent,
        connection=cast(QDBusConnection, cast(Any, connection)),
        token="konokashi_test",
    )


def test_flatpak_detection_uses_only_the_sandbox_marker(tmp_path: Path) -> None:
    marker = tmp_path / ".flatpak-info"
    assert not is_flatpak_session(marker)
    marker.write_text("[Application]\nname=example\n", encoding="utf-8")
    assert is_flatpak_session(marker)


def test_portal_parent_identifier_uses_x11_window_id(
    qt_app: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeWindow:
        @staticmethod
        def winId() -> int:
            return 0xA2B

    monkeypatch.setattr(desktop_portal.QGuiApplication, "platformName", lambda: "xcb")

    assert portal_parent_identifier(cast(Any, FakeWindow())) == "x11:a2b"


def test_portal_parent_identifier_uses_packaged_wayland_bridge(
    qt_app: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    bridge_path = tmp_path / "libkonokashi_portal_parent.so"
    bridge_path.touch()

    class FakeExport:
        argtypes: object = None
        restype: object = None

        def __call__(
            self,
            _pointer: object,
            output: object,
            _capacity: int,
        ) -> int:
            value = b"wayland:konokashi-test-parent"
            desktop_portal.ctypes.memmove(output, value, len(value))
            return len(value)

    class FakeBridge:
        konokashi_portal_parent_identifier = FakeExport()

    monkeypatch.setattr(
        desktop_portal.QGuiApplication,
        "platformName",
        lambda: "wayland",
    )
    monkeypatch.setattr(desktop_portal.ctypes, "CDLL", lambda _path: FakeBridge())
    monkeypatch.setattr(desktop_portal, "getCppPointer", lambda _window: (42,))

    assert (
        portal_parent_identifier(cast(Any, object()), bridge_path=bridge_path)
        == "wayland:konokashi-test-parent"
    )


def test_portal_parent_identifier_rejects_missing_wayland_bridge(
    qt_app: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        desktop_portal.QGuiApplication,
        "platformName",
        lambda: "wayland",
    )

    with pytest.raises(DesktopPortalError, match="bridge is missing"):
        portal_parent_identifier(
            cast(Any, object()),
            bridge_path=tmp_path / "missing.so",
        )


def test_selected_directory_accepts_one_local_unicode_uri(tmp_path: Path) -> None:
    folder = tmp_path / "音楽"
    result = selected_directory(
        {"uris": QDBusVariant([QUrl.fromLocalFile(str(folder)).toString()])}
    )

    assert result == str(folder)


@pytest.mark.parametrize(
    "results",
    [
        {},
        {"uris": []},
        {"uris": ["file:///one", "file:///two"]},
        {"uris": ["https://example.invalid/music"]},
        {"uris": [42]},
        "not a result map",
    ],
)
def test_selected_directory_rejects_malformed_or_nonlocal_results(
    results: object,
) -> None:
    with pytest.raises(DesktopPortalError):
        selected_directory(results)


def test_request_subscribes_before_nonblocking_directory_call_and_completes(
    qt_app: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _ControlledWatcher.instances.clear()
    monkeypatch.setattr(desktop_portal, "QDBusPendingCallWatcher", _ControlledWatcher)
    connection = _FakeConnection()
    request = _request(connection)
    completed: list[tuple[object, object]] = []
    request.finished.connect(lambda value, error: completed.append((value, error)))

    request.start(parent_window="wayland:konokashi-parent")

    assert connection.signal_paths == [
        "/org/freedesktop/portal/desktop/request/1_42/konokashi_test"
    ]
    assert len(connection.calls) == 1
    message = connection.calls[0]
    assert message.member() == "OpenFile"
    parent_window, title, options = message.arguments()
    assert parent_window == "wayland:konokashi-parent"
    assert title == "Add music library folder"
    assert options["directory"] is True
    assert options["multiple"] is False
    assert options["modal"] is True
    assert options["handle_token"] == "konokashi_test"
    assert completed == []

    connection.reply.arguments = (
        "/org/freedesktop/portal/desktop/request/1_42/konokashi_test",
    )
    _ControlledWatcher.instances[0].complete()
    receiver = cast(Any, connection.receiver)
    selected = tmp_path / "Музыка"
    receiver.response(
        0,
        {"uris": [QUrl.fromLocalFile(str(selected)).toString()]},
    )

    assert completed == [(str(selected), None)]


def test_request_reports_cancel_error_and_disconnected_bus(
    qt_app: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ControlledWatcher.instances.clear()
    monkeypatch.setattr(desktop_portal, "QDBusPendingCallWatcher", _ControlledWatcher)
    connection = _FakeConnection()
    request = _request(connection)
    completed: list[tuple[object, object]] = []
    request.finished.connect(lambda value, error: completed.append((value, error)))
    request.start()
    cast(Any, connection.receiver).response(1, {})
    assert completed == [(None, None)]

    failed_connection = _FakeConnection(connected=False)
    failed = _request(failed_connection)
    failures: list[tuple[object, object]] = []
    failed.finished.connect(lambda value, error: failures.append((value, error)))
    QTimer.singleShot(0, failed.start)
    qt_app.processEvents()
    assert failures and failures[0][0] is None
    assert isinstance(failures[0][1], DesktopPortalError)


def test_parent_destruction_closes_request_without_emitting(
    qt_app: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ControlledWatcher.instances.clear()
    monkeypatch.setattr(desktop_portal, "QDBusPendingCallWatcher", _ControlledWatcher)
    connection = _FakeConnection()
    parent = QWidget()
    request = _request(connection, parent)
    completed: list[tuple[object, object]] = []
    request.finished.connect(lambda value, error: completed.append((value, error)))
    request.start()

    parent.deleteLater()
    qt_app.sendPostedEvents()
    qt_app.processEvents()

    assert completed == []
