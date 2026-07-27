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
from collections import deque
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


# ---------- 资源/数据路径（兼容 PyInstaller 单文件模式） ----------

def resource_path(relative: str) -> Path:
    """获取资源路径（只读/内置资源）：优先 _MEIPASS 打包目录。
    只用于 product/ 协议文件等打包进来的资源，**绝不要用来写文件**（_MEIPASS 是临时目录，重启就清空）。"""
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / relative
    base = Path(__file__).resolve().parent
    # 开发模式下，product 在上一级目录
    candidate = base / relative
    if candidate.exists():
        return candidate
    return base.parent / relative


def user_data_path(relative: str = "") -> Path:
    """获取**用户可写**数据目录：
    * 打包模式：优先 exe 同级的 data\\；exe 写目录不可用（如 Program Files）时降级到用户「文档\\串口解析工具\\data」。
    * 开发模式：项目根目录下的 data\\。
    相对路径 relative 会拼在后面并自动创建目录。"""
    try:
        # 1) 打包模式：sys.executable = xxx.exe
        if getattr(sys, "frozen", False) and hasattr(sys, "executable"):
            exe_dir = Path(sys.executable).resolve().parent
            try:
                write_probe = exe_dir / ".write_probe"
                write_probe.write_text("probe", encoding="utf-8")
                write_probe.unlink(missing_ok=True)
                root = exe_dir
            except (OSError, PermissionError):
                # 无权限写 exe 目录（如 C:\\Program Files\\）→ 降级：我的文档\串口解析工具
                doc_dir = Path.home() / "Documents"
                if not doc_dir.exists():
                    doc_dir = Path.home()
                root = doc_dir / "串口解析工具"
            data_dir = root / "data"
        else:
            # 2) 开发模式：项目根 data
            project_root = Path(__file__).resolve().parent.parent
            data_dir = project_root / "data"
        # 拼 relative 后缀
        if relative:
            data_dir = data_dir / relative
        data_dir.mkdir(parents=True, exist_ok=True)
        return data_dir
    except Exception:
        # 终极兜底：%USERPROFILE%\\串口解析工具\\data
        fb = Path.home() / "串口解析工具" / "data"
        if relative:
            fb = fb / relative
        fb.mkdir(parents=True, exist_ok=True)
        return fb


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


# ---------- 崩溃日志（启动期闪退辅助）：写 exe 同级目录 crash_*.log ----------


def _crash_log_dir() -> Path:
    """崩溃日志落盘目录：
    * 打包 EXE → exe 同级目录；
    * 开发模式 → 项目根目录（protocol_parser 父级）。
    """
    try:
        if getattr(sys, "frozen", False):
            return Path(sys.executable).resolve().parent
        return Path(__file__).resolve().parent.parent
    except Exception:
        return Path.cwd()


def _write_crash_log_gui(exc: BaseException) -> Path | None:
    """启动期 / 运行期崩溃 → 写 exe 同级目录 crash_时间戳.log，
    即便 mainloop 和 messagebox 都起不来，用户也能在 exe 旁边找到堆栈。
    """
    import traceback as _tb

    try:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = _crash_log_dir() / f"crash_{ts}.log"
        tb_s = _tb.format_exc()
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"Time:       {datetime.now().isoformat(timespec='seconds')}\n")
            f.write(f"Frozen:     {getattr(sys, 'frozen', False)}\n")
            f.write(f"Executable: {sys.executable}\n")
            f.write(f"MEIPASS:    {getattr(sys, '_MEIPASS', '')}\n")
            f.write(f"CWD:        {os.getcwd()}\n")
            f.write(f"Argv:       {sys.argv}\n")
            f.write("\n========== Exception ==========\n")
            f.write(f"{type(exc).__module__}.{type(exc).__name__}: {exc}\n")
            f.write("\n========== Traceback ==========\n")
            f.write(tb_s)
            f.write("\n========== sys.path ==========\n")
            for p in sys.path:
                f.write(p + "\n")
        return path
    except Exception:
        return None


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
            "app_bg":          "#F9FAFB",  # 应用整体背景（比卡片灰更明显，让"留白分隔"直接有层差）
            "card_bg":         "#F3F4F6",  # LabelFrame / 卡片背景（纯白 + 一圈细边框 = 独立卡片）
            "card_border":     "#E0E0E0",  # 卡片边框色（比通用 border 稍深，每张卡片有"一层"的视觉）
            "surface":         "#F3F4F6",  # Entry / Combobox / Text 背景
            "border":          "#E0E0E0",  # 控件边框色
            "primary":         "#0078D4",  # Win11 主色（蓝）
            "primary_hover":   "#106EBE",  # 主色 hover
            "success":         "#0F7B0F",  # 接收成功 / OK
            "error":           "#C42B1C",  # 错误 / 异常红
            "warn":            "#BC6A00",  # 告警 / 方向橙
            "tx":              "#0A7A5A",  # [TX] 发送绿
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
            "tooltip_bg":      "#1F2937",  # Tooltip 气泡背景
            "tooltip_fg":      "#F9FAFB",  # Tooltip 文字
        },
        "dark": {
            "app_bg":          "#141517",  # App 背景（深色差）
            "card_bg":         "#23252A",  # LabelFrame / 卡片（比 app_bg 更亮一点）
            "card_border":     "#383A41",  # 卡片边框（亮于背景，轮廓清晰）
            "surface":         "#2E3035",  # Entry / Combobox / Text 背景
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
            "tooltip_bg":      "#E6E8EB",  # Tooltip 气泡背景
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

        # ttk 根样式：Label/Button/Frame/LabelFrame/Entry/Combobox/Radiobutton/Checkbutton/Notebook
        ttk_style.configure(".", background=app_bg, foreground=text, fieldbackground=surface, bordercolor=border, lightcolor=border, darkcolor=border)

        # Frame / LabelFrame：卡片 = 细边框 + 卡片区与留白(app_bg)分层
        ttk_style.configure("TFrame", background=app_bg)
        ttk_style.configure("Card.TFrame", background=card_bg, relief="flat")
        # 真正的"卡片"LabelFrame：带一圈可见边框，保证每个卡片独立一层（绝不与 app_bg 或其它卡片糊成同图层）
        ttk_style.configure("TLabelframe",
                            background=card_bg,
                            bordercolor=card_border,
                            relief="solid",
                            borderwidth=1)
        ttk_style.configure("TLabelframe.Label",
                            background=app_bg,        # 标签文字在"卡片上沿外"用 app_bg（视觉更层叠）；如果要更"融入卡片"改成 card_bg 也可
                            foreground=text_2,
                            font=("Microsoft YaHei UI", 10, "bold"))
        ttk_style.configure("TLabel", background=app_bg, foreground=text)
        ttk_style.configure("Card.TLabel", background=card_bg, foreground=text)
        ttk_style.configure("Hint.TLabel", background=card_bg, foreground=text_2)
        ttk_style.configure("Title.TLabel", background=card_bg, foreground=text, font=("Microsoft YaHei UI", 10, "bold"))
        # 状态栏：同样卡片边框 + 和 app_bg 分层（底部一个独立横条卡片）
        ttk_style.configure("StatusBar.TFrame",
                            background=card_bg,
                            bordercolor=card_border,
                            relief="solid",
                            borderwidth=1)
        ttk_style.configure("StatusBar.TLabel", background=card_bg, foreground=text_2, font=("Microsoft YaHei UI", 10))

        # Button：主按钮 + 普通按钮（紧凑尺寸）
        ttk_style.configure("TButton", padding=(6, 2), relief="flat", background=surface, foreground=text, bordercolor=border, focusthickness=1, font=("Microsoft YaHei UI", 10))
        ttk_style.map("TButton",
                      background=[("active", palette["primary"] if self.style == "win11" else surface),
                                  ("pressed", primary_hover),
                                  ("disabled", app_bg)],
                      foreground=[("active", "#FFFFFF" if self.style == "win11" else text),
                                  ("disabled", text_dis)])
        ttk_style.configure("Primary.TButton", padding=(8, 3), relief="flat", background=primary, foreground="#FFFFFF", font=("Microsoft YaHei UI", 10, "bold"), borderwidth=0)
        ttk_style.map("Primary.TButton",
                      background=[("active", primary_hover), ("pressed", primary_hover), ("disabled", border)],
                      foreground=[("disabled", text_dis)])
        ttk_style.configure("Danger.TButton", padding=(8, 3), relief="flat", background=palette["error"], foreground="#FFFFFF", font=("Microsoft YaHei UI", 10, "bold"), borderwidth=0)
        ttk_style.map("Danger.TButton",
                      background=[("active", "#A5211C"), ("pressed", "#A5211C"), ("disabled", border)])
        # 紧凑型切换按钮（置顶、保存原始数据等）
        ttk_style.configure("CompactPrimary.TButton", padding=(6, 2), relief="flat", background=primary, foreground="#FFFFFF", font=("Microsoft YaHei UI", 10, "bold"), borderwidth=0)
        ttk_style.map("CompactPrimary.TButton",
                      background=[("active", primary_hover), ("pressed", primary_hover), ("disabled", border)],
                      foreground=[("disabled", text_dis)])
        ttk_style.configure("CompactDanger.TButton", padding=(6, 2), relief="flat", background=palette["error"], foreground="#FFFFFF", font=("Microsoft YaHei UI", 10, "bold"), borderwidth=0)
        ttk_style.map("CompactDanger.TButton",
                      background=[("active", "#A5211C"), ("pressed", "#A5211C"), ("disabled", border)])

        # Entry / Combobox / Spinbox
        for s in ("TEntry", "TSpinbox", "TCombobox"):
            ttk_style.configure(s, fieldbackground=surface, foreground=text, bordercolor=border, lightcolor=border, darkcolor=border, arrowsize=14)
            ttk_style.map(s,
                          fieldbackground=[("readonly", card_bg), ("disabled", app_bg)],
                          foreground=[("readonly", text), ("disabled", text_dis)],
                          bordercolor=[("focus", primary), ("readonly", border)])

        # Radiobutton / Checkbutton（修复 clam 主题下选中指示器显示为 × 的问题）
        # 方案：增大指示器直径并配置状态颜色，使勾选标记更清晰可见
        for _cb_style in ("TRadiobutton", "TCheckbutton"):
            ttk_style.configure(_cb_style, background=card_bg, foreground=text,
                                focuscolor=primary, indicatordiameter=13,
                                indicatorcolor=surface, indicatorforeground=text)
            ttk_style.map(_cb_style,
                          background=[("active", card_bg)],
                          indicatorcolor=[("selected", primary), ("pressed", primary_hover), ("active", surface)],
                          indicatorforeground=[("selected", "#000000"), ("pressed", "#000000")])
        # 顶栏专用（背景跟随 app_bg 而不是 card_bg）
        ttk_style.configure("Toolbar.TCheckbutton", background=app_bg, foreground=text)
        ttk_style.configure("Toolbar.TRadiobutton", background=app_bg, foreground=text)
        ttk_style.configure("Toolbar.TLabel", background=app_bg, foreground=text)
        ttk_style.configure("Toolbar.TButton", padding=(4, 2), font=("Microsoft YaHei UI", 10))

        # PanedWindow：左右分栏之间的"分隔条"加宽，让左右大卡片之间更有层差感
        try:
            ttk_style.configure("TPanedwindow", background=app_bg, sashwidth=8, sashrelief="flat")
        except tk.TclError:
            # 某些主题或老版本 ttk 不接受 TPanedwindow 上的配置，忽略即可
            ttk_style.configure("TPanedwindow", background=app_bg)

        # Notebook
        ttk_style.configure("TNotebook", background=app_bg, borderwidth=0)
        ttk_style.configure("TNotebook.Tab", padding=(14, 6), background=app_bg, foreground=text_2, font=("Microsoft YaHei UI", 10))
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

def _enable_full_combobox(combo: ttk.Combobox) -> None:
    """合上时悬停显示当前完整文本；展开后列表项悬停也显示完整行；列表按最长项加宽。"""
    tip_win: list[tk.Toplevel | None] = [None]
    after_id: list[str | None] = [None]

    def _hide_tip() -> None:
        if after_id[0] is not None:
            try:
                combo.after_cancel(after_id[0])
            except Exception:
                pass
            after_id[0] = None
        w = tip_win[0]
        if w is not None:
            try:
                w.destroy()
            except Exception:
                pass
            tip_win[0] = None

    def _show_tip(text: str, x: int, y: int) -> None:
        _hide_tip()
        text = (text or "").strip()
        if not text:
            return
        try:
            tw = tk.Toplevel(combo)
            tw.wm_overrideredirect(True)
            try:
                tw.wm_attributes("-topmost", True)
            except Exception:
                pass
            tk.Label(
                tw,
                text=text,
                bg="#1F2937",
                fg="#F9FAFB",
                font=("Microsoft YaHei UI", 9),
                padx=8,
                pady=4,
                justify="left",
            ).pack()
            tw.update_idletasks()
            tw.geometry(f"+{x + 12}+{y + 16}")
            tip_win[0] = tw
        except Exception:
            tip_win[0] = None

    def _schedule_closed_tip(event=None) -> None:
        _hide_tip()

        def _do() -> None:
            after_id[0] = None
            try:
                text = str(combo.get() or "")
            except Exception:
                text = ""
            if not text:
                return
            try:
                x = combo.winfo_rootx()
                y = combo.winfo_rooty() + combo.winfo_height()
            except Exception:
                return
            _show_tip(text, x, y)

        try:
            after_id[0] = combo.after(350, _do)
        except Exception:
            pass

    def _find_listbox():
        try:
            popdown = combo.tk.call("ttk::combobox::PopdownWindow", combo)
        except Exception:
            return None
        for path in (f"{popdown}.f.l", f"{popdown}.lb", f"{popdown}.f.lb"):
            try:
                return combo.nametowidget(path)
            except Exception:
                continue
        # 兜底：递归找 Listbox
        try:
            pd = combo.nametowidget(str(popdown))
            stack = [pd]
            while stack:
                w = stack.pop()
                if w.winfo_class() == "Listbox":
                    return w
                stack.extend(w.winfo_children())
        except Exception:
            pass
        return None

    def _widen_popdown(_event=None) -> None:
        def _do() -> None:
            try:
                values = combo.cget("values") or ()
                if not values:
                    return
                longest = max(len(str(v)) for v in values)
                try:
                    entry_w = int(float(str(combo.cget("width") or 0)))
                except Exception:
                    entry_w = 0
                w = max(longest + 2, entry_w, 12)
                lb = _find_listbox()
                if lb is None:
                    return
                try:
                    lb.configure(width=w)
                except Exception:
                    pass

                def _on_motion(event) -> None:
                    try:
                        idx = lb.nearest(event.y)
                        text = str(lb.get(idx))
                    except Exception:
                        text = ""
                    _show_tip(text, event.x_root, event.y_root)

                def _on_leave(_e=None) -> None:
                    _hide_tip()

                lb.bind("<Motion>", _on_motion, add="+")
                lb.bind("<Leave>", _on_leave, add="+")
                lb.bind("<ButtonPress>", _on_leave, add="+")
            except Exception:
                pass

        try:
            combo.after(80, _do)
        except Exception:
            pass

    combo.bind("<Enter>", _schedule_closed_tip, add="+")
    combo.bind("<Leave>", lambda _e: _hide_tip(), add="+")
    combo.bind("<ButtonPress>", lambda _e: _hide_tip(), add="+")
    combo.bind("<ButtonPress-1>", _widen_popdown, add="+")
    combo.bind("<Down>", _widen_popdown, add="+")
    combo.bind("<<ComboboxSelected>>", lambda _e: _hide_tip(), add="+")

class RoundedButton(tk.Canvas):
    """大圆角按钮，支持动态 configure(text=..., style=...)，兼容 Tooltip。"""

    # 样式名 → (正常背景, hover背景, 文字颜色)
    _STYLE_MAP: dict[str, tuple[str, str, str]] = {}

    @classmethod
    def init_styles(cls, theme: "ThemeManager") -> None:
        """根据主题色初始化样式映射。需在 ThemeManager.apply() 之后调用。"""
        primary = theme.get("primary")
        primary_hover = theme.get("primary_hover")
        error = theme.get("error")
        surface = theme.get("surface")
        text = theme.get("text")

        cls._STYLE_MAP = {
            "TButton":             (surface, primary,    text),
            "Toolbar.TButton":     (surface, primary,    text),
            "Primary.TButton":     (primary, primary_hover, "#FFFFFF"),
            "Danger.TButton":      (error,   "#A5211C",    "#FFFFFF"),
            "CompactPrimary.TButton": (primary, primary_hover, "#FFFFFF"),
            "CompactDanger.TButton":  (error,   "#A5211C",    "#FFFFFF"),
        }

    @classmethod
    def redraw_all(cls, root: tk.Misc) -> None:
        """遍历控件树，强制所有 RoundedButton 实例重绘。"""
        def _walk(w):
            if isinstance(w, cls):
                w._draw()
            for child in w.winfo_children():
                _walk(child)
        _walk(root)

    def __init__(self, parent, *, text: str = "", command=None,
                 style: str = "TButton", width: int | None = None,
                 font: tuple | str | None = None, state: str = "normal",
                 **kwargs):
        self._command = command
        self._style_name = style
        self._state = state
        self._hovered = False
        self._pressed = False
        self._text = text
        self._width_chars = width

        if font is None:
            font = ("Microsoft YaHei UI", 8)
        elif isinstance(font, str):
            font = (font, 8)
        self._font_tuple = font
        
        bg_normal, _, fg = self._colors()
        
        # 计算 Canvas 像素尺寸
        import tkinter.font as tkfont
        f = tkfont.Font(font=self._font_tuple)
        char_w = max(f.measure("0"), 5)
        text_w = f.measure(text) if text else 0
        # width 参数作为最小字符宽度，但始终保证能容纳实际文本
        base_w = max((width * char_w) if width else 0, text_w)
        pad_x = 14 if style.startswith("Compact") else 18
        pad_y = 4 if style.startswith("Compact") else 5
        self._pad_x = pad_x
        self._pad_y = pad_y
        cw = base_w + pad_x * 2
        ch = f.metrics("linespace") + pad_y * 2
        self._radius = min(ch // 2, 12)  # 圆角：高度的一半，最备12px

        # 获取父控件背景色（兼容 ttk/tk 控件）
        parent_bg = kwargs.get("bg")
        if parent_bg is None:
            try:
                parent_bg = parent.cget("background")
            except Exception:
                parent_bg = "#F0F0F0"

        super().__init__(
            parent, width=cw, height=ch,
            bg=parent_bg,
            highlightthickness=0, bd=0, relief="flat",
        )

        self._bg_id = self.create_rectangle(0, 0, cw, ch, fill=bg_normal, outline="", tags=("bg",))
        self._text_id = self.create_text(cw / 2, ch / 2, text=text, fill=fg,
                                         font=self._font_tuple, tags=("text",))

        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)

    # ---- 内部方法 ----

    def _colors(self) -> tuple[str, str, str]:
        return self._STYLE_MAP.get(self._style_name, ("#E0E0E0", "#C0C0C0", "#000000"))

    def _draw(self) -> None:
        bg_normal, bg_hover, fg = self._colors()
        if self._state == "disabled":
            bg = "#D0D0D0"; fg = "#999999"
        elif self._pressed or self._hovered:
            bg = bg_hover
        else:
            bg = bg_normal

        # 优先使用 Canvas 配置尺寸（winfo_width 在首次显示前返回 1，导致截断）
        w = int(self.cget("width"))
        h = int(self.cget("height"))
        r = self._radius

        self.coords(self._bg_id, 0, 0, w, h)
        self.coords(self._text_id, w / 2, h / 2)
        self.itemconfigure(self._bg_id, fill=bg, outline="")
        self.itemconfigure(self._text_id, fill=fg, text=self._text, font=self._font_tuple)

    def _on_enter(self, _e=None): self._hovered = True; self._draw()
    def _on_leave(self, _e=None): self._hovered = False; self._pressed = False; self._draw()
    def _on_press(self, _e=None): self._pressed = True; self._draw()

    def _on_release(self, _e=None):
        self._pressed = False
        self._draw()
        if self._state != "disabled" and self._command:
            try:
                self._command()
            except Exception:
                pass

    # ---- 兼容 ttk.Button API ----

    def configure(self, cnf=None, **kw):
        if cnf:
            if isinstance(cnf, dict):
                kw.update(cnf)
            else:
                return super().configure(cnf, **kw)
        # 收集 Canvas 原生参数（如 width, height）传递给父类
        native_kw = {}
        for k in ("width", "height"):
            if k in kw:
                native_kw[k] = kw.pop(k)
        if "text" in kw:
            self._text = kw.pop("text")
            import tkinter.font as tkfont
            f = tkfont.Font(font=self._font_tuple)
            text_w = f.measure(self._text) if self._text else 0
            if self._width_chars:
                char_w = max(f.measure("0"), 5)
                fixed_w = self._width_chars * char_w + self._pad_x * 2
                native_kw["width"] = fixed_w
            else:
                new_w = text_w + self._pad_x * 2
                cur_w = int(self.cget("width"))
                if new_w != cur_w:
                    native_kw["width"] = new_w
        if "style" in kw:
            self._style_name = kw.pop("style")
        if "state" in kw:
            self._state = kw.pop("state")
        if "command" in kw:
            self._command = kw.pop("command")
        self._draw()
        # 将原生参数传递给 Canvas 父类
        if native_kw:
            return super().configure(**native_kw)
        return super().configure()

    config = configure

    def cget(self, key):
        if key == "text": return self._text
        if key == "style": return self._style_name
        if key == "state": return self._state
        return super().cget(key)

    def __getitem__(self, key): return self.cget(key)
    def __setitem__(self, key, val): self.configure(**{key: val})


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
        # ============================================================
        #  主题/视觉：Light/Dark + Win11 风格
        # ============================================================
        _pref_snap = load_snapshot()
        _extras: dict = dict(getattr(_pref_snap, "extras", None) or {})
        self.theme = ThemeManager(
            mode=str(_extras.get("theme_mode", "light")),
            style=str(_extras.get("theme_style", "win11")),
        )
        self.theme_mode_var = tk.StringVar(value=self.theme.mode)
        self.theme_style_var = tk.StringVar(value=self.theme.style)

        # 显示模式变量
        self.view_mode_var = tk.StringVar(value="protocol")  # protocol | raw

        # 配置全局 ttk 样式
        self.ttk_style = ttk.Style()
        try:
            self.theme.apply_ttk_styles(self.ttk_style)
        except Exception:
            pass
        RoundedButton.init_styles(self.theme)
        self.root.configure(bg=self.theme.get("app_bg"))
        # 显示模式变量
        self.view_mode_var = tk.StringVar(value="protocol")  # protocol | raw


        # ============================================================
        #  窗口尺寸：统一约束 + 按上次会话恢复
        # ============================================================
        _MIN_W, _MIN_H = 1730, 800
        try:
            self.root.minsize(_MIN_W, _MIN_H)
        except Exception:
            pass

        import re as _re
        def _parse_geom(g: str) -> tuple[int, int, int, int] | None:
            m = _re.match(r"^(\d+)x(\d+)(?:\+(-?\d+)\+(-?\d+))?$", str(g or "").strip())
            if not m:
                return None
            w, h = int(m.group(1)), int(m.group(2))
            x = int(m.group(3)) if m.group(3) is not None else None
            y = int(m.group(4)) if m.group(4) is not None else None
            if w < _MIN_W or h < _MIN_H:
                return None
            if x is None or y is None:
                return w, h, 0, 0
            return w, h, x, y

        try:
            sw = max(800, int(self.root.winfo_screenwidth()))
            sh = max(600, int(self.root.winfo_screenheight()))
        except Exception:
            sw, sh = 1920, 1080

        _DEF_W = max(_MIN_W, min(1400, int(sw * 0.75)))
        _DEF_H = max(_MIN_H, min(860, int(sh * 0.75)))

        _last_geom = _extras.get("window_geometry") if isinstance(_extras, dict) else None
        _parsed = _parse_geom(str(_last_geom)) if _last_geom else None

        if _parsed is None:
            self.root.geometry(f"{_DEF_W}x{_DEF_H}")
        else:
            w, h, x, y = _parsed
            w = max(w, _MIN_W)
            h = max(h, _MIN_H)
            if x == 0 and y == 0:
                self.root.geometry(f"{w}x{h}")
            elif x < -2000 or y < -1000 or x > sw - 200 or y > sh - 100:
                self.root.geometry(f"{w}x{h}")
            else:
                self.root.geometry(f"{w}x{h}+{x}+{y}")

        try:
            self.root.minsize(_MIN_W, _MIN_H)
            self.root.update_idletasks()
        except Exception:
            pass

        # ============================================================
        #  字号偏好（Ctrl+滚轮缩放）
        # ============================================================
        self.font_size_var = tk.IntVar(value=int(_extras.get("font_size", 10)))
        # 构建可变等宽字体（Ctrl+滚轮缩放字号）
        def _pick_mono_family() -> str:
            try:
                families = {f.lower() for f in tkfont.families()}
            except Exception:
                families = set()
            for name in (
                "Cascadia Mono",      # Win10/11 较清晰
                "Consolas",           # 常见默认
                "Sarasa Mono SC",     # 若装了中文等宽
                "Microsoft YaHei Mono",
                "Courier New",
            ):
                if name.lower() in families:
                    return name
            return "Consolas"

        _mono = _pick_mono_family()
        self.serial_font = tkfont.Font(family=_mono, size=self.font_size_var.get())
        self.cmd_font = tkfont.Font(family=_mono, size=self.font_size_var.get(), weight="bold")
        # 日志框 Tag 定义颜色（在创建 serial_text 之后会按 theme.get() 刷新）

        # 置顶状态（菜单栏会引用该变量，必须先初始化）
        self.topmost_var = tk.BooleanVar(value=bool(_extras.get("topmost", False)))
        if self.topmost_var.get():
            try:
                self.root.attributes("-topmost", True)
            except Exception:
                pass

        # ---- 菜单栏：关于 / 检查更新 ----
        self._build_menu_bar()

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

        # 显示选项（原在高级抽屉，现移到主界面）
        self.detail_var = tk.BooleanVar(value=False)
        self.autoscroll_var = tk.BooleanVar(value=True)

        # 串口配置面板折叠状态（默认折叠，减少主界面垂直占用）
        self.serial_config_collapsed_var = tk.BooleanVar(
            value=bool(_extras.get("serial_config_collapsed", True))
        )

        self.log_path: Path | None = None
        self.log_file = None
        self.log_count = 0
        self.rx_frame_count = 0
        self.tx_frame_count = 0
        

        # 原始数据保存：默认关闭 + 默认保存路径用 user_data_path（可写持久目录）
        self.save_raw_enabled_var = tk.BooleanVar(
            value=bool(_extras.get("save_raw_enabled_default", False))  # 默认 False（默认关闭）
        )
        # 默认路径：user_data_path() → 开发模式=项目根data；打包模式=exe同级data；无权限=文档\串口解析工具\data
        default_path = user_data_path()
        default_path = Path(str(_extras.get("save_raw_path_default", str(default_path))))
        # 旧快照路径里如果含有 _MEIPASS 临时目录（C:\Users\xxx\AppData\Local\Temp\_MEI...），自动丢弃，改用 user_data_path
        try:
            if "_MEI" in str(default_path) or str(default_path).startswith(
                str(Path(os.environ.get("TEMP", "")))
            ) or "AppData\\Local\\Temp" in str(default_path).replace("/", "\\"):
                default_path = user_data_path()
        except Exception:
            default_path = user_data_path()
        self.save_raw_path_var = tk.StringVar(value=str(default_path))
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
        self.tx_auto_crc8_var = tk.BooleanVar(value=False)
        # 显示缓冲区限制（防止内存溢出）
        self.max_display_lines = 50000

        # 主布局
        self._build_ui()
        # 设置左右分栏初始比例（右侧约 30%）——仅当面板默认显示时才在启动后恢复宽度
        self._load_protocols()

        # 定时刷新 UI 队列
        self._ui_queue: deque[tuple[str, tuple]] = deque()
        self.root.after(100, self._process_ui_queue)
        # 串口列表：启动立即刷新 + 后台热插拔轮询
        self.root.after(200, self._safe(self._start_port_watch))

        # 关闭窗口时：保存偏好 + 安全停止串口（不要等更新才保存）
        self.root.protocol("WM_DELETE_WINDOW", self._safe(self._on_app_close))

        # 若是 monitor 启动方式：自动跳转到「串口实时」tab + 选中指定串口/波特率
        if self._monitor_port:
            self._apply_monitor_args()

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
        """已移除「关于 / 检查更新」菜单项。"""
        try:
            self.root.config(menu="")
        except Exception:
            pass

    def _menu_about(self) -> None:
        body = (
            f"串口协议解析工具\n"
            f"当前版本：v{VERSION}\n"
            f"发布仓库：github.com/{UPDATER_GITHUB_REPO}\n\n"
            f"—— 功能特性 ——\n"
            "· 串口实时监控（HEX / ASCII）\n"
            "· 导入 Word 协议文档，中文属性名和枚举解析\n"
            "· 每个串口独立窗口进程，支持 6M / 自定义波特率\n"
            "· 在线更新（菜单栏 → 检查更新）"
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
        check_btn = RoundedButton(btns, text="立即检查", command=lambda: self._dlg_check_now())
        check_btn.pack(side="right")
        update_btn = RoundedButton(btns, text="下载并更新", state="disabled")
        update_btn.pack(side="right", padx=8)
        close_btn = RoundedButton(btns, text="关闭", command=win.destroy)
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

            # 更新前：先保存偏好
            try:
                self._save_preferences()
            except Exception:
                pass

            try:
                _updater_apply(path, snapshot_path=None)  # 内部会 os._exit(0)
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
        #  根级统一响应式布局（grid）：
        #    row 0  →  顶部工具栏（fixed）
        #    row 1  →  主体 body（可伸缩，weight=1，PanedWindow 水平左右分栏）
        #    row 2  →  底部状态栏（fixed）
        #    col 0  →  weight=1（所有内容横向跟随窗口伸缩）
        # ============================================================
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=0)
        self.root.rowconfigure(1, weight=1)
        self.root.rowconfigure(2, weight=0)

        # 限制窗口最小尺寸，防止缩太小时控件被截断（和 __init__ 保持一致，避免被后续逻辑覆盖）
        try:
            self.root.update_idletasks()
        except Exception:
            pass

        # ============================================================
        #  row=0: 顶部工具栏（一行三列响应式 —— 中间 spacer(weight=1) 缓冲，缩窗口绝不裁剪左右控件）
        #    col 0  weight=0  →  产品协议 + 刷新/导入/查看
        #    col 1  weight=1  →  伸缩 spacer（缩窗口时优先变窄到 0，保护左右不被裁）
        #    col 2  weight=0  →  指令发送/添加串口/保存日志/清空/置顶
        # ============================================================
        top = ttk.Frame(self.root, padding=(8, 4, 8, 4))
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(0, weight=1)
        top.rowconfigure(0, weight=0)

        def _tool_button(parent: tk.Misc, text: str, cmd,
                         style: str = "Toolbar.TButton", tip: str | None = None,
                         padx: tuple[int, int] = (2, 2)) -> RoundedButton:
            b = RoundedButton(parent, text=text, style=style, command=self._safe(cmd))
            col = parent._next_col if hasattr(parent, "_next_col") else 0  # type: ignore[attr-defined]
            b.grid(row=0, column=col, sticky="w", padx=padx, pady=(1, 1))
            parent._next_col = col + 1  # type: ignore[attr-defined]
            if tip:
                Tooltip(b, tip, self.theme)
            return b

        def _tool_place_reset(container: tk.Misc) -> None:
            container._next_col = 0  # type: ignore[attr-defined]

        top_row0 = ttk.Frame(top)
        top_row0.grid(row=0, column=0, sticky="ew")
        top_row0.columnconfigure(0, weight=0)   # 左区：固定尺寸，绝不裁剪
        top_row0.columnconfigure(1, weight=1)   # 中间：伸缩缓冲区，压到 0 都不影响左右
        top_row0.columnconfigure(2, weight=0)   # 右区：固定尺寸，绝不裁剪


        # 中间伸缩 spacer：窗口缩小时这一块先被压扁为 0，左右两侧的按钮/下拉永远不被裁剪隐藏
        row0_spacer = ttk.Frame(top_row0, width=1)
        row0_spacer.grid(row=0, column=1, sticky="ew")

        try:
            self.root.bind("<F5>", lambda _e: (self._safe(self._start_serial)(), None)[1] if not self.is_collecting else None)
            self.root.bind("<Shift-F5>", lambda _e: (self._safe(self._stop_serial)(), None)[1] if self.is_collecting else None)
        except Exception:
            pass

        # ============================================================
        #  row=1: 中间主体（左侧：串口配置 + 实时数据 ∥ 指令发送）
        # ============================================================
        body = ttk.Frame(self.root, padding=(10, 0, 10, 6))
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        self.body_frame = body  # 供面板显隐时触发重排

        # 左侧面板整体：使用「app_bg TFrame」不使用 Card.TFrame —— 作为"留白底色"，
        #   里面的 串口配置 / 实时数据 / 指令发送 三个 LabelFrame 各自作为独立"卡片区"
        #   这样卡片与卡片之间就有 留白（app_bg）+ 边框（card_border）双重视觉层差。
        self.serial_frame = ttk.Frame(body)  # 无 style → TFrame=app_bg，padding 统一留白
        self.serial_frame.grid(row=0, column=0, sticky="nsew")
        self.serial_frame.columnconfigure(0, weight=1)
        self.serial_frame.rowconfigure(0, weight=0)   # 串口配置区（fixed）
        self.serial_frame.rowconfigure(1, weight=1)   # 下方分栏区（expand）
        # 垂直留白：串口配置（上）↔ 下方分栏（下）之间 4px 的 app_bg 色 gap，形成层
        self.serial_frame.configure(padding=(0, 0, 0, 4))

        self._build_serial_config_panel(self.serial_frame)

        # 下方：实时数据（左，自适应） + 指令发送（右，固定宽度，不可拖）
        self.SEND_PANEL_WIDTH = 600

        self.content_row = ttk.Frame(self.serial_frame)
        self.content_row.grid(row=1, column=0, sticky="nsew", padx=0, pady=0)
        self.content_row.columnconfigure(0, weight=1)   # 左侧拉伸
        self.content_row.columnconfigure(1, weight=0)   # 右侧固定
        self.content_row.rowconfigure(0, weight=1)

        # 左侧：实时数据
        self.realtime_container = ttk.Frame(self.content_row)
        self.realtime_container.grid(row=0, column=0, sticky="nsew")
        self.realtime_container.columnconfigure(0, weight=1)
        self.realtime_container.rowconfigure(0, weight=1)
        self.realtime_container.configure(padding=(4, 4, 2, 0))
        self._build_serial_panel(self.realtime_container)

        # 右侧：固定宽度外壳（grid_propagate=False 锁死宽度）
        self.send_outer = tk.Frame(
            self.content_row,
            width=self.SEND_PANEL_WIDTH,
            bg=self.theme.get("app_bg"),
            highlightthickness=0,
        )
        self.send_outer.grid(row=0, column=1, sticky="ns")
        self.send_outer.grid_propagate(False)
        self.send_outer.columnconfigure(0, weight=1)
        self.send_outer.rowconfigure(0, weight=1)

        self.send_frame = ttk.Frame(self.send_outer)
        self.send_frame.grid(row=0, column=0, sticky="nsew")
        self.send_frame.columnconfigure(0, weight=1)
        self.send_frame.rowconfigure(0, weight=1)
        self.send_frame.configure(padding=(2, 4, 4, 0))

        self.send_panel_inner = ttk.Frame(self.send_frame)
        self.send_panel_inner.grid(row=0, column=0, sticky="nsew")
        self.send_panel_inner.columnconfigure(0, weight=1)
        self.send_panel_inner.rowconfigure(2, weight=1)  # 协议/Raw 拉伸
        self._build_send_panel(self.send_panel_inner)

        # 兼容旧引用
        self.serial_tab = self.serial_frame
        self.send_tab = self.send_frame
        self.main_paned = None  # 已不再使用 PanedWindow

        self.send_frame_visible = False
        self.send_frame_frac = 0.0  # 不再按比例

        # 默认隐藏发送面板
        try:
            self.send_outer.grid_remove()
        except Exception:
            pass


        # ============================================================
        #  row=2: 底部状态栏（fixed）
        # ============================================================
        self.status_var = tk.StringVar(value="就绪")
        self.stats_var = tk.StringVar(value="RX 0  TX 0  错误 0  缓冲 0B")
        status = ttk.Frame(self.root, style="StatusBar.TFrame", padding=(10, 4))
        status.grid(row=2, column=0, sticky="ew")
        status.columnconfigure(0, weight=1)
        ttk.Label(status, textvariable=self.status_var, anchor="w", style="StatusBar.TLabel").grid(
            row=0, column=0, sticky="we")
        ttk.Label(status, textvariable=self.stats_var, anchor="e", style="StatusBar.TLabel").grid(row=0, column=1, sticky="e")

        # 字号变化时即时应用 + 保存偏好
        self.font_size_var.trace_add("write", lambda *_a: self._apply_font_and_line_spacing(True))

        # 置顶/保存原始数据 变量变化时自动更新按钮样式
        try:
            self.topmost_var.trace_add("write", lambda *_a: self._on_topmost_change())
        except Exception:
            pass
        try:
            self.save_raw_enabled_var.trace_add("write", lambda *_a: self._update_save_raw_btn_style())
        except Exception:
            pass

        # ============================================================
        #  启动后 UI 与 variable 状态强同步（杜绝"第一次点击无效、第二次才生效"）
        # ============================================================
        # 1) 保存原始数据按钮：save_raw_enabled_var 默认从快照恢复可能为 True，
        #    但 UI 初始写死是「开始存储数据」→ 两者完全矛盾导致"点一次视觉不变"。
        #    这里构建完毕后立刻"根据 variable 重置 UI"，保持一致。
        try:
            self._update_save_raw_btn_style()
        except Exception:
            pass

        # 2) 指令发送顶栏按钮：根据 send_frame_visible 初始态纠正按钮文字（防止未来改动时不一致）
        try:
            if getattr(self, "send_frame_visible", True):
                self.send_panel_btn.config(text="关闭指令发送界面", style="Danger.TButton")
            else:
                self.send_panel_btn.config(text="打开指令发送界面", style="Toolbar.TButton")
        except Exception:
            pass


        # 3) 串口配置收起/展开按钮：_apply_serial_config_collapsed 已在 _build_serial_config_panel 末尾调用，
        #    这里再兜底一次，避免 trace/快照恢复导致文字错位。
        try:
            self._apply_serial_config_collapsed()
        except Exception:
            pass

        # 4) 顶栏快捷键绑定：用独立函数代替 lambda，避免"首次 F5/Shift+F5 绑定错误导致第一次按无效"
        def _on_f5(_e=None):
            if not self.is_collecting:
                try:
                    self._safe(self._start_serial)()
                except Exception:
                    pass
            return "break"

        def _on_shift_f5(_e=None):
            if self.is_collecting:
                try:
                    self._safe(self._stop_serial)()
                except Exception:
                    pass
            return "break"

        try:
            self.root.unbind("<F5>")
        except Exception:
            pass
        try:
            self.root.unbind("<Shift-F5>")
        except Exception:
            pass
        try:
            self.root.bind("<F5>", _on_f5)
            self.root.bind("<Shift-F5>", _on_shift_f5)
        except Exception:
            pass

    # ------------------------------------------------------------
    # 串口配置面板（原「高级设置」中的显示/保存项合并到这里）
    # ------------------------------------------------------------
    def _build_serial_config_panel(self, parent: tk.Misc) -> None:
        theme = self.theme

        # 标题行：左「串口配置」+ 右四按钮（与标题同一行）
        _cfg_hdr = ttk.Frame(parent)
        ttk.Label(_cfg_hdr, text="串口配置", style="TLabelframe.Label").pack(
            side="left", padx=(0, 12)
        )

        self.send_panel_btn = RoundedButton(
            _cfg_hdr, text="打开指令发送界面", style="Toolbar.TButton",
            command=self._safe(self._toggle_send_panel),
        )
        self.send_panel_btn.pack(side="left", padx=(0, 4))
        Tooltip(self.send_panel_btn, "打开或关闭右侧「指令发送」面板。", theme)

        RoundedButton(
            _cfg_hdr, text="添加串口", style="Toolbar.TButton",
            command=self._safe(self._add_serial_port),
        ).pack(side="left", padx=(0, 4))

        RoundedButton(
            _cfg_hdr, text="保存日志", style="Toolbar.TButton",
            command=self._safe(self._choose_log),
        ).pack(side="left", padx=(0, 4))

        ttk.Frame(_cfg_hdr).pack(side="left", expand=True, fill="x")

        # 右侧：发送方 + HEX + 自动滚动（与「串口配置」同一行）
        ttk.Frame(_cfg_hdr, width=12).pack(side="left")  # 小间距；若要贴最右可再加 expand

        ttk.Label(_cfg_hdr, text="发送方：", style="TLabelframe.Label").pack(side="left")
        sender_frame = ttk.Frame(_cfg_hdr)
        sender_frame.pack(side="left", padx=(4, 12))
        self.sender_module_rb = ttk.Radiobutton(
            sender_frame, text="模组发送", variable=self.serial_sender_var,
            value="模组发送", style="TRadiobutton",
        )
        self.sender_module_rb.pack(side="left", padx=(0, 8))
        self.sender_mcu_rb = ttk.Radiobutton(
            sender_frame, text="MCU发送", variable=self.serial_sender_var,
            value="MCU发送", style="TRadiobutton",
        )
        self.sender_mcu_rb.pack(side="left")

        _auto_chk = ttk.Checkbutton(
            _cfg_hdr, text="自动滚动", variable=self.autoscroll_var, style="TCheckbutton",
        )
        _auto_chk.pack(side="left")
        Tooltip(_auto_chk, "有新报文时自动滚动到底部；关闭后可方便回看历史。", theme)

        # 置顶：标题栏最右侧
        try:
            self.topmost_btn = RoundedButton(
                _cfg_hdr, text="置顶", style="CompactPrimary.TButton",
                command=self._safe(self._toggle_topmost_btn), width=4,
            )
            self.topmost_btn.pack(side="left", padx=(8, 0))
            Tooltip(self.topmost_btn, "窗口始终置顶 / 取消置顶。", theme)
            self.topmost_chk = self.topmost_btn
        except Exception:
            self.topmost_btn = None
            self.topmost_chk = None

        ttk.Frame(_cfg_hdr).pack(side="left", expand=True, fill="x")

        frame = ttk.LabelFrame(parent, labelwidget=_cfg_hdr, padding=(12, 10))
        frame.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)
        # 串口配置面板整体按 grid 展开：每行 1 列，权重=0（fixed height，不参与挤压）
        frame.columnconfigure(0, weight=1)

        def _hint(parent: tk.Misc, text_: str) -> ttk.Label:
            return ttk.Label(parent, text=text_, style="Hint.TLabel", font=("Microsoft YaHei UI", 9))

        # ---- 第一行：串口 / 波特率 / 数据位 / 停止位 / 展开 / 开始监控 ----
        row1_wrap = ttk.Frame(frame, style="Card.TFrame")
        row1_wrap.grid(row=0, column=0, sticky="ew", pady=(0, 3))
        row1 = ttk.Frame(row1_wrap, style="Card.TFrame")
        row1.pack(fill="x", padx=2, pady=2)
        row1.columnconfigure(100, weight=1)

        ttk.Label(row1, text="串口：", style="Card.TLabel").grid(row=0, column=0, sticky="w")
        # 串口选择框
        self.port_combo = ttk.Combobox(row1, textvariable=self.port_var, width=40, state="readonly")
        self.port_combo.grid(row=0, column=1, sticky="w", padx=(4, 2))
        _enable_full_combobox(self.port_combo)
        # 监控中切换串口：自动停止当前串口并连接新选中的串口
        self.port_combo.bind("<<ComboboxSelected>>", self._safe(self._on_port_change_while_collecting), add="+")
        refresh_ports_btn = RoundedButton(row1, text="刷新", width=6,
                                          command=self._safe(self._refresh_ports), style="Toolbar.TButton")
        refresh_ports_btn.grid(row=0, column=2, sticky="w", padx=(0, 8))
        Tooltip(refresh_ports_btn, "重新扫描本机可用串口。", theme)

        ttk.Label(row1, text="波特率：", style="Card.TLabel").grid(row=0, column=10, sticky="w")
        self.baudrate_combo = ttk.Combobox(
            row1, textvariable=self.baudrate_var, width=11, state="normal",
            values=[9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600,
                    1000000, 1500000, 2000000, 3000000, 4000000, 5000000, 6000000],
        )
        _enable_full_combobox(self.baudrate_combo)
        self.baudrate_combo.grid(row=0, column=11, sticky="w", padx=(4, 2))
        self.baudrate_combo.bind("<<ComboboxSelected>>", self._safe(self._on_serial_param_change_while_collecting), add="+")
        self.baudrate_combo.bind("<Return>", self._safe(self._on_serial_param_change_while_collecting), add="+")
        self.baudrate_combo.bind("<FocusOut>", self._safe(self._on_serial_param_change_while_collecting), add="+")

        ttk.Label(row1, text="数据位：", style="Card.TLabel").grid(row=0, column=20, sticky="w", padx=(8, 0))
        bytesize_combo = ttk.Combobox(
            row1, textvariable=self.bytesize_var, values=[5, 6, 7, 8], width=5, state="readonly"
        )
        bytesize_combo.grid(row=0, column=21, sticky="w", padx=(4, 8))
        _enable_full_combobox(bytesize_combo)
        bytesize_combo.bind("<<ComboboxSelected>>", self._safe(self._on_serial_param_change_while_collecting), add="+")

        ttk.Label(row1, text="停止位：", style="Card.TLabel").grid(row=0, column=30, sticky="w")
        stopbits_combo = ttk.Combobox(
            row1, textvariable=self.stopbits_var, values=[1, 1.5, 2], width=5, state="readonly"
        )
        stopbits_combo.grid(row=0, column=31, sticky="w", padx=(4, 8))
        _enable_full_combobox(stopbits_combo)
        stopbits_combo.bind("<<ComboboxSelected>>", self._safe(self._on_serial_param_change_while_collecting), add="+")

        self.hex_btn = RoundedButton(
            row1,
            text="HEX 格式",
            style="Primary.TButton",
            command=self._safe(self._toggle_hex_format_btn),
            width=10,
        )
        self.hex_btn.grid(row=0, column=198, sticky="e", padx=(8, 4))
        Tooltip(self.hex_btn, "在 HEX / ASCII 显示之间切换。", theme)
        self.hex_chk = self.hex_btn  # 兼容旧引用
        self._sync_hex_btn_style()

        # 收起/展开
        self.serial_config_toggle_btn = RoundedButton(
            row1, text="收起 ▲", width=5, style="Toolbar.TButton",
            command=self._safe(self._toggle_serial_config_panel),
        )
        self.serial_config_toggle_btn.grid(row=0, column=199, sticky="e", padx=(6, 2))
        Tooltip(
            self.serial_config_toggle_btn,
            "收起/展开串口配置高级选项（保存原始数据等）。",
            theme,
        )

        # 开始监控（必须先创建，再 grid）
        self.start_btn = RoundedButton(
            row1, text="● 开始监控", style="Primary.TButton",
            command=self._safe(self._toggle_serial),
        )
        self.start_btn.grid(row=0, column=200, sticky="e", padx=(0, 4))
        Tooltip(
            self.start_btn,
            "开始监控（绿灯）/ 停止监控（灰）。\n快捷键：F5 开始 / Shift+F5 停止。",
            theme,
        )

        self._serial_config_detail_row = None

        # ---- 第三/第四行：保存原始数据（拆为两行，避免窄窗口时路径/文件名被裁剪隐藏） ----
        #  row3a：☑保存原始数据   路径：[Entry 伸缩 weight=1]  [选择]
        #  row3b：（缩进对齐）    文件名：[Entry]  （超过50MB自动分割, .dat）
        row3a_wrap = ttk.Frame(frame, style="Card.TFrame")
        row3a_wrap.grid(row=2, column=0, sticky="ew", pady=(2, 2))
        self._serial_config_raw_row_a = row3a_wrap
        row3a = ttk.Frame(row3a_wrap, style="Card.TFrame")
        row3a.pack(fill="x", padx=2, pady=4)
        # col 2 给路径 Entry 作为唯一伸缩列，窄窗口只压缩路径、不裁按钮/标签
        row3a.columnconfigure(2, weight=1)

        # 保存原始数据按钮（蓝色=未启用 / 红色=已启用）
        self.save_raw_btn = RoundedButton(
            row3a, text="开始存储数据", style="CompactPrimary.TButton",
            command=self._safe(self._toggle_save_raw_btn),
        )
        self.save_raw_btn.grid(row=0, column=0, sticky="w", padx=(0, 20))

        ttk.Label(row3a, text="路径：", style="Card.TLabel").grid(row=0, column=1, sticky="w")
        raw_path = ttk.Entry(row3a, textvariable=self.save_raw_path_var, state="readonly")
        raw_path.grid(row=0, column=2, sticky="we", padx=(4, 8))
        RoundedButton(row3a, text="选择", width=6, style="Toolbar.TButton",
                      command=self._safe(self._choose_save_raw_path)).grid(row=0, column=3, sticky="w", padx=(0, 4))

        row3b_wrap = ttk.Frame(frame, style="Card.TFrame")
        row3b_wrap.grid(row=3, column=0, sticky="ew", pady=(0, 2))
        self._serial_config_raw_row_b = row3b_wrap
        row3b = ttk.Frame(row3b_wrap, style="Card.TFrame")
        row3b.pack(fill="x", padx=2, pady=(0, 4))
        # 保持与上面 "保存原始数据 checkbox" 的文字对齐：前面放一个等宽空白（宽度≈checkbox）
        row3b.columnconfigure(0, minsize=112)   # 与「☑保存原始数据 + 20px pad」对齐

        ttk.Label(row3b, text="", style="Card.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(row3b, text="文件名：", style="Card.TLabel").grid(row=0, column=1, sticky="w")
        raw_name = ttk.Entry(row3b, textvariable=self.save_raw_filename_var, width=26)
        raw_name.grid(row=0, column=2, sticky="w", padx=(4, 10))
        _hint(row3b, "(.dat 格式，超过 50MB 自动分割)").grid(row=0, column=3, sticky="w")

        # 根据持久化/默认折叠状态应用显示
        self._apply_serial_config_collapsed()

    def _toggle_serial_config_panel(self) -> None:
        """切换串口配置面板的折叠/展开状态。"""
        self.serial_config_collapsed_var.set(not self.serial_config_collapsed_var.get())
        self._apply_serial_config_collapsed()
        try:
            self._save_preferences()
        except Exception:
            pass

    def _apply_serial_config_collapsed(self) -> None:
        """根据 serial_config_collapsed_var 显示/隐藏高级配置行。"""
        try:
            collapsed = bool(self.serial_config_collapsed_var.get())
        except Exception:
            collapsed = True

        for attr in (
            "_serial_config_detail_row",
            "_serial_config_raw_row_a",
            "_serial_config_raw_row_b",
        ):
            w = getattr(self, attr, None)
            if w is None:
                continue
            try:
                if collapsed:
                    w.grid_remove()
                else:
                    w.grid()
            except Exception:
                pass

        try:
            self.serial_config_toggle_btn.config(
                text="展开 ▼" if collapsed else "收起 ▲"
            )
        except Exception:
            pass

    def _build_serial_panel(self, parent: tk.Misc) -> None:
        """构建实时数据面板（日志框 + 字体控件），放入「实时数据」分组中。"""
        theme = self.theme
        card_bg = theme.get("card_bg")

        # 实时数据分组
        # 标题栏：实时数据 + 模式按钮 + 清空
        _hdr = ttk.Frame(parent)
        ttk.Label(_hdr, text="实时数据", style="TLabelframe.Label").pack(side="left", padx=(0, 8))
        self.view_mode_btn = RoundedButton(
            _hdr,
            text="协议解析模式",
            style="CompactPrimary.TButton",
            command=self._safe(self._toggle_view_mode),
            width=12,
        )
        self.view_mode_btn.pack(side="left", padx=(0, 6))
        self.clear_output_btn = RoundedButton(
            _hdr,
            text="清空",
            style="Toolbar.TButton",
            command=self._safe(self._clear_output),
            width=4,
        )
        self.clear_output_btn.pack(side="left", padx=(0, 4))

        # —— 产品协议（从顶栏红框移来）——
        ttk.Separator(_hdr, orient="vertical").pack(side="left", fill="y", padx=8, pady=2)
        self._proto_label = ttk.Label(_hdr, text="产品协议：", style="TLabelframe.Label")
        self._proto_label.pack(side="left", padx=(0, 4))
        self.product_combo = ttk.Combobox(
            _hdr, textvariable=self.product_var, width=18, state="readonly"
        )
        self.product_combo.pack(side="left", padx=(0, 6))
        _enable_full_combobox(self.product_combo)
        self.product_combo.bind("<<ComboboxSelected>>", self._on_product_change)
        self._import_btn = RoundedButton(
            _hdr, text="导入Word协议", style="Toolbar.TButton",
            command=self._safe(self._import_docx),
        )
        self._import_btn.pack(side="left", padx=(0, 4))
        self._view_proto_btn = RoundedButton(
            _hdr, text="查看协议", style="Toolbar.TButton",
            command=self._safe(self._show_protocol),
        )
        self._view_proto_btn.pack(side="left", padx=(0, 4))

        realtime_frame = ttk.LabelFrame(parent, labelwidget=_hdr, padding=6)
        realtime_frame.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)
        realtime_frame.columnconfigure(0, weight=1)
        realtime_frame.rowconfigure(0, weight=1)

        # 日志输出区（占满整个分组）
        out_frame = tk.Frame(realtime_frame, bg=card_bg,
                             highlightthickness=0)
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
            spacing3=2,
            bd=0,
            highlightthickness=0,
        )
        self.serial_text.grid(row=0, column=0, sticky="nsew", padx=0, pady=0)

        scroll = ttk.Scrollbar(out_frame, orient="vertical", command=self.serial_text.yview)
        scroll.grid(row=0, column=1, sticky="ns", padx=(0, 0), pady=0)
        self.serial_text.configure(yscrollcommand=scroll.set)

        # ============================================================
        # 定义显示用的 Tag（颜色统一从 theme 取，便于切换主题时刷新）
        # ============================================================
        self._apply_theme_tags()

        # 初始状态：根据 HEX 格式勾选状态设置发送方选择是否可用
        self._on_hex_format_change()

        # Radiobutton 和 Checkbutton 改值时同步给 collector
        self.serial_sender_var.trace_add("write", self._on_serial_sender_change)
        self.hex_format_var.trace_add("write", self._on_hex_format_sync_collector)

        # 通用右键菜单
        _bind_text_widget_menu(self.serial_text, readonly=True)

        # Ctrl + 鼠标滚轮：日志框内缩放字号（不触发分栏）
        self.serial_text.bind("<Control-MouseWheel>", self._on_ctrl_mousewheel_text, add="+")
        # Linux 兼容
        self.serial_text.bind("<Control-Button-4>", self._on_ctrl_mousewheel_text, add="+")
        self.serial_text.bind("<Control-Button-5>", self._on_ctrl_mousewheel_text, add="+")

    # ------------------------------------------------------------
    # 字体：无级缩放
    # ------------------------------------------------------------
    def _on_ctrl_mousewheel_text(self, event):
        delta = getattr(event, "delta", 0)
        # Linux: Button-4 上滚 / Button-5 下滚
        num = getattr(event, "num", None)
        if num == 4:
            steps = 1
        elif num == 5:
            steps = -1
        else:
            steps = 1 if delta > 0 else -1
        try:
            self._zoom_serial_font(steps)
        except Exception as e:
            self._report_error("缩放字号失败", e)
        return "break"

    def _zoom_serial_font(self, steps: int) -> None:
        new_size = int(self.font_size_var.get()) + int(steps)
        new_size = max(8, min(32, new_size))
        if new_size == int(self.font_size_var.get()):
            return
        self.font_size_var.set(new_size)

    def _build_send_panel(self, parent: tk.Misc) -> None:
        """构建"指令发送"Tab。"""

        # 模式选择
        mode_row = ttk.LabelFrame(parent, text="发送模式", padding=8)
        mode_row.grid(row=1, column=0, sticky="we", pady=(0, 8))
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
        self.protocol_frame.grid(row=2, column=0, sticky="nsew", pady=(0, 8))
        self.protocol_frame.columnconfigure(1, weight=1)

        # 第 0 行：命令 + 方向
        ttk.Label(self.protocol_frame, text="命令:").grid(row=0, column=0, sticky="w", pady=(0, 4))
        self.tx_cmd_label_var = tk.StringVar(value="")
        self.cmd_combo = ttk.Combobox(
            self.protocol_frame,
            textvariable=self.tx_cmd_label_var,
            state="readonly",
            width=28,
        )
        _enable_full_combobox(self.cmd_combo)
        self.cmd_combo.grid(row=0, column=1, sticky="ew", pady=(0, 4))
        self.cmd_combo.bind("<<ComboboxSelected>>", self._on_send_cmd_selected)

        ttk.Label(self.protocol_frame, text="方向:").grid(row=0, column=2, sticky="e", padx=(8, 0))
        dir_combo = ttk.Combobox(
            self.protocol_frame,
            textvariable=self.tx_direction_var,
            values=["模组发送", "MCU发送"],
            state="readonly",
            width=12,
        )
        _enable_full_combobox(dir_combo)
        dir_combo.grid(row=0, column=3, sticky="w", pady=(0, 4))

        # 第 1 行：快捷动作
        ttk.Label(self.protocol_frame, text="快捷动作:").grid(row=1, column=0, sticky="w", pady=(0, 4))
        self.tx_quick_var = tk.StringVar(value="")
        self.quick_combo = ttk.Combobox(
            self.protocol_frame,
            textvariable=self.tx_quick_var,
            width=28,
            state="readonly",
        )
        self.quick_combo.grid(row=1, column=1, columnspan=3, sticky="ew", pady=(0, 4))
        self.quick_combo.bind("<<ComboboxSelected>>", self._safe(self._on_quick_action_selected))
        self._quick_action_map: dict[str, dict] = {}

        # 第 2 行：字段 JSON
        ttk.Label(self.protocol_frame, text="字段 JSON:").grid(row=2, column=0, sticky="nw", pady=3)
        self.fields_text = tk.Text(
            self.protocol_frame,
            height=4,
            font=self.serial_font,
            relief="solid",
            borderwidth=1,
            highlightthickness=1,
            highlightbackground="#E0E0E0",
            highlightcolor="#E0E0E0",
            bd=1,
        )
        self.fields_text.grid(row=2, column=1, columnspan=3, sticky="nsew", pady=3)
        self.protocol_frame.rowconfigure(2, weight=1)
        self.protocol_frame.columnconfigure(1, weight=1)
        self.fields_text.insert("1.0", self.tx_fields_var.get())

        # Raw 内容（共用 1 个帧，通过 mode 显示不同的 placeholder）
        self.raw_frame = ttk.LabelFrame(parent, text="Raw 内容（切换模式后此处改变语义）", padding=8)
        self.raw_frame.grid(row=2, column=0, sticky="nsew", pady=(0, 8))  # 与协议同排
        self.raw_frame.columnconfigure(0, weight=1)
        self.raw_frame.rowconfigure(1, weight=1)
        self.raw_frame.columnconfigure(0, weight=1)
        self.raw_hint = ttk.Label(self.raw_frame, text="HEX 模式：1A 2B 3C 或 1A2B3C", foreground="#555")
        self.raw_hint.grid(row=0, column=0, sticky="w")
        self.raw_text = tk.Text(
            self.raw_frame,
            height=4,
            font=self.serial_font,
            relief="solid",
            borderwidth=1,
            highlightthickness=1,
            highlightbackground="#E0E0E0",
            highlightcolor="#E0E0E0",
            bd=1,
        )
        self.raw_text.grid(row=1, column=1, columnspan=3, sticky="nsew", pady=3)
        self.raw_frame.rowconfigure(1, weight=1)

        # 周期发送 + 操作按钮（两行，避免窄宽度下被裁切）
        act = ttk.LabelFrame(parent, text="发送操作", padding=6)
        act.grid(row=3, column=0, sticky="ew")
        act.columnconfigure(0, weight=1)

        # 第一行：间隔 + 启用循环
        row_a = ttk.Frame(act)
        row_a.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        ttk.Label(row_a, text="间隔(ms):").pack(side="left")
        ivs = ttk.Spinbox(
            row_a, from_=10, to=3600000, increment=10,
            textvariable=self.tx_interval_ms_var, width=8,
        )
        ivs.pack(side="left", padx=(2, 8))
        ttk.Checkbutton(row_a, text="启用循环", variable=self.tx_cycle_var).pack(
            side="left", padx=(0, 8)
        )

        ttk.Checkbutton(
            row_a,
            text="自动追加校验",
            variable=self.tx_auto_crc8_var,
        ).pack(side="left", padx=(0, 8))

        # 第二行：发送一次 + 开始循环（单独一行，避免被裁）
        row_b = ttk.Frame(act)
        row_b.grid(row=1, column=0, sticky="ew", pady=(0, 4))
        self.send_once_btn = RoundedButton(
            row_b, text="▶ 发送一次", command=self._safe(self._on_send_once)
        )
        self.send_once_btn.pack(side="left", padx=2)
        self.tx_cycle_btn = RoundedButton(
            row_b, text="▶ 开始循环", command=self._safe(self._on_toggle_cycle_send)
        )
        self.tx_cycle_btn.pack(side="left", padx=2)

        # 第三行：复制 / 清空
        row_c = ttk.Frame(act)
        row_c.grid(row=2, column=0, sticky="ew")
        self.copy_hex_btn = RoundedButton(
            row_c, text="复制当前帧 HEX", command=self._safe(self._on_copy_hex)
        )
        self.copy_hex_btn.pack(side="left", padx=2)
        self.clear_send_btn = RoundedButton(
            row_c, text="清空输入", command=self._safe(self._on_clear_send)
        )
        self.clear_send_btn.pack(side="left", padx=2)

        self._on_send_mode_change()

    def _refresh_send_cmd_list(self) -> None:
        """根据当前协议刷新发送面板的命令下拉。"""
        labels: list[str] = []
        self._send_cmd_map: dict[str, int] = {}
        cfg = self.cfg or {}
        for c in (cfg.get("commands") or []):
            if not isinstance(c, dict):
                continue
            code = c.get("cmd_code", c.get("code", c.get("id", c.get("cmd"))))
            name = c.get("name") or c.get("title") or ""
            try:
                if isinstance(code, str):
                    code_i = int(code, 0)
                else:
                    code_i = int(code)
            except Exception:
                continue
            label = f"0x{code_i:02X}  {name}".strip() if name else f"0x{code_i:02X}"
            labels.append(label)
            self._send_cmd_map[label] = code_i
        try:
            if not hasattr(self, "cmd_combo") or self.cmd_combo is None:
                return
            self.cmd_combo["values"] = labels
            if labels:
                cur = (self.tx_cmd_label_var.get() or "").strip()
                if cur in self._send_cmd_map:
                    self.cmd_combo.set(cur)
                else:
                    self.cmd_combo.current(0)
                    self._on_send_cmd_selected()
            else:
                self.cmd_combo.set("")
                self.tx_cmd_code_var.set("0x20")
        except Exception:
            pass
        try:
            self._refresh_quick_actions()
        except Exception:
            pass

    def _refresh_quick_actions(self) -> None:
        """根据当前协议 attributes 生成「照明打开/关闭」等快捷动作。"""
        labels: list[str] = []
        self._quick_action_map = {}
        attrs = (self.cfg or {}).get("attributes") or {}
        if not isinstance(attrs, dict):
            attrs = {}

        for aid, meta in attrs.items():
            if not isinstance(meta, dict):
                continue
            name = meta.get("cn_name") or meta.get("name") or str(aid)
            try:
                typeid_i = int(meta.get("typeid", 0))
            except Exception:
                typeid_i = 0
            enum_map = meta.get("enum") or {}
            if not isinstance(enum_map, dict):
                enum_map = {}

            if enum_map:
                for ek, ev in enum_map.items():
                    try:
                        val = int(str(ek), 0)
                    except Exception:
                        continue
                    label = f"{name} → {ev}"
                    labels.append(label)
                    self._quick_action_map[label] = {
                        "attrid": aid,
                        "value": val,
                        "typeid": typeid_i,
                    }
            else:
                rng = meta.get("range")
                is_bool = typeid_i == 0 or rng in ([0, 1], "[0,1]")
                if is_bool:
                    for val, ev in ((0, "关闭"), (1, "打开")):
                        label = f"{name} → {ev}"
                        labels.append(label)
                        self._quick_action_map[label] = {
                            "attrid": aid,
                            "value": val,
                            "typeid": typeid_i,
                        }
                else:
                    label = f"{name} → 写入"
                    labels.append(label)
                    self._quick_action_map[label] = {
                        "attrid": aid,
                        "value": 1,
                        "typeid": typeid_i,
                    }

        try:
            if hasattr(self, "quick_combo") and self.quick_combo is not None:
                self.quick_combo["values"] = labels
                self.tx_quick_var.set("")
        except Exception:
            pass

    def _on_quick_action_selected(self, _event=None) -> None:
        """快捷动作：切到「命令下发」并填入对应属性字段 JSON。"""
        label = (self.tx_quick_var.get() or "").strip()
        info = (self._quick_action_map or {}).get(label)
        if not info:
            return

        try:
            self.send_mode_var.set("protocol")
            self._on_send_mode_change()
        except Exception:
            pass

        target_code = 0x01
        chosen_label = None
        for lb, code in (self._send_cmd_map or {}).items():
            if code == target_code or "命令下发" in str(lb):
                chosen_label = lb
                break
        if chosen_label:
            try:
                self.tx_cmd_label_var.set(chosen_label)
                self.cmd_combo.set(chosen_label)
                self._on_send_cmd_selected()
            except Exception:
                self.tx_cmd_code_var.set("0x01")
        else:
            self.tx_cmd_code_var.set("0x01")

        try:
            self.tx_direction_var.set("模组发送")
        except Exception:
            pass

        import json
        payload = {
            "msg_id": 0,
            "attrs": [
                [info["attrid"], info["value"], info["typeid"]]
            ],
        }
        text = json.dumps(payload, ensure_ascii=False)
        try:
            self.fields_text.delete("1.0", "end")
            self.fields_text.insert("1.0", text)
            self.tx_fields_var.set(text)
        except Exception:
            pass

        self._set_status(f"已填入快捷动作: {label}")

    def _on_send_cmd_selected(self, _event=None) -> None:
        label = (self.tx_cmd_label_var.get() or "").strip()
        code = self._send_cmd_map.get(label)
        if code is not None:
            self.tx_cmd_code_var.set(f"0x{code:02X}")

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
                    messagebox.showwarning("提示", "请输入 HEX 内容（例如：A5 A5 03 23 00 01 01）")
                    return
                from protocol_parser.parser import calc_checksum
                s_clean = (
                    s.replace(" ", "").replace("\n", "").replace("\r", "").replace("\t", "")
                )
                if s_clean.lower().startswith("0x"):
                    s_clean = s_clean[2:]
                if len(s_clean) % 2 == 1:
                    s_clean = "0" + s_clean
                try:
                    payload = bytes.fromhex(s_clean)
                except Exception as e:
                    raise ValueError(f"HEX 格式非法：{e}") from e

                if self.tx_auto_crc8_var.get():
                    # 与协议组帧一致：默认 sum；有配置则用 frame.checksum.algorithm
                    cs_algo = "sum"
                    cs_len = 1
                    try:
                        cs_cfg = (self.cfg or {}).get("frame", {}).get("checksum") or {}
                        if isinstance(cs_cfg, dict):
                            cs_algo = str(cs_cfg.get("algorithm") or "sum").lower()
                            cs_len = int(cs_cfg.get("length") or 1)
                    except Exception:
                        pass
                    if cs_algo not in ("none", ""):
                        cs_bytes = calc_checksum(payload, cs_algo)
                        if len(cs_bytes) < cs_len:
                            cs_bytes = b"\x00" * (cs_len - len(cs_bytes)) + cs_bytes
                        elif len(cs_bytes) > cs_len:
                            cs_bytes = cs_bytes[-cs_len:]
                        payload = payload + cs_bytes
                self.collector.send(payload)
            else:  # raw_ascii
                s = self._current_raw_text()
                if not s:
                    messagebox.showwarning("提示", "请输入 ASCII 内容")
                    return
                self.collector.send_raw(s, as_text=True)
            self._sync_inputs_to_vars()
        except Exception as e:
            self._report_error("发送失败", e)

    def _on_toggle_cycle_send(self) -> None:
        # 正在循环（有 after 任务）→ 停止
        if getattr(self, "_tx_cycle_job", None) is not None:
            self.tx_cycle_var.set(False)
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
            self._set_status("已停止循环发送")
            return

        # 未在循环 → 启动
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

        # 启动前先检查一次内容，避免空内容循环狂弹窗
        mode = self.send_mode_var.get()
        if mode == "raw_hex" and not self._current_raw_text().strip():
            messagebox.showwarning("提示", "请输入 HEX 内容后再开始循环")
            return
        if mode == "raw_ascii" and not self._current_raw_text():
            messagebox.showwarning("提示", "请输入 ASCII 内容后再开始循环")
            return

        self.tx_cycle_var.set(True)
        try:
            self.tx_cycle_btn.configure(text="⏹ 停止循环")
        except Exception:
            pass
        self._sync_inputs_to_vars()
        self._on_send_once()
        self._schedule_tx_cycle()
        self._set_status("循环发送已开始")


    def _schedule_tx_cycle(self) -> None:
        try:
            iv = max(10, int(self.tx_interval_ms_var.get()))
        except Exception:
            iv = 1000

        def _job():
            still = bool(
                self.collector
                and self.collector.running
                and self.tx_cycle_var.get()
            )
            if not still:
                self.tx_cycle_var.set(False)
                try:
                    self.tx_cycle_btn.configure(text="▶ 开始循环")
                except Exception:
                    pass
                self._tx_cycle_job = None
                return
            try:
                self._on_send_once()
            except Exception as e:
                try:
                    self._report_error("周期发送出错", e)
                except Exception:
                    pass
                self.tx_cycle_var.set(False)
                try:
                    self.tx_cycle_btn.configure(text="▶ 开始循环")
                except Exception:
                    pass
                self._tx_cycle_job = None
                return
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
        try:
            self._refresh_send_cmd_list()
        except Exception:
            pass

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

    def _toggle_hex_format_btn(self) -> None:
        """HEX / ASCII 切换按钮。"""
        self.hex_format_var.set(not bool(self.hex_format_var.get()))
        self._sync_hex_btn_style()
        try:
            self._on_hex_format_change()
        except Exception:
            pass

    def _sync_hex_btn_style(self) -> None:
        if not hasattr(self, "hex_btn") or self.hex_btn is None:
            return
        if bool(self.hex_format_var.get()):
            self.hex_btn.configure(text="HEX 格式", style="Primary.TButton", width=10)
        else:
            self.hex_btn.configure(text="ASCII 格式", style="Toolbar.TButton", width=10)


    def _on_hex_format_change(self) -> None:
        """HEX/ASCII 切换：ASCII 模式下禁用发送方选择，HEX 模式下恢复可用。"""
        hex_checked = bool(self.hex_format_var.get())
        try:
            state_txt = "normal" if hex_checked else "disabled"
            for item in (self.sender_module_rb, self.sender_mcu_rb):
                try:
                    item.configure(state=state_txt)
                except Exception:
                    pass
        except Exception:
            pass

    def _clear_output(self) -> None:
        """清空输出与统计。"""
        self.serial_text.configure(state="normal")
        self.serial_text.delete("1.0", "end")
        self.serial_text.configure(state="disabled")
        self.rx_frame_count = 0
        self.tx_frame_count = 0
        self.stats_var.set("RX 0  TX 0  错误 0  缓冲 0B")

    def _toggle_view_mode(self) -> None:
        """在「协议解析模式」与「原始数据模式」之间切换。"""
        cur = self.view_mode_var.get()
        if cur == "protocol":
            self.view_mode_var.set("raw")
        else:
            self.view_mode_var.set("protocol")
        self._apply_view_mode()

    def _apply_view_mode(self) -> None:
        """按当前 view_mode 更新按钮文案、顶栏协议控件、采集是否解析。"""
        mode = self.view_mode_var.get()
        is_proto = mode == "protocol"

        # 按钮显示当前模式名称
        try:
            if hasattr(self, "view_mode_btn") and self.view_mode_btn is not None:
                self.view_mode_btn.configure(
                    text="协议解析模式" if is_proto else "原始数据模式",
                    style="CompactPrimary.TButton" if is_proto else "CompactDanger.TButton",
                )
        except Exception:
            pass

        # 协议相关控件显隐
        for w in (
            getattr(self, "_proto_label", None),
            getattr(self, "product_combo", None),
            getattr(self, "_import_btn", None),
            getattr(self, "_view_proto_btn", None),
        ):
            if w is None:
                continue
            try:
                if is_proto:
                    w.pack(side="left", padx=(0, 4))  # 或按上面各自 padx
                else:
                    w.pack_forget()
            except Exception:
                pass

        # 采集中：动态切换是否做协议解析（SerialCollector.raw_mode）
        try:
            if self.collector is not None:
                self.collector.raw_mode = not is_proto
        except Exception:
            pass

        self._set_status("协议解析模式" if is_proto else "原始数据模式（不解析）")

    def _on_topmost_change(self) -> None:
        """窗口置顶状态变化时同步到窗口属性并更新按钮样式。"""
        new_state = bool(self.topmost_var.get())
        self.root.attributes("-topmost", new_state)
        if hasattr(self, "topmost_btn") and self.topmost_btn is not None:
            if new_state:
                self.topmost_btn.configure(text="已置顶", style="CompactDanger.TButton")
            else:
                self.topmost_btn.configure(text="置顶", style="CompactPrimary.TButton")
        self._set_status("已置顶" if new_state else "已取消置顶")

    def _toggle_topmost_btn(self) -> None:
        """点击置顶按钮时切换状态。"""
        self.topmost_var.set(not self.topmost_var.get())
        # trace_add 会自动触发 _on_topmost_change()

    def _toggle_send_panel(self) -> None:
        """显示/隐藏指令发送面板（固定宽度，不可横向拖动）。"""
        try:
            if self.send_frame_visible:
                self.send_outer.grid_remove()
                self.send_frame_visible = False
                try:
                    self.send_panel_btn.config(text="打开指令发送界面", style="Toolbar.TButton")
                except Exception:
                    pass
                self._set_status("已隐藏指令发送面板")
            else:
                self.send_outer.grid(row=0, column=1, sticky="ns")
                # 再次锁定宽度（某些主题下 grid 后需重设）
                try:
                    self.send_outer.configure(width=self.SEND_PANEL_WIDTH)
                    self.send_outer.grid_propagate(False)
                except Exception:
                    pass
                self.send_frame_visible = True
                try:
                    self.send_panel_btn.config(text="关闭指令发送界面", style="Danger.TButton")
                except Exception:
                    pass
                self._set_status("已显示指令发送面板")
            self.root.update_idletasks()
        except Exception as e:
            self._report_error("切换指令发送面板失败", e)

    def _restore_send_panel_width(self) -> None:
        """已改为固定宽度，无需恢复 sash 比例。"""
        return

    # ------------------------------------------------------------
    # 主题/字体/偏好 相关方法
    # ------------------------------------------------------------
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
        """Ctrl+滚轮改字号：只改字体，不动分栏 / 发送面板。"""
        try:
            size = max(8, min(32, int(self.font_size_var.get())))
            self.serial_font.configure(size=size)
            self.cmd_font.configure(size=size, weight="bold")
        except Exception:
            pass
        try:
            if hasattr(self, "serial_text") and self.serial_text is not None:
                self.serial_text.configure(font=self.serial_font)
        except Exception:
            pass
        try:
            self._apply_theme_tags()
        except Exception:
            pass
        try:
            for _w in (getattr(self, "fields_text", None), getattr(self, "raw_text", None)):
                if _w is not None:
                    _w.configure(font=self.serial_font)
        except Exception:
            pass
        # 注意：这里不要 paneconfig / sashpos，否则改字号会把发送面板顶出来
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
            RoundedButton.init_styles(self.theme)
            RoundedButton.redraw_all(self.root)
        t = self.theme
        # 2) 根窗口 / 顶栏 / Notebook 体 / 状态栏 颜色
        try:
            self.root.configure(bg=t.get("app_bg"))
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
        # 4) tag 颜色/字体
        self._apply_font_and_line_spacing(save=False)
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
        """把视觉/字体/抽屉/高级设置偏好 + 当前窗口尺寸/位置 写进 snapshot.extras。"""
        snap = load_snapshot() or SessionSnapshot()
        try:
            extras = dict(snap.extras) if isinstance(snap.extras, dict) else {}
        except Exception:
            extras = {}

        # 记录当前窗口几何：关闭时保存，下次启动恢复（保证 minsize 生效 + 不必每次都手动拉大小）
        try:
            geom = self.root.geometry() if self.root else ""
            if geom:
                extras["window_geometry"] = str(geom)
        except Exception:
            pass

        extras.update({
            "theme_mode": self.theme.mode,
            "theme_style": self.theme.style,
            "font_size": int(self.font_size_var.get()),
            "topmost": bool(self.topmost_var.get()),
            "serial_config_collapsed": bool(self.serial_config_collapsed_var.get()),
            "save_raw_enabled_default": bool(self.save_raw_enabled_var.get()),
            "save_raw_path_default": self.save_raw_path_var.get(),
            "raw_auto_split_mb": int(self.raw_auto_split_mb_var.get()),
        })
        snap.extras = extras
        try:
            save_snapshot(snap)
        except Exception as e:  # noqa: BLE001
            # 保存偏好失败不影响使用，只写本地 e
            # rror log
            try:
                _log_error_to_disk(e)
            except Exception:
                pass

    def _on_app_close(self) -> None:
        """WM_DELETE_WINDOW 统一关闭流程：
        ① 保存视觉偏好（theme/font/topmost/raw 默认）→ extras
        ② 停周期发送 → 停串口 → close_log_file(写结束标记) → close_save_raw_file
        ③ root.destroy()
        """
        """WM_DELETE_WINDOW 统一关闭流程：..."""
        try:
            self._stop_port_watch()
        except Exception:
            pass
        # ① 偏好
        try:
            self._save_preferences()
        except Exception:
            pass
        # ② 资源释放：先停串口（含周期发送）
        try:
            if self.is_collecting:
                self._stop_serial()
        except Exception:
            pass
        # 日志文件：写结束标记后 close
        try:
            if self.log_file is not None:
                try:
                    self.log_file.write(f"===== 结束记录（共 {getattr(self, 'log_count', 0)} 条） =====\n")
                    self.log_file.flush()
                    self.log_file.close()
                except Exception:
                    pass
                self.log_file = None
        except Exception:
            pass
        # 原始数据文件
        try:
            self._close_save_raw_file()
        except Exception:
            pass
        # ④ destroy
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
        _dlg_w, _dlg_h = 520, 240
        dlg.geometry(f"{_dlg_w}x{_dlg_h}")
        dlg.resizable(False, False)
        dlg.transient(self.root)
        dlg.grab_set()

        x = self.root.winfo_x() + (self.root.winfo_width() - _dlg_w) // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - _dlg_h) // 2
        dlg.geometry(f"+{x}+{y}")

        frm = ttk.Frame(dlg, padding=16)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)

        ttk.Label(frm, text="串口:").grid(row=0, column=0, sticky="w", pady=4)
        port_display_list = []
        for p in ports:
            desc = p.get("description", "")
            if desc and desc != p["device"]:
                port_display_list.append(f'{p["device"]} - {desc}')
            else:
                port_display_list.append(p["device"])

        import re
        def _com_sort_key(item: str):
            m = re.match(r"^COM(\d+)", str(item), re.I)
            if m:
                return (0, int(m.group(1)))
            return (1, str(item).lower())
        port_display_list.sort(key=_com_sort_key)

        port_var = tk.StringVar()
        port_combo = ttk.Combobox(frm, textvariable=port_var, values=port_display_list, width=48, state="readonly")
        _enable_full_combobox(port_combo)
        port_combo.grid(row=0, column=1, sticky="ew", padx=(8, 0), pady=4)
        if port_display_list:
            port_combo.current(0)

        import re
        def _com_sort_key(item: str):
            m = re.match(r"^COM(\d+)", str(item), re.I)
            if m:
                return (0, int(m.group(1)))
            return (1, str(item).lower())
        port_display_list.sort(key=_com_sort_key)

        ttk.Label(frm, text="波特率:").grid(row=1, column=0, sticky="w", pady=4)
        baudrate_var = tk.StringVar(value="115200")
        baud_combo = ttk.Combobox(
            frm, textvariable=baudrate_var,
            values=[9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600, 1000000, 1500000, 2000000, 3000000, 4000000, 5000000, 6000000],
            width=10, state="normal",
        )
        baud_combo.grid(row=1, column=1, sticky="w", padx=(8, 0), pady=4)
        _enable_full_combobox(baud_combo)
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

        RoundedButton(btn_frm, text="确定", command=on_ok).pack(side="left", padx=8)
        RoundedButton(btn_frm, text="取消", command=dlg.destroy).pack(side="left", padx=8)

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


    # ---------- 串口实时 ----------

    def _refresh_ports(self, *, silent: bool = False) -> bool:
        """刷新可用串口列表。

        silent=True：后台轮询时用，列表没变化不刷状态栏。
        返回：列表相对上次是否有变化。
        """
        ports = SerialCollector.list_ports()
        display_list = []
        for p in ports:
            dev = p.get("device", "")
            desc = p.get("description", "")
            if desc and desc != dev:
                display_list.append(f"{dev} - {desc}")
            else:
                display_list.append(dev)

        import re
        def _com_sort_key(item: str):
            m = re.match(r"^COM(\d+)", str(item), re.I)
            if m:
                return (0, int(m.group(1)))
            return (1, str(item).lower())

        display_list.sort(key=_com_sort_key)
        devices = [d.split(" - ")[0].strip() for d in display_list]

        changed = devices != getattr(self, "_last_port_devices", None)
        self._last_port_devices = devices

        cur = (self.port_var.get() or "").strip()
        # 当前显示值对应的设备名（"COM3 - xxx" → "COM3"）
        cur_dev = cur.split(" - ")[0].strip() if cur else ""

        try:
            self.port_combo["values"] = display_list
        except Exception:
            return changed

        if changed:
            try:
                self._sync_port_combo_popdown(display_list)
            except Exception:
                pass
            if not silent:
                self._set_status(f"串口列表已更新（{len(display_list)} 个）")

        if not display_list:
            if cur:
                self.port_var.set("")
            if not silent:
                self._set_status("未找到可用串口")
            return changed

        # 仍在列表里：尽量保持原选项（匹配 device 前缀）
        keep_idx = -1
        if cur_dev:
            for i, disp in enumerate(display_list):
                if disp == cur or disp.startswith(cur_dev + " ") or disp.startswith(cur_dev + "-") or disp == cur_dev:
                    keep_idx = i
                    break
        if keep_idx >= 0:
            try:
                self.port_combo.current(keep_idx)
            except Exception:
                pass
        elif not cur:
            # 启动时无选中 → 选第一项
            try:
                self.port_combo.current(0)
            except Exception:
                pass
        else:
            # 原来的口拔掉了
            try:
                self.port_combo.current(0)
            except Exception:
                self.port_var.set("")
            if not silent:
                self._set_status(f"串口 {cur_dev} 已断开，已切换到 {self.port_var.get()}")

        if changed and not silent:
            self._set_status(f"找到 {len(ports)} 个串口")
        elif changed and silent:
            # 热插拔：轻提示，不打扰
            self._set_status(f"串口列表已更新（{len(ports)} 个）")
        return changed

    def _sync_port_combo_popdown(self, display_list: list) -> None:
        """串口列表变化时：强制收起下拉并刷新 values。
        ttk 展开中的 Listbox 无法可靠热更新，收起后再打开即为最新。
        """
        combo = getattr(self, "port_combo", None)
        if combo is None:
            return
        # 1) 尽量关掉已展开的下拉
        try:
            combo.tk.call("ttk::combobox::Unpost", combo)
        except Exception:
            try:
                combo.event_generate("<Escape>")
            except Exception:
                pass
        try:
            combo.event_generate("<FocusOut>")
        except Exception:
            pass

        # 2) 写入新列表
        try:
            combo["values"] = list(display_list)
        except Exception:
            return

        # 3) 尽量保留当前选中项
        cur = (self.port_var.get() or "").strip()
        cur_dev = cur.split(" - ")[0].strip() if cur else ""
        if not display_list:
            return
        keep_idx = -1
        if cur_dev:
            for i, disp in enumerate(display_list):
                if (
                    disp == cur
                    or disp == cur_dev
                    or disp.startswith(cur_dev + " ")
                    or disp.startswith(cur_dev + "-")
                ):
                    keep_idx = i
                    break
        try:
            if keep_idx >= 0:
                combo.current(keep_idx)
            elif not cur:
                combo.current(0)
        except Exception:
            pass

    def _start_port_watch(self) -> None:
        """启动串口热插拔轮询（启动时先刷一次，之后定时扫）。"""
        self._port_watch_job = None
        self._last_port_devices = None
        try:
            self._refresh_ports(silent=False)
        except Exception:
            pass
        self._schedule_port_watch()

    def _schedule_port_watch(self) -> None:
        try:
            if self._port_watch_job is not None:
                self.root.after_cancel(self._port_watch_job)
        except Exception:
            pass
        self._port_watch_job = self.root.after(1500, self._safe(self._poll_ports))

    def _poll_ports(self) -> None:
        self._port_watch_job = None
        try:
            # 监控中也允许更新列表（不改当前连接，只刷新下拉）
            self._refresh_ports(silent=True)
        except Exception:
            pass
        self._schedule_port_watch()

    def _stop_port_watch(self) -> None:
        job = getattr(self, "_port_watch_job", None)
        if job is not None:
            try:
                self.root.after_cancel(job)
            except Exception:
                pass
            self._port_watch_job = None

    def _toggle_serial(self) -> None:
        """切换串口监控状态。"""
        if self.is_collecting:
            self._stop_serial()
        else:
            self._start_serial()

    def _on_port_change_while_collecting(self, _event=None) -> None:
        """监控中切换串口下拉项：自动停止当前串口并连接到新选中的串口。"""
        if not self.is_collecting:
            return
        new_port_display = self.port_var.get()
        if not new_port_display:
            return
        # 先停止当前串口（清掉线程和句柄，避免新串口连接被旧线程阻塞）
        try:
            self._stop_serial()
        except Exception:
            pass
        # 重新连接到新选中的串口
        try:
            self._start_serial()
        except Exception as e:  # noqa: BLE001
            self._report_error("切换串口失败", e)

    def _on_serial_param_change_while_collecting(self, _event=None) -> None:
        """监控中修改波特率/数据位/停止位：按新参数重新连接。"""
        if not self.is_collecting:
            return
        try:
            baud_s = str(self.baudrate_var.get()).strip()
            baudrate = int(baud_s)
            if baudrate <= 0:
                raise ValueError("baud")
        except Exception:
            messagebox.showwarning("提示", "波特率必须填写正整数（支持自定义，如6M填6000000）")
            return
        try:
            self._stop_serial()
        except Exception:
            pass
        try:
            self._start_serial()
        except Exception as e:
            self._report_error("切换串口参数失败", e)

    def _start_serial(self) -> None:
        """启动串口监控。

        未加载任何协议时也允许启动：作为通用串口助手使用，原始数据按 HEX/ASCII 显示。
        已加载协议时：HEX 模式按协议帧解析，匹配不到帧头的字节同样回退为原始数据显示。
        """
        # 未加载协议：用空 cfg 让 collector 不崩，FrameSynchronizer 检测到无 frame 配置时会直接走 on_raw
        cfg = self.cfg if self.cfg else {}
        no_protocol = not self.cfg
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
                cfg=cfg,
                port=port,
                baudrate=baudrate,
                bytesize=bytesize,
                stopbits=stopbits,
                direction=direction,
                on_frame=on_frame,
                on_error=on_error,
                on_raw=on_raw,
                raw_mode=(is_ascii or self.view_mode_var.get() == "raw"),
                on_tx_sent=on_tx_sent,
            )
            self.collector.start()
        except Exception as e:  # noqa: BLE001
            self._report_error("串口打开失败", e)
            self._set_status("就绪")
            return

        self.is_collecting = True
        try:
            self.start_btn.configure(text="✓ 停止监控", style="Danger.TButton")
        except Exception:
            self.start_btn.configure(text="停止监控")
        mode_label = "ASCII" if is_ascii else "HEX"
        # 无协议时状态栏追加提示，告知用户当前为通用串口模式（原始数据直显）
        proto_tag = " (无协议·通用模式)" if no_protocol else ""

        if self.save_raw_enabled_var.get():
            self._open_save_raw_file()
            self._set_status(f"监控中: {port} @ {baudrate} ({mode_label}){proto_tag} - 保存原始数据")
        else:
            self._set_status(f"监控中: {port} @ {baudrate} ({mode_label}){proto_tag}")

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
                    self._update_save_raw_btn_style()
                    return
                self.save_raw_path_var.set(path)
            if self.is_collecting:
                self._open_save_raw_file()
                self._set_status(f"原始数据保存开启: {self.save_raw_path_var.get()}")
        else:
            self._close_save_raw_file()
            self._set_status("原始数据保存已关闭")
        self._update_save_raw_btn_style()

    def _update_save_raw_btn_style(self) -> None:
        """根据 save_raw_enabled_var 更新按钮样式。"""
        if hasattr(self, "save_raw_btn") and self.save_raw_btn is not None:
            if self.save_raw_enabled_var.get():
                self.save_raw_btn.configure(text="停止存储数据", style="CompactDanger.TButton")
            else:
                self.save_raw_btn.configure(text="开始存储数据", style="CompactPrimary.TButton")

    def _toggle_save_raw_btn(self) -> None:
        """点击保存原始数据按钮时切换状态。"""
        self.save_raw_enabled_var.set(not self.save_raw_enabled_var.get())
        self._on_save_raw_toggle()
        # trace_add 会自动触发 _update_save_raw_btn_style()

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
            self.start_btn.configure(text="○ 开始监控", style="Primary.TButton")
        except Exception:
            self.start_btn.configure(text="开始监控")
        self._set_status("已停止")

    # ---------- UI 队列处理 ----------

    def _process_ui_queue(self) -> None:
        """处理 UI 队列。最外层统一兜底，不允许堆栈冒泡到 Tk mainloop。"""
        try:
            while self._ui_queue:
                kind, args = self._ui_queue.popleft()
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
        """显示原始数据：ASCII 模式按文本显示；HEX 模式按十六进制字符串显示。"""
        self.serial_text.configure(state="normal")
        self._trim_display()
        ts_str = datetime.fromtimestamp(ts).strftime("%H:%M:%S.%f")[:-3]
        is_hex_mode = bool(self.hex_format_var.get())
        if is_hex_mode:
            # HEX 模式：把原始字节转成 "41 42 43" 形式，按行宽 16 字节折行显示
            hex_str = " ".join(f"{b:02X}" for b in data)
            # 按 16 字节（48 字符 + 15 空格 = 47 字符）折行
            tokens = hex_str.split(" ")
            chunk_size = 16
            for i in range(0, len(tokens), chunk_size):
                chunk = " ".join(tokens[i:i + chunk_size])
                self.serial_text.insert("end", f"[{ts_str}] ", "ts")
                self.serial_text.insert("end", f"{chunk}\n", "raw")
        else:
            # ASCII 模式：直接按文本显示（不可打印字符替换为 .）
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

# Windows 高 DPI：先声明感知，再创建窗口，避免字体被拉伸发虚
    if sys.platform == "win32":
        try:
            from ctypes import windll
            try:
                # Per-Monitor V2（Win10 1703+）
                windll.shcore.SetProcessDpiAwareness(2)
            except Exception:
                try:
                    windll.shcore.SetProcessDpiAwareness(1)
                except Exception:
                    try:
                        windll.user32.SetProcessDPIAware()
                    except Exception:
                        pass
        except Exception:
            pass

    try:
        root = tk.Tk()
        try:
            # 按系统 DPI 设 Tk 缩放，避免非整数拉伸发虚
            if sys.platform == "win32":
                from ctypes import windll
                dpi = windll.user32.GetDpiForSystem()
                root.tk.call("tk", "scaling", dpi / 72.0)
        except Exception:
            pass
    except Exception as e:
        # 极端情况：DISPLAY / Tk 初始化失败
        friendly, _ = classify_protocol_error(e)
        log_path_a = _log_error_to_disk(e)
        log_path_b = _write_crash_log_gui(e)
        log_path = log_path_b or log_path_a
        print(f"[错误] 无法启动 GUI：{friendly}", file=sys.stderr)
        if log_path is not None:
            print(f"       详细日志已写入: {log_path}", file=sys.stderr)
        return 1

    app: ProtocolParserApp | None = None
    try:
        app = ProtocolParserApp(root, monitor_port=monitor_port, monitor_baud=monitor_baud)
        # ProtocolParserApp.__init__ 内部已经调用 self.root.protocol("WM_DELETE_WINDOW", _on_app_close)，
        # 覆盖会导致保存偏好/快照/关日志/关串口的逻辑丢失，这里不得再绑定。

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
                    # 运行时严重错误也写 exe 同级 crash log，方便用户夜间抓包查
                    try:
                        _write_crash_log_gui(e)
                    except Exception:
                        pass
                    break

        _safe_mainloop()
        return 0
    except (SystemExit, KeyboardInterrupt):
        raise
    except BaseException as e:  # noqa: BLE001  启动阶段兜底：任何错误都写 exe 同级 crash.log + 尽力弹窗
        log_path_a = _log_error_to_disk(e)
        log_path_b = _write_crash_log_gui(e)
        log_path = log_path_b or log_path_a
        try:
            friendly, _ = classify_protocol_error(e)
            try:
                msg = f"{friendly}"
                if log_path is not None:
                    msg += f"\n\n崩溃日志：{log_path}\n（打包闪退请把此 log 发给开发者）"
                messagebox.showerror("启动失败", msg)
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
