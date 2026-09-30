"""The Clean Architecture dependency rule, enforced as a test.

A layering convention that is only written down in a README decays within
weeks — someone adds one convenient import and nothing complains. This walks
every module's imports with `ast` and fails the build the moment an inner layer
reaches outward.

The rule: **source code dependencies point inward only.**

    presentation ─┐
    infrastructure┼─► application ─► domain
    composition ──┘
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "penny"

#: layer -> the layers it is permitted to import (besides itself).
ALLOWED: dict[str, set[str]] = {
    "domain": set(),
    "application": {"domain"},
    "infrastructure": {"domain", "application"},
    "presentation": {"domain", "application", "infrastructure", "composition"},
    # The composition root exists precisely to know about everything.
    "composition": {"domain", "application", "infrastructure", "presentation"},
}


def _layer_of(module: str) -> str | None:
    """The layer a dotted module name belongs to, or None if it is not one.

    `penny.__init__` and third-party modules both return None — neither is a
    layer, and neither is subject to the rule.
    """
    parts = module.split(".")
    if len(parts) > 1 and parts[0] == "penny" and parts[1] in ALLOWED:
        return parts[1]
    return None


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
    return found


def _modules() -> list[tuple[str, Path]]:
    out = []
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        relative = path.relative_to(PACKAGE_ROOT.parent).with_suffix("")
        out.append((".".join(relative.parts), path))
    return out


class DependencyRule(unittest.TestCase):
    def test_package_is_layered(self):
        self.assertTrue(PACKAGE_ROOT.is_dir(), "penny package not found")
        layers = {
            p.name for p in PACKAGE_ROOT.iterdir() if p.is_dir() and not p.name.startswith("_")
        }
        self.assertEqual(
            layers, set(ALLOWED), "the set of layers changed without updating the rule"
        )

    def test_dependencies_point_inward(self):
        violations: list[str] = []

        for module, path in _modules():
            source_layer = _layer_of(module)
            if source_layer is None:
                continue
            permitted = ALLOWED[source_layer] | {source_layer}

            for imported in _imports(path):
                target_layer = _layer_of(imported)
                if target_layer is None or target_layer in permitted:
                    continue
                violations.append(
                    f"{module} -> {imported} ({source_layer} may not import {target_layer})"
                )

        self.assertEqual(violations, [], "Dependency rule violated:\n  " + "\n  ".join(violations))

    def test_domain_imports_no_third_party(self):
        """The domain layer must run on the standard library alone."""
        allowed_roots = {
            "penny",
            "__future__",
            "dataclasses",
            "datetime",
            "typing",
            "calendar",
            "enum",
            "decimal",
            "collections",
            "statistics",
            "abc",
            "uuid",
        }
        violations = []
        for module, path in _modules():
            if _layer_of(module) != "domain":
                continue
            for imported in _imports(path):
                if imported.split(".")[0] not in allowed_roots:
                    violations.append(f"{module} -> {imported}")

        self.assertEqual(
            violations,
            [],
            "domain must not depend on third-party packages:\n  " + "\n  ".join(violations),
        )

    def test_application_has_no_framework_imports(self):
        """Use cases must not know about FastAPI, LangChain or Anthropic."""
        forbidden = {
            "fastapi",
            "starlette",
            "langchain",
            "langgraph",
            "anthropic",
            "langchain_core",
            "langchain_anthropic",
            "opentelemetry",
            "uvicorn",
        }
        violations = []
        for module, path in _modules():
            if _layer_of(module) != "application":
                continue
            for imported in _imports(path):
                if imported.split(".")[0] in forbidden:
                    violations.append(f"{module} -> {imported}")

        self.assertEqual(
            violations, [], "application must stay framework-free:\n  " + "\n  ".join(violations)
        )


LEDGER_ROOT = PACKAGE_ROOT.parent / "penny_ledger"
INGEST_ROOT = PACKAGE_ROOT.parent / "penny_ingest"


def _package_modules(root: Path) -> list[tuple[str, Path]]:
    if not root.is_dir():
        return []
    out = []
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root.parent).with_suffix("")
        out.append((".".join(relative.parts), path))
    return out


class PackageRule(unittest.TestCase):
    """The three-package rule from the design doc (Package ownership)."""

    def test_ledger_depends_only_on_domain_ports_and_the_driver(self):
        allowed_roots = {
            "penny_ledger",
            "sqlalchemy",
            "alembic",
            "__future__",
            "collections",
            "dataclasses",
            "datetime",
            "decimal",
            "json",
            "logging",
            "os",
            "typing",
            "uuid",
        }
        violations = []
        for module, path in _package_modules(LEDGER_ROOT):
            for imported in _imports(path):
                root = imported.split(".")[0]
                if root == "penny":
                    if not (
                        imported.startswith("penny.domain")
                        or imported.startswith("penny.application.ports")
                        or imported == "penny.infrastructure.observability.telemetry"
                    ):
                        violations.append(f"{module} -> {imported}")
                elif root not in allowed_roots:
                    violations.append(f"{module} -> {imported}")
        self.assertEqual(
            violations, [], "penny_ledger reached outward:\n  " + "\n  ".join(violations)
        )

    def test_chat_never_imports_ingest(self):
        violations = [
            f"{module} -> {imported}"
            for module, path in _modules()
            for imported in _imports(path)
            if imported.split(".")[0] == "penny_ingest"
        ]
        self.assertEqual(
            violations, [], "penny must not import penny_ingest:\n  " + "\n  ".join(violations)
        )

    def test_ingest_never_imports_chat_layers(self):
        violations = [
            f"{module} -> {imported}"
            for module, path in _package_modules(INGEST_ROOT)
            for imported in _imports(path)
            if imported.startswith(
                (
                    "penny.application",
                    "penny.infrastructure",
                    "penny.presentation",
                    "penny.composition",
                )
            )
            and not imported.startswith("penny.application.ports")
        ]
        self.assertEqual(
            violations,
            [],
            "penny_ingest must not import chat layers:\n  " + "\n  ".join(violations),
        )


if __name__ == "__main__":
    unittest.main()
