"""The "Etkinlik günlüğü" tab of the PC window: newest-first list of ``pc.activity`` entries
with category / level / search filters. Local file reads only (no network on the UI thread);
new entries are picked up by ``poll()`` (cheap version check) and the list is only rebuilt
when what is shown actually changed, keeping selection and scroll position."""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import font as tkfont
from tkinter import ttk

from pc import activity as A
from pc import theme as T
from pc.viewmodel import APP_TITLE
from pc.widgets import AutoScrollbar, SelectableText
from tiyatro.models import ISTANBUL

LEVEL_CHOICES = {"Hepsi": "all", "Uyarı ve hata": "warn"}
COLUMNS = (("when", "Zaman", 105), ("cat", "Kategori", 120), ("title", "Özet", 520),
           ("result", "Sonuç", 300))   # widths are only the fallback; see ``column_widths``
ROW_STYLE = "Activity.Treeview"
OPEN_RE = re.compile(r"bilet al[ıi]nabilir|a[cç][ıi]ld[ıi]", re.I)
TINTED = ("error", "warn")


def row_class(entry: dict) -> str:
    """Colour class of a log row: error / warn / open (ticket opened) / notify / changed /
    muted ("değişiklik yok") / plain."""
    level, cat = entry.get("level"), entry.get("cat")
    if level == "error":
        return "error"
    if level == "warn":
        return "warn"
    if cat == A.CAT_TICKET and OPEN_RE.search(entry.get("title") or ""):
        return "open"
    if cat == A.CAT_NOTIFY:
        return "notify"
    result = A.result_text(entry)
    if result.startswith("değişiklik var"):
        return "changed"
    if result.startswith("değişiklik yok") or (entry.get("title") or "").startswith("Hızlı mod:"):
        return "muted"
    return "plain"


def row_tag(entry: dict, index: int) -> str:
    """Tree tag of a row: the class (tinted rows) or class + zebra parity (the others)."""
    cls = row_class(entry)
    return cls if cls in TINTED else f"{cls}_{index % 2}"


def row_height(linespace: int, scaling: float) -> int:
    """Row height: the font's line height plus padding that grows with the DPI scaling
    (Tk scaling is pixels per point; 1.333 = 96 dpi)."""
    return linespace + max(6, round(8 * scaling * 72 / 96))


def tag_colours() -> dict[str, dict]:
    """Tag -> {foreground, background, bold} from the system colours (``pc.theme``)."""
    def on(tint, hue):
        return T.readable(hue, bg=T.rgb_of(tint))
    out: dict[str, dict] = {
        "error": {"foreground": on(T.ERROR_ROW, T.RED), "background": T.ERROR_ROW},
        "warn": {"foreground": on(T.WARN_ROW, T.AMBER), "background": T.WARN_ROW},
        "sel": {"foreground": T.HILITE_TEXT, "background": T.HILITE},
    }
    fg = {"plain": T.WINDOW_TEXT, "muted": T.GRAY, "notify": T.BLUE, "open": T.GREEN,
          "changed": T.WINDOW_TEXT}
    for cls, colour in fg.items():
        for parity, bg in enumerate((T.WINDOW, T.ZEBRA)):
            out[f"{cls}_{parity}"] = {"foreground": colour, "background": bg,
                                      "bold": cls in ("open", "changed")}
    return out


def column_widths(font, pad: int) -> dict[str, int]:
    """Pixel widths from the real font: Zaman fits "dd.mm HH:MM", Kategori the longest category."""
    m = font.measure
    return {"when": m("00.00 00:00") + 2 * pad,
            "cat": max(m(c) for c in A.CATEGORIES) + 2 * pad,
            "title": m("x" * 60), "result": m("x" * 34) + 2 * pad}


SAVE_DELAY_MS = 500


def default_reveal(path: Path) -> None:
    """Show the log file in Explorer (falls back to opening its folder)."""
    try:
        if path.exists() and os.name == "nt":
            subprocess.Popen(["explorer", f"/select,{path}"])  # noqa: S603,S607
        elif hasattr(os, "startfile"):
            os.startfile(str(path.parent))  # type: ignore[attr-defined]
    except OSError:
        pass


class DetailDialog(tk.Toplevel):
    def __init__(self, parent, entry: dict):
        super().__init__(parent)
        self.title("Kayıt ayrıntısı")
        self.transient(parent.winfo_toplevel())
        stamp = A.format_ts(entry["ts"])
        lines = [A.sentence(entry), "", f"Zaman: {stamp}", f"Kategori: {entry['cat']}",
                 f"Düzey: {A.level_label(entry['level'])}"]
        if entry["detail"].strip():
            lines += ["", entry["detail"]]
        box = ttk.Frame(self)
        box.pack(fill="both", expand=True, padx=10, pady=(10, 4))
        self.body = SelectableText(box, width=70, height=12, box=True, autosize=False)
        sb = AutoScrollbar(box, orient="vertical", command=self.body.yview)
        self.body.configure(yscrollcommand=sb.set)
        self.body.set_text("\n".join(lines))
        self.body.scrolls = lambda: bool(sb.winfo_ismapped())
        self.body.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        ttk.Button(self, text="Kapat", command=self.destroy).pack(anchor="e", padx=10, pady=8)
        self.bind("<Escape>", lambda _e: self.destroy())


class ActivityTab(ttk.Frame):
    def __init__(self, parent, log: A.ActivityLog, filters: dict, on_change, mb,
                 reveal=default_reveal):
        super().__init__(parent)
        self.log, self.mb, self.reveal = log, mb, reveal
        self._on_change = on_change
        self._entries: dict[str, dict] = {}   # iid -> entry (what the tree shows)
        self._keys: list[str] = []
        self._seen_version = -1
        self._last_filter = None
        self.refresh_count = 0
        self.detail_dialog: DetailDialog | None = None

        bar = ttk.LabelFrame(self, text="Süz")
        bar.pack(fill="x", padx=4, pady=(6, 2))
        cats = ttk.Frame(bar)
        cats.pack(fill="x", padx=6, pady=(2, 0))
        self.cat_vars: dict[str, tk.BooleanVar] = {}
        for i, cat in enumerate(A.CATEGORIES):
            var = tk.BooleanVar(value=cat in filters["cats"])
            self.cat_vars[cat] = var
            ttk.Checkbutton(cats, text=cat, variable=var, command=self.filters_changed).grid(
                row=i // 4, column=i % 4, sticky="w", padx=(0, 14))
        row = ttk.Frame(bar)
        row.pack(fill="x", padx=6, pady=4)
        ttk.Label(row, text="Düzey:").pack(side="left")
        self.level_var = tk.StringVar(
            value="Uyarı ve hata" if filters["level"] == "warn" else "Hepsi")
        self.level_box = ttk.Combobox(row, textvariable=self.level_var, state="readonly",
                                      width=14, values=list(LEVEL_CHOICES))
        self.level_box.pack(side="left", padx=(4, 12))
        self.level_box.bind("<<ComboboxSelected>>", lambda _e: self.filters_changed())
        ttk.Label(row, text="Ara:").pack(side="left")
        self.search_var = tk.StringVar(value=filters["text"])
        self._save_after = None
        self.search_var.trace_add("write", lambda *_a: self._search_changed())
        ttk.Entry(row, textvariable=self.search_var, width=28).pack(side="left", padx=4)
        self.count_var = tk.StringVar()
        ttk.Label(row, textvariable=self.count_var, foreground=T.GRAY_TEXT).pack(
            side="left", padx=12)
        self.reveal_btn = ttk.Button(row, text="Dosyayı göster", command=self._reveal)
        self.reveal_btn.pack(side="right")
        self.clear_btn = ttk.Button(row, text="Temizle", command=self.clear)
        self.clear_btn.pack(side="right", padx=6)

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=4, pady=(2, 4))
        style = ttk.Style(self)
        base = tkfont.nametofont(style.lookup("Treeview", "font") or "TkDefaultFont")
        self.row_font = base
        scaling = float(self.tk.call("tk", "scaling"))
        self.row_px = row_height(base.metrics("linespace"), scaling)
        style.configure(ROW_STYLE, rowheight=self.row_px, background=T.WINDOW,
                        fieldbackground=T.WINDOW, foreground=T.WINDOW_TEXT)
        style.map(ROW_STYLE, background=[("selected", T.HILITE)],
                  foreground=[("selected", T.HILITE_TEXT)])
        self.tree = ttk.Treeview(body, columns=[c[0] for c in COLUMNS], show="headings",
                                 selectmode="browse", style=ROW_STYLE)
        widths = column_widths(base, max(8, round(scaling * 72 / 96 * 8)))
        for key, label, _w in COLUMNS:
            self.tree.heading(key, text=label)
            self.tree.column(key, width=widths[key], anchor="w", stretch=key == "title",
                             minwidth=40)
        vsb = AutoScrollbar(body, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self._bold = base.copy()
        self._bold.configure(weight="bold")
        self._base_tags: dict[str, tuple] = {}
        self._selected: tuple = ()
        for tag, spec in tag_colours().items():
            opts = {"foreground": spec["foreground"], "background": spec["background"]}
            if spec.get("bold"):
                opts["font"] = self._bold
            self.tree.tag_configure(tag, **opts)
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._sync_selection_tags(), add="+")
        self.tree.bind("<Double-1>", self._double_click)
        self.tree.bind("<Return>", lambda _e: self.open_detail())
        self.tree.bind("<Control-c>", lambda _e: self.copy_selected())
        self.tree.bind("<Control-Insert>", lambda _e: self.copy_selected())
        self.tree.bind("<Button-3>", self._row_menu)
        self.row_menu = tk.Menu(self.tree, tearoff=0)
        self.row_menu.add_command(label="Kopyala", command=self.copy_selected)
        self.row_menu.add_command(label="Ayrıntı", command=self.open_detail)
        legend = ttk.Frame(self)
        legend.pack(fill="x", padx=6, pady=(0, 4))
        ttk.Label(legend, text="Renkler:", foreground=T.GRAY_TEXT).pack(side="left")
        colours = tag_colours()
        for text, colour in (("hata", colours["error"]["foreground"]),
                             ("uyarı", colours["warn"]["foreground"]),
                             ("bilet açıldı", T.GREEN), ("bildirim", T.BLUE),
                             ("değişiklik yok", T.GRAY)):
            ttk.Label(legend, text="● " + text, foreground=colour).pack(side="left", padx=(8, 0))
        self.refresh()

    # ---- filter state ----------------------------------------------------------------
    def filters(self) -> dict:
        return {"cats": [c for c in A.CATEGORIES if self.cat_vars[c].get()],
                "level": LEVEL_CHOICES.get(self.level_var.get(), "all"),
                "text": self.search_var.get()[:200]}

    def filters_changed(self) -> None:
        self.refresh()
        self._on_change()

    def _search_changed(self) -> None:
        """Filter live, but save ui.json only after typing paused (not per keystroke)."""
        self.refresh()
        if self._save_after is not None:
            self.after_cancel(self._save_after)
        self._save_after = self.after(SAVE_DELAY_MS, self._save_search)

    def _save_search(self) -> None:
        self._save_after = None
        self._on_change()

    # ---- list ----------------------------------------------------------------------------
    @staticmethod
    def _iid(entry: dict, counter: dict) -> str:
        key = f"{entry['ts']}|{entry['cat']}|{entry['title']}"
        counter[key] = counter.get(key, 0) + 1
        return "e" + hashlib.md5(key.encode("utf-8")).hexdigest()[:12] + f"-{counter[key]}"

    def refresh(self) -> None:
        """Rebuild the list when the log or the filter changed; keeps selection/scroll."""
        version = self.log.version
        flt = self.filters()
        loaded = self.log.load()
        if self.log.read_error:  # unreadable right now: keep what is shown, retry next poll
            return
        self._seen_version = version
        entries = A.filter_entries(loaded, set(flt["cats"]), flt["level"] if flt["level"]
                                   == "warn" else "info", flt["text"])
        counter: dict = {}
        ids = [self._iid(e, counter) for e in reversed(entries)][::-1]  # oldest gets #1
        self.count_var.set(f"{len(entries)} / {len(loaded)} kayıt"
                           + (" (süzgeç açık)" if len(entries) != len(loaded) else ""))
        if ids == self._keys and flt == self._last_filter:
            return
        self._last_filter = flt
        tree = self.tree
        selected = tree.selection()
        top = tree.yview()[0]
        old_keys = self._keys
        anchor = old_keys[min(len(old_keys) - 1, round(top * len(old_keys)))] \
            if old_keys and top > 0.0001 else None
        tree.delete(*tree.get_children())
        now = datetime.now(ISTANBUL)
        self._entries = dict(zip(ids, entries))
        self._base_tags = {}
        for n, (iid, e) in enumerate(zip(ids, entries)):
            self._base_tags[iid] = (row_tag(e, n),)
            tree.insert("", "end", iid=iid, tags=self._base_tags[iid],
                        values=(A.when_text(e["ts"], now), e["cat"], e["title"],
                                A.result_text(e)))
        self._keys = ids
        keep = [i for i in selected if i in self._entries]
        self._selected = ()
        if keep:
            tree.selection_set(keep)
        self._sync_selection_tags()
        if anchor in self._entries:
            tree.yview_moveto(ids.index(anchor) / max(1, len(ids)))
        else:
            tree.yview_moveto(0)
        self.refresh_count += 1

    def _sync_selection_tags(self) -> None:
        """A tag background hides the native selection colour, so the selected row carries the
        "sel" tag (system highlight colours) instead of its colour tag."""
        now = tuple(self.tree.selection())
        for iid in self._selected:
            if iid not in now and iid in self._base_tags and self.tree.exists(iid):
                self.tree.item(iid, tags=self._base_tags[iid])
        for iid in now:
            self.tree.item(iid, tags=("sel",))
        self._selected = now

    def poll(self) -> None:
        """Called by the app timer and after every tick: cheap unless something was added."""
        if self.log.version != self._seen_version:
            self.refresh()

    # ---- actions ---------------------------------------------------------------------------
    def clear(self) -> None:
        if self.mb.askyesno(APP_TITLE, "Günlükteki tüm kayıtlar silinsin mi?\n\n"
                                       "Bu işlem geri alınamaz."):
            self.log.clear()
            self.refresh()

    def _reveal(self) -> None:
        self.reveal(self.log.path)

    def _double_click(self, event) -> None:
        iid = self.tree.identify_row(event.y)
        if iid:
            self.tree.selection_set(iid)
            self.open_detail()

    def row_text(self, iid: str) -> str:
        """The row as the user sees it (tab separated: Zaman, Kategori, Özet, Sonuç)."""
        return "\t".join(str(v) for v in self.tree.item(iid, "values"))

    def copy_selected(self) -> str:
        """Selected rows (one per line) to the clipboard."""
        text = "\n".join(self.row_text(i) for i in self.tree.selection())
        if text:
            self.clipboard_clear()
            self.clipboard_append(text)
        return "break"

    def _row_menu(self, event) -> str:
        iid = self.tree.identify_row(event.y)
        if iid:
            if iid not in self.tree.selection():
                self.tree.selection_set(iid)
            try:
                self.row_menu.tk_popup(event.x_root, event.y_root)
            finally:
                self.row_menu.grab_release()
        return "break"

    def open_detail(self) -> DetailDialog | None:
        sel = self.tree.selection()
        entry = self._entries.get(sel[0]) if sel else None
        if entry is None:
            return None
        if self.detail_dialog is not None and self.detail_dialog.winfo_exists():
            self.detail_dialog.destroy()
        self.detail_dialog = DetailDialog(self, entry)
        return self.detail_dialog
