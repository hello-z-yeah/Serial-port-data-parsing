"""协议解析工具 GUI（PySide6 + qfluentwidgets Fluent 风格）。

【重构说明】
- 仅替换界面框架与事件绑定（Tkinter → PySide6 + qfluentwidgets）
- protocol_parser 模块、SerialCollector、组帧/拆帧/校验、文件读写等业务逻辑 100% 原样保留
- UI → 业务层入参与回调接口保持完全一致
"""
from __future__ import annotations

import os
import sys
import threading
import time
import json
import re
from queue import Queue, Empty
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Callable

from PySide6.QtCore import (
    Qt, QTimer, Signal, Slot, QObject, QSize, QThread
)
from PySide6.QtGui import QFont, QTextCharFormat, QColor, QAction, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QSplitter, QFrame, QLabel, QSizePolicy, QFileDialog, QMessageBox,
    QAbstractItemView, QHeaderView, QTableWidgetItem, QMenu,
    QDialog, QDialogButtonBox, QFormLayout, QSpinBox as QtSpinBox,
)
from qfluentwidgets import (
    FluentWindow, setTheme, Theme, setThemeColor,
    PrimaryPushButton, PushButton, LineEdit, ComboBox,
    CardWidget, BodyLabel, StrongBodyLabel, CaptionLabel,
    TextEdit, CheckBox, SpinBox, TableWidget, TreeWidget,
    SwitchButton, FluentIcon, InfoBar, InfoBarPosition,
    NavigationItemPosition, ScrollArea, SimpleCardWidget,
    TransparentToolButton, ToolButton, ToggleButton,
)

# 让 exe 也能找到 protocol_parser 包
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from protocol_parser import (  # noqa: E402
    VERSION,
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
from protocol_parser.theme import ThemeManager, PALETTE  # noqa: E402
from protocol_parser.widgets import apply_tooltip  # noqa: E402


# ---------- 资源/数据路径（与原版完全一致） ----------

def resource_path(relative: str) -> Path:
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / relative
    base = Path(__file__).resolve().parent
    candidate = base / relative
    if candidate.exists():
        return candidate
    return base.parent / relative


def user_data_path(relative: str = "") -> Path:
    try:
        if getattr(sys, "frozen", False) and hasattr(sys, "executable"):
            exe_dir = Path(sys.executable).resolve().parent
            try:
                write_probe = exe_dir / ".write_probe"
                write_probe.write_text("probe", encoding="utf-8")
                write_probe.unlink(missing_ok=True)
                root = exe_dir
            except (OSError, PermissionError):
                doc_dir = Path.home() / "Documents"
                if not doc_dir.exists():
                    doc_dir = Path.home()
                root = doc_dir / "串口解析工具"
            data_dir = root / "data"
        else:
            project_root = Path(__file__).resolve().parent.parent
            data_dir = project_root / "data"
        if relative:
            data_dir = data_dir / relative
        data_dir.mkdir(parents=True, exist_ok=True)
        return data_dir
    except Exception:
        fb = Path.home() / "串口解析工具" / "data"
        if relative:
            fb = fb / relative
        fb.mkdir(parents=True, exist_ok=True)
        return fb


def get_protocol_dir() -> Path:
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        proto_dir = exe_dir / "product"
        proto_dir.mkdir(parents=True, exist_ok=True)
        return proto_dir
    dev = Path(__file__).resolve().parent.parent / "product"
    dev.mkdir(parents=True, exist_ok=True)
    return dev


def _crash_log_dir() -> Path:
    try:
        if getattr(sys, "frozen", False):
            return Path(sys.executable).resolve().parent
        return Path(__file__).resolve().parent.parent
    except Exception:
        return Path.cwd()


def _write_crash_log_gui(exc: BaseException) -> Path | None:
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
        return path
    except Exception:
        return None


def load_builtin_protocol() -> dict:
    from protocol_parser.parser import load_protocol as _load
    external_dir = get_protocol_dir()
    external_file = external_dir / "v3_serial.json"
    if external_file.exists():
        try:
            return _load(external_file)
        except ProtocolError:
            pass
    bundled = resource_path("product") / "v3_serial.json"
    if bundled.exists():
        try:
            return _load(bundled)
        except ProtocolError:
            pass
    return {"product": "串口3.0协议", "description": "内置基础协议", "commands": [], "frame": {}, "enums": {}, "attributes": {}}


_builtin_v3: dict | None = None


def get_builtin_v3(refresh: bool = False) -> dict:
    global _builtin_v3
    if refresh or _builtin_v3 is None:
        _builtin_v3 = load_builtin_protocol()
    return _builtin_v3


# ---------- 线程安全信号桥（替代 Tk after + queue） ----------

class UiBridge(QObject):
    """把串口线程回调安全投递到主线程。"""
    frame_signal = Signal(object, float)          # ParseResult, ts
    raw_signal = Signal(bytes, float)             # data, ts
    error_signal = Signal(str)                    # msg
    tx_signal = Signal(bytes, float)              # data_sent, ts
    status_signal = Signal(str)                   # status text


# ---------- 主窗口 ----------


# ---------- 指令库「配置循环发送」对话框 ----------

class CycleConfigDialog(QDialog):
    """勾选 + 间隔 + 手动排序。列表顺序 = 循环发送顺序。"""

    def __init__(
        self,
        parent: QWidget | None,
        items: list[dict],
        seq: list[dict],
        is_hex: bool,
    ):
        super().__init__(parent)
        self.setWindowTitle("配置循环发送")
        self.resize(720, 480)
        self.setMinimumSize(560, 360)
        self._items = items
        self._seq = list(seq)
        self._is_hex = is_hex
        self.result_seq: list[dict] | None = None

        pool = {it.get("id"): it for it in items if it.get("id")}
        seq_map = {s.get("id"): s for s in self._seq}
        ordered_ids: list[str] = []
        for s in self._seq:
            iid = s.get("id")
            if iid in pool and iid not in ordered_ids:
                ordered_ids.append(iid)
        for it in items:
            iid = it.get("id")
            if iid and iid not in ordered_ids:
                ordered_ids.append(iid)
        self._ordered_ids = ordered_ids
        self._pool = pool
        self._seq_map = seq_map

        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)

        layout.addWidget(BodyLabel(
            "勾选参与循环的指令，双击「间隔」可改 ms；列表顺序 = 发送顺序（右侧按钮可排序）"
        ))

        body = QHBoxLayout()
        self.table = TableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["参与", "名称", "指令数据", "间隔(ms)"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.setColumnWidth(0, 50)
        self.table.setColumnWidth(1, 120)
        self.table.setColumnWidth(3, 90)
        self.table.setRowCount(len(self._ordered_ids))

        for row, cid in enumerate(self._ordered_ids):
            it = self._pool.get(cid) or {}
            on = cid in self._seq_map
            delay = str((self._seq_map.get(cid) or {}).get("delay_ms", 1000))

            chk = CheckBox()
            chk.setChecked(on)
            cell = QWidget()
            lay = QHBoxLayout(cell)
            lay.setContentsMargins(8, 0, 0, 0)
            lay.addWidget(chk)
            lay.addStretch()
            self.table.setCellWidget(row, 0, cell)
            # 保存 checkbox 引用
            cell._chk = chk  # type: ignore

            name_item = QTableWidgetItem(it.get("name") or "")
            name_item.setFlags(name_item.flags() & ~Qt.ItemIsEditable)
            name_item.setData(Qt.UserRole, cid)
            self.table.setItem(row, 1, name_item)

            payload_item = QTableWidgetItem(it.get("payload") or "")
            payload_item.setFlags(payload_item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(row, 2, payload_item)

            self.table.setItem(row, 3, QTableWidgetItem(delay))

        body.addWidget(self.table, stretch=1)

        # 右侧排序按钮
        side = QVBoxLayout()
        for text, slot in [
            ("上移 ↑", lambda: self._move(-1)),
            ("下移 ↓", lambda: self._move(1)),
            ("置顶", lambda: self._move_edge(True)),
            ("置底", lambda: self._move_edge(False)),
            ("全选", lambda: self._toggle_all(True)),
            ("全不选", lambda: self._toggle_all(False)),
        ]:
            b = PushButton(text)
            b.clicked.connect(slot)
            side.addWidget(b)
        side.addStretch()
        body.addLayout(side)
        layout.addLayout(body, stretch=1)

        bf = QHBoxLayout()
        bf.addStretch()
        btn_save = PrimaryPushButton("保存")
        btn_save.clicked.connect(self._on_save)
        bf.addWidget(btn_save)
        btn_cancel = PushButton("取消")
        btn_cancel.clicked.connect(self.reject)
        bf.addWidget(btn_cancel)
        layout.addLayout(bf)

    def _selected_row(self) -> int:
        rows = self.table.selectionModel().selectedRows()
        return rows[0].row() if rows else -1

    def _move(self, delta: int) -> None:
        row = self._selected_row()
        if row < 0:
            return
        new_row = row + delta
        if new_row < 0 or new_row >= self.table.rowCount():
            return
        self._swap_rows(row, new_row)
        self.table.selectRow(new_row)

    def _move_edge(self, to_top: bool) -> None:
        row = self._selected_row()
        if row < 0:
            return
        target = 0 if to_top else self.table.rowCount() - 1
        if row == target:
            return
        # 逐步交换
        step = -1 if to_top else 1
        while row != target:
            self._swap_rows(row, row + step)
            row += step
        self.table.selectRow(target)

    def _swap_rows(self, r1: int, r2: int) -> None:
        # 交换 checkbox 状态
        c1 = self.table.cellWidget(r1, 0)
        c2 = self.table.cellWidget(r2, 0)
        if c1 and c2 and hasattr(c1, "_chk") and hasattr(c2, "_chk"):
            on1, on2 = c1._chk.isChecked(), c2._chk.isChecked()
            c1._chk.setChecked(on2)
            c2._chk.setChecked(on1)
        for col in (1, 2, 3):
            i1 = self.table.takeItem(r1, col)
            i2 = self.table.takeItem(r2, col)
            if i2:
                self.table.setItem(r1, col, i2)
            if i1:
                self.table.setItem(r2, col, i1)

    def _toggle_all(self, on: bool) -> None:
        for r in range(self.table.rowCount()):
            w = self.table.cellWidget(r, 0)
            if w and hasattr(w, "_chk"):
                w._chk.setChecked(on)

    def _on_save(self) -> None:
        new_seq = []
        for r in range(self.table.rowCount()):
            w = self.table.cellWidget(r, 0)
            if not (w and hasattr(w, "_chk") and w._chk.isChecked()):
                continue
            name_item = self.table.item(r, 1)
            cid = name_item.data(Qt.UserRole) if name_item else None
            if not cid:
                continue
            delay_item = self.table.item(r, 3)
            try:
                d = max(10, int((delay_item.text() if delay_item else "1000").strip()))
            except Exception:
                d = 1000
            new_seq.append({"id": cid, "delay_ms": d})
        self.result_seq = new_seq
        self.accept()



class ProtocolParserApp(FluentWindow):
    """主界面：FluentWindow + 业务逻辑原样保留。"""

    def __init__(self, monitor_port: str | None = None, monitor_baud: int = 9600):
        super().__init__()
        self.setWindowTitle(f"串口协议解析工具 v{VERSION}")
        self.resize(1400, 860)
        self.setMinimumSize(1100, 700)

        # 主题
        self.theme = ThemeManager(mode="light", style="win11")
        setTheme(Theme.LIGHT)
        setThemeColor(PALETTE["primary"])

        # 启动参数
        self._monitor_port = monitor_port
        self._monitor_baud = monitor_baud

        # ---------- 业务状态（与原版完全一致） ----------
        self.cfg: dict | None = None
        self.product_var = ""
        self.port_var = ""
        self.baudrate_var = "9600"
        self.bytesize_var = 8
        self.stopbits_var = 1
        self.collector: SerialCollector | None = None
        self.is_collecting = False
        self.serial_sender = "模组发送"
        self.hex_format = True
        self.detail_mode = False
        self.autoscroll = True
        self.view_mode = "protocol"  # protocol | raw

        self.log_path: Path | None = None
        self.log_file = None
        self.log_count = 0
        self.rx_frame_count = 0
        self.tx_frame_count = 0

        # 原始数据保存
        self.save_raw_enabled = False
        self.save_raw_path = str(user_data_path())
        self.save_raw_filename = datetime.now().strftime("serial_data_%Y%m%d_%H%M%S")
        self.save_raw_file = None
        self.save_raw_current_size = 0
        self.raw_auto_split_mb = 50
        self.save_raw_max_size = 50 * 1024 * 1024
        self.save_raw_count = 0
        self._save_raw_active = False
        self._save_raw_as_ascii = True
        self._save_q: Queue = Queue(maxsize=5000)
        self._save_writer_thread = None
        self._save_writer_stop = threading.Event()

        # 发送
        self.send_mode = "protocol"  # protocol / raw_hex / raw_ascii
        self.tx_cmd_code = "0x20"
        self.tx_direction = "模组发送"
        self.tx_fields = '{"value": 1}'
        self.tx_raw = ""
        self.tx_cycle = False
        self.tx_interval_ms = 1000
        self._tx_cycle_timer: QTimer | None = None
        self.tx_auto_crc8 = False
        self.tx_append_crlf = False
        self.tx_crc_algo = "ADD8"

        # 指令库
        self.CMDLIB_MAX = 40
        self._cmdlib_mode = "hex"
        self._cmdlib_hex: list[dict] = []
        self._cmdlib_ascii: list[dict] = []
        self._cmdlib_cycle_hex: list[dict] = []
        self._cmdlib_cycle_ascii: list[dict] = []
        self._cmdlib_cycle_on = False
        self._cmdlib_cycle_timer: QTimer | None = None
        self._cmdlib_cycle_idx = 0

        self.max_display_lines = 10000
        self._disp_line_count = 0

        # 信号桥
        self.bridge = UiBridge()
        self.bridge.frame_signal.connect(self._on_ui_frame)
        self.bridge.raw_signal.connect(self._on_ui_raw)
        self.bridge.error_signal.connect(self._on_ui_error)
        self.bridge.tx_signal.connect(self._on_ui_tx)

        # 构建 UI
        self._build_ui()
        self._load_protocols()
        self._cmdlib_load()
        self._refresh_ports()

        # 定时器：端口热插拔
        self._port_watch_timer = QTimer(self)
        self._port_watch_timer.timeout.connect(self._poll_ports)
        self._port_watch_timer.start(1500)

        # 显示缓冲定时刷新
        self._disp_buf: list[str] = []
        self._disp_flush_timer = QTimer(self)
        self._disp_flush_timer.setSingleShot(True)
        self._disp_flush_timer.timeout.connect(self._flush_display_buf)

        if self._monitor_port:
            self._apply_monitor_args()

        self._set_status("就绪")

    # ================================================================ 
    # UI 构建
    # ================================================================ 

    def _build_ui(self) -> None:
        # 中央内容区
        self.central = QWidget()
        self.central.setObjectName("centralWidget")
        main_layout = QVBoxLayout(self.central)
        main_layout.setContentsMargins(12, 8, 12, 8)
        main_layout.setSpacing(8)

        # --- 顶部工具栏 ---
        top_bar = self._build_top_bar()
        main_layout.addWidget(top_bar)

        # --- 串口配置卡片 ---
        self.serial_config_card = self._build_serial_config_card()
        main_layout.addWidget(self.serial_config_card)

        # --- 主体分栏：实时数据 | 指令库 ---
        self.main_splitter = QSplitter(Qt.Horizontal)
        self.main_splitter.setHandleWidth(6)

        # 左侧：实时数据
        self.realtime_card = self._build_realtime_card()
        self.main_splitter.addWidget(self.realtime_card)

        # 右侧：指令库（默认隐藏）
        self.cmdlib_card = self._build_cmdlib_card()
        self.cmdlib_card.setVisible(False)
        self.main_splitter.addWidget(self.cmdlib_card)
        self.main_splitter.setStretchFactor(0, 3)
        self.main_splitter.setStretchFactor(1, 1)

        main_layout.addWidget(self.main_splitter, stretch=1)

        # --- 底部发送面板（默认隐藏） ---
        self.send_card = self._build_send_card()
        self.send_card.setVisible(False)
        self.send_card.setMaximumHeight(220)
        main_layout.addWidget(self.send_card)

        # --- 状态栏 ---
        status_bar = self._build_status_bar()
        main_layout.addWidget(status_bar)

        # 把 central 放到 FluentWindow 的 stackedWidget
        self.addSubInterface(self.central, FluentIcon.HOME, "主界面")
        self.navigationInterface.setVisible(False)  # 单页面，隐藏导航

    def _build_top_bar(self) -> QWidget:
        bar = QWidget()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # 产品协议
        layout.addWidget(BodyLabel("产品协议："))
        self.product_combo = ComboBox()
        self.product_combo.setMinimumWidth(180)
        self.product_combo.currentTextChanged.connect(self._on_product_change)
        layout.addWidget(self.product_combo)

        self.btn_import = PushButton("导入Word协议")
        self.btn_import.clicked.connect(self._safe(self._import_docx))
        layout.addWidget(self.btn_import)

        self.btn_view_proto = PushButton("查看协议")
        self.btn_view_proto.clicked.connect(self._safe(self._show_protocol))
        layout.addWidget(self.btn_view_proto)

        layout.addStretch()

        self.btn_send_panel = PushButton("打开指令发送")
        self.btn_send_panel.clicked.connect(self._toggle_send_panel)
        layout.addWidget(self.btn_send_panel)

        self.btn_cmdlib = PushButton("打开指令库")
        self.btn_cmdlib.clicked.connect(self._toggle_cmdlib_panel)
        layout.addWidget(self.btn_cmdlib)

        self.btn_add_port = PushButton("添加串口")
        self.btn_add_port.clicked.connect(self._safe(self._add_serial_port))
        layout.addWidget(self.btn_add_port)

        self.btn_save_log = PushButton("保存日志")
        self.btn_save_log.clicked.connect(self._safe(self._choose_log))
        layout.addWidget(self.btn_save_log)

        self.btn_topmost = ToggleButton("置顶")
        self.btn_topmost.toggled.connect(self._on_topmost_toggled)
        layout.addWidget(self.btn_topmost)

        return bar

    def _build_serial_config_card(self) -> CardWidget:
        card = CardWidget()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        row1 = QHBoxLayout()
        row1.addWidget(BodyLabel("串口："))
        self.port_combo = ComboBox()
        self.port_combo.setMinimumWidth(220)
        self.port_combo.currentTextChanged.connect(self._on_port_changed)
        row1.addWidget(self.port_combo)

        self.btn_refresh_ports = PushButton("刷新")
        self.btn_refresh_ports.clicked.connect(self._safe(lambda: self._refresh_ports()))
        row1.addWidget(self.btn_refresh_ports)

        row1.addWidget(BodyLabel("波特率："))
        self.baud_combo = ComboBox()
        self.baud_combo.setEditable(True)
        self.baud_combo.addItems([
            "9600", "19200", "38400", "57600", "115200", "230400",
            "460800", "921600", "1000000", "1500000", "2000000",
            "3000000", "4000000", "5000000", "6000000"
        ])
        self.baud_combo.setCurrentText("9600")
        self.baud_combo.currentTextChanged.connect(self._on_baud_changed)
        row1.addWidget(self.baud_combo)

        row1.addWidget(BodyLabel("数据位："))
        self.bytesize_combo = ComboBox()
        self.bytesize_combo.addItems(["5", "6", "7", "8"])
        self.bytesize_combo.setCurrentText("8")
        row1.addWidget(self.bytesize_combo)

        row1.addWidget(BodyLabel("停止位："))
        self.stopbits_combo = ComboBox()
        self.stopbits_combo.addItems(["1", "1.5", "2"])
        self.stopbits_combo.setCurrentText("1")
        row1.addWidget(self.stopbits_combo)

        row1.addStretch()

        self.btn_hex = ToggleButton("HEX 格式")
        self.btn_hex.setChecked(True)
        self.btn_hex.toggled.connect(self._on_hex_toggled)
        row1.addWidget(self.btn_hex)

        self.btn_start = PrimaryPushButton("● 开始监控")
        self.btn_start.clicked.connect(self._safe(self._toggle_serial))
        apply_tooltip(self.btn_start, "开始/停止监控（F5 / Shift+F5）")
        row1.addWidget(self.btn_start)

        layout.addLayout(row1)

        # 第二行：原始数据保存
        row2 = QHBoxLayout()
        self.btn_save_raw = PushButton("开始存储数据")
        self.btn_save_raw.clicked.connect(self._safe(self._toggle_save_raw))
        row2.addWidget(self.btn_save_raw)

        row2.addWidget(BodyLabel("路径："))
        self.save_path_edit = LineEdit()
        self.save_path_edit.setText(self.save_raw_path)
        self.save_path_edit.setReadOnly(True)
        row2.addWidget(self.save_path_edit, stretch=1)

        self.btn_choose_path = PushButton("选择")
        self.btn_choose_path.clicked.connect(self._safe(self._choose_save_raw_path))
        row2.addWidget(self.btn_choose_path)

        row2.addWidget(BodyLabel("文件名："))
        self.save_name_edit = LineEdit()
        self.save_name_edit.setText(self.save_raw_filename)
        self.save_name_edit.setFixedWidth(200)
        row2.addWidget(self.save_name_edit)

        layout.addLayout(row2)
        return card

    def _build_realtime_card(self) -> CardWidget:
        card = CardWidget()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)

        # 工具条
        toolbar = QHBoxLayout()
        toolbar.addWidget(StrongBodyLabel("实时数据"))

        self.btn_view_mode = ToggleButton("协议解析模式")
        self.btn_view_mode.setChecked(True)
        self.btn_view_mode.toggled.connect(self._on_view_mode_toggled)
        toolbar.addWidget(self.btn_view_mode)

        self.btn_clear = PushButton("清空")
        self.btn_clear.clicked.connect(self._safe(self._clear_output))
        toolbar.addWidget(self.btn_clear)

        self.btn_autoscroll = ToggleButton("自动滚动")
        self.btn_autoscroll.setChecked(True)
        self.btn_autoscroll.toggled.connect(lambda c: setattr(self, "autoscroll", c))
        toolbar.addWidget(self.btn_autoscroll)

        self.btn_sender = ToggleButton("模组发送")
        self.btn_sender.setChecked(True)
        self.btn_sender.toggled.connect(self._on_sender_toggled)
        toolbar.addWidget(self.btn_sender)

        toolbar.addStretch()
        layout.addLayout(toolbar)

        # 文本区
        self.serial_text = TextEdit()
        self.serial_text.setReadOnly(True)
        font = QFont("Cascadia Mono", 10)
        font.setStyleHint(QFont.Monospace)
        self.serial_text.setFont(font)
        self.serial_text.setLineWrapMode(TextEdit.WidgetWidth)
        layout.addWidget(self.serial_text, stretch=1)

        return card

    def _build_cmdlib_card(self) -> CardWidget:
        card = CardWidget()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(8, 8, 8, 8)

        bar = QHBoxLayout()
        bar.addWidget(StrongBodyLabel("指令库"))
        self.btn_cmdlib_mode = ToggleButton("HEX")
        self.btn_cmdlib_mode.setChecked(True)
        self.btn_cmdlib_mode.toggled.connect(self._cmdlib_toggle_mode)
        bar.addWidget(self.btn_cmdlib_mode)
        self.btn_cmdlib_cycle = PushButton("循环发送")
        self.btn_cmdlib_cycle.clicked.connect(self._safe(self._cmdlib_toggle_cycle))
        bar.addWidget(self.btn_cmdlib_cycle)
        self.btn_cmdlib_add = PushButton("新增")
        self.btn_cmdlib_add.clicked.connect(self._safe(self._cmdlib_add_item))
        bar.addWidget(self.btn_cmdlib_add)
        bar.addStretch()
        layout.addLayout(bar)

        self.cmdlib_table = TableWidget()
        self.cmdlib_table.setColumnCount(4)
        self.cmdlib_table.setHorizontalHeaderLabels(["序号", "名称", "指令数据", "操作"])
        self.cmdlib_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.cmdlib_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.cmdlib_table.setEditTriggers(QAbstractItemView.DoubleClicked)
        self.cmdlib_table.cellDoubleClicked.connect(self._cmdlib_on_cell_double)
        layout.addWidget(self.cmdlib_table, stretch=1)

        return card

    def _build_send_card(self) -> CardWidget:
        card = CardWidget()
        layout = QHBoxLayout(card)
        layout.setContentsMargins(10, 8, 10, 8)

        # 左：模式
        mode_box = QVBoxLayout()
        mode_box.addWidget(StrongBodyLabel("发送模式"))
        self.btn_mode_proto = ToggleButton("协议模式")
        self.btn_mode_proto.setChecked(True)
        self.btn_mode_proto.clicked.connect(lambda: self._set_send_mode("protocol"))
        mode_box.addWidget(self.btn_mode_proto)
        self.btn_mode_hex = ToggleButton("HEX")
        self.btn_mode_hex.clicked.connect(lambda: self._set_send_mode("raw_hex"))
        mode_box.addWidget(self.btn_mode_hex)
        self.btn_mode_ascii = ToggleButton("ASCII")
        self.btn_mode_ascii.clicked.connect(lambda: self._set_send_mode("raw_ascii"))
        mode_box.addWidget(self.btn_mode_ascii)
        mode_box.addStretch()
        layout.addLayout(mode_box)

        # 中：输入区
        center = QVBoxLayout()
        self.fields_edit = TextEdit()
        self.fields_edit.setPlaceholderText('协议字段 JSON，例如 {"value": 1}')
        self.fields_edit.setMaximumHeight(120)
        center.addWidget(self.fields_edit)

        self.raw_edit = TextEdit()
        self.raw_edit.setPlaceholderText("HEX 或 ASCII 原始数据")
        self.raw_edit.setMaximumHeight(120)
        self.raw_edit.setVisible(False)
        center.addWidget(self.raw_edit)
        layout.addLayout(center, stretch=1)

        # 右：操作
        act = QVBoxLayout()
        self.btn_send_once = PrimaryPushButton("发送")
        self.btn_send_once.clicked.connect(self._safe(self._on_send_once))
        act.addWidget(self.btn_send_once)

        self.btn_clear_send = PushButton("清空输入")
        self.btn_clear_send.clicked.connect(self._on_clear_send)
        act.addWidget(self.btn_clear_send)

        self.btn_crlf = ToggleButton("加回车换行")
        self.btn_crlf.toggled.connect(lambda c: setattr(self, "tx_append_crlf", c))
        act.addWidget(self.btn_crlf)

        self.btn_crc = ToggleButton("自动追加校验位")
        self.btn_crc.toggled.connect(lambda c: setattr(self, "tx_auto_crc8", c))
        act.addWidget(self.btn_crc)

        crc_row = QHBoxLayout()
        crc_row.addWidget(BodyLabel("算法:"))
        self.crc_algo_combo = ComboBox()
        self.crc_algo_combo.addItems(["ADD8", "0-ADD8", "XOR8", "ADD16", "ModbusCRC16", "CCITT-CRC16", "CRC32"])
        self.crc_algo_combo.currentTextChanged.connect(lambda t: setattr(self, "tx_crc_algo", t))
        crc_row.addWidget(self.crc_algo_combo)
        act.addLayout(crc_row)

        cycle_row = QHBoxLayout()
        self.btn_cycle = ToggleButton("自动发送")
        self.btn_cycle.toggled.connect(self._on_toggle_cycle_send)
        cycle_row.addWidget(self.btn_cycle)
        cycle_row.addWidget(BodyLabel("间隔ms"))
        self.interval_spin = SpinBox()
        self.interval_spin.setRange(10, 3600000)
        self.interval_spin.setValue(1000)
        self.interval_spin.valueChanged.connect(lambda v: setattr(self, "tx_interval_ms", v))
        cycle_row.addWidget(self.interval_spin)
        act.addLayout(cycle_row)

        act.addStretch()
        layout.addLayout(act)
        return card

    def _build_status_bar(self) -> QWidget:
        bar = QWidget()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(4, 2, 4, 2)
        self.status_label = CaptionLabel("就绪")
        layout.addWidget(self.status_label, stretch=1)
        self.stats_label = CaptionLabel("RX 0  TX 0  错误 0")
        layout.addWidget(self.stats_label)
        return bar

    # ================================================================ 
    # 安全包装 / 状态
    # ================================================================ 

    def _safe(self, fn: Callable):
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception as e:
                self._report_error("操作失败", e)
                return None
        return wrapper

    def _report_error(self, title: str, exc: Exception) -> None:
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
        QMessageBox.critical(self, title, body)

    def _set_status(self, msg: str) -> None:
        port = self.port_var.split(" - ")[0].strip() if self.port_var else "未选串口"
        baud = self.baudrate_var or "-"
        save_s = "存储中" if self._save_raw_active else ("待存储" if self.save_raw_enabled else "未存储")
        base = f"{port}  |  {baud}  |  {save_s}"
        self.status_label.setText(f"{base}  ·  {msg}" if msg else base)

    def _update_stats_bar(self) -> None:
        err = 0
        partial = 0
        if self.collector and getattr(self.collector, "sync", None):
            err = getattr(self.collector.sync, "error_count", 0) or 0
            partial = getattr(self.collector.sync, "partial_bytes", 0) or 0
        self.stats_label.setText(
            f"RX {self.rx_frame_count}  TX {self.tx_frame_count}  错误 {err}  缓冲 {partial}B"
        )

    # ================================================================ 
    # 协议加载（业务逻辑原样）
    # ================================================================ 

    def _load_protocols(self) -> None:
        products: list[tuple[str, str]] = []
        get_builtin_v3(refresh=False)
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
        self._product_sources = {p[0]: p[1] for p in products}
        self.product_combo.clear()
        self.product_combo.addItems([p[0] for p in products])
        if products:
            self.product_combo.setCurrentIndex(0)
            self._load_product_cfg(products[0][0])
        self._set_status(f"已加载 {len(products)} 个协议")

    def _load_product_cfg(self, product_name: str) -> None:
        source = self._product_sources.get(product_name)
        if source == "__builtin_v3__":
            self.cfg = get_builtin_v3()
        else:
            try:
                from protocol_parser.parser import merge_protocol
                user_cfg = load_protocol(source)
                self.cfg = merge_protocol(get_builtin_v3(), user_cfg)
            except Exception as e:
                self._report_error("协议加载失败", e)
                return
        self.product_var = product_name
        self._set_status(f"已加载: {product_name}")

    def _on_product_change(self, name: str) -> None:
        if name:
            self._load_product_cfg(name)

    def _import_docx(self) -> None:
        from protocol_parser.docx_importer import import_from_docx
        from protocol_parser.attr_editor import AttributeEditorDialog
        path, _ = QFileDialog.getOpenFileName(self, "选择 Word 协议文档", "", "Word 文档 (*.docx);;所有文件 (*.*)")
        if not path:
            return
        try:
            imported_cfg = import_from_docx(path)
        except Exception as e:
            self._report_error("导入失败", e)
            return
        warnings = imported_cfg.get("_import_warnings") or []
        if warnings:
            msg = "⚠ 导入时发现以下问题：\n\n" + "\n".join(f"{i}. {w}" for i, w in enumerate(warnings, 1))
            QMessageBox.warning(self, "Word 导入告警（不影响继续编辑/保存）", msg)

        dlg = AttributeEditorDialog(self, imported_cfg)
        if dlg.exec() != QDialog.Accepted or not dlg.result:
            return

        user_cfg = dlg.result
        protocol_name = user_cfg.get("product", Path(path).stem)
        save_path = get_protocol_dir() / f"{protocol_name}.json"
        with open(save_path, "w", encoding="utf-8") as f:
            json.dump(user_cfg, f, ensure_ascii=False, indent=2)
        self._load_protocols()
        # 选中新导入的协议
        idx = self.product_combo.findText(protocol_name)
        if idx >= 0:
            self.product_combo.setCurrentIndex(idx)
        self._set_status(f"已导入: {protocol_name}")

    def _show_protocol(self) -> None:
        if not self.cfg:
            return
        content = json.dumps(self.cfg, ensure_ascii=False, indent=2)
        dlg = QDialog(self)
        dlg.setWindowTitle(f"协议详情 - {self.cfg.get('product', '')}")
        dlg.resize(800, 600)
        lay = QVBoxLayout(dlg)
        te = TextEdit()
        te.setPlainText(content)
        te.setReadOnly(True)
        lay.addWidget(te)
        dlg.exec()

    # ================================================================ 
    # 串口控制（业务逻辑原样，仅替换 UI 调用）
    # ================================================================ 

    def _refresh_ports(self, *, silent: bool = False) -> bool:
        ports = SerialCollector.list_ports()
        display_list = []
        for p in ports:
            dev = p.get("device", "")
            desc = p.get("description", "")
            if desc and desc != dev:
                display_list.append(f"{dev} - {desc}")
            else:
                display_list.append(dev)

        def _com_sort_key(item: str):
            m = re.match(r"^COM(\d+)", str(item), re.I)
            if m:
                return (0, int(m.group(1)))
            return (1, str(item).lower())

        display_list.sort(key=_com_sort_key)
        devices = [d.split(" - ")[0].strip() for d in display_list]
        changed = devices != getattr(self, "_last_port_devices", None)
        self._last_port_devices = devices

        cur = self.port_combo.currentText()
        self.port_combo.blockSignals(True)
        self.port_combo.clear()
        self.port_combo.addItems(display_list)
        if cur:
            idx = self.port_combo.findText(cur)
            if idx >= 0:
                self.port_combo.setCurrentIndex(idx)
            elif display_list:
                self.port_combo.setCurrentIndex(0)
        elif display_list:
            self.port_combo.setCurrentIndex(0)
        self.port_combo.blockSignals(False)
        self.port_var = self.port_combo.currentText()

        if changed and not silent:
            self._set_status(f"找到 {len(ports)} 个串口")
        return changed

    def _poll_ports(self) -> None:
        try:
            self._refresh_ports(silent=True)
        except Exception:
            pass

    def _on_port_changed(self, text: str) -> None:
        self.port_var = text
        if self.is_collecting:
            self._stop_serial()
            self._start_serial()

    def _on_baud_changed(self, text: str) -> None:
        self.baudrate_var = text.strip()
        if self.is_collecting:
            self._stop_serial()
            self._start_serial()

    def _toggle_serial(self) -> None:
        if self.is_collecting:
            self._stop_serial()
        else:
            self._start_serial()

    def _start_serial(self) -> None:
        cfg = self.cfg if self.cfg else {}
        no_protocol = not self.cfg
        port_display = self.port_var
        if not port_display:
            QMessageBox.warning(self, "提示", "请选择串口")
            return
        port = port_display.split(" - ")[0].strip()
        try:
            baudrate = int(str(self.baudrate_var).strip())
            if baudrate <= 0:
                raise ValueError
        except Exception:
            QMessageBox.warning(self, "提示", "波特率必须填写正整数")
            return
        try:
            bytesize = int(self.bytesize_combo.currentText())
        except Exception:
            bytesize = 8
        try:
            stopbits = float(self.stopbits_combo.currentText())
        except Exception:
            stopbits = 1

        self._set_status(f"正在连接 {port} @ {baudrate}...")

        def on_frame(result, frame, ts):
            try:
                self.bridge.frame_signal.emit(result, ts)
                self._write_raw_data(frame.raw, ts)
            except Exception as e:
                try:
                    _log_error_to_disk(e)
                except Exception:
                    pass

        def on_error(msg):
            try:
                self.bridge.error_signal.emit(msg)
            except Exception as e:
                try:
                    _log_error_to_disk(e)
                except Exception:
                    pass

        def on_raw(data, ts):
            try:
                self.bridge.raw_signal.emit(data, ts)
                self._write_raw_data(data, ts)
            except Exception as e:
                try:
                    _log_error_to_disk(e)
                except Exception:
                    pass

        def on_tx_sent(data_sent: bytes, direction_label: str, ts: float):
            try:
                self.tx_frame_count += 1
                self._write_raw_data(data_sent, ts, prefix="TX ")
                self.bridge.tx_signal.emit(data_sent, ts)
            except Exception as e:
                try:
                    _log_error_to_disk(e)
                except Exception:
                    pass

        direction = None
        if self.hex_format:
            if self.serial_sender == "模组发送":
                direction = "request"
            elif self.serial_sender == "MCU发送":
                direction = "response"

        is_ascii = not self.hex_format
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
                raw_mode=(is_ascii or self.view_mode == "raw"),
                on_tx_sent=on_tx_sent,
            )
            self.collector.start()
        except Exception as e:
            self._report_error("串口打开失败", e)
            self._set_status("就绪")
            return

        self.is_collecting = True
        self.btn_start.setText("✓ 停止监控")
        mode_label = "ASCII" if is_ascii else "HEX"
        proto_tag = " (无协议·通用模式)" if no_protocol else ""
        if self.save_raw_enabled and not self._save_raw_active:
            self._open_save_raw_file()
            self._set_status(f"监控中: {port} @ {baudrate} ({mode_label}){proto_tag} - 保存原始数据")
        else:
            self._set_status(f"监控中: {port} @ {baudrate} ({mode_label}){proto_tag}")

    def _stop_serial(self) -> None:
        try:
            self._cmdlib_stop_cycle()
        except Exception:
            pass
        if self._tx_cycle_timer:
            self._tx_cycle_timer.stop()
            self._tx_cycle_timer = None
        self.tx_cycle = False
        try:
            if self.collector:
                self.collector.stop()
                self.collector = None
        except Exception as e:
            _log_error_to_disk(e)
            self.collector = None
        self.is_collecting = False
        try:
            self._close_save_raw_file()
        except Exception:
            pass
        self.save_raw_count = 0
        self.btn_start.setText("● 开始监控")
        self._set_status("已停止")

    # ================================================================ 
    # UI 回调（主线程）
    # ================================================================ 

    @Slot(object, float)
    def _on_ui_frame(self, result: ParseResult, ts: float) -> None:
        self.rx_frame_count += 1
        self._display_serial_frame(result, ts)
        self._update_stats_bar()

    @Slot(bytes, float)
    def _on_ui_raw(self, data: bytes, ts: float) -> None:
        self._display_raw_data(data, ts)

    @Slot(str)
    def _on_ui_error(self, msg: str) -> None:
        self._append_text(f"[错误] {msg}\n", color=PALETTE["error"])
        self._stop_serial()

    @Slot(bytes, float)
    def _on_ui_tx(self, data_sent: bytes, ts: float) -> None:
        self._display_serial_tx(data_sent, ts)
        self._update_stats_bar()

    def _append_text(self, text: str, color: str | None = None) -> None:
        cursor = self.serial_text.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        if color:
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(color))
            cursor.setCharFormat(fmt)
        cursor.insertText(text)
        if self.autoscroll:
            self.serial_text.setTextCursor(cursor)
            self.serial_text.ensureCursorVisible()
        # 简单行数限制
        doc = self.serial_text.document()
        if doc.blockCount() > self.max_display_lines:
            cursor.movePosition(cursor.MoveOperation.Start)
            cursor.movePosition(cursor.MoveOperation.Down, cursor.MoveMode.KeepAnchor, doc.blockCount() - self.max_display_lines)
            cursor.removeSelectedText()

    def _enqueue_display_text(self, text: str) -> None:
        if not text:
            return
        self._disp_buf.append(text)
        if len(self._disp_buf) > 400:
            self._disp_buf = self._disp_buf[-200:]
        if not self._disp_flush_timer.isActive():
            self._disp_flush_timer.start(150)

    def _flush_display_buf(self) -> None:
        if not self._disp_buf:
            return
        text = "".join(self._disp_buf)
        self._disp_buf.clear()
        self._append_text(text, color=PALETTE["field"])

    def _display_serial_frame(self, result: ParseResult, ts: float) -> None:
        ts_str = datetime.fromtimestamp(ts).strftime("%H:%M:%S.%f")[:-3]
        ok = result.error is None and result.checksum_ok is not False
        cs = "✓" if result.checksum_ok else ("✗" if result.checksum_ok is False else " ")
        status = "OK" if result.error is None else "ERR"
        raw_display = result.raw_hex
        if not self.hex_format:
            try:
                raw_bytes = bytes.fromhex(raw_display.replace(" ", ""))
                raw_display = "".join(chr(b) if 32 <= b < 127 else "." for b in raw_bytes)
            except Exception:
                pass

        line = f"[{ts_str}] {status} {cs} {result.cmd_code:<6} {result.cmd_name}"
        if result.direction:
            line += f" [{result.direction}]"
        data_fields = []
        in_data = False
        for f in result.fields:
            ftype = f.get("type", "")
            fname = f.get("name", "")
            ftext = f.get("text", "")
            if ftype == "separator":
                in_data = True
                continue
            if in_data and ftype not in ("header", "version", "cmd", "length", "checksum"):
                if isinstance(fname, str) and fname.startswith("attrid_"):
                    continue
                if ftext:
                    data_fields.append(f"{fname}={ftext}")
        if data_fields:
            line += f"  {{ {', '.join(data_fields)} }}"
        line += f"  | {raw_display}\n"
        color = PALETTE["success"] if ok else PALETTE["error"]
        self._append_text(line, color=color)

    def _display_serial_tx(self, data_sent: bytes, ts: float) -> None:
        ts_str = datetime.fromtimestamp(ts).strftime("%H:%M:%S.%f")[:-3]
        hex_str = " ".join(f"{b:02X}" for b in data_sent)
        line = f"[{ts_str}] [TX] Raw-HEX    | {hex_str}\n"
        self._append_text(line, color=PALETTE["tx"])

    def _display_raw_data(self, data: bytes, ts: float) -> None:
        ts_str = datetime.fromtimestamp(ts).strftime("%H:%M:%S.%f")[:-3]
        parts = []
        if self.hex_format:
            tokens = [f"{b:02X}" for b in data]
            for i in range(0, len(tokens), 16):
                parts.append(f"[{ts_str}] {' '.join(tokens[i:i+16])}\n")
        else:
            text = data.decode("utf-8", errors="replace")
            for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
                if not line:
                    continue
                printable = "".join(ch if (32 <= ord(ch) < 127 or ch == "\t") else "." for ch in line)
                parts.append(f"[{ts_str}] {printable}\n")
        if parts:
            self._enqueue_display_text("".join(parts))

    def _clear_output(self) -> None:
        self.serial_text.clear()
        self.rx_frame_count = 0
        self.tx_frame_count = 0
        self._update_stats_bar()

    # ================================================================ 
    # 发送（业务逻辑原样）
    # ================================================================ 

    def _set_send_mode(self, mode: str) -> None:
        self.send_mode = mode
        self.btn_mode_proto.setChecked(mode == "protocol")
        self.btn_mode_hex.setChecked(mode == "raw_hex")
        self.btn_mode_ascii.setChecked(mode == "raw_ascii")
        self.fields_edit.setVisible(mode == "protocol")
        self.raw_edit.setVisible(mode != "protocol")

    def _encode_current_protocol(self) -> bytes:
        if not self.cfg:
            raise RuntimeError("请先选择协议")
        cmd_s = (self.tx_cmd_code or "").strip()
        if not cmd_s:
            raise ValueError("请输入命令字 CmdID")
        if cmd_s.lower().startswith("0x"):
            cmd_code = int(cmd_s, 16)
        else:
            try:
                cmd_code = int(cmd_s, 0)
            except Exception:
                cmd_code = int(cmd_s)
        fields_txt = self.fields_edit.toPlainText().strip()
        fields = json.loads(fields_txt) if fields_txt else {}
        direction = "response" if self.tx_direction == "MCU发送" else "request"
        from protocol_parser.parser import encode_frame
        return encode_frame(cmd_code, self.cfg, direction=direction, fields=fields)

    def _on_send_once(self) -> None:
        if not (self.collector and self.collector.running):
            QMessageBox.warning(self, "提示", "请先打开串口（开始监控）后再发送")
            return
        mode = self.send_mode
        try:
            if mode == "protocol":
                data = self._encode_current_protocol()
                self.collector.send(data)
            elif mode == "raw_hex":
                s = self.raw_edit.toPlainText().strip()
                if not s:
                    QMessageBox.warning(self, "提示", "请输入 HEX 内容")
                    return
                from protocol_parser.parser import calc_checksum
                s_clean = s.replace(" ", "").replace("\n", "").replace("\r", "").replace("\t", "")
                if s_clean.lower().startswith("0x"):
                    s_clean = s_clean[2:]
                if len(s_clean) % 2 == 1:
                    s_clean = "0" + s_clean
                payload = bytes.fromhex(s_clean)
                if self.tx_auto_crc8:
                    cs_bytes = calc_checksum(payload, self.tx_crc_algo)
                    if cs_bytes:
                        payload = payload + cs_bytes
                if self.tx_append_crlf:
                    payload = payload + b"\r\n"
                self.collector.send(payload)
            else:
                s = self.raw_edit.toPlainText()
                if not s:
                    QMessageBox.warning(self, "提示", "请输入 ASCII 内容")
                    return
                if self.tx_append_crlf and not s.endswith("\r\n") and not s.endswith("\n"):
                    s = s + "\r\n"
                self.collector.send_raw(s, as_text=True)
        except Exception as e:
            self._report_error("发送失败", e)

    def _on_clear_send(self) -> None:
        if self.send_mode == "protocol":
            self.fields_edit.clear()
        else:
            self.raw_edit.clear()

    def _on_toggle_cycle_send(self, checked: bool) -> None:
        if not checked:
            if self._tx_cycle_timer:
                self._tx_cycle_timer.stop()
                self._tx_cycle_timer = None
            self.tx_cycle = False
            self._set_status("已停止循环发送")
            return
        if not (self.collector and self.collector.running):
            QMessageBox.warning(self, "提示", "请先打开串口后再发送")
            self.btn_cycle.setChecked(False)
            return
        self.tx_cycle = True
        self._on_send_once()
        self._tx_cycle_timer = QTimer(self)
        self._tx_cycle_timer.timeout.connect(self._safe(self._on_send_once))
        self._tx_cycle_timer.start(max(10, self.tx_interval_ms))
        self._set_status("循环发送已开始")

    # ================================================================ 
    # 原始数据保存（业务逻辑原样）
    # ================================================================ 

    def _toggle_save_raw(self) -> None:
        self.save_raw_enabled = not self.save_raw_enabled
        if self.save_raw_enabled:
            self.btn_save_raw.setText("停止存储数据")
            if self.is_collecting and not self._save_raw_active:
                self._open_save_raw_file()
        else:
            self.btn_save_raw.setText("开始存储数据")
            self._close_save_raw_file()
        self._set_status("")

    def _choose_save_raw_path(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择保存路径")
        if path:
            self.save_raw_path = path
            self.save_path_edit.setText(path)

    def _write_raw_data(self, data: bytes, ts: float, prefix: str = "") -> None:
        if not self._save_raw_active:
            return
        try:
            self._save_q.put_nowait((ts, prefix, data))
        except Exception:
            pass

    def _start_save_writer(self) -> None:
        self._stop_save_writer()
        self._save_writer_stop.clear()
        try:
            while True:
                self._save_q.get_nowait()
        except Exception:
            pass
        self._save_writer_thread = threading.Thread(
            target=self._save_writer_loop, daemon=True, name="save-raw-writer"
        )
        self._save_writer_thread.start()

    def _stop_save_writer(self) -> None:
        self._save_writer_stop.set()
        t = self._save_writer_thread
        if t is not None and t.is_alive():
            t.join(timeout=1.5)
        self._save_writer_thread = None

    def _save_writer_loop(self) -> None:
        buf: list[str] = []
        buf_n = 0
        last = time.time()
        BATCH_N = 8 * 1024
        BATCH_T = 0.1

        def flush() -> None:
            nonlocal buf, buf_n, last
            if not buf:
                last = time.time()
                return
            f = self.save_raw_file
            if not f:
                buf, buf_n = [], 0
                last = time.time()
                return
            try:
                s = "".join(buf)
                f.write(s)
                f.flush()
                self.save_raw_current_size += len(s)
            except Exception:
                pass
            buf, buf_n = [], 0
            last = time.time()
            if self.save_raw_current_size >= self.save_raw_max_size:
                self.save_raw_count += 1
                # 主线程切换文件
                QTimer.singleShot(0, self._safe(self._rotate_save_raw_file))

        while not self._save_writer_stop.is_set():
            try:
                item = self._save_q.get(timeout=0.1)
            except Empty:
                if buf and (time.time() - last) >= BATCH_T:
                    flush()
                continue
            ts, prefix, data = item
            try:
                ts_str = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                if self._save_raw_as_ascii:
                    text = data.decode("utf-8", errors="replace")
                    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
                        if line.strip():
                            s = f"[{ts_str}] {prefix}{line}\n"
                            buf.append(s)
                            buf_n += len(s)
                else:
                    hex_str = " ".join(f"{b:02X}" for b in data)
                    s = f"[{ts_str}] {prefix}{hex_str}\n"
                    buf.append(s)
                    buf_n += len(s)
            except Exception:
                continue
            if buf_n >= BATCH_N or (time.time() - last) >= BATCH_T:
                flush()
        flush()

    def _open_save_raw_file(self) -> None:
        if self._save_raw_active and self.save_raw_file is not None:
            return
        save_dir = Path(self.save_raw_path)
        if not save_dir.exists():
            try:
                save_dir.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                QMessageBox.critical(self, "路径错误", f"无法创建目录: {e}")
                self.save_raw_enabled = False
                return
        filename = self.save_name_edit.text().strip() or "serial_data"
        if self.save_raw_count > 0:
            filepath = save_dir / f"{filename}_{self.save_raw_count:03d}.dat"
        else:
            filepath = save_dir / f"{filename}.dat"
        try:
            self.save_raw_file = open(filepath, "a", encoding="utf-8")
            try:
                self.save_raw_current_size = filepath.stat().st_size
            except Exception:
                self.save_raw_current_size = 0
            self._save_raw_as_ascii = not self.hex_format
            self._save_raw_active = True
            self._start_save_writer()
            self._set_status(f"正在保存原始数据: {filepath}")
        except Exception as e:
            QMessageBox.critical(self, "文件错误", f"无法打开文件: {e}")
            self.save_raw_enabled = False
            self._save_raw_active = False

    def _rotate_save_raw_file(self) -> None:
        self._close_save_raw_file()
        save_dir = Path(self.save_raw_path)
        filename = self.save_name_edit.text().strip() or "serial_data"
        filepath = save_dir / f"{filename}_{self.save_raw_count:03d}.dat"
        try:
            self.save_raw_file = open(filepath, "w", encoding="utf-8")
            self.save_raw_current_size = 0
            self._save_raw_as_ascii = not self.hex_format
            self._save_raw_active = True
            self._start_save_writer()
            self._set_status(f"原始数据已分割，新文件: {filepath}")
        except Exception:
            self.save_raw_enabled = False
            self._save_raw_active = False

    def _close_save_raw_file(self) -> None:
        self._save_raw_active = False
        self._stop_save_writer()
        if self.save_raw_file:
            try:
                self.save_raw_file.flush()
                self.save_raw_file.close()
            except Exception:
                pass
            self.save_raw_file = None

    # ================================================================ 
    # 指令库（简化但接口兼容）
    # ================================================================ 

    def _cmdlib_path(self, name: str) -> Path:
        return user_data_path("cmdlib") / f"{name}.json"

    def _cmdlib_load(self, force: bool = False) -> None:
        if getattr(self, "_cmdlib_loaded", False) and not force:
            return
        def _load(name: str) -> list:
            p = self._cmdlib_path(name)
            try:
                if p.exists():
                    data = json.loads(p.read_text(encoding="utf-8"))
                    if isinstance(data, list):
                        return data[: self.CMDLIB_MAX]
            except Exception:
                pass
            return []
        self._cmdlib_hex = _load("hex_cmds")
        self._cmdlib_ascii = _load("ascii_cmds")
        self._cmdlib_cycle_hex = _load("cycle_hex")
        self._cmdlib_cycle_ascii = _load("cycle_ascii")
        self._cmdlib_loaded = True
        self._cmdlib_refresh_list()

    def _cmdlib_save_list(self, name: str, items: list) -> None:
        p = self._cmdlib_path(name)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")

    def _cmdlib_current_list(self) -> list[dict]:
        return self._cmdlib_hex if self._cmdlib_mode == "hex" else self._cmdlib_ascii

    def _cmdlib_set_current_list(self, items: list[dict]) -> None:
        items = items[: self.CMDLIB_MAX]
        if self._cmdlib_mode == "hex":
            self._cmdlib_hex = items
            self._cmdlib_save_list("hex_cmds", items)
        else:
            self._cmdlib_ascii = items
            self._cmdlib_save_list("ascii_cmds", items)

    def _cmdlib_refresh_list(self) -> None:
        items = self._cmdlib_current_list()
        self.cmdlib_table.setRowCount(max(len(items), 1))
        for i in range(self.CMDLIB_MAX):
            if i >= self.cmdlib_table.rowCount():
                break
            name = items[i].get("name", "") if i < len(items) else ""
            payload = items[i].get("payload", "") if i < len(items) else ""
            self.cmdlib_table.setItem(i, 0, QTableWidgetItem(str(i + 1)))
            self.cmdlib_table.setItem(i, 1, QTableWidgetItem(name))
            self.cmdlib_table.setItem(i, 2, QTableWidgetItem(payload))
            btn = PushButton("发送")
            btn.clicked.connect(lambda checked=False, idx=i: self._cmdlib_send_by_index(idx))
            self.cmdlib_table.setCellWidget(i, 3, btn)

    def _cmdlib_send_by_index(self, idx: int) -> None:
        items = self._cmdlib_current_list()
        if idx >= len(items):
            return
        self._cmdlib_send_one(items[idx])

    def _cmdlib_send_one(self, item: dict) -> None:
        if not (self.collector and self.collector.running):
            QMessageBox.warning(self, "提示", "请先开始监控")
            return
        payload = (item.get("payload") or "").strip()
        if not payload:
            return
        try:
            if self._cmdlib_mode == "hex":
                s = payload.replace(" ", "").replace("\n", "").replace("\r", "").replace("\t", "")
                if s.lower().startswith("0x"):
                    s = s[2:]
                if not s or len(s) % 2:
                    raise ValueError("HEX 长度必须为偶数")
                data = bytes.fromhex(s)
                if self.tx_auto_crc8:
                    from protocol_parser.parser import calc_checksum
                    cs = calc_checksum(data, self.tx_crc_algo)
                    if cs:
                        data = data + cs
                if self.tx_append_crlf:
                    data = data + b"\r\n"
                self.collector.send(data)
            else:
                text = payload
                if self.tx_append_crlf and not text.endswith("\r\n") and not text.endswith("\n"):
                    text = text + "\r\n"
                data = text.encode("utf-8", errors="replace")
                self.collector.send(data)
            self.tx_frame_count += 1
            self._set_status(f"指令库已发送: {item.get('name', '')}")
            self._update_stats_bar()
        except Exception as e:
            QMessageBox.critical(self, "发送失败", str(e))

    def _cmdlib_toggle_mode(self, checked: bool) -> None:
        self._cmdlib_mode = "hex" if checked else "ascii"
        self.btn_cmdlib_mode.setText("HEX" if checked else "ASCII")
        self._cmdlib_refresh_list()

    def _cmdlib_add_item(self) -> None:
        import uuid
        items = list(self._cmdlib_current_list())
        if len(items) >= self.CMDLIB_MAX:
            QMessageBox.warning(self, "上限", f"最多 {self.CMDLIB_MAX} 条")
            return
        items.append({"id": uuid.uuid4().hex, "name": "新指令", "payload": ""})
        self._cmdlib_set_current_list(items)
        self._cmdlib_refresh_list()

    def _cmdlib_on_cell_double(self, row: int, col: int) -> None:
        if col not in (1, 2):
            return
        items = list(self._cmdlib_current_list())
        while len(items) <= row:
            items.append({"id": "", "name": "", "payload": ""})
        item = self.cmdlib_table.item(row, col)
        if item is None:
            return
        val = item.text()
        if col == 1:
            items[row]["name"] = val
        else:
            items[row]["payload"] = val
        self._cmdlib_set_current_list(items)


    def _cmdlib_open_cycle_config(self) -> None:
        """配置循环发送：勾选 + 间隔 + 手动排序。"""
        items = [it for it in self._cmdlib_current_list() if it.get("id")]
        if not items:
            QMessageBox.information(self, "提示", "当前指令库为空，请先添加指令")
            return
        is_hex = self._cmdlib_mode == "hex"
        seq = list(self._cmdlib_cycle_hex if is_hex else self._cmdlib_cycle_ascii)
        dlg = CycleConfigDialog(self, items, seq, is_hex)
        if dlg.exec() == QDialog.Accepted and dlg.result_seq is not None:
            if is_hex:
                self._cmdlib_cycle_hex = dlg.result_seq
                self._cmdlib_save_list("cycle_hex", dlg.result_seq)
            else:
                self._cmdlib_cycle_ascii = dlg.result_seq
                self._cmdlib_save_list("cycle_ascii", dlg.result_seq)
            self._set_status(f"循环配置已保存（{len(dlg.result_seq)} 条）")

    def _cmdlib_toggle_cycle(self) -> None:
        if self._cmdlib_cycle_on:
            self._cmdlib_stop_cycle()
            return
        seq = self._cmdlib_cycle_hex if self._cmdlib_mode == "hex" else self._cmdlib_cycle_ascii
        if not seq:
            QMessageBox.information(self, "提示", "请先配置循环指令")
            return
        if not (self.collector and self.collector.running):
            QMessageBox.warning(self, "提示", "请先开始监控")
            return
        self._cmdlib_cycle_on = True
        self._cmdlib_cycle_idx = 0
        self.btn_cmdlib_cycle.setText("停止循环")
        self._cmdlib_cycle_tick()

    def _cmdlib_stop_cycle(self) -> None:
        self._cmdlib_cycle_on = False
        if self._cmdlib_cycle_timer:
            self._cmdlib_cycle_timer.stop()
            self._cmdlib_cycle_timer = None
        self.btn_cmdlib_cycle.setText("循环发送")

    def _cmdlib_cycle_tick(self) -> None:
        if not self._cmdlib_cycle_on:
            return
        seq = self._cmdlib_cycle_hex if self._cmdlib_mode == "hex" else self._cmdlib_cycle_ascii
        if not seq:
            self._cmdlib_stop_cycle()
            return
        step = seq[self._cmdlib_cycle_idx % len(seq)]
        pool = {x.get("id"): x for x in self._cmdlib_current_list()}
        item = pool.get(step.get("id"))
        if item:
            self._cmdlib_send_one(item)
        delay = max(10, int(step.get("delay_ms", 1000) or 1000))
        self._cmdlib_cycle_idx = (self._cmdlib_cycle_idx + 1) % len(seq)
        self._cmdlib_cycle_timer = QTimer(self)
        self._cmdlib_cycle_timer.setSingleShot(True)
        self._cmdlib_cycle_timer.timeout.connect(self._cmdlib_cycle_tick)
        self._cmdlib_cycle_timer.start(delay)

    # ================================================================ 
    # 其他 UI 事件
    # ================================================================ 

    def _toggle_send_panel(self) -> None:
        vis = not self.send_card.isVisible()
        self.send_card.setVisible(vis)
        self.btn_send_panel.setText("关闭指令发送" if vis else "打开指令发送")

    def _toggle_cmdlib_panel(self) -> None:
        vis = not self.cmdlib_card.isVisible()
        self.cmdlib_card.setVisible(vis)
        self.btn_cmdlib.setText("关闭指令库" if vis else "打开指令库")

    def _on_hex_toggled(self, checked: bool) -> None:
        self.hex_format = checked
        self.btn_hex.setText("HEX 格式" if checked else "ASCII 格式")
        if not checked:
            self.view_mode = "raw"
            self.btn_view_mode.setChecked(False)
            self.btn_view_mode.setEnabled(False)
        else:
            self.btn_view_mode.setEnabled(True)
        if self.collector:
            self.collector.raw_mode = (not checked) or (self.view_mode == "raw")
            direction = None
            if checked:
                direction = "request" if self.serial_sender == "模组发送" else "response"
            self.collector.direction = direction

    def _on_view_mode_toggled(self, checked: bool) -> None:
        self.view_mode = "protocol" if checked else "raw"
        self.btn_view_mode.setText("协议解析模式" if checked else "原始数据模式")
        if self.collector:
            self.collector.raw_mode = (not self.hex_format) or (self.view_mode == "raw")

    def _on_sender_toggled(self, checked: bool) -> None:
        self.serial_sender = "模组发送" if checked else "MCU发送"
        self.btn_sender.setText(self.serial_sender)
        if self.collector and self.hex_format:
            self.collector.direction = "request" if checked else "response"

    def _on_topmost_toggled(self, checked: bool) -> None:
        self.setWindowFlag(Qt.WindowStaysOnTopHint, checked)
        self.show()  # 需要重新 show 才能生效

    def _add_serial_port(self) -> None:
        ports = SerialCollector.list_ports()
        if not ports:
            QMessageBox.warning(self, "提示", "未找到可用串口")
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("添加串口")
        dlg.setFixedSize(420, 160)
        lay = QFormLayout(dlg)
        port_combo = ComboBox()
        display_list = []
        for p in ports:
            desc = p.get("description", "")
            if desc and desc != p["device"]:
                display_list.append(f'{p["device"]} - {desc}')
            else:
                display_list.append(p["device"])
        port_combo.addItems(display_list)
        lay.addRow("串口:", port_combo)
        baud_combo = ComboBox()
        baud_combo.setEditable(True)
        baud_combo.addItems(["9600", "115200", "921600", "1000000"])
        baud_combo.setCurrentText("9600")
        lay.addRow("波特率:", baud_combo)
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        lay.addRow(btns)
        if dlg.exec() == QDialog.Accepted:
            port_display = port_combo.currentText()
            port = port_display.split(" - ")[0].strip()
            try:
                baud = int(baud_combo.currentText().strip())
            except Exception:
                baud = 9600
            self._spawn_monitor(port, baud)

    def _spawn_monitor(self, port: str, baudrate: int) -> None:
        import subprocess
        if getattr(sys, "frozen", False):
            cmd = [sys.executable, "--monitor", port, str(int(baudrate))]
            DETACHED_PROCESS = 0x00000008
            try:
                subprocess.Popen(cmd, creationflags=DETACHED_PROCESS, close_fds=True)
            except Exception as e:
                QMessageBox.critical(self, "启动失败", f"无法启动新进程: {e}")
        else:
            script_path = Path(__file__).resolve()
            try:
                subprocess.Popen([sys.executable, str(script_path), "--monitor", port, str(int(baudrate))], close_fds=True)
            except Exception as e:
                QMessageBox.critical(self, "启动失败", f"无法启动新进程: {e}")

    def _apply_monitor_args(self) -> None:
        self._refresh_ports()
        if self._monitor_port:
            for i in range(self.port_combo.count()):
                text = self.port_combo.itemText(i)
                if text.startswith(self._monitor_port):
                    self.port_combo.setCurrentIndex(i)
                    break
        self.baud_combo.setCurrentText(str(self._monitor_baud))
        self.setWindowTitle(f"串口监控 v{VERSION} - {self._monitor_port} @ {self._monitor_baud}")

    def _choose_log(self) -> None:
        content = self.serial_text.toPlainText()
        if not content.strip():
            QMessageBox.information(self, "保存日志", "当前实时数据为空")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "保存日志",
            f"protocol_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log",
            "日志文件 (*.log);;文本文件 (*.txt)"
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(f"===== 导出时间 {datetime.now().isoformat(timespec='seconds')} =====\n")
                f.write(content)
                if not content.endswith("\n"):
                    f.write("\n")
            self._set_status(f"日志已保存: {path}")
            QMessageBox.information(self, "保存日志", f"已保存到:\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "保存失败", str(e))

    def closeEvent(self, event) -> None:
        try:
            if self.is_collecting:
                self._stop_serial()
            self._close_save_raw_file()
            if self.log_file:
                try:
                    self.log_file.close()
                except Exception:
                    pass
        finally:
            event.accept()


# ---------- 启动 ----------

def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--monitor", nargs=2, metavar=("PORT", "BAUD"), default=None)
    args, _ = ap.parse_known_args()

    monitor_port = None
    monitor_baud = 9600
    if args.monitor is not None:
        monitor_port = args.monitor[0]
        try:
            monitor_baud = int(args.monitor[1])
        except Exception:
            monitor_baud = 9600

    # 高 DPI
    if sys.platform == "win32":
        try:
            from ctypes import windll
            try:
                windll.shcore.SetProcessDpiAwareness(2)
            except Exception:
                try:
                    windll.user32.SetProcessDPIAware()
                except Exception:
                    pass
        except Exception:
            pass

    try:
        app = QApplication(sys.argv)
        app.setStyle("Fusion")
        window = ProtocolParserApp(monitor_port=monitor_port, monitor_baud=monitor_baud)
        window.show()
        return app.exec()
    except BaseException as e:
        log_path = _write_crash_log_gui(e)
        try:
            friendly, _ = classify_protocol_error(e)
            print(f"[启动失败] {friendly}", file=sys.stderr)
            if log_path:
                print(f"           详细日志: {log_path}", file=sys.stderr)
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
