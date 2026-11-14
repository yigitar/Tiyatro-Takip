"""The "Etkinlik günlüğü" tab of the PC window: newest-first list of ``pc.activity`` entries
with category / level / search filters. Local file reads only (no network on the UI thread);
new entries are picked up by ``poll()`` (cheap version check) and the list is only rebuilt
when what is shown actually changed, keeping selection and scroll position."""
from __future__ import annotations

import hashlib
import os
import subprocess
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import ttk

from pc import activity as A
from pc.viewmodel import APP_TITLE
from tiyatro.models import ISTANBUL

LEVEL_CHOICES = {"Hepsi": "all", "Uyarı ve hata": "warn"}
COLUMNS = (("when", "Zaman", 105), ("cat", "Kategori", 120), ("title", "Özet", 520),
           ("result", "Sonuç", 300))
WARN_BG, ERROR_BG, ERROR_FG = "#fff4cc", "#fde2e2", "#7f1d1d"


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
        when = A._when(entry["ts"])
        stamp = when.strftime("%d.%m.%Y %H:%M:%S") if when else entry["ts"]
        lines = [A.sentence(entry), "", f"Zaman: {stamp}", f"Kategori: {entry['cat']}",
                 f"Düzey: {A.level_label(entry['level'])}"]
        if entry["detail"].strip():
            lines += ["", entry["detail"]]
        box = ttk.Frame(self)
        box.pack(fill="both", expand=True, padx=10, pady=(10, 4))
        self.body = tk.Text(box, width=70, height=12, wrap="word", font="TkDefaultFont",
                            borderwidth=1, relief="solid")
        sb = ttk.Scrollbar(box, orient="vertical", command=self.body.yview)
        self.body.configure(yscrollcommand=sb.set)
        self.body.insert("1.0", "\n".join(lines))
        self.body.configure(state="disabled")
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
        self.search_var.trace_add("write", lambda *_a: self.filters_changed())
        ttk.Entry(row, textvariable=self.search_var, width=28).pack(side="left", padx=4)
        self.count_var = tk.StringVar()
        ttk.Label(row, textvariable=self.count_var, foreground="#6b7280").pack(
            side="left", padx=12)
        self.reveal_btn = ttk.Button(row, text="Dosyayı göster", command=self._reveal)
        self.reveal_btn.pack(side="right")
        self.clear_btn = ttk.Button(row, text="Temizle", command=self.clear)
        self.clear_btn.pack(side="right", padx=6)

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=4, pady=(2, 4))
        self.tree = ttk.Treeview(body, columns=[c[0] for c in COLUMNS], show="headings",
                                 selectmode="browse")
        for key, label, width in COLUMNS:
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, anchor="w", stretch=key in ("title", "result"))
        vsb = ttk.Scrollbar(body, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tree.tag_configure("warn", background=WARN_BG)
        self.tree.tag_configure("error", background=ERROR_BG, foreground=ERROR_FG)
        self.tree.bind("<Double-1>", self._double_click)
        self.tree.bind("<Return>", lambda _e: self.open_detail())
        self.refresh()

    # ---- filter state ----------------------------------------------------------------
    def filters(self) -> dict:
        return {"cats": [c for c in A.CATEGORIES if self.cat_vars[c].get()],
                "level": LEVEL_CHOICES.get(self.level_var.get(), "all"),
                "text": self.search_var.get()[:200]}

    def filters_changed(self) -> None:
        self.refresh()
        self._on_change()

    # ---- list ----------------------------------------------------------------------------
    @staticmethod
    def _iid(entry: dict, counter: dict) -> str:
        key = f"{entry['ts']}|{entry['cat']}|{entry['title']}"
        counter[key] = counter.get(key, 0) + 1
        return "e" + hashlib.md5(key.encode("utf-8")).hexdigest()[:12] + f"-{counter[key]}"

    def refresh(self) -> None:
        """Rebuild the list when the log or the filter changed; keeps selection/scroll."""
        self._seen_version = self.log.version
        flt = self.filters()
        loaded = self.log.load()
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
        for iid, e in zip(ids, entries):
            tree.insert("", "end", iid=iid, tags=(e["level"],) if e["level"] != "info" else (),
                        values=(A.when_text(e["ts"], now), e["cat"], e["title"],
                                A.result_text(e)))
        self._keys = ids
        keep = [i for i in selected if i in self._entries]
        if keep:
            tree.selection_set(keep)
        if anchor in self._entries:
            tree.yview_moveto(ids.index(anchor) / max(1, len(ids)))
        else:
            tree.yview_moveto(0)
        self.refresh_count += 1

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

    def open_detail(self) -> DetailDialog | None:
        sel = self.tree.selection()
        entry = self._entries.get(sel[0]) if sel else None
        if entry is None:
            return None
        if self.detail_dialog is not None and self.detail_dialog.winfo_exists():
            self.detail_dialog.destroy()
        self.detail_dialog = DetailDialog(self, entry)
        return self.detail_dialog
