"""The "Bildirim geçmişi" tab: every meaningful change as one exact line with its time, read
from ``state["history"]`` (written by the scanner, so it does not depend on this app having
been open). Newest first, filter by kind / source, selectable text, "okundu" watermark
(entries newer than it are bold) kept in ui.json. The tree is rebuilt only when what is
shown changed (compute-once layout)."""
from __future__ import annotations

import json
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import font as tkfont
from tkinter import ttk

from pc import theme as T
from pc.widgets import AutoScrollbar
from tiyatro import history as H
from tiyatro.models import ISTANBUL
from tiyatro.timeutil import parse_iso

ALL = "Hepsi"
SOURCE_LABEL = {"biletinial": "Biletinial", "basiskele": "Başiskele", "vkv": "VKV"}
COLUMNS = (("when", "Zaman", 110), ("group", "Tür", 90), ("source", "Kaynak", 90),
           ("text", "Gelişme", 640))


def when_text(ts: str) -> str:
    try:
        d = parse_iso(ts)
    except (TypeError, ValueError):
        return ""
    return f"{d.astimezone(ISTANBUL):%d.%m %H:%M}" if d else ""


def visible(hist, group: str = ALL, source: str = ALL) -> list[dict]:
    """Entries to show: newest first, filtered; unusable entries are skipped."""
    out = [h for h in (hist if isinstance(hist, list) else [])
           if isinstance(h, dict) and isinstance(h.get("text"), str) and isinstance(h.get("ts"), str)]
    if group != ALL:
        out = [h for h in out if H.GROUP_OF.get(h.get("kind"), "diğer") == group]
    if source != ALL:
        out = [h for h in out if SOURCE_LABEL.get(h.get("source"), h.get("source")) == source]
    return sorted(out, key=lambda h: h["ts"], reverse=True)


def unread(hist, read_ts: str) -> int:
    """Entries newer than the "okundu" watermark (quiet kinds do not count: no notification)."""
    return sum(1 for h in visible(hist) if h["ts"] > (read_ts or "")
               and h.get("kind") not in H.QUIET_KINDS)


def load(path) -> dict:
    out = {"read_ts": "", "group": ALL, "source": ALL}
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8")).get("history")
    except (OSError, ValueError, AttributeError):
        return out
    if isinstance(data, dict):
        if isinstance(data.get("read_ts"), str):
            out["read_ts"] = data["read_ts"][:40]
        if data.get("group") in (ALL, *H.GROUPS):
            out["group"] = data["group"]
        if data.get("source") in (ALL, *SOURCE_LABEL.values()):
            out["source"] = data["source"]
    return out


class HistoryTab(ttk.Frame):
    def __init__(self, parent, get_history, saved: dict, on_change, on_open, on_unread=None):
        super().__init__(parent)
        self._get, self._on_change, self._on_open = get_history, on_change, on_open
        self._on_unread = on_unread or (lambda _n: None)
        self.read_ts = saved.get("read_ts", "")
        self._shown: list[dict] = []
        self._sig = None
        self.refresh_count = 0
        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=6, pady=(6, 2))
        ttk.Label(bar, text="Tür:").pack(side="left")
        self.group_var = tk.StringVar(value=saved.get("group", ALL))
        g = ttk.Combobox(bar, textvariable=self.group_var, state="readonly", width=12,
                         values=[ALL, *H.GROUPS])
        g.pack(side="left", padx=(4, 12))
        ttk.Label(bar, text="Kaynak:").pack(side="left")
        self.source_var = tk.StringVar(value=saved.get("source", ALL))
        s = ttk.Combobox(bar, textvariable=self.source_var, state="readonly", width=12,
                         values=[ALL, *SOURCE_LABEL.values()])
        s.pack(side="left", padx=4)
        for box in (g, s):
            box.bind("<<ComboboxSelected>>", lambda _e: self._filters_changed())
        self.count_var = tk.StringVar()
        ttk.Label(bar, textvariable=self.count_var, foreground=T.GRAY_TEXT).pack(side="left", padx=12)
        self.read_btn = ttk.Button(bar, text="Tümünü okundu say", command=self.mark_read)
        self.read_btn.pack(side="right")
        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=6, pady=(2, 6))
        base = tkfont.nametofont(ttk.Style(self).lookup("Treeview", "font") or "TkDefaultFont")
        self._bold = base.copy()
        self._bold.configure(weight="bold")
        self.tree = ttk.Treeview(body, columns=[c[0] for c in COLUMNS], show="headings",
                                 selectmode="browse")
        for key, label, width in COLUMNS:
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, anchor="w", stretch=key == "text", minwidth=50)
        vsb = AutoScrollbar(body, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tree.tag_configure("unread", font=self._bold)
        self.tree.tag_configure("quiet", foreground=T.GRAY_TEXT)
        self.tree.bind("<Double-1>", lambda _e: self.open_selected())
        self.tree.bind("<Return>", lambda _e: self.open_selected())
        self.tree.bind("<Control-c>", lambda _e: self.copy_selected())
        self.tree.bind("<Button-3>", self._menu)
        self.menu = tk.Menu(self.tree, tearoff=0)
        self.menu.add_command(label="Kopyala", command=self.copy_selected)
        self.menu.add_command(label="Listede göster", command=self.open_selected)
        self.refresh()

    # ---- state -------------------------------------------------------------------------
    def to_json(self) -> dict:
        return {"read_ts": self.read_ts, "group": self.group_var.get(), "source": self.source_var.get()}

    def refresh(self) -> None:
        hist = self._get()
        shown = visible(hist, self.group_var.get(), self.source_var.get())
        n_unread = unread(hist, self.read_ts)
        sig = (tuple(h["key"] for h in shown), self.read_ts)
        self._on_unread(n_unread)
        self.count_var.set(f"{len(shown)} kayıt" + (f" · {n_unread} okunmamış" if n_unread else ""))
        if sig == self._sig:
            return
        self._sig = sig
        self._shown = shown
        keep = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        for i, h in enumerate(shown):
            tags = []
            if h["ts"] > self.read_ts and h.get("kind") not in H.QUIET_KINDS:
                tags.append("unread")
            if h.get("kind") in H.QUIET_KINDS or h.get("kind") == "OPENED_SOLD_OUT":
                tags.append("quiet")
            self.tree.insert("", "end", iid=f"h{i}", tags=tags, values=(
                when_text(h["ts"]), H.GROUP_OF.get(h.get("kind"), "diğer"),
                SOURCE_LABEL.get(h.get("source"), h.get("source") or ""), h["text"]))
        for iid in keep:
            if self.tree.exists(iid):
                self.tree.selection_set(iid)
        self.refresh_count += 1

    def _filters_changed(self) -> None:
        self.refresh()
        self._on_change()

    def mark_read(self) -> None:
        newest = max((h["ts"] for h in visible(self._get())), default="")
        if newest and newest > self.read_ts:
            self.read_ts = newest
            self.refresh()
            self._on_change()

    # ---- actions -----------------------------------------------------------------------
    def _selected_entry(self) -> dict | None:
        sel = self.tree.selection()
        if not sel:
            return None
        i = int(sel[0][1:])
        return self._shown[i] if i < len(self._shown) else None

    def row_text(self, iid: str) -> str:
        return "\t".join(str(v) for v in self.tree.item(iid, "values"))

    def copy_selected(self) -> str:
        sel = self.tree.selection()
        text = "\n".join(self.row_text(i) for i in sel)
        if text:
            self.clipboard_clear()
            self.clipboard_append(text)
        return text

    def open_selected(self) -> None:
        e = self._selected_entry()
        if e and e.get("row"):
            self._on_open(e["row"])

    def _menu(self, event) -> None:
        iid = self.tree.identify_row(event.y)
        if iid:
            self.tree.selection_set(iid)
            self.menu.tk_popup(event.x_root, event.y_root)
