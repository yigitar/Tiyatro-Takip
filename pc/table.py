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
LINK_COLOR = "#1d4ed8"      # link-style cells ("Oyun ↗"): plain blue text, underlined on hover
ZEBRA_BG = "#f7f8fa"        # every second row: barely visible, like Excel banding
BOX_EDGE, BOX_ON = "#8a94a3", "#2563eb"   # drawn checkbox: outline / checked fill
PAD = 6
CHECKED_GLYPH = "☑"   # cell text of a checked box (pc.app_logic.CHECKED)
DROP_COLOR = "#2563eb"      # column drag: drop indicator line / ghost outline
GHOST_BG = "#dbeafe"
MIN_COL_PX = 44             # narrowest a column can be dragged/shrunk (96 dpi); never below its base
MAX_COL_PX = 700            # widest a dragged / auto-fitted column gets (96 dpi)
EDGE_PX = 5                 # half width of the header border hit area (96 dpi)
DRAG_PX = 4                 # press-and-move distance (96 dpi) before a header press becomes a drag
RESET_LABEL = "Sütunları varsayılana sıfırla"


def reconcile_layout(default_keys, order, hidden):
    """(full order, hidden set) from saved values: unknown keys are dropped, columns missing
    from the saved order are inserted right after their default predecessor (so a column
    added by a later version lands at its default place), at least one column stays visible."""
    default = list(default_keys)
    full: list = []
    for key in order if isinstance(order, (list, tuple)) else ():
        if isinstance(key, str) and key in default and key not in full:
            full.append(key)
    for n, key in enumerate(default):
        if key in full:
            continue
        pos = 0
        for prev in reversed(default[:n]):
            if prev in full:
                pos = full.index(prev) + 1
                break
        full.insert(pos, key)
    gone = {k for k in (hidden if isinstance(hidden, (list, tuple, set)) else ())
            if isinstance(k, str) and k in default}
    if len(gone) >= len(full):
        gone.discard(full[0])
    return full, gone


class _Row:
    __slots__ = ("iid", "cells", "tags")

    def __init__(self, iid, cells, tags):
        self.iid, self.cells, self.tags = iid, cells, tuple(tags)


class GridTable(ttk.Frame):
    def __init__(self, master, columns, *, stretch=(), on_sort=None, on_cell=None,
                 row_px: int = 22, head_px: int = 26, height_rows: int | None = None,
                 sortable: bool = True, zebra: bool = True, customizable: bool = False,
                 on_layout=None, hscroll: bool = True):
        super().__init__(master)
        # 1.0 at 96 dpi; the window is DPI aware (pc.winsetup), Tk reports the real DPI
        self.scale = max(1.0, float(self.winfo_fpixels("1i")) / 96.0)
        self.keys = [c[0] for c in columns]
        self._labels = {c[0]: c[1] for c in columns}
        self._base = {c[0]: int(round(int(c[2]) * self.scale)) for c in columns}
        self._zebra = zebra
        self._hover: tuple | None = None
        self.pad = int(round(PAD * self.scale))
        self._stretch = set(stretch)
        self._on_sort, self._on_cell = on_sort, on_cell
        # column layout: user's order / hidden set / widths he dragged (scaled px). Cells
        # always stay in the canonical ``keys`` order; only the display order changes.
        self._customizable, self._on_layout = customizable, on_layout
        self._order = list(self.keys)
        self._hidden: set = set()
        self._user_w: dict = {}
        self._hs: dict | None = None      # header interaction in progress
        self._menu_vars: dict = {}
        self._sortable = sortable
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
        for italic in (False, True):   # underlined variants for hovered links
            self._fonts[("link", italic)] = tkfont.Font(
                root=self, family=spec["family"], size=spec["size"], underline=True,
                slant="italic" if italic else "roman")
        # row/heading heights: the given 96-dpi px scaled, never tighter than the font needs
        line = self._fonts[(False, False)].metrics("linespace")
        self.row_px = max(int(round(row_px * self.scale)), line + 6)
        self.head_px = max(int(round(head_px * self.scale)), line + 8)
        self._fit_cache: dict = {}

        bg = ttk.Style().lookup("Treeview", "background") or "white"
        self.header = tk.Canvas(self, height=self.head_px, highlightthickness=0, background=HEAD_BG)
        self.body = tk.Canvas(self, highlightthickness=0, background=bg, takefocus=1)
        if height_rows is not None:
            self.body.configure(height=max(1, height_rows) * self.row_px)
        self.hbar = AutoScrollbar(self, orient="horizontal", command=self._xview)
        self.header.pack(side="top", fill="x")
        self.body.pack(side="top", fill="both", expand=True)
        if hscroll:        # a fixed-size table (sessions) has none: it would toggle after paint
            self.hbar.pack(side="bottom", fill="x")
        self.body.configure(yscrollcommand=self._on_yscroll, xscrollcommand=self._on_xscroll)

        self.body.bind("<Configure>", self._on_configure)
        self.body.bind("<Button-1>", self._click)
        self.body.bind("<Motion>", self._motion)
        self.body.bind("<Leave>", self._leave)
        self.body.bind("<MouseWheel>", self._wheel)
        self.body.bind("<Up>", lambda e: self._step(-1))
        self.body.bind("<Down>", lambda e: self._step(1))
        self.body.bind("<Prior>", lambda e: self._step(-10))
        self.body.bind("<Next>", lambda e: self._step(10))
        self.body.bind("<Home>", lambda e: self._step(-10 ** 6))
        self.body.bind("<End>", lambda e: self._step(10 ** 6))
        self.body.bind("<space>", self._space)
        self.header.bind("<Button-1>", self._head_press)
        self.header.bind("<Double-Button-1>", self._head_double)
        self.header.bind("<Motion>", self._head_motion)
        self.header.bind("<B1-Motion>", self._head_drag)
        self.header.bind("<ButtonRelease-1>", self._head_release)
        self.header.bind("<Leave>", self._head_leave)
        if customizable:
            self.header.bind("<Button-3>", self._head_menu)

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

    @property
    def order(self) -> list:
        """Visible column keys, left to right."""
        return [k for k in self._order if k not in self._hidden]

    def _base_label(self, key: str) -> str:
        return self._labels.get(key, "").rstrip(" ▲▼")

    def min_width(self, key: str) -> int:
        """Narrowest width of a column: header text (with room for a sort arrow), at least
        ``MIN_COL_PX``; never above the column's default width."""
        hfont = self._fonts[(True, False)]
        need = hfont.measure(self._base_label(key)) + 2 * self.pad + int(round(14 * self.scale))
        return min(self._base[key], max(int(round(MIN_COL_PX * self.scale)), need))

    def column_widths(self, available: int) -> list[int]:
        """Widths of the visible columns, in display order. A width the user dragged is kept
        (never below the column minimum); the others share what is left: default widths,
        spare room to the stretch columns, a shortage taken proportionally from the columns
        that have room above their minimum. Only when even the minimums do not fit does the
        total exceed ``available`` (the horizontal scrollbar then appears)."""
        vis = self.order
        widths = {}
        for key in vis:
            if key in self._user_w:
                widths[key] = max(self._user_w[key], self.min_width(key))
        free = [k for k in vis if k not in widths]
        if free:
            room = available - sum(widths.values())
            base = {k: self._base[k] for k in free}
            spare = room - sum(base.values())
            if spare >= 0:
                grow = [k for k in free if k in self._stretch] or free[:1]
                share, rest = divmod(spare, len(grow))
                for n, key in enumerate(grow):
                    base[key] += share + (rest if n == 0 else 0)
            else:
                mins = {k: self.min_width(k) for k in free}
                cap = {k: base[k] - mins[k] for k in free}
                total = sum(cap.values())
                need = min(-spare, total)
                if total > 0:
                    cut = {k: need * cap[k] // total for k in free}
                    left = need - sum(cut.values())
                    for key in sorted(free, key=lambda k: cap[k] - cut[k], reverse=True):
                        if left <= 0:
                            break
                        if cap[key] - cut[key] > 0:
                            cut[key] += 1
                            left -= 1
                    for key in free:
                        base[key] -= cut[key]
            widths.update(base)
        return [widths[k] for k in vis]

    def _layout(self):
        avail = self.body.winfo_width()
        if avail <= 1:   # not laid out yet (or unmapped): default widths, no squeezing
            avail = sum(self._base[k] for k in self.order)
        widths = self.column_widths(avail)
        xs = [0]
        for w in widths:
            xs.append(xs[-1] + w)
        return widths, xs

    # ---- column layout (order / width / visibility) ------------------------------------------
    def layout_state(self) -> dict:
        """What is persisted: order (all keys), hidden keys, user widths at 96 dpi."""
        return {"order": list(self._order),
                "hidden": [k for k in self._order if k in self._hidden],
                "widths": {k: int(round(w / self.scale)) for k, w in self._user_w.items()
                           if k in self._base}}

    def set_layout_state(self, state) -> None:
        """Restore a saved layout; tolerant of anything (unknown keys, bad numbers, missing
        parts). Does not call ``on_layout``."""
        state = state if isinstance(state, dict) else {}
        self._order, self._hidden = reconcile_layout(
            self.keys, state.get("order"), state.get("hidden"))
        self._user_w = {}
        widths = state.get("widths")
        for key, w in (widths.items() if isinstance(widths, dict) else ()):
            if (key in self._base and isinstance(w, (int, float)) and not isinstance(w, bool)
                    and 20 <= w <= 3000):
                self._user_w[key] = int(round(w * self.scale))
        self._request_draw()

    def reset_layout(self) -> None:
        self._order, self._hidden, self._user_w = list(self.keys), set(), {}
        self._request_draw()
        self._changed()

    def toggle_column(self, key: str) -> bool:
        """Show/hide a column; the last visible one cannot be hidden."""
        if key not in self._base:
            return False
        if key in self._hidden:
            self._hidden.discard(key)
        elif len(self.order) > 1:
            self._hidden.add(key)
        else:
            return False
        self._request_draw()
        self._changed()
        return True

    def move_column(self, key: str, position: int) -> None:
        """Move ``key`` so it sits at visible boundary ``position`` (0..n, counted before
        the move)."""
        vis = self.order
        if key not in vis:
            return
        i = vis.index(key)
        if position in (i, i + 1):
            return
        self._order.remove(key)
        if position < len(vis):
            self._order.insert(self._order.index(vis[position]), key)
        else:
            self._order.insert(self._order.index(vis[-1]) + 1, key)
        self._request_draw()
        self._changed()

    def autofit(self, key: str) -> None:
        """Width the column needs for its header and all its cells, capped."""
        if key not in self._base:
            return
        idx = self.keys.index(key)
        hfont = self._fonts[(True, False)]
        want = hfont.measure(self._base_label(key) + " ▼")
        for row in self._rows:
            cell = row.cells[idx]
            if cell.kind == "check":
                continue
            font = self._fonts[("link", False)] if cell.kind == "link" \
                else self._row_style(row, key, cell)[0]
            want = max(want, font.measure(cell.text))
        width = want + 2 * self.pad + 2
        self._user_w[key] = min(max(width, self.min_width(key)),
                                int(round(MAX_COL_PX * self.scale)))
        self._request_draw()
        self._changed()

    def _changed(self) -> None:
        if self._on_layout:
            self._on_layout()

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
        room = max(width - 2 * self.pad, 4)
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
        order = self.order
        slot = {k: self.keys.index(k) for k in order}
        for i, key in enumerate(order):
            head.create_rectangle(xs[i], 0, xs[i + 1], self.head_px, fill=HEAD_BG,
                                  outline=GRID_COLOR)
            head.create_text(xs[i] + self.pad, self.head_px // 2, anchor="w", font=hfont,
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
            elif self._zebra and n % 2 == 1:
                body.create_rectangle(0, y0, total_w, y1, fill=ZEBRA_BG, outline="")
            body.create_line(0, y1, total_w, y1, fill=GRID_COLOR)
            for i, key in enumerate(order):
                cell = row.cells[slot[key]]
                font, fg, _bg = self._row_style(row, key, cell)
                mid = y0 + self.row_px // 2
                centre = (xs[i] + xs[i + 1]) // 2
                if cell.kind == "link":
                    hot = self._hover == (row.iid, key)
                    body.create_text(centre, mid, text=cell.text, fill=LINK_COLOR,
                                     font=self._fonts[("link", False)] if hot
                                     else self._fonts[(False, False)])
                elif cell.kind == "check":
                    self._draw_checkbox(centre, mid, cell.text == CHECKED_GLYPH)
                else:
                    body.create_text(xs[i] + self.pad, mid, anchor="w", font=font, fill=fg,
                                     text=self._fit(cell.text, font, widths[i]))
        body.configure(scrollregion=(0, 0, total_w, max(len(self._rows) * self.row_px,
                                                        body.winfo_height())))
        head.configure(scrollregion=(0, 0, total_w, self.head_px))
        if self._hs and self._hs["mode"] == "drag":
            self._overlay(self._hs["x"])

    def _draw_checkbox(self, cx: int, cy: int, checked: bool) -> None:
        """Crisp checkbox sized to the row (about 55 % of its height, even px)."""
        size = max(12, int(self.row_px * 0.55)) // 2 * 2
        half, line = size // 2, max(1, int(round(self.scale)))
        x0, y0, x1, y1 = cx - half, cy - half, cx + half, cy + half
        body = self.body
        if checked:
            body.create_rectangle(x0, y0, x1, y1, fill=BOX_ON, outline=BOX_ON, width=line)
            body.create_line(x0 + size * 0.22, cy + size * 0.02, x0 + size * 0.42, y1 - size * 0.24,
                             x1 - size * 0.20, y0 + size * 0.26, fill="white",
                             width=max(2, int(round(size / 6))), capstyle="round",
                             joinstyle="round")
        else:
            body.create_rectangle(x0, y0, x1, y1, fill="white", outline=BOX_EDGE, width=line)

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
        for i, key in enumerate(self.order):
            if xs[i] <= x < xs[i + 1]:
                return self._rows[n].iid, key
        return self._rows[n].iid, None

    def _motion(self, event):
        iid, key = self._hit(event)
        hand = bool(iid and key and self.cell(iid, key).kind in ("link", "check"))
        self.body.configure(cursor="hand2" if hand else "")
        hot = (iid, key) if hand and self.cell(iid, key).kind == "link" else None
        if hot != self._hover:   # underline the hovered link only
            self._hover = hot
            self._request_draw()

    def _leave(self, _event):
        if self._hover is not None:
            self._hover = None
            self._request_draw()

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
        """Sort by the header cell under the event."""
        x = self.header.canvasx(event.x)
        _widths, xs = self._layout()
        for i, key in enumerate(self.order):
            if xs[i] <= x < xs[i + 1]:
                self._sort(key)
                return

    # ---- header: resize border, drag to reorder, click to sort ---------------------------------
    def _head_hit(self, event):
        """("border", i): near the right border of display column i (resize it);
        ("cell", i): inside column i; (None, None) otherwise."""
        x = self.header.canvasx(event.x)
        _widths, xs = self._layout()
        reach = max(3, int(round(EDGE_PX * self.scale)))
        best = None
        if self._customizable:
            for i in range(len(xs) - 1):
                dist = abs(x - xs[i + 1])
                if dist <= reach and (best is None or dist < best[0]):
                    best = (dist, i)
        if best:
            return "border", best[1]
        for i in range(len(xs) - 1):
            if xs[i] <= x < xs[i + 1]:
                return "cell", i
        return None, None

    def _head_motion(self, event):
        if self._hs:
            return
        kind, _i = self._head_hit(event)
        self.header.configure(cursor="sb_h_double_arrow" if kind == "border" else "")

    def _head_leave(self, _event):
        if not self._hs:
            self.header.configure(cursor="")

    def _head_press(self, event):
        self._hs = None
        kind, i = self._head_hit(event)
        if kind is None:
            return
        widths, _xs = self._layout()
        x = self.header.canvasx(event.x)
        key = self.order[i]
        if kind == "border":
            self._hs = {"mode": "resize", "key": key, "x0": x, "w0": widths[i], "moved": False}
        else:
            self._hs = {"mode": "press", "key": key, "x0": x, "x": x, "drop": None}

    def _head_double(self, event):
        kind, i = self._head_hit(event)
        if kind == "border":
            self._hs = None
            self.autofit(self.order[i])
        else:
            self._head_press(event)

    def _head_drag(self, event):
        hs = self._hs
        if not hs:
            return
        x = self.header.canvasx(event.x)
        if hs["mode"] == "resize":
            key = hs["key"]
            wide = int(round(MAX_COL_PX * self.scale))
            new = min(max(int(hs["w0"] + x - hs["x0"]), self.min_width(key)), wide)
            if new != self._user_w.get(key) and (hs["moved"] or new != hs["w0"]):
                self._user_w[key] = new
                hs["moved"] = True
                self._request_draw()
            return
        hs["x"] = x
        if (hs["mode"] == "press" and self._customizable
                and abs(x - hs["x0"]) >= max(3, int(round(DRAG_PX * self.scale)))):
            hs["mode"] = "drag"
            self.header.configure(cursor="fleur")
        if hs["mode"] == "drag":
            _widths, xs = self._layout()
            hs["drop"] = min(range(len(xs)), key=lambda p: abs(xs[p] - x))
            self._overlay(x)

    def _head_release(self, event):
        hs, self._hs = self._hs, None
        self.header.delete("ovl")
        self.body.delete("ovl")
        self.header.configure(cursor="")
        if not hs:
            return
        if hs["mode"] == "resize":
            if hs["moved"]:
                self._changed()
        elif hs["mode"] == "press":
            self._sort(hs["key"])
        elif hs["mode"] == "drag":
            _widths, xs = self._layout()
            x = self.header.canvasx(event.x)
            self.move_column(hs["key"], min(range(len(xs)), key=lambda p: abs(xs[p] - x)))

    def _overlay(self, x: float) -> None:
        """Drag feedback: outline on the dragged header, a ghost following the pointer, and a
        thick drop line (header and body) at the boundary the column would land on."""
        hs = self._hs
        head, body = self.header, self.body
        head.delete("ovl")
        body.delete("ovl")
        widths, xs = self._layout()
        order = self.order
        if not hs or hs["key"] not in order:
            return
        i = order.index(hs["key"])
        head.create_rectangle(xs[i] + 1, 1, xs[i + 1] - 1, self.head_px - 1, outline=DROP_COLOR,
                              width=2, tags="ovl")
        gx0 = x - widths[i] / 2
        head.create_rectangle(gx0, 2, gx0 + widths[i], self.head_px - 2, fill=GHOST_BG,
                              outline=DROP_COLOR, tags="ovl")
        head.create_text(gx0 + self.pad, self.head_px // 2, anchor="w", tags="ovl",
                         font=self._fonts[(True, False)],
                         text=self._fit(self._base_label(hs["key"]), self._fonts[(True, False)],
                                        widths[i]))
        drop = hs.get("drop")
        if drop is not None and drop not in (i, i + 1):
            line = max(2, int(round(3 * self.scale)))
            head.create_line(xs[drop], 0, xs[drop], self.head_px, fill=DROP_COLOR, width=line,
                             tags="ovl")
            top = body.canvasy(0)
            body.create_line(xs[drop], top, xs[drop], top + body.winfo_height(), fill=DROP_COLOR,
                             width=line, tags="ovl")

    # ---- header context menu (reset, show/hide columns) ----------------------------------------
    def header_menu(self) -> tk.Menu:
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label=RESET_LABEL, command=self.reset_layout)
        menu.add_separator()
        self._menu_vars = {}
        only = len(self.order) <= 1
        for key in self._order:
            var = tk.BooleanVar(master=self, value=key not in self._hidden)
            self._menu_vars[key] = var
            menu.add_checkbutton(
                label=self._base_label(key), variable=var,
                command=lambda k=key: self.toggle_column(k),
                state="disabled" if only and key not in self._hidden else "normal")
        return menu

    def _head_menu(self, event):
        menu = self.header_menu()
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _sort(self, column: str):
        if self._sortable and self._on_sort:
            self._on_sort(column)
