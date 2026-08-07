#!/usr/bin/env python3
"""Apply BuildFix33 remaining source patches (parser + mcu_page).

Windows / macOS / Linux 通用，不依赖系统 patch 命令。

在仓库根目录执行：

    python scripts/apply_buildfix33_patches.py

然后：

    git add protocol_parser/parser.py protocol_parser/mcu_page.py
    git commit -m "fix(parser,mcu_page): apply BuildFix33 patches to source"
    git push origin fix/hardening-p0-p1
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _parse_hunk_header(line: str) -> tuple[int, int, int, int] | None:
    # @@ -old_start,old_count +new_start,new_count @@
    m = re.match(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
    if not m:
        return None
    return (
        int(m.group(1)),
        int(m.group(2) or "1"),
        int(m.group(3)),
        int(m.group(4) or "1"),
    )


def apply_unified_patch(target: Path, patch_text: str) -> None:
    """Apply a single-file unified diff to target. Raises on failure."""
    lines = patch_text.splitlines()
    i = 0
    while i < len(lines) and not lines[i].startswith("@@"):
        i += 1
    if i >= len(lines):
        raise RuntimeError(f"patch has no hunks: {target}")

    orig_lines = target.read_text(encoding="utf-8").splitlines()
    result: list[str] = []
    orig_idx = 0

    while i < len(lines):
        header = lines[i]
        parsed = _parse_hunk_header(header)
        if parsed is None:
            if header.startswith("---") or header.startswith("+++") or not header.strip():
                i += 1
                continue
            raise RuntimeError(f"unexpected patch line: {header!r}")
        old_start, old_count, new_start, new_count = parsed
        i += 1

        while orig_idx < old_start - 1:
            result.append(orig_lines[orig_idx])
            orig_idx += 1

        while i < len(lines) and not lines[i].startswith("@@"):
            row = lines[i]
            if row.startswith("\\"):
                i += 1
                continue
            if not row or row[0] not in " +-":
                if row.startswith("---") or row.startswith("+++"):
                    break
                raise RuntimeError(f"bad hunk line: {row!r}")
            tag, body = row[0], row[1:]
            if tag == " ":
                if orig_idx >= len(orig_lines) or orig_lines[orig_idx] != body:
                    got = orig_lines[orig_idx] if orig_idx < len(orig_lines) else "<EOF>"
                    raise RuntimeError(
                        f"context mismatch in {target.name} at line {orig_idx + 1}:\n"
                        f"  expected: {body!r}\n"
                        f"  got:      {got!r}"
                    )
                result.append(body)
                orig_idx += 1
            elif tag == "-":
                if orig_idx >= len(orig_lines) or orig_lines[orig_idx] != body:
                    got = orig_lines[orig_idx] if orig_idx < len(orig_lines) else "<EOF>"
                    raise RuntimeError(
                        f"delete mismatch in {target.name} at line {orig_idx + 1}:\n"
                        f"  expected: {body!r}\n"
                        f"  got:      {got!r}"
                    )
                orig_idx += 1
            elif tag == "+":
                result.append(body)
            i += 1

    while orig_idx < len(orig_lines):
        result.append(orig_lines[orig_idx])
        orig_idx += 1

    text = "\n".join(result)
    if target.read_text(encoding="utf-8").endswith("\n"):
        text += "\n"
    target.write_text(text, encoding="utf-8")


def main() -> int:
    jobs = [
        (
            ROOT / "patches" / "parser_strict_hex_and_cache.patch",
            ROOT / "protocol_parser" / "parser.py",
        ),
        (
            ROOT / "patches" / "mcu_page_qtimer_context.patch",
            ROOT / "protocol_parser" / "mcu_page.py",
        ),
    ]

    for patch_path, target in jobs:
        if not patch_path.exists():
            print(f"缺少 patch: {patch_path}", file=sys.stderr)
            return 1
        if not target.exists():
            print(f"缺少目标文件: {target}", file=sys.stderr)
            return 1

        content = target.read_text(encoding="utf-8")
        if target.name == "parser.py" and "_builtin_v3_lock" in content and "fullmatch" in content:
            print(f"跳过（已应用）: {target.relative_to(ROOT)}")
            continue
        if target.name == "mcu_page.py" and "def _run_pending_io_wake_send" in content:
            print(f"跳过（已应用）: {target.relative_to(ROOT)}")
            continue

        print(f"应用 {patch_path.relative_to(ROOT)} -> {target.relative_to(ROOT)}")
        try:
            apply_unified_patch(target, patch_path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"失败: {exc}", file=sys.stderr)
            return 1
        print("  OK")

    print("全部完成。请执行：")
    print("  git add protocol_parser/parser.py protocol_parser/mcu_page.py")
    print('  git commit -m "fix(parser,mcu_page): apply BuildFix33 patches to source"')
    print("  git push origin fix/hardening-p0-p1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
