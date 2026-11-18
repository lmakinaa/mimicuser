"""Import lint — the layering rules, enforced rather than documented.

The brief's hardest architectural requirement is that no single agent runtime
becomes the platform's abstraction. That is only true if it is checked.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

BACKEND = Path(__file__).resolve().parents[2]

# layer → package prefixes it may NOT import
FORBIDDEN: dict[str, tuple[str, ...]] = {
    "domain": ("app", "repositories", "engines", "tools", "orchestration", "services",
               "sqlalchemy", "fastapi", "playwright", "anthropic", "claude_agent_sdk"),
    "storage": ("app", "repositories", "engines", "orchestration", "services",
                "sqlalchemy", "fastapi", "claude_agent_sdk"),
    "context": ("app", "repositories", "engines", "sqlalchemy", "fastapi",
                "claude_agent_sdk"),
    "tools": ("app", "repositories", "engines", "sqlalchemy", "fastapi",
              "claude_agent_sdk"),
    "orchestration": ("app", "repositories", "sqlalchemy", "fastapi"),
    "repositories": ("app", "orchestration", "engines", "tools", "fastapi", "playwright"),
}


def _module_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


def _python_files(package: str) -> list[Path]:
    return sorted((BACKEND / package).rglob("*.py"))


@pytest.mark.parametrize("package", sorted(FORBIDDEN))
def test_layer_does_not_import_upward(package: str):
    forbidden = FORBIDDEN[package]
    violations: list[str] = []
    for path in _python_files(package):
        for name in _module_names(path):
            if name in forbidden:
                violations.append(f"{path.relative_to(BACKEND)} imports {name}")
    assert not violations, "layering violated:\n" + "\n".join(violations)


def test_claude_agent_sdk_is_confined_to_its_adapter():
    """The engine-agnosticism guarantee, mechanically.

    If this fails, some module outside the adapter has taken a dependency on
    one specific agent runtime, and swapping engines is no longer possible.
    """
    allowed = BACKEND / "engines" / "claude_agent"
    offenders = [
        str(path.relative_to(BACKEND))
        for package in ("app", "domain", "orchestration", "repositories", "storage",
                        "tools", "context", "services", "worker")
        for path in _python_files(package)
        if "claude_agent_sdk" in _module_names(path)
    ]
    assert not offenders, (
        "claude_agent_sdk may only be imported inside "
        f"{allowed.relative_to(BACKEND)}; found in: {offenders}"
    )


def test_engines_do_not_import_orchestration_or_persistence():
    offenders = [
        f"{path.relative_to(BACKEND)} imports {name}"
        for path in _python_files("engines")
        for name in _module_names(path)
        if name in ("orchestration", "repositories", "app", "services")
    ]
    assert not offenders, "engines must not depend on callers:\n" + "\n".join(offenders)
