"""协议属性选择/编辑对话框。

提供：
- 复选框选择需要保留的属性
- 表格内联编辑属性（Name / 属性名称 / Type / 数据属性 / 取值范围 / 取值说明）
- 列顺序和列名与 Word 导入的产品功能协议表完全一致
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable

from .parser import TYPEID_MAP


# typeid 选项：(显示名, 数值)
TYPEID_OPTIONS = [(f"{v['name']} (typeid={k})", k) for k, v in TYPEID_MAP.items()]

# 协议 Word 的属性表列顺序（用户指定）：
#   表头：attrID | Name | 属性名称 | Type | 数据属性 | 取值范围 | 取值说明
#
# 对应 cfg.attributes 的字段：
#   attrID      -> Treeview 的 #0 列
#   Name        -> "name"          (原英文 on / mode / fan-level)
#   属性名称     -> "cn_name"       (中文 照明 / 模式 / 吹风档位)
#   Type        -> "typeid"        (BOOL/UINT8/... 显示时用 TYPEID_MAP 转成 name)
#   数据属性     -> "access"        (读写 / 只读)
#   取值范围     -> "range"         ([0,1] / [30,42])
#   取值说明     -> "enum"          ({0:关闭, 1:打开})
#
# 以前单独的「单位」列合并进：取值范围后再附加单位显示即可，存仍存在 attr["unit"] 里，但 UI 不单独展示一列。
TABLE_COLUMNS = ("name", "cn_name", "typeid", "access", "range", "enum")
TABLE_HEADERS = {
    "name":    "Name",
    "cn_name": "属性名称",
    "typeid":  "Type",
    "access":  "数据属性",
    "range":   "取值范围",
    "enum":    "取值说明",
}
TABLE_WIDTHS = {
    "#0":      100,   # attrID
    "name":    190,
    "cn_name": 130,
    "typeid":  130,
    "access":  90,
    "range":   110,
    "enum":    260,
}


class AttributeEditorDialog:
    """属性表选择/编辑对话框。

    用法：
        dlg = AttributeEditorDialog(parent, cfg, on_save=lambda new_cfg: ...)
        dlg.show()
    """

    def __init__(self, parent: tk.Misc, cfg: dict, on_save: Callable[[dict], None] | None = None):
        self.parent = parent
        self.cfg = cfg
        self.on_save = on_save
        self.result: dict | None = None

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

        self.win = tk.Toplevel(parent)
        self.dialog = self.win  # 兼容外部 dlg.dialog 的访问方式
        self.win.title(f"属性编辑 - {cfg.get('product', '')}")
        self.win.geometry("1000x600")
        self.win.minsize(800, 500)
        self.win.transient(parent)
        self.win.grab_set()

        # 树形编辑控件
        self._tree: ttk.Treeview | None = None
        self._editing_item: str | None = None
        self._entry: tk.Entry | None = None
        self._combo: ttk.Combobox | None = None

        self._build_ui()
        self._refresh_tree()

        # 居中
        self.win.update_idletasks()
        try:
            x = parent.winfo_rootx() + (parent.winfo_width() - self.win.winfo_width()) // 2
            y = parent.winfo_rooty() + (parent.winfo_height() - self.win.winfo_height()) // 2
            self.win.geometry(f"+{x}+{y}")
        except Exception:
            pass

    def show(self) -> None:
        self.win.wait_window()

    def _build_ui(self) -> None:
        # 顶部说明
        info = ttk.Frame(self.win, padding=8)
        info.pack(fill="x")
        attr_count = len(self._attr_state)
        ttk.Label(
            info,
            text=f"共 {attr_count} 个属性。勾选要保留的属性，双击单元格修改内容。",
            foreground="#0066CC",
        ).pack(side="left")

        # 工具按钮
        ttk.Button(info, text="全选", command=self._select_all).pack(side="right", padx=2)
        ttk.Button(info, text="反选", command=self._invert_selection).pack(side="right", padx=2)
        ttk.Button(info, text="删除选中", command=self._delete_selected).pack(side="right", padx=2)
        ttk.Button(info, text="新增属性", command=self._add_attribute).pack(side="right", padx=2)

        # 属性表 Treeview
        tree_frame = ttk.Frame(self.win, padding=(8, 0, 8, 0))
        tree_frame.pack(fill="both", expand=True)
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(0, weight=1)

        self._tree = ttk.Treeview(
            tree_frame,
            columns=TABLE_COLUMNS,
            show="tree headings",
            selectmode="extended",
        )
        self._tree.heading("#0", text="attrID", anchor="w")
        for key in TABLE_COLUMNS:
            self._tree.heading(key, text=TABLE_HEADERS[key], anchor="w")

        self._tree.column("#0", width=TABLE_WIDTHS["#0"], minwidth=80, stretch=False)
        for key in TABLE_COLUMNS:
            stretch = (key == "enum") or (key in ("name", "range", "cn_name"))
            self._tree.column(key, width=TABLE_WIDTHS[key], minwidth=60, stretch=stretch)

        # 滚动条
        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self._tree.yview)
        hsb = ttk.Scrollbar(tree_frame, orient="horizontal", command=self._tree.xview)
        self._tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self._tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")

        # 事件
        self._tree.bind("<Double-Button-1>", self._on_double_click)
        self._tree.bind("<Button-1>", self._on_single_click)
        self._tree.bind("<Delete>", lambda e: self._delete_selected())

        # 底部按钮
        btns = ttk.Frame(self.win, padding=8)
        btns.pack(fill="x")
        ttk.Label(btns, text="提示：删除选中行可移除不需要的属性", foreground="#888888").pack(side="left")
        ttk.Button(btns, text="取消", command=self._on_cancel).pack(side="right", padx=2)
        ttk.Button(btns, text="保存修改", command=self._on_save).pack(side="right", padx=2)

    def _refresh_tree(self) -> None:
        if not self._tree:
            return
        self._tree.delete(*self._tree.get_children())
        # 按 attrid 排序
        def _sort_key(k: str) -> int:
            try:
                return int(k, 16)
            except Exception:
                return 0xFF

        for key in sorted(self._attr_state.keys(), key=_sort_key):
            attr = self._attr_state[key]
            enum_map = attr.get("enum") or {}
            # 取值说明按数值键升序，格式「0: 关闭, 1: 打开」(更贴近用户 Word 文档的写法)
            try:
                sorted_pairs = sorted(
                    ((int(k), v) for k, v in enum_map.items()),
                    key=lambda kv: kv[0],
                )
            except Exception:
                sorted_pairs = [(k, v) for k, v in enum_map.items()]
            enum_text = ", ".join(f"{k}: {v}" for k, v in sorted_pairs)
            type_text = self._format_typeid(attr.get("typeid"))
            range_text = attr.get("range", "")
            # 单位用户没独立显示列：如果有就直接拼到取值范围里一起显示，省空间
            unit = (attr.get("unit") or "").strip()
            if unit:
                range_text = f"{range_text} {unit}".strip()
            check = "☑" if attr.get("selected") else "☐"
            self._tree.insert(
                "",
                "end",
                iid=key,
                text=f"{check} {key}",
                values=(
                    attr.get("name", ""),
                    attr.get("cn_name", ""),
                    type_text,
                    attr.get("access", ""),
                    range_text,
                    enum_text,
                ),
            )

    @staticmethod
    def _format_typeid(tid, with_hex: bool = True) -> str:
        """格式化 typeid 显示。默认 with_hex=True 时显示「BOOL 0x00」这种「名称 + 原始十六进制值」，
        对齐 Word 协议文档中 Type 列既显示 0x00 又表示 BOOL/UINT8 的风格。"""
        try:
            tid_int = int(tid)
        except Exception:
            return str(tid)
        name: str | None = None
        info = TYPEID_MAP.get(tid_int)
        if info:
            name = info["name"]
        if with_hex and name:
            return f"{name} 0x{tid_int:02X}"
        if name:
            return name
        return f"0x{tid_int:02X}"

    def _on_single_click(self, event) -> None:
        """点击列标题区域切换勾选。"""
        region = self._tree.identify("region", event.x, event.y)
        if region != "tree":
            return
        item = self._tree.identify_row(event.y)
        if not item:
            return
        attr = self._attr_state.get(item)
        if not attr:
            return
        attr["selected"] = not attr.get("selected", True)
        check = "☑" if attr["selected"] else "☐"
        self._tree.item(item, text=f"{check} {item}")

    def _on_double_click(self, event) -> None:
        if not self._tree:
            return
        region = self._tree.identify("region", event.x, event.y)
        if region not in ("cell", "tree"):
            return
        row_id = self._tree.identify_row(event.y)
        col_id = self._tree.identify_column(event.x)
        if not row_id or not col_id:
            return

        # 关闭上一个编辑控件
        self._close_editor()

        # 树列（#0）是勾选区
        if region == "tree":
            return

        # 列索引转字段名：按照 TABLE_COLUMNS 定义顺序
        col_index = int(col_id.replace("#", "")) - 1
        if col_index < 0 or col_index >= len(TABLE_COLUMNS):
            return
        field = TABLE_COLUMNS[col_index]

        # 计算位置
        x, y, w, h = self._tree.bbox(row_id, col_id)
        attr = self._attr_state.get(row_id, {})

        if field == "typeid":
            # Type：下拉框（BOOL / UINT8 ...）
            self._combo = ttk.Combobox(
                self._tree,
                values=[name for name, _ in TYPEID_OPTIONS],
                state="readonly",
            )
            current = self._format_typeid(attr.get("typeid"))
            if current in [n for n, _ in TYPEID_OPTIONS]:
                self._combo.set(current)
            self._combo.place(x=x, y=y, width=w, height=h)
            self._combo.focus_set()
            self._combo.bind("<<ComboboxSelected>>", lambda e, r=row_id: self._finish_typeid_edit(r))
            self._combo.bind("<Return>", lambda e, r=row_id: self._finish_typeid_edit(r))
            self._combo.bind("<Escape>", lambda e: self._close_editor())
            self._editing_item = (row_id, field)
        elif field == "access":
            # 数据属性：固定下拉 读写 / 只读
            self._combo = ttk.Combobox(
                self._tree,
                values=["读写", "只读"],
                state="readonly",
            )
            current = str(attr.get("access", "") or "")
            if current in ("读写", "只读"):
                self._combo.set(current)
            else:
                self._combo.set("读写")
            self._combo.place(x=x, y=y, width=w, height=h)
            self._combo.focus_set()
            self._combo.bind("<<ComboboxSelected>>", lambda e, r=row_id: self._finish_access_edit(r))
            self._combo.bind("<Return>", lambda e, r=row_id: self._finish_access_edit(r))
            self._combo.bind("<Escape>", lambda e: self._close_editor())
            self._editing_item = (row_id, field)
        else:
            # Name / 属性名称 / 取值范围 / 取值说明 用 Entry
            self._entry = tk.Entry(self._tree)
            current_value = str(attr.get(field, ""))
            # 取值说明编辑时用「0: 关闭, 1: 打开」的文本形式，方便用户直接编辑
            if field == "enum":
                enum_map = attr.get("enum") or {}
                try:
                    sorted_pairs = sorted(
                        ((int(k), v) for k, v in enum_map.items()),
                        key=lambda kv: kv[0],
                    )
                except Exception:
                    sorted_pairs = [(k, v) for k, v in enum_map.items()]
                current_value = ", ".join(f"{k}: {v}" for k, v in sorted_pairs)
            self._entry.insert(0, current_value)
            self._entry.select_range(0, "end")
            self._entry.place(x=x, y=y, width=w, height=h)
            self._entry.focus_set()
            self._entry.bind("<Return>", lambda e, r=row_id, f=field: self._finish_text_edit(r, f))
            self._entry.bind("<FocusOut>", lambda e, r=row_id, f=field: self._finish_text_edit(r, f))
            self._entry.bind("<Escape>", lambda e: self._close_editor())
            self._editing_item = (row_id, field)

    def _finish_typeid_edit(self, row_id: str) -> None:
        if not self._combo or not self._editing_item:
            return
        rid, field = self._editing_item
        if rid != row_id:
            return
        current = self._combo.get()
        for name, value in TYPEID_OPTIONS:
            if name == current:
                self._attr_state[rid]["typeid"] = value
                break
        self._close_editor()
        self._refresh_tree()

    def _finish_access_edit(self, row_id: str) -> None:
        if not self._combo or not self._editing_item:
            return
        rid, field = self._editing_item
        if rid != row_id:
            return
        self._attr_state[rid]["access"] = self._combo.get()
        self._close_editor()
        self._refresh_tree()

    def _finish_text_edit(self, row_id: str, field: str) -> None:
        if not self._entry or not self._editing_item:
            return
        rid, ed_field = self._editing_item
        if rid != row_id or ed_field != field:
            return
        value = self._entry.get()
        attr = self._attr_state.get(rid, {})
        if field == "enum":
            # 取值说明：解析成 {value: label} dict（兼容等号/冒号/多行）
            attr["enum"] = self._parse_enum_text(value)
        elif field == "range":
            # 范围列展示里可能附带单位「[30,42] ℃」—— 存的时候把单位抽出来单独存 unit，范围只留前面的数值部分
            raw = value.strip()
            range_text = raw
            unit_text = attr.get("unit") or ""
            m = None
            try:
                # 尝试拆分：最后一个「空格 + 非数字开头的字符串」视为单位
                mm = __import__("re").match(r"^(.*?)\s+([^\[\]\d\-].*)$", raw)
                if mm:
                    left, right = mm.group(1).strip(), mm.group(2).strip()
                    if right:
                        range_text, unit_text = left, right
            except Exception:
                pass
            attr["range"] = range_text
            if unit_text:
                attr["unit"] = unit_text
            else:
                attr.pop("unit", None)
        else:
            attr[field] = value
        self._close_editor()
        self._refresh_tree()

    @staticmethod
    def _parse_enum_text(text: str) -> dict[str, str]:
        """解析「取值说明」字符串，格式与 Word 文档一致：
            "0: 关闭, 1: 打开"   "0=关;1=开"   "0-低档，1-高档"   多行也兼容
        """
        import re
        result: dict[str, str] = {}
        if not text:
            return result
        parts = re.split(r"[;,，；\n]+", text)
        for part in parts:
            part = part.strip()
            if not part:
                continue
            m = re.match(r"(\d+)\s*[:=：\-]\s*(.+)", part)
            if m:
                result[m.group(1)] = m.group(2).strip().strip('"\'「」『』【】()（）')
        return result

    def _close_editor(self) -> None:
        if self._entry is not None:
            try:
                self._entry.destroy()
            except Exception:
                pass
            self._entry = None
        if self._combo is not None:
            try:
                self._combo.destroy()
            except Exception:
                pass
            self._combo = None
        self._editing_item = None

    def _select_all(self) -> None:
        for attr in self._attr_state.values():
            attr["selected"] = True
        self._refresh_tree()

    def _invert_selection(self) -> None:
        for attr in self._attr_state.values():
            attr["selected"] = not attr["selected"]
        self._refresh_tree()

    def _delete_selected(self) -> None:
        to_delete = [k for k, v in self._attr_state.items() if not v.get("selected", True)]
        if not to_delete:
            return
        if not tk.messagebox.askyesno(
            "确认删除",
            f"确定要删除 {len(to_delete)} 个未勾选的属性吗？",
            parent=self.win,
        ):
            return
        for k in to_delete:
            del self._attr_state[k]
        self._refresh_tree()

    def _add_attribute(self) -> None:
        """新增属性对话框（字段顺序与导入Word协议一致）。"""
        from tkinter import simpledialog
        dlg = tk.Toplevel(self.win)
        dlg.title("新增属性")
        dlg.geometry("440x360")
        dlg.transient(self.win)
        dlg.grab_set()

        try:
            x = self.win.winfo_rootx() + (self.win.winfo_width() - 440) // 2
            y = self.win.winfo_rooty() + (self.win.winfo_height() - 360) // 2
            dlg.geometry(f"+{x}+{y}")
        except Exception:
            pass

        entries: dict[str, tk.Entry | ttk.Combobox] = {}
        row = 0
        # 顺序完全贴合用户的 Word 表头：
        # attrID | Name | 属性名称 | Type | 数据属性 | 取值范围 | 取值说明 | 单位(可选)
        for label, field, default in [
            ("attrID (十六进制)", "attrid",    "0x10"),
            ("Name (英文名称)",  "name",      ""),
            ("属性名称 (中文)",  "cn_name",   ""),
            ("数据属性",        "access",    "读写"),
            ("取值范围",        "range",     ""),
            ("单位 (可选)",     "unit",      ""),
            ("取值说明",        "enum",      ""),
        ]:
            ttk.Label(dlg, text=label + ":").grid(row=row, column=0, padx=8, pady=4, sticky="w")
            if field == "attrid":
                ent = ttk.Combobox(dlg, values=[f"0x{i:02X}" for i in range(0, 256)], width=24)
                ent.set(default)
            elif field == "access":
                ent = ttk.Combobox(dlg, values=["读写", "只读"], width=22, state="readonly")
                ent.set(default)
            elif field == "enum":
                ent = tk.Entry(dlg, width=26)
                ent.insert(0, "0: 关闭, 1: 打开")
            else:
                ent = tk.Entry(dlg, width=26)
                ent.insert(0, default)
            ent.grid(row=row, column=1, padx=8, pady=4, sticky="ew")
            entries[field] = ent
            row += 1

        # Type：单独一行，用 BOOL / UINT8 下拉
        ttk.Label(dlg, text="Type:").grid(row=row, column=0, padx=8, pady=4, sticky="w")
        type_combo = ttk.Combobox(
            dlg, values=[name for name, _ in TYPEID_OPTIONS], width=22, state="readonly",
        )
        type_combo.set("UINT8 (typeid=2)")
        type_combo.grid(row=row, column=1, padx=8, pady=4, sticky="ew")
        entries["typeid"] = type_combo
        row += 1

        dlg.columnconfigure(1, weight=1)

        def do_ok():
            aid = entries["attrid"].get().strip()
            name = entries["name"].get().strip()
            if not aid or not name:
                tk.messagebox.showerror("错误", "attrID 和 Name(英文名称) 不能为空", parent=dlg)
                return
            # 解析 attrid
            try:
                aid_clean = aid.lower().replace("0x", "")
                aid_int = int(aid_clean, 16)
                key = f"0x{aid_int:02X}"
            except Exception:
                tk.messagebox.showerror("错误", f"attrID 格式错误: {aid}", parent=dlg)
                return
            if key in self._attr_state:
                tk.messagebox.showerror("错误", f"attrID {key} 已存在", parent=dlg)
                return
            # 解析 typeid
            type_text = entries["typeid"].get()
            type_value = 2
            for tn, tv in TYPEID_OPTIONS:
                if tn == type_text:
                    type_value = tv
                    break

            self._attr_state[key] = {
                "name": name,
                "cn_name": entries["cn_name"].get().strip(),
                "typeid": type_value,
                "access": entries["access"].get().strip(),
                "unit": entries["unit"].get().strip(),
                "range": entries["range"].get().strip(),
                "enum": self._parse_enum_text(entries["enum"].get().strip()),
                "selected": True,
            }
            self._refresh_tree()
            dlg.destroy()

        btn_frame = ttk.Frame(dlg)
        btn_frame.grid(row=row, column=0, columnspan=2, pady=12)
        ttk.Button(btn_frame, text="确定", command=do_ok).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="取消", command=dlg.destroy).pack(side="left", padx=4)

    def _on_cancel(self) -> None:
        self._close_editor()
        self.win.destroy()

    def _on_save(self) -> None:
        self._close_editor()
        # 重建 attributes（只保留 selected=True 的），所有字段都要存，包括新增的 cn_name
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
        try:
            if self.on_save:
                self.on_save(self.cfg)
        finally:
            self.win.destroy()
