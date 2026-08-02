"""主窗口：组装所有子面板 + 信号槽绑定。

红线：
- 业务逻辑零修改：仅 import protocol_parser.* 的 API
- 串口收发用 SerialWorker (QThread)，定时发送用 CycleSendTimer (QTimer)
- 不在模块顶层实例化 QWidget —— 由 ui.app.py 先建 QApplication 再 import 本模块
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QSplitter, QVBoxLayout, QHBoxLayout, QScrollArea,
    QApplication,
)
from qfluentwidgets import (
    FluentIcon as FIF, InfoBar, InfoBarPosition, MSFluentWindow,
    NavigationItemPosition,
)

from protocol_parser import (
    VERSION, get_builtin_v3, list_serial_ports, load_protocol, merge_protocol,
    parse_frame, parse_hex_input, classify_protocol_error, calc_checksum,
    EncodeFrameError, ProtocolError, to_hex,
)
from protocol_parser.docx_importer import import_and_save, import_from_docx
from protocol_parser.paths import get_protocol_dir
from protocol_parser.session_snapshot import (
    SessionSnapshot, save_snapshot, load_snapshot, clear_snapshot,
)

from .workers import SerialWorker, CycleSendTimer
from .serial_panel import SerialPanel
from .tx_panel import TxPanel
from .parser_panel import ParserPanel
from .cmd_library_panel import CmdLibraryPanel
from .log_panel import LogPanel
from .settings_drawer import SettingsDrawer, write_log_csv


class MainWindow(QMainWindow):
    """主窗口：Win11 Fluent 风格，组装所有子面板。"""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"串口数据解析工具 v{VERSION}")
        self.resize(1280, 820)

        # 业务对象（不实例化 QWidget）
        self._worker = SerialWorker(self)
        self._cycle_timer = CycleSendTimer(1000, self)
        self._cfg: dict = get_builtin_v3()
        self._direction: Optional[str] = None  # request / response / None(自动)

        # 构建 UI（此时 QApplication 已存在）
        self._build_ui()
        self._connect_signals()

        # 启动时尝试恢复会话快照
        QTimer.singleShot(100, self._maybe_restore_session)

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        # 顶部串口面板（满宽）
        self.serial_panel = SerialPanel(self._worker, central)
        root.addWidget(self.serial_panel)

        # 中间区域：左 (TX + CmdLib) | 右 (Parser + Settings)
        mid_split = QSplitter(Qt.Orientation.Horizontal, central)
        mid_split.setChildrenCollapsible(False)

        # 左侧
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(10)
        self.tx_panel = TxPanel(self._worker, self._cycle_timer, left_widget)
        left_layout.addWidget(self.tx_panel)
        self.cmd_library_panel = CmdLibraryPanel(left_widget)
        left_layout.addWidget(self.cmd_library_panel, 1)
        mid_split.addWidget(left_widget)

        # 右侧
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(10)
        self.parser_panel = ParserPanel(right_widget)
        right_layout.addWidget(self.parser_panel)
        self.settings_drawer = SettingsDrawer(right_widget)
        right_layout.addWidget(self.settings_drawer, 1)
        mid_split.addWidget(right_widget)

        mid_split.setStretchFactor(0, 1)
        mid_split.setStretchFactor(1, 1)
        mid_split.setSizes([600, 600])
        root.addWidget(mid_split, 2)

        # 底部日志面板（满宽，吃掉剩余空间）
        self.log_panel = LogPanel(central)
        root.addWidget(self.log_panel, 3)

        # 把当前协议 cfg 同步给命令库
        self.cmd_library_panel.set_protocol_cfg(self._cfg)

    # ------------------------------------------------------------------ 信号
    def _connect_signals(self) -> None:
        # ---- 串口面板 ----
        self.serial_panel.openRequested.connect(self._on_open_serial)
        self.serial_panel.closeRequested.connect(self._on_close_serial)
        self.serial_panel.refreshRequested.connect(self._on_refresh)

        # ---- SerialWorker 信号 ----
        self._worker.portOpened.connect(lambda: self.serial_panel.set_connected(True))
        self._worker.portClosed.connect(lambda: self.serial_panel.set_connected(False))
        self._worker.frameReceived.connect(self._on_frame_received)
        self._worker.rawReceived.connect(self._on_raw_received)
        self._worker.txSent.connect(self._on_tx_sent)
        self._worker.errorOccurred.connect(self._on_error)

        # ---- TX 面板 ----
        self.tx_panel.sendRequested.connect(self._on_send_requested)
        self.tx_panel.cycleSendToggled.connect(self._on_cycle_toggled)
        self._cycle_timer.triggered_send.connect(self.tx_panel.trigger_send_now)

        # ---- Parser 面板 ----
        self.parser_panel.parseRequested.connect(self._on_parse_requested)
        self.parser_panel.appendChecksumRequested.connect(self._on_append_checksum)

        # ---- 命令库 ----
        self.cmd_library_panel.cmdSelected.connect(self._on_cmd_selected)

        # ---- 设置侧栏 ----
        self.settings_drawer.themeChanged.connect(self._on_theme_changed)
        self.settings_drawer.fontPointSizeChanged.connect(self._on_font_size_changed)
        self.settings_drawer.importDocxRequested.connect(self._on_import_docx)
        self.settings_drawer.exportLogsRequested.connect(self._on_export_logs)
        self.settings_drawer.saveSnapshotRequested.connect(self._on_save_snapshot)
        self.settings_drawer.loadSnapshotRequested.connect(self._on_load_snapshot)

    # ==================================================================
    # 串口
    # ==================================================================
    def _on_open_serial(self, port: str, baud: int, bytesize: int, stopbits: float, parity_idx: int) -> None:
        try:
            self._worker.open(
                cfg=self._cfg,
                port=port,
                baudrate=baud,
                bytesize=bytesize,
                stopbits=stopbits,
                direction=self._direction,
                raw_mode=not bool(self._cfg.get("frame")),
            )
        except Exception as e:
            self._show_error("打开串口失败", str(e))

    def _on_close_serial(self) -> None:
        # 先停定时发送
        if self._cycle_timer.isActive():
            self._cycle_timer.stop_cycle()
        self._worker.close()

    def _on_refresh(self) -> None:
        # SerialPanel.refresh_ports 已自调用，这里只做日志提示
        try:
            ports = list_serial_ports()
            self._show_info("刷新串口", f"共检测到 {len(ports)} 个串口")
        except Exception as e:
            self._show_error("刷新失败", str(e))

    # ==================================================================
    # 接收 / TX 回调
    # ==================================================================
    def _on_frame_received(self, result, frame, ts: float) -> None:
        # 实时解析结果显示在日志面板
        self.log_panel.append_frame_result(result, ts)
        # 同时把最新收到的原始 HEX 同步到解析面板（用户可继续手动解析）
        if result.raw_hex:
            self.parser_panel.set_hex_text(result.raw_hex)

    def _on_raw_received(self, data: bytes, ts: float) -> None:
        self.log_panel.append_rx_raw(data, ts)

    def _on_tx_sent(self, data: bytes, dir_label: str, ts: float) -> None:
        self.log_panel.append_tx(data, ts)

    def _on_error(self, msg: str) -> None:
        self.log_panel.append_error(msg)
        self._show_error("串口错误", msg)

    # ==================================================================
    # 发送
    # ==================================================================
    def _on_send_requested(self, payload: str, as_text: bool) -> None:
        if not self._worker.is_open:
            self._show_error("发送失败", "串口未打开，请先开始监控再发送")
            return
        try:
            if as_text:
                self._worker.send_raw(payload, as_text=True)
            else:
                self._worker.send(payload)  # HEX
        except EncodeFrameError as e:
            friendly, debug = classify_protocol_error(e)
            self._show_error("HEX 解析失败", f"{friendly}\n{debug}")
            self.log_panel.append_error(f"TX 失败: {friendly}")
        except Exception as e:
            friendly, _ = classify_protocol_error(e)
            self._show_error("发送失败", friendly)
            self.log_panel.append_error(f"TX 失败: {friendly}")

    def _on_cycle_toggled(self, enabled: bool, interval_ms: int) -> None:
        if enabled and not self._worker.is_open:
            self._show_warning("定时发送", "串口未打开，定时发送将不会真正写入串口")
        # CycleSendTimer 的启停已在 TxPanel 内部完成，这里仅用于 UI 反馈
        if enabled:
            self._show_info("定时发送", f"已开启，间隔 {interval_ms} ms")

    # ==================================================================
    # 解析
    # ==================================================================
    def _on_parse_requested(self, hex_str: str) -> None:
        try:
            data = parse_hex_input(hex_str)
            result = parse_frame(data, self._cfg, direction=self._direction)
            self.log_panel.append_frame_result(result)
        except ProtocolError as e:
            friendly, debug = classify_protocol_error(e)
            self.log_panel.append_error(f"解析失败: {friendly}")
            self._show_error("解析失败", friendly)
        except Exception as e:
            friendly, _ = classify_protocol_error(e)
            self.log_panel.append_error(f"解析异常: {friendly}")

    def _on_append_checksum(self, hex_str: str, algo: str) -> None:
        """调用 protocol_parser.calc_checksum 计算校验并追加到输入框。"""
        try:
            data = parse_hex_input(hex_str)
            cs = calc_checksum(data, algo)
            cs_hex = " ".join(f"{b:02X}" for b in cs)
            new_text = f"{to_hex(data)} {cs_hex}"
            self.parser_panel.set_hex_text(new_text)
            self._show_info("校验和已追加", f"算法={algo}, 校验字节={cs_hex}")
        except ProtocolError as e:
            friendly, _ = classify_protocol_error(e)
            self._show_error("校验计算失败", friendly)
        except Exception as e:
            friendly, _ = classify_protocol_error(e)
            self._show_error("校验计算失败", friendly)

    # ==================================================================
    # 命令库
    # ==================================================================
    def _on_cmd_selected(self, payload: str, mode: str) -> None:
        self.tx_panel.fill_payload(payload, mode)
        self._show_info("已填入发送区", payload[:60])

    # ==================================================================
    # 设置侧栏回调
    # ==================================================================
    def _on_theme_changed(self, mode: str) -> None:
        # qfluentwidgets.setTheme 已在 SettingsDrawer 内部调用，这里仅日志
        pass

    def _on_font_size_changed(self, size: int) -> None:
        app = QApplication.instance()
        if app is None:
            return
        font = app.font()
        font.setPointSize(int(size))
        app.setFont(font)
        # 同时更新已有控件的字体
        for w in app.topLevelWidgets():
            self._apply_font_recursive(w, font)

    @staticmethod
    def _apply_font_recursive(widget, font: QFont) -> None:
        widget.setFont(font)
        from PySide6.QtWidgets import QObject
        for child in widget.findChildren(QObject):
            try:
                child.setFont(font)
            except Exception:
                pass

    def _on_import_docx(self, path: str) -> None:
        """导入 Word 协议文档，调用 docx_importer。"""
        try:
            proto_dir = get_protocol_dir()
            cfg, out_path = import_and_save(path, proto_dir)
            warnings = cfg.get("_import_warnings", [])
            # 合并到内置 V3
            base = get_builtin_v3()
            self._cfg = merge_protocol(base, cfg)
            self.cmd_library_panel.set_protocol_cfg(self._cfg)
            msg = f"已导入: {out_path.name}\n命令 {len(cfg.get('commands', []))} 条"
            if warnings:
                msg += "\n\n警告:\n" + "\n".join(warnings)
            self._show_info("导入成功", msg)
        except Exception as e:
            friendly, debug = classify_protocol_error(e)
            self._show_error("Word 导入失败", f"{friendly}\n{debug}")

    def _on_export_logs(self, path: str, fmt: str) -> None:
        """导出收发日志到 Excel/CSV（UTF-8-SIG + 中文表头）。"""
        lines = self.log_panel.export_lines()
        ok = write_log_csv(path, lines)
        if ok:
            self._show_info("导出成功", f"已写入 {len(lines)} 行到\n{path}")
        else:
            self._show_error("导出失败", "写入文件失败，请检查路径权限")

    # ==================================================================
    # 会话快照
    # ==================================================================
    def _on_save_snapshot(self) -> None:
        snap = self._build_snapshot()
        try:
            save_snapshot(snap)
            self._show_info("会话已保存", "当前串口/协议/发送配置已持久化")
        except Exception as e:
            self._show_error("保存失败", str(e))

    def _on_load_snapshot(self) -> None:
        snap = load_snapshot()
        if snap is None:
            self._show_warning("恢复会话", "未找到会话快照")
            return
        self._apply_snapshot(snap)
        self._show_info("会话已恢复", "已恢复上次保存的配置")

    def _build_snapshot(self) -> SessionSnapshot:
        sc = self.serial_panel.current_config()
        tx = self.tx_panel.current_state()
        return SessionSnapshot(
            was_collecting=self._worker.is_open,
            port=sc.get("port", ""),
            baudrate=sc.get("baudrate", 9600),
            bytesize=sc.get("bytesize", 8),
            stopbits=sc.get("stopbits", 1.0),
            tx_send_mode="raw_hex" if tx.get("send_mode") == "hex" else "raw_ascii",
            tx_raw=tx.get("payload", ""),
            tx_cycle_enabled=tx.get("cycle_enabled", False),
            tx_interval_ms=tx.get("interval_ms", 1000),
            is_update_session=False,
        )

    def _apply_snapshot(self, snap: SessionSnapshot) -> None:
        # 串口面板
        self.serial_panel.apply_config({
            "port": snap.port,
            "baudrate": snap.baudrate,
            "bytesize": snap.bytesize,
            "stopbits": snap.stopbits,
        })
        # 发送面板
        self.tx_panel.apply_state({
            "send_mode": "hex" if snap.tx_send_mode in ("raw_hex", "protocol", "") else "ascii",
            "payload": snap.tx_raw,
            "interval_ms": snap.tx_interval_ms,
        })
        # 自动恢复接收（仅当之前在接收 且 用户选了"恢复"）
        if snap.was_collecting and snap.port and not self._worker.is_open:
            QTimer.singleShot(300, lambda: self.serial_panel.openRequested.emit(
                snap.port, snap.baudrate, snap.bytesize, snap.stopbits, 0
            ))

    def _maybe_restore_session(self) -> None:
        """启动时若存在 is_update_session=True 的快照，自动恢复（来自在线更新）。"""
        snap = load_snapshot()
        if snap is None or not snap.is_update_session:
            return
        self._apply_snapshot(snap)
        self._show_info("会话已恢复", "更新前的会话已自动恢复")
        clear_snapshot()

    # ==================================================================
    # 关闭事件：保存偏好
    # ==================================================================
    def closeEvent(self, event) -> None:
        try:
            # 停定时发送
            if self._cycle_timer.isActive():
                self._cycle_timer.stop_cycle()
            # 关串口
            if self._worker.is_open:
                self._worker.close()
            # 写入正常关闭时的偏好快照（is_update_session=False）
            snap = self._build_snapshot()
            save_snapshot(snap)
        except Exception:
            pass
        super().closeEvent(event)

    # ==================================================================
    # 通知
    # ==================================================================
    def _show_info(self, title: str, content: str) -> None:
        InfoBar.info(title=title, content=content, orient=Qt.Orientation.Horizontal,
                     isClosable=True, position=InfoBarPosition.TOP,
                     duration=3000, parent=self)

    def _show_warning(self, title: str, content: str) -> None:
        InfoBar.warning(title=title, content=content, orient=Qt.Orientation.Horizontal,
                        isClosable=True, position=InfoBarPosition.TOP,
                        duration=4000, parent=self)

    def _show_error(self, title: str, content: str) -> None:
        InfoBar.error(title=title, content=content, orient=Qt.Orientation.Horizontal,
                      isClosable=True, position=InfoBarPosition.TOP,
                      duration=5000, parent=self)
