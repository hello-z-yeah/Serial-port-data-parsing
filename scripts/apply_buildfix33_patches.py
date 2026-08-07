#!/usr/bin/env python3
"""Apply BuildFix33 remaining fixes (parser + mcu_page).

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


def _parse_hunk_header(line: str):
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
        old_start, _old_count, _new_start, _new_count = parsed
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


def fix_mcu_page(path: Path) -> None:
    """Surgical transforms that tolerate surrounding code drift."""
    text = path.read_text(encoding="utf-8")

    def _fix_method_timers(s: str) -> tuple[str, int]:
        pattern = re.compile(
            r"QTimer\.singleShot\((.*?),\s*self\.([A-Za-z_][A-Za-z0-9_]*)\s*\)",
            re.S,
        )
        count = 0
        pieces: list[str] = []
        pos = 0
        for m in pattern.finditer(s):
            if ", self, self." in m.group(0):
                continue
            pieces.append(s[pos:m.start()])
            pieces.append(f"QTimer.singleShot({m.group(1)}, self, self.{m.group(2)})")
            pos = m.end()
            count += 1
        pieces.append(s[pos:])
        return "".join(pieces), count

    text, n1 = _fix_method_timers(text)

    text, n2 = re.subn(
        r'QTimer\.singleShot\(\s*0\s*,\s*lambda:\s*setattr\(self,\s*["\']_rebalancing["\']\s*,\s*False\)\s*\)',
        "QTimer.singleShot(0, self, self._clear_rebalancing)",
        text,
    )
    text, n2b = re.subn(
        r'QTimer\.singleShot\(0, lambda: setattr\(self, "_rebalancing", False\)\)',
        "QTimer.singleShot(0, self, self._clear_rebalancing)",
        text,
    )
    n2 += n2b

    if "def _clear_rebalancing" not in text:
        anchor = "    def _rebalance_lower_panels(self) -> None:"
        helper = (
            "    def _clear_rebalancing(self) -> None:\n"
            "        self._rebalancing = False\n"
            "\n"
        )
        if anchor not in text:
            raise RuntimeError("cannot find _rebalance_lower_panels to insert helper")
        text = text.replace(anchor, helper + anchor, 1)

    if "def _run_pending_io_wake_send" not in text:
        anchor = "    def _on_io_wake(self) -> None:"
        helpers = '''    def _run_pending_io_wake_send(self) -> None:
        cb = getattr(self, "_pending_io_wake_send", None)
        self._pending_io_wake_send = None
        if callable(cb):
            try:
                cb()
            except RuntimeError:
                pass

    def _run_pending_io_wake_restore(self) -> None:
        cb = getattr(self, "_pending_io_wake_restore", None)
        self._pending_io_wake_restore = None
        if callable(cb):
            try:
                cb()
            except RuntimeError:
                pass

'''
        if anchor not in text:
            raise RuntimeError("cannot find _on_io_wake to insert helpers")
        text = text.replace(anchor, helpers + anchor, 1)

    text, n3 = re.subn(
        r"QTimer\.singleShot\(\s*50\s*,\s*_send_reset_heartbeat\s*\)\s*\n\s*"
        r"QTimer\.singleShot\(\s*100\s*,\s*_restore_button\s*\)",
        "self._pending_io_wake_send = _send_reset_heartbeat\n"
        "        self._pending_io_wake_restore = _restore_button\n"
        "        QTimer.singleShot(50, self, self._run_pending_io_wake_send)\n"
        "        QTimer.singleShot(100, self, self._run_pending_io_wake_restore)",
        text,
    )

    unsafe = [
        ln
        for ln in text.splitlines()
        if "QTimer.singleShot" in ln and ", self," not in ln and "lambda" not in ln
    ]
    if unsafe:
        raise RuntimeError("still have unsafe QTimer.singleShot lines:\n" + "\n".join(unsafe))

    path.write_text(text, encoding="utf-8")
    print(f"  mcu_page transforms: method-timers={n1}, rebalance-lambda={n2}, wake={n3}")


def main() -> int:
    parser = ROOT / "protocol_parser" / "parser.py"
    mcu = ROOT / "protocol_parser" / "mcu_page.py"
    parser_patch = ROOT / "patches" / "parser_strict_hex_and_cache.patch"

    if not parser.exists() or not mcu.exists():
        print("请在仓库根目录运行本脚本", file=sys.stderr)
        return 1

    ptxt = parser.read_text(encoding="utf-8")
    if "_builtin_v3_lock" in ptxt and "fullmatch" in ptxt:
        print(f"跳过（已应用）: {parser.relative_to(ROOT)}")
    else:
        if not parser_patch.exists():
            print(f"缺少 patch: {parser_patch}", file=sys.stderr)
            return 1
        print(f"应用 {parser_patch.relative_to(ROOT)} -> {parser.relative_to(ROOT)}")
        try:
            apply_unified_patch(parser, parser_patch.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"失败: {exc}", file=sys.stderr)
            return 1
        print("  OK")

    mtxt = mcu.read_text(encoding="utf-8")
    if "def _run_pending_io_wake_send" in mtxt and "QTimer.singleShot(0, self, self.apply_dpi_metrics)" in mtxt:
        print(f"跳过（已应用）: {mcu.relative_to(ROOT)}")
    else:
        print(f"应用 surgical fix -> {mcu.relative_to(ROOT)}")
        try:
            fix_mcu_page(mcu)
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
