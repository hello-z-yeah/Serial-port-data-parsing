"""PySide6 + qfluentwidgets 通用控件辅助。"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QModelIndex, QObject, QEvent
from PySide6.QtWidgets import (
    QLabel, QWidget, QVBoxLayout, QHBoxLayout, QFrame, QPushButton,
    QButtonGroup, QDialog, QSizePolicy, QTableWidget, QTableWidgetItem, QStyle, QHeaderView, QMessageBox,
)
from qfluentwidgets import (
    ToolTipFilter, ToolTipPosition, CardWidget, StrongBodyLabel,
    BodyLabel, PrimaryPushButton, PushButton, TableWidget, ToolTip,
)

from .theme import PALETTE
from .ui_corners import CORNER_RADIUS_PX
from .dpi_font import fit_text_control, apply_adaptive_geometry, fit_dialog_to_content


def stabilize_transient_dialog(
    widget: QWidget,
    *,
    min_width: int | None = 360,
    max_width: int | None = 520,
) -> None:
    """Pin a modal window so global adaptive-geometry cannot bounce its size."""
    widget.setProperty("smstSkipGlobalAdaptiveUi", True)
    if min_width is not None:
        widget.setMinimumWidth(int(min_width))
    if max_width is not None:
        widget.setMaximumWidth(int(max_width))


def stabilize_native_message_box(
    box: QMessageBox,
    *,
    min_width: int = 420,
    max_width: int = 480,
) -> None:
    """Prevent global adaptive-geometry from resizing native QMessageBox in a loop.

    Long wrapped text otherwise triggers LayoutRequest/Resize feedback on the
    dialog window and makes it flicker horizontally.
    """
    stabilize_transient_dialog(box, min_width=min_width, max_width=max_width)
    extra = f"""
        QMessageBox {{
            min-width: {min_width}px;
            max-width: {max_width}px;
        }}
        QMessageBox QLabel {{
            min-width: {max(280, min_width - 80)}px;
            max-width: {max(320, max_width - 80)}px;
        }}
    """
    box.setStyleSheet((box.styleSheet() or "") + extra)


def ask_yes_no(
    parent: QWidget | None,
    title: str,
    text: str,
    *,
    default_no: bool = True,
) -> "QMessageBox.StandardButton":
    """Yes/No prompt using the same Fluent card style as other message boxes."""
    return StyledMessageBox.question(
        parent,
        title,
        text,
        default_button=(
            QMessageBox.StandardButton.No
            if default_no
            else QMessageBox.StandardButton.Yes
        ),
    )


class FluentCellToolTipFilter(QObject):
    """表格单元格悬停提示统一为灰色 Fluent 气泡, 替代系统黄底。"""

    def __init__(self, table: QTableWidget):
        super().__init__(table)
        self._table = table
        self._tooltip: ToolTip | None = None
        table.viewport().installEventFilter(self)

    def _hide(self) -> None:
        if self._tooltip is not None:
            self._tooltip.hide()

    def eventFilter(self, obj, event) -> bool:
        try:
            viewport = self._table.viewport()
        except RuntimeError:
            # 表格已被销毁，避免在应用退出时访问已删除的 C++ 对象。
            return False
        if obj is viewport and event.type() == QEvent.Type.ToolTip:
            item = self._table.itemAt(event.pos())
            if item is not None:
                text = str(item.toolTip() or "").strip()
                if text:
                    if self._tooltip is None:
                        self._tooltip = ToolTip(text, self._table.window())
                    else:
                        self._tooltip.setText(text)
                    self._tooltip.adjustSize()
                    gp = viewport.mapToGlobal(event.pos())
                    x = gp.x() - self._tooltip.width() // 2
                    y = gp.y() - self._tooltip.height() - 8
                    if y < 0:
                        y = gp.y() + 20
                    self._tooltip.move(x, y)
                    self._tooltip.show()
                    return True
            self._hide()
        elif event.type() in (QEvent.Type.Leave, QEvent.Type.Hide,
                              QEvent.Type.MouseButtonPress):
            self._hide()
        return super().eventFilter(obj, event)


class CellWidgetAlignedTable(TableWidget):
    """修复 qfluentwidgets 表格 cellWidget 的两种错位。

    1) cellWidget 不随滚动条移动(冻结在初始位置) → 滚动条
       valueChanged 时按 visualRect 强制对齐;
    2) 表格尺寸/几何更新时 cellWidget 被 Qt 摆到瞬态错误位置
       (开关发送面板、窗口缩放时的复选框抖动) → 在 Qt 摆放的
       同一帧内立即校正, 消除可见抖动。
    """

    def _reposition_cell_widgets(self) -> None:
        model = self.model()
        if model is None:
            return
        for row in range(self.rowCount()):
            for column in range(self.columnCount()):
                widget = self.cellWidget(row, column)
                if widget is not None:
                    widget.setGeometry(self.visualRect(model.index(row, column)))

    def _on_scroll_changed(self, *_args) -> None:
        self._reposition_cell_widgets()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.horizontalScrollBar().valueChanged.connect(self._on_scroll_changed)
        self.verticalScrollBar().valueChanged.connect(self._on_scroll_changed)
        FluentCellToolTipFilter(self)

    def updateGeometries(self):
        super().updateGeometries()
        self._reposition_cell_widgets()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reposition_cell_widgets()


class FrozenColumnTable(QWidget):
    """带左侧冻结列的表格：前 N 列不随横向滚动条移动。

    采用双表格并排方案：左侧冻结表格只显示前 N 列，右侧主表格显示剩余列。
    仅右侧主表格有横向和竖向滚动条，左侧冻结表格无滚动条。
    双向同步垂直滚动、行选择、行高、item 内容和 cellWidget。
    """

    def __init__(self, parent=None, frozen_columns: int = 2):
        super().__init__(parent)
        self._frozen_column_count = max(1, int(frozen_columns))
        self._syncing_selection = False
        self._syncing_scroll = False
        self._total_columns = 0

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 左侧冻结表格（使用与主表格相同的类型，确保行高渲染行为一致）
        self._frozen_table = CellWidgetAlignedTable(self)
        self._frozen_table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._frozen_table.verticalHeader().setVisible(False)
        # 水平表头保持可见，与右侧主表格样式一致
        self._frozen_table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._frozen_table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._frozen_table.setAlternatingRowColors(True)
        self._frozen_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._frozen_table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self._frozen_table.verticalHeader().setDefaultSectionSize(38)
        # 冻结表格右侧保留一条细分隔线，其余样式由 setStyleSheet 统一同步
        # 显式隐藏横向滚动条，确保不占用空间
        self._frozen_table.setStyleSheet(
            "QTableView { border: none; border-right: 1px solid %s; }"
            "QScrollBar:horizontal { height: 0px; }"
            % (PALETTE["card_border"])
        )
        self._apply_frozen_header_defaults()

        # 右侧主表格
        self._main_table = CellWidgetAlignedTable(self)
        self._main_table.verticalHeader().setVisible(False)
        self._main_table.setAlternatingRowColors(True)
        self._main_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._main_table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)

        layout.addWidget(self._frozen_table)
        layout.addWidget(self._main_table, 1)

        # 同步垂直滚动（单向：主表格滚动 → 冻结表格跟随）
        self._main_table.verticalScrollBar().valueChanged.connect(self._on_main_scroll)

        # 同步选择
        self._main_table.itemSelectionChanged.connect(self._sync_selection_to_frozen)
        self._frozen_table.itemSelectionChanged.connect(self._sync_selection_from_frozen)

        # 主表格行高变化时同步到冻结表格
        self._main_table.verticalHeader().sectionResized.connect(self._on_row_resized)

    # ---- 滚动同步 ----
    def _apply_frozen_header_defaults(self) -> None:
        """把冻结表格表头设成与主表格一致的默认行为（Fixed、不可移动等）。"""
        fh = self._frozen_table.horizontalHeader()
        fh.setMinimumSectionSize(46)
        fh.setSectionsMovable(False)
        fh.setCascadingSectionResizes(False)
        fh.setStretchLastSection(False)
        for col in range(self._frozen_table.columnCount()):
            fh.setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)

    def _on_main_scroll(self, value: int) -> None:
        if self._syncing_scroll:
            return
        self._syncing_scroll = True
        try:
            # 先同步行高，确保滚动位置对齐
            self._sync_all_row_heights()
            # 直接设置冻结表格滚动条值，AlwaysOff 只隐藏控件不影响 setValue
            self._frozen_table.verticalScrollBar().setValue(value)
        finally:
            self._syncing_scroll = False

    # ---- 选择同步 ----
    def _sync_selection_to_frozen(self) -> None:
        if self._syncing_selection:
            return
        self._syncing_selection = True
        try:
            rows = {idx.row() for idx in self._main_table.selectedIndexes()}
            self._frozen_table.clearSelection()
            for row in rows:
                self._frozen_table.selectRow(row)
        finally:
            self._syncing_selection = False

    def _sync_selection_from_frozen(self) -> None:
        if self._syncing_selection:
            return
        self._syncing_selection = True
        try:
            rows = {idx.row() for idx in self._frozen_table.selectedIndexes()}
            self._main_table.clearSelection()
            for row in rows:
                self._main_table.selectRow(row)
        finally:
            self._syncing_selection = False

    # ---- 行高同步 ----
    def _on_row_resized(self, row: int, _old: int, new: int) -> None:
        if self._frozen_table.rowHeight(row) != new:
            self._frozen_table.setRowHeight(row, new)

    def _sync_all_row_heights(self) -> None:
        for row in range(self._main_table.rowCount()):
            h = self._main_table.rowHeight(row)
            if self._frozen_table.rowHeight(row) != h:
                self._frozen_table.setRowHeight(row, h)

    # ---- 列索引转换 ----
    def _is_frozen_column(self, column: int) -> bool:
        return column < self._frozen_column_count

    def _main_column(self, column: int) -> int:
        return column - self._frozen_column_count

    # ---- 基础接口 ----
    def setColumnCount(self, columns: int) -> None:
        self._total_columns = int(columns)
        frozen_cols = min(columns, self._frozen_column_count)
        main_cols = max(0, columns - self._frozen_column_count)
        self._frozen_table.setColumnCount(frozen_cols)
        self._main_table.setColumnCount(main_cols)
        self._apply_frozen_header_defaults()
        self._update_frozen_table_width()

    def columnCount(self) -> int:
        return self._total_columns

    def setRowCount(self, rows: int) -> None:
        self._frozen_table.setRowCount(rows)
        self._main_table.setRowCount(rows)
        self._sync_all_row_heights()

    def rowCount(self) -> int:
        return self._main_table.rowCount()

    def setItem(self, row: int, column: int, item) -> None:
        if self._is_frozen_column(column):
            self._frozen_table.setItem(row, column, item)
        else:
            self._main_table.setItem(row, self._main_column(column), item)

    def item(self, row: int, column: int):
        if self._is_frozen_column(column):
            return self._frozen_table.item(row, column)
        return self._main_table.item(row, self._main_column(column))

    def setCellWidget(self, row: int, column: int, widget) -> None:
        if self._is_frozen_column(column):
            self._frozen_table.setCellWidget(row, column, widget)
        else:
            self._main_table.setCellWidget(row, self._main_column(column), widget)

    def cellWidget(self, row: int, column: int):
        if self._is_frozen_column(column):
            return self._frozen_table.cellWidget(row, column)
        return self._main_table.cellWidget(row, self._main_column(column))

    def setColumnWidth(self, column: int, width: int) -> None:
        if self._is_frozen_column(column):
            self._frozen_table.setColumnWidth(column, width)
            # 冻结表格宽度需要随列宽变化
            self._update_frozen_table_width()
        else:
            self._main_table.setColumnWidth(self._main_column(column), width)

    def _update_frozen_table_width(self) -> None:
        total = 0
        for col in range(self._frozen_table.columnCount()):
            total += self._frozen_table.columnWidth(col)
        self._frozen_table.setMinimumWidth(total)
        self._frozen_table.setMaximumWidth(total)

    def columnWidth(self, column: int) -> int:
        if self._is_frozen_column(column):
            return self._frozen_table.columnWidth(column)
        return self._main_table.columnWidth(self._main_column(column))

    def setRowHeight(self, row: int, height: int) -> None:
        self._main_table.setRowHeight(row, height)
        self._frozen_table.setRowHeight(row, height)

    def rowHeight(self, row: int) -> int:
        return self._main_table.rowHeight(row)

    def setHorizontalHeaderLabels(self, labels) -> None:
        frozen_labels = labels[:self._frozen_column_count]
        main_labels = labels[self._frozen_column_count:]
        self._frozen_table.setHorizontalHeaderLabels(frozen_labels)
        self._main_table.setHorizontalHeaderLabels(main_labels)

    def horizontalHeaderItem(self, column: int):
        if self._is_frozen_column(column):
            return self._frozen_table.horizontalHeaderItem(column)
        return self._main_table.horizontalHeaderItem(self._main_column(column))

    def setHorizontalHeaderItem(self, column: int, item) -> None:
        if self._is_frozen_column(column):
            self._frozen_table.setHorizontalHeaderItem(column, item)
        else:
            self._main_table.setHorizontalHeaderItem(self._main_column(column), item)

    def setItemDelegateForColumn(self, column: int, delegate) -> None:
        if self._is_frozen_column(column):
            self._frozen_table.setItemDelegateForColumn(column, delegate)
        else:
            self._main_table.setItemDelegateForColumn(self._main_column(column), delegate)

    def clearContents(self) -> None:
        self._frozen_table.clearContents()
        self._main_table.clearContents()

    def selectRow(self, row: int) -> None:
        self._main_table.selectRow(row)
        self._frozen_table.selectRow(row)

    def clearSelection(self) -> None:
        self._main_table.clearSelection()
        self._frozen_table.clearSelection()

    def selectedIndexes(self):
        return self._main_table.selectedIndexes()

    # ---- 样式/外观接口（转发到两个表格） ----
    def setSelectionBehavior(self, behavior) -> None:
        self._main_table.setSelectionBehavior(behavior)
        self._frozen_table.setSelectionBehavior(behavior)

    def setSelectionMode(self, mode) -> None:
        self._main_table.setSelectionMode(mode)
        self._frozen_table.setSelectionMode(mode)

    def setAlternatingRowColors(self, enable: bool) -> None:
        self._main_table.setAlternatingRowColors(enable)
        self._frozen_table.setAlternatingRowColors(enable)

    def setWordWrap(self, on: bool) -> None:
        self._main_table.setWordWrap(on)
        self._frozen_table.setWordWrap(on)

    def setTextElideMode(self, mode) -> None:
        self._main_table.setTextElideMode(mode)
        self._frozen_table.setTextElideMode(mode)

    def setStyleSheet(self, ss: str) -> None:
        self._main_table.setStyleSheet(ss)
        # 冻结表格继承主表格样式，额外保留右边框分隔线
        frozen_ss = ss + (
            "\nQTableView { border: none; border-right: 1px solid %s; }"
            % PALETTE["card_border"]
        )
        self._frozen_table.setStyleSheet(frozen_ss)

    def styleSheet(self) -> str:
        return self._main_table.styleSheet()

    def setPalette(self, palette) -> None:
        self._main_table.setPalette(palette)
        self._frozen_table.setPalette(palette)

    def palette(self):
        return self._main_table.palette()

    def font(self):
        return self._main_table.font()

    def setFont(self, font) -> None:
        self._main_table.setFont(font)
        self._frozen_table.setFont(font)

    def itemDelegate(self):
        return self._main_table.itemDelegate()

    def setObjectName(self, name: str) -> None:
        self._main_table.setObjectName(name)
        self._frozen_table.setObjectName(name)

    def objectName(self) -> str:
        return self._main_table.objectName()

    def setProperty(self, name: str, value) -> bool:
        r1 = self._main_table.setProperty(name, value)
        r2 = self._frozen_table.setProperty(name, value)
        return r1 and r2

    def property(self, name: str):
        return self._main_table.property(name)

    def setUpdatesEnabled(self, enable: bool) -> None:
        self._main_table.setUpdatesEnabled(enable)
        self._frozen_table.setUpdatesEnabled(enable)

    def updatesEnabled(self) -> bool:
        return self._main_table.updatesEnabled()

    def viewport(self):
        return self._main_table.viewport()

    def model(self):
        return self._main_table.model()

    def setModel(self, model) -> None:
        self._main_table.setModel(model)

    def indexAt(self, pos):
        return self._main_table.indexAt(pos)

    def visualItemRect(self, item):
        return self._main_table.visualItemRect(item)

    def rowViewportPosition(self, row: int) -> int:
        return self._main_table.rowViewportPosition(row)

    def columnViewportPosition(self, column: int) -> int:
        if self._is_frozen_column(column):
            return self._frozen_table.columnViewportPosition(column)
        return self._main_table.columnViewportPosition(self._main_column(column))

    def itemAt(self, *args):
        return self._main_table.itemAt(*args)

    def setIndexWidget(self, index, widget) -> None:
        self._main_table.setIndexWidget(index, widget)

    def indexWidget(self, index):
        return self._main_table.indexWidget(index)

    def editItem(self, item) -> None:
        self._main_table.editItem(item)

    def openPersistentEditor(self, item) -> None:
        self._main_table.openPersistentEditor(item)

    def closePersistentEditor(self, item) -> None:
        self._main_table.closePersistentEditor(item)

    def sortItems(self, column: int, order=...) -> None:
        if self._is_frozen_column(column):
            self._frozen_table.sortItems(column, order)
        else:
            self._main_table.sortItems(self._main_column(column), order)

    def findItems(self, text, flags):
        return self._main_table.findItems(text, flags)

    def setSpan(self, row: int, column: int, rowSpan: int, columnSpan: int) -> None:
        self._main_table.setSpan(row, column, rowSpan, columnSpan)

    def setShowGrid(self, show: bool) -> None:
        self._main_table.setShowGrid(show)
        self._frozen_table.setShowGrid(show)

    def showGrid(self) -> bool:
        return self._main_table.showGrid()

    def setGridStyle(self, style) -> None:
        self._main_table.setGridStyle(style)
        self._frozen_table.setGridStyle(style)

    def gridStyle(self):
        return self._main_table.gridStyle()

    def setSortingEnabled(self, enable: bool) -> None:
        self._main_table.setSortingEnabled(enable)

    def isSortingEnabled(self) -> bool:
        return self._main_table.isSortingEnabled()

    def setCornerButtonEnabled(self, enable: bool) -> None:
        self._main_table.setCornerButtonEnabled(enable)

    def isCornerButtonEnabled(self) -> bool:
        return self._main_table.isCornerButtonEnabled()

    def setDragDropMode(self, mode) -> None:
        self._main_table.setDragDropMode(mode)

    def dragDropMode(self):
        return self._main_table.dragDropMode()

    def setDragEnabled(self, enable: bool) -> None:
        self._main_table.setDragEnabled(enable)

    def dragEnabled(self) -> bool:
        return self._main_table.dragEnabled()

    def setAcceptDrops(self, enable: bool) -> None:
        self._main_table.setAcceptDrops(enable)

    def acceptDrops(self) -> bool:
        return self._main_table.acceptDrops()

    def setDropIndicatorShown(self, enable: bool) -> None:
        self._main_table.setDropIndicatorShown(enable)

    def showDropIndicator(self) -> bool:
        return self._main_table.showDropIndicator()

    def setTabKeyNavigation(self, enable: bool) -> None:
        self._main_table.setTabKeyNavigation(enable)
        self._frozen_table.setTabKeyNavigation(enable)

    def tabKeyNavigation(self) -> bool:
        return self._main_table.tabKeyNavigation()

    def setTextElideMode(self, mode) -> None:
        self._main_table.setTextElideMode(mode)
        self._frozen_table.setTextElideMode(mode)

    def textElideMode(self):
        return self._main_table.textElideMode()

    def setIconSize(self, size) -> None:
        self._main_table.setIconSize(size)
        self._frozen_table.setIconSize(size)

    def iconSize(self):
        return self._main_table.iconSize()

    def setSelectionMode(self, mode) -> None:
        self._main_table.setSelectionMode(mode)
        self._frozen_table.setSelectionMode(mode)

    def selectionMode(self):
        return self._main_table.selectionMode()

    def setSelectionBehavior(self, behavior) -> None:
        self._main_table.setSelectionBehavior(behavior)
        self._frozen_table.setSelectionBehavior(behavior)

    def selectionBehavior(self):
        return self._main_table.selectionBehavior()

    def setSelectionModel(self, selectionModel) -> None:
        self._main_table.setSelectionModel(selectionModel)

    def selectionModel(self):
        return self._main_table.selectionModel()

    def selectedItems(self):
        return self._main_table.selectedItems()

    def selectedRanges(self):
        return self._main_table.selectedRanges()

    def setCurrentCell(self, row: int, column: int) -> None:
        if self._is_frozen_column(column):
            self._frozen_table.setCurrentCell(row, column)
        else:
            self._main_table.setCurrentCell(row, self._main_column(column))

    def currentRow(self) -> int:
        return self._main_table.currentRow()

    def currentColumn(self) -> int:
        col = self._main_table.currentColumn()
        if col < 0:
            return col
        return col + self._frozen_column_count

    def currentItem(self):
        return self._main_table.currentItem()

    def setCurrentItem(self, item) -> None:
        self._main_table.setCurrentItem(item)

    def scrollToItem(self, item, hint=...) -> None:
        self._main_table.scrollToItem(item, hint)

    def scrollTo(self, index, hint=...) -> None:
        self._main_table.scrollTo(index, hint)

    def resizeColumnsToContents(self) -> None:
        self._main_table.resizeColumnsToContents()
        self._frozen_table.resizeColumnsToContents()
        self._update_frozen_table_width()

    def resizeColumnToContents(self, column: int) -> None:
        if self._is_frozen_column(column):
            self._frozen_table.resizeColumnToContents(column)
            self._update_frozen_table_width()
        else:
            self._main_table.resizeColumnToContents(self._main_column(column))

    def resizeRowsToContents(self) -> None:
        self._main_table.resizeRowsToContents()
        self._sync_all_row_heights()

    def resizeRowToContents(self, row: int) -> None:
        self._main_table.resizeRowToContents(row)
        self._sync_all_row_heights()

    def setColumnHidden(self, column: int, hide: bool) -> None:
        if self._is_frozen_column(column):
            self._frozen_table.setColumnHidden(column, hide)
            self._update_frozen_table_width()
        else:
            self._main_table.setColumnHidden(self._main_column(column), hide)

    def isColumnHidden(self, column: int) -> bool:
        if self._is_frozen_column(column):
            return self._frozen_table.isColumnHidden(column)
        return self._main_table.isColumnHidden(self._main_column(column))

    def setRowHidden(self, row: int, hide: bool) -> None:
        self._main_table.setRowHidden(row, hide)
        self._frozen_table.setRowHidden(row, hide)

    def isRowHidden(self, row: int) -> bool:
        return self._main_table.isRowHidden(row)

    def setColumnWidth(self, column: int, width: int) -> None:
        if self._is_frozen_column(column):
            self._frozen_table.setColumnWidth(column, width)
            self._update_frozen_table_width()
        else:
            self._main_table.setColumnWidth(self._main_column(column), width)

    def horizontalHeader(self):
        return self._main_table.horizontalHeader()

    def verticalHeader(self):
        return self._main_table.verticalHeader()

    def setHorizontalScrollBarPolicy(self, policy) -> None:
        self._main_table.setHorizontalScrollBarPolicy(policy)

    def setVerticalScrollBarPolicy(self, policy) -> None:
        self._main_table.setVerticalScrollBarPolicy(policy)

    def verticalScrollBar(self):
        return self._main_table.verticalScrollBar()

    def horizontalScrollBar(self):
        return self._main_table.horizontalScrollBar()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_frozen_table_width()
        self._sync_all_row_heights()


def apply_tooltip(widget: QWidget, text: str) -> None:
    """给控件加 Fluent 风格 Tooltip。"""
    if not text:
        return
    widget.setToolTip(text)
    widget.installEventFilter(
        ToolTipFilter(widget, showDelay=300, position=ToolTipPosition.BOTTOM)
    )


def apply_fluent_dialog_style(dialog: QDialog) -> None:
    """统一普通 ``QDialog`` 的背景与文字颜色，避免退回系统原始灰色外观。"""
    dialog.setProperty("smstSkipGlobalAdaptiveUi", True)
    dialog.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    dialog.setStyleSheet(f"""
        QDialog {{
            background: {PALETTE['surface']};
            color: {PALETTE['text']};
        }}
        QLabel {{
            color: {PALETTE['text']};
        }}
        QLineEdit, QPlainTextEdit, QTextEdit {{
            background: {PALETTE['card_bg']};
            border: 1px solid {PALETTE['card_border']};
            border-radius: {CORNER_RADIUS_PX}px;
            color: {PALETTE['text']};
        }}
    """)


def apply_fluent_progress_dialog_style(dialog: QWidget) -> None:
    """统一进度弹窗与主界面 Card / 按钮风格。"""
    stabilize_transient_dialog(dialog, min_width=360, max_width=480)
    dialog.setStyleSheet(f"""
        QProgressDialog {{
            background: {PALETTE['surface']};
            color: {PALETTE['text']};
        }}
        QProgressDialog QLabel {{
            color: {PALETTE['text']};
        }}
        QProgressBar {{
            border: 1px solid {PALETTE['card_border']};
            border-radius: 6px;
            background: {PALETTE['card_bg']};
            min-height: 10px;
        }}
        QProgressBar::chunk {{
            background: {PALETTE['primary']};
            border-radius: 5px;
        }}
        QProgressDialog QPushButton {{
            background: {PALETTE['card_bg']};
            border: 1px solid {PALETTE['card_border']};
            border-radius: {CORNER_RADIUS_PX}px;
            color: {PALETTE['text']};
            padding: 6px 18px;
            min-width: 72px;
        }}
        QProgressDialog QPushButton:hover {{
            background: {PALETTE['surface']};
        }}
    """)


class TwoOptionSegmentSwitch(QFrame):
    """两个选项组成的分段滑块。"""

    valueChanged = Signal(str)

    def __init__(
        self,
        left_text: str,
        right_text: str,
        parent: QWidget | None = None,
        *,
        value: str | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("TwoOptionSegmentSwitch")
        self._left_text = str(left_text)
        self._right_text = str(right_text)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)

        self.left_button = QPushButton(self._left_text, self)
        self.right_button = QPushButton(self._right_text, self)
        for button in (self.left_button, self.right_button):
            button.setCheckable(True)
            button.setAutoExclusive(False)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setMinimumHeight(30)
            button.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)

        self.left_button.setObjectName("SegmentLeftButton")
        self.right_button.setObjectName("SegmentRightButton")

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._group.addButton(self.left_button)
        self._group.addButton(self.right_button)

        layout.addWidget(self.left_button)
        layout.addWidget(self.right_button)

        self.setStyleSheet(f"""
            QFrame#TwoOptionSegmentSwitch {{
                background: #FFFFFF;
                border: 1px solid #C8CDD5;
                border-radius: 6px;
            }}
            QPushButton#SegmentLeftButton,
            QPushButton#SegmentRightButton {{
                min-width: 76px;
                padding: 5px 12px;
                color: {PALETTE['text']};
                background: #FFFFFF;
                border: none;
                font-weight: 600;
            }}
            QPushButton#SegmentLeftButton {{
                border-top-left-radius: 5px;
                border-bottom-left-radius: 5px;
            }}
            QPushButton#SegmentRightButton {{
                border-top-right-radius: 5px;
                border-bottom-right-radius: 5px;
            }}
            QPushButton#SegmentLeftButton:hover:!checked,
            QPushButton#SegmentRightButton:hover:!checked {{
                background: #F3F6FA;
            }}
            QPushButton#SegmentLeftButton:checked,
            QPushButton#SegmentRightButton:checked {{
                color: #FFFFFF;
                background: {PALETTE['primary']};
            }}
            QPushButton#SegmentLeftButton:checked:hover,
            QPushButton#SegmentRightButton:checked:hover {{
                background: {PALETTE['primary_hover']};
            }}
        """)

        fit_text_control(self.left_button)
        fit_text_control(self.right_button)

        self.left_button.clicked.connect(
            lambda checked=False: self._select(self._left_text, emit_signal=True)
        )
        self.right_button.clicked.connect(
            lambda checked=False: self._select(self._right_text, emit_signal=True)
        )

        self.setValue(value or self._left_text, emit_signal=False)

    def _select(self, value: str, *, emit_signal: bool) -> None:
        is_left = str(value) == self._left_text
        self.left_button.setChecked(is_left)
        self.right_button.setChecked(not is_left)
        if emit_signal:
            self.valueChanged.emit(self._left_text if is_left else self._right_text)

    def value(self) -> str:
        return self._left_text if self.left_button.isChecked() else self._right_text

    def setValue(self, value: str, *, emit_signal: bool = False) -> None:
        self._select(str(value), emit_signal=emit_signal)


class _StyledMessageDialog(QDialog):
    """与主界面一致的轻量模态提示框。"""

    _KIND_COLOR = {
        "information": PALETTE["primary"],
        "warning": PALETTE["warn"],
        "critical": PALETTE["error"],
        "question": PALETTE["primary"],
    }
    _KIND_MARK = {
        "information": "i",
        "warning": "!",
        "critical": "×",
        "question": "?",
    }

    def __init__(
        self,
        parent: QWidget | None,
        title: str,
        text: str,
        kind: str,
        buttons: "QMessageBox.StandardButton | None" = None,
        default_button: "QMessageBox.StandardButton | None" = None,
    ) -> None:
        super().__init__(parent)
        self.setModal(True)
        self.setWindowTitle(str(title))
        self.setMinimumWidth(380)
        self.setMaximumWidth(16_777_215)
        self.setObjectName("StyledMessageDialog")
        self._result_button: "QMessageBox.StandardButton | None" = None
        self._dismiss_button = self._resolve_dismiss_button(buttons, kind)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(0)

        card = CardWidget(self)
        card.setObjectName("StyledMessageCard")
        if hasattr(card, "setBorderRadius"):
            card.setBorderRadius(CORNER_RADIUS_PX)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(18, 16, 18, 16)
        card_layout.setSpacing(14)

        title_row = QHBoxLayout()
        title_row.setSpacing(12)

        color = self._KIND_COLOR.get(kind, PALETTE["primary"])
        mark = QLabel(self._KIND_MARK.get(kind, "i"), card)
        mark.setObjectName("StyledMessageMark")
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mark.setFixedSize(34, 34)
        mark.setStyleSheet(f"""
            QLabel#StyledMessageMark {{
                color: #FFFFFF;
                background: {color};
                border-radius: 17px;
                font-size: 20px;
                font-weight: 700;
            }}
        """)
        title_row.addWidget(mark, 0, Qt.AlignmentFlag.AlignTop)

        text_box = QVBoxLayout()
        text_box.setSpacing(8)
        title_label = StrongBodyLabel(str(title), card)
        body_label = BodyLabel(str(text), card)
        body_label.setWordWrap(True)
        body_label.setMaximumWidth(400)
        body_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        text_box.addWidget(title_label)
        text_box.addWidget(body_label)
        title_row.addLayout(text_box, 1)
        card_layout.addLayout(title_row)

        button_row = QHBoxLayout()
        button_row.setSpacing(8)
        button_row.addStretch(1)
        self._build_buttons(card, button_row, buttons, default_button)
        card_layout.addLayout(button_row)

        outer.addWidget(card)
        self.setStyleSheet(f"""
            QDialog#StyledMessageDialog {{
                background: {PALETTE['surface']};
            }}
            CardWidget#StyledMessageCard {{
                background: {PALETTE['card_bg']};
                border: 1px solid {PALETTE['card_border']};
                border-radius: {CORNER_RADIUS_PX}px;
            }}
        """)
        self.setProperty("smstSkipGlobalAdaptiveUi", True)
        fit_dialog_to_content(
            self,
            preferred_width=520,
            minimum=(380, 180),
            margin=(40, 80),
        )
        self.setMaximumWidth(520)

    @staticmethod
    def _resolve_dismiss_button(
        buttons: "QMessageBox.StandardButton | None",
        kind: str,
    ) -> "QMessageBox.StandardButton":
        if buttons is None:
            return QMessageBox.StandardButton.Ok
        if buttons & QMessageBox.StandardButton.No:
            return QMessageBox.StandardButton.No
        if buttons & QMessageBox.StandardButton.Cancel:
            return QMessageBox.StandardButton.Cancel
        if buttons & QMessageBox.StandardButton.Ok:
            return QMessageBox.StandardButton.Ok
        if kind == "question":
            return QMessageBox.StandardButton.No
        return QMessageBox.StandardButton.NoButton

    def reject(self) -> None:  # type: ignore[override]
        if self._result_button is None:
            self._result_button = self._dismiss_button
        super().reject()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if self._result_button is None:
            self._result_button = self._dismiss_button
        super().closeEvent(event)

    def _build_buttons(
        self,
        parent: QWidget,
        layout: QHBoxLayout,
        buttons: "QMessageBox.StandardButton | None",
        default_button: "QMessageBox.StandardButton | None",
    ) -> None:
        if buttons is None:
            ok_button = PrimaryPushButton("确定", parent)
            ok_button.setMinimumWidth(88)
            ok_button.clicked.connect(self.accept)
            layout.addWidget(ok_button)
            return

        configured: list[tuple[str, "QMessageBox.StandardButton"]] = []
        if buttons & QMessageBox.StandardButton.Yes:
            configured.append(("是", QMessageBox.StandardButton.Yes))
        if buttons & QMessageBox.StandardButton.No:
            configured.append(("否", QMessageBox.StandardButton.No))
        if buttons & QMessageBox.StandardButton.Ok:
            configured.append(("确定", QMessageBox.StandardButton.Ok))
        if buttons & QMessageBox.StandardButton.Cancel:
            configured.append(("取消", QMessageBox.StandardButton.Cancel))

        for idx, (label, standard) in enumerate(configured):
            is_default = default_button is not None and default_button == standard
            btn = PrimaryPushButton(label, parent) if is_default else PushButton(label, parent)
            btn.setMinimumWidth(88)
            btn.clicked.connect(
                lambda checked=False, b=standard: self._on_button_clicked(b)
            )
            if idx:
                layout.addSpacing(8)
            layout.addWidget(btn)

    def _on_button_clicked(self, button: "QMessageBox.StandardButton") -> None:
        self._result_button = button
        if button in (
            QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Cancel,
        ):
            self.reject()
        else:
            self.accept()


class StyledMessageBox:
    """兼容 ``QMessageBox.warning/information/critical/question`` 的外观统一门面。"""

    @staticmethod
    def _show(
        parent: QWidget | None,
        title: str,
        text: str,
        kind: str,
        buttons: "QMessageBox.StandardButton | None" = None,
        default_button: "QMessageBox.StandardButton | None" = None,
    ) -> "QMessageBox.StandardButton":
        dialog = StyledMessageBox.build(
            parent,
            title,
            text,
            kind,
            buttons=buttons,
            default_button=default_button,
        )
        dialog.exec()
        return dialog._result_button or QMessageBox.StandardButton.NoButton

    @staticmethod
    def build(
        parent: QWidget | None,
        title: str,
        text: str,
        kind: str,
        *,
        buttons: "QMessageBox.StandardButton | None" = None,
        default_button: "QMessageBox.StandardButton | None" = None,
    ) -> _StyledMessageDialog:
        """Create a styled dialog; call ``open()`` for non-blocking prompts."""
        return _StyledMessageDialog(
            parent,
            title,
            text,
            kind,
            buttons,
            default_button,
        )

    @staticmethod
    def build_question(
        parent: QWidget | None,
        title: str,
        text: str,
        *,
        default_yes: bool = True,
    ) -> _StyledMessageDialog:
        default = (
            QMessageBox.StandardButton.Yes
            if default_yes
            else QMessageBox.StandardButton.No
        )
        return StyledMessageBox.build(
            parent,
            title,
            text,
            "question",
            buttons=QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            default_button=default,
        )

    @staticmethod
    def build_notice(
        parent: QWidget | None,
        title: str,
        text: str,
        *,
        kind: str = "information",
    ) -> _StyledMessageDialog:
        return StyledMessageBox.build(
            parent,
            title,
            text,
            kind,
            buttons=QMessageBox.StandardButton.Ok,
            default_button=QMessageBox.StandardButton.Ok,
        )

    @staticmethod
    def _reject_extra_arguments(args, kwargs) -> None:
        if args or kwargs:
            raise TypeError(
                "StyledMessageBox 仅支持 parent/title/text；"
                "如需自定义按钮，请使用原生 QMessageBox.question"
            )

    @staticmethod
    def information(parent: QWidget | None, title: str, text: str, *args, **kwargs) -> int:
        StyledMessageBox._reject_extra_arguments(args, kwargs)
        return StyledMessageBox._show(parent, title, text, "information")

    @staticmethod
    def warning(parent: QWidget | None, title: str, text: str, *args, **kwargs) -> int:
        StyledMessageBox._reject_extra_arguments(args, kwargs)
        return StyledMessageBox._show(parent, title, text, "warning")

    @staticmethod
    def critical(parent: QWidget | None, title: str, text: str, *args, **kwargs) -> int:
        StyledMessageBox._reject_extra_arguments(args, kwargs)
        return StyledMessageBox._show(parent, title, text, "critical")

    @staticmethod
    def question(
        parent: QWidget | None,
        title: str,
        text: str,
        buttons: "QMessageBox.StandardButton" = None,
        default_button: "QMessageBox.StandardButton" = None,
    ) -> "QMessageBox.StandardButton":
        if buttons is None:
            buttons = QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        return StyledMessageBox._show(parent, title, text, "question", buttons, default_button)
