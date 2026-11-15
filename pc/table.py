"""``GridTable``: the list widget of the PC window (wave 6).

``ttk.Treeview`` can only style whole rows, but the list must show *derived* values (our rule
or fallback, not read from the source) in italic grey-blue per cell, a ☑/☐ cell that toggles
following, and small "Oyun ↗" buttons. This widget draws header and rows on two canvases and
offers the subset of the Treeview API the window and its tests use (``get_children``,
``item``, ``heading``, ``selection``, ``selection_set``, ``see``, ``yview`` ...), so callers
read the same.

Cells are ``pc.app_logic.Cell`` objects (``text``, ``derived``, ``kind`` = "text" / "check" /
"link"). Row tags style rows (``tag_configure``): ``bold``, ``foreground``, ``background`` and
an optional ``column`` that limits the style to one column. Scrollbars are not part of the
widget except the horizontal one, which (like the vertical one the window packs next to it)
only exists while the content does not fit.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

from pc.widgets import AutoScrollbar

DERIVED_COLOR = "#4f6d8f"   # grey-blue: derived / assumed values
TEXT_COLOR = "#111827"
SELECT_BG = "#cfe3ff"
GRID_COLOR = "#e5e7eb"
HEAD_BG = "#eef1f5"
BUTTON_BG, BUTTON_EDGE, BUTTON_FG = "#eef2f7", "#9aa7b8", "#1d4ed8"
PAD = 6


class _Row:
    __slots__ = ("iid", "cells", "tags")

    def __init__(self, iid, cells, tags):
        self.iid, self.cells, self.tags = iid, cells, tuple(tags)


class GridTable(ttk.Frame):
    def __init__(self, master, columns, *, stretch=(), on_sort=None, on_cell=None,
                 row_px: int = 22, head_px: int = 26, height_rows: int | None = None,
                 sortable: bool = True):
        super().__init__(master)
        self.keys = [c[0] for c in columns]
        self._labels = {c[0]: c[1] for c in columns}
        self._base = {c[0]: int(c[2]) for c in columns}
        self._stretch = set(stretch)
        self._on_sort, self._on_cell = on_sort, on_cell
        self.row_px, self.head_px, self._sortable = row_px, head_px, sortable
        self._rows: list[_Row] = []
        self._index: dict[str, int] = {}
        self._selected: str | None = None
        self._tags: dict[str, dict] = {}
        self._commands: dict[str, str] = {}
        self._user_yscroll = None
        self._width = 0
        self._dirty = False
        base = tkfont.nametofont("TkDefaultFont")
        spec = base.actual()
        self._fonts = {}
        for bold in (False, True):
            for italic in (False, True):
                self._fonts[(bold, italic)] = tkfont.Font(
                    root=self, family=spec["family"], size=spec["size"],
                    weight="bold" if bold else "normal", slant="italic" if italic else "roman")
        self._fit_cache: dict = {}

        bg = ttk.Style().lookup("Treeview", "background") or "white"
        self.header = tk.Canvas(self, height=head_px, highlightthickness=0, background=HEAD_BG)
        self.body = tk.Canvas(self, highlightthickness=0, background=bg, takefocus=1)
        if height_rows is not None:
            self.body.configure(height=max(1, height_rows) * row_px)
        self.hbar = AutoScrollbar(self, orient="horizontal", command=self._xview)
        self.header.pack(side="top", fill="x")
        self.body.pack(side="top", fill="both", expand=True)
        self.hbar.pack(side="bottom", fill="x")
        self.body.configure(yscrollcommand=self._on_yscroll, xscrollcommand=self._on_xscroll)

        self.body.bind("<Configure>", self._on_configure)
        self.body.bind("<Button-1>", self._click)
        self.body.bind("<Motion>", self._motion)
        self.body.bind("<MouseWheel>", self._wheel)
        self.body.bind("<Up>", lambda e: self._step(-1))
        self.body.bind("<Down>", lambda e: self._step(1))
        self.body.bind("<Prior>", lambda e: self._step(-10))
        self.body.bind("<Next>", lambda e: self._step(10))
        self.body.bind("<Home>", lambda e: self._step(-10 ** 6))
        self.body.bind("<End>", lambda e: self._step(10 ** 6))
        self.body.bind("<space>", self._space)
        self.header.bind("<Button-1>", self._head_click)

    # ---- Treeview-like API ----------------------------------------------------------------
    def __getitem__(self, key):
        if key == "columns":
            return tuple(self.keys)
        return super().__getitem__(key)

    def configure(self, cnf=None, **kw):
        if "yscrollcommand" in kw:
            self._user_yscroll = kw.pop("yscrollcommand")
        if cnf or kw:
            return super().configure(cnf or {}, **kw)
        return None

    config = configure

    def heading(self, column, option=None, **kw):
        if "text" in kw:
            self._labels[column] = kw["text"]
            self._request_draw()
        if kw:
            return None
        info = {"text": self._labels.get(column, ""), "command": self._command(column)}
        return info[option] if option else info

    def _command(self, column: str) -> str:
        if column not in self._commands:
            self._commands[column] = self.register(lambda c=column: self._sort(c))
        return self._commands[column]

    def get_children(self, item=None):
        return tuple(r.iid for r in self._rows)

    def delete(self, *items):
        gone = set(items)
        self._rows = [r for r in self._rows if r.iid not in gone]
        self._reindex()
        if self._selected in gone:
            self._selected = None
        self._request_draw()

    def insert(self, parent, index, iid=None, values=(), tags=(), cells=None):
        from pc.app_logic import Cell
        iid = iid or f"row{len(self._rows)}"
        cells = list(cells) if cells is not None else [Cell(str(v)) for v in values]
        self._rows.append(_Row(iid, cells, tags if isinstance(tags, (tuple, list)) else (tags,)))
        self._index[iid] = len(self._rows) - 1
        self._request_draw()
        return iid

    def item(self, iid, option=None):
        row = self._rows[self._index[iid]]
        info = {"values": tuple(c.text for c in row.cells), "tags": row.tags}
        return info[option] if option else info

    def set_tags(self, iid, tags):
        if iid in self._index:
            self._rows[self._index[iid]].tags = tuple(tags)
            self._request_draw()

    def cell(self, iid, column):
        return self._rows[self._index[iid]].cells[self.keys.index(column)]

    def selection(self):
        return (self._selected,) if self._selected in self._index else ()

    def selection_set(self, iid):
        iid = iid[0] if isinstance(iid, (tuple, list)) else iid
        if iid not in self._index:
            return
        self._selected = iid
        self._request_draw()
        self.event_generate("<<TreeviewSelect>>")

    def selection_remove(self, *items):
        if self._selected in items:
            self._selected = None
            self._request_draw()
            self.event_generate("<<TreeviewSelect>>")

    def see(self, iid):
        if iid not in self._index:
            return
        top = self._index[iid] * self.row_px
        total = max(len(self._rows) * self.row_px, 1)
        view = self.body.winfo_height()
        first = self.body.canvasy(0)
        if top < first:
            self.body.yview_moveto(top / total)
        elif top + self.row_px > first + view > 1:
            self.body.yview_moveto(max(0, top + self.row_px - view) / total)

    def yview(self, *args):
        return self.body.yview(*args)

    def yview_moveto(self, fraction):
        self.body.yview_moveto(fraction)

    def tag_configure(self, tag, *, bold=None, italic=None, foreground=None, background=None,
                      column=None):
        self._tags[tag] = {"bold": bold, "italic": italic, "foreground": foreground,
                           "background": background, "column": column}
        self._request_draw()

    # ---- geometry -----------------------------------------------------------------------------
    def _reindex(self):
        self._index = {r.iid: i for i, r in enumerate(self._rows)}

    def column_widths(self, available: int) -> list[int]:
        """Base widths; spare room goes to the stretch columns; never below the base
        (a horizontal scrollbar appears instead)."""
        base = [self._base[k] for k in self.keys]
        spare = available - sum(base)
        grow = [k for k in self.keys if k in self._stretch] or self.keys[:1]
        if spare > 0:
            share, rest = divmod(spare, len(grow))
            for n, key in enumerate(grow):
                base[self.keys.index(key)] += share + (rest if n == 0 else 0)
        return base

    def _layout(self):
        avail = max(self.body.winfo_width(), 1)
        widths = self.column_widths(avail)
        xs = [0]
        for w in widths:
            xs.append(xs[-1] + w)
        return widths, xs

    def _on_configure(self, event):
        if event.width != self._width:
            self._width = event.width
            self._request_draw()

    # ---- drawing ------------------------------------------------------------------------------
    def _request_draw(self):
        if not self._dirty:
            self._dirty = True
            self.after_idle(self.draw)

    def _fit(self, text: str, font, width: int) -> str:
        key = (text, id(font), width)
        hit = self._fit_cache.get(key)
        if hit is not None:
            return hit
        room = max(width - 2 * PAD, 4)
        out = text
        if font.measure(text) > room:
            while out and font.measure(out + "…") > room:
                out = out[:-1]
            out = out + "…" if out else ""
        if len(self._fit_cache) > 20000:
            self._fit_cache.clear()
        self._fit_cache[key] = out
        return out

    def _row_style(self, row: _Row, col: str, cell):
        bold = italic = False
        fg, bg = TEXT_COLOR, None
        for tag in row.tags:
            cfg = self._tags.get(tag)
            if not cfg or (cfg["column"] and cfg["column"] != col):
                continue
            bold = bold or bool(cfg["bold"])
            italic = italic or bool(cfg["italic"])
            fg = cfg["foreground"] or fg
            if cfg["background"] and cfg["column"] is None:
                bg = cfg["background"]
        if cell.derived:
            italic, fg = True, DERIVED_COLOR
        return self._fonts[(bold, italic)], fg, bg

    def draw(self):
        self._dirty = False
        if not self.winfo_exists():
            return
        widths, xs = self._layout()
        total_w = max(xs[-1], self.body.winfo_width())
        body, head = self.body, self.header
        body.delete("all")
        head.delete("all")
        hfont = self._fonts[(True, False)]
        for i, key in enumerate(self.keys):
            head.create_rectangle(xs[i], 0, xs[i + 1], self.head_px, fill=HEAD_BG,
                                  outline=GRID_COLOR)
            head.create_text(xs[i] + PAD, self.head_px // 2, anchor="w", font=hfont,
                             text=self._fit(self._labels.get(key, ""), hfont, widths[i]))
        for n, row in enumerate(self._rows):
            y0 = n * self.row_px
            y1 = y0 + self.row_px
            base_bg = next((self._tags[t]["background"] for t in row.tags
                            if t in self._tags and self._tags[t]["background"]
                            and not self._tags[t]["column"]), None)
            if row.iid == self._selected:
                body.create_rectangle(0, y0, total_w, y1, fill=SELECT_BG, outline="")
            elif base_bg:
                body.create_rectangle(0, y0, total_w, y1, fill=base_bg, outline="")
            body.create_line(0, y1, total_w, y1, fill=GRID_COLOR)
            for i, (key, cell) in enumerate(zip(self.keys, row.cells)):
                font, fg, _bg = self._row_style(row, key, cell)
                mid = y0 + self.row_px // 2
                if cell.kind == "link":
                    body.create_rectangle(xs[i] + 3, y0 + 2, xs[i + 1] - 3, y1 - 3,
                                          fill=BUTTON_BG, outline=BUTTON_EDGE)
                    body.create_text((xs[i] + xs[i + 1]) // 2, mid, text=cell.text, font=font,
                                     fill=BUTTON_FG)
                elif cell.kind == "check":
                    body.create_text((xs[i] + xs[i + 1]) // 2, mid, text=cell.text, font=font,
                                     fill=fg)
                else:
                    body.create_text(xs[i] + PAD, mid, anchor="w", font=font, fill=fg,
                                     text=self._fit(cell.text, font, widths[i]))
        body.configure(scrollregion=(0, 0, total_w, max(len(self._rows) * self.row_px,
                                                        body.winfo_height())))

    # ---- scrolling ---------------------------------------------------------------------------
    def _on_yscroll(self, first, last):
        if self._user_yscroll is not None:
            self._user_yscroll(first, last)

    def _on_xscroll(self, first, last):
        self.hbar.set(first, last)
        self.header.xview_moveto(first)

    def _xview(self, *args):
        self.body.xview(*args)
        self.header.xview(*args)

    def _wheel(self, event):
        self.body.yview_scroll(-1 if event.delta > 0 else 1, "units")
        return "break"

    # ---- interaction ---------------------------------------------------------------------------
    def _hit(self, event):
        """(iid, column key) under a body event, or (None, None)."""
        y = self.body.canvasy(event.y)
        n = int(y // self.row_px)
        if not 0 <= n < len(self._rows):
            return None, None
        _widths, xs = self._layout()
        x = self.body.canvasx(event.x)
        for i, key in enumerate(self.keys):
            if xs[i] <= x < xs[i + 1]:
                return self._rows[n].iid, key
        return self._rows[n].iid, None

    def _motion(self, event):
        iid, key = self._hit(event)
        hand = bool(iid and key and self.cell(iid, key).kind in ("link", "check"))
        self.body.configure(cursor="hand2" if hand else "")

    def _click(self, event):
        self.body.focus_set()
        iid, key = self._hit(event)
        if iid is None:
            return
        if key and self.cell(iid, key).kind in ("link", "check") and self._on_cell:
            self._on_cell(iid, key)
            return
        if iid != self._selected:
            self.selection_set(iid)

    def _space(self, _event):
        if self._selected and self._on_cell and "follow" in self.keys:
            self._on_cell(self._selected, "follow")
        return "break"

    def _step(self, delta: int):
        if not self._rows:
            return "break"
        cur = self._index.get(self._selected, -1 if delta > 0 else len(self._rows))
        new = min(max(cur + delta, 0), len(self._rows) - 1)
        self.selection_set(self._rows[new].iid)
        self.see(self._rows[new].iid)
        return "break"

    def _head_click(self, event):
        x = self.header.canvasx(event.x)
        _widths, xs = self._layout()
        for i, key in enumerate(self.keys):
            if xs[i] <= x < xs[i + 1]:
                self._sort(key)
                return

    def _sort(self, column: str):
        if self._sortable and self._on_sort:
            self._on_sort(column)
