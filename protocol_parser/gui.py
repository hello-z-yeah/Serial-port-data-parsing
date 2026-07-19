"""协议解析工具 GUI（Tkinter）。

特性：
- 双击打开即用，无需命令行
- 两种模式：粘贴解析 / 串口实时监控
- 协议产品下拉选择
- 解析结果树形展示
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


def init_protocol_dir() -> None:
    """初始化协议目录：第一次运行时从内置资源复制初始协议。

    确保打包成 exe 后，用户打开就能看到默认协议（如 v3_serial）。
    """
    user_dir = get_protocol_dir()

    if any(user_dir.glob("*.json")):
        return

    bundled = resource_path("product")
    if bundled.exists():
        import shutil
        for f in bundled.glob("*.json"):
            dest = user_dir / f.name
            if not dest.exists():
                shutil.copy2(f, dest)


# ---------- 主窗口 ----------

class ProtocolParserApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("协议解析工具 V3.0")
        self.root.geometry("1100x720")
        self.root.minsize(900, 600)

        self.cfg: dict | None = None
        self.product_var = tk.StringVar()
        self.mode_var = tk.StringVar(value="paste")  # paste / serial

        # 串口相关
        self.port_var = tk.StringVar()
        self.baudrate_var = tk.IntVar(value=115200)
        self.collector: SerialCollector | None = None
        self.serial_thread: threading.Thread | None = None
        self.is_collecting = False

        # 日志
        self.log_path: Path | None = None
        self.log_file = None
        self.log_count = 0

        # 主布局
        self._build_ui()
        self._load_protocols()

        # 定时刷新 UI 队列（用于子线程→主线程）
        self._ui_queue: list[tuple[str, tuple]] = []
        self.root.after(100, self._process_ui_queue)

    # ---------- UI 构建 ----------

    def _build_ui(self) -> None:
        # 顶部：产品选择 + 模式切换
        top = ttk.Frame(self.root, padding=8)
        top.pack(fill="x")

        ttk.Label(top, text="产品协议:").grid(row=0, column=0, padx=2)
        self.product_combo = ttk.Combobox(top, textvariable=self.product_var, width=30, state="readonly")
        self.product_combo.grid(row=0, column=1, padx=4)
        self.product_combo.bind("<<ComboboxSelected>>", self._on_product_change)

        ttk.Button(top, text="刷新", command=self._load_protocols).grid(row=0, column=2, padx=4)
        ttk.Button(top, text="导入Word协议", command=self._import_docx).grid(row=0, column=3, padx=4)
        ttk.Button(top, text="查看协议", command=self._show_protocol).grid(row=0, column=4, padx=4)

        ttk.Separator(top, orient="vertical").grid(row=0, column=5, padx=10, sticky="ns")

        ttk.Label(top, text="模式:").grid(row=0, column=6)
        ttk.Radiobutton(top, text="粘贴解析", variable=self.mode_var, value="paste", command=self._switch_mode).grid(row=0, column=7)
        ttk.Radiobutton(top, text="串口实时", variable=self.mode_var, value="serial", command=self._switch_mode).grid(row=0, column=8)

        ttk.Button(top, text="保存日志", command=self._choose_log).grid(row=0, column=9, padx=8)
        ttk.Button(top, text="清空", command=self._clear_output).grid(row=0, column=10, padx=2)

        # 中间内容区（用 Notebook 切换两种模式）
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=8, pady=4)

        self._build_paste_tab()
        self._build_serial_tab()

        # 底部状态栏
        self.status_var = tk.StringVar(value="就绪")
        status = ttk.Frame(self.root, relief="sunken", padding=4)
        status.pack(fill="x", side="bottom")
        ttk.Label(status, textvariable=self.status_var, anchor="w").pack(side="left")
        self.stats_var = tk.StringVar(value="")
        ttk.Label(status, textvariable=self.stats_var, anchor="e").pack(side="right")

    def _build_paste_tab(self) -> None:
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="粘贴解析")
        self.paste_tab = tab

        # 输入区
        input_frame = ttk.LabelFrame(tab, text="Hex 数据（一行一条，支持空格/逗号分隔）", padding=8)
        input_frame.pack(fill="x", padx=4, pady=4)

        self.input_text = tk.Text(input_frame, height=4, font=("Consolas", 10), wrap="word")
        self.input_text.pack(fill="x", expand=True)
        self.input_text.bind("<Control-Return>", lambda e: self._parse_paste())

        btns = ttk.Frame(input_frame)
        btns.pack(fill="x", pady=(4, 0))
        ttk.Button(btns, text="解析 (Ctrl+Enter)", command=self._parse_paste).pack(side="left")
        ttk.Button(btns, text="清空输入", command=lambda: self.input_text.delete("1.0", "end")).pack(side="left", padx=4)
        ttk.Label(btns, text="方向:").pack(side="left", padx=(8, 0))
        self.direction_var = tk.StringVar(value="auto")
        ttk.Combobox(btns, textvariable=self.direction_var, values=["auto", "request", "response"], width=10, state="readonly").pack(side="left")

        # 输出区
        out_frame = ttk.LabelFrame(tab, text="解析结果", padding=4)
        out_frame.pack(fill="both", expand=True, padx=4, pady=4)

        self.output_text = tk.Text(out_frame, font=("Consolas", 10), wrap="word", state="disabled")
        self.output_text.pack(fill="both", expand=True, side="left")

        scroll = ttk.Scrollbar(out_frame, command=self.output_text.yview)
        scroll.pack(side="right", fill="y")
        self.output_text.configure(yscrollcommand=scroll.set)

        # 配色
        self.output_text.tag_configure("header", foreground="#0066CC", font=("Consolas", 10, "bold"))
        self.output_text.tag_configure("ok", foreground="#008800")
        self.output_text.tag_configure("err", foreground="#CC0000")
        self.output_text.tag_configure("field", foreground="#444444")
        self.output_text.tag_configure("raw", foreground="#888888")

    def _build_serial_tab(self) -> None:
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="串口实时")
        self.serial_tab = tab

        # 串口配置区
        cfg_frame = ttk.LabelFrame(tab, text="串口配置", padding=8)
        cfg_frame.pack(fill="x", padx=4, pady=4)

        ttk.Label(cfg_frame, text="串口:").grid(row=0, column=0, padx=2)
        self.port_combo = ttk.Combobox(cfg_frame, textvariable=self.port_var, width=18)
        self.port_combo.grid(row=0, column=1, padx=4)
        ttk.Button(cfg_frame, text="刷新", command=self._refresh_ports).grid(row=0, column=2, padx=2)

        ttk.Label(cfg_frame, text="波特率:").grid(row=0, column=3, padx=(10, 2))
        ttk.Combobox(cfg_frame, textvariable=self.baudrate_var, values=[9600, 19200, 38400, 57600, 115200, 230400, 460800], width=10).grid(row=0, column=4, padx=4)

        self.start_btn = ttk.Button(cfg_frame, text="开始监控", command=self._toggle_serial)
        self.start_btn.grid(row=0, column=5, padx=10)

        ttk.Label(cfg_frame, text="显示:").grid(row=0, column=6, padx=(10, 2))
        self.detail_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(cfg_frame, text="详细模式", variable=self.detail_var).grid(row=0, column=7)

        ttk.Label(cfg_frame, text="自动滚动:").grid(row=0, column=8, padx=(10, 2))
        self.autoscroll_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(cfg_frame, variable=self.autoscroll_var).grid(row=0, column=9)

        # 实时输出区
        out_frame = ttk.LabelFrame(tab, text="实时数据", padding=4)
        out_frame.pack(fill="both", expand=True, padx=4, pady=4)

        self.serial_text = tk.Text(out_frame, font=("Consolas", 10), wrap="word", state="disabled")
        self.serial_text.pack(fill="both", expand=True, side="left")
        scroll = ttk.Scrollbar(out_frame, command=self.serial_text.yview)
        scroll.pack(side="right", fill="y")
        self.serial_text.configure(yscrollcommand=scroll.set)

        self.serial_text.tag_configure("ts", foreground="#666666")
        self.serial_text.tag_configure("ok", foreground="#008800")
        self.serial_text.tag_configure("err", foreground="#CC0000")
        self.serial_text.tag_configure("cmd", foreground="#0066CC", font=("Consolas", 10, "bold"))
        self.serial_text.tag_configure("field", foreground="#444444")
        self.serial_text.tag_configure("raw", foreground="#888888")

    # ---------- 协议加载 ----------

    def _load_protocols(self) -> None:
        d = get_protocol_dir()
        products: list[tuple[str, str]] = []  # (product, file)
        if d.exists():
            for f in sorted(d.glob("*.json")):
                try:
                    cfg = load_protocol(f)
                    products.append((cfg.get("product", f.stem), str(f)))
                except ProtocolError:
                    continue
        self._product_files = {p: fp for p, fp in products}
        self.product_combo["values"] = [p for p, _ in products]
        if products:
            self.product_combo.current(0)
            self._load_product_cfg()
        self._set_status(f"已加载 {len(products)} 个协议（目录: {d}）")

    def _load_product_cfg(self) -> None:
        product = self.product_var.get()
        fp = self._product_files.get(product)
        if not fp:
            return
        try:
            self.cfg = load_protocol(fp)
            self._set_status(f"已加载协议: {product}（{len(self.cfg.get('commands', []))} 条命令）")
        except ProtocolError as e:
            self.cfg = None
            messagebox.showerror("协议加载失败", str(e))

    def _on_product_change(self, event=None) -> None:
        self._load_product_cfg()

    def _import_docx(self) -> None:
        """导入 Word 协议文档，自动转为 JSON。"""
        # 检查 python-docx
        try:
            from protocol_parser.docx_importer import (
                HAS_DOCX,
                ImporterError,
                check_docx_available,
                import_and_save,
            )
        except ImportError as e:
            messagebox.showerror("错误", f"导入模块加载失败: {e}")
            return

        if not check_docx_available():
            messagebox.showerror(
                "缺少依赖",
                "导入 Word 文档需要 python-docx 库。\n\n"
                "请在命令行执行：\n"
                "  pip install python-docx -i https://pypi.tuna.tsinghua.edu.cn/simple\n\n"
                "安装后重新启动本程序。",
            )
            return

        # 选择文件
        path = filedialog.askopenfilename(
            title="选择 Word 协议文档",
            filetypes=[
                ("Word 文档", "*.docx"),
                ("Word 97-2003", "*.doc"),
                ("所有文件", "*.*"),
            ],
        )
        if not path:
            return

        # 输入产品名（可选）
        from tkinter import simpledialog
        default_name = Path(path).stem
        product_name = simpledialog.askstring(
            "产品名称",
            "请输入产品名称（用作 JSON 文件名，留空则从文档自动识别）：",
            initialvalue=default_name,
            parent=self.root,
        )
        # simpledialog 返回 None 表示用户取消；空字符串表示留空
        if product_name is None:
            return

        # 执行导入
        self._set_status(f"正在导入 {Path(path).name}...")
        self.root.update()

        try:
            protocols_dir = get_protocol_dir()
            protocols_dir.mkdir(parents=True, exist_ok=True)
            cfg, out_path = import_and_save(
                path,
                protocols_dir,
                product_name=product_name or None,
            )
        except ImporterError as e:
            messagebox.showerror("导入失败", str(e))
            self._set_status("导入失败")
            return
        except Exception as e:
            messagebox.showerror("导入失败", f"未知错误: {e}")
            self._set_status("导入失败")
            return

        # 刷新下拉框
        self._load_protocols()

        # 自动选中新导入的产品
        product = cfg.get("product", "")
        values = list(self.product_combo["values"])
        if product in values:
            self.product_var.set(product)
            self._load_product_cfg()

        # 显示导入结果摘要
        cmd_count = len(cfg.get("commands", []))
        attr_count = len(cfg.get("attributes", {}))
        messagebox.showinfo(
            "导入成功",
            f"已成功导入协议：\n\n"
            f"产品: {product}\n"
            f"命令数: {cmd_count}\n"
            f"属性数: {attr_count}\n"
            f"保存到: {out_path}\n\n"
            f"已自动选择该产品，可以直接开始解析数据。",
        )

    def _show_protocol(self) -> None:
        if not self.cfg:
            messagebox.showwarning("提示", "请先选择产品协议")
            return
        win = tk.Toplevel(self.root)
        win.title(f"协议详情 - {self.cfg.get('product', '')}")
        win.geometry("700x600")
        text = tk.Text(win, font=("Consolas", 10), wrap="word")
        text.pack(fill="both", expand=True, side="left")
        sb = ttk.Scrollbar(win, command=text.yview)
        sb.pack(side="right", fill="y")
        text.configure(yscrollcommand=sb.set)

        cfg = self.cfg
        text.insert("end", f"产品: {cfg.get('product', '')}\n", "header")
        text.insert("end", f"说明: {cfg.get('description', '')}\n\n")

        frame = cfg.get("frame", {})
        text.insert("end", "【帧结构】\n", "header")
        text.insert("end", f"  帧头: {frame.get('header', '?')} ({frame.get('header_size', 2)}B)\n")
        text.insert("end", f"  版本: {frame.get('ver', '?')}\n")
        if frame.get("checksum"):
            cs = frame["checksum"]
            text.insert("end", f"  校验: {cs.get('algorithm', '?')} ({cs.get('length', 1)}B)\n")
        text.insert("end", "\n")

        text.insert("end", "【命令列表】\n", "header")
        for cmd in cfg.get("commands", []):
            text.insert("end", f"  {cmd['cmd_code']:<8} {cmd['name']}\n", "cmd")
            if cmd.get("description"):
                text.insert("end", f"            {cmd['description']}\n", "field")
            if "request" in cmd:
                text.insert("end", f"            请求: {cmd['request'].get('format', '?')} - {cmd['request'].get('name', '')}\n", "raw")
            if "response" in cmd:
                text.insert("end", f"            响应: {cmd['response'].get('format', '?')} - {cmd['response'].get('name', '')}\n", "raw")

        attrs = cfg.get("attributes", {})
        if attrs:
            text.insert("end", f"\n【属性表（{len(attrs)} 项）】\n", "header")
            for aid, a in attrs.items():
                text.insert("end", f"  {aid:<6} {a.get('name', '')}\n", "cmd")
                text.insert("end", f"            类型: typeid={a.get('typeid', '?')}, 访问: {a.get('access', '?')}\n", "raw")
                if a.get("enum"):
                    text.insert("end", "            枚举:\n", "field")
                    for k, v in a["enum"].items():
                        text.insert("end", f"              {k}: {v}\n", "field")
                elif a.get("unit") or a.get("range"):
                    extra = []
                    if a.get("unit"):
                        extra.append(f"单位 {a['unit']}")
                    if a.get("range"):
                        extra.append(f"范围 {a['range']}")
                    text.insert("end", f"            {' '.join(extra)}\n", "field")

        text.configure(state="disabled")

    # ---------- 模式切换 ----------

    def _switch_mode(self) -> None:
        mode = self.mode_var.get()
        if mode == "paste":
            self.notebook.select(self.paste_tab)
        else:
            self.notebook.select(self.serial_tab)

    # ---------- 粘贴解析 ----------

    def _parse_paste(self) -> None:
        if not self.cfg:
            messagebox.showwarning("提示", "请先选择产品协议")
            return
        content = self.input_text.get("1.0", "end").strip()
        if not content:
            return
        lines = [l.strip() for l in content.split("\n") if l.strip() and not l.strip().startswith("#")]
        if not lines:
            return

        direction = self.direction_var.get()
        direction = None if direction == "auto" else direction
        sync = FrameSynchronizer(self.cfg)
        results: list[tuple[ParseResult, str]] = []

        for idx, line in enumerate(lines, 1):
            try:
                data = parse_hex_input(line)
                result = parse_frame(data, self.cfg, direction=direction)
                results.append((result, ""))
            except ProtocolError as e:
                # 整行解析失败，尝试作为字节流帧同步
                try:
                    data = parse_hex_input(line)
                    frames = sync.feed(data)
                    if frames:
                        for frame in frames:
                            r = parse_frame(frame.raw, self.cfg)
                            results.append((r, f"（字节流同步：第 {idx} 行）"))
                    else:
                        results.append((ParseResult(
                            product=self.cfg.get("product", ""),
                            raw_hex=line,
                            cmd_code="",
                            cmd_name="解析失败",
                            direction="",
                            description="",
                            error=f"[行 {idx}] 无法识别为完整帧",
                        ), ""))
                except ProtocolError as e2:
                    results.append((ParseResult(
                        product=self.cfg.get("product", ""),
                        raw_hex=line,
                        cmd_code="",
                        cmd_name="解析失败",
                        direction="",
                        description="",
                        error=f"[行 {idx}] {e2}",
                    ), ""))

        self._display_paste_results(results)
        self._set_status(f"已解析 {len(results)} 条指令")

    def _display_paste_results(self, results: list[tuple[ParseResult, str]]) -> None:
        self.output_text.configure(state="normal")
        for result, note in results:
            ts = datetime.now().strftime("%H:%M:%S")
            self.output_text.insert("end", f"━━━ {ts} ━━━\n", "header")
            self.output_text.insert("end", f"原始: {result.raw_hex}\n", "raw")
            self.output_text.insert("end", f"命令: {result.cmd_code}  {result.cmd_name}")
            if result.direction:
                self.output_text.insert("end", f"  [{result.direction}]")
            self.output_text.insert("end", "\n")
            if note:
                self.output_text.insert("end", f"{note}\n", "field")
            if result.description:
                self.output_text.insert("end", f"说明: {result.description}\n", "field")
            if result.checksum_ok is not None:
                tag = "ok" if result.checksum_ok else "err"
                label = "通过" if result.checksum_ok else "失败"
                self.output_text.insert("end", f"校验: {label}\n", tag)
            if result.length_match is False:
                self.output_text.insert("end", "长度: 字段长度与实际不一致\n", "err")
            if result.error:
                self.output_text.insert("end", f"错误: {result.error}\n", "err")
            for f in result.fields:
                self.output_text.insert("end", f"  · {f.get('name', ''):<24} {f.get('text', '')}\n", "field")
            self.output_text.insert("end", "\n")

            if self.log_file:
                self._write_log(result)

        self.output_text.see("end")
        self.output_text.configure(state="disabled")

    def _clear_output(self) -> None:
        text = self.output_text if self.mode_var.get() == "paste" else self.serial_text
        text.configure(state="normal")
        text.delete("1.0", "end")
        text.configure(state="disabled")

    # ---------- 串口实时 ----------

    def _refresh_ports(self) -> None:
        ports = SerialCollector.list_ports()
        port_names = [p["device"] for p in ports]
        self.port_combo["values"] = port_names
        if port_names and not self.port_var.get():
            self.port_combo.current(0)
        self._set_status(f"找到 {len(port_names)} 个串口")

    def _toggle_serial(self) -> None:
        if self.is_collecting:
            self._stop_serial()
        else:
            self._start_serial()

    def _start_serial(self) -> None:
        if not self.cfg:
            messagebox.showwarning("提示", "请先选择产品协议")
            return
        port = self.port_var.get()
        if not port:
            messagebox.showwarning("提示", "请选择串口")
            return
        try:
            baudrate = int(self.baudrate_var.get())
        except Exception:
            baudrate = 115200

        self._set_status(f"正在连接 {port} @ {baudrate}...")
        self.root.update()

        def on_frame(result, frame, ts):
            self._ui_queue.append(("serial_frame", (result, ts)))

        def on_error(msg):
            self._ui_queue.append(("serial_error", (msg,)))

        try:
            self.collector = SerialCollector(
                cfg=self.cfg,
                port=port,
                baudrate=baudrate,
                on_frame=on_frame,
                on_error=on_error,
            )
            self.collector.start()
        except Exception as e:
            messagebox.showerror("串口打开失败", str(e))
            self._set_status("就绪")
            return

        self.is_collecting = True
        self.start_btn.configure(text="停止监控")
        self._set_status(f"监控中: {port} @ {baudrate}")

    def _stop_serial(self) -> None:
        if self.collector:
            self.collector.stop()
            self.collector = None
        self.is_collecting = False
        self.start_btn.configure(text="开始监控")
        self._set_status("已停止")

    # ---------- UI 队列处理（子线程 → 主线程） ----------

    def _process_ui_queue(self) -> None:
        while self._ui_queue:
            kind, args = self._ui_queue.pop(0)
            if kind == "serial_frame":
                self._display_serial_frame(*args)
            elif kind == "serial_error":
                self._display_serial_error(*args)
        self.root.after(100, self._process_ui_queue)

    def _display_serial_frame(self, result: ParseResult, ts: float) -> None:
        self.serial_text.configure(state="normal")
        ts_str = datetime.fromtimestamp(ts).strftime("%H:%M:%S.%f")[:-3]
        ok_tag = "ok" if (result.error is None and result.checksum_ok is not False) else "err"
        cs = "✓" if result.checksum_ok else "✗" if result.checksum_ok is False else " "
        status = "OK" if result.error is None else "ERR"

        if self.detail_var.get():
            self.serial_text.insert("end", f"[{ts_str}] ", "ts")
            self.serial_text.insert("end", f"{status} {cs} {result.cmd_code}  {result.cmd_name}", "cmd")
            if result.direction:
                self.serial_text.insert("end", f"  [{result.direction}]")
            self.serial_text.insert("end", f"\n  原始: {result.raw_hex}\n", "raw")
            if result.error:
                self.serial_text.insert("end", f"  错误: {result.error}\n", "err")
            for f in result.fields:
                self.serial_text.insert("end", f"  · {f.get('name', ''):<24} {f.get('text', '')}\n", "field")
            self.serial_text.insert("end", "\n")
        else:
            self.serial_text.insert("end", f"[{ts_str}] ", "ts")
            self.serial_text.insert("end", f"{status} {cs} {result.cmd_code:<6} ", ok_tag)
            self.serial_text.insert("end", f"{result.cmd_name}")
            if result.direction:
                self.serial_text.insert("end", f" [{result.direction}]")
            self.serial_text.insert("end", f"  | {result.raw_hex}\n", "raw")

        if self.autoscroll_var.get():
            self.serial_text.see("end")
        self.serial_text.configure(state="disabled")

        if self.log_file:
            self._write_log(result, ts)

        # 更新统计
        if self.collector and self.collector.sync:
            self.stats_var.set(f"帧 {self.collector.sync.frame_count}  错误 {self.collector.sync.error_count}  缓冲 {self.collector.sync.partial_bytes}B")

    def _display_serial_error(self, msg: str) -> None:
        self.serial_text.configure(state="normal")
        self.serial_text.insert("end", f"[错误] {msg}\n", "err")
        self.serial_text.see("end")
        self.serial_text.configure(state="disabled")
        self._stop_serial()

    # ---------- 日志 ----------

    def _choose_log(self) -> None:
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
        if not self.log_file:
            return
        ts = ts or time.time()
        ts_str = datetime.fromtimestamp(ts).strftime("%H:%M:%S.%f")[:-3]
        ok_tag = "OK" if (result.error is None and result.checksum_ok is not False) else "ERR"
        cs = "✓" if result.checksum_ok else "✗" if result.checksum_ok is False else " "
        self.log_file.write(f"[{ts_str}] {ok_tag} {cs} {result.cmd_code} {result.cmd_name}")
        if result.direction:
            self.log_file.write(f" [{result.direction}]")
        self.log_file.write(f" | {result.raw_hex}\n")
        if result.error:
            self.log_file.write(f"  错误: {result.error}\n")
        for f in result.fields:
            self.log_file.write(f"  · {f.get('name', ''):<24} {f.get('text', '')}\n")
        self.log_file.flush()
        self.log_count += 1

    # ---------- 工具 ----------

    def _set_status(self, msg: str) -> None:
        self.status_var.set(msg)
        self.root.update_idletasks()

    def on_close(self) -> None:
        if self.is_collecting:
            self._stop_serial()
        if self.log_file:
            self.log_file.write(f"===== 结束记录（共 {self.log_count} 条） =====\n")
            self.log_file.close()
        self.root.destroy()


# ---------- 入口 ----------

def main() -> int:
    root = tk.Tk()
    app = ProtocolParserApp(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
