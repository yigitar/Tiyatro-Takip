"""Small Tk helpers: a scrollbar that only exists while the content does not fit."""
from __future__ import annotations

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


def autosize_text(text, max_lines, min_lines: int = 1, on_change=None):
    """Keep a (disabled) ``tk.Text`` exactly as tall as its wrapped content, up to
    ``max_lines`` (an int or a callable); beyond that it scrolls. Re-fits on every width
    change. ``on_change`` runs after the height changed. Returns the fit function."""
    def fit(_event=None):
        try:
            counted = text.count("1.0", "end-1c", "displaylines")
        except Exception:  # widget gone / not counted yet
            return
        n = counted[0] if counted else 1
        cap = max_lines() if callable(max_lines) else max_lines
        height = max(min_lines, min(int(cap), max(int(n), 1)))
        if int(text.cget("height")) != height:
            text.configure(height=height)
            if on_change is not None:
                on_change()
    text.bind("<Configure>", fit, add="+")
    fit()
    return fit
