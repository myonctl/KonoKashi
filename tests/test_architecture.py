"""Import-boundary regression tests for dependency-free core layers."""

import ast
from pathlib import Path


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)
    return imported


def test_core_layers_do_not_import_frameworks_or_adapters() -> None:
    source_root = Path(__file__).parents[1] / "src" / "lyricflow"
    forbidden_roots = {
        "PySide6",
        "httpx",
        "sqlite3",
        "lyricflow.infrastructure",
        "lyricflow.presentation",
    }

    violations: list[str] = []
    for layer in ("domain", "application"):
        for path in (source_root / layer).rglob("*.py"):
            for imported in _imports(path):
                if any(
                    imported == root or imported.startswith(f"{root}.")
                    for root in forbidden_roots
                ):
                    violations.append(f"{path.relative_to(source_root)}: {imported}")

    assert violations == []


def test_pyside_imports_are_confined_to_qt_adapters() -> None:
    source_root = Path(__file__).parents[1] / "src" / "lyricflow"
    violations: list[str] = []

    for path in source_root.rglob("*.py"):
        relative_parts = path.relative_to(source_root).parts
        if "infrastructure" in relative_parts or relative_parts[:2] == (
            "presentation",
            "desktop",
        ):
            continue
        for imported in _imports(path):
            if imported == "PySide6" or imported.startswith("PySide6."):
                violations.append(f"{path.relative_to(source_root)}: {imported}")

    assert violations == []
