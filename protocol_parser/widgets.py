"""通用 UI 控件：Tooltip / RoundedButton / Text 右键菜单。"""
from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

from protocol_parser.theme import ThemeManager

class Tooltip:
    """给任意 widget 绑定悬停气泡说明。"""

    _DELAY_MS = 350  # 鼠标悬停多久后出现
    _TOPLEVEL: tk.Toplevel | None = None  # 全局共享一个气泡，避免多实例抖

    def __init__(self, widget: tk.Misc, text: str, theme: ThemeManager | None = None, width: int = 48):
        self.widget = widget
        self.text = text
        self.theme = theme
        self.width = width
        self._after_id: str | None = None
        widget.bind("<Enter>", self._on_enter, add="+")
        widget.bind("<Leave>", self._on_leave, add="+")
        widget.bind("<Motion>", self._on_motion, add="+")
        widget.bind("<ButtonPress>", self._on_leave, add="+")

    # ---------- helpers ----------
    def _wrap(self) -> str:
        lines = []
        for para in self.text.split("\n"):
            if len(para) <= self.width:
                lines.append(para)
                continue
            # 简单按宽度换行（中文按字符数，足够用）
            buf = ""
            for ch in para:
                if len(buf) >= self.width:
                    lines.append(buf)
                    buf = ""
                buf += ch
            if buf:
                lines.append(buf)
        return "\n".join(lines)

    # ---------- events ----------
    def _on_enter(self, _event=None):
        if self._after_id is not None:
            return
        self._after_id = self.widget.after(self._DELAY_MS, self._show)

    def _on_configure(self, event=None) -> None:
        """grid/pack 拉宽后按真实像素重绘，避免显示宽与点击区域不一致。"""
        try:
            w = int(self.winfo_width())
            h = int(self.winfo_height())
        except Exception:
            return
        if w <= 1 or h <= 1:
            return
        try:
            if int(self.cget("width")) != w or int(self.cget("height")) != h:
                super().configure(width=w, height=h)
        except Exception:
            pass
        self._draw()

    def _on_motion(self, _event=None):
        # 进入控件区域内的小移动不重新计时，离开的 Leave 会负责取消
        pass

    def _on_leave(self, _event=None):
        if self._after_id is not None:
            try:
                self.widget.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None
        self._hide()

    # ---------- show / hide ----------
    def _show(self):
        self._after_id = None
        try:
            if not self.widget.winfo_ismapped():
                return
        except Exception:
            return
        theme = self.theme or ThemeManager()
        bg = theme.get("tooltip_bg")
        fg = theme.get("tooltip_fg")
        x = self.widget.winfo_rootx() + 14
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 8
        tl = tk.Toplevel(self.widget)
        tl.wm_overrideredirect(True)
        try:
            tl.attributes("-alpha", 0.94)
        except Exception:
            pass
        tl.configure(bg=theme.get("border"))
        inner = tk.Frame(tl, bg=bg, padx=10, pady=6)
        inner.pack(padx=1, pady=1)
        tk.Label(inner, text=self._wrap(), bg=bg, fg=fg, justify="left",
                 font=("Microsoft YaHei UI", 9), wraplength=self.width * 10).pack()
        tl.update_idletasks()
        # 防超出屏幕右侧/底部
        sw = tl.winfo_screenwidth()
        sh = tl.winfo_screenheight()
        w = tl.winfo_width()
        h = tl.winfo_height()
        if x + w > sw - 8:
            x = sw - w - 8
        if y + h > sh - 8:
            y = max(8, self.widget.winfo_rooty() - h - 8)
        tl.wm_geometry(f"+{x}+{y}")
        Tooltip._TOPLEVEL = tl

    @staticmethod
    def _hide():
        tl = Tooltip._TOPLEVEL
        if tl is None:
            return
        try:
            tl.destroy()
        except Exception:
            pass
        Tooltip._TOPLEVEL = None

# ---------- 大圆角按钮控件（替代 ttk.Button，支持动态文本/样式切换） ----------


class RoundedButton(tk.Canvas):
    """大圆角按钮，支持动态 configure(text=..., style=...)，兼容 Tooltip。"""

    # 样式名 → (正常背景, hover背景, 文字颜色)
    _STYLE_MAP: dict[str, tuple[str, str, str]] = {}

    @classmethod
    def init_styles(cls, theme: "ThemeManager") -> None:
        """根据主题色初始化样式映射。需在 ThemeManager.apply() 之后调用。"""
        primary = theme.get("primary")
        primary_hover = theme.get("primary_hover")
        error = theme.get("error")
        surface = theme.get("surface")
        text = theme.get("text")

        cls._STYLE_MAP = {
            "TButton":             (surface, primary,    text),
            "Toolbar.TButton":     (surface, primary,    text),
            "Primary.TButton":     (primary, primary_hover, "#FFFFFF"),
            "Danger.TButton":      (error,   "#A5211C",    "#FFFFFF"),
            "CompactPrimary.TButton": (primary, primary_hover, "#FFFFFF"),
            "CompactDanger.TButton":  (error,   "#A5211C",    "#FFFFFF"),
        }

    @classmethod
    def redraw_all(cls, root: tk.Misc) -> None:
        """遍历控件树，强制所有 RoundedButton 实例重绘。"""
        def _walk(w):
            if isinstance(w, cls):
                w._draw()
            for child in w.winfo_children():
                _walk(child)
        _walk(root)

    def __init__(self, parent, *, text: str = "", command=None,
                 style: str = "TButton", width: int | None = None,
                 font: tuple | str | None = None, state: str = "normal",
                 **kwargs):
        self._command = command
        self._style_name = style
        self._state = state
        self._hovered = False
        self._pressed = False
        self._text = text
        self._width_chars = width

        if font is None:
            font = ("Microsoft YaHei UI", 8)
        elif isinstance(font, str):
            font = (font, 8)
        self._font_tuple = font
        
        bg_normal, _, fg = self._colors()
        
        # 计算 Canvas 像素尺寸
        import tkinter.font as tkfont
        f = tkfont.Font(font=self._font_tuple)
        char_w = max(f.measure("0"), 5)
        text_w = f.measure(text) if text else 0
        # width 参数作为最小字符宽度，但始终保证能容纳实际文本
        base_w = max((width * char_w) if width else 0, text_w)
        pad_x = 14 if style.startswith("Compact") else 18
        pad_y = 4 if style.startswith("Compact") else 5
        self._pad_x = pad_x
        self._pad_y = pad_y
        cw = base_w + pad_x * 2
        ch = f.metrics("linespace") + pad_y * 2
        self._radius = min(ch // 2, 12)  # 圆角：高度的一半，最备12px

        # 获取父控件背景色（兼容 ttk/tk 控件）
        parent_bg = kwargs.get("bg")
        if parent_bg is None:
            try:
                parent_bg = parent.cget("background")
            except Exception:
                parent_bg = "#F0F0F0"

        super().__init__(
            parent, width=cw, height=ch,
            bg=parent_bg,
            highlightthickness=0, bd=0, relief="flat",
        )

        self._bg_id = self.create_rectangle(0, 0, cw, ch, fill=bg_normal, outline="", tags=("bg",))
        self._text_id = self.create_text(cw / 2, ch / 2, text=text, fill=fg,
                                         font=self._font_tuple, tags=("text",))

        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Configure>", self._on_configure, add="+")

        def _on_configure(self, event=None) -> None:
            """grid/pack 拉宽后，按真实像素重绘，避免“看起来窄、点得到的区域宽”。"""
            try:
                w = int(self.winfo_width())
                h = int(self.winfo_height())
            except Exception:
                return
            if w <= 1 or h <= 1:
                return
            # 与当前配置尺寸不一致时，同步到 Canvas，再重绘
            try:
                if int(self.cget("width")) != w or int(self.cget("height")) != h:
                    super().configure(width=w, height=h)
            except Exception:
                pass
            self._draw()

    # ---- 内部方法 ----

    def _colors(self) -> tuple[str, str, str]:
        return self._STYLE_MAP.get(self._style_name, ("#E0E0E0", "#C0C0C0", "#000000"))

    def _draw(self) -> None:
        bg_normal, bg_hover, fg = self._colors()
        if self._state == "disabled":
            bg = "#D0D0D0"; fg = "#999999"
        elif self._pressed or self._hovered:
            bg = bg_hover
        else:
            bg = bg_normal

        try:
            w = max(int(self.winfo_width()), int(self.cget("width")))
            h = max(int(self.winfo_height()), int(self.cget("height")))
        except Exception:
            w = int(self.cget("width"))
            h = int(self.cget("height"))
        if w <= 1:
            w = int(self.cget("width"))
        if h <= 1:
            h = int(self.cget("height"))

        self.coords(self._bg_id, 0, 0, w, h)
        self.coords(self._text_id, w / 2, h / 2)
        self.itemconfigure(self._bg_id, fill=bg, outline="")
        self.itemconfigure(self._text_id, fill=fg, text=self._text, font=self._font_tuple)

    def _on_enter(self, _e=None): self._hovered = True; self._draw()
    def _on_leave(self, _e=None): self._hovered = False; self._pressed = False; self._draw()
    def _on_press(self, _e=None): self._pressed = True; self._draw()

    def _on_release(self, _e=None):
        self._pressed = False
        self._draw()
        if self._state != "disabled" and self._command:
            try:
                self._command()
            except Exception:
                pass

    # ---- 兼容 ttk.Button API ----

    def configure(self, cnf=None, **kw):
        if cnf:
            if isinstance(cnf, dict):
                kw.update(cnf)
            else:
                return super().configure(cnf, **kw)
        # 收集 Canvas 原生参数（如 width, height）传递给父类
        native_kw = {}
        for k in ("width", "height"):
            if k in kw:
                native_kw[k] = kw.pop(k)
        if "text" in kw:
            self._text = kw.pop("text")
            import tkinter.font as tkfont
            f = tkfont.Font(font=self._font_tuple)
            text_w = f.measure(self._text) if self._text else 0
            if self._width_chars:
                char_w = max(f.measure("0"), 5)
                fixed_w = self._width_chars * char_w + self._pad_x * 2
                native_kw["width"] = fixed_w
            else:
                new_w = text_w + self._pad_x * 2
                cur_w = int(self.cget("width"))
                if new_w != cur_w:
                    native_kw["width"] = new_w
        if "style" in kw:
            self._style_name = kw.pop("style")
        if "state" in kw:
            self._state = kw.pop("state")
        if "command" in kw:
            self._command = kw.pop("command")
        self._draw()
        # 将原生参数传递给 Canvas 父类
        if native_kw:
            return super().configure(**native_kw)
        return super().configure()

    config = configure

    def cget(self, key):
        if key == "text": return self._text
        if key == "style": return self._style_name
        if key == "state": return self._state
        return super().cget(key)

    def __getitem__(self, key): return self.cget(key)
    def __setitem__(self, key, val): self.configure(**{key: val})

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
