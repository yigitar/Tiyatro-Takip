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


def autosize_text(text, max_lines, min_lines: int = 1, on_change=None):
    """Keep a (disabled) ``tk.Text`` exactly as tall as its wrapped content, up to
    ``max_lines`` (an int or a callable); beyond that it scrolls. Re-fits on every width
    change. ``on_change`` runs after the height changed. Returns the fit function."""
    font = tkfont.Font(root=text, font=text.cget("font"))

    def fit(_event=None):
        try:
            width = text.winfo_width()
            if width <= 1:                    # not laid out yet: the requested width
                width = text.winfo_reqwidth()
            inner = 2 * (int(str(text.cget("borderwidth"))) + int(str(text.cget("padx")))
                         + int(str(text.cget("highlightthickness"))))
            n = wrapped_lines(text.get("1.0", "end-1c"), font, width - inner)
        except tk.TclError:                   # widget gone
            return
        cap = max_lines() if callable(max_lines) else max_lines
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
