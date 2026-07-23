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
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

# 让 exe 也能找到 protocol_parser 包
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from protocol_parser import (  # noqa: E402
    ParseResult,
    ProtocolError,
    load_protocol,
    parse_frame,
    parse_hex_input,
    to_hex,
)
from protocol_parser.serial_collector import FrameSynchronizer, SerialCollector  # noqa: E402


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


# ---------- 主窗口 ----------


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
        self.root.title("协议解析工具 V3.0")
        self.root.geometry("1100x720")
        self.root.minsize(900, 600)

        # 置顶状态
        self.topmost_var = tk.BooleanVar(value=False)

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

        # 日志
        self.log_path: Path | None = None
        self.log_file = None
        self.log_count = 0

        # 原始数据保存
        self.save_raw_enabled_var = tk.BooleanVar(value=True)
        import os
        default_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
        self.save_raw_path_var = tk.StringVar(value=default_path)
        from datetime import datetime
        default_name = datetime.now().strftime("serial_data_%Y%m%d_%H%M%S")
        self.save_raw_filename_var = tk.StringVar(value=default_name)
        self.save_raw_file = None
        self.save_raw_current_size = 0
        self.save_raw_max_size = 50 * 1024 * 1024
        self.save_raw_count = 0
        self._save_raw_active = False

        # 显示缓冲区限制（防止内存溢出）
        self.max_display_lines = 50000

        # 主布局
        self._build_ui()
        self._load_protocols()

        # 定时刷新 UI 队列
        self._ui_queue: list[tuple[str, tuple]] = []
        self.root.after(100, self._process_ui_queue)

        # 若是 monitor 启动方式：自动跳转到「串口实时」tab + 选中指定串口/波特率
        if self._monitor_port:
            self._apply_monitor_args()

    # ---------- UI 构建 ----------

    def _build_ui(self) -> None:
        # 顶部工具栏：左右分区，中间弹性填充
        top = ttk.Frame(self.root, padding=8)
        top.pack(fill="x")
        top.columnconfigure(0, weight=1)

        # 左区：协议操作
        left = ttk.Frame(top)
        left.grid(row=0, column=0, sticky="w")
        ttk.Label(left, text="产品协议:").pack(side="left", padx=2)
        self.product_combo = ttk.Combobox(left, textvariable=self.product_var, width=28, state="readonly")
        self.product_combo.pack(side="left", padx=4)
        self.product_combo.bind("<<ComboboxSelected>>", self._on_product_change)
        ttk.Button(left, text="刷新", command=self._load_protocols).pack(side="left", padx=2)
        ttk.Button(left, text="导入Word协议", command=self._import_docx).pack(side="left", padx=2)
        ttk.Button(left, text="查看协议", command=self._show_protocol).pack(side="left", padx=2)

        # 右区：功能按钮
        right = ttk.Frame(top)
        right.grid(row=0, column=1, sticky="e")
        top.columnconfigure(1, weight=1)
        ttk.Button(right, text="添加串口", command=self._add_serial_port).pack(side="left", padx=2)
        ttk.Button(right, text="保存日志", command=self._choose_log).pack(side="left", padx=2)
        ttk.Button(right, text="清空", command=self._clear_output).pack(side="left", padx=2)
        ttk.Checkbutton(right, text="置顶", variable=self.topmost_var, command=self._toggle_topmost).pack(side="left", padx=4)

        # 中间内容区：只留串口实时（粘贴解析已删除）
        self.main_panel = ttk.Frame(self.root)
        self.main_panel.pack(fill="both", expand=True, padx=8, pady=4)
        self.main_panel.columnconfigure(0, weight=1)
        self.main_panel.rowconfigure(1, weight=1)
        self._build_serial_panel(self.main_panel)

        # 底部状态栏
        self.status_var = tk.StringVar(value="就绪")
        status = ttk.Frame(self.root, relief="sunken", padding=4)
        status.pack(fill="x", side="bottom")
        status.columnconfigure(0, weight=1)
        ttk.Label(status, textvariable=self.status_var, anchor="w").grid(row=0, column=0, sticky="w")
        self.stats_var = tk.StringVar(value="")
        ttk.Label(status, textvariable=self.stats_var, anchor="e").grid(row=0, column=1, sticky="e")

    def _build_serial_panel(self, parent: tk.Misc) -> None:
        """构建串口实时主面板（直接嵌在主窗口里，不再包 Notebook Tab）。"""
        # 串口配置区
        cfg_frame = ttk.LabelFrame(parent, text="串口配置", padding=8)
        cfg_frame.grid(row=0, column=0, sticky="new", padx=4, pady=4)
        cfg_frame.columnconfigure(0, weight=1)

        row1 = ttk.Frame(cfg_frame)
        row1.grid(row=0, column=0, sticky="ew")
        ttk.Label(row1, text="串口:").pack(side="left", padx=2)
        self.port_combo = ttk.Combobox(row1, textvariable=self.port_var, width=32, state="readonly")
        self.port_combo.pack(side="left", padx=4)
        ttk.Button(row1, text="刷新", command=self._refresh_ports).pack(side="left", padx=2)

        ttk.Label(row1, text="波特率:").pack(side="left", padx=(12, 2))
        # 波特率改为可手动输入自定义值（含6000000=6M支持）
        self.baudrate_combo = ttk.Combobox(
            row1, textvariable=self.baudrate_var,
            values=[9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600, 1000000, 1500000, 2000000, 3000000, 4000000, 5000000, 6000000],
            width=10, state="normal",  # state=normal 允许手动输入任意数字
        )
        self.baudrate_combo.pack(side="left", padx=4)
        ttk.Label(row1, text="(支持自定义, 含6M=6000000)", foreground="#888").pack(side="left", padx=2)

        ttk.Label(row1, text="数据位:").pack(side="left", padx=(12, 2))
        ttk.Combobox(row1, textvariable=self.bytesize_var, values=[5, 6, 7, 8], width=4, state="readonly").pack(side="left", padx=4)

        ttk.Label(row1, text="停止位:").pack(side="left", padx=(12, 2))
        ttk.Combobox(row1, textvariable=self.stopbits_var, values=[1, 1.5, 2], width=4, state="readonly").pack(side="left", padx=4)

        self.start_btn = ttk.Button(row1, text="开始监控", command=self._toggle_serial)
        self.start_btn.pack(side="left", padx=(12, 4))

        row2 = ttk.Frame(cfg_frame)
        row2.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        self.detail_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(row2, text="详细模式", variable=self.detail_var).pack(side="left", padx=2)

        # 发送方：两个单选 Radiobutton
        self._sender_frame = ttk.Frame(row2)
        self._sender_frame.pack(side="left", padx=(16, 2))
        ttk.Label(self._sender_frame, text="发送方:").pack(side="left", padx=2)
        self.sender_module = ttk.Radiobutton(self._sender_frame, text="模组发送", variable=self.serial_sender_var, value="模组发送")
        self.sender_module.pack(side="left", padx=4)
        self.sender_mcu = ttk.Radiobutton(self._sender_frame, text="MCU发送", variable=self.serial_sender_var, value="MCU发送")
        self.sender_mcu.pack(side="left", padx=4)
        self.sender_labels = [self._sender_frame]

        # 数据格式：单选勾 HEX 格式
        self._hex_chk = ttk.Checkbutton(
            row2, text="HEX格式", variable=self.hex_format_var,
            command=self._on_hex_format_change,
        )
        self._hex_chk.pack(side="left", padx=(16, 2))
        ttk.Label(row2, text="(不勾选=ASCII)", foreground="#888").pack(side="left", padx=2)

        self.autoscroll_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(row2, text="自动滚动", variable=self.autoscroll_var).pack(side="left", padx=(16, 2))

        row3 = ttk.Frame(cfg_frame)
        row3.grid(row=2, column=0, sticky="ew", pady=(6, 0))
        ttk.Checkbutton(row3, text="保存原始数据", variable=self.save_raw_enabled_var, command=self._on_save_raw_toggle).pack(side="left", padx=2)
        ttk.Label(row3, text="路径:").pack(side="left", padx=(8, 2))
        self.save_raw_path_entry = ttk.Entry(row3, textvariable=self.save_raw_path_var, width=20, state="readonly")
        self.save_raw_path_entry.pack(side="left", padx=2)
        _bind_text_widget_menu(self.save_raw_path_entry, readonly=True)
        ttk.Button(row3, text="选择", command=self._choose_save_raw_path).pack(side="left", padx=2)
        ttk.Label(row3, text="文件名:").pack(side="left", padx=(8, 2))
        _file_entry = ttk.Entry(row3, textvariable=self.save_raw_filename_var, width=15)
        _file_entry.pack(side="left", padx=2)
        _bind_text_widget_menu(_file_entry, readonly=False)
        ttk.Label(row3, text="(.dat格式，超过50MB自动分割)").pack(side="left", padx=(8, 2))

        # 实时输出区
        out_frame = ttk.LabelFrame(parent, text="实时数据", padding=4)
        out_frame.grid(row=1, column=0, sticky="nsew", padx=4, pady=4)
        out_frame.columnconfigure(0, weight=1)
        out_frame.rowconfigure(0, weight=1)

        self.serial_text = tk.Text(out_frame, font=("Consolas", 10), wrap="word", state="disabled")
        self.serial_text.grid(row=0, column=0, sticky="nsew")
        _bind_text_widget_menu(self.serial_text, readonly=True)

        scroll = ttk.Scrollbar(out_frame, command=self.serial_text.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.serial_text.configure(yscrollcommand=scroll.set)

        self.serial_text.tag_configure("ts", foreground="#666666")
        self.serial_text.tag_configure("ok", foreground="#008800")
        self.serial_text.tag_configure("err", foreground="#CC0000")
        self.serial_text.tag_configure("cmd", foreground="#0066CC", font=("Consolas", 10, "bold"))
        self.serial_text.tag_configure("field", foreground="#444444")
        self.serial_text.tag_configure("raw", foreground="#888888")
        self.serial_text.tag_configure("direction", foreground="#E65100", font=("Consolas", 10, "bold"))
        self.serial_text.tag_configure("pid", foreground="#AD1457", font=("Consolas", 10, "bold"))
        self.serial_text.tag_configure("model", foreground="#00695C", font=("Consolas", 10, "bold"))
        self.serial_text.tag_configure("raw_data", foreground="#006064")

        # 初始状态：根据 HEX 格式勾选状态决定是否显示发送方
        self._on_hex_format_change()

        # Radiobutton 和 Checkbutton 改值时同步给 collector
        self.serial_sender_var.trace_add("write", self._on_serial_sender_change)
        self.hex_format_var.trace_add("write", self._on_hex_format_sync_collector)

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
            except ProtocolError as e:
                messagebox.showerror("协议加载失败", str(e))
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
        except Exception as e:
            import traceback
            messagebox.showerror("导入失败", f"{str(e)}\n\n{traceback.format_exc()}")
            return

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
        """HEX格式勾选变化：未勾选=ASCII模式，隐藏并禁用发送方；勾选=HEX模式，显示发送方。"""
        hex_checked = bool(self.hex_format_var.get())
        try:
            state_txt = "normal" if hex_checked else "disabled"
            for item in (self.sender_module, self.sender_mcu):
                try:
                    item.configure(state=state_txt)
                except Exception:
                    pass
            if hex_checked:
                self._sender_frame.pack(side="left", padx=(16, 2), before=self._hex_chk)
            else:
                self._sender_frame.pack_forget()
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
        self.root.title(f"串口监控 - {self._monitor_port} @ {self._monitor_baud}")

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
            self._ui_queue.append(("serial_frame", (result, ts)))
            self._write_raw_data(frame.raw, ts)

        def on_error(msg):
            self._ui_queue.append(("serial_error", (msg,)))

        def on_raw(data, ts):
            self._ui_queue.append(("serial_raw", (data, ts)))
            self._write_raw_data(data, ts)

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
            )
            self.collector.start()
        except Exception as e:
            messagebox.showerror("串口打开失败", str(e))
            self._set_status("就绪")
            return

        self.is_collecting = True
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

    def _write_raw_data(self, data: bytes, ts: float) -> None:
        """写入原始数据到文件，超过50MB自动分割。"""
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
                for line in lines:
                    if line.strip():
                        self.save_raw_file.write(f"[{ts_str}] {line}\n")
                self.save_raw_file.flush()
                self.save_raw_current_size += sum(len(f"[{ts_str}] {line}\n") for line in lines if line.strip())
            else:
                hex_str = " ".join(f"{b:02X}" for b in data)
                line = f"[{ts_str}] {hex_str}\n"
                self.save_raw_file.write(line)
                self.save_raw_file.flush()
                self.save_raw_current_size += len(line)

            if self.save_raw_current_size >= self.save_raw_max_size:
                self.save_raw_count += 1
                self._open_save_raw_file()
        except Exception as e:
            if self._save_raw_active and self.save_raw_file:
                try:
                    messagebox.showerror("保存错误", f"写入文件失败: {e}")
                    self.save_raw_enabled_var.set(False)
                except Exception:
                    pass
                self._close_save_raw_file()

    def _stop_serial(self) -> None:
        """停止串口监控。"""
        if self.collector:
            self.collector.stop()
            self.collector = None
        self.is_collecting = False
        self._close_save_raw_file()
        self.save_raw_count = 0
        self.start_btn.configure(text="开始监控")
        self._set_status("已停止")

    # ---------- UI 队列处理 ----------

    def _process_ui_queue(self) -> None:
        """处理 UI 队列。"""
        try:
            while self._ui_queue:
                kind, args = self._ui_queue.pop(0)
                try:
                    if kind == "serial_frame":
                        self._display_serial_frame(*args)
                    elif kind == "serial_raw":
                        self._display_raw_data(*args)
                    elif kind == "serial_error":
                        self._display_serial_error(*args)
                except Exception as e:
                    import traceback
                    try:
                        self._write_log(f"UI队列处理异常 kind={kind}: {e}\n{traceback.format_exc()}")
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
            self._write_log(result, ts)

        if self.collector and self.collector.sync:
            self.stats_var.set(f"帧 {self.collector.sync.frame_count}  错误 {self.collector.sync.error_count}  缓冲 {self.collector.sync.partial_bytes}B")

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

    def _write_log(self, result: ParseResult, ts: float | None = None) -> None:
        """写入日志。"""
        if not self.log_file:
            return
        ts = ts or time.time()
        ts_str = datetime.fromtimestamp(ts).strftime("%H:%M:%S.%f")[:-3]
        ok_tag = "OK" if (result.error is None and result.checksum_ok is not False) else "ERR"
        cs = "✓" if result.checksum_ok else "✗" if result.checksum_ok is False else " "
        self.log_file.write(f"[{ts_str}] {ok_tag} {cs} {result.cmd_code} {result.cmd_name}")
        # HEX未勾选（ASCII）时发送方不生效，只在 HEX 模式写发送方标签
        sender_label = ""
        if bool(self.hex_format_var.get()):
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
        if self.is_collecting:
            self._stop_serial()
        self._close_save_raw_file()
        if self.log_file:
            try:
                self.log_file.write(f"===== 结束记录（共 {self.log_count} 条） =====\n")
                self.log_file.close()
            except Exception:
                pass
            self.log_file = None
        self.root.destroy()


# ---------- 启动 ----------

def main():
    """主入口。"""
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

    root = tk.Tk()
    app = ProtocolParserApp(root, monitor_port=monitor_port, monitor_baud=monitor_baud)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
