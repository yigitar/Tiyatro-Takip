"""Small Tk helpers: a scrollbar that only exists while the content does not fit."""
from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk


class AutoScrollbar(ttk.Scrollbar):
    """A ``ttk.Scrollbar`` (pack layout) that hides itself while the whole content is
    visible and comes back when it is not. Pack it like any scrollbar; the options given
    to ``pack`` are remembered for re-showing."""

    def __init__(self, master=None, **kw):
        super().__init__(master, **kw)
        self._pack_opts: dict | None = None
        self.shown = False

    def pack(self, **kw):  # noqa: A003 - Tk API name
        self._pack_opts = kw
        self.shown = True
        super().pack(**kw)

    def set(self, first, last):
        fits = float(first) <= 0.0 and float(last) >= 1.0
        if self._pack_opts is not None:
            if fits and self.shown:
                self.pack_forget()
                self.shown = False
            elif not fits and not self.shown:
                super().pack(**self._pack_opts)
                self.shown = True
        super().set(first, last)


def wrapped_lines(text, font, width_px: int) -> int:
    """Number of display lines ``text`` takes when word-wrapped to ``width_px`` (greedy,
    like a Tk Text with ``wrap="word"``). Measured with the font instead of Tk's
    ``count -displaylines``, which blocks on a widget that is not mapped yet."""
    width_px = max(int(width_px), 20)
    space = font.measure(" ")
    total = 0
    for paragraph in str(text).split(chr(10)):
        lines, used = 1, 0
        for word in paragraph.split():
            w = font.measure(word)
            if used and used + space + w > width_px:
                lines, used = lines + 1, 0
            while w > width_px:           # a word longer than the line is broken
                lines, w = lines + 1, w - width_px
            used = w if not used else used + space + w
        total += lines
    return total


def autosize_text(text, max_lines, min_lines: int = 1, on_change=None, scrollbar=None,
                  also_fonts=()):
    """Keep a (read-only) ``tk.Text`` exactly as tall as its wrapped content, up to
    ``max_lines`` (an int or a callable); beyond that it scrolls. Re-fits on every width
    change. ``on_change`` runs after the height changed. ``also_fonts``: further fonts the
    text uses (italic segments): the line count is the largest over all fonts. Returns the
    fit function."""
    font = tkfont.Font(root=text, font=text.cget("font"))
    fonts = [font, *also_fonts]

    def lines_for(width_px):
        return max(wrapped_lines(text.get("1.0", "end-1c"), f, width_px) for f in fonts)

    def fit(_event=None, width=None):
        """``width``: outer widget width to wrap for (the final layout width), else the
        widget's current one."""
        try:
            if width is None:
                width = text.winfo_width()
                if scrollbar is not None and scrollbar.winfo_ismapped():
                    width += scrollbar.winfo_width()      # the width the text had without it
            if width <= 1:                    # not laid out yet: the requested width
                width = text.winfo_reqwidth()
            inner = 2 * (int(str(text.cget("borderwidth"))) + int(str(text.cget("padx")))
                         + int(str(text.cget("highlightthickness"))))
            n = lines_for(width - inner)
            cap = max_lines() if callable(max_lines) else max_lines
            if scrollbar is not None:         # deterministic: decided here, before painting
                over = n > int(cap)
                sbw = scrollbar.winfo_reqwidth()
                if over:
                    n = lines_for(width - inner - sbw)
                if over and not scrollbar.winfo_ismapped():
                    scrollbar.pack(side="right", fill="y")
                elif not over and scrollbar.winfo_manager():
                    scrollbar.pack_forget()
        except tk.TclError:                   # widget gone
            return
        height = max(min_lines, min(int(cap), n))
        if int(text.cget("height")) != height:
            text.configure(height=height)
            if on_change is not None:
                on_change()
    text.bind("<Configure>", fit, add="+")
    fit()
    return fit


_COPY_KEYS = ("c", "insert")


def _pane_background(widget) -> str:
    return ttk.Style().lookup("TFrame", "background") or widget._root().cget("background")


class SelectableText(tk.Text):
    """Read-only text the user can select with the mouse and copy (Ctrl+C / Ctrl+Insert,
    right-click "Kopyala" / "Tümünü seç"). A normal (not disabled) ``tk.Text`` whose editing
    keys, paste and cut are swallowed; no caret. Looks like a label (pane background, no
    border) unless ``box=True`` (white, bordered: the Özet box).

    Auto-sized: after every change of text or ``wraplength`` the width (in characters) and the
    height (wrapped lines) are set from the text itself, never from the widget's current
    geometry, so a layout that computes the wrap width once stays stable.

    ``ttk.Label``-like API: ``configure(text=, wraplength=, textvariable=)``, ``cget("text")``
    (the plain text) and ``cget("wraplength")``."""

    def __init__(self, master, *, text: str = "", wraplength: int = 0, font="TkDefaultFont",
                 foreground=None, background=None, box: bool = False, italic_font=None,
                 derived_color: str | None = None, wrap: str = "word",
                 autosize: bool = True, **kw):
        opts = dict(wrap=wrap, font=font, cursor="xterm", insertwidth=0, takefocus=0,
                    exportselection=False, undo=False, height=1, width=1)
        if box:
            opts.update(borderwidth=1, relief="solid", background=background or "white")
        else:
            opts.update(borderwidth=0, highlightthickness=0, relief="flat", padx=0, pady=0,
                        background=background or _pane_background(master))
        if foreground is not None:
            opts["foreground"] = foreground
        textvariable = kw.pop("textvariable", None)
        opts.update(kw)
        super().__init__(master, **opts)
        try:    # keep the selection visible while the focus is elsewhere (menu, other widget)
            tk.Text.configure(self, inactiveselectbackground=tk.Text.cget(self, "selectbackground"))
        except tk.TclError:
            pass
        self._font = tkfont.Font(root=self, font=font)
        self._fonts = [self._font]
        self._italic = italic_font
        if italic_font is not None:
            self._fonts.append(tkfont.Font(root=self, font=italic_font))
            self.tag_configure("derived", font=italic_font,
                               **({"foreground": derived_color} if derived_color else {}))
        self._plain = ""
        self._autosize = autosize        # False: the caller sets width / height (scrolling dialogs)
        self._wrap_px = int(wraplength or 0)
        self._menu: tk.Menu | None = None
        self.scrolls = lambda: False     # the pane scrolls on the wheel unless this says otherwise
        self.bind("<Key>", self._on_key)
        for virtual in ("<<Paste>>", "<<Cut>>", "<<Clear>>", "<<PasteSelection>>", "<<Undo>>",
                        "<<Redo>>"):
            self.bind(virtual, lambda _e: "break")
        self.bind("<Button-3>", self.popup_menu)
        self.bind("<Button-1>", lambda _e: self.focus_set(), add="+")
        if text:
            self.set_text(text)
        if textvariable is not None:
            self.configure(textvariable=textvariable)

    # ---- content -----------------------------------------------------------------------
    def set_segments(self, segments) -> None:
        """``segments``: iterable of (text, derived: bool); derived ones are italic grey-blue."""
        segments = [(str(t), bool(d)) for t, d in segments]
        tk.Text.configure(self, state="normal")
        self.delete("1.0", "end")
        for text, derived in segments:
            self.insert("end", text, ("derived",) if derived and self._italic else ())
        self._plain = "".join(t for t, _d in segments)
        self._fit()

    def set_text(self, text: str, derived: bool = False) -> None:
        self.set_segments([(text, derived)])

    @property
    def plain(self) -> str:
        return self._plain

    # ---- sizing ------------------------------------------------------------------------
    def _fit(self) -> None:
        if not self._autosize:
            return
        text = self._plain
        zero = max(1, self._font.measure("0"))
        rows = text.split(chr(10))
        if self._wrap_px > 0:
            chars = max(1, self._wrap_px // zero)
            lines = max(wrapped_lines(text, f, chars * zero) for f in self._fonts)
        else:
            longest = max(f.measure(line) for f in self._fonts for line in rows)
            chars = max(1, -(-longest // zero) + 1)
            lines = len(rows)
        if int(tk.Text.cget(self, "width")) != chars or int(tk.Text.cget(self, "height")) != lines:
            tk.Text.configure(self, width=chars, height=max(1, lines))

    def configure(self, cnf=None, **kw):
        if isinstance(cnf, dict):
            kw, cnf = {**cnf, **kw}, None
        refit = False
        if "text" in kw:
            self.set_text(kw.pop("text"))
        if "wraplength" in kw:
            self._wrap_px = int(kw.pop("wraplength") or 0)
            refit = True
        if "textvariable" in kw:
            var = kw.pop("textvariable")
            var.trace_add("write", lambda *_a, v=var: self.set_text(v.get()))
            self.set_text(var.get())
        if cnf is None and not kw:
            if refit:
                self._fit()
            return None
        result = super().configure(cnf, **kw)
        if refit:
            self._fit()
        return result

    config = configure

    def cget(self, key):
        if key == "text":
            return self._plain
        if key == "wraplength":
            return self._wrap_px
        return super().cget(key)

    # ---- selection / copy ------------------------------------------------------------------
    def selected_text(self) -> str:
        try:
            return self.get("sel.first", "sel.last")
        except tk.TclError:
            return ""

    def select_all(self) -> None:
        self.tag_add("sel", "1.0", "end-1c")
        self.mark_set("insert", "1.0")

    def copy_selection(self) -> bool:
        """Selection to the clipboard; False (clipboard untouched) when nothing is selected."""
        text = self.selected_text()
        if not text:
            return False
        self.clipboard_clear()
        self.clipboard_append(text)
        return True

    def _on_key(self, event):
        ctrl = bool(event.state & 0x4)
        key = str(event.keysym)
        if ctrl and key.lower() in _COPY_KEYS:
            self.copy_selection()
            return "break"
        if ctrl and key.lower() == "a":
            self.select_all()
            return "break"
        if key in ("Tab", "ISO_Left_Tab"):
            return None
        return "break"                   # every other key would edit the text or move the caret

    def context_menu(self) -> tk.Menu:
        if self._menu is None:
            self._menu = tk.Menu(self, tearoff=0)
            self._menu.add_command(label="Kopyala", command=self.copy_selection)
            self._menu.add_command(label="Tümünü seç", command=self.select_all)
        return self._menu

    def popup_menu(self, event) -> str:
        menu = self.context_menu()
        menu.entryconfigure(0, state="normal" if self.selected_text() else "disabled")
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
        return "break"


class SelectableLabel(SelectableText):
    """A wrapped, label-like ``SelectableText`` (left aligned; see there)."""


class RichLabel(SelectableLabel):
    """A label whose segments can be italic grey-blue (derived values), selectable and wrapped to
    its ``wraplength`` (set by the layout). ``cget("text")`` returns the plain text."""

    def __init__(self, master, italic_font, color: str, **kw):
        super().__init__(master, italic_font=italic_font, derived_color=color, **kw)

    def set_parts(self, parts, sep: str = "  ·  ") -> None:
        """``parts``: objects with ``text`` and ``derived`` (``app_logic.Cell``)."""
        segments = []
        for n, part in enumerate(parts):
            if n:
                segments.append((sep, False))
            segments.append((part.text, part.derived))
        self.set_segments(segments)


def add_copy_menu(widget, get_text) -> tk.Menu:
    """Right-click "Kopyala" for a widget that cannot hold a selection (a ``tk.Label`` that also
    has a click action): copies ``get_text()``."""
    menu = tk.Menu(widget, tearoff=0)

    def copy():
        widget.clipboard_clear()
        widget.clipboard_append(str(get_text()))
    menu.add_command(label="Kopyala", command=copy)

    def show(event):
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
        return "break"
    widget.bind("<Button-3>", show)
    widget.copy_text = copy
    widget.copy_menu = menu
    return menu


class LinkButton(tk.Label):
    """Plain text link that behaves like a button: ``invoke()``, hand cursor, underlined
    while hovered. Used instead of a bordered ``ttk.Button`` where a small unobtrusive
    "open" action is wanted (source cards)."""

    def __init__(self, master, text: str, command, *, color: str = "#1d4ed8",
                 hover_color: str = "#1e40af", font=None, background=None, **kw):
        opts = {"text": text, "foreground": color, "cursor": "hand2", "padx": 2, "pady": 2}
        if background is not None:
            opts["background"] = background
        super().__init__(master, **opts, **kw)
        base = tkfont.Font(font=font or tkfont.nametofont("TkDefaultFont"))
        self._plain = base
        self._under = tkfont.Font(font=base)
        self._under.configure(underline=True)
        self.configure(font=self._plain)
        self._command, self._color, self._hover = command, color, hover_color
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)
        self.bind("<Button-1>", lambda _e: self.invoke())

    def _enter(self, _e=None):
        self.configure(font=self._under, foreground=self._hover)

    def _leave(self, _e=None):
        self.configure(font=self._plain, foreground=self._color)

    def invoke(self) -> None:
        if self._command is not None:
            self._command()


class Flow(tk.Frame):
    """Container that lays its children out left to right and wraps them onto further
    rows when the width is not enough (children are created with the flow as master and
    registered with ``add``). Children are ``place``d at computed coordinates, so rows do
    not share column widths; the flow sets its own height to the rows it needs. Re-flows
    by itself when the width or a child's size changes; coordinates are only re-applied
    when they actually changed."""

    def __init__(self, master, *, gap_x: int = 12, gap_y: int = 2, **kw):
        super().__init__(master, **kw)
        self.items: list[tk.Widget] = []
        self.gap_x, self.gap_y = gap_x, gap_y
        self._placed: list | None = None
        self._pending = False
        self.rows = 0
        self.bind("<Configure>", lambda _e: self._request())

    def add(self, widget: tk.Widget) -> tk.Widget:
        self.items.append(widget)
        widget.bind("<Configure>", lambda _e: self._request(), add="+")
        self._request()
        return widget

    def _request(self) -> None:
        if not self._pending:
            self._pending = True
            self.after_idle(self.relayout)

    def relayout(self) -> None:
        self._pending = False
        if not self.winfo_exists() or not self.items:
            return
        width = self.winfo_width()
        width = width if width > 1 else 10 ** 6   # not laid out yet: one row
        x = y = row_h = 0
        placed = []
        rows = 1
        for w in self.items:
            need, h = w.winfo_reqwidth(), w.winfo_reqheight()
            if x and x + need > width:
                x, y, row_h, rows = 0, y + row_h + self.gap_y, 0, rows + 1
            placed.append((x, y))
            x += need + self.gap_x
            row_h = max(row_h, h)
        total = y + row_h
        self.rows = rows
        if placed == self._placed and int(self.cget("height")) == total:
            return
        self._placed = placed
        for w, (px, py) in zip(self.items, placed):
            w.place(x=px, y=py)
        self.configure(height=total)
