"""Executable dependency rules for the application layers.

These tests intentionally inspect syntax rather than importing application modules so
that optional integrations and runtime configuration cannot hide a layering breach.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


PACKAGE_ROOT = Path(__file__).parents[2] / "knowledge_service"
PRIVATE_REPOSITORY_ATTRIBUTES = frozenset({"_transaction", "_put", "_db"})


@pytest.fixture
def postgres_backend():
    """Architecture checks are source-only and must not require PostgreSQL."""
    yield


def _python_files(layer: str):
    return sorted((PACKAGE_ROOT / layer).rglob("*.py"))


def _module_name(path: Path) -> str:
    relative = path.relative_to(PACKAGE_ROOT.parent).with_suffix("")
    return ".".join(relative.parts)


def _resolved_imports(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    module = _module_name(path)
    package = module.rpartition(".")[0]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                anchor = package.split(".")
                anchor = anchor[: len(anchor) - node.level + 1]
                imported = ".".join([*anchor, node.module or ""]).rstrip(".")
            else:
                imported = node.module or ""
            yield node.lineno, imported
            for alias in node.names:
                if alias.name != "*":
                    yield node.lineno, ".".join(
                        part for part in (imported, alias.name) if part)


def _forbidden_imports(source_layers, target_layers):
    failures = []
    prefixes = tuple(f"knowledge_service.{layer}" for layer in target_layers)
    for layer in source_layers:
        for path in _python_files(layer):
            for line, imported in _resolved_imports(path):
                if imported in prefixes or imported.startswith(
                        tuple(prefix + "." for prefix in prefixes)):
                    failures.append(
                        f"{path.relative_to(PACKAGE_ROOT.parent)}:{line} imports {imported}")
    return failures


def _attributes(layers, names):
    failures = []
    for layer in layers:
        for path in _python_files(layer):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr in names:
                    failures.append(
                        f"{path.relative_to(PACKAGE_ROOT.parent)}:{node.lineno} uses .{node.attr}")
    return sorted(set(failures))


def _assert_no_violations(failures):
    assert not failures, "\n" + "\n".join(failures)


def test_repository_and_integrations_do_not_import_services():
    _assert_no_violations(
        _forbidden_imports(("repository", "integrations"), ("services",)))


def test_services_do_not_import_api():
    _assert_no_violations(_forbidden_imports(("services",), ("api",)))


def test_api_does_not_import_repository_or_integrations():
    _assert_no_violations(
        _forbidden_imports(("api",), ("repository", "integrations")))


def test_api_does_not_access_repository_attributes():
    _assert_no_violations(_attributes(("api",), {"repository"}))


def test_api_and_services_do_not_use_private_repository_primitives():
    _assert_no_violations(
        _attributes(("api", "services"), PRIVATE_REPOSITORY_ATTRIBUTES))


@pytest.mark.parametrize(
    ("relative_path", "statement"),
    [
        ("api/example.py", "from knowledge_service import services"),
        ("repository/example.py", "from .. import services"),
    ],
)
def test_resolved_imports_expand_import_from_aliases(
        tmp_path, monkeypatch, relative_path, statement):
    package_root = tmp_path / "knowledge_service"
    path = package_root / relative_path
    path.parent.mkdir(parents=True)
    path.write_text(statement, encoding="utf-8")
    monkeypatch.setitem(_resolved_imports.__globals__, "PACKAGE_ROOT", package_root)

    assert (1, "knowledge_service.services") in set(_resolved_imports(path))
