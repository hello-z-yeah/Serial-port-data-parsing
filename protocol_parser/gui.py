"""协议解析工具 GUI（Tkinter）。

特性：
- 双击打开即用，无需命令行
- 两种模式：粘贴解析 / 串口实时监控
- 支持多串口同时监控（独立窗口）
- 协议产品下拉选择
- 解析结果树形展示
- HEX/ASCII 数据格式切换
- 日志保存
"""
from __future__ import annotations

import os
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

# 让 exe 也能找到 protocol_parser 包
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from protocol_parser import (  # noqa: E402
    VERSION,
    UPDATER_GITHUB_REPO,
    ParseResult,
    ProtocolError,
    classify_protocol_error,
    _log_error_to_disk,
    load_protocol,
    parse_frame,
    parse_hex_input,
    to_hex,
)
from protocol_parser.serial_collector import FrameSynchronizer, SerialCollector  # noqa: E402
from protocol_parser.session_snapshot import (  # noqa: E402
    SessionSnapshot,
    clear_snapshot,
    default_session_path,
    load_snapshot,
    save_snapshot,
)
from protocol_parser.updater import (  # noqa: E402
    UpdateInfo,
    check_update as _updater_check,
    download_exe as _updater_download,
    verify_sha256 as _updater_verify,
    prepare_update_and_quit as _updater_apply,
    compute_sha256 as _updater_sha,
)


# ---------- 资源路径（兼容 PyInstaller 单文件模式） ----------

def resource_path(relative: str) -> Path:
    """获取资源路径，兼容开发模式和 PyInstaller 打包模式。"""
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / relative
    base = Path(__file__).resolve().parent
    # 开发模式下，product 在上一级目录
    candidate = base / relative
    if candidate.exists():
        return candidate
    return base.parent / relative


def get_protocol_dir() -> Path:
    """获取协议配置目录。

    始终返回用户可见的 product/ 目录，确保：
    1. 打包成 exe 后：使用 exe 同目录下的 product/（不存在则创建）
    2. 开发模式下：使用项目根目录的 product/

    这样用户可以在任意电脑上使用，导入的协议会保存在可见位置。
    """
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        proto_dir = exe_dir / "product"
        proto_dir.mkdir(parents=True, exist_ok=True)
        return proto_dir
    dev = Path(__file__).resolve().parent.parent / "product"
    dev.mkdir(parents=True, exist_ok=True)
    return dev


def load_builtin_protocol() -> dict:
    """加载内置的串口3.0基础协议。

    优先从用户可见的 product/ 目录读取（便于更新），
    如果不存在则从打包内的资源读取。
    """
    from protocol_parser.parser import load_protocol as _load

    # 优先从外部目录读取（用户可见，便于更新）
    external_dir = get_protocol_dir()
    external_file = external_dir / "v3_serial.json"
    if external_file.exists():
        try:
            return _load(external_file)
        except ProtocolError:
            pass

    # 从打包内资源读取
    bundled = resource_path("product") / "v3_serial.json"
    if bundled.exists():
        try:
            return _load(bundled)
        except ProtocolError:
            pass

    return {"product": "串口3.0协议", "description": "内置基础协议", "commands": [], "frame": {}, "enums": {}, "attributes": {}}


# 内置 V3.0 协议缓存
_builtin_v3: dict | None = None


def get_builtin_v3(refresh: bool = False) -> dict:
    """获取内置 V3.0 协议（支持刷新缓存）。"""
    global _builtin_v3
    if refresh or _builtin_v3 is None:
        _builtin_v3 = load_builtin_protocol()
    return _builtin_v3


# ---------- 通用：主题管理（Light/Dark + Win11/Classic 风格） ----------


class ThemeManager:
    """纯 Tk 主题系统（不引入第三方依赖）。

    主题 = 配色 + ttk 样式：
      - light / dark：两套配色（背景/卡片/主色/次级色/文本色/禁用色）
      - classic / win11：classic 使用系统默认 ttk 外观；
        win11 使用 ttk clam 自定义，卡片/控件有更柔和的圆角/间距/微阴影（纯 Frame 模拟）。
    """

    PALETTES: dict[str, dict[str, str]] = {
        "light": {
            "app_bg":          "#F3F4F6",  # 应用整体背景（Win11 Mica 近似）
            "card_bg":         "#FFFFFF",  # LabelFrame / 卡片背景
            "surface":         "#FFFFFF",  # Entry / Combobox / Text 背景
            "border":          "#D1D5DB",  # 边框色
            "primary":         "#0078D4",  # Win11 主色（蓝）
            "primary_hover":   "#106EBE",  # 主色 hover
            "success":         "#0F7B0F",  # 接收成功 / OK
            "error":           "#C42B1C",  # 错误 / 异常红
            "warn":            "#BC6A00",  # 告警 / 方向橙
            "tx":              "#0A7A5A",  # [TX] 发送绿（原用深红，改为更现代的青蓝绿）
            "cmd":             "#1A56DB",  # 命令字蓝
            "field":           "#374151",  # 字段灰
            "raw":             "#6B7280",  # Raw 报文灰
            "ts":              "#8A9099",  # 时间戳灰
            "pid":             "#8E24AA",  # PID 紫
            "model":           "#0D7A73",  # Model 青
            "raw_data":        "#0E6E7A",  # raw_data
            "text":            "#111827",  # 主文本色
            "text_secondary":  "#525C6B",  # 次要说明文本
            "text_disabled":   "#9CA3AF",  # 禁用文本
            "tooltip_bg":      "#1F2937",  # Tooltip 气泡背景（统一深色，对比强）
            "tooltip_fg":      "#F9FAFB",  # Tooltip 文字
        },
        "dark": {
            "app_bg":          "#1B1C1E",  # App 背景（Win11 深色近似）
            "card_bg":         "#2A2B2E",  # LabelFrame / 卡片
            "surface":         "#303136",  # Entry / Combobox / Text 背景
            "border":          "#3F4045",  # 边框
            "primary":         "#4CC2FF",  # Win11 深色主色（亮蓝）
            "primary_hover":   "#7FD2FF",  # 主色 hover
            "success":         "#54C361",  # 接收成功 / OK
            "error":           "#F06E68",  # 错误 / 异常红
            "warn":            "#F6C177",  # 告警 / 方向橙
            "tx":              "#39D5A4",  # 发送 [TX] 绿
            "cmd":             "#6CB5FF",  # 命令字蓝
            "field":           "#D1D5DB",  # 字段灰
            "raw":             "#9BA1A6",  # Raw 报文灰
            "ts":              "#7A7F85",  # 时间戳灰
            "pid":             "#E0A4F7",  # PID 紫
            "model":           "#5AD6CF",  # Model 青
            "raw_data":        "#76D0DB",  # raw_data
            "text":            "#ECEDF0",  # 主文本
            "text_secondary":  "#B9BCC2",  # 次要说明文本
            "text_disabled":   "#7A7F85",  # 禁用文本
            "tooltip_bg":      "#E6E8EB",  # Tooltip 气泡背景（统一浅色，对比强）
            "tooltip_fg":      "#111827",  # Tooltip 文字
        },
    }

    def __init__(self, mode: str = "light", style: str = "win11"):
        self.mode = mode if mode in self.PALETTES else "light"
        self.style = style if style in ("win11", "classic") else "win11"

    # ------------------------------------------------------------------
    # 取色
    # ------------------------------------------------------------------
    def get(self, name: str) -> str:
        return self.PALETTES[self.mode].get(name, "#000000")

    # ------------------------------------------------------------------
    # 应用到 ttk 全局样式
    # ------------------------------------------------------------------
    def apply_ttk_styles(self, ttk_style: ttk.Style) -> None:
        palette = self.PALETTES[self.mode]
        app_bg = palette["app_bg"]
        card_bg = palette["card_bg"]
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

        # ttk 根样式：Label/Button/Frame/LabelFrame/Entry/Combobox/Radiobutton/Checkbutton/Notebook
        ttk_style.configure(".", background=app_bg, foreground=text, fieldbackground=surface, bordercolor=border, lightcolor=border, darkcolor=border)

        # Frame / LabelFrame：卡片式
        ttk_style.configure("TFrame", background=app_bg)
        ttk_style.configure("Card.TFrame", background=card_bg, relief="flat")
        ttk_style.configure("TLabelframe", background=card_bg, bordercolor=border, relief="solid", borderwidth=1)
        ttk_style.configure("TLabelframe.Label", background=card_bg, foreground=text_2, font=("Microsoft YaHei UI", 9, "bold"))
        ttk_style.configure("TLabel", background=app_bg, foreground=text)
        ttk_style.configure("Card.TLabel", background=card_bg, foreground=text)
        ttk_style.configure("Hint.TLabel", background=card_bg, foreground=text_2)
        ttk_style.configure("Title.TLabel", background=card_bg, foreground=text, font=("Microsoft YaHei UI", 10, "bold"))

        # Button：主按钮 + 普通按钮
        ttk_style.configure("TButton", padding=(12, 6), relief="flat", background=surface, foreground=text, bordercolor=border, focusthickness=1)
        ttk_style.map("TButton",
                      background=[("active", palette["primary"] if self.style == "win11" else surface),
                                  ("pressed", primary_hover),
                                  ("disabled", app_bg)],
                      foreground=[("active", "#FFFFFF" if self.style == "win11" else text),
                                  ("disabled", text_dis)])
        # 大号「开始/停止监控」主按钮（粗体+圆角视觉感）
        ttk_style.configure("Primary.TButton", padding=(16, 8), relief="flat", background=primary, foreground="#FFFFFF", font=("Microsoft YaHei UI", 10, "bold"), borderwidth=0)
        ttk_style.map("Primary.TButton",
                      background=[("active", primary_hover), ("pressed", primary_hover), ("disabled", border)],
                      foreground=[("disabled", text_dis)])
        ttk_style.configure("Danger.TButton", padding=(16, 8), relief="flat", background=palette["error"], foreground="#FFFFFF", font=("Microsoft YaHei UI", 10, "bold"), borderwidth=0)
        ttk_style.map("Danger.TButton",
                      background=[("active", "#A5211C"), ("pressed", "#A5211C"), ("disabled", border)])

        # Entry / Combobox / Spinbox
        for s in ("TEntry", "TSpinbox", "TCombobox"):
            ttk_style.configure(s, fieldbackground=surface, foreground=text, bordercolor=border, lightcolor=border, darkcolor=border, arrowsize=14)
            ttk_style.map(s,
                          fieldbackground=[("readonly", card_bg), ("disabled", app_bg)],
                          foreground=[("readonly", text), ("disabled", text_dis)],
                          bordercolor=[("focus", primary), ("readonly", border)])

        # Radiobutton / Checkbutton
        ttk_style.configure("TRadiobutton", background=card_bg, foreground=text, focuscolor=primary)
        ttk_style.configure("TCheckbutton", background=card_bg, foreground=text, focuscolor=primary)
        ttk_style.map("TRadiobutton", background=[("active", card_bg)])
        ttk_style.map("TCheckbutton", background=[("active", card_bg)])
        # 顶栏专用（背景跟随 app_bg 而不是 card_bg）
        ttk_style.configure("Toolbar.TCheckbutton", background=app_bg, foreground=text)
        ttk_style.configure("Toolbar.TRadiobutton", background=app_bg, foreground=text)
        ttk_style.configure("Toolbar.TLabel", background=app_bg, foreground=text)
        ttk_style.configure("Toolbar.TButton", padding=(8, 4))

        # Notebook
        ttk_style.configure("TNotebook", background=app_bg, borderwidth=0)
        ttk_style.configure("TNotebook.Tab", padding=(14, 6), background=app_bg, foreground=text_2, font=("Microsoft YaHei UI", 9))
        ttk_style.map("TNotebook.Tab",
                      background=[("selected", card_bg), ("active", card_bg)],
                      foreground=[("selected", primary if self.style == "win11" else text), ("active", text)])

        # Scrollbar：Win11 细滚动条视觉
        ttk_style.configure("Vertical.TScrollbar", background=border, troughcolor=app_bg, bordercolor=app_bg, arrowcolor=text_2, relief="flat", arrowsize=14)
        ttk_style.map("Vertical.TScrollbar", background=[("active", text_2)])
        ttk_style.configure("Horizontal.TScrollbar", background=border, troughcolor=app_bg, bordercolor=app_bg, arrowcolor=text_2, relief="flat", arrowsize=14)
        ttk_style.map("Horizontal.TScrollbar", background=[("active", text_2)])

        # StatusBar
        ttk_style.configure("Status.TFrame", background=palette["card_bg"] if self.style == "win11" else app_bg, relief="flat")
        ttk_style.configure("Status.TLabel", background=palette["card_bg"] if self.style == "win11" else app_bg, foreground=text_2)


# ---------- 通用：半透明气泡 Tooltip（替代括号小字说明） ----------


class Tooltip:
    """给任意 widget 绑定悬停气泡说明。"""

    _DELAY_MS = 350  # 鼠标悬停多久后出现
    _TOPLEVEL: tk.Toplevel | None = None  # 全局共享一个气泡，避免多实例抖

    def __init__(self, widget: tk.Misc, text: str, theme: ThemeManager | None = None, width: int = 48):
        self.widget = widget
        self.text = text
        self.theme = theme
        self.width = width
        self._after_id: str | None = None
        widget.bind("<Enter>", self._on_enter, add="+")
        widget.bind("<Leave>", self._on_leave, add="+")
        widget.bind("<Motion>", self._on_motion, add="+")
        widget.bind("<ButtonPress>", self._on_leave, add="+")

    # ---------- helpers ----------
    def _wrap(self) -> str:
        lines = []
        for para in self.text.split("\n"):
            if len(para) <= self.width:
                lines.append(para)
                continue
            # 简单按宽度换行（中文按字符数，足够用）
            buf = ""
            for ch in para:
                if len(buf) >= self.width:
                    lines.append(buf)
                    buf = ""
                buf += ch
            if buf:
                lines.append(buf)
        return "\n".join(lines)

    # ---------- events ----------
    def _on_enter(self, _event=None):
        if self._after_id is not None:
            return
        self._after_id = self.widget.after(self._DELAY_MS, self._show)

    def _on_motion(self, _event=None):
        # 进入控件区域内的小移动不重新计时，离开的 Leave 会负责取消
        pass

    def _on_leave(self, _event=None):
        if self._after_id is not None:
            try:
                self.widget.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None
        self._hide()

    # ---------- show / hide ----------
    def _show(self):
        self._after_id = None
        try:
            if not self.widget.winfo_ismapped():
                return
        except Exception:
            return
        theme = self.theme or ThemeManager()
        bg = theme.get("tooltip_bg")
        fg = theme.get("tooltip_fg")
        x = self.widget.winfo_rootx() + 14
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 8
        tl = tk.Toplevel(self.widget)
        tl.wm_overrideredirect(True)
        try:
            tl.attributes("-alpha", 0.94)
        except Exception:
            pass
        tl.configure(bg=theme.get("border"))
        inner = tk.Frame(tl, bg=bg, padx=10, pady=6)
        inner.pack(padx=1, pady=1)
        tk.Label(inner, text=self._wrap(), bg=bg, fg=fg, justify="left",
                 font=("Microsoft YaHei UI", 9), wraplength=self.width * 10).pack()
        tl.update_idletasks()
        # 防超出屏幕右侧/底部
        sw = tl.winfo_screenwidth()
        sh = tl.winfo_screenheight()
        w = tl.winfo_width()
        h = tl.winfo_height()
        if x + w > sw - 8:
            x = sw - w - 8
        if y + h > sh - 8:
            y = max(8, self.widget.winfo_rooty() - h - 8)
        tl.wm_geometry(f"+{x}+{y}")
        Tooltip._TOPLEVEL = tl

    @staticmethod
    def _hide():
        tl = Tooltip._TOPLEVEL
        if tl is None:
            return
        try:
            tl.destroy()
        except Exception:
            pass
        Tooltip._TOPLEVEL = None


# ---------- 通用：可折叠抽屉 Drawer（右侧滑出） ----------


class Drawer:
    """右侧可折叠抽屉（用于收纳「高级设置」低频配置）。"""

    def __init__(self, parent: tk.Misc, theme: ThemeManager, width: int = 420):
        self.theme = theme
        self.width = width
        self.visible = False
        # 遮罩层 + 抽屉层，放在 parent 上用 place 定位（不破坏原有 grid/pack）
        self.overlay = tk.Frame(parent, bg="#000000")
        try:
            self.overlay.attributes("-alpha", 0.35)
        except Exception:
            self.overlay.configure(bg=self.theme.get("text_disabled"))
        self.frame = tk.Frame(parent, bg=self.theme.get("card_bg"), highlightthickness=1,
                              highlightbackground=self.theme.get("border"))
        # 顶栏：抽屉标题 + 关闭按钮
        self._header = tk.Frame(self.frame, bg=self.theme.get("card_bg"))
        self._header.pack(fill="x", padx=12, pady=(10, 8))
        tk.Label(self._header, text="高级设置 ⚙️", bg=self.theme.get("card_bg"),
                 fg=self.theme.get("text"), font=("Microsoft YaHei UI", 11, "bold")).pack(side="left")
        close_btn = tk.Button(self._header, text="×", command=self.hide, relief="flat",
                              bg=self.theme.get("card_bg"), fg=self.theme.get("text_secondary"),
                              activebackground=self.theme.get("border"),
                              activeforeground=self.theme.get("text"),
                              font=("Microsoft YaHei UI", 14), padx=8, pady=0, bd=0, cursor="hand2")
        close_btn.pack(side="right")
        # 内容区：用户通过 content_frame 自行 pack/grid
        self.content_wrap = tk.Frame(self.frame, bg=self.theme.get("card_bg"))
        self.content_wrap.pack(fill="both", expand=True, padx=4, pady=(0, 6))
        self._content_canvas = tk.Canvas(self.content_wrap, bg=self.theme.get("card_bg"),
                                         highlightthickness=0, bd=0)
        self._content_canvas.pack(side="left", fill="both", expand=True)
        self._scrollbar = ttk.Scrollbar(self.content_wrap, orient="vertical", command=self._content_canvas.yview)
        self._scrollbar.pack(side="right", fill="y")
        self._content_canvas.configure(yscrollcommand=self._scrollbar.set)
        self.content_frame = tk.Frame(self._content_canvas, bg=self.theme.get("card_bg"))
        self._content_window = self._content_canvas.create_window((0, 0), window=self.content_frame, anchor="nw")
        self.content_frame.bind("<Configure>", lambda _e: (
            self._content_canvas.configure(scrollregion=self._content_canvas.bbox("all")),
            self._content_canvas.itemconfigure(self._content_window, width=self._content_canvas.winfo_width()),
        ))
        # 鼠标滚轮支持
        def _on_wheel(e):
            try:
                self._content_canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
            except Exception:
                pass
        self._content_canvas.bind_all("<MouseWheel>", _on_wheel, add="+")
        # 点击遮罩关闭
        self.overlay.bind("<Button-1>", lambda _e: self.hide())

    def show(self):
        parent = self.frame.master
        self.visible = True
        try:
            pw, ph = parent.winfo_width(), parent.winfo_height()
            self.overlay.place(x=0, y=0, width=pw, height=ph)
            self.frame.place(x=pw - self.width, y=0, width=self.width, height=ph)
            self.frame.tkraise()
        except Exception:
            pass

    def hide(self):
        self.visible = False
        try:
            self.overlay.place_forget()
            self.frame.place_forget()
        except Exception:
            pass

    def toggle(self):
        if self.visible:
            self.hide()
        else:
            self.show()

    def refresh_colors(self):
        self.overlay.configure(bg=self.theme.get("text_disabled"))
        self.frame.configure(bg=self.theme.get("card_bg"), highlightbackground=self.theme.get("border"))
        self._header.configure(bg=self.theme.get("card_bg"))
        for w in self._header.winfo_children():
            if isinstance(w, tk.Label):
                w.configure(bg=self.theme.get("card_bg"), fg=self.theme.get("text"))
            elif isinstance(w, tk.Button):
                w.configure(bg=self.theme.get("card_bg"), fg=self.theme.get("text_secondary"),
                            activebackground=self.theme.get("border"))
        self.content_wrap.configure(bg=self.theme.get("card_bg"))
        self._content_canvas.configure(bg=self.theme.get("card_bg"))
        self.content_frame.configure(bg=self.theme.get("card_bg"))


# ---------- 通用：字体工具（仅保留等宽字体） ----------


def _filter_monospace_fonts(families: list[str]) -> list[str]:
    """从系统字体列表里挑出「适合 HEX/ASCII 报文」的等宽字体。

    Tk 无法直接查询某个字体是否 fixed-pitch，这里采用白名单关键字匹配（中英文 Windows/macOS
    常见等宽字体全覆盖，排序越靠前越推荐）。
    """
    priority_keywords = [
        "JetBrains Mono",
        "Fira Code",
        "Cascadia Code",
        "Cascadia Mono",
        "Source Code Pro",
        "Consolas",
        "Menlo",
        "Monaco",
        "Roboto Mono",
        "IBM Plex Mono",
        "DejaVu Sans Mono",
        "Liberation Mono",
        "Courier New",
        "宋体",  # 中文宋体也是等宽，中文对齐友好
        "等线",
    ]
    hit_ordered: list[str] = []
    seen: set[str] = set()
    # 第一阶段：按优先级关键字扫
    for kw in priority_keywords:
        for f in families:
            if f in seen:
                continue
            if kw.lower() in f.lower() or f == kw:
                hit_ordered.append(f)
                seen.add(f)
    # 第二阶段：兜底：所有名字含 Mono / Monospace / Code / 等宽 / mono 的字体
    fallback_keys = ("mono", "monospace", "code", "等宽")
    for f in families:
        if f in seen:
            continue
        low = f.lower()
        if any(k in low for k in fallback_keys[:3]) or any(k in f for k in fallback_keys[3:]):
            hit_ordered.append(f)
            seen.add(f)
    # 第三阶段：没有匹配的系统上，至少保留 Courier New（所有 Windows 默认都有）
    if "Courier New" in families and "Courier New" not in seen:
        hit_ordered.append("Courier New")
        seen.add("Courier New")
    return hit_ordered


# ---------- 通用：Text/Entry 右键菜单 + 快捷键 ----------

def _bind_text_widget_menu(widget, readonly: bool = False) -> None:
    """给 tk.Text / ttk.Entry 绑定：
    - 右键菜单（复制/剪切/粘贴/全选/清空）
    - 通用快捷键 Ctrl+C / Ctrl+V / Ctrl+X / Ctrl+A / Ctrl+BackSpace(清空)

    readonly=True：只允许 Copy/全选（用于显示用的 Text/Entry）
    """
    widget_class = widget.winfo_class()  # "Text" or "TEntry" / "Entry"
    is_text = (widget_class == "Text")

    def _sel_range():
        """返回选中的 (start, end)，如果没有选中返回 None。Entry/Text 兼容。"""
        try:
            if is_text:
                if widget.tag_ranges("sel"):
                    return widget.index("sel.first"), widget.index("sel.last")
                return None
            else:
                # Entry
                sel = widget.select_present()
                if sel:
                    return widget.index("sel.first"), widget.index("sel.last")
                return None
        except tk.TclError:
            return None

    def _has_selection() -> bool:
        return _sel_range() is not None

    def _copy():
        try:
            if _sel_range() is None:
                # 没选中就复制整行/整内容
                if is_text:
                    content = widget.get("1.0", "end-1c")
                else:
                    content = widget.get()
                widget.clipboard_clear()
                widget.clipboard_append(content)
            else:
                widget.event_generate("<<Copy>>")
        except Exception:
            try:
                widget.event_generate("<Control-c>")
            except Exception:
                pass

    def _cut():
        if readonly:
            return
        try:
            widget.event_generate("<<Cut>>")
        except Exception:
            try:
                widget.event_generate("<Control-x>")
            except Exception:
                pass

    def _paste():
        if readonly:
            return
        try:
            widget.event_generate("<<Paste>>")
        except Exception:
            try:
                widget.event_generate("<Control-v>")
            except Exception:
                pass

    def _select_all():
        try:
            if is_text:
                widget.tag_add("sel", "1.0", "end-1c")
                widget.mark_set("insert", "end-1c")
                widget.see("insert")
            else:
                widget.select_range(0, "end")
                widget.icursor("end")
        except Exception:
            try:
                widget.event_generate("<Control-a>")
            except Exception:
                pass

    def _clear():
        if readonly:
            # 只读控件（显示类Text）允许"清空"显示缓冲，防内存膨胀
            try:
                if is_text:
                    widget.configure(state="normal")
                    widget.delete("1.0", "end")
                    widget.configure(state="disabled")
                else:
                    widget.configure(state="normal")
                    widget.delete(0, "end")
                    widget.configure(state="readonly")
            except Exception:
                pass
        else:
            try:
                if is_text:
                    widget.delete("1.0", "end")
                else:
                    widget.delete(0, "end")
            except Exception:
                pass

    # —— 右键菜单 ——
    menu = tk.Menu(widget, tearoff=0)
    menu.add_command(label="复制 (Ctrl+C)", command=_copy, accelerator="Ctrl+C")
    if not readonly:
        menu.add_command(label="剪切 (Ctrl+X)", command=_cut, accelerator="Ctrl+X")
        menu.add_command(label="粘贴 (Ctrl+V)", command=_paste, accelerator="Ctrl+V")
    menu.add_separator()
    menu.add_command(label="全选 (Ctrl+A)", command=_select_all, accelerator="Ctrl+A")
    menu.add_command(label="清空", command=_clear)

    def _popup(event):
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    try:
        widget.bind("<Button-3>", _popup)  # Windows 右键
        widget.bind("<Button-2>", _popup)  # Mac/Linux 中键
    except Exception:
        pass

    # —— 快捷键绑定（Tk 的 Text 自带部分快捷键，但 Entry 需要自己绑 Ctrl+A） ——
    try:
        widget.bind("<Control-c>", lambda e: (None, _copy(), "break")[2] if False else None)
        widget.bind("<Control-C>", lambda e: _copy())
    except Exception:
        pass
    try:
        widget.bind("<Control-a>", lambda e: (_select_all(), "break")[1])
        widget.bind("<Control-A>", lambda e: (_select_all(), "break")[1])
    except Exception:
        pass
    if not readonly:
        try:
            widget.bind("<Control-x>", lambda e: (_cut(), "break")[1])
            widget.bind("<Control-X>", lambda e: (_cut(), "break")[1])
        except Exception:
            pass
        try:
            widget.bind("<Control-v>", lambda e: (_paste(), "break")[1])
            widget.bind("<Control-V>", lambda e: (_paste(), "break")[1])
        except Exception:
            pass


class ProtocolParserApp:
    def __init__(self, root: tk.Tk, monitor_port: str | None = None, monitor_baud: int = 115200):
        self.root = root
        self.root.title(f"串口协议解析工具 v{VERSION}")
        self.root.geometry("1100x720")
        self.root.minsize(900, 600)

        # ============================================================
        #  主题/视觉：Light/Dark + Win11 风格（纯 Tk，不引入第三方依赖）
        # ============================================================
        # 先尝试从旧快照 extras 里恢复偏好（即便没有 was_collecting 也能恢复字体/主题）
        _pref_snap = load_snapshot()
        _extras: dict = dict(getattr(_pref_snap, "extras", None) or {})
        self.theme = ThemeManager(
            mode=str(_extras.get("theme_mode", "light")),
            style=str(_extras.get("theme_style", "win11")),
        )
        self.theme_mode_var = tk.StringVar(value=self.theme.mode)
        self.theme_style_var = tk.StringVar(value=self.theme.style)
        # 配置全局 ttk 样式
        self.ttk_style = ttk.Style()
        try:
            self.theme.apply_ttk_styles(self.ttk_style)
        except Exception:
            # 样式应用失败不影响主流程
            pass
        # 根窗口背景色
        self.root.configure(bg=self.theme.get("app_bg"))

        # ============================================================
        #  字体偏好（仅等宽字体）+ 行间距
        # ============================================================
        _all_mono = _filter_monospace_fonts(tkfont.families())
        self.available_monospace_fonts = _all_mono
        _default_family = "Consolas" if "Consolas" in _all_mono else (_all_mono[0] if _all_mono else "Courier New")
        self.font_family_var = tk.StringVar(value=str(_extras.get("font_family", _default_family)))
        self.font_size_var = tk.IntVar(value=int(_extras.get("font_size", 10)))
        self.line_spacing_px_var = tk.IntVar(value=int(_extras.get("line_spacing_px", 2)))
        # 构建可变等宽字体（Ctrl+滚轮 / A+ / A- 都会直接改这个 Font 对象）
        self.serial_font = tkfont.Font(family=self.font_family_var.get(), size=self.font_size_var.get())
        self.cmd_font = tkfont.Font(family=self.font_family_var.get(), size=self.font_size_var.get(), weight="bold")
        # 日志框 Tag 定义颜色（在创建 serial_text 之后会按 theme.get() 刷新）

        # ---- 菜单栏：帮助 → 检查更新 / 关于 ----
        self._build_menu_bar()

        # 置顶状态
        self.topmost_var = tk.BooleanVar(value=bool(_extras.get("topmost", False)))
        if self.topmost_var.get():
            try:
                self.root.attributes("-topmost", True)
            except Exception:
                pass

        self.cfg: dict | None = None
        self.product_var = tk.StringVar()

        # 启动参数：--monitor port baud 时自动填好选中串口/波特率
        self._monitor_port = monitor_port
        self._monitor_baud = monitor_baud

        # 串口相关
        self.port_var = tk.StringVar()
        self.baudrate_var = tk.StringVar(value="115200")  # 改成StringVar支持手动输入自定义波特率
        self.bytesize_var = tk.IntVar(value=8)
        self.stopbits_var = tk.IntVar(value=1)
        self.collector: SerialCollector | None = None
        self.is_collecting = False
        self.serial_sender_var = tk.StringVar(value="模组发送")

        # 数据格式：HEX格式单选（勾选=HEX，不勾选=ASCII）
        self.hex_format_var = tk.BooleanVar(value=True)

        self.log_path: Path | None = None
        self.log_file = None
        self.log_count = 0
        self.rx_frame_count = 0
        self.tx_frame_count = 0

        # 原始数据保存
        self.save_raw_enabled_var = tk.BooleanVar(value=bool(_extras.get("save_raw_enabled_default", True)))
        import os
        default_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
        self.save_raw_path_var = tk.StringVar(value=str(_extras.get("save_raw_path_default", default_path)))
        from datetime import datetime
        default_name = datetime.now().strftime("serial_data_%Y%m%d_%H%M%S")
        self.save_raw_filename_var = tk.StringVar(value=default_name)
        self.save_raw_file = None
        self.save_raw_current_size = 0
        self.raw_auto_split_mb_var = tk.IntVar(value=int(_extras.get("raw_auto_split_mb", 50)))
        self.save_raw_max_size = max(1, self.raw_auto_split_mb_var.get()) * 1024 * 1024
        self.save_raw_count = 0
        self._save_raw_active = False
        # 每当 raw 自动分割 MB 数调整，同步更新 save_raw_max_size
        self.raw_auto_split_mb_var.trace_add("write", lambda *_a: self._refresh_max_raw_size())

        # 发送：协议模式 / Raw 模式 + 循环周期
        self.send_mode_var = tk.StringVar(value="protocol")  # protocol / raw_hex / raw_ascii
        self.tx_cmd_code_var = tk.StringVar(value="0x20")
        self.tx_direction_var = tk.StringVar(value="模组发送")
        self.tx_fields_var = tk.StringVar(value='{"value": 1}')
        self.tx_raw_var = tk.StringVar(value="")
        self.tx_cycle_var = tk.BooleanVar(value=False)
        self.tx_interval_ms_var = tk.IntVar(value=1000)
        self._tx_cycle_job: str | None = None

        # 显示缓冲区限制（防止内存溢出）
        self.max_display_lines = 50000

        # Drawer（右侧高级设置抽屉）
        self.drawer: Drawer | None = None

        # 主布局
        self._build_ui()
        self._load_protocols()

        # 恢复 Drawer 折叠偏好（默认关闭）
        if _extras.get("drawer_open", False) and self.drawer is not None:
            try:
                self.drawer.show()
            except Exception:
                pass

        # 定时刷新 UI 队列
        self._ui_queue: list[tuple[str, tuple]] = []
        self.root.after(100, self._process_ui_queue)

        # 关闭窗口时：保存偏好 + 安全停止串口（不要等更新才保存）
        self.root.protocol("WM_DELETE_WINDOW", self._safe(self._on_app_close))

        # 若是 monitor 启动方式：自动跳转到「串口实时」tab + 选中指定串口/波特率
        if self._monitor_port:
            self._apply_monitor_args()

        # 最后：如果存在更新会话快照 → 恢复（恢复失败友好提示，不崩）
        try:
            self._maybe_restore_session_after_update()
        except Exception as e:  # noqa: BLE001
            self._report_error("恢复更新会话失败", e)

    # ---------- 错误上报：friendly 弹窗 + error.log，绝不裸抛堆栈 ----------

    def _report_error(
        self,
        title: str,
        exc: Exception,
        *,
        parent: tk.Misc | None = None,
    ) -> None:
        """统一的异常上报入口。

        - ProtocolError：只弹 friendly_msg，不展示底层堆栈；
        - 其他异常：弹"未知错误"友好提示 + 日志路径；
        - 所有异常都会写入 error.log，方便开发者定位；
        - 绝对不向用户抛 traceback。
        """
        friendly, debug = classify_protocol_error(exc)
        try:
            log_path = _log_error_to_disk(exc)
        except Exception:
            log_path = None

        body = friendly
        if debug and isinstance(exc, ProtocolError):
            body += f"\n\n原因: {debug}"
        if log_path is not None:
            body += f"\n\n详细日志: {log_path}"

        try:
            if threading.current_thread() is not threading.main_thread():
                self.root.after(
                    0,
                    lambda: messagebox.showerror(title, body, parent=parent or self.root),
                )
            else:
                messagebox.showerror(title, body, parent=parent or self.root)
        except Exception:
            # 极端情况：弹窗本身失败（例如 GUI 已销毁）——至少把日志写到 stderr
            try:
                print(f"[{title}] {friendly}", file=sys.stderr)
                if debug:
                    print(f"  详细: {debug}", file=sys.stderr)
            except Exception:
                pass

    def _safe(self, fn):
        """GUI 回调安全包装器：最外层捕获异常并弹友好提示。

        用法：
            1. 在绑定 command/callback 时直接传 `self._safe(self._xxx)`；
            2. 或在 `def _xxx` 里用 `@_safe` 风格手动包裹。
        """
        def _wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception as e:  # noqa: BLE001  顶层兜底
                self._report_error("操作失败", e)
                return None
        return _wrapper

    # ---------- UI 构建 ----------

    def _build_menu_bar(self) -> None:
        """构建顶部菜单栏：当前只有「帮助」菜单，放检查更新 / 关于。"""
        menubar = tk.Menu(self.root)
        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="检查更新…", command=self._safe(self._menu_check_update))
        help_menu.add_separator()
        help_menu.add_command(label=f"关于（当前 v{VERSION}）", command=self._safe(self._menu_about))
        menubar.add_cascade(label="帮助", menu=help_menu)
        self.root.config(menu=menubar)

    def _menu_about(self) -> None:
        body = (
            f"串口协议解析工具\n"
            f"当前版本：v{VERSION}\n"
            f"发布仓库：github.com/{UPDATER_GITHUB_REPO}\n\n"
            f"—— 功能特性 ——\n"
            "· 串口实时监控（HEX / ASCII）\n"
            "· 导入 Word 协议文档，中文属性名和枚举解析\n"
            "· 每个串口独立窗口进程，支持 6M / 自定义波特率\n"
            "· 在线更新（帮助 → 检查更新）"
        )
        messagebox.showinfo(f"关于（v{VERSION}）", body, parent=self.root)

    def _menu_check_update(self) -> None:
        """手动菜单触发：检查更新 Toplevel（可后台线程下载，带进度条，可取消）。"""
        win = tk.Toplevel(self.root)
        win.title("检查更新")
        win.geometry("520x300")
        win.transient(self.root)
        win.grab_set()
        win.resizable(False, False)

        ttk.Label(win, text=f"当前版本：v{VERSION}", font=("Microsoft YaHei", 10, "bold")).pack(
            anchor="w", padx=14, pady=(14, 2)
        )
        latest_var = tk.StringVar(value="最新版本：检查中…")
        ttk.Label(win, textvariable=latest_var).pack(anchor="w", padx=14, pady=(0, 6))

        notes_txt = tk.Text(win, height=8, wrap="word", state="disabled")
        notes_txt.pack(fill="both", expand=True, padx=14, pady=2)
        _bind_text_widget_menu(notes_txt, readonly=True)

        prog = ttk.Progressbar(win, orient="horizontal", mode="determinate")
        prog.pack(fill="x", padx=14, pady=(6, 4))
        prog_var = tk.StringVar(value="")
        ttk.Label(win, textvariable=prog_var, anchor="w").pack(fill="x", padx=14)

        btns = ttk.Frame(win)
        btns.pack(fill="x", padx=14, pady=10)
        check_btn = ttk.Button(btns, text="立即检查", command=lambda: self._dlg_check_now())
        check_btn.pack(side="right")
        update_btn = ttk.Button(btns, text="下载并更新", state="disabled")
        update_btn.pack(side="right", padx=8)
        close_btn = ttk.Button(btns, text="关闭", command=win.destroy)
        close_btn.pack(side="right")

        state: dict = {
            "info": None,
            "cancel": False,
            "worker_thread": None,
        }

        def _set_status(s: str) -> None:
            def _inner():
                prog_var.set(s)

            self.root.after(0, _inner)

        def _set_notes(html: str) -> None:
            def _inner():
                notes_txt.config(state="normal")
                notes_txt.delete("1.0", "end")
                notes_txt.insert("1.0", html)
                notes_txt.config(state="disabled")

            self.root.after(0, _inner)

        def _progress(dl: int, total: int) -> None:
            if state["cancel"]:
                # 无法中断 urllib 但至少不更新 UI
                return
            pct = int((dl / total) * 100) if total else 0
            dl_mb = dl / (1024 * 1024)
            total_mb = total / (1024 * 1024) if total else 0
            def _inner():
                prog.config(maximum=total if total else 100, value=dl if total else pct)
                if total:
                    prog_var.set(f"下载中：{pct:>3}%  ({dl_mb:5.1f} / {total_mb:5.1f} MB)")
                else:
                    prog_var.set(f"下载中…({dl_mb:5.1f} MB)")

            self.root.after(0, _inner)

        def _apply_update(path: str, sha_expected: str) -> None:
            _set_status("正在校验文件…")
            try:
                sha_actual = _updater_sha(path)
            except Exception as e:
                self._report_error("更新失败", e, parent=win)
                return
            if sha_expected and sha_actual != sha_expected.lower():
                msg = (
                    "SHA256 校验不通过，已中止更新。\n"
                    f"期望: {sha_expected}\n实际: {sha_actual}"
                )
                try:
                    os.remove(path)
                except Exception:
                    pass
                try:
                    messagebox.showerror("更新失败", msg, parent=win)
                except Exception:
                    print(f"[更新失败] {msg}", file=sys.stderr)
                _set_status("已取消（校验失败）")
                update_btn.config(state="disabled")
                return
            ok = messagebox.askyesno(
                "即将更新",
                "下载完成，即将关闭当前程序并覆盖更新。\n确认立即更新吗？（更新后会自动重新启动，并尽量恢复当前的串口/协议配置）",
                parent=win,
            )
            if not ok:
                return

            # 更新前：安全停止串口、flush 日志/原始数据、写会话快照
            snapshot_path: str | None = None
            try:
                snap = self._prepare_session_snapshot_for_update()
                snapshot_path = str(default_session_path())
                _set_status(
                    "已准备更新"
                    + (f"（重启后会恢复 {snap.port or '未使用串口'}）" if snap.was_collecting else "（未恢复串口）")
                )
            except Exception as e:
                self._report_error("更新失败（保存会话）", e, parent=win)
                return

            try:
                _updater_apply(path, snapshot_path=snapshot_path)  # 内部会 os._exit(0)
            except Exception as e:
                friendly, _ = classify_protocol_error(e)
                log_path = _log_error_to_disk(e)
                body = f"无法应用更新：{friendly}\n请手动关闭程序后，将新 EXE 覆盖到原位置。\n临时文件: {path}"
                if log_path is not None:
                    body += f"\n日志: {log_path}"
                try:
                    messagebox.showerror("更新失败", body, parent=win)
                except Exception:
                    print(f"[更新失败] {body}", file=sys.stderr)

        def _download_worker(info: UpdateInfo) -> None:
            try:
                _set_status("开始下载新版本…")
                # 临时下载目录：和旧 EXE 同目录，保证 update bat 同分区原子 move
                if getattr(sys, "frozen", False):
                    base_dir = Path(sys.executable).resolve().parent
                else:
                    base_dir = Path(__file__).resolve().parent.parent
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                dst = base_dir / f"Serial-port-data-parsing_update_{ts}.exe.tmp"
                try:
                    _updater_download(info.download_url, str(dst), progress_cb=_progress, timeout=3600.0)
                except Exception as e:
                    if state["cancel"]:
                        _set_status("已取消下载")
                    else:
                        _log_error_to_disk(e)
                        friendly, _ = classify_protocol_error(e)
                        _set_status(f"下载失败：{friendly}")
                        self.root.after(
                            0,
                            lambda: self._report_error("下载失败", e, parent=win),
                        )
                    return
                _apply_update(str(dst), info.sha256_expected)
            except Exception as e:  # noqa: BLE001  后台线程绝对不能裸抛
                _log_error_to_disk(e)
                friendly, _ = classify_protocol_error(e)
                _set_status(f"更新失败：{friendly}")
                self.root.after(
                    0,
                    lambda: self._report_error("更新失败", e, parent=win),
                )

        def _start_download() -> None:
            info: UpdateInfo | None = state["info"]
            if not info or not info.has_new:
                return
            if state["worker_thread"] and state["worker_thread"].is_alive():
                return
            state["cancel"] = False
            update_btn.config(state="disabled", text="下载中…")
            t = threading.Thread(target=_download_worker, args=(info,), daemon=True)
            state["worker_thread"] = t
            t.start()

        def _check_worker() -> None:
            try:
                _set_status("正在访问 GitHub Releases…")
                info = _updater_check(VERSION, UPDATER_GITHUB_REPO, timeout=15.0)
            except Exception as e:
                info = UpdateInfo(has_new=False, current_version=VERSION)
                _log_error_to_disk(e)
                _set_status("检查失败")
                self.root.after(
                    0,
                    lambda: self._report_error("检查更新失败", e, parent=win),
                )
                return
            state["info"] = info

            def _apply():
                if info.has_new:
                    latest_var.set(f"最新版本：{info.latest_version}  ✨ 发现新版本")
                    notes = f"更新说明：\n{info.release_notes or '（暂无更新说明）'}\n\n"
                    notes += f"下载地址：\n{info.download_url}\n"
                    if info.sha256_expected:
                        notes += f"\nSHA256: {info.sha256_expected}\n"
                    _set_notes(notes)
                    update_btn.config(state="normal", text="下载并更新")
                    _set_status("点击「下载并更新」开始更新")
                else:
                    latest_var.set(f"最新版本：{info.latest_version or '（检查不到）'}")
                    _set_notes("当前已是最新版本，无需更新。")
                    update_btn.config(state="disabled")
                    _set_status("当前已是最新版本")

            self.root.after(0, _apply)

        def _check_now() -> None:
            if state["worker_thread"] and state["worker_thread"].is_alive():
                return
            latest_var.set("最新版本：检查中…")
            _set_notes("")
            t = threading.Thread(target=_check_worker, daemon=True)
            state["worker_thread"] = t
            t.start()

        self._dlg_check_now = _check_now  # type: ignore[attr-defined]
        update_btn.config(command=_start_download)

        # 打开窗口立即触发一次「检查中…」（由用户手动触发，符合模式 A：不启动就不检查）
        # 这里不自动检查，用户点「立即检查」才开始
        latest_var.set("最新版本：请点击「立即检查」")
        _set_status("尚未开始检查")
        _set_notes("")

    def _build_ui(self) -> None:
        # ============================================================
        #  顶栏（核心控制区）：只保留 协议 + 串口 + 波特率 + 大号【开始/停止监控】+ 高级设置 ⚙️
        # ============================================================
        top = tk.Frame(self.root, bg=self.theme.get("app_bg"), padx=14, pady=10)
        top.pack(fill="x")

        def _toolbar_label(text: str) -> tk.Label:
            return tk.Label(top, text=text, bg=self.theme.get("app_bg"), fg=self.theme.get("text_secondary"),
                            font=("Microsoft YaHei UI", 9))

        # 1) 产品协议
        _toolbar_label("产品协议").pack(side="left")
        self.product_combo = ttk.Combobox(top, textvariable=self.product_var, width=26, state="readonly")
        self.product_combo.pack(side="left", padx=(4, 12))
        self.product_combo.bind("<<ComboboxSelected>>", self._on_product_change)
        Tooltip(self.product_combo,
                "切换当前要解析/发送的产品协议 JSON。\n放 JSON 到 product/ 目录后，点旁边刷新按钮即可出现。",
                self.theme)

        # 2) 串口
        _toolbar_label("串口").pack(side="left")
        self.port_combo = ttk.Combobox(top, textvariable=self.port_var, width=16, state="readonly")
        self.port_combo.pack(side="left", padx=(4, 12))
        Tooltip(self.port_combo, "选择要监控的串口号（如 COM3 / COM5）。\n点右边的小按钮刷新设备列表。", self.theme)
        refresh_ports_btn = ttk.Button(top, text="↻", width=3, command=self._safe(self._refresh_ports), style="Toolbar.TButton")
        refresh_ports_btn.pack(side="left", padx=(0, 12))
        Tooltip(refresh_ports_btn, "重新扫描本机可用串口。", self.theme)

        # 3) 波特率
        _toolbar_label("波特率").pack(side="left")
        self.baudrate_combo = ttk.Combobox(
            top, textvariable=self.baudrate_var, width=10, state="normal",
            values=[9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600, 1000000, 1500000, 2000000, 3000000, 4000000, 5000000, 6000000],
        )
        self.baudrate_combo.pack(side="left", padx=(4, 16))
        Tooltip(self.baudrate_combo,
                "选择或输入任意波特率。\n常用：115200\n支持高达 6,000,000（6M）。",
                self.theme)

        # 弹性空白
        tk.Frame(top, bg=self.theme.get("app_bg")).pack(side="left", fill="x", expand=True)

        # 4) 大号 开始/停止监控按钮
        self.start_btn = ttk.Button(top, text="● 开始监控", style="Primary.TButton",
                                    command=self._safe(self._toggle_serial))
        self.start_btn.pack(side="left", padx=8)
        Tooltip(self.start_btn,
                "开始监控（绿灯）/ 停止监控（灰）。\n快捷键：F5 开始 / Shift+F5 停止。",
                self.theme)
        # 绑定快捷键
        try:
            self.root.bind("<F5>", lambda _e: (self._safe(self._start_serial)(), None)[1] if not self.is_collecting else None)
            self.root.bind("<Shift-F5>", lambda _e: (self._safe(self._stop_serial)(), None)[1] if self.is_collecting else None)
        except Exception:
            pass

        # 5) 高级设置 ⚙️ 按钮（打开 Drawer）
        self.drawer_btn = tk.Button(
            top, text="⚙ 高级", relief="flat", cursor="hand2", bd=0,
            bg=self.theme.get("app_bg"), fg=self.theme.get("text"),
            activebackground=self.theme.get("border"), activeforeground=self.theme.get("text"),
            font=("Microsoft YaHei UI", 10), padx=10, pady=4,
            command=self._safe(self._toggle_drawer),
        )
        self.drawer_btn.pack(side="left", padx=4)
        Tooltip(self.drawer_btn,
                "打开/关闭「高级设置」抽屉。\n低频配置：数据位/停止位/HEX 模式/发送方/自动滚动/保存日志/保存原始数据/主题/字体…",
                self.theme)

        # ============================================================
        #  中间内容区：Notebook（串口实时 | 指令发送）
        # ============================================================
        body = tk.Frame(self.root, bg=self.theme.get("app_bg"))
        body.pack(fill="both", expand=True, padx=10, pady=(0, 6))

        self.notebook = ttk.Notebook(body)
        self.notebook.pack(fill="both", expand=True)

        # Tab 1：串口实时
        self.serial_tab = ttk.Frame(self.notebook, padding=4, style="Card.TFrame")
        self.serial_tab.columnconfigure(0, weight=1)
        self.serial_tab.rowconfigure(0, weight=1)
        self._build_serial_panel(self.serial_tab)
        self.notebook.add(self.serial_tab, text="  串口实时  ")

        # Tab 2：指令发送
        self.send_tab = ttk.Frame(self.notebook, padding=8, style="Card.TFrame")
        self.send_tab.columnconfigure(0, weight=1)
        self.send_tab.rowconfigure(2, weight=1)
        self._build_send_panel(self.send_tab)
        self.notebook.add(self.send_tab, text="  指令发送  ")

        # ============================================================
        #  Drawer（右侧高级设置）：在 body 上 place
        # ============================================================
        self.drawer = Drawer(body, self.theme, width=440)
        self._build_drawer_content(self.drawer.content_frame)

        # ============================================================
        #  底部状态栏
        # ============================================================
        self.status_var = tk.StringVar(value="就绪")
        self.stats_var = tk.StringVar(value="RX 0  TX 0  错误 0  缓冲 0B")
        status = tk.Frame(self.root, bg=self.theme.get("card_bg"), padx=10, pady=4,
                          highlightthickness=1,
                          highlightbackground=self.theme.get("border"))
        status.pack(fill="x", side="bottom")
        tk.Label(status, textvariable=self.status_var, anchor="w",
                 bg=self.theme.get("card_bg"), fg=self.theme.get("text_secondary"),
                 font=("Microsoft YaHei UI", 9)).pack(side="left", fill="x", expand=True)
        tk.Label(status, textvariable=self.stats_var, anchor="e",
                 bg=self.theme.get("card_bg"), fg=self.theme.get("text_secondary"),
                 font=("Microsoft YaHei UI", 9)).pack(side="right")

    # ------------------------------------------------------------
    # Drawer（右侧高级设置）的内容区构造
    # ------------------------------------------------------------
    def _build_drawer_content(self, cf: tk.Misc) -> None:
        theme = self.theme
        card_bg = theme.get("card_bg")
        text = theme.get("text")
        text_2 = theme.get("text_secondary")

        def _section(title: str, parent: tk.Misc = cf) -> tk.LabelFrame:
            lf = ttk.LabelFrame(parent, text=f"  {title}  ", padding=10)
            lf.pack(fill="x", padx=10, pady=(8, 4))
            # ttk LabelFrame 内部子控件背景跟随 card_bg
            try:
                for _ in range(1):  # 作用域占位
                    pass
            except Exception:
                pass
            return lf

        def _label(parent: tk.Misc, text_: str, secondary: bool = False) -> tk.Label:
            fg = text_2 if secondary else text
            return tk.Label(parent, text=text_, bg=card_bg, fg=fg, font=("Microsoft YaHei UI", 9))

        # --------------------------------------------------------
        # 1) 串口参数（高级）
        # --------------------------------------------------------
        s1 = _section("串口高级参数")
        r = tk.Frame(s1, bg=card_bg)
        r.pack(fill="x")
        _label(r, "数据位").grid(row=0, column=0, sticky="w")
        bs_cmb = ttk.Combobox(r, textvariable=self.bytesize_var, values=[5, 6, 7, 8], width=6, state="readonly")
        bs_cmb.grid(row=0, column=1, padx=(6, 16), pady=4, sticky="w")
        Tooltip(bs_cmb, "串口数据位（通常 8）。", theme)
        _label(r, "停止位").grid(row=0, column=2, sticky="w")
        sp_cmb = ttk.Combobox(r, textvariable=self.stopbits_var, values=[1, 1.5, 2], width=6, state="readonly")
        sp_cmb.grid(row=0, column=3, padx=(6, 0), pady=4, sticky="w")
        Tooltip(sp_cmb, "串口停止位（通常 1）。", theme)

        r2 = tk.Frame(s1, bg=card_bg)
        r2.pack(fill="x", pady=(6, 0))
        _label(r2, "默认发送方：").grid(row=0, column=0, sticky="w")
        self.sender_drawer_module = ttk.Radiobutton(r2, text="模组发送", variable=self.serial_sender_var, value="模组发送")
        self.sender_drawer_module.grid(row=0, column=1, padx=(8, 10), sticky="w")
        self.sender_drawer_mcu = ttk.Radiobutton(r2, text="MCU发送", variable=self.serial_sender_var, value="MCU发送")
        self.sender_drawer_mcu.grid(row=0, column=2, sticky="w")
        Tooltip(self.sender_drawer_module, "决定「串口实时」里解析时默认标记哪一方为请求方；\n不影响指令发送 Tab 里单独选择的方向。", theme)
        self.sender_labels = [r2]

        # --------------------------------------------------------
        # 2) 显示选项
        # --------------------------------------------------------
        s2 = _section("显示选项")
        self.detail_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(s2, text="详细模式（逐字段展开）", variable=self.detail_var).pack(anchor="w", pady=2)
        self._hex_drawer_chk = ttk.Checkbutton(
            s2, text="HEX 格式（不勾选 = ASCII 文本模式）", variable=self.hex_format_var,
            command=self._safe(self._on_hex_format_change),
        )
        self._hex_drawer_chk.pack(anchor="w", pady=2)
        Tooltip(self._hex_drawer_chk, "HEX：十六进制报文（默认）\nASCII：纯文本 AT / 调试字符串。", theme)
        self.autoscroll_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(s2, text="自动滚动到最新一条", variable=self.autoscroll_var).pack(anchor="w", pady=2)

        # --------------------------------------------------------
        # 3) 日志 + 原始数据保存
        # --------------------------------------------------------
        s3 = _section("日志 / 原始数据保存")

        l1 = tk.Frame(s3, bg=card_bg)
        l1.pack(fill="x", pady=2)
        _label(l1, "结构化解析日志：").grid(row=0, column=0, sticky="w")
        ttk.Button(l1, text="选择路径并启用…", width=18, style="Toolbar.TButton",
                   command=self._safe(self._choose_log)).grid(row=0, column=1, padx=6, sticky="w")
        Tooltip(l1.winfo_children()[-1],
                "开启后，每一条 RX/TX 解析结果都会按「时间戳 + [RX]/[TX] + 帧内容」格式追加写入 .log 文件。\n再次关闭按钮或重开程序后若文件仍存在，会自动追加而不是覆盖。",
                theme)

        l2 = tk.Frame(s3, bg=card_bg)
        l2.pack(fill="x", pady=2)
        ttk.Checkbutton(l2, text="启用原始数据保存", variable=self.save_raw_enabled_var,
                        command=self._safe(self._on_save_raw_toggle)).grid(row=0, column=0, sticky="w")
        Tooltip(l2.winfo_children()[-1], "将串口上收到/发送的每一笔原始 bytes 落盘。", theme)

        l3 = tk.Frame(s3, bg=card_bg)
        l3.pack(fill="x", pady=2)
        _label(l3, "保存目录：").grid(row=0, column=0, sticky="w")
        self.drawer_raw_path = ttk.Entry(l3, textvariable=self.save_raw_path_var, width=24, state="readonly")
        self.drawer_raw_path.grid(row=0, column=1, padx=6, sticky="we")
        l3.columnconfigure(1, weight=1)
        ttk.Button(l3, text="选择…", width=6, style="Toolbar.TButton",
                   command=self._safe(self._choose_save_raw_path)).grid(row=0, column=2, padx=4)
        _bind_text_widget_menu(self.drawer_raw_path, readonly=True)

        l4 = tk.Frame(s3, bg=card_bg)
        l4.pack(fill="x", pady=2)
        _label(l4, "文件名前缀：").grid(row=0, column=0, sticky="w")
        self.drawer_raw_filename = ttk.Entry(l4, textvariable=self.save_raw_filename_var, width=20)
        self.drawer_raw_filename.grid(row=0, column=1, padx=6, sticky="we")
        _bind_text_widget_menu(self.drawer_raw_filename, readonly=False)
        _label(l4, ".dat", secondary=True).grid(row=0, column=2, sticky="w", padx=(4, 0))

        l5 = tk.Frame(s3, bg=card_bg)
        l5.pack(fill="x", pady=(6, 0))
        _label(l5, "自动分割阈值：").grid(row=0, column=0, sticky="w")
        split_sp = ttk.Spinbox(l5, from_=5, to=2048, width=6, textvariable=self.raw_auto_split_mb_var)
        split_sp.grid(row=0, column=1, padx=6, sticky="w")
        Tooltip(split_sp, "单个文件超过多少 MB 就自动切到下一个（5~2048 MB）。\n默认 50 MB。", theme)
        _label(l5, "MB / 文件", secondary=True).grid(row=0, column=2, sticky="w", padx=(4, 0))

        # --------------------------------------------------------
        # 4) 主题与字体
        # --------------------------------------------------------
        s4 = _section("主题 / 字体 / 外观")

        t1 = tk.Frame(s4, bg=card_bg)
        t1.pack(fill="x", pady=2)
        _label(t1, "主题模式：").grid(row=0, column=0, sticky="w")
        theme_mode = ttk.Combobox(t1, textvariable=self.theme_mode_var, width=10, state="readonly",
                                  values=["light", "dark"])
        theme_mode.grid(row=0, column=1, padx=6, sticky="w")
        Tooltip(theme_mode, "light = 浅色 / dark = 深色（夜间抓包护眼）。", theme)
        _label(t1, "风格：").grid(row=0, column=2, sticky="w", padx=(12, 0))
        theme_style = ttk.Combobox(t1, textvariable=self.theme_style_var, width=10, state="readonly",
                                   values=["win11", "classic"])
        theme_style.grid(row=0, column=3, padx=6, sticky="w")
        Tooltip(theme_style,
                "win11 = 现代圆角+浅阴影（用 ttk clam 自定义近似实现）\nclassic = 系统原生灰色外观。",
                theme)
        ttk.Button(t1, text="立即应用", style="Toolbar.TButton",
                   command=self._safe(self._on_apply_theme)).grid(row=0, column=4, padx=(10, 0), sticky="w")

        t2 = tk.Frame(s4, bg=card_bg)
        t2.pack(fill="x", pady=(8, 2))
        _label(t2, "等宽字体：").grid(row=0, column=0, sticky="w")
        self.drawer_font_family = ttk.Combobox(t2, textvariable=self.font_family_var, width=24,
                                                values=self.available_monospace_fonts or ["Consolas"],
                                                state="readonly")
        self.drawer_font_family.grid(row=0, column=1, padx=6, sticky="we")
        t2.columnconfigure(1, weight=1)
        Tooltip(self.drawer_font_family, "仅显示系统内匹配到的等宽字体。\n推荐：Consolas / JetBrains Mono / Cascadia Code 等。", theme)

        t3 = tk.Frame(s4, bg=card_bg)
        t3.pack(fill="x", pady=2)
        _label(t3, "字号：").grid(row=0, column=0, sticky="w")
        fsize_sp = ttk.Spinbox(t3, from_=8, to=32, width=6, textvariable=self.font_size_var)
        fsize_sp.grid(row=0, column=1, padx=6, sticky="w")
        Tooltip(fsize_sp, "日志区字号（8~32）。也可以 Ctrl + 鼠标滚轮直接在日志区内无级缩放。", theme)
        _label(t3, "行间距：").grid(row=0, column=2, sticky="w", padx=(12, 0))
        lsp_sp = ttk.Spinbox(t3, from_=0, to=24, width=5, textvariable=self.line_spacing_px_var)
        lsp_sp.grid(row=0, column=3, padx=6, sticky="w")
        Tooltip(lsp_sp, "报文之间垂直留白（0~24 像素），值越大报文越稀疏，护眼。", theme)
        ttk.Button(t3, text="字体与字号设置…", style="Toolbar.TButton",
                   command=self._safe(self._choose_font_settings)).grid(row=0, column=4, padx=(10, 0), sticky="w")

        # 字体/行距一改就即时应用 + 保存偏好
        self.font_family_var.trace_add("write", lambda *_a: self._apply_font_and_line_spacing(True))
        self.font_size_var.trace_add("write", lambda *_a: self._apply_font_and_line_spacing(True))
        self.line_spacing_px_var.trace_add("write", lambda *_a: self._apply_font_and_line_spacing(True))

        # --------------------------------------------------------
        # 5) 其他：协议操作 / 其他工具
        # --------------------------------------------------------
        s5 = _section("协议操作 / 其他工具")
        r_prot = tk.Frame(s5, bg=card_bg)
        r_prot.pack(fill="x", pady=2)
        ttk.Button(r_prot, text="刷新协议列表", width=14, style="Toolbar.TButton",
                   command=self._safe(self._load_protocols)).pack(side="left", padx=(0, 6))
        ttk.Button(r_prot, text="导入 Word 协议…", width=16, style="Toolbar.TButton",
                   command=self._safe(self._import_docx)).pack(side="left", padx=6)
        ttk.Button(r_prot, text="查看当前协议", width=14, style="Toolbar.TButton",
                   command=self._safe(self._show_protocol)).pack(side="left", padx=6)

        r_misc = tk.Frame(s5, bg=card_bg)
        r_misc.pack(fill="x", pady=(6, 2))
        ttk.Checkbutton(r_misc, text="窗口始终置顶", variable=self.topmost_var,
                        style="TCheckbutton", command=self._safe(self._toggle_topmost)).pack(side="left", padx=(0, 10))
        ttk.Button(r_misc, text="清空显示", width=12, style="Toolbar.TButton",
                   command=self._safe(self._clear_output)).pack(side="left", padx=6)
        ttk.Button(r_misc, text="添加串口窗口", width=14, style="Toolbar.TButton",
                   command=self._safe(self._add_serial_port)).pack(side="left", padx=6)

        # --------------------------------------------------------
        # 6) 底部：立即保存偏好
        # --------------------------------------------------------
        footer = tk.Frame(cf, bg=card_bg)
        footer.pack(fill="x", padx=10, pady=(12, 12))
        tk.Label(footer, text="所有偏好：下次启动自动还原 ✓",
                 bg=card_bg, fg=theme.get("text_secondary"),
                 font=("Microsoft YaHei UI", 9)).pack(side="left")
        ttk.Button(footer, text="立即保存偏好", command=self._safe(self._save_preferences)).pack(side="right")

    def _build_serial_panel(self, parent: tk.Misc) -> None:
        """构建串口实时主面板（新方案：主区域就是日志框 + 字体控件，低频配置全进 Drawer）。"""
        theme = self.theme
        card_bg = theme.get("card_bg")

        # 日志输出区（占满整个 Tab）
        out_frame = tk.Frame(parent, bg=card_bg,
                             highlightthickness=1,
                             highlightbackground=theme.get("border"))
        out_frame.grid(row=0, column=0, sticky="nsew")
        out_frame.columnconfigure(0, weight=1)
        out_frame.rowconfigure(0, weight=1)

        # Text：日志框
        self.serial_text = tk.Text(
            out_frame,
            font=self.serial_font,
            wrap="word",
            state="disabled",
            bg=theme.get("surface"),
            fg=theme.get("text"),
            insertbackground=theme.get("text"),
            selectbackground=theme.get("primary"),
            selectforeground="#FFFFFF",
            relief="flat",
            padx=10,
            pady=6,
            spacing1=0,
            spacing3=self.line_spacing_px_var.get(),  # 行间距（每段后垂直留白）
            bd=0,
            highlightthickness=0,
        )
        self.serial_text.grid(row=0, column=0, sticky="nsew", padx=0, pady=0)
        # 增强版右键菜单（原来的复制/清空… + 新增【字体与字号设置】）
        self._bind_text_widget_menu_enhanced(self.serial_text, readonly=True)

        scroll = ttk.Scrollbar(out_frame, orient="vertical", command=self.serial_text.yview)
        scroll.grid(row=0, column=1, sticky="ns", padx=(0, 0), pady=0)
        self.serial_text.configure(yscrollcommand=scroll.set)

        # ============================================================
        # 日志框右上角微型控件：A- / 字号显示 / A+
        # ============================================================
        zoom_wrap = tk.Frame(out_frame, bg=theme.get("surface"))
        # place：靠右上角，距离边缘 6px
        zoom_wrap.place(relx=1.0, rely=0.0, anchor="ne", x=-8, y=8)

        def _mini_btn(text: str, cmd, tip: str) -> tk.Button:
            b = tk.Button(
                zoom_wrap, text=text, relief="flat", bd=0, cursor="hand2",
                bg=theme.get("border"), fg=theme.get("text"), padx=8, pady=2,
                activebackground=theme.get("primary"), activeforeground="#FFFFFF",
                font=("Microsoft YaHei UI", 9, "bold"),
                command=cmd,
            )
            Tooltip(b, tip, theme)
            return b

        self._zoom_out_btn = _mini_btn("A−", self._safe(lambda: self._zoom_serial_font(-1)),
                                        "缩小字号（也可以 Ctrl + 鼠标滚轮 向下）")
        self._zoom_out_btn.pack(side="left", padx=(0, 4))
        self._font_size_label = tk.Label(
            zoom_wrap, text=f"{self.font_size_var.get()}pt", padx=6, pady=2,
            bg=theme.get("surface"), fg=theme.get("text_secondary"),
            font=("Microsoft YaHei UI", 9),
        )
        self._font_size_label.pack(side="left", padx=(0, 4))
        self._zoom_in_btn = _mini_btn("A+", self._safe(lambda: self._zoom_serial_font(+1)),
                                       "放大字号（也可以 Ctrl + 鼠标滚轮 向上）")
        self._zoom_in_btn.pack(side="left")

        # ============================================================
        # 定义显示用的 Tag（颜色统一从 theme 取，便于切换主题时刷新）
        # ============================================================
        self._apply_theme_tags()

        # 初始状态：根据 HEX 格式勾选状态决定是否显示发送方（发送方现在在 Drawer 里，这里只保留逻辑同步）
        self._on_hex_format_change()

        # Radiobutton 和 Checkbutton 改值时同步给 collector
        self.serial_sender_var.trace_add("write", self._on_serial_sender_change)
        self.hex_format_var.trace_add("write", self._on_hex_format_sync_collector)

        # Ctrl + 鼠标滚轮：日志框内无级缩放
        self.serial_text.bind("<Control-MouseWheel>", self._safe(self._on_ctrl_mousewheel_text), add="+")

    # ------------------------------------------------------------
    # 增强版 Text 右键菜单（加【字体与字号设置】）
    # ------------------------------------------------------------
    def _bind_text_widget_menu_enhanced(self, widget, readonly: bool = False) -> None:
        # 先绑定原有通用右键菜单+快捷键
        _bind_text_widget_menu(widget, readonly=readonly)
        # 给 readonly 日志框再加一个【字体与字号设置】菜单项：通过覆写 Button-3 菜单实现（最简单）：直接重新弹增强版 menu
        is_text = widget.winfo_class() == "Text"
        if not (readonly and is_text):
            return

        def _copy():
            try:
                if widget.tag_ranges("sel"):
                    widget.event_generate("<<Copy>>")
                else:
                    c = widget.get("1.0", "end-1c")
                    widget.clipboard_clear()
                    widget.clipboard_append(c)
            except Exception:
                pass

        def _clear():
            try:
                widget.configure(state="normal")
                widget.delete("1.0", "end")
                widget.configure(state="disabled")
            except Exception:
                pass

        menu = tk.Menu(widget, tearoff=0)
        menu.add_command(label="复制 (Ctrl+C)", command=_copy, accelerator="Ctrl+C")
        menu.add_separator()
        menu.add_command(label="全选 (Ctrl+A)",
                         command=lambda: (widget.tag_add("sel", "1.0", "end-1c"), None)[1])
        menu.add_command(label="清空显示缓冲", command=_clear)
        menu.add_separator()
        menu.add_command(label="字体与字号设置…", command=self._safe(self._choose_font_settings))
        menu.add_command(label="放大 (Ctrl+滚轮↑)", command=self._safe(lambda: self._zoom_serial_font(+1)))
        menu.add_command(label="缩小 (Ctrl+滚轮↓)", command=self._safe(lambda: self._zoom_serial_font(-1)))

        def _popup(event):
            try:
                menu.tk_popup(event.x_root, event.y_root)
            finally:
                menu.grab_release()

        try:
            widget.bind("<Button-3>", _popup)  # Windows 右键
            widget.bind("<Button-2>", _popup)  # Mac/Linux 中键
        except Exception:
            pass

    # ------------------------------------------------------------
    # 字体：无级缩放 + 设置对话框
    # ------------------------------------------------------------
    def _on_ctrl_mousewheel_text(self, event):
        delta = getattr(event, "delta", 0)
        steps = 1 if delta > 0 else -1
        self._zoom_serial_font(steps)
        return "break"

    def _zoom_serial_font(self, steps: int) -> None:
        new_size = int(self.font_size_var.get()) + int(steps)
        new_size = max(8, min(32, new_size))
        if new_size == int(self.font_size_var.get()):
            return
        self.font_size_var.set(new_size)
        # 注：变量 trace_add 会自动触发 _apply_font_and_line_spacing

    def _choose_font_settings(self) -> None:
        """【字体与字号设置】对话框：等宽字体列表 + 字号 + 行距 + 实时预览。"""
        try:
            self.root.attributes("-topmost", False)
        except Exception:
            pass
        dlg = tk.Toplevel(self.root)
        dlg.title("字体与字号设置")
        dlg.configure(bg=self.theme.get("card_bg"))
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.resizable(False, False)
        pad = 14
        card_bg = self.theme.get("card_bg")

        # 预览前先暂存当前值，点"取消"可回滚
        _bak_family = self.font_family_var.get()
        _bak_size = int(self.font_size_var.get())
        _bak_line = int(self.line_spacing_px_var.get())

        ttk.Label(dlg, text="等宽字体：").grid(row=0, column=0, sticky="e", padx=(pad, 6), pady=(pad, 6))
        fam = ttk.Combobox(dlg, textvariable=self.font_family_var, width=28,
                            values=self.available_monospace_fonts or ["Consolas"], state="readonly")
        fam.grid(row=0, column=1, columnspan=2, sticky="we", padx=(0, pad), pady=(pad, 6))
        dlg.columnconfigure(1, weight=1)

        ttk.Label(dlg, text="字号：").grid(row=1, column=0, sticky="e", padx=(pad, 6), pady=6)
        sz = ttk.Spinbox(dlg, from_=8, to=32, width=6, textvariable=self.font_size_var)
        sz.grid(row=1, column=1, sticky="w", padx=(0, pad), pady=6)

        ttk.Label(dlg, text="行间距 (px)：").grid(row=2, column=0, sticky="e", padx=(pad, 6), pady=6)
        ls = ttk.Spinbox(dlg, from_=0, to=24, width=6, textvariable=self.line_spacing_px_var)
        ls.grid(row=2, column=1, sticky="w", padx=(0, pad), pady=6)

        # 实时预览：用当前 serial_text 真实内容（实时跟着变）
        ttk.Label(dlg, text="预览（实时生效）：").grid(row=3, column=0, columnspan=3, sticky="w",
                                                        padx=(pad, pad), pady=(12, 4))
        preview = tk.Text(dlg, height=8, width=52, relief="solid", bd=1, state="normal",
                          bg=self.theme.get("surface"), fg=self.theme.get("text"))
        preview.grid(row=4, column=0, columnspan=3, padx=pad, pady=(0, 6), sticky="we")
        preview.configure(font=self.serial_font, spacing3=self.line_spacing_px_var.get())
        _sample = (
            "[TX] 03 20 00 03 11 22 33 7A  ← 发送帧（绿色）\n"
            "[RX] Modem→MCU Cmd=0x24(查询属性)  ✓\n"
            "  • 0x12 高度              = 1024 mm\n"
            "  • 0x31 工作模式          = 自动模式\n"
            "RAW(12): A5 A5 03 24 00 0C 12 02 04 00 31 01 02 CC\n"
            "[ERR] 校验失败 预期=CC 实际=FF  ← 异常（红色）"
        )
        preview.insert("1.0", _sample)
        preview.configure(state="disabled")

        # 每次字体/行距变化，同步更新预览 + 真实日志框
        def _sync(_event=None):
            self._apply_font_and_line_spacing(save=False)
            try:
                preview.configure(font=(self.font_family_var.get(), int(self.font_size_var.get())),
                                  spacing3=int(self.line_spacing_px_var.get()))
            except Exception:
                pass

        for v in (self.font_family_var, self.font_size_var, self.line_spacing_px_var):
            try:
                v.trace_add("write", lambda *_a: _sync())
            except Exception:
                pass

        # 底部按钮
        btn_row = tk.Frame(dlg, bg=card_bg)
        btn_row.grid(row=5, column=0, columnspan=3, sticky="we", padx=pad, pady=(12, pad))

        def _on_cancel():
            self.font_family_var.set(_bak_family)
            self.font_size_var.set(_bak_size)
            self.line_spacing_px_var.set(_bak_line)
            self._apply_font_and_line_spacing(save=False)
            dlg.destroy()

        def _on_ok():
            self._save_preferences()
            dlg.destroy()

        ttk.Button(btn_row, text="确定", style="Primary.TButton", command=_on_ok).pack(side="right", padx=(6, 0))
        ttk.Button(btn_row, text="取消", command=_on_cancel).pack(side="right")

        # 居中
        dlg.update_idletasks()
        w = dlg.winfo_width()
        h = dlg.winfo_height()
        sw = dlg.winfo_screenwidth()
        sh = dlg.winfo_screenheight()
        dlg.geometry(f"+{(sw - w) // 2}+{(sh - h) // 3}")
        dlg.wait_window()


    def _build_send_panel(self, parent: tk.Misc) -> None:
        """构建"指令发送"Tab。"""
        tip = ttk.Label(
            parent,
            text="三种发送模式：协议模式（自动组帧+CRC）/ Raw HEX / Raw ASCII；可设置毫秒级周期循环发送。",
            foreground="#555555",
            justify="left",
        )
        tip.grid(row=0, column=0, sticky="we", pady=(0, 6))

        # 模式选择
        mode_row = ttk.LabelFrame(parent, text="发送模式", padding=8)
        mode_row.grid(row=1, column=0, sticky="we", pady=(0, 6))
        mode_row.columnconfigure(0, weight=1)
        for i, (label, value) in enumerate([
            ("协议模式（自动组帧+CRC）", "protocol"),
            ("Raw HEX", "raw_hex"),
            ("Raw ASCII", "raw_ascii"),
        ]):
            rb = ttk.Radiobutton(mode_row, text=label, value=value, variable=self.send_mode_var, command=self._on_send_mode_change)
            rb.grid(row=0, column=i, padx=6, sticky="w")

        # 协议模式内容
        self.protocol_frame = ttk.LabelFrame(parent, text="协议参数", padding=8)
        self.protocol_frame.grid(row=2, column=0, sticky="nsew", pady=(0, 6))
        self.protocol_frame.columnconfigure(1, weight=1)
        self.protocol_frame.columnconfigure(3, weight=1)
        ttk.Label(self.protocol_frame, text="命令字 (CmdID):").grid(row=0, column=0, sticky="w", pady=3)
        self.cmd_code_entry = ttk.Entry(self.protocol_frame, textvariable=self.tx_cmd_code_var, width=20)
        self.cmd_code_entry.grid(row=0, column=1, sticky="we", padx=(0, 12), pady=3)
        ttk.Label(self.protocol_frame, text="方向:").grid(row=0, column=2, sticky="e")
        dir_combo = ttk.Combobox(
            self.protocol_frame,
            textvariable=self.tx_direction_var,
            values=["模组发送", "MCU发送"],
            state="readonly",
            width=12,
        )
        dir_combo.grid(row=0, column=3, sticky="w", pady=3)

        ttk.Label(self.protocol_frame, text="字段 JSON:").grid(row=1, column=0, sticky="nw", pady=3)
        self.fields_text = tk.Text(self.protocol_frame, height=10, font=("Consolas", 10))
        self.fields_text.grid(row=1, column=1, columnspan=3, sticky="nsew", pady=3)
        self.protocol_frame.rowconfigure(1, weight=1)
        # 双向同步 StringVar 和 Text（避免复杂 trace，每次发送时从 text 读）
        self.fields_text.insert("1.0", self.tx_fields_var.get())

        # Raw 内容（共用 1 个帧，通过 mode 显示不同的 placeholder）
        self.raw_frame = ttk.LabelFrame(parent, text="Raw 内容（切换模式后此处改变语义）", padding=8)
        self.raw_frame.grid(row=3, column=0, sticky="nsew", pady=(0, 6))
        self.raw_frame.columnconfigure(0, weight=1)
        self.raw_hint = ttk.Label(self.raw_frame, text="HEX 模式：1A 2B 3C 或 1A2B3C", foreground="#555")
        self.raw_hint.grid(row=0, column=0, sticky="w")
        self.raw_text = tk.Text(self.raw_frame, height=8, font=("Consolas", 10))
        self.raw_text.grid(row=1, column=0, sticky="nsew", pady=3)
        self.raw_frame.rowconfigure(1, weight=1)

        # 周期发送 + 操作按钮
        act = ttk.LabelFrame(parent, text="发送操作", padding=8)
        act.grid(row=4, column=0, sticky="we")
        act.columnconfigure(7, weight=1)
        ttk.Label(act, text="间隔(ms):").grid(row=0, column=0, sticky="w")
        ivs = ttk.Spinbox(act, from_=10, to=3600000, increment=10, textvariable=self.tx_interval_ms_var, width=8)
        ivs.grid(row=0, column=1, sticky="w", padx=(2, 10))
        cb = ttk.Checkbutton(act, text="启用循环", variable=self.tx_cycle_var)
        cb.grid(row=0, column=2, sticky="w", padx=(0, 10))

        self.send_once_btn = ttk.Button(act, text="▶ 发送一次", command=self._safe(self._on_send_once))
        self.send_once_btn.grid(row=0, column=3, sticky="w", padx=2)
        self.tx_cycle_btn = ttk.Button(act, text="▶ 开始循环", command=self._safe(self._on_toggle_cycle_send))
        self.tx_cycle_btn.grid(row=0, column=4, sticky="w", padx=2)
        self.copy_hex_btn = ttk.Button(act, text="复制当前帧 HEX", command=self._safe(self._on_copy_hex))
        self.copy_hex_btn.grid(row=0, column=5, sticky="w", padx=2)
        self.clear_send_btn = ttk.Button(act, text="清空输入", command=self._safe(self._on_clear_send))
        self.clear_send_btn.grid(row=0, column=6, sticky="w", padx=2)

        self._on_send_mode_change()

    def _on_send_mode_change(self) -> None:
        mode = self.send_mode_var.get()
        if mode == "protocol":
            self.protocol_frame.grid()
            self.raw_frame.grid_remove()
        else:
            self.protocol_frame.grid_remove()
            self.raw_frame.grid()
            if mode == "raw_hex":
                self.raw_hint.configure(text="Raw HEX：例如 1A 2B 3C 0D 0A（空格可省略）")
            else:
                self.raw_hint.configure(text="Raw ASCII：直接输入文本内容（写入实际字节= UTF-8 编码 或 原字符）")

    def _current_fields_text(self) -> str:
        try:
            return self.fields_text.get("1.0", "end-1c")
        except Exception:
            return ""

    def _current_raw_text(self) -> str:
        try:
            return self.raw_text.get("1.0", "end-1c")
        except Exception:
            return ""

    def _sync_inputs_to_vars(self) -> None:
        try:
            self.tx_fields_var.set(self._current_fields_text())
        except Exception:
            pass
        try:
            self.tx_raw_var.set(self._current_raw_text())
        except Exception:
            pass

    def _parse_fields_json(self, txt: str) -> dict | list | None:
        s = (txt or "").strip()
        if not s:
            return None
        import json
        try:
            return json.loads(s)
        except Exception:
            raise ValueError(f"字段 JSON 解析失败: {s[:80]}")

    def _encode_current_protocol(self) -> bytes:
        if not self.cfg:
            raise RuntimeError("请先选择协议")
        cmd_s = (self.tx_cmd_code_var.get() or "").strip()
        if not cmd_s:
            raise ValueError("请输入命令字 CmdID")
        if cmd_s.lower().startswith("0x"):
            cmd_code = int(cmd_s, 16)
        else:
            try:
                cmd_code = int(cmd_s, 0)
            except Exception:
                cmd_code = int(cmd_s)
        fields = self._parse_fields_json(self._current_fields_text()) or {}
        direction = self.tx_direction_var.get()
        if direction == "MCU发送":
            dr = "response"
        else:
            dr = "request"
        from protocol_parser.parser import encode_frame
        return encode_frame(cmd_code, self.cfg, direction=dr, fields=fields)

    def _on_send_once(self) -> None:
        if not (self.collector and self.collector.running):
            messagebox.showwarning("提示", "请先打开串口（开始监控）后再发送")
            return
        mode = self.send_mode_var.get()
        try:
            if mode == "protocol":
                data = self._encode_current_protocol()
                self.collector.send(data)
            elif mode == "raw_hex":
                s = self._current_raw_text().strip()
                if not s:
                    raise ValueError("请输入 HEX 内容")
                self.collector.send_raw(s, as_text=False)
            else:  # raw_ascii
                s = self._current_raw_text()
                if not s:
                    raise ValueError("请输入 ASCII 内容")
                self.collector.send_raw(s, as_text=True)
            self._sync_inputs_to_vars()
        except Exception as e:  # noqa: BLE001
            self._report_error("发送失败", e)

    def _on_toggle_cycle_send(self) -> None:
        if self.tx_cycle_var.get():
            # 正在运行 → 停止
            self.tx_cycle_var.set(False)
            if self._tx_cycle_job is not None:
                try:
                    self.root.after_cancel(self._tx_cycle_job)
                except Exception:
                    pass
                self._tx_cycle_job = None
            try:
                self.tx_cycle_btn.configure(text="▶ 开始循环")
            except Exception:
                pass
            self._sync_inputs_to_vars()
        else:
            if not (self.collector and self.collector.running):
                messagebox.showwarning("提示", "请先打开串口（开始监控）后再发送")
                return
            try:
                iv = int(self.tx_interval_ms_var.get())
                if iv < 10:
                    raise ValueError
            except Exception:
                messagebox.showwarning("提示", "循环间隔必须为 ≥ 10ms 的整数")
                return
            self.tx_cycle_var.set(True)
            try:
                self.tx_cycle_btn.configure(text="⏹ 停止循环")
            except Exception:
                pass
            self._sync_inputs_to_vars()
            self._schedule_tx_cycle()
            # 立刻发一次
            self._on_send_once()

    def _schedule_tx_cycle(self) -> None:
        iv = 1000
        try:
            iv = max(10, int(self.tx_interval_ms_var.get()))
        except Exception:
            iv = 1000

        def _job():
            try:
                if not (self.collector and self.collector.running):
                    self.tx_cycle_var.set(False)
                    try:
                        self.tx_cycle_btn.configure(text="▶ 开始循环")
                    except Exception:
                        pass
                    self._tx_cycle_job = None
                    return
                if not self.tx_cycle_var.get():
                    self._tx_cycle_job = None
                    return
                self._on_send_once()
            except Exception as e:  # noqa: BLE001
                try:
                    self._report_error("周期发送出错", e)
                except Exception:
                    pass
            finally:
                try:
                    iv2 = max(10, int(self.tx_interval_ms_var.get()))
                except Exception:
                    iv2 = 1000
                self._tx_cycle_job = self.root.after(iv2, _job)

        self._tx_cycle_job = self.root.after(iv, _job)

    def _on_copy_hex(self) -> None:
        try:
            mode = self.send_mode_var.get()
            if mode == "protocol":
                data = self._encode_current_protocol()
                hex_str = " ".join(f"{b:02X}" for b in data)
            elif mode == "raw_hex":
                s = self._current_raw_text().strip()
                s2 = s.replace(" ", "").replace("\n", "").replace("\r", "").replace("\t", "")
                hex_str = " ".join(s2[i:i + 2].upper() for i in range(0, len(s2), 2))
            else:
                s = self._current_raw_text()
                data = s.encode("utf-8")
                hex_str = " ".join(f"{b:02X}" for b in data)
            self.root.clipboard_clear()
            self.root.clipboard_append(hex_str)
            self._set_status(f"已复制 HEX（{len(hex_str)} 字符）")
        except Exception as e:  # noqa: BLE001
            self._report_error("复制失败", e)

    def _on_clear_send(self) -> None:
        try:
            self.fields_text.delete("1.0", "end")
        except Exception:
            pass
        try:
            self.raw_text.delete("1.0", "end")
        except Exception:
            pass
        self._sync_inputs_to_vars()

    # ---------- 协议加载 ----------

    def _load_protocols(self) -> None:
        """加载协议列表：内置串口3.0协议（始终第一项）+ 用户导入的产品协议。"""
        products: list[tuple[str, str]] = []

        get_builtin_v3(refresh=True)

        products.append(("串口3.0协议", "__builtin_v3__"))

        d = get_protocol_dir()
        if d.exists():
            for f in sorted(d.glob("*.json")):
                if f.name.lower() in ("v3_serial.json", "_template.json"):
                    continue
                try:
                    cfg = load_protocol(f)
                    products.append((cfg.get("product", f.stem), str(f)))
                except Exception:
                    continue

        self.product_combo["values"] = [p[0] for p in products]
        self._product_sources = {p[0]: p[1] for p in products}

        if products:
            self.product_combo.current(0)
            self._load_product_cfg(products[0][0])

        self._set_status(f"已加载 {len(products)} 个协议")

    def _load_product_cfg(self, product_name: str) -> None:
        """加载指定产品协议。"""
        source = self._product_sources.get(product_name)
        if source == "__builtin_v3__":
            self.cfg = get_builtin_v3()
        else:
            try:
                from protocol_parser.parser import merge_protocol
                user_cfg = load_protocol(source)
                self.cfg = merge_protocol(get_builtin_v3(), user_cfg)
            except Exception as e:  # noqa: BLE001  顶层弹 friendly
                self._report_error("协议加载失败", e)
                return

        self._set_status(f"已加载: {product_name}")

    def _on_product_change(self, event=None) -> None:
        """切换产品协议。"""
        self._load_product_cfg(self.product_var.get())

    def _import_docx(self) -> None:
        """导入 Word 协议文档。"""
        from protocol_parser.docx_importer import import_from_docx
        from protocol_parser.attr_editor import AttributeEditorDialog

        path = filedialog.askopenfilename(
            title="选择 Word 协议文档",
            filetypes=[("Word 文档", "*.docx"), ("所有文件", "*.*")],
        )
        if not path:
            return

        try:
            imported_cfg = import_from_docx(path)
        except Exception as e:  # noqa: BLE001  顶层兜底：不甩 traceback 给用户
            self._report_error("导入失败", e)
            return

        # 显示导入告警（比如命令/属性为空、没读到表格等），让用户马上知道哪里可能不对
        warnings = imported_cfg.get("_import_warnings") or []
        if warnings:
            msg_text = "⚠ 导入时发现以下问题，请检查协议文档格式：\n\n"
            for i, w in enumerate(warnings, 1):
                msg_text += f"{i}. {w}\n"
            messagebox.showwarning("Word 导入告警（不影响继续编辑/保存）", msg_text, parent=self.root)

        dlg = AttributeEditorDialog(self.root, imported_cfg)
        self.root.wait_window(dlg.dialog)

        if dlg.result:
            import json

            user_cfg = dlg.result

            protocol_name = user_cfg.get("product", Path(path).stem)
            save_path = get_protocol_dir() / f"{protocol_name}.json"
            with open(save_path, "w", encoding="utf-8") as f:
                json.dump(user_cfg, f, ensure_ascii=False, indent=2)

            self._load_protocols()
            idx = [p[0] for p in list(self._product_sources.items())].index(protocol_name)
            if idx >= 0:
                self.product_combo.current(idx)

            self._set_status(f"已导入: {protocol_name}")

    def _show_protocol(self) -> None:
        """查看当前协议详情。"""
        if not self.cfg:
            return

        import json

        content = json.dumps(self.cfg, ensure_ascii=False, indent=2)

        dlg = tk.Toplevel(self.root)
        dlg.title(f"协议详情 - {self.cfg.get('product', '')}")
        dlg.geometry("800x600")

        text = tk.Text(dlg, font=("Consolas", 10))
        text.pack(fill="both", expand=True)
        text.insert("1.0", content)
        text.configure(state="disabled")

        scroll = ttk.Scrollbar(dlg, command=text.yview)
        scroll.pack(fill="y", side="right")
        text.configure(yscrollcommand=scroll.set)

    def _on_hex_format_change(self) -> None:
        """HEX/ASCII 切换：新 UI 下不再有"显示/隐藏发送方"的操作（发送方改在 Drawer 里一直显示），
        只保留 ASCII 模式下强制 direction=None 的逻辑，和未来串口启动时保持一致。
        """
        hex_checked = bool(self.hex_format_var.get())
        try:
            state_txt = "normal" if hex_checked else "disabled"
            for item in (self.sender_drawer_module, self.sender_drawer_mcu):
                try:
                    item.configure(state=state_txt)
                except Exception:
                    pass
        except Exception:
            pass

    def _clear_output(self) -> None:
        """清空输出。"""
        self.serial_text.configure(state="normal")
        self.serial_text.delete("1.0", "end")
        self.serial_text.configure(state="disabled")

    def _toggle_topmost(self) -> None:
        """切换窗口置顶状态。"""
        self.root.attributes("-topmost", self.topmost_var.get())
        status = "已置顶" if self.topmost_var.get() else "已取消置顶"
        self._set_status(status)

    # ------------------------------------------------------------
    # 新增：抽屉/主题/字体/偏好 相关方法
    # ------------------------------------------------------------
    def _toggle_drawer(self) -> None:
        try:
            if self.drawer is None:
                return
            self.drawer.toggle()
            # 顺便保存当前折叠状态
            try:
                self._save_preferences()
            except Exception:
                pass
        except Exception as e:  # noqa: BLE001
            self._report_error("切换高级设置失败", e)

    def _apply_theme_tags(self) -> None:
        """刷新 serial_text 的 tag 颜色/字体。颜色统一从 ThemeManager 取。"""
        if not hasattr(self, "serial_text") or self.serial_text is None:
            return
        t = self.theme
        self.serial_text.tag_configure("ts", foreground=t.get("ts"), font=self.serial_font)
        self.serial_text.tag_configure("ok", foreground=t.get("success"), font=self.serial_font)
        self.serial_text.tag_configure("err", foreground=t.get("error"), font=self.serial_font)
        self.serial_text.tag_configure("cmd", foreground=t.get("cmd"), font=self.cmd_font)
        self.serial_text.tag_configure("field", foreground=t.get("field"), font=self.serial_font)
        self.serial_text.tag_configure("raw", foreground=t.get("raw"), font=self.serial_font)
        self.serial_text.tag_configure("tx", foreground=t.get("tx"), font=self.cmd_font)
        self.serial_text.tag_configure("direction", foreground=t.get("warn"), font=self.cmd_font)
        self.serial_text.tag_configure("pid", foreground=t.get("pid"), font=self.cmd_font)
        self.serial_text.tag_configure("model", foreground=t.get("model"), font=self.cmd_font)
        self.serial_text.tag_configure("raw_data", foreground=t.get("raw_data"), font=self.serial_font)

    def _apply_font_and_line_spacing(self, save: bool = True) -> None:
        """改字体/字号/行距时：
        1) 重建 serial_font / cmd_font 并配置到 serial_text；
        2) 所有 tag 的 font 重新设置（等宽字体保证对齐，仅改大小不变颜色）；
        3) Text spacing3（行间距）= line_spacing_px；
        4) 右上角 A- / 字号 / A+ 控件同步显示。
        """
        # 1) 更新可变 Font
        try:
            family = self.font_family_var.get() or "Consolas"
            size = max(8, min(32, int(self.font_size_var.get())))
            self.serial_font.configure(family=family, size=size)
            self.cmd_font.configure(family=family, size=size, weight="bold")
        except Exception:
            pass
        # 2) 应用到 serial_text Text 控件
        try:
            self.serial_text.configure(font=self.serial_font)
        except Exception:
            pass
        # 3) 行距：spacing3
        try:
            spacing3 = max(0, min(40, int(self.line_spacing_px_var.get())))
            self.serial_text.configure(spacing3=spacing3)
        except Exception:
            spacing3 = 0
        # 4) 同步 tag 颜色/字体（包含 cmd_font）
        self._apply_theme_tags()
        # 5) 右上角微型控件显示字号
        try:
            if hasattr(self, "_font_size_label") and self._font_size_label is not None:
                size_pt = int(self.font_size_var.get())
                self._font_size_label.configure(text=f"{size_pt}pt")
        except Exception:
            pass
        # 6) 指令发送 Tab 里的两个代码 Text 也跟着改（fields_text / raw_text）—— 保证 HEX 对齐不错位
        try:
            for _w in (getattr(self, "fields_text", None), getattr(self, "raw_text", None)):
                if _w is None:
                    continue
                _w.configure(font=self.serial_font)
        except Exception:
            pass
        if save:
            try:
                self._save_preferences()
            except Exception:
                pass

    def _on_apply_theme(self) -> None:
        """高级设置 →「立即应用」按钮：切主题（Light/Dark + Win11/Classic）并重绘所有颜色。"""
        # 1) 切 theme.mode/style
        new_mode = str(self.theme_mode_var.get())
        new_style = str(self.theme_style_var.get())
        if new_mode not in ("light", "dark"):
            new_mode = "light"
        if new_style not in ("win11", "classic"):
            new_style = "win11"
        if self.theme.mode != new_mode or self.theme.style != new_style:
            self.theme.mode = new_mode
            self.theme.style = new_style
            try:
                self.theme.apply_ttk_styles(self.ttk_style)
            except Exception as e:  # noqa: BLE001
                self._report_error("应用 ttk 样式失败", e)
        t = self.theme
        # 2) 根窗口 / 顶栏 / Notebook 体 / 状态栏 / Drawer 颜色
        try:
            self.root.configure(bg=t.get("app_bg"))
        except Exception:
            pass
        try:
            if self.drawer is not None:
                self.drawer.theme = self.theme
                self.drawer.refresh_colors()
        except Exception:
            pass
        # 3) serial_text / 字体zoom_wrap 重绘颜色
        try:
            self.serial_text.configure(
                bg=t.get("surface"),
                fg=t.get("text"),
                insertbackground=t.get("text"),
                selectbackground=t.get("primary"),
                selectforeground="#FFFFFF",
            )
        except Exception:
            pass
        try:
            if hasattr(self, "drawer_btn"):
                self.drawer_btn.configure(bg=t.get("app_bg"), fg=t.get("text"),
                                          activebackground=t.get("border"), activeforeground=t.get("text"))
        except Exception:
            pass
        # 4) 顶部工具栏所有 Label/Frame（这些是 tk.Label，不是 ttk.Label，需要手动改）
        try:
            for child in getattr(self, "_top_toolbar_children", []):
                try:
                    if isinstance(child, tk.Label):
                        child.configure(bg=t.get("app_bg"), fg=t.get("text_secondary"))
                    elif isinstance(child, tk.Frame):
                        child.configure(bg=t.get("app_bg"))
                except Exception:
                    pass
        except Exception:
            pass
        # 5) tag 颜色/字体 + zoom 控件颜色
        self._apply_font_and_line_spacing(save=False)
        try:
            for attr in ("_zoom_in_btn", "_zoom_out_btn"):
                btn = getattr(self, attr, None)
                if btn is None:
                    continue
                btn.configure(bg=t.get("border"), fg=t.get("text"),
                              activebackground=t.get("primary"), activeforeground="#FFFFFF")
            if getattr(self, "_font_size_label", None) is not None:
                self._font_size_label.configure(bg=t.get("surface"), fg=t.get("text_secondary"))
        except Exception:
            pass
        # 6) 保存偏好
        try:
            self._save_preferences()
        except Exception:
            pass
        self._set_status(f"已应用主题：{new_mode} / {new_style}")

    def _refresh_max_raw_size(self) -> None:
        try:
            mb = max(1, int(self.raw_auto_split_mb_var.get()))
            self.save_raw_max_size = mb * 1024 * 1024
        except Exception:
            self.save_raw_max_size = 50 * 1024 * 1024

    def _save_preferences(self) -> None:
        """把视觉/字体/抽屉/高级设置偏好写进 snapshot.extras（不改动串口会话字段）。"""
        snap = load_snapshot() or SessionSnapshot()
        try:
            extras = dict(snap.extras) if isinstance(snap.extras, dict) else {}
        except Exception:
            extras = {}
        extras.update({
            "theme_mode": self.theme.mode,
            "theme_style": self.theme.style,
            "font_family": self.font_family_var.get(),
            "font_size": int(self.font_size_var.get()),
            "line_spacing_px": int(self.line_spacing_px_var.get()),
            "drawer_open": bool(self.drawer.visible) if self.drawer is not None else False,
            "topmost": bool(self.topmost_var.get()),
            "save_raw_enabled_default": bool(self.save_raw_enabled_var.get()),
            "save_raw_path_default": self.save_raw_path_var.get(),
            "raw_auto_split_mb": int(self.raw_auto_split_mb_var.get()),
        })
        snap.extras = extras
        try:
            save_snapshot(snap)
        except Exception as e:  # noqa: BLE001
            # 保存偏好失败不影响使用，只写本地 error log
            try:
                _log_error_to_disk(e)
            except Exception:
                pass

    def _on_app_close(self) -> None:
        """WM_DELETE_WINDOW：保存偏好 → 停串口 → 关日志/原始文件 → destroy。"""
        try:
            self._save_preferences()
        except Exception:
            pass
        try:
            if self.is_collecting:
                self._stop_serial()
        except Exception:
            pass
        try:
            if self.log_file is not None:
                self.log_file.flush()
                self.log_file.close()
                self.log_file = None
        except Exception:
            pass
        try:
            self._close_save_raw_file()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass


    # ---------- 添加串口窗口 ----------

    def _add_serial_port(self) -> None:
        """添加新串口：启动同一份程序的独立进程，带 --monitor port baud 参数。"""
        ports = SerialCollector.list_ports()
        if not ports:
            messagebox.showwarning("提示", "未找到可用串口")
            return

        dlg = tk.Toplevel(self.root)
        dlg.title("添加串口")
        dlg.geometry("380x200")
        dlg.resizable(False, False)
        dlg.transient(self.root)
        dlg.grab_set()

        x = self.root.winfo_x() + (self.root.winfo_width() - 380) // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - 200) // 2
        dlg.geometry(f"+{x}+{y}")

        frm = ttk.Frame(dlg, padding=16)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text="串口:").grid(row=0, column=0, sticky="w", pady=4)
        port_display_list = []
        for p in ports:
            desc = p.get("description", "")
            if desc and desc != p["device"]:
                port_display_list.append(f'{p["device"]} - {desc}')
            else:
                port_display_list.append(p["device"])

        port_var = tk.StringVar()
        port_combo = ttk.Combobox(frm, textvariable=port_var, values=port_display_list, width=30, state="readonly")
        port_combo.grid(row=0, column=1, sticky="ew", padx=(8, 0), pady=4)
        if port_display_list:
            port_combo.current(0)

        ttk.Label(frm, text="波特率:").grid(row=1, column=0, sticky="w", pady=4)
        baudrate_var = tk.StringVar(value="115200")
        baud_combo = ttk.Combobox(
            frm, textvariable=baudrate_var,
            values=[9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600, 1000000, 1500000, 2000000, 3000000, 4000000, 5000000, 6000000],
            width=10, state="normal",
        )
        baud_combo.grid(row=1, column=1, sticky="w", padx=(8, 0), pady=4)
        ttk.Label(frm, text="(支持自定义,含6M)", foreground="#888").grid(row=1, column=1, sticky="e", padx=(8, 0), pady=4)

        btn_frm = ttk.Frame(frm)
        btn_frm.grid(row=2, column=0, columnspan=2, pady=(12, 0))

        def on_ok():
            port_display = port_var.get()
            if not port_display:
                messagebox.showwarning("提示", "请选择串口", parent=dlg)
                return
            port = port_display.split(" - ")[0].strip()
            try:
                baudrate = int(str(baudrate_var.get()).strip())
                if baudrate <= 0:
                    raise ValueError
            except Exception:
                messagebox.showwarning("提示", "波特率必须是正整数", parent=dlg)
                return
            self._spawn_monitor(port, baudrate)
            dlg.destroy()
            self._set_status(f"已打开串口监控进程: {port}")

        ttk.Button(btn_frm, text="确定", command=on_ok).pack(side="left", padx=8)
        ttk.Button(btn_frm, text="取消", command=dlg.destroy).pack(side="left", padx=8)

    def _spawn_monitor(self, port: str, baudrate: int) -> None:
        """启动独立进程运行相同程序，传 --monitor port baud。"""
        import subprocess

        if getattr(sys, "frozen", False):
            cmd = [sys.executable, "--monitor", port, str(int(baudrate))]
            DETACHED_PROCESS = 0x00000008
            try:
                subprocess.Popen(cmd, creationflags=DETACHED_PROCESS, close_fds=True)
            except Exception as e:
                messagebox.showerror("启动失败", f"无法启动新进程（EXE模式）: {e}")
        else:
            script_path = Path(__file__).resolve()
            try:
                subprocess.Popen([sys.executable, str(script_path), "--monitor", port, str(int(baudrate))], close_fds=True)
            except Exception as e:
                messagebox.showerror("启动失败", f"无法启动新进程（开发模式）: {e}")

    def _apply_monitor_args(self) -> None:
        """启动参数 --monitor port baud 生效：选中指定串口/波特率。"""
        self.root.update_idletasks()
        self._refresh_ports()
        display_values = list(self.port_combo["values"])
        if self._monitor_port:
            matched = -1
            for i, disp in enumerate(display_values):
                if disp == self._monitor_port or disp.startswith(self._monitor_port + " ") or disp.startswith(self._monitor_port + "-"):
                    matched = i
                    break
            if matched >= 0:
                self.port_combo.current(matched)
        try:
            self.baudrate_var.set(str(int(self._monitor_baud)))
        except Exception:
            self.baudrate_var.set(str(self._monitor_baud))
        self.root.title(f"串口监控 v{VERSION} - {self._monitor_port} @ {self._monitor_baud}")

    # ---------- 更新后：自动恢复会话快照 ----------

    def _maybe_restore_session_after_update(self) -> None:
        """启动后如果 `_update_session.json` 存在，按优先级恢复：

        1) 协议（产品/协议来源：__builtin_v3__ 或文件路径，若源丢了就保留当前默认）
        2) 串口/波特率/帧格式/HEX·ASCII/方向/详细模式
        3) 日志/原始数据保存开关 & 路径（文件还在就接上）
        4) 若 `was_collecting=True` → 尝试自动 start serial；串口不存在/被拔了只弹友好提示，绝不崩。
        5) 所有恢复步骤，即使 ① 协议/串口任何一步出错 → 立刻 clear_snapshot() 防止下次启动再进入恢复流程循环尝试。

        约定：失败友好提示不阻塞主循环，所有异常（非 ProtocolError 也 _report_error。
        """
        snap = load_snapshot()
        if not snap:
            return

        product_tip: list[str] = []
        recovered_need_collect = bool(snap.was_collecting)
        port_matched_combobox = False
        serial_launch_attempted = False
        serial_launch_succeeded = False
        serial_error_friendly: str | None = None
        serial_error_debug: Exception | None = None

        # 1) 恢复协议（如果源还存在）
        try:
            if snap.product_name and snap.product_source:
                # 如果 source 存在就尝试加载
                if snap.product_source == "__builtin_v3__":
                    # 内置 V3 一定在
                    try:
                        self.product_var.set(snap.product_name)
                        self._load_product_cfg(snap.product_name)
                        product_tip.append(f"恢复协议: {snap.product_name}")
                    except Exception as e:  # noqa: BLE001
                        self._report_error("恢复更新会话（协议）", e)
                else:
                    import os as _os
                    if _os.path.isfile(snap.product_source):
                        # 源文件还在，但 product_combo 可能没有，把它塞进 product_sources 并加进下拉
                        existing = self._product_sources if hasattr(self, "_product_sources") else {}
                        if snap.product_name not in existing:
                            try:
                                values = list(self.product_combo["values"]) if self.product_combo.get() else []
                                values.append(snap.product_name)
                                self.product_combo["values"] = values
                                self._product_sources[snap.product_name] = snap.product_source
                            except Exception:
                                pass
                        try:
                            self.product_var.set(snap.product_name)
                            self._load_product_cfg(snap.product_name)
                            product_tip.append(f"恢复协议: {snap.product_name}")
                        except Exception as e:  # noqa: BLE001
                            self._report_error("恢复更新会话（协议）", e)
                    else:
                        # 文件被用户删掉了，跳过
                        pass
        except Exception as e:  # noqa: BLE001
            self._report_error("恢复更新会话（协议）", e)

        # 2) 恢复串口/波特率/帧格式 UI
        try:
            if snap.port:
                self._refresh_ports()
                display_values = list(self.port_combo["values"])
                matched = -1
                for i, disp in enumerate(display_values):
                    if disp == snap.port or disp.startswith(snap.port + " ") or disp.startswith(snap.port + "-"):
                        matched = i
                        break
                if matched >= 0:
                    self.port_combo.current(matched)
                    port_matched_combobox = True
                    product_tip.append(f"串口: {snap.port}")
                else:
                    # 下拉没匹配到也把字符串填到var里（下次打开端口选单若选上）
                    try:
                        self.port_var.set(snap.port)
                    except Exception:
                        pass
                    if recovered_need_collect:
                        # 标记下等会儿 start_serial 会失败；这里提前提示
                        product_tip.append(f"串口 {snap.port} 暂不可用")
            try:
                self.baudrate_var.set(str(int(snap.baudrate)))
            except Exception:
                self.baudrate_var.set(str(snap.baudrate))
            try:
                self.bytesize_var.set(str(int(snap.bytesize)))
            except Exception:
                pass
            try:
                stopbits_val = {1.0: "1", 1.5: "1.5", 2.0: "2"}.get(float(snap.stopbits), "1")
                self.stopbits_var.set(stopbits_val)
            except Exception:
                pass
        except Exception as e:  # noqa: BLE001
            self._report_error("恢复更新会话（串口设置）", e)

        # 3) 数据格式/方向/详细模式
        try:
            self.hex_format_var.set(bool(snap.is_hex_format))
            self._on_hex_format_change()
            if snap.direction == "request":
                self.serial_sender_var.set("模组发送")
            elif snap.direction == "response":
                self.serial_sender_var.set("MCU发送")
            else:
                pass
            try:
                self.detail_var.set(bool(snap.detail_mode))
            except Exception:
                pass
        except Exception:
            pass

        # 4) 日志路径 & 原始数据开关 + 路径（若文件还存在则接上，保留原文件名继续加）
        try:
            if snap.log_path:
                import os as _os
                log_p = Path(snap.log_path)
                # 保证目录存在；如果文件已经存在就"追加"模式打开，不存在先不打开，等用户再点"开始记录"
                try:
                    log_p.parent.mkdir(parents=True, exist_ok=True)
                except Exception:
                    pass
                if _os.path.isfile(str(log_p)):
                    try:
                        self.log_path = log_p
                        self.log_file = open(str(log_p), "a", encoding="utf-8")
                        from datetime import datetime
                        self.log_file.write(f"\n===== 更新重启后继续记录 {datetime.now().isoformat(timespec='seconds')} =====\n")
                        self.log_file.flush()
                        product_tip.append("继续写入日志")
                    except Exception:
                        self.log_file = None
                        self.log_path = None
        except Exception:
            pass
        try:
            if getattr(self, "save_raw_enabled_var", None) is not None:
                self.save_raw_enabled_var.set(bool(snap.save_raw_enabled))
            if getattr(self, "save_raw_path_var", None) is not None and snap.save_raw_path:
                self.save_raw_path_var.set(snap.save_raw_path)
            if getattr(self, "save_raw_filename_var", None) is not None and snap.save_raw_filename:
                self.save_raw_filename_var.set(snap.save_raw_filename)
        except Exception:
            pass

        # 4.5) 发送面板 + 周期发送配置恢复
        tx_cycle_should_run = False
        try:
            if getattr(self, "send_mode_var", None) is not None and snap.tx_send_mode:
                self.send_mode_var.set(snap.tx_send_mode)
            if getattr(self, "tx_cmd_code_var", None) is not None and snap.tx_cmd_code is not None:
                self.tx_cmd_code_var.set(snap.tx_cmd_code)
            if getattr(self, "tx_direction_var", None) is not None and snap.tx_direction is not None:
                self.tx_direction_var.set(snap.tx_direction or "模组发送")
            if getattr(self, "tx_fields_var", None) is not None and snap.tx_fields_json is not None:
                self.tx_fields_var.set(snap.tx_fields_json)
            if getattr(self, "tx_raw_var", None) is not None and snap.tx_raw is not None:
                self.tx_raw_var.set(snap.tx_raw)
            if getattr(self, "tx_interval_ms_var", None) is not None and snap.tx_interval_ms:
                self.tx_interval_ms_var.set(int(snap.tx_interval_ms))
            # 只有之前 is_collecting 且 tx_cycle_enabled=True 才尝试恢复运行状态
            # （因为发送依赖 open 串口，若 5) 成功打开串口，最后这里再起循环）
            tx_cycle_should_run = bool(snap.tx_cycle_enabled)
            product_tip.append("发送面板配置")
        except Exception:
            tx_cycle_should_run = False

        # 5) 自动开始接收（重点：串口被拔 → _start_serial 已自己 _report_error，不会崩；这里再兜一层）
        if recovered_need_collect and snap.port:
            # 跳到"串口实时"tab
            try:
                self.notebook.select(self.serial_tab)
            except Exception:
                pass
            serial_launch_attempted = True
            try:
                self._start_serial()
                serial_launch_succeeded = bool(self.is_collecting)
            except Exception as e:  # noqa: BLE001
                # _start_serial 自己也 _report_error；这里把 friendly 记住，防止它吞掉异常
                serial_error_debug = e
                try:
                    from protocol_parser.parser import classify_protocol_error
                    serial_error_friendly, _ = classify_protocol_error(e)
                except Exception:
                    serial_error_friendly = None
                self._report_error("自动恢复串口接收失败", e)
            if not serial_launch_succeeded:
                if not port_matched_combobox and serial_error_friendly is None:
                    # 串口根本不在列表里；用我们自己的提示（因为此时 _start_serial 里面走的是 showwarning "请选择串口"，不是_串口读取错误）
                    serial_error_friendly = f"检测不到 {snap.port}，可能是更新过程中串口线被拔出或被其它程序占用。"
                # _start_serial 里 _report_error 已经弹过；这里状态栏再补一句文字
                try:
                    self._set_status(f"{snap.port} 无法恢复（请确认串口线是否已重新接入）")
                except Exception:
                    pass
            # 5.5) 如果串口成功打开 & 之前在周期发送：恢复 Tk after 定时器
            if serial_launch_succeeded and tx_cycle_should_run:
                try:
                    self.tx_cycle_var.set(True)
                    try:
                        if self.tx_cycle_btn:
                            self.tx_cycle_btn.configure(text="⏹ 停止循环")
                    except Exception:
                        pass
                    self._schedule_tx_cycle()
                    product_tip.append("恢复周期发送")
                except Exception as e:  # noqa: BLE001
                    self._report_error("恢复周期发送失败", e)

        # 收尾：清掉快照（无论成功失败都清）
        clear_snapshot()

        tip_parts: list[str] = []
        if product_tip:
            tip_parts.append("、".join(product_tip))

        # 串口重连失败 → 合并进"会话已恢复"弹窗，而不是单独又弹一个（避免双弹窗）
        if serial_launch_attempted and not serial_launch_succeeded:
            msg = serial_error_friendly or f"未能重新建立 {snap.port} 的串口连接。"
            tip_parts.append(f"串口未恢复：{msg}")

        if tip_parts:
            tip = "更新后已自动恢复会话：\n" + "\n".join("• " + p for p in tip_parts)
            if serial_launch_attempted and not serial_launch_succeeded:
                tip += (
                    f"\n\n串口线可能在更新期间被拔出，请重新插入 {snap.port}，"
                    "然后点击主窗口「开始监控」即可恢复接收。"
                )
        else:
            tip = "更新后已自动恢复会话。"

        try:
            messagebox.showinfo("会话已恢复", tip, parent=self.root)
        except Exception:
            try:
                self._set_status(tip.replace("\n", " | "))
            except Exception:
                pass


    # ---------- 串口实时 ----------

    def _refresh_ports(self) -> None:
        """刷新可用串口列表。"""
        ports = SerialCollector.list_ports()
        display_list = []
        for p in ports:
            desc = p.get("description", "")
            if desc and desc != p["device"]:
                display_list.append(f'{p["device"]} - {desc}')
            else:
                display_list.append(p["device"])
        self.port_combo["values"] = display_list
        if display_list and not self.port_var.get():
            self.port_combo.current(0)
        self._set_status(f"找到 {len(ports)} 个串口")

    def _toggle_serial(self) -> None:
        """切换串口监控状态。"""
        if self.is_collecting:
            self._stop_serial()
        else:
            self._start_serial()

    def _start_serial(self) -> None:
        """启动串口监控。"""
        if not self.cfg:
            messagebox.showwarning("提示", "请先选择产品协议")
            return
        port_display = self.port_var.get()
        if not port_display:
            messagebox.showwarning("提示", "请选择串口")
            return
        port = port_display.split(" - ")[0].strip()
        try:
            baud_s = str(self.baudrate_var.get()).strip()
            if not baud_s:
                raise ValueError("empty")
            baudrate = int(baud_s)
            if baudrate <= 0:
                raise ValueError("non-positive")
        except Exception:
            messagebox.showwarning("提示", "波特率必须填写正整数（支持自定义，如6M填6000000）")
            return
        try:
            bytesize = int(self.bytesize_var.get())
        except Exception:
            bytesize = 8
        try:
            stopbits = float(self.stopbits_var.get())
        except Exception:
            stopbits = 1

        self._set_status(f"正在连接 {port} @ {baudrate}...")
        self.root.update()

        def on_frame(result, frame, ts):
            try:
                self._ui_queue.append(("serial_frame", (result, ts)))
                self._write_raw_data(frame.raw, ts)
            except Exception as e:  # noqa: BLE001  串口线程里绝对不能裸抛
                try:
                    _log_error_to_disk(e)
                except Exception:
                    pass

        def on_error(msg):
            try:
                self._ui_queue.append(("serial_error", (msg,)))
            except Exception as e:  # noqa: BLE001
                try:
                    _log_error_to_disk(e)
                except Exception:
                    pass

        def on_raw(data, ts):
            try:
                self._ui_queue.append(("serial_raw", (data, ts)))
                self._write_raw_data(data, ts)
            except Exception as e:  # noqa: BLE001
                try:
                    _log_error_to_disk(e)
                except Exception:
                    pass

        def on_tx_sent(data_sent: bytes, direction_label: str, ts: float):
            """TX 成功回调：同屏显示 + 原始数据保存 + 统计。
            - 方向固定：direction_label=TX
            - data_sent：真正写进串口的 bytes
            - 协议模式 data_sent 本身就是完整帧，所以可以直接"解析自己发出去的帧"得到 ParseResult，
              以 RX 同样的格式插入到 serial_text，只是在最前面带一个颜色化的 TX 标签。
            """
            try:
                self.tx_frame_count += 1
                # 写原始数据文件（和 RX 分开一行前缀，方便之后过滤）
                self._write_raw_data(data_sent, ts, prefix="TX ")
                self._ui_queue.append(("serial_tx", (data_sent, ts)))
            except Exception as e:  # noqa: BLE001
                try:
                    _log_error_to_disk(e)
                except Exception:
                    pass

        # HEX 未勾选（ASCII模式）时发送方不生效，direction 置为 None
        direction = None
        if bool(self.hex_format_var.get()):
            sender = self.serial_sender_var.get()
            if sender == "模组发送":
                direction = "request"
            elif sender == "MCU发送":
                direction = "response"

        is_ascii = not bool(self.hex_format_var.get())

        try:
            self.collector = SerialCollector(
                cfg=self.cfg,
                port=port,
                baudrate=baudrate,
                bytesize=bytesize,
                stopbits=stopbits,
                direction=direction,
                on_frame=on_frame,
                on_error=on_error,
                on_raw=on_raw,
                raw_mode=is_ascii,
                on_tx_sent=on_tx_sent,
            )
            self.collector.start()
        except Exception as e:  # noqa: BLE001
            self._report_error("串口打开失败", e)
            self._set_status("就绪")
            return

        self.is_collecting = True
        try:
            self.start_btn.configure(text="●  停止监控", style="Danger.TButton")
        except Exception:
            self.start_btn.configure(text="停止监控")
        mode_label = "ASCII" if is_ascii else "HEX"

        if self.save_raw_enabled_var.get():
            self._open_save_raw_file()
            self._set_status(f"监控中: {port} @ {baudrate} ({mode_label}) - 保存原始数据")
        else:
            self._set_status(f"监控中: {port} @ {baudrate} ({mode_label})")

    def _on_serial_sender_change(self, *args) -> None:
        """切换发送方（Radiobutton变化会直接改variable，这里主动同步给collector）。"""
        # 先同步 UI 显示隐藏（防止未来拓展）
        try:
            self._on_hex_format_change()
        except Exception:
            pass
        if not self.collector:
            return
        direction = None
        if bool(self.hex_format_var.get()):
            sender = self.serial_sender_var.get()
            if sender == "模组发送":
                direction = "request"
            elif sender == "MCU发送":
                direction = "response"
        self.collector.direction = direction
        self._set_status(f"已切换发送方: {self.serial_sender_var.get()}")

    def _on_hex_format_sync_collector(self, *args) -> None:
        """HEX格式勾选变化，更新UI显示/隐藏发送方，并同步raw_mode/direction给collector。"""
        self._on_hex_format_change()
        if not self.collector:
            return
        is_ascii = not bool(self.hex_format_var.get())
        self.collector.raw_mode = is_ascii
        # 同步 direction：ASCII不用direction
        direction = None
        if not is_ascii:
            sender = self.serial_sender_var.get()
            if sender == "模组发送":
                direction = "request"
            elif sender == "MCU发送":
                direction = "response"
        self.collector.direction = direction
        mode_label = "ASCII" if is_ascii else "HEX"
        self._set_status(f"已切换数据格式: {mode_label}")

    def _choose_save_raw_path(self) -> None:
        """选择原始数据保存路径。"""
        path = filedialog.askdirectory(title="选择保存路径")
        if path:
            self.save_raw_path_var.set(path)
            if self.save_raw_enabled_var.get() and self.is_collecting:
                self._open_save_raw_file()

    def _on_save_raw_toggle(self) -> None:
        """切换保存原始数据开关。"""
        if self.save_raw_enabled_var.get():
            if not self.save_raw_path_var.get():
                path = filedialog.askdirectory(title="选择保存路径")
                if not path:
                    self.save_raw_enabled_var.set(False)
                    return
                self.save_raw_path_var.set(path)
            if self.is_collecting:
                self._open_save_raw_file()
                self._set_status(f"原始数据保存开启: {self.save_raw_path_var.get()}")
        else:
            self._close_save_raw_file()
            self._set_status("原始数据保存已关闭")

    def _open_save_raw_file(self) -> None:
        """打开原始数据保存文件。"""
        self._close_save_raw_file()
        save_dir = Path(self.save_raw_path_var.get())
        if not save_dir.exists():
            try:
                save_dir.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                messagebox.showerror("路径错误", f"无法创建目录: {e}")
                self.save_raw_enabled_var.set(False)
                return

        filename = self.save_raw_filename_var.get().strip()
        if not filename:
            filename = "serial_data"

        if self.save_raw_count > 0:
            filepath = save_dir / f"{filename}_{self.save_raw_count:03d}.dat"
        else:
            filepath = save_dir / f"{filename}.dat"

        try:
            self.save_raw_file = open(filepath, "w", encoding="utf-8")
            self.save_raw_current_size = 0
            self._save_raw_active = True
            self._set_status(f"正在保存原始数据: {filepath}")
        except Exception as e:
            messagebox.showerror("文件错误", f"无法打开文件: {e}")
            self.save_raw_enabled_var.set(False)

    def _close_save_raw_file(self) -> None:
        """关闭原始数据保存文件。"""
        self._save_raw_active = False
        if self.save_raw_file:
            try:
                self.save_raw_file.close()
            except Exception:
                pass
            self.save_raw_file = None
            self.save_raw_current_size = 0

    def _write_raw_data(self, data: bytes, ts: float, prefix: str = "") -> None:
        """写入原始数据到文件，超过50MB自动分割。prefix 可写"TX "来区分发送。"""
        if not self._save_raw_active or not self.save_raw_file:
            return
        try:
            if not self.save_raw_enabled_var.get():
                return
        except Exception:
            return

        try:
            ts_str = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            is_ascii = not bool(self.hex_format_var.get())
            if is_ascii:
                text = data.decode("utf-8", errors="replace")
                lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
                total = 0
                for line in lines:
                    if line.strip():
                        full = f"[{ts_str}] {prefix}{line}\n"
                        self.save_raw_file.write(full)
                        total += len(full)
                self.save_raw_file.flush()
                self.save_raw_current_size += total
            else:
                hex_str = " ".join(f"{b:02X}" for b in data)
                line = f"[{ts_str}] {prefix}{hex_str}\n"
                self.save_raw_file.write(line)
                self.save_raw_file.flush()
                self.save_raw_current_size += len(line)

            if self.save_raw_current_size >= self.save_raw_max_size:
                self.save_raw_count += 1
                self._open_save_raw_file()
        except Exception as e:
            if self._save_raw_active and self.save_raw_file:
                try:
                    self._report_error("保存错误", e)
                    self.save_raw_enabled_var.set(False)
                except Exception:
                    pass
                self._close_save_raw_file()

    def _stop_serial(self) -> None:
        """停止串口监控。"""
        # 先停周期发送（先取消 Tk after 任务，再写标志）
        try:
            if self._tx_cycle_job is not None:
                try:
                    self.root.after_cancel(self._tx_cycle_job)
                except Exception:
                    pass
                self._tx_cycle_job = None
        except Exception:
            pass
        self.tx_cycle_var.set(False)
        try:
            if self.tx_cycle_btn:
                self.tx_cycle_btn.configure(text="▶ 开始循环", state="normal")
        except Exception:
            pass
        try:
            if self.collector:
                self.collector.stop()
                self.collector = None
        except Exception as e:  # noqa: BLE001  stop 阶段不能抛
            _log_error_to_disk(e)
            self.collector = None
        self.is_collecting = False
        try:
            self._close_save_raw_file()
        except Exception:
            pass
        self.save_raw_count = 0
        try:
            self.start_btn.configure(text="○  开始监控", style="Primary.TButton")
        except Exception:
            self.start_btn.configure(text="开始监控")
        self._set_status("已停止")

    # ---------- UI 队列处理 ----------

    def _process_ui_queue(self) -> None:
        """处理 UI 队列。最外层统一兜底，不允许堆栈冒泡到 Tk mainloop。"""
        try:
            while self._ui_queue:
                kind, args = self._ui_queue.pop(0)
                try:
                    if kind == "serial_frame":
                        self.rx_frame_count += 1
                        self._display_serial_frame(*args)
                    elif kind == "serial_raw":
                        self._display_raw_data(*args)
                    elif kind == "serial_tx":
                        self._display_serial_tx(*args)
                    elif kind == "serial_error":
                        self._display_serial_error(*args)
                except Exception as e:  # noqa: BLE001
                    # 单条消息失败不影响其他消息，只弹友好提示 + 日志
                    self._report_error(f"UI 处理失败（{kind}）", e)
        except Exception as e:  # noqa: BLE001  顶层：死循环绝对不能崩
            try:
                _log_error_to_disk(e)
            except Exception:
                pass
        finally:
            self.root.after(100, self._process_ui_queue)

    def _format_raw_display_serial(self, raw_hex: str) -> str:
        """串口原始数据显示格式转换。"""
        is_ascii = not bool(self.hex_format_var.get())
        if is_ascii:
            try:
                raw_bytes = bytes.fromhex(raw_hex.replace(" ", ""))
                return "".join(chr(b) if 32 <= b < 127 else "." for b in raw_bytes)
            except (ValueError, UnicodeDecodeError):
                return raw_hex
        return raw_hex

    def _display_serial_frame(self, result: ParseResult, ts: float) -> None:
        """显示串口解析结果。"""
        self.serial_text.configure(state="normal")
        self._trim_display()
        ts_str = datetime.fromtimestamp(ts).strftime("%H:%M:%S.%f")[:-3]
        ok_tag = "ok" if (result.error is None and result.checksum_ok is not False) else "err"
        cs = "✓" if result.checksum_ok else "✗" if result.checksum_ok is False else " "
        status = "OK" if result.error is None else "ERR"

        sender_label = self._get_sender_label(self.serial_sender_var.get())
        raw_display = self._format_raw_display_serial(result.raw_hex)

        if self.detail_var.get():
            self.serial_text.insert("end", f"[{ts_str}] ", "ts")
            self.serial_text.insert("end", f"{status} {cs} {result.cmd_code}  {result.cmd_name}", "cmd")
            if sender_label:
                self.serial_text.insert("end", f"  [{sender_label}]")
            elif result.direction:
                self.serial_text.insert("end", f"  [{result.direction}]")
            self.serial_text.insert("end", f"\n  原始: {raw_display}\n", "raw")
            if result.error:
                self.serial_text.insert("end", f"  错误: {result.error}\n", "err")
            for f in result.fields:
                ftype = f.get("type", "")
                fname = f.get("name", "")
                ftext = f.get("text", "")
                if ftype == "separator":
                    self.serial_text.insert("end", f"  {fname}\n", "cmd")
                elif ftype in ("header", "version", "cmd", "length", "checksum"):
                    self.serial_text.insert("end", f"  · {fname:<22} {ftext}\n", "field")
                else:
                    self.serial_text.insert("end", f"  · {fname:<22} {ftext}\n", "field")
                    children = f.get("children", [])
                    if children and isinstance(children, list):
                        for child in children:
                            if not child.get("__inner_field__"):
                                continue
                            cname = child.get("name", "")
                            ctext = child.get("text", "")
                            if cname and ctext:
                                self.serial_text.insert("end", f"    └ {cname:<20} {ctext}\n", "field")
            self.serial_text.insert("end", "\n")
        else:
            self.serial_text.insert("end", f"[{ts_str}] ", "ts")
            self.serial_text.insert("end", f"{status} {cs} {result.cmd_code:<6} ", ok_tag)
            self.serial_text.insert("end", f"{result.cmd_name}")
            if sender_label:
                self.serial_text.insert("end", f" [{sender_label}]")
            elif result.direction:
                self.serial_text.insert("end", f" [{result.direction}]")

            # 提取 PID / MODEL 追加到标题后
            extra_title = []
            in_data_section_extract = False
            for f in result.fields:
                ftype = f.get("type", "")
                fname = f.get("name", "")
                if ftype == "separator":
                    in_data_section_extract = True
                    continue
                if in_data_section_extract and ftype not in ("header", "version", "cmd", "length", "checksum"):
                    if fname == "设备PID":
                        v = f.get("value")
                        if isinstance(v, int):
                            extra_title.append(f"PID:{v}")
                    elif fname == "产品Model":
                        v = f.get("value")
                        if isinstance(v, str) and v:
                            extra_title.append(f"MODEL:{v}")
            if extra_title:
                self.serial_text.insert("end", "  " + " ".join(extra_title), "cmd")

            data_fields = []
            in_data_section = False
            for f in result.fields:
                ftype = f.get("type", "")
                fname = f.get("name", "")
                ftext = f.get("text", "")
                if ftype == "separator":
                    in_data_section = True
                    continue
                if in_data_section and ftype not in ("header", "version", "cmd", "length", "checksum"):
                    # 标题上已经显示的不再出现在属性列表
                    if fname in ("设备PID", "产品Model"):
                        continue
                    # 未映射真实属性名的占位（attrid_0x...）直接整段跳过，不显示
                    if isinstance(fname, str) and fname.startswith("attrid_"):
                        continue
                    if not ftext:
                        continue
                    children = f.get("children", [])
                    inner_fields = [c for c in children if c.get("__inner_field__")] if children else []
                    if inner_fields:
                        for inner in inner_fields:
                            iname = inner.get("name", "")
                            # 内层同样过滤 attrid_ 开头的未知属性
                            if isinstance(iname, str) and iname.startswith("attrid_"):
                                continue
                            itext = inner.get("text", "")
                            ichildren = inner.get("children", [])
                            iraw = inner.get("raw", "")
                            ival_text = itext.replace("[强制上报] ", "")
                            if iraw and len(iraw) >= 4:
                                ibytes = iraw.replace(" ", "")
                                ival_hex = ibytes[4:] if len(ibytes) >= 4 else ""
                                if ival_hex:
                                    data_fields.append(f"{iname}{ival_text} ({ival_hex})")
                                else:
                                    data_fields.append(f"{iname}{ival_text}")
                            elif ichildren and isinstance(ichildren, list) and ichildren[0].get("attrid"):
                                data_fields.append(f"{iname}{ival_text}")
                            else:
                                data_fields.append(f"{iname}={itext}")
                    elif children and isinstance(children, list) and children[0].get("attrid"):
                        raw_hex = f.get("raw", "")
                        val_text = ftext.replace("[强制上报] ", "")
                        if raw_hex:
                            raw_bytes = raw_hex.replace(" ", "")
                            if len(raw_bytes) >= 4:
                                value_hex = raw_bytes[4:]
                                data_fields.append(f"{fname}{val_text} ({value_hex})")
                            else:
                                data_fields.append(f"{fname}{val_text}")
                        else:
                            data_fields.append(f"{fname}{val_text}")
                    else:
                        data_fields.append(f"{fname}={ftext}")
            if data_fields:
                self.serial_text.insert("end", f"  {{ {'; '.join(data_fields)} }}", "field")
            self.serial_text.insert("end", f"  | {raw_display}\n", "raw")

        if self.autoscroll_var.get():
            self.serial_text.see("end")
        self.serial_text.configure(state="disabled")

        if self.log_file:
            self._write_log(result, ts, direction="RX")

        if self.collector and self.collector.sync:
            self.stats_var.set(
                f"RX {self.rx_frame_count}  TX {self.tx_frame_count}  错误 {self.collector.sync.error_count}  缓冲 {self.collector.sync.partial_bytes}B"
            )

    def _display_serial_tx(self, data_sent: bytes, ts: float) -> None:
        """显示 TX 发送出去的数据。协议帧：
        1. 如果当前为 HEX 协议模式：尝试用当前 cfg 反向解析成 ParseResult（方向强制为 TX），
           显示格式与 RX 相同 + 统一用 [TX] 标记
        2. 否则（ASCII / Raw 模式：直接显示 [TX]+HEX/ASCII
        """
        self.serial_text.configure(state="normal")
        self._trim_display()
        ts_str = datetime.fromtimestamp(ts).strftime("%H:%M:%S.%f")[:-3]

        parsed_ok = False
        is_ascii_mode = not bool(self.hex_format_var.get())
        mode = self.send_mode_var.get()
        if not is_ascii_mode and self.cfg and mode == "protocol":
            try:
                parsed = parse_frame(data_sent, self.cfg, direction="TX")
                if parsed and parsed.cmd_code is not None:
                    # 用"和 RX 相同"的展示格式，只把方向标记改成 [TX] 发送]
                    raw_display = self._format_raw_display_serial(parsed.raw_hex)
                    self.serial_text.insert("end", f"[{ts_str}] ", "ts")
                    self.serial_text.insert("end", "[TX]", "tx")
                    self.serial_text.insert("end", " ", "ts")
                    self.serial_text.insert("end", f"{parsed.cmd_code:<6} ", "cmd")
                    self.serial_text.insert("end", f"{parsed.cmd_name}")
                    if parsed.error:
                        self.serial_text.insert("end", f"  (解析失败: {parsed.error}", "err")
                    data_fields = []
                    in_data_section = False
                    for f in parsed.fields:
                        ftype = f.get("type", "")
                        fname = f.get("name", "")
                        ftext = f.get("text", "")
                        if ftype == "separator":
                            in_data_section = True
                            continue
                        if in_data_section and ftype not in ("header", "version", "cmd", "length", "checksum"):
                            if isinstance(fname, str) and fname.startswith("attrid_"):
                                continue
                            if not ftext:
                                continue
                            data_fields.append(f"{fname}={ftext}")
                    if data_fields:
                        self.serial_text.insert("end", f"  {{ {'; '.join(data_fields)} }}", "field")
                    self.serial_text.insert("end", f"  | {raw_display}\n", "raw")
                    if self.log_file:
                        self._write_log(parsed, ts, direction="TX")
                    parsed_ok = True
            except Exception:
                parsed_ok = False
        if not parsed_ok:
            try:
                hex_str = " ".join(f"{b:02X}" for b in data_sent)
            except Exception:
                hex_str = ""
            self.serial_text.insert("end", f"[{ts_str}] ", "ts")
            self.serial_text.insert("end", "[TX]", "tx")
            self.serial_text.insert("end", " ", "ts")
            if is_ascii_mode and self.send_mode_var.get() == "raw_ascii":
                try:
                    ascii_str = "".join(chr(b) if 32 <= b < 127 else "." for b in data_sent)
                except Exception:
                    ascii_str = hex_str
                self.serial_text.insert("end", f"Raw-ASCII | {ascii_str}\n", "raw")
            else:
                self.serial_text.insert("end", f"Raw-HEX    | {hex_str}\n", "raw")
            if self.log_file:
                try:
                    t = datetime.fromtimestamp(ts).isoformat(timespec="milliseconds") if ts else datetime.now().isoformat(timespec="milliseconds")
                    self.log_file.write(f"[{t}] TX raw: {hex_str}\n")
                    self.log_file.flush()
                except Exception:
                    pass

        if self.autoscroll_var.get():
            self.serial_text.see("end")
        self.serial_text.configure(state="disabled")
        if self.collector and self.collector.sync:
            self.stats_var.set(
                f"RX {self.rx_frame_count}  TX {self.tx_frame_count}  错误 {self.collector.sync.error_count}  缓冲 {self.collector.sync.partial_bytes}B"
            )

    def _display_serial_error(self, msg: str) -> None:
        """显示串口错误。"""
        self.serial_text.configure(state="normal")
        self.serial_text.insert("end", f"[错误] {msg}\n", "err")
        self.serial_text.see("end")
        self.serial_text.configure(state="disabled")
        self._stop_serial()

    def _display_raw_data(self, data: bytes, ts: float) -> None:
        """显示 ASCII 原始数据。"""
        self.serial_text.configure(state="normal")
        self._trim_display()
        ts_str = datetime.fromtimestamp(ts).strftime("%H:%M:%S.%f")[:-3]
        text = data.decode("utf-8", errors="replace")
        lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        for line in lines:
            if line == "":
                continue
            printable = "".join(ch if (32 <= ord(ch) < 127 or ch in ("\t",)) else "." for ch in line)
            self.serial_text.insert("end", f"[{ts_str}] ", "ts")
            self.serial_text.insert("end", f"{printable}\n", "field")
        if self.autoscroll_var.get():
            self.serial_text.see("end")
        self.serial_text.configure(state="disabled")

    def _trim_display(self) -> None:
        """清理显示缓冲区，防止内存溢出。"""
        line_count = int(self.serial_text.index("end-1c").split(".")[0])
        if line_count > self.max_display_lines:
            delete_lines = line_count - self.max_display_lines
            self.serial_text.delete(f"1.0", f"{delete_lines}.end")

    # ---------- 日志 ----------

    def _choose_log(self) -> None:
        """选择日志文件。"""
        if self.log_file:
            if messagebox.askyesno("日志", "已开启日志记录，要关闭吗？"):
                self.log_file.close()
                self.log_file = None
                self.log_path = None
                self._set_status("日志已关闭")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".log",
            filetypes=[("日志文件", "*.log"), ("文本文件", "*.txt"), ("所有文件", "*.*")],
            initialfile=f"protocol_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log",
        )
        if not path:
            return
        try:
            self.log_path = Path(path)
            self.log_file = self.log_path.open("a", encoding="utf-8")
            self.log_file.write(f"\n===== 开始记录 {datetime.now().isoformat(timespec='seconds')} =====\n")
            self.log_file.flush()
            self._set_status(f"日志已开启: {path}")
        except Exception as e:
            messagebox.showerror("日志文件错误", str(e))

    def _write_log(self, result: ParseResult, ts: float | None = None, direction: str = "RX") -> None:
        """写入日志。"""
        if not self.log_file:
            return
        ts = ts or time.time()
        ts_str = datetime.fromtimestamp(ts).strftime("%H:%M:%S.%f")[:-3]
        ok_tag = "OK" if (result.error is None and result.checksum_ok is not False) else "ERR"
        cs = "✓" if result.checksum_ok else "✗" if result.checksum_ok is False else " "
        self.log_file.write(f"[{ts_str}] [{direction}] {ok_tag} {cs} {result.cmd_code} {result.cmd_name}")
        sender_label = ""
        if bool(self.hex_format_var.get()) and direction != "TX":
            sender_label = self._get_sender_label(self.serial_sender_var.get())
        if sender_label:
            self.log_file.write(f" [{sender_label}]")
        elif result.direction:
            self.log_file.write(f" [{result.direction}]")
        self.log_file.write(f" | {result.raw_hex}\n")
        if result.error:
            self.log_file.write(f"  错误: {result.error}\n")
        for f in result.fields:
            self.log_file.write(f"  · {f.get('name', ''):<24} {f.get('text', '')}\n")
        self.log_file.flush()
        self.log_count += 1

    # ---------- 工具 ----------

    def _get_sender_label(self, sender: str) -> str:
        """获取发送方标签。"""
        if sender == "模组发送":
            return "模组→MCU"
        elif sender == "MCU发送":
            return "MCU→模组"
        return ""

    def _set_status(self, msg: str) -> None:
        """设置状态栏。"""
        self.status_var.set(msg)
        self.root.update_idletasks()

    # ---------- 更新前：安全停止 + flush 磁盘 + 保存会话快照 ----------

    def _prepare_session_snapshot_for_update(self) -> SessionSnapshot:
        """点击"安装更新"时，先调用本函数：

        1) 若串口正在接收 → 安全停止（join 线程 + close 句柄，保证串口/日志不再被占用）
        2) flush 缓冲区中还没写入磁盘的日志 / 原始数据 / 强制 fsync
        3) 构造 SessionSnapshot，包含：
           - was_collecting（告诉新程序要自动重新开始）
           - 串口号、波特率、帧格式
           - 协议产品名 + 来源（__builtin_v3__ 或绝对路径）
           - 数据格式（HEX/ASCII）、方向、详细模式
           - 正在写的日志路径 + 原始数据保存路径/文件名
        4) save_snapshot() 写盘；返回对象，prepare_update_and_quit 会校验文件存在。
        """
        was_collecting = bool(self.collector and self.collector.running)

        # 1) 停止串口 + flush 回调队列（这样 on_frame/on_raw/on_error 不再写 log/save_raw）
        if self.collector is not None:
            try:
                self.collector.stop()
            except Exception:
                pass
            self.collector = None

        # 2) flush log & 原始数据，保证 bat 替换/重启时文件句柄释放干净
        if self.log_file is not None:
            try:
                try:
                    self.log_file.flush()
                except Exception:
                    pass
                try:
                    import os as _os
                    _os.fsync(self.log_file.fileno())
                except Exception:
                    pass
                self.log_file.close()
            except Exception:
                pass
            # 不把 log_file = None 掉；重启后新程序会按快照 log_path 重新 open。
            # 为避免 on_close 再双 close，这里置 None。
            self.log_file = None

        if self.save_raw_file is not None:
            try:
                try:
                    self.save_raw_file.flush()
                except Exception:
                    pass
                try:
                    import os as _os
                    _os.fsync(self.save_raw_file.fileno())
                except Exception:
                    pass
                self.save_raw_file.close()
            except Exception:
                pass
            self.save_raw_file = None
            self._save_raw_active = False

        # 3) 构造快照
        port = ""
        try:
            port = self.port_var.get().strip()
        except Exception:
            port = ""
        baudrate = 115200
        try:
            baudrate = int(self.baudrate_var.get().strip() or 115200)
        except Exception:
            baudrate = 115200
        bytesize = 8
        stopbits = 1.0
        try:
            bs = int(self.bytesize_var.get().strip() or 8)
            bytesize = bs if bs in (5, 6, 7, 8) else 8
        except Exception:
            bytesize = 8
        try:
            sb_raw = self.stopbits_var.get().strip()
            stopbits = 1.5 if sb_raw == "1.5" else 2.0 if sb_raw == "2" else 1.0
        except Exception:
            stopbits = 1.0

        product_name = ""
        product_source = ""
        try:
            product_name = self.product_var.get().strip()
            product_source = self._product_sources.get(product_name, "") if hasattr(self, "_product_sources") else ""
        except Exception:
            product_name = ""
            product_source = ""

        try:
            is_hex_format = bool(self.hex_format_var.get())
        except Exception:
            is_hex_format = True
        direction = ""
        try:
            d = self.direction_var.get().strip()
            direction = d
        except Exception:
            direction = ""
        detail_mode = False
        try:
            detail_mode = bool(self.detail_var.get())
        except Exception:
            detail_mode = False

        log_path_str = str(self.log_path) if isinstance(self.log_path, Path) else (self.log_path or "")  # type: ignore[redundant-cast]
        log_path_str = str(log_path_str) if log_path_str else ""
        save_raw_enabled = bool(self.save_raw_enabled_var.get()) if getattr(self, "save_raw_enabled_var", None) else False
        save_raw_path = str(self.save_raw_path_var.get()) if getattr(self, "save_raw_path_var", None) else ""
        save_raw_filename = str(self.save_raw_filename_var.get()) if getattr(self, "save_raw_filename_var", None) else ""

        send_mode = "protocol"
        tx_cmd_code = ""
        tx_direction = ""
        tx_fields_json = ""
        tx_raw = ""
        tx_cycle_enabled = False
        tx_interval_ms = 1000
        try:
            send_mode = self.send_mode_var.get()
            tx_cmd_code = self.tx_cmd_code_var.get()
            tx_direction = self.tx_direction_var.get()
            tx_fields_json = self.tx_fields_var.get()
            tx_raw = self.tx_raw_var.get()
            tx_cycle_enabled = bool(self.tx_cycle_var.get())
            tx_interval_ms = int(self.tx_interval_ms_var.get() or 1000)
        except Exception:
            pass

        snap = SessionSnapshot(
            was_collecting=was_collecting,
            port=port,
            baudrate=baudrate,
            bytesize=bytesize,
            stopbits=stopbits,
            product_name=product_name,
            product_source=product_source,
            is_hex_format=is_hex_format,
            direction=direction,
            detail_mode=detail_mode,
            log_path=log_path_str,
            save_raw_enabled=save_raw_enabled,
            save_raw_path=save_raw_path,
            save_raw_filename=save_raw_filename,
            tx_send_mode=send_mode,
            tx_cmd_code=tx_cmd_code,
            tx_direction=tx_direction,
            tx_fields_json=tx_fields_json,
            tx_raw=tx_raw,
            tx_cycle_enabled=tx_cycle_enabled,
            tx_interval_ms=tx_interval_ms,
        )
        save_snapshot(snap)
        return snap

    def on_close(self) -> None:
        """关闭主窗口。

        因为「添加串口」改为 subprocess 启动独立程序实例（不再使用 Toplevel），所以关主窗口时直接关自己退出即可。
        """
        try:
            if self.is_collecting:
                try:
                    self._stop_serial()
                except Exception as e:
                    _log_error_to_disk(e)
            self._close_save_raw_file()
            if self.log_file:
                try:
                    self.log_file.write(f"===== 结束记录（共 {self.log_count} 条） =====\n")
                    self.log_file.close()
                except Exception as e:
                    _log_error_to_disk(e)
                self.log_file = None
        finally:
            try:
                self.root.destroy()
            except Exception:
                pass


# ---------- 启动 ----------

def main():
    """主入口。最外层统一兜底，绝不把堆栈直接抛给终端用户。"""
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--monitor", nargs=2, metavar=("PORT", "BAUD"), default=None,
                    help="启动后自动选中指定串口与波特率，例如 --monitor COM40 9600（BAUD支持自定义如 6000000=6M）")
    args, _unknown = ap.parse_known_args()

    monitor_port = None
    monitor_baud = 115200
    if args.monitor is not None:
        monitor_port = args.monitor[0]
        try:
            monitor_baud = int(args.monitor[1])
        except Exception:
            monitor_baud = 115200

    try:
        root = tk.Tk()
    except Exception as e:
        # 极端情况：DISPLAY / Tk 初始化失败
        friendly, _ = classify_protocol_error(e)
        log_path = _log_error_to_disk(e)
        print(f"[错误] 无法启动 GUI：{friendly}", file=sys.stderr)
        if log_path is not None:
            print(f"       详细日志已写入: {log_path}", file=sys.stderr)
        return 1

    app: ProtocolParserApp | None = None
    try:
        app = ProtocolParserApp(root, monitor_port=monitor_port, monitor_baud=monitor_baud)
        root.protocol("WM_DELETE_WINDOW", app.on_close)

        # 给 Tk mainloop 再套一层异常兜底，防止第三方回调/事件把堆栈甩到 stderr
        def _safe_mainloop():
            while True:
                try:
                    root.mainloop()
                    break
                except Exception as e:  # noqa: BLE001
                    if app is not None:
                        try:
                            app._report_error("运行时错误", e)
                            continue
                        except Exception:
                            pass
                    # 无法弹窗时至少写日志
                    try:
                        _log_error_to_disk(e)
                    except Exception:
                        pass
                    friendly, _ = classify_protocol_error(e)
                    try:
                        print(f"[运行时错误] {friendly}", file=sys.stderr)
                    except Exception:
                        pass
                    break

        _safe_mainloop()
        return 0
    except Exception as e:  # noqa: BLE001  启动阶段兜底
        try:
            friendly, _ = classify_protocol_error(e)
            log_path = _log_error_to_disk(e)
            try:
                messagebox.showerror("启动失败", f"{friendly}\n\n日志: {log_path}")
            except Exception:
                print(f"[启动失败] {friendly}", file=sys.stderr)
                if log_path is not None:
                    print(f"           详细日志已写入: {log_path}", file=sys.stderr)
        finally:
            try:
                root.destroy()
            except Exception:
                pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
