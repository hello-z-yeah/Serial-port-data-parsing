"""主题管理（Light Fluent 工业风）。

使用 qfluentwidgets 的 Theme 系统，保持简洁高效的 Light 模式。
"""
from __future__ import annotations

from qfluentwidgets import Theme, setTheme, setThemeColor


# Fluent Light 工业风配色（与原 Tk light 调色板对齐）
PALETTE = {
    "app_bg":          "#F9FAFB",
    "card_bg":         "#FFFFFF",
    "card_border":     "#E0E0E0",
    "surface":         "#F3F4F6",
    "border":          "#E0E0E0",
    "primary":         "#0078D4",
    "primary_hover":   "#106EBE",
    "success":         "#0F7B0F",
    "error":           "#C42B1C",
    "warn":            "#BC6A00",
    "tx":              "#0A7A5A",
    "cmd":             "#1A56DB",
    "field":           "#374151",
    "raw":             "#6B7280",
    "ts":              "#8A9099",
    "pid":             "#8E24AA",
    "model":           "#0D7A73",
    "raw_data":        "#0E6E7A",
    "text":            "#111827",
    "text_secondary":  "#525C6B",
    "text_disabled":   "#9CA3AF",
}


class ThemeManager:
    """兼容原 ThemeManager 接口的轻量包装。

    注意：不再在 __init__ 里调用 setTheme，避免在 QApplication 创建前产生副作用。
    主题设置统一在 main() 里、创建任何窗口之前完成。
    """

    def __init__(self, mode: str = "light", style: str = "win11"):
        self.mode = "light"  # 强制 Light 工业风
        self.style = "win11"

    def get(self, name: str) -> str:
        return PALETTE.get(name, "#000000")

    def apply(self) -> None:
        """显式应用主题（需在 QApplication 已创建后调用）。"""
        setTheme(Theme.LIGHT)
        setThemeColor(PALETTE["primary"])
