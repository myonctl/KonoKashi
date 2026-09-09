"""Local-only, actionable results for a completed music-library scan."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from konokashi.domain.library import LibraryReviewItem, LibraryScanSummary


class LibraryReviewDialog(QDialog):
    """Show bounded scan details that are deliberately excluded from exports."""

    scan_again_requested = Signal()

    def __init__(
        self,
        summary: LibraryScanSummary,
        review_items: tuple[LibraryReviewItem, ...],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Music library results")
        self.resize(760, 480)
        layout = QVBoxLayout(self)

        heading = QLabel(f"Scan {summary.status.value}")
        heading.setAccessibleName("Library scan status")
        layout.addWidget(heading)
        counters = QLabel(
            f"Discovered {summary.discovered} · Processed {summary.processed} · "
            f"Unchanged {summary.unchanged} · Moved {summary.moved} · "
            f"Missing {summary.missing} · Review {summary.review} · "
            f"Downloaded {summary.downloaded} · "
            f"Download misses {summary.download_misses} · Errors {summary.errors}"
        )
        counters.setWordWrap(True)
        counters.setAccessibleName("Library scan counters")
        layout.addWidget(counters)

        privacy = QLabel(
            "Paths and detailed errors stay on this device and are not included "
            "in diagnostic exports."
        )
        privacy.setWordWrap(True)
        layout.addWidget(privacy)

        self.items = QTreeWidget()
        self.items.setAccessibleName("Library scan issues and review items")
        self.items.setHeaderLabels(("Type", "Path", "Details"))
        self.items.setRootIsDecorated(False)
        for issue in summary.issues:
            self._add_item(issue.category.value, issue.path, issue.detail)
        for item in review_items:
            artists = " & ".join(item.artists) or "unknown artist"
            identity = f"{artists} — {item.title or 'unknown title'}"
            self._add_item("review", item.path, f"{item.reason} ({identity})")
        self.items.resizeColumnToContents(0)
        layout.addWidget(self.items, 1)

        self.copy_path_button = QPushButton("Copy path")
        self.open_folder_button = QPushButton("Open containing folder")
        self.scan_again_button = QPushButton("Scan again")
        for button in (self.copy_path_button, self.open_folder_button):
            button.setEnabled(False)
        self.copy_path_button.clicked.connect(self._copy_path)
        self.open_folder_button.clicked.connect(self._open_folder)
        self.scan_again_button.clicked.connect(self._scan_again)
        self.items.itemSelectionChanged.connect(self._selection_changed)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.addButton(self.copy_path_button, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.addButton(
            self.open_folder_button, QDialogButtonBox.ButtonRole.ActionRole
        )
        buttons.addButton(
            self.scan_again_button, QDialogButtonBox.ButtonRole.ActionRole
        )
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _add_item(self, kind: str, path: str | None, details: str) -> None:
        row = QTreeWidgetItem((kind, path or "—", details))
        row.setData(0, Qt.ItemDataRole.UserRole, path)
        self.items.addTopLevelItem(row)

    def _selected_path(self) -> str | None:
        selected = self.items.selectedItems()
        if not selected:
            return None
        value = selected[0].data(0, Qt.ItemDataRole.UserRole)
        return value if isinstance(value, str) and value else None

    def _selection_changed(self) -> None:
        enabled = self._selected_path() is not None
        self.copy_path_button.setEnabled(enabled)
        self.open_folder_button.setEnabled(enabled)

    def _copy_path(self) -> None:
        path = self._selected_path()
        if path is not None:
            QGuiApplication.clipboard().setText(path)

    def _open_folder(self) -> None:
        path = self._selected_path()
        if path is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).parent)))

    def _scan_again(self) -> None:
        self.scan_again_requested.emit()
        self.accept()
