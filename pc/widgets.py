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


def autosize_text(text, max_lines, min_lines: int = 1, on_change=None, scrollbar=None):
    """Keep a (disabled) ``tk.Text`` exactly as tall as its wrapped content, up to
    ``max_lines`` (an int or a callable); beyond that it scrolls. Re-fits on every width
    change. ``on_change`` runs after the height changed. Returns the fit function."""
    font = tkfont.Font(root=text, font=text.cget("font"))

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
            n = wrapped_lines(text.get("1.0", "end-1c"), font, width - inner)
            cap = max_lines() if callable(max_lines) else max_lines
            if scrollbar is not None:         # deterministic: decided here, before painting
                over = n > int(cap)
                sbw = scrollbar.winfo_reqwidth()
                if over:
                    n = wrapped_lines(text.get("1.0", "end-1c"), font, width - inner - sbw)
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


class RichLabel(tk.Text):
    """A one-line read-only label whose segments can be italic grey-blue (derived values).
    ``cget("text")`` returns the plain text, like a ``ttk.Label``."""

    def __init__(self, master, italic_font, color: str, **kw):
        bg = ttk.Style().lookup("TFrame", "background") or self._root().cget("background")
        super().__init__(master, height=1, wrap="none", borderwidth=0, highlightthickness=0,
                         relief="flat", padx=0, pady=0, background=bg, cursor="arrow",
                         font="TkDefaultFont", **kw)
        self.tag_configure("derived", font=italic_font, foreground=color)
        self._plain = ""

    def set_parts(self, parts, sep: str = "  ·  ") -> None:
        """``parts``: objects with ``text`` and ``derived`` (``app_logic.Cell``)."""
        self.configure(state="normal")
        self.delete("1.0", "end")
        for n, part in enumerate(parts):
            if n:
                self.insert("end", sep)
            self.insert("end", part.text, ("derived",) if part.derived else ())
        self.configure(state="disabled", width=max(10, len(self.get("1.0", "end-1c")) + 2))
        self._plain = sep.join(p.text for p in parts)

    def cget(self, key):
        return self._plain if key == "text" else super().cget(key)


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
