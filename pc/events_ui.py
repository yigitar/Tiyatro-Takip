"""The "Diğer kaynaklar" tab: Başiskele Belediyesi and VKV Gölcük events.

Columns: Kaynak, Etkinlik, Tarih/Saat, Kitle, Tür, Rezervasyon durumu, Takip. The follow
switch of a row (click the Takip cell, Space/Enter, or the button) writes
``config.event_follow`` through the service; a switched-off event stays in the list,
greyed. Rows come from ``service.event_views()`` (local state only, no network here) and
the list is only rebuilt when what is shown changed, keeping selection and scroll.
"""
from __future__ import annotations

import logging
import tkinter as tk
from tkinter import ttk

from pc.widgets import AutoScrollbar

log = logging.getLogger("tiyatro.pc")

COLUMNS = (("source", "Kaynak", 120), ("title", "Etkinlik", 300), ("when", "Tarih/Saat", 125),
           ("audience", "Kitle", 75), ("genre", "Tür", 110),
           ("reservation", "Rezervasyon durumu", 190), ("follow", "Takip", 90))
FOLLOW_ON, FOLLOW_OFF = "☑ Takipte", "☐ Kapalı"
HINT = ("Başiskele Belediyesi ve VKV Gölcük (Tiyatro / Çocuk Tiyatrosu) etkinlikleri. "
        "Takip sütununa tıklayarak bir etkinliğin bildirimlerini açıp kapatabilirsin; "
        "kapalı etkinlikler listede gri kalır.")


def row_values(view: dict) -> tuple[str, ...]:
    return (view["source_name"], view["title"], view["when"], view["audience"] or "—",
            view["genre"] or "—", view["reservation"],
            FOLLOW_ON if view["followed"] else FOLLOW_OFF)


class EventsTab(ttk.Frame):
    def __init__(self, parent, service, edit, open_url):
        """``edit(fn, *args, key=..., widget=...)`` is the app's background-edit helper."""
        super().__init__(parent)
        self.service, self.edit, self.open_url = service, edit, open_url
        self.views: dict[str, dict] = {}
        self._sig = None
        ttk.Label(self, text=HINT, wraplength=900, justify="left").pack(
            fill="x", padx=8, pady=(6, 4))
        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=8, pady=(0, 4))
        self.toggle_btn = ttk.Button(bar, text="Takibi aç/kapat", command=self.toggle_selected)
        self.toggle_btn.pack(side="left")
        self.open_btn = ttk.Button(bar, text="Sayfayı aç", command=self.open_selected)
        self.open_btn.pack(side="left", padx=6)
        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.tree = ttk.Treeview(body, columns=[c[0] for c in COLUMNS], show="headings",
                                 selectmode="browse")
        for key, label, width in COLUMNS:
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, anchor="w", stretch=key == "title")
        vsb = AutoScrollbar(body, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tree.tag_configure("off", foreground="#9ca3af")
        self.tree.tag_configure("full", foreground="#7f1d1d")
        self.tree.bind("<Button-1>", self._click, add="+")
        self.tree.bind("<space>", lambda _e: self.toggle_selected())
        self.tree.bind("<Return>", lambda _e: self.toggle_selected())
        self.empty = ttk.Label(body, text="Henüz etkinlik bilgisi yok.")

    # ---- data ------------------------------------------------------------------------
    def refresh(self) -> None:
        fn = getattr(self.service, "event_views", None)
        try:
            views = fn() if fn is not None else []
        except Exception:
            log.exception("event_views() failed")
            return
        sig = tuple((v["id"], row_values(v)) for v in views)
        if sig == self._sig:
            return
        self._sig = sig
        self.views = {v["id"]: v for v in views}
        selected = self.tree.selection()
        top = self.tree.yview()[0]
        self.tree.delete(*self.tree.get_children())
        for v in views:
            tags = []
            if not v["followed"]:
                tags.append("off")
            elif v["full"]:
                tags.append("full")
            self.tree.insert("", "end", iid=v["id"], values=row_values(v), tags=tuple(tags))
        if selected and selected[0] in self.views:
            self.tree.selection_set(selected[0])
        self.tree.yview_moveto(top)
        if views:
            self.empty.place_forget()
        else:
            self.empty.place(relx=0.5, rely=0.3, anchor="center")

    def selected_view(self) -> dict | None:
        sel = self.tree.selection()
        return self.views.get(sel[0]) if sel else None

    # ---- actions ---------------------------------------------------------------------
    def toggle(self, event_id: str) -> None:
        view = self.views.get(event_id)
        if view is None:
            return
        self.edit(self.service.set_event_follow, event_id, not view["followed"],
                  key=("ef", event_id))

    def toggle_selected(self) -> None:
        view = self.selected_view()
        if view is not None:
            self.toggle(view["id"])

    def open_selected(self) -> None:
        view = self.selected_view()
        if view is not None and view["url"]:
            self.open_url(view["url"])

    def _click(self, event) -> None:
        if self.tree.identify_region(event.x, event.y) != "cell":
            return
        if self.tree.identify_column(event.x) != f"#{len(COLUMNS)}":
            return
        row = self.tree.identify_row(event.y)
        if row:
            self.tree.selection_set(row)
            self.toggle(row)
