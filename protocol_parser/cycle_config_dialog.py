"""Cycle-send configuration dialog extracted from gui.py."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import BodyLabel, CheckBox, PrimaryPushButton, PushButton

from protocol_parser.dpi_font import (
    UI_FONT_BASE_POINT_SIZE,
    fit_dialog_to_content,
    fit_text_control,
)
from protocol_parser.log_text_style import make_crisp_ui_font
from protocol_parser.widgets import CellWidgetAlignedTable, apply_fluent_dialog_style

class CycleOrderTable(CellWidgetAlignedTable):
    """循环发送配置表：支持拖动整行调整发送顺序。"""

    rowMoveRequested = Signal(int, int)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropOverwriteMode(False)
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setDefaultDropAction(Qt.MoveAction)

    def dropEvent(self, event) -> None:  # type: ignore[override]
        source_row = self.currentRow()
        if source_row < 0:
            event.ignore()
            return

        try:
            pos = event.position().toPoint()
        except AttributeError:
            pos = event.pos()

        index = self.indexAt(pos)
        if index.isValid():
            target_row = index.row()
            if pos.y() > self.visualRect(index).center().y():
                target_row += 1
        else:
            target_row = self.rowCount()

        # 删除源行后，位于其下方的目标索引需要前移一位。
        if target_row > source_row:
            target_row -= 1
        target_row = max(0, min(target_row, self.rowCount() - 1))

        if target_row != source_row:
            self.rowMoveRequested.emit(source_row, target_row)

        event.acceptProposedAction()


# ---------- 指令库「配置循环发送」对话框 ----------

class CycleConfigDialog(QDialog):
    """勾选指令、设置独立间隔，并通过鼠标拖动调整发送顺序。"""

    def __init__(
        self,
        parent: QWidget | None,
        items: list[dict],
        seq: list[dict],
        is_hex: bool,
    ):
        super().__init__(parent)
        apply_fluent_dialog_style(self)
        self.setWindowTitle("配置循环发送")
        self._cycle_font = make_crisp_ui_font(UI_FONT_BASE_POINT_SIZE)
        self.setFont(self._cycle_font)
        self.resize(720, 480)
        self.setMinimumSize(600, 380)
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
        fit_dialog_to_content(
            self,
            preferred_width=760,
            minimum=(560, 360),
            margin=(36, 72),
            point_size=UI_FONT_BASE_POINT_SIZE,
            include_tables=True,
        )

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)

        intro_label = BodyLabel(
            "勾选参与循环的指令；每条指令可设置独立间隔(ms)；按住任意一行拖动即可调整发送顺序。"
        )
        intro_label.setFont(self._cycle_font)
        intro_label.setWordWrap(True)
        layout.addWidget(intro_label)

        body = QHBoxLayout()
        self.table = CycleOrderTable()
        self.table.setFont(self._cycle_font)
        self.table.rowMoveRequested.connect(self._move_row)
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["参与", "名称", "指令数据", "间隔(ms)"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        cycle_header = self.table.horizontalHeader()
        cycle_header.setSectionResizeMode(2, QHeaderView.Stretch)
        header_font = QFont(self._cycle_font)
        header_font.setWeight(QFont.Weight.DemiBold)
        cycle_header.setFont(header_font)
        cycle_metrics = QFontMetrics(self._cycle_font)
        self._cycle_row_height = max(32, cycle_metrics.height() + 12)
        cycle_header.setMinimumHeight(max(30, QFontMetrics(header_font).height() + 10))
        self.table.verticalHeader().setDefaultSectionSize(self._cycle_row_height)
        self.table.verticalHeader().setMinimumSectionSize(self._cycle_row_height)
        self.table.setColumnWidth(0, max(50, cycle_metrics.horizontalAdvance("参与") + 24))
        self.table.setColumnWidth(1, max(140, cycle_metrics.horizontalAdvance("名称") + 80))
        self.table.setColumnWidth(3, max(100, cycle_metrics.horizontalAdvance("间隔(ms)") + 28))
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
            name_item.setFont(self._cycle_font)
            name_item.setFlags(name_item.flags() & ~Qt.ItemIsEditable)
            name_item.setData(Qt.UserRole, cid)
            self.table.setItem(row, 1, name_item)

            payload_item = QTableWidgetItem(it.get("payload") or "")
            payload_item.setFont(self._cycle_font)
            payload_item.setFlags(payload_item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(row, 2, payload_item)

            delay_item = QTableWidgetItem(delay)
            delay_item.setFont(self._cycle_font)
            self.table.setItem(row, 3, delay_item)
            self.table.setRowHeight(row, self._cycle_row_height)

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
            b.setFont(self._cycle_font)
            fit_text_control(b, point_size=UI_FONT_BASE_POINT_SIZE)
            b.clicked.connect(slot)
            side.addWidget(b)
        side.addStretch()
        body.addLayout(side)
        layout.addLayout(body, stretch=1)

        bf = QHBoxLayout()
        bf.addStretch()
        btn_save = PrimaryPushButton("保存")
        btn_save.setFont(self._cycle_font)
        fit_text_control(btn_save, point_size=UI_FONT_BASE_POINT_SIZE)
        btn_save.clicked.connect(self._on_save)
        bf.addWidget(btn_save)
        btn_cancel = PushButton("取消")
        btn_cancel.setFont(self._cycle_font)
        fit_text_control(btn_cancel, point_size=UI_FONT_BASE_POINT_SIZE)
        btn_cancel.clicked.connect(self.reject)
        bf.addWidget(btn_cancel)
        layout.addLayout(bf)

    def _selected_row(self) -> int:
        rows = self.table.selectionModel().selectedRows()
        return rows[0].row() if rows else -1

    def _row_records(self) -> list[dict]:
        records: list[dict] = []
        for row in range(self.table.rowCount()):
            cell = self.table.cellWidget(row, 0)
            checked = bool(cell and hasattr(cell, "_chk") and cell._chk.isChecked())
            name_item = self.table.item(row, 1)
            payload_item = self.table.item(row, 2)
            delay_item = self.table.item(row, 3)
            records.append({
                "id": name_item.data(Qt.UserRole) if name_item else "",
                "checked": checked,
                "name": name_item.text() if name_item else "",
                "payload": payload_item.text() if payload_item else "",
                "delay": delay_item.text() if delay_item else "1000",
            })
        return records

    def _populate_records(self, records: list[dict]) -> None:
        self.table.setRowCount(len(records))
        for row, record in enumerate(records):
            chk = CheckBox()
            chk.setChecked(bool(record.get("checked")))
            cell = QWidget()
            lay = QHBoxLayout(cell)
            lay.setContentsMargins(8, 0, 0, 0)
            lay.addWidget(chk)
            lay.addStretch()
            cell._chk = chk  # type: ignore[attr-defined]
            self.table.setCellWidget(row, 0, cell)

            name_item = QTableWidgetItem(str(record.get("name") or ""))
            name_item.setFont(self._cycle_font)
            name_item.setFlags(name_item.flags() & ~Qt.ItemIsEditable)
            name_item.setData(Qt.UserRole, record.get("id") or "")
            self.table.setItem(row, 1, name_item)

            payload_item = QTableWidgetItem(str(record.get("payload") or ""))
            payload_item.setFont(self._cycle_font)
            payload_item.setFlags(payload_item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(row, 2, payload_item)
            delay_item = QTableWidgetItem(str(record.get("delay") or "1000"))
            delay_item.setFont(self._cycle_font)
            self.table.setItem(row, 3, delay_item)
            self.table.setRowHeight(row, getattr(self, "_cycle_row_height", 32))

    def _move_row(self, source_row: int, target_row: int) -> None:
        records = self._row_records()
        if not (0 <= source_row < len(records) and 0 <= target_row < len(records)):
            return
        record = records.pop(source_row)
        records.insert(target_row, record)
        self._populate_records(records)
        self.table.selectRow(target_row)

    def _move(self, delta: int) -> None:
        row = self._selected_row()
        if row < 0:
            return
        target = row + delta
        if 0 <= target < self.table.rowCount():
            self._move_row(row, target)

    def _move_edge(self, to_top: bool) -> None:
        row = self._selected_row()
        if row < 0:
            return
        target = 0 if to_top else self.table.rowCount() - 1
        if row != target:
            self._move_row(row, target)

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

