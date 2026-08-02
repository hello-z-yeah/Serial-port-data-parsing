"""协议属性选择/编辑对话框（PySide6 + qfluentwidgets）。

提供：
- 复选框选择需要保留的属性
- 表格内联编辑属性（Name / 属性名称 / Type / 数据属性 / 取值范围 / 取值说明）
- 列顺序和列名与 Word 导入的产品功能协议表完全一致

【业务逻辑】与原 Tk 版完全一致：只改 UI 壳，cfg.attributes 的读写与字段映射不变。
"""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QHeaderView, QAbstractItemView,
    QTableWidgetItem, QWidget, QFormLayout, QDialogButtonBox, QMessageBox,
)
from qfluentwidgets import (
    PushButton, PrimaryPushButton, LineEdit, ComboBox, BodyLabel,
    TableWidget, StrongBodyLabel, CheckBox,
)

from .parser import TYPEID_MAP


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

    def __init__(self, parent: QWidget | None, cfg: dict, on_save: Callable[[dict], None] | None = None):
        super().__init__(parent)
        self.cfg = cfg
        self.on_save = on_save
        self.result: dict | None = None
        self.setWindowTitle(f"编辑协议属性 - {cfg.get('product', '')}")
        self.resize(960, 560)
        self.setMinimumSize(720, 400)

        # 复制属性表，避免直接修改原始 cfg
        self._attr_state: dict[str, dict] = {}
        for key, attr in (cfg.get("attributes") or {}).items():
            self._attr_state[key] = {
                "name": attr.get("name", ""),
                "cn_name": attr.get("cn_name", ""),
                "typeid": attr.get("typeid", 2),
                "access": attr.get("access", ""),
                "unit": attr.get("unit", ""),
                "range": attr.get("range", ""),
                "enum": dict(attr.get("enum") or {}),
                "selected": True,
            }

        self._build_ui()
        self._refresh_table()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # 顶部说明 + 工具按钮
        top = QHBoxLayout()
        self.info_label = BodyLabel(f"共 {len(self._attr_state)} 个属性。勾选要保留的属性，双击单元格修改内容。")
        top.addWidget(self.info_label, stretch=1)

        btn_add = PushButton("新增属性")
        btn_add.clicked.connect(self._add_attribute)
        top.addWidget(btn_add)

        btn_del = PushButton("删除选中")
        btn_del.clicked.connect(self._delete_selected)
        top.addWidget(btn_del)

        btn_all = PushButton("全选")
        btn_all.clicked.connect(self._select_all)
        top.addWidget(btn_all)

        btn_inv = PushButton("反选")
        btn_inv.clicked.connect(self._invert_selection)
        top.addWidget(btn_inv)

        layout.addLayout(top)

        # 表格
        self.table = TableWidget()
        self.table.setColumnCount(len(TABLE_HEADERS))
        self.table.setHorizontalHeaderLabels(TABLE_HEADERS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        self.table.horizontalHeader().setSectionResizeMode(COL_ENUM, QHeaderView.Stretch)
        self.table.setColumnWidth(COL_SELECTED, 50)
        self.table.setColumnWidth(COL_ATTRID, 80)
        self.table.setColumnWidth(COL_NAME, 140)
        self.table.setColumnWidth(COL_CN_NAME, 110)
        self.table.setColumnWidth(COL_TYPEID, 130)
        self.table.setColumnWidth(COL_ACCESS, 80)
        self.table.setColumnWidth(COL_RANGE, 100)
        self.table.cellChanged.connect(self._on_cell_changed)
        layout.addWidget(self.table, stretch=1)

        # 底部
        bottom = QHBoxLayout()
        bottom.addWidget(BodyLabel("提示：取消勾选或删除选中行可移除不需要的属性"))
        bottom.addStretch()
        btn_cancel = PushButton("取消")
        btn_cancel.clicked.connect(self.reject)
        bottom.addWidget(btn_cancel)
        btn_save = PrimaryPushButton("保存")
        btn_save.clicked.connect(self._on_save)
        bottom.addWidget(btn_save)
        layout.addLayout(bottom)

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

        self.table.blockSignals(False)
        self.info_label.setText(f"共 {len(self._attr_state)} 个属性。勾选要保留的属性，双击单元格修改内容。")

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
            attr["name"] = text
        elif col == COL_CN_NAME:
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
        dlg.setWindowTitle("新增属性")
        dlg.setFixedSize(420, 380)
        form = QFormLayout(dlg)

        attrid_combo = ComboBox()
        attrid_combo.setEditable(True)
        attrid_combo.addItems([f"0x{i:02X}" for i in range(0, 256)])
        attrid_combo.setCurrentText("0x10")
        form.addRow("attrID (十六进制):", attrid_combo)

        name_edit = LineEdit()
        form.addRow("Name (英文名称):", name_edit)

        cn_edit = LineEdit()
        form.addRow("属性名称 (中文):", cn_edit)

        type_combo = ComboBox()
        type_combo.addItems([name for name, _ in TYPEID_OPTIONS])
        type_combo.setCurrentText("UINT8 (typeid=2)")
        form.addRow("Type:", type_combo)

        access_combo = ComboBox()
        access_combo.addItems(["读写", "只读"])
        access_combo.setCurrentText("读写")
        form.addRow("数据属性:", access_combo)

        range_edit = LineEdit()
        form.addRow("取值范围:", range_edit)

        unit_edit = LineEdit()
        form.addRow("单位 (可选):", unit_edit)

        enum_edit = LineEdit()
        enum_edit.setText("0: 关闭, 1: 打开")
        form.addRow("取值说明:", enum_edit)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        form.addRow(btns)

        if dlg.exec() != QDialog.Accepted:
            return

        aid = attrid_combo.currentText().strip()
        try:
            if aid.lower().startswith("0x"):
                key = f"0x{int(aid, 16):02X}"
            else:
                key = f"0x{int(aid):02X}"
        except Exception:
            QMessageBox.critical(self, "错误", f"attrID 格式错误: {aid}")
            return
        if key in self._attr_state:
            QMessageBox.critical(self, "错误", f"attrID {key} 已存在")
            return

        type_text = type_combo.currentText()
        type_value = 2
        for tn, tv in TYPEID_OPTIONS:
            if tn == type_text:
                type_value = tv
                break

        self._attr_state[key] = {
            "name": name_edit.text().strip(),
            "cn_name": cn_edit.text().strip(),
            "typeid": type_value,
            "access": access_combo.currentText().strip(),
            "unit": unit_edit.text().strip(),
            "range": range_edit.text().strip(),
            "enum": self._parse_enum_text(enum_edit.text().strip()),
            "selected": True,
        }
        self._refresh_table()

    def _on_save(self) -> None:
        # 重建 attributes（只保留 selected=True 的）——与原版逻辑一致
        new_attributes: dict = {}
        for key, attr in self._attr_state.items():
            if not attr.get("selected", True):
                continue
            new_attr: dict = {"name": attr.get("name", "")}
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
            new_attributes[key] = new_attr

        self.cfg["attributes"] = new_attributes
        self.result = self.cfg
        if self.on_save:
            try:
                self.on_save(self.cfg)
            except Exception:
                pass
        self.accept()
