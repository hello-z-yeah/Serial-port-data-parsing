"""主题管理（Light/Dark + Win11/Classic），纯 Tk，无第三方依赖。"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk


class ThemeManager:
    """纯 Tk 主题系统。

    主题 = 配色 + ttk 样式：
      - light / dark：两套配色
      - classic / win11：classic 用系统默认；win11 用 clam 自定义
    """

    PALETTES: dict[str, dict[str, str]] = {
        "light": {
            "app_bg": "#F9FAFB",
            "card_bg": "#F3F4F6",
            "card_border": "#E0E0E0",
            "surface": "#F3F4F6",
            "border": "#E0E0E0",
            "primary": "#0078D4",
            "primary_hover": "#106EBE",
            "success": "#0F7B0F",
            "error": "#C42B1C",
            "warn": "#BC6A00",
            "tx": "#0A7A5A",
            "cmd": "#1A56DB",
            "field": "#374151",
            "raw": "#6B7280",
            "ts": "#8A9099",
            "pid": "#8E24AA",
            "model": "#0D7A73",
            "raw_data": "#0E6E7A",
            "text": "#111827",
            "text_secondary": "#525C6B",
            "text_disabled": "#9CA3AF",
            "tooltip_bg": "#1F2937",
            "tooltip_fg": "#F9FAFB",
        },
        "dark": {
            "app_bg": "#141517",
            "card_bg": "#23252A",
            "card_border": "#383A41",
            "surface": "#2E3035",
            "border": "#3F4045",
            "primary": "#4CC2FF",
            "primary_hover": "#7FD2FF",
            "success": "#54C361",
            "error": "#F06E68",
            "warn": "#F6C177",
            "tx": "#39D5A4",
            "cmd": "#6CB5FF",
            "field": "#D1D5DB",
            "raw": "#9BA1A6",
            "ts": "#7A7F85",
            "pid": "#E0A4F7",
            "model": "#5AD6CF",
            "raw_data": "#76D0DB",
            "text": "#ECEDF0",
            "text_secondary": "#B9BCC2",
            "text_disabled": "#7A7F85",
            "tooltip_bg": "#E6E8EB",
            "tooltip_fg": "#111827",
        },
    }

    def __init__(self, mode: str = "light", style: str = "win11"):
        self.mode = mode if mode in self.PALETTES else "light"
        self.style = style if style in ("win11", "classic") else "win11"

    def get(self, name: str) -> str:
        return self.PALETTES[self.mode].get(name, "#000000")

    def apply_ttk_styles(self, ttk_style: ttk.Style) -> None:
        palette = self.PALETTES[self.mode]
        app_bg = palette["app_bg"]
        card_bg = palette["card_bg"]
        card_border = palette.get("card_border", palette["border"])
        surface = palette["surface"]
        border = palette["border"]
        primary = palette["primary"]
        primary_hover = palette["primary_hover"]
        text = palette["text"]
        text_2 = palette["text_secondary"]
        text_dis = palette["text_disabled"]

        if self.style == "win11":
            try:
                ttk_style.theme_use("clam")
            except tk.TclError:
                pass

        ttk_style.configure(
            ".",
            background=app_bg,
            foreground=text,
            fieldbackground=surface,
            bordercolor=border,
            lightcolor=border,
            darkcolor=border,
        )
        ttk_style.configure("TFrame", background=app_bg)
        ttk_style.configure("Card.TFrame", background=card_bg, relief="flat")
        ttk_style.configure(
            "TLabelframe",
            background=card_bg,
            bordercolor=card_border,
            relief="solid",
            borderwidth=1,
        )
        ttk_style.configure(
            "TLabelframe.Label",
            background=app_bg,
            foreground=text_2,
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        ttk_style.configure("TLabel", background=app_bg, foreground=text)
        ttk_style.configure("Card.TLabel", background=card_bg, foreground=text)
        ttk_style.configure("Hint.TLabel", background=card_bg, foreground=text_2)
        ttk_style.configure(
            "Title.TLabel",
            background=card_bg,
            foreground=text,
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        ttk_style.configure(
            "StatusBar.TFrame",
            background=card_bg,
            bordercolor=card_border,
            relief="solid",
            borderwidth=1,
        )
        ttk_style.configure(
            "StatusBar.TLabel",
            background=card_bg,
            foreground=text_2,
            font=("Microsoft YaHei UI", 10),
        )

        ttk_style.configure(
            "TButton",
            padding=(6, 2),
            relief="flat",
            background=surface,
            foreground=text,
            bordercolor=border,
            focusthickness=1,
            font=("Microsoft YaHei UI", 10),
        )
        ttk_style.map(
            "TButton",
            background=[
                ("active", palette["primary"] if self.style == "win11" else surface),
                ("pressed", primary_hover),
                ("disabled", app_bg),
            ],
            foreground=[
                ("active", "#FFFFFF" if self.style == "win11" else text),
                ("disabled", text_dis),
            ],
        )
        ttk_style.configure(
            "Primary.TButton",
            padding=(8, 3),
            relief="flat",
            background=primary,
            foreground="#FFFFFF",
            font=("Microsoft YaHei UI", 10, "bold"),
            borderwidth=0,
        )
        ttk_style.map(
            "Primary.TButton",
            background=[("active", primary_hover), ("pressed", primary_hover), ("disabled", border)],
            foreground=[("disabled", text_dis)],
        )
        ttk_style.configure(
            "Danger.TButton",
            padding=(8, 3),
            relief="flat",
            background=palette["error"],
            foreground="#FFFFFF",
            font=("Microsoft YaHei UI", 10, "bold"),
            borderwidth=0,
        )
        ttk_style.map(
            "Danger.TButton",
            background=[("active", "#A5211C"), ("pressed", "#A5211C"), ("disabled", border)],
        )
        ttk_style.configure(
            "CompactPrimary.TButton",
            padding=(6, 2),
            relief="flat",
            background=primary,
            foreground="#FFFFFF",
            font=("Microsoft YaHei UI", 10, "bold"),
            borderwidth=0,
        )
        ttk_style.map(
            "CompactPrimary.TButton",
            background=[("active", primary_hover), ("pressed", primary_hover), ("disabled", border)],
            foreground=[("disabled", text_dis)],
        )
        ttk_style.configure(
            "CompactDanger.TButton",
            padding=(6, 2),
            relief="flat",
            background=palette["error"],
            foreground="#FFFFFF",
            font=("Microsoft YaHei UI", 10, "bold"),
            borderwidth=0,
        )
        ttk_style.map(
            "CompactDanger.TButton",
            background=[("active", "#A5211C"), ("pressed", "#A5211C"), ("disabled", border)],
        )

        for s in ("TEntry", "TSpinbox", "TCombobox"):
            ttk_style.configure(
                s,
                fieldbackground=surface,
                foreground=text,
                bordercolor=border,
                lightcolor=border,
                darkcolor=border,
                arrowsize=14,
            )
            ttk_style.map(
                s,
                fieldbackground=[("readonly", card_bg), ("disabled", app_bg)],
                foreground=[("readonly", text), ("disabled", text_dis)],
                bordercolor=[("focus", primary), ("readonly", border)],
            )

        for _cb_style in ("TRadiobutton", "TCheckbutton"):
            ttk_style.configure(
                _cb_style,
                background=card_bg,
                foreground=text,
                focuscolor=primary,
                indicatordiameter=13,
                indicatorcolor=surface,
                indicatorforeground=text,
            )
            ttk_style.map(
                _cb_style,
                background=[("active", card_bg)],
                indicatorcolor=[("selected", primary), ("pressed", primary_hover), ("active", surface)],
                indicatorforeground=[("selected", "#000000"), ("pressed", "#000000")],
            )
        ttk_style.configure("Toolbar.TCheckbutton", background=app_bg, foreground=text)
        ttk_style.configure("Toolbar.TRadiobutton", background=app_bg, foreground=text)
        ttk_style.configure("Toolbar.TLabel", background=app_bg, foreground=text)
        ttk_style.configure("Toolbar.TButton", padding=(4, 2), font=("Microsoft YaHei UI", 10))

        try:
            ttk_style.configure("TPanedwindow", background=app_bg, sashwidth=8, sashrelief="flat")
        except tk.TclError:
            ttk_style.configure("TPanedwindow", background=app_bg)

        ttk_style.configure("TNotebook", background=app_bg, borderwidth=0)
        ttk_style.configure(
            "TNotebook.Tab",
            padding=(14, 6),
            background=app_bg,
            foreground=text_2,
            font=("Microsoft YaHei UI", 10),
        )
        ttk_style.map(
            "TNotebook.Tab",
            background=[("selected", card_bg), ("active", card_bg)],
            foreground=[("selected", primary if self.style == "win11" else text), ("active", text)],
        )

        ttk_style.configure(
            "Vertical.TScrollbar",
            background=border,
            troughcolor=app_bg,
            bordercolor=app_bg,
            arrowcolor=text_2,
            relief="flat",
            arrowsize=14,
        )
        ttk_style.map("Vertical.TScrollbar", background=[("active", text_2)])
        ttk_style.configure(
            "Horizontal.TScrollbar",
            background=border,
            troughcolor=app_bg,
            bordercolor=app_bg,
            arrowcolor=text_2,
            relief="flat",
            arrowsize=14,
        )
        ttk_style.map("Horizontal.TScrollbar", background=[("active", text_2)])

        ttk_style.configure(
            "Status.TFrame",
            background=palette["card_bg"] if self.style == "win11" else app_bg,
            relief="flat",
        )
        ttk_style.configure(
            "Status.TLabel",
            background=palette["card_bg"] if self.style == "win11" else app_bg,
            foreground=text_2,
        )