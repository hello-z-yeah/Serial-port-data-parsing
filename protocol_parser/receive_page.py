"""串口接收分析页面。

页面只负责组合主窗口已经存在的实时数据卡片与指令库卡片，并提供
“指令库”显隐开关。共享发送面板的开关位于主窗口顶部工具栏，
因此在两个导航页面中都可以随时打开或关闭。串口、协议、发送和指令库
业务逻辑仍由 ProtocolParserApp 维护。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QSizePolicy,
    QSplitter,
)
from qfluentwidgets import CardWidget, ToggleButton, StrongBodyLabel


class ReceiveAnalysisPage(QWidget):
    """左侧导航页1：普通串口接收、协议分析与指令库。"""

    def __init__(self, main_window: QWidget):
        super().__init__(main_window)
        self.setObjectName("serialReceiveAnalysisPage")
        self._mw = main_window
        self._realtime_card: QWidget | None = None
        self._cmdlib_card: QWidget | None = None
        self.main_splitter: QSplitter | None = None

        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(0, 2, 0, 0)
        self._root.setSpacing(6)

        switch_card = CardWidget(self)
        self.switch_card = switch_card
        switch_layout = QHBoxLayout(switch_card)
        self.switch_layout = switch_layout
        switch_layout.setContentsMargins(12, 7, 12, 7)
        switch_layout.setSpacing(8)
        switch_layout.addWidget(StrongBodyLabel("串口接收分析", switch_card))
        switch_layout.addStretch(1)

        self.cmdlib_toggle = ToggleButton("指令库", switch_card)
        self.cmdlib_toggle.setChecked(False)
        self.cmdlib_toggle.toggled.connect(self._set_cmdlib_visible)
        switch_layout.addWidget(self.cmdlib_toggle)

        self._root.addWidget(switch_card)

        self.content = QWidget(self)
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(0)
        self._root.addWidget(self.content, 1)

    def attach(
        self,
        realtime_card: QWidget,
        cmdlib_card: QWidget,
        header_controls: QWidget | None = None,
    ) -> None:
        """把实时数据卡片与指令库卡片横向并排放入页面。

        ``header_controls`` 用于承载 HEX/原始数据/清空/自动滚动按钮。
        这些按钮放到“串口接收分析”标题右侧，避免再占用实时数据卡片
        内部的第一行高度。
        """
        self._realtime_card = realtime_card
        self._cmdlib_card = cmdlib_card

        if header_controls is not None:
            header_controls.setParent(self.switch_card)
            header_controls.setSizePolicy(
                QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed
            )
            # 插在末尾“指令库”按钮之前；标题与按钮组之间保留 stretch。
            self.switch_layout.insertWidget(2, header_controls)

        splitter = QSplitter(Qt.Orientation.Horizontal, self.content)
        splitter.setObjectName("receiveDataCommandSplitter")
        splitter.setHandleWidth(6)
        splitter.setChildrenCollapsible(False)

        realtime_card.setParent(splitter)
        cmdlib_card.setParent(splitter)
        realtime_card.setMinimumWidth(300)
        cmdlib_card.setMinimumWidth(260)
        realtime_card.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        cmdlib_card.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )

        splitter.addWidget(realtime_card)
        splitter.addWidget(cmdlib_card)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([900, 460])

        self.main_splitter = splitter
        self.content_layout.addWidget(splitter, 1)
        self._set_cmdlib_visible(self.cmdlib_toggle.isChecked())

    def _set_cmdlib_visible(self, visible: bool) -> None:
        card = self._cmdlib_card
        if card is None:
            return
        card.setVisible(bool(visible))
        QTimer.singleShot(0, self._rebalance_splitter)

    def _rebalance_splitter(self) -> None:
        splitter = self.main_splitter
        realtime = self._realtime_card
        cmdlib = self._cmdlib_card
        if splitter is None or realtime is None or cmdlib is None:
            return

        total = max(1, splitter.contentsRect().width() - splitter.handleWidth())
        cmdlib_on = not cmdlib.isHidden()
        if not cmdlib_on:
            splitter.setSizes([total, 0])
        else:
            right = max(260, int(total * 0.34))
            left = max(300, total - right)
            if left + right > total:
                right = max(1, total - min(left, max(1, total - 1)))
                left = max(1, total - right)
            splitter.setSizes([left, right])
        splitter.updateGeometry()
        self.content_layout.activate()
        relayout = getattr(self._mw, "_relayout_receive_toolbars", None)
        if callable(relayout):
            QTimer.singleShot(0, relayout)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        QTimer.singleShot(0, self._rebalance_splitter)

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        QTimer.singleShot(0, self._rebalance_splitter)
        # 两个页面的产品选择互不联动。切回页面1时恢复页面1当前选择。
        combo = getattr(self._mw, "product_combo", None)
        if combo is None:
            return
        name = str(combo.currentText() or "").strip()
        if name and name != getattr(self._mw, "product_var", ""):
            loader = getattr(self._mw, "_load_product_cfg", None)
            if callable(loader):
                loader(name)
