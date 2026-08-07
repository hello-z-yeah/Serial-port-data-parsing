"""协议属性选择/编辑对话框（PySide6 + qfluentwidgets）。

提供：
- 复选框选择需要保留的属性
- 表格内联编辑属性（Name / 属性名称 / Type / 数据属性 / 取值范围 / 取值说明）
- 列顺序和列名与 Word 导入的产品功能协议表完全一致

【业务逻辑】与原 Tk 版完全一致：只改 UI 壳，cfg.attributes 的读写与字段映射不变。
"""
from __future__ import annotations

from typing import Callable
import weakref

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QHeaderView, QAbstractItemView,
    QTableWidgetItem, QWidget, QFormLayout, QDialogButtonBox, QMessageBox,
    QListView, QApplication,
)
from qfluentwidgets import (
    PushButton, PrimaryPushButton, LineEdit, ComboBox, EditableComboBox, BodyLabel,
    TableWidget, StrongBodyLabel, CheckBox,
)

from .parser import TYPEID_MAP
from .widgets import StyledMessageBox, apply_fluent_dialog_style
from .attr_center import AttrStateCenter
from .ui_error import format_expected_user_error
from .combo_font import MatchedPopupComboBox, MatchedPopupEditableComboBox
from .dpi_font import (
    responsive_point_size, make_ui_font, apply_adaptive_geometry,
    fit_window_to_screen, fit_text_control,
)

# 与主窗口提示框统一外观。
QMessageBox = StyledMessageBox


def _is_widget_alive(widget: QWidget | None) -> bool:
    if widget is None:
        return False
    try:
        import shiboken6
        return bool(shiboken6.isValid(widget))
    except Exception:
        return True


def _schedule_popup_font_sync(combo: QWidget) -> None:
    """Per-instance, weak-reference popup update safe across dialog closing."""
    combo_ref = weakref.ref(combo)

    def _apply() -> None:
        current = combo_ref()
        if not _is_widget_alive(current):
            return
        _sync_popup_font(current)

    QTimer.singleShot(0, _apply)


def _sync_popup_font(combo: QWidget) -> None:
    """Make every attribute-editor popup use the combo's own font and row size."""
    menu = getattr(combo, "dropMenu", None)
    if menu is None:
        return
    font = QFont(combo.font())
    try:
        menu.setFont(font)
        for child in menu.findChildren(QWidget):
            child.setFont(font)
        for action in menu.actions():
            action.setFont(font)
            widget_for_action = getattr(menu, "widgetForAction", None)
            if callable(widget_for_action):
                action_widget = widget_for_action(action)
                if action_widget is not None:
                    action_widget.setFont(font)
    except Exception:
        pass
    view = getattr(menu, "view", None)
    if view is None:
        try:
            view = menu.findChild(QListView)
        except Exception:
            view = None
    if view is not None:
        try:
            view.setFont(font)
            row_height = max(combo.height(), QFontMetrics(font).height() + 14)
            signature = f"{font.family()}|{font.pointSizeF():g}|{font.weight()}|{row_height}"
            if view.property("_combo_popup_font_signature") != signature:
                view.setProperty("_combo_popup_font_signature", signature)
                view.setStyleSheet(
                    view.styleSheet()
                    + f"""
                        QListView, QListWidget {{
                            font-family: \"{font.family()}\";
                            font-size: {font.pointSizeF():g}pt;
                            font-weight: {font.weight()};
                        }}
                        QListView::item, QListWidget::item {{
                            min-height: {row_height}px;
                            padding-left: 10px;
                            padding-right: 10px;
                        }}
                    """
                )
        except Exception:
            pass


class _DialogComboBox(MatchedPopupComboBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFont(make_ui_font(responsive_point_size(self, maximum=14)))


class _DialogEditableComboBox(MatchedPopupEditableComboBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFont(make_ui_font(responsive_point_size(self, maximum=14)))



# typeid 选项：(显示名, 数值)
TYPEID_OPTIONS = [(f"{v['name']} (typeid={k})", k) for k, v in TYPEID_MAP.items()]

# 协议 Word 的属性表列顺序（与原版一致）
# attrID | Name | 属性名称 | Type | 数据属性 | 取值范围 | 取值说明
TABLE_HEADERS = ["选中", "attrID", "Name", "属性名称", "Type", "数据属性", "取值范围", "取值说明"]
COL_SELECTED = 0
COL_ATTRID = 1
COL_NAME = 2
COL_CN_NAME = 3
COL_TYPEID = 4
COL_ACCESS = 5
COL_RANGE = 6
COL_ENUM = 7


class AttributeEditorDialog(QDialog):
    """属性表选择/编辑对话框。

    用法：
        dlg = AttributeEditorDialog(parent, cfg)
        if dlg.exec() == QDialog.Accepted and dlg.result:
            cfg = dlg.result
    """

    def __init__(
        self,
        parent: QWidget | None,
        cfg: dict,
        on_save: Callable[[dict], None] | None = None,
        *,
        prefer_chinese_name: bool = False,
        selected_attrids: set[str] | None = None,
        allow_delete: bool = False,
    ):
        super().__init__(parent)
        apply_fluent_dialog_style(self)
        self.cfg = cfg
        self.on_save = on_save
        self.prefer_chinese_name = bool(prefer_chinese_name)
        self.allow_delete = bool(allow_delete)
        self.delete_requested = False
        self._initial_selected_attrids = (
            {str(key).upper() for key in selected_attrids}
            if selected_attrids is not None
            else None
        )
        self.result: dict | None = None
        self.setWindowTitle(f"编辑协议属性 - {cfg.get('product', '')}")
        self.resize(960, 560)
        self.setMinimumSize(720, 400)

        # 复制属性表，避免直接修改原始 cfg
        self._attr_state: dict[str, dict] = {}
        for key, attr in (cfg.get("attributes") or {}).items():
            self._attr_state[key] = {
                "name": attr.get("name", ""),
                "original_name": attr.get("original_name") or attr.get("name", ""),
                "cn_name": attr.get("cn_name", ""),
                "typeid": attr.get("typeid", 2),
                "access": attr.get("access", ""),
                "unit": attr.get("unit", ""),
                "range": attr.get("range", ""),
                "enum": dict(attr.get("enum") or {}),
                # 这些字段不在表格中编辑，但决定 0x24 快照的线属性号、
                # 初始值及是否参与快照，保存时必须原样保留。
                "snapshot_wire_id": attr.get("snapshot_wire_id"),
                "initial_value": attr.get("initial_value"),
                "snapshot_include": attr.get("snapshot_include"),
                "source_data_rwx": attr.get("source_data_rwx"),
                "source_data_type": attr.get("source_data_type"),
                "source_attribute_key": attr.get("source_attribute_key"),
                "source_attribute_name": attr.get("source_attribute_name"),
                "selected": (
                    True
                    if self._initial_selected_attrids is None
                    else str(key).upper() in self._initial_selected_attrids
                ),
            }

        self._build_ui()
        self._refresh_table()
        apply_adaptive_geometry(self)
        fit_window_to_screen(
            self,
            preferred=(1040, 620),
            minimum=(680, 420),
            margin=(36, 72),
        )

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # 顶部说明与工具按钮分成可伸缩的两行，避免在高 DPI 或窄屏
        # 下把按钮文字压成半行。
        top = QGridLayout()
        top.setHorizontalSpacing(8)
        top.setVerticalSpacing(6)
        self.info_label = BodyLabel(f"共 {len(self._attr_state)} 个属性。勾选要保留的属性，双击单元格修改内容。")
        self.info_label.setWordWrap(True)
        top.addWidget(self.info_label, 0, 0, 1, 4)

        btn_add = PushButton("新增属性")
        btn_add.clicked.connect(self._add_attribute)
        top.addWidget(btn_add, 1, 0)

        btn_del = PushButton("删除选中")
        btn_del.clicked.connect(self._delete_selected)
        top.addWidget(btn_del, 1, 1)

        btn_all = PushButton("全选")
        btn_all.clicked.connect(self._select_all)
        top.addWidget(btn_all, 1, 2)

        btn_inv = PushButton("反选")
        btn_inv.clicked.connect(self._invert_selection)
        top.addWidget(btn_inv, 1, 3)
        for button in (btn_add, btn_del, btn_all, btn_inv):
            fit_text_control(button)
        top.setColumnStretch(0, 1)
        top.setColumnStretch(1, 1)
        top.setColumnStretch(2, 1)
        top.setColumnStretch(3, 1)

        layout.addLayout(top)

        # 表格
        self.table = TableWidget()
        # Use Qt's row index internally so odd/even colors cannot get stuck on
        # the same tag when rows are inserted or refreshed.
        self.table.setAlternatingRowColors(True)
        self.table.setStyleSheet(
            self.table.styleSheet()
            + """
            QTableView {
                alternate-background-color: #F6F8FB;
                background-color: #FFFFFF;
            }
            """
        )
        headers = list(TABLE_HEADERS)
        if self.prefer_chinese_name:
            headers[COL_NAME] = "名称（中文）"
            headers[COL_CN_NAME] = "原始名称"
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        header = self.table.horizontalHeader()
        for column in range(len(headers)):
            header.setSectionResizeMode(column, QHeaderView.Interactive)
        header.setStretchLastSection(False)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.table.setColumnWidth(COL_SELECTED, 50)
        self.table.setColumnWidth(COL_ATTRID, 80)
        self.table.setColumnWidth(COL_NAME, 140)
        self.table.setColumnWidth(COL_CN_NAME, 110)
        self.table.setColumnWidth(COL_TYPEID, 130)
        self.table.setColumnWidth(COL_ACCESS, 80)
        self.table.setColumnWidth(COL_RANGE, 100)
        self.table.cellChanged.connect(self._on_cell_changed)
        layout.addWidget(self.table, stretch=1)

        # 底部说明和操作分行，防止高 DPI 下长提示把按钮挤出窗口。
        bottom = QGridLayout()
        bottom.setHorizontalSpacing(8)
        bottom.setVerticalSpacing(6)
        hint_label = BodyLabel("提示：取消勾选或删除选中行可移除不需要的属性")
        hint_label.setWordWrap(True)
        bottom.addWidget(hint_label, 0, 0, 1, 4)
        action_column = 0
        if self.allow_delete:
            btn_delete_proto = PushButton("删除协议")
            btn_delete_proto.setStyleSheet(
                btn_delete_proto.styleSheet()
                + "QPushButton { color: #C42B1C; }"
            )
            btn_delete_proto.clicked.connect(self._request_delete)
            fit_text_control(btn_delete_proto)
            bottom.addWidget(btn_delete_proto, 1, action_column)
            action_column += 1
        bottom.setColumnStretch(action_column, 1)
        btn_cancel = PushButton("取消")
        btn_cancel.clicked.connect(self.reject)
        fit_text_control(btn_cancel)
        bottom.addWidget(btn_cancel, 1, action_column + 1)
        btn_save = PrimaryPushButton("保存")
        btn_save.clicked.connect(self._on_save)
        fit_text_control(btn_save)
        bottom.addWidget(btn_save, 1, action_column + 2)
        layout.addLayout(bottom)

    def _request_delete(self) -> None:
        """标记请求删除整个协议，由调用方确认后执行。"""
        self.delete_requested = True
        self.reject()

    @staticmethod
    def _format_typeid(tid, with_hex: bool = True) -> str:
        try:
            tid_int = int(tid)
        except Exception:
            return str(tid)
        info = TYPEID_MAP.get(tid_int)
        name = info["name"] if info else None
        if with_hex and name:
            return f"{name} 0x{tid_int:02X}"
        if name:
            return name
        return f"0x{tid_int:02X}"

    def _parse_enum_text(self, text: str) -> dict[str, str]:
        result: dict[str, str] = {}
        if not text:
            return result
        for part in text.replace("，", ",").split(","):
            part = part.strip()
            if not part:
                continue
            if ":" in part:
                k, v = part.split(":", 1)
            elif "=" in part:
                k, v = part.split("=", 1)
            else:
                continue
            result[k.strip()] = v.strip()
        return result

    def _enum_to_text(self, enum_map: dict) -> str:
        if not enum_map:
            return ""
        try:
            sorted_pairs = sorted(
                ((int(k), v) for k, v in enum_map.items()),
                key=lambda kv: kv[0],
            )
        except Exception:
            sorted_pairs = list(enum_map.items())
        return ", ".join(f"{k}: {v}" for k, v in sorted_pairs)

    def _refresh_table(self) -> None:
        self.table.blockSignals(True)
        self.table.setRowCount(0)

        def _sort_key(k: str) -> int:
            try:
                return int(k, 0)
            except Exception:
                return 9999

        keys = sorted(self._attr_state.keys(), key=_sort_key)
        self.table.setRowCount(len(keys))

        for row, key in enumerate(keys):
            attr = self._attr_state[key]
            # 选中
            chk = CheckBox()
            chk.setChecked(bool(attr.get("selected", True)))
            chk.stateChanged.connect(lambda state, k=key: self._on_check_changed(k, state))
            cell = QWidget()
            lay = QHBoxLayout(cell)
            lay.setContentsMargins(8, 0, 0, 0)
            lay.addWidget(chk)
            lay.addStretch()
            self.table.setCellWidget(row, COL_SELECTED, cell)

            # attrID（只读）
            item_id = QTableWidgetItem(key)
            item_id.setFlags(item_id.flags() & ~Qt.ItemIsEditable)
            item_id.setData(Qt.UserRole, key)
            self.table.setItem(row, COL_ATTRID, item_id)

            if self.prefer_chinese_name:
                self.table.setItem(row, COL_NAME, QTableWidgetItem(attr.get("cn_name", "")))
                self.table.setItem(
                    row, COL_CN_NAME,
                    QTableWidgetItem(attr.get("original_name") or attr.get("name", "")),
                )
            else:
                self.table.setItem(row, COL_NAME, QTableWidgetItem(attr.get("name", "")))
                self.table.setItem(row, COL_CN_NAME, QTableWidgetItem(attr.get("cn_name", "")))
            self.table.setItem(row, COL_TYPEID, QTableWidgetItem(self._format_typeid(attr.get("typeid"))))
            self.table.setItem(row, COL_ACCESS, QTableWidgetItem(attr.get("access", "")))

            range_text = attr.get("range", "")
            unit = (attr.get("unit") or "").strip()
            if unit:
                range_text = f"{range_text} {unit}".strip()
            self.table.setItem(row, COL_RANGE, QTableWidgetItem(range_text))
            self.table.setItem(row, COL_ENUM, QTableWidgetItem(self._enum_to_text(attr.get("enum") or {})))

        for row in range(self.table.rowCount()):
            for column in range(self.table.columnCount()):
                item = self.table.item(row, column)
                if item is not None:
                    item.setToolTip(item.text())
        self.table.blockSignals(False)
        self.info_label.setText(f"共 {len(self._attr_state)} 个属性。勾选要保留的属性，双击单元格修改内容。")
        apply_adaptive_geometry(self.table)

    def _row_key(self, row: int) -> str | None:
        item = self.table.item(row, COL_ATTRID)
        if item is None:
            return None
        return item.data(Qt.UserRole) or item.text()

    def _on_check_changed(self, key: str, state: int) -> None:
        attr = self._attr_state.get(key)
        if attr is not None:
            attr["selected"] = state == Qt.Checked

    def _on_cell_changed(self, row: int, col: int) -> None:
        key = self._row_key(row)
        if not key or key not in self._attr_state:
            return
        attr = self._attr_state[key]
        item = self.table.item(row, col)
        if item is None:
            return
        text = item.text().strip()

        if col == COL_NAME:
            if self.prefer_chinese_name:
                attr["cn_name"] = text
            else:
                attr["name"] = text
        elif col == COL_CN_NAME:
            if self.prefer_chinese_name:
                attr["original_name"] = text
            else:
                attr["cn_name"] = text
        elif col == COL_TYPEID:
            # 尝试从显示文本解析 typeid
            for tn, tv in TYPEID_OPTIONS:
                if tn.startswith(text.split()[0]) if text else False:
                    attr["typeid"] = tv
                    break
            else:
                # 尝试直接解析数字
                try:
                    if text.lower().startswith("0x"):
                        attr["typeid"] = int(text, 16)
                    else:
                        # "BOOL 0x00" 形式
                        for part in text.replace("(", " ").replace(")", " ").split():
                            if part.lower().startswith("0x") or part.isdigit():
                                attr["typeid"] = int(part, 0)
                                break
                except Exception:
                    pass
            # 刷新显示为标准格式
            self.table.blockSignals(True)
            item.setText(self._format_typeid(attr.get("typeid")))
            self.table.blockSignals(False)
        elif col == COL_ACCESS:
            attr["access"] = text
        elif col == COL_RANGE:
            # 简单：整段作为 range，不拆 unit
            attr["range"] = text
        elif col == COL_ENUM:
            attr["enum"] = self._parse_enum_text(text)

    def _select_all(self) -> None:
        for attr in self._attr_state.values():
            attr["selected"] = True
        self._refresh_table()

    def _invert_selection(self) -> None:
        for attr in self._attr_state.values():
            attr["selected"] = not attr.get("selected", True)
        self._refresh_table()

    def _delete_selected(self) -> None:
        rows = set(idx.row() for idx in self.table.selectedIndexes())
        if not rows:
            # 也支持删除未勾选的
            to_del = [k for k, a in self._attr_state.items() if not a.get("selected", True)]
        else:
            to_del = []
            for r in rows:
                k = self._row_key(r)
                if k:
                    to_del.append(k)
        for k in to_del:
            self._attr_state.pop(k, None)
        self._refresh_table()

    def _add_attribute(self) -> None:
        dlg = QDialog(self)
        apply_fluent_dialog_style(dlg)
        dlg.setWindowTitle("新增属性")
        dlg.setMinimumSize(420, 380)
        dlg.resize(520, 460)
        form = QFormLayout(dlg)

        attrid_combo = _DialogEditableComboBox()
        attrid_combo.addItems([f"0x{i:02X}" for i in range(0, 256)])
        attrid_combo.setCurrentText("0x10")
        form.addRow("attrID (十六进制):", attrid_combo)

        name_edit = LineEdit()
        form.addRow("Name (英文名称):", name_edit)

        cn_edit = LineEdit()
        form.addRow("属性名称 (中文):", cn_edit)

        type_combo = _DialogComboBox()
        type_combo.addItems([name for name, _ in TYPEID_OPTIONS])
        type_combo.setCurrentText("UINT8 (typeid=2)")
        form.addRow("Type:", type_combo)

        access_combo = _DialogComboBox()
        access_combo.addItems(["读写", "只读", "只写"])
        access_combo.setCurrentText("读写")
        form.addRow("数据属性:", access_combo)

        range_edit = LineEdit()
        form.addRow("取值范围:", range_edit)

        unit_edit = LineEdit()
        form.addRow("单位 (可选):", unit_edit)

        enum_edit = LineEdit()
        enum_edit.setText("0: 关闭, 1: 打开")
        form.addRow("取值说明:", enum_edit)

        button_host = QWidget(dlg)
        button_row = QHBoxLayout(button_host)
        button_row.setContentsMargins(0, 8, 0, 0)
        button_row.setSpacing(8)
        button_row.addStretch(1)
        cancel_button = PushButton("取消", button_host)
        cancel_button.setMinimumWidth(88)
        cancel_button.clicked.connect(dlg.reject)
        button_row.addWidget(cancel_button)
        ok_button = PrimaryPushButton("确定", button_host)
        ok_button.setMinimumWidth(88)
        ok_button.clicked.connect(dlg.accept)
        button_row.addWidget(ok_button)
        form.addRow(button_host)
        apply_adaptive_geometry(dlg)
        fit_window_to_screen(
            dlg, preferred=(560, 500), minimum=(420, 360), margin=(36, 72)
        )

        if dlg.exec() != QDialog.Accepted:
            return

        aid = attrid_combo.currentText().strip()
        try:
            numeric_attrid = int(aid, 16) if aid.lower().startswith("0x") else int(aid)
        except (TypeError, ValueError):
            QMessageBox.warning(
                self,
                "attrID 输入提示",
                f"attrID“{aid}”格式不正确。\n\n请输入 0–255 或 0x00–0xFF。",
            )
            return
        if not 0 <= numeric_attrid <= 0xFF:
            QMessageBox.warning(
                self,
                "attrID 输入提示",
                f"attrID“{aid}”超出范围。\n\n允许范围：0–255（0x00–0xFF）。",
            )
            return
        key = f"0x{numeric_attrid:02X}"
        if key in self._attr_state:
            QMessageBox.warning(
                self,
                "attrID 输入提示",
                f"attrID {key} 已存在，请选择其他属性 ID。",
            )
            return

        type_text = type_combo.currentText()
        type_value = 2
        for tn, tv in TYPEID_OPTIONS:
            if tn == type_text:
                type_value = tv
                break

        self._attr_state[key] = {
            "name": name_edit.text().strip(),
            "original_name": name_edit.text().strip(),
            "cn_name": cn_edit.text().strip(),
            "typeid": type_value,
            "access": access_combo.currentText().strip(),
            "unit": unit_edit.text().strip(),
            "range": range_edit.text().strip(),
            "enum": self._parse_enum_text(enum_edit.text().strip()),
            "selected": True,
        }
        self._refresh_table()

    def _validate_candidate_cfg(self, candidate_cfg: dict) -> bool:
        """Validate edited metadata before closing the dialog.

        Invalid attrID/type/access/range/enum combinations are user-editable
        configuration issues, so they must keep this editor open and show a
        normal prompt instead of surfacing later as an import/program failure.
        """
        attributes = candidate_cfg.get("attributes") or {}
        if not attributes:
            QMessageBox.warning(self, "属性选择提示", "请至少保留一个属性后再保存。")
            return False

        seen_numeric_ids: set[int] = set()
        for raw_key, meta in attributes.items():
            try:
                numeric_id = (
                    int(str(raw_key), 16)
                    if str(raw_key).lower().startswith("0x")
                    else int(raw_key)
                )
            except (TypeError, ValueError):
                QMessageBox.warning(
                    self,
                    "属性配置不符合要求",
                    f"属性 ID“{raw_key}”格式不正确。\n\n请输入 0–255 或 0x00–0xFF。",
                )
                return False
            if not 0 <= numeric_id <= 0xFF:
                QMessageBox.warning(
                    self,
                    "属性配置不符合要求",
                    f"属性 ID“{raw_key}”超出 1 字节范围 0x00–0xFF。",
                )
                return False
            if numeric_id in seen_numeric_ids:
                QMessageBox.warning(
                    self,
                    "属性配置不符合要求",
                    f"属性 ID 0x{numeric_id:02X} 重复，请修改后保存。",
                )
                return False
            seen_numeric_ids.add(numeric_id)

            if not isinstance(meta, dict):
                QMessageBox.warning(
                    self,
                    "属性配置不符合要求",
                    f"属性 0x{numeric_id:02X} 的定义必须是对象。",
                )
                return False
            try:
                typeid = int(meta.get("typeid", 2))
            except (TypeError, ValueError):
                typeid = -1
            if typeid not in TYPEID_MAP:
                QMessageBox.warning(
                    self,
                    "属性配置不符合要求",
                    f"属性 0x{numeric_id:02X} 的 Type 无效：{meta.get('typeid')!r}。",
                )
                return False
            access = str(meta.get("access") or "读写").strip()
            if access not in ("只读", "读写", "只写"):
                QMessageBox.warning(
                    self,
                    "属性配置不符合要求",
                    f"属性 0x{numeric_id:02X} 的权限“{access}”无效。\n\n允许值：只读、读写、只写。",
                )
                return False

        try:
            AttrStateCenter().load_product(candidate_cfg)
        except Exception as exc:
            QMessageBox.warning(
                self,
                "属性配置不符合要求",
                format_expected_user_error(exc),
            )
            return False
        return True

    def _on_save(self) -> None:
        # 重建 attributes（只保留 selected=True 的）——与原版逻辑一致
        new_attributes: dict = {}
        for key, attr in self._attr_state.items():
            if not attr.get("selected", True):
                continue
            new_attr: dict = {"name": attr.get("name", "")}
            original_name = (attr.get("original_name") or "").strip()
            if original_name:
                new_attr["original_name"] = original_name
            cn_name = (attr.get("cn_name") or "").strip()
            if cn_name:
                new_attr["cn_name"] = cn_name
            if attr.get("typeid") is not None:
                new_attr["typeid"] = attr["typeid"]
            access = (attr.get("access") or "").strip()
            if access:
                new_attr["access"] = access
            unit = (attr.get("unit") or "").strip()
            if unit:
                new_attr["unit"] = unit
            range_text = (attr.get("range") or "").strip()
            if range_text:
                new_attr["range"] = range_text
            if attr.get("enum"):
                new_attr["enum"] = dict(attr["enum"])
            for passthrough_key in (
                "snapshot_wire_id",
                "initial_value",
                "snapshot_include",
                "source_data_rwx",
                "source_data_type",
                "source_attribute_key",
                "source_attribute_name",
            ):
                if attr.get(passthrough_key) is not None:
                    new_attr[passthrough_key] = attr.get(passthrough_key)
            new_attributes[key] = new_attr

        candidate_cfg = dict(self.cfg)
        candidate_cfg["attributes"] = new_attributes
        if not self._validate_candidate_cfg(candidate_cfg):
            return

        self.cfg["attributes"] = new_attributes
        self.result = self.cfg
        if self.on_save:
            try:
                self.on_save(self.cfg)
            except Exception:
                pass
        self.accept()
