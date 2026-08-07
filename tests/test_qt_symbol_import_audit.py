"""Static audit for Qt class names used without a binding.

This catches runtime-only NameError failures in GUI branches that cannot be
exercised on a headless build host without PySide6, such as a newly introduced
QGridLayout missing from the QtWidgets import list.
"""
from __future__ import annotations

import ast
import builtins
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIRS = (ROOT / "protocol_parser", ROOT / "build_tools")


def _module_bindings(tree: ast.Module) -> set[str]:
    """Collect names that can be bound at module scope, including try/if arms."""
    bindings = set(dir(builtins))

    def bind_target(target: ast.AST) -> None:
        if isinstance(target, ast.Name):
            bindings.add(target.id)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for item in target.elts:
                bind_target(item)

    def visit_module_statement(node: ast.stmt) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bindings.add(node.name)
            return
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if alias.name != "*":
                    bindings.add(alias.asname or alias.name.split(".", 1)[0])
            return
        if isinstance(node, ast.Assign):
            for target in node.targets:
                bind_target(target)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            bind_target(node.target)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            bind_target(node.target)
            for child in (*node.body, *node.orelse):
                visit_module_statement(child)
        elif isinstance(node, (ast.If, ast.While)):
            for child in (*node.body, *node.orelse):
                visit_module_statement(child)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if item.optional_vars is not None:
                    bind_target(item.optional_vars)
            for child in node.body:
                visit_module_statement(child)
        elif isinstance(node, ast.Try):
            for child in (*node.body, *node.orelse, *node.finalbody):
                visit_module_statement(child)
            for handler in node.handlers:
                if handler.name:
                    bindings.add(handler.name)
                for child in handler.body:
                    visit_module_statement(child)

    for statement in tree.body:
        visit_module_statement(statement)
    return bindings


def test_all_qt_class_names_have_a_module_binding() -> None:
    unresolved: list[str] = []
    for source_dir in SOURCE_DIRS:
        for path in sorted(source_dir.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
            bindings = _module_bindings(tree)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Name) or not isinstance(node.ctx, ast.Load):
                    continue
                name = node.id
                if name.startswith("Q") and len(name) > 1 and name[1].isupper() and name not in bindings:
                    unresolved.append(f"{path.relative_to(ROOT)}:{node.lineno}: {name}")

    assert not unresolved, "Qt names used without an import/definition:\n" + "\n".join(unresolved)
