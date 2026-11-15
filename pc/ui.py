"""Tiyatro Takip PC window (Tk). Start with ``python -m pc.app`` (see pc/start.bat).

All decisions live in ``pc.app_logic`` / ``pc.viewmodel`` / ``pc.service``; this module
only wires widgets. Network I/O never runs on the UI thread: a scan worker thread runs
``Service.tick()`` and everything the user triggers (refresh, config edits) runs in a
short-lived thread; results come back through a ``queue.Queue`` polled with ``after``.
"""
from __future__ import annotations

import logging
import logging.handlers
import queue
import sys
import threading
import tkinter as tk
import webbrowser
from collections import deque
from datetime import datetime
from pathlib import Path
from tkinter import font as tkfont
from tkinter import messagebox, ttk

try:  # Windows only; beeps are skipped elsewhere
    import winsound
except ImportError:  # pragma: no cover
    winsound = None

from pc import activity as A
from pc import app_logic as L
from pc import winsetup
from pc.activity_ui import ActivityTab, default_reveal
from pc.service import RefreshError
from pc.settings import DEFAULT_LOCAL_DIR, SettingsError, load_settings
from pc.table import DERIVED_COLOR, GridTable
from pc.viewmodel import APP_TITLE, DAY_SHORT, credit_lines
from pc.widgets import AutoScrollbar, Flow, LinkButton, RichLabel, autosize_text
from tiyatro import sources as S
from tiyatro.config import config_to_dict
from tiyatro.models import ISTANBUL

log = logging.getLogger("pc.app")

STRIP_BG, STRIP_EDGE, VALUE_COLOR = "#eef2f7", "#c7d0dc", "#111827"
LEGEND_BG, LEGEND_EDGE = "#f8fafc", "#cbd5e1"
CARD_BG, CARD_MIN_PX = "white", 300
POLL_MS = 200
STATUS_MS = 5000
RELOAD_MS = 60_000
ACTIVITY_MS = 2000
MAX_POPUPS = 3
NO_INFO = "Bilgi yok"
DETAIL_CHROME_PX = 30  # title + border of the "Oyun ayrıntısı" frame
COLUMNS = L.LIST_COLUMNS      # (key, heading, width): one table for plays and source events
NEW_COLOR = "#6d28d9"         # name of an item the user has not opened yet (bold)
FOLLOW_HELP_EVENT = ("Takip açıkken bu etkinlik için rezervasyon açılış saati duyurulunca, "
                     "açılışa 1 gün ve 10 dk kala ve açıldığında bildirim gelir; açılış saati "
                     "yayınlanmadıysa şimdilik bildirim gelmez.")
NO_SESSION_PAGE = "Bu kaynakta seans başına ayrı sayfa yok: düğme etkinlik sayfasını açar."


def setup_logging(local_dir: Path, secrets: list[str]) -> None:
    """Rotating ``app.log``; every line is scrubbed of tokens (``secrets`` may grow later)."""

    class Scrubbing(logging.Formatter):
        def format(self, record):
            return L.scrub_secrets(super().format(record), secrets)

    local_dir.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        local_dir / "app.log", maxBytes=512_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(Scrubbing("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)


_beeping = threading.Lock()


def default_beep() -> None:
    """One beep pattern in a thread; skipped while a previous one is still playing."""
    if winsound is None or not _beeping.acquire(blocking=False):
        return

    def run():
        try:
            winsound.Beep(1100, 220)
            winsound.Beep(1500, 220)
            winsound.Beep(1100, 220)
        except Exception:  # no sound device
            pass
        finally:
            _beeping.release()
    threading.Thread(target=run, daemon=True).start()


class ScanWorker(threading.Thread):
    """Runs ``Service.tick()`` in a loop, sleeping ``next_interval()`` (wake-able)."""

    def __init__(self, service, post):
        super().__init__(name="scan-worker", daemon=True)
        self.service, self.post = service, post
        self._stop_ev, self._wake = threading.Event(), threading.Event()
        self._fails = 0

    def wake(self) -> None:
        self._wake.set()

    def stop(self) -> None:
        self._stop_ev.set()
        self._wake.set()

    def run(self) -> None:
        while not self._stop_ev.is_set():
            try:
                self.post(("tick", self.service.tick()))
                self._fails = 0
            except Exception as exc:
                self._fails += 1
                log.exception("tick failed")
                self.post(("worker_error", f"{type(exc).__name__}: {exc}"))
            try:
                wait = float(self.service.next_interval())
            except Exception:
                log.exception("next_interval failed")
                wait = 30.0
            if self._fails:  # never a tight retry loop
                wait = max(wait, min(30.0 * 2 ** (self._fails - 1), 300.0))
            self._wake.wait(min(max(wait, 1.0), 3600.0))
            self._wake.clear()


class Tooltip:
    def __init__(self, widget, text_fn):
        self.widget, self.text_fn, self.tip = widget, text_fn, None
        widget.bind("<Enter>", self._show, add="+")
        widget.bind("<Leave>", self._hide, add="+")

    def _show(self, _e=None):
        text = self.text_fn()
        if not text or self.tip is not None:
            return
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        self.tip.wm_geometry(f"+{self.widget.winfo_rootx() + 10}+"
                             f"{self.widget.winfo_rooty() - 10 - 18 * min(text.count(chr(10)) + 1, 8)}")
        tk.Label(self.tip, text=text, justify="left", background="#ffffe0", relief="solid",
                 borderwidth=1, wraplength=520).pack()

    def _hide(self, _e=None):
        if self.tip is not None:
            self.tip.destroy()
            self.tip = None


class Popup(tk.Toplevel):
    """One desktop notification: topmost, never grabs focus (beeps: ``App.alert``)."""

    def __init__(self, app, title: str, text: str, url: str):
        super().__init__(app.root)
        self.app, self.url = app, url
        title, text = L.popup_text(title, text)
        self.title(title)
        self.attributes("-topmost", True)
        try:
            self.attributes("-toolwindow", True)
        except tk.TclError:
            pass
        self.resizable(False, False)
        ttk.Label(self, text=title, font=app.bold_font, wraplength=400).pack(
            anchor="w", padx=10, pady=(10, 4))
        box = ttk.Frame(self)
        box.pack(fill="both", expand=True, padx=10)
        self.body = tk.Text(box, width=52, height=min(12, max(3, text.count("\n") + 3)),
                            wrap="word", relief="flat", background=self.cget("background"))
        self.body.insert("1.0", text)
        self.body.configure(state="disabled")
        self.body.pack(side="left", fill="both", expand=True)
        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=10, pady=10)
        self.open_btn = ttk.Button(bar, text="Sayfayı aç", command=self.open_page,
                                   state="normal" if url else "disabled")
        self.open_btn.pack(side="left")
        self.seen_btn = ttk.Button(bar, text="Gördüm", command=self.seen)
        self.seen_btn.pack(side="right")
        self.protocol("WM_DELETE_WINDOW", self.seen)

    def open_page(self) -> None:
        if self.url:
            self.app.open_url(self.url)

    def seen(self) -> None:
        self.app.popup_closed(self)
        self.destroy()


class TargetEditor(tk.Toplevel):
    def __init__(self, app, slug: str, target):
        super().__init__(app.root)
        self.app, self.slug, self.target = app, slug, target
        self.saving = False
        self.bind("<Destroy>", self._destroyed, add="+")
        row = app.rows_by_slug.get(slug)
        self.title(f"Hedef düzenle — {row.title if row else slug} / {target.name}")
        self.transient(app.root)
        rule = target.rule
        pad = {"padx": 8, "pady": 3}
        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=8, pady=8)

        top = ttk.Frame(body)
        top.pack(fill="x", **pad)
        ttk.Label(top, text="Kişi sayısı:").pack(side="left")
        self.people = tk.StringVar(value=str(target.people))
        self.people_spin = ttk.Spinbox(top, from_=1, to=4, width=4, textvariable=self.people)
        self.people_spin.pack(side="left", padx=6)

        days = ttk.LabelFrame(body, text="Günler")
        days.pack(fill="x", **pad)
        self.day_vars = [tk.BooleanVar(value=v) for v in L.days_to_flags(rule.days)]
        for i, var in enumerate(self.day_vars):
            ttk.Checkbutton(days, text=DAY_SHORT[i], variable=var).grid(row=0, column=i, padx=4)
        quick = ttk.Frame(days)
        quick.grid(row=1, column=0, columnspan=7, sticky="w", pady=4)
        for label, chosen in L.QUICK_DAYS.items():
            ttk.Button(quick, text=label, command=lambda c=chosen: self.set_days(c)).pack(
                side="left", padx=3)

        hours = ttk.LabelFrame(body, text="Saat (boş = sınırsız, SS:DD)")
        hours.pack(fill="x", **pad)
        self.from_var = tk.StringVar(value=rule.from_time or "")
        self.to_var = tk.StringVar(value=rule.to_time or "")
        ttk.Label(hours, text="Şu saatten sonra:").grid(row=0, column=0, sticky="w", padx=4)
        ttk.Entry(hours, width=8, textvariable=self.from_var).grid(row=0, column=1)
        ttk.Label(hours, text="Şu saate kadar:").grid(row=0, column=2, sticky="w", padx=(14, 4))
        ttk.Entry(hours, width=8, textvariable=self.to_var).grid(row=0, column=3)

        over = ttk.LabelFrame(body, text="Gün bazlı özel saat (isteğe bağlı)")
        over.pack(fill="x", **pad)
        self.over_vars: dict[int, tuple[tk.StringVar, tk.StringVar]] = {}
        for wd in range(7):
            cur = rule.overrides.get(wd) or {}
            a, b = tk.StringVar(value=cur.get("from") or ""), tk.StringVar(
                value=cur.get("to") or "")
            self.over_vars[wd] = (a, b)
            ttk.Label(over, text=DAY_SHORT[wd], width=5).grid(row=wd, column=0, sticky="w")
            ttk.Entry(over, width=8, textvariable=a).grid(row=wd, column=1, padx=2)
            ttk.Label(over, text="–").grid(row=wd, column=2)
            ttk.Entry(over, width=8, textvariable=b).grid(row=wd, column=3, padx=2)

        excl = ttk.LabelFrame(body, text="Hariç tutulan tarihler (işaretli = hariç)")
        excl.pack(fill="both", expand=True, **pad)
        self.dates = L.editor_dates(app.entry(slug), rule.exclude_dates, app.now())
        self.excl_vars: dict[str, tk.BooleanVar] = {}
        canvas = tk.Canvas(excl, height=110, highlightthickness=0)
        scroll = AutoScrollbar(excl, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)
        inner.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        if not self.dates:
            ttk.Label(inner, text="(seans tarihi yok)").pack(anchor="w")
        for iso in self.dates:
            var = tk.BooleanVar(value=iso in rule.exclude_dates)
            self.excl_vars[iso] = var
            ttk.Checkbutton(inner, text=L.date_label(iso), variable=var).pack(anchor="w")

        self.error = tk.StringVar()
        ttk.Label(body, textvariable=self.error, foreground=L.RED, wraplength=420).pack(
            fill="x", **pad)
        bar = ttk.Frame(body)
        bar.pack(fill="x", **pad)
        self.save_btn = ttk.Button(bar, text="Kaydet", command=self.save)
        self.save_btn.pack(side="right")
        ttk.Button(bar, text="İptal", command=self.destroy).pack(side="right", padx=6)
        try:
            self.grab_set()
        except tk.TclError:  # window not viewable yet (headless)
            pass

    def _destroyed(self, event) -> None:
        if event.widget is self and not self.app.closing:
            self.app.refresh_rows()  # cancelled or saved: list/detail show the real config

    def set_days(self, chosen) -> None:
        for i, var in enumerate(self.day_vars):
            var.set(i in chosen)

    def collect(self) -> dict:
        try:
            people = int(self.people.get())
        except ValueError:
            raise ValueError("kişi sayısı bir sayı olmalı (1-4)") from None
        return dict(
            people=people, days=L.flags_to_days([v.get() for v in self.day_vars]),
            from_text=self.from_var.get(), to_text=self.to_var.get(),
            overrides={wd: (a.get(), b.get()) for wd, (a, b) in self.over_vars.items()},
            excluded={d for d, v in self.excl_vars.items() if v.get()}, dates=self.dates)

    def save(self) -> None:
        """Validate everything first (nothing is written on error), then apply as one batch."""
        self.error.set("")
        service = self.app.service
        try:
            if service.config is None:
                raise ValueError("ayarlar henüz okunamadı, biraz sonra tekrar deneyin")
            plan = L.build_edit_plan(self.slug, self.target.id, **self.collect())
            L.dry_run(plan, config_to_dict(service.config))
        except ValueError as exc:
            self.error.set(str(exc))
            return
        except Exception as exc:
            log.exception("editor validation failed")
            self.error.set(f"Beklenmeyen hata: {type(exc).__name__}: {exc}")
            return
        if self.saving:
            return
        self.saving = True
        self.save_btn.configure(state="disabled", text="bekleniyor…")
        self.error.set("")
        self.app.run_bg(lambda: L.apply_plan(service, plan), self._saved)

    def _saved(self, _result, exc) -> None:
        self.saving = False
        self.app.refresh_rows()  # whatever happened, part of the batch may be written
        if not self.winfo_exists():
            return
        if exc is not None:
            log.warning("target edit failed: %s", exc)
            self.save_btn.configure(state="normal", text="Kaydet")
            self.error.set(f"Kaydedilemedi: {exc}")
            return
        self.destroy()


class App:
    def __init__(self, root: tk.Tk, service, local_dir: Path | str = DEFAULT_LOCAL_DIR, *,
                 open_url=webbrowser.open, beep=default_beep, msgbox=messagebox,
                 inline: bool = False, reveal=default_reveal, win=winsetup):
        self.root, self.service = root, service
        self.win = win   # Windows integration (autostart, keep awake); injectable for tests
        self._awake_on = False
        self.local_dir = Path(local_dir)
        self.open_url, self.beep, self.mb = open_url, beep, msgbox
        self.reveal = reveal
        self.activity = getattr(service, "activity", None) \
            or A.ActivityLog(self.local_dir / "activity.jsonl")
        self.inline = inline  # tests: run background calls synchronously
        self.q: queue.Queue = queue.Queue()
        self.worker: ScanWorker | None = None
        self.busy = 0
        self.rows: list = []
        self.rows_by_slug: dict = {}
        self.selected: str | None = None
        self._detail_sig = None
        self.popups: list[Popup] = []
        self.popup_wait: deque = deque()
        self.beep_count = 0
        self._beep_after = None
        self._pending: set = set()
        self.last_tick_errors: list[str] = []
        self.worker_error = ""
        self.closing = False

        ui = L.load_ui(self.local_dir / "ui.json")
        self.beep_enabled = ui["beep"]
        self.keep_awake = ui["keep_awake"]
        self.sort = L.load_sort(self.local_dir / "ui.json")  # (column, "asc"|"desc") or None
        self._default_rows: list = []
        self.bold_font = tkfont.nametofont("TkDefaultFont").copy()
        self.bold_font.configure(weight="bold")
        self.italic_font = tkfont.nametofont("TkDefaultFont").copy()
        self.italic_font.configure(slant="italic")
        base = tkfont.nametofont("TkDefaultFont").actual()["size"]
        bigger = (lambda n: base + n) if base > 0 else (lambda n: base - n)
        self.small_font = tkfont.nametofont("TkDefaultFont").copy()
        self.value_font = tkfont.nametofont("TkDefaultFont").copy()
        self.value_font.configure(weight="bold", size=bigger(1))
        self.icon_font = tkfont.nametofont("TkDefaultFont").copy()
        self.icon_font.configure(size=bigger(11))
        self.scale = max(1.0, float(root.winfo_fpixels("1i")) / 96.0)
        self.seen = L.SeenStore.load(self.local_dir / "ui.json")  # items the user has opened
        root.title(APP_TITLE)
        geom = L.sanitize_geometry(ui["geometry"], root.winfo_screenwidth(),
                                   root.winfo_screenheight())
        root.geometry(geom or L.default_geometry(root.winfo_screenwidth(),
                                                 root.winfo_screenheight()))
        root.minsize(*L.MIN_WINDOW)
        root.report_callback_exception = self._ui_exception
        self._build()
        root.protocol("WM_DELETE_WINDOW", self.close)
        service.on_notify(lambda t, x, evs: self.q.put(("notify", t, x, evs)))

    # ---- construction ----------------------------------------------------------------
    def _build(self) -> None:
        root = self.root
        menu = tk.Menu(root)
        settings = tk.Menu(menu, tearoff=False, postcommand=self._sync_autostart)
        self.beep_var = tk.BooleanVar(value=self.beep_enabled)
        settings.add_checkbutton(label="Sesli uyarı", variable=self.beep_var,
                                 command=self._beep_toggled)
        self.autostart_var = tk.BooleanVar(value=self._autostart_state())
        settings.add_checkbutton(label="Windows açılışında başlat",
                                 variable=self.autostart_var, command=self._autostart_toggled)
        self.keep_awake_var = tk.BooleanVar(value=self.keep_awake)
        settings.add_checkbutton(label="Bilgisayarın uyumasını engelle",
                                 variable=self.keep_awake_var, command=self._keep_awake_toggled)
        settings.add_separator()
        settings.add_command(label="Çıkış", command=self.close)
        menu.add_cascade(label="Ayarlar", menu=settings)
        root.configure(menu=menu)

        self._build_status()   # info strip right under the menu bar

        self.notebook = ttk.Notebook(root)
        self.notebook.pack(fill="both", expand=True, padx=8, pady=(self.px(6), 0))
        games = ttk.Frame(self.notebook)
        self.notebook.add(games, text="Oyunlar")
        self.activity_tab = ActivityTab(
            self.notebook, self.activity, A.load_filters(self.local_dir / "ui.json"),
            self.save_ui, self.mb, self.reveal)
        self.notebook.add(self.activity_tab, text="Etkinlik günlüğü")

        self.source_bar = tk.Frame(games)  # "Kaynaklar": one card per source (wave 8)
        self.source_cards: dict = {}       # source id -> widgets of its card
        self.source_labels: list = []      # body label of every card (lines of text)
        self.source_titles: list = []
        self.source_buttons: list = []     # "Portalı aç" links
        self.source_open_buttons: dict = {}   # source id -> "Kaynağı aç" link
        self._source_sig = None
        self._card_cols = 0
        self.source_bar.bind("<Configure>", lambda _e: self._layout_cards())
        self._build_legend(games)
        pane = ttk.PanedWindow(games, orient="vertical")
        self.pane = pane
        self._sash_user, self._fit_key, self._need_seen = False, None, 0
        pane.bind("<B1-Motion>", lambda _e: setattr(self, "_sash_user", True), add="+")
        pane.pack(fill="both", expand=True)
        top = ttk.Frame(pane)
        pane.add(top, weight=3)
        self.tree = GridTable(top, COLUMNS, stretch=("title", "sessions", "summary"),
                              on_sort=self.sort_by, on_cell=self._cell_click,
                              row_px=L.ROW_PX, head_px=L.HEADING_PX)
        for key, label, _width in COLUMNS:
            self.tree.heading(key, text=L.heading_text(label, key, self.sort))
        vsb = AutoScrollbar(top, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tree.tag_configure("new", bold=True, foreground=NEW_COLOR, column="title")
        self.tree.tag_configure("removed", background="#fde2e2", foreground="#7f1d1d")
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.root.bind_all("<MouseWheel>", self._detail_wheel, add="+")

        self.detail = ttk.LabelFrame(pane, text="Oyun ayrıntısı")
        pane.add(self.detail, weight=2)
        self._detail_placeholder()

    def px(self, n: int) -> int:
        return int(round(n * self.scale))

    def _build_status(self) -> None:
        """Info strip under the menu bar: small grey caption above a bold value, tinted
        background, wraps onto a second row when the window is narrow."""
        self.status = tk.Frame(self.root, background=STRIP_BG, highlightthickness=1,
                               highlightbackground=STRIP_EDGE)
        self.status.pack(fill="x", side="top")
        self.status.columnconfigure(0, weight=1)
        self.status_flow = Flow(self.status, gap_x=self.px(22), gap_y=self.px(2),
                                background=STRIP_BG)
        self.status_flow.grid(row=0, column=0, sticky="we", padx=self.px(10), pady=self.px(4))
        self.status_labels: dict[str, tk.Label] = {}
        cells = (("last", "Son tarama"), ("mode", "Mod"), ("next", "Sonraki açılış"),
                 ("gh", "GitHub"), ("hb", "PC kaydı"), ("pending", "Bekleyen bildirim"))
        for key, title in cells:
            frame = tk.Frame(self.status_flow, background=STRIP_BG)
            tk.Label(frame, text=title, foreground=L.GRAY, background=STRIP_BG,
                     font=self.small_font, anchor="w").pack(anchor="w")
            lbl = tk.Label(frame, text="—", background=STRIP_BG, foreground=VALUE_COLOR,
                           font=self.value_font, anchor="w", justify="left",
                           wraplength=self.px(520))
            lbl.pack(anchor="w")
            self.status_labels[key] = lbl
            self.status_flow.add(frame)
        self.paused_badge = tk.Label(self.status, text="DURAKLATILDI", background=L.RED,
                                     foreground="white", padx=8, pady=2, font=self.value_font)
        # Actions at the far right of the strip (the old buttons bar row is gone).
        self.status.columnconfigure(2, weight=0)
        self.actions = tk.Frame(self.status, background=STRIP_BG)
        self.actions.grid(row=0, column=2, sticky="e", padx=(0, self.px(10)), pady=self.px(4))
        self.progress = ttk.Progressbar(self.actions, mode="indeterminate", length=self.px(70))
        self.refresh_btn = ttk.Button(self.actions, text="Listeyi güncelle",
                                      command=self.refresh_list)
        self.refresh_btn.pack(side="left")
        self.pause_btn = ttk.Button(self.actions, text="Duraklat", command=self.toggle_pause)
        self.pause_btn.pack(side="left", padx=(self.px(6), 0))
        self.msg_var = tk.StringVar()
        self.msg_label = tk.Label(self.status, textvariable=self.msg_var, background=STRIP_BG,
                                  foreground=VALUE_COLOR, anchor="w", justify="left",
                                  wraplength=self.px(900))
        self.msg_label.grid(row=3, column=0, columnspan=3, sticky="w", padx=self.px(10))
        self.msg_label.grid_remove()    # shown only while it has text
        self.warn_label = tk.Label(self.status, text="", foreground=L.AMBER, background=STRIP_BG,
                                   anchor="w")
        self.warn_label.grid(row=1, column=0, sticky="w", padx=self.px(10))
        self.err_label = tk.Label(self.status, text="", foreground=L.RED, cursor="hand2",
                                  background=STRIP_BG, anchor="w")
        self.err_label.grid(row=2, column=0, sticky="w", padx=self.px(10))
        self.warn_label.grid_remove()   # shown only while they have text
        self.err_label.grid_remove()
        self._err_text = ""
        Tooltip(self.err_label, lambda: self._err_text)
        self.err_label.bind("<Button-1>", lambda _e: self._err_text and self.mb.showinfo(
            APP_TITLE, self._err_text))

    def _build_legend(self, parent) -> None:
        """"Açıklama" box: what normal / italic / purple-bold list text and the source status
        icons mean, each with a real sample. Sits right above the list header."""
        self.legend = tk.Frame(parent, background=LEGEND_BG, highlightthickness=1,
                               highlightbackground=LEGEND_EDGE)
        self.legend.pack(side="top", fill="x", pady=(0, self.px(4)))
        tk.Label(self.legend, text="Açıklama", font=self.bold_font, background=LEGEND_BG,
                 foreground=VALUE_COLOR).grid(row=0, column=0, sticky="nw", padx=self.px(8),
                                              pady=self.px(4))
        self.legend.columnconfigure(1, weight=1)
        flow = Flow(self.legend, gap_x=self.px(18), gap_y=self.px(1), background=LEGEND_BG)
        flow.grid(row=0, column=1, sticky="we", padx=(0, self.px(8)), pady=self.px(3))
        self.legend_flow = flow
        self.legend_texts: list[str] = []

        def item(sample, meaning, *, font=None, color=VALUE_COLOR):
            frame = tk.Frame(flow, background=LEGEND_BG)
            tk.Label(frame, text=sample, font=font, foreground=color,
                     background=LEGEND_BG).pack(side="left")
            tk.Label(frame, text=" " + meaning, foreground=L.GRAY,
                     background=LEGEND_BG).pack(side="left")
            self.legend_texts.append(f"{sample} {meaning}")
            flow.add(frame)

        styles = {"normal": (None, VALUE_COLOR), "derived": (self.italic_font, DERIVED_COLOR),
                  "new": (self.bold_font, NEW_COLOR)}
        for sample, meaning, style in L.LEGEND_LIST:
            font, color = styles[style]
            item(sample, meaning, font=font, color=color)
        for icon, meaning, color in L.LEGEND_STATES:
            item(icon, meaning, color=color)
        note = tk.Label(flow, text=L.LEGEND_TIMES, foreground=L.GRAY, background=LEGEND_BG)
        self.legend_texts.append(L.LEGEND_TIMES)
        flow.add(note)

    # ---- helpers ---------------------------------------------------------------------
    def now(self) -> datetime:
        return datetime.now(ISTANBUL)

    def entry(self, slug: str) -> dict:
        state = self.service.state or {}
        entry = (state.get("plays") or {}).get(slug)
        return entry if isinstance(entry, dict) else {}

    def run_bg(self, fn, on_done=None) -> None:
        """Run ``fn`` off the UI thread; ``on_done(result, exc)`` runs on the UI thread."""
        def work():
            try:
                self.q.put(("call", on_done, fn(), None))
            except Exception as exc:
                log.exception("background call failed")
                self.q.put(("call", on_done, None, exc))
        if self.inline:
            work()
        else:
            threading.Thread(target=work, name="bg-call", daemon=True).start()

    def set_message(self, text: str, error: bool = False) -> None:
        self.msg_var.set(text)
        self.msg_label.configure(foreground=L.RED if error else VALUE_COLOR)
        if text:
            self.msg_label.grid()
        else:
            self.msg_label.grid_remove()

    # ---- queue pump -------------------------------------------------------------------
    def pump(self) -> None:
        while True:
            try:
                item = self.q.get_nowait()
            except queue.Empty:
                return
            try:
                self._handle(item)
            except Exception:
                log.exception("UI handler failed")

    def _handle(self, item) -> None:
        kind = item[0]
        if kind == "tick":
            self._on_tick(item[1])
        elif kind == "worker_error":
            self.worker_error = f"Tarama hatası: {item[1]}"
            self._set_errors([self.worker_error])
        elif kind == "notify":
            self.show_popup(item[1], item[2], item[3])
        elif kind == "call":
            _k, done, result, exc = item
            if done is not None:
                done(result, exc)

    def _poll(self) -> None:
        if self.closing:
            return
        self.pump()
        self.root.after(POLL_MS, self._poll)

    def _on_tick(self, res) -> None:
        self.last_tick_errors = list(res.errors)
        self.worker_error = ""
        if res.errors:
            log.warning("tick errors: %s", "; ".join(res.errors))
        self.refresh_rows()
        self.update_status()
        self.activity_tab.poll()

    def _ui_exception(self, exc, val, tb) -> None:
        log.error("UI exception", exc_info=(exc, val, tb))
        self._set_errors([f"Arayüz hatası: {exc.__name__}: {val}"])

    # ---- rows / detail --------------------------------------------------------------------
    def refresh_rows(self) -> None:
        try:
            rows = self.service.rows()
        except Exception:
            log.exception("rows() failed")
            return
        self._apply_seen(rows)
        self._default_rows = rows
        self._render_rows()
        self._render_sources()
        self._fit_panes()

    def _apply_seen(self, rows) -> None:
        """Mark items the user has not opened yet (``is_new``). The first non-empty list of
        a service that already holds real data is the baseline: everything in it counts as
        seen, so the upgrade does not paint the whole list."""
        state = getattr(self.service, "state", None)
        ready = bool(isinstance(state, dict) and (state.get("plays") or state.get("source_events")))
        if ready and self.seen.baseline([r.slug for r in rows]):
            self.save_ui()
        for r in rows:
            r.is_new = self.seen.is_new(r.slug) and not r.is_removed

    def _fit_panes(self) -> None:
        """Divider position: the list shows its rows, the detail pane gets the height its
        content needs (so list, detail, session table, targets and Özet are visible
        without scrolling when the window is big enough). Once per change of row count,
        window height or detail content; never after the user dragged the divider."""
        if self._sash_user or self.closing:
            return
        try:
            pane_px = self.pane.winfo_height()
        except tk.TclError:  # window already destroyed
            return
        if pane_px < 100:  # not laid out yet (first call): retry when idle
            self.root.after(150, self._fit_panes)
            return
        need = None
        body = getattr(self, "detail_body", None)
        if body is not None and body.winfo_exists():
            self.root.update_idletasks()
            need = body.winfo_reqheight() + DETAIL_CHROME_PX
        # the divider must not jump around while the user clicks through rows: keep the
        # largest detail height seen at this window size
        if self._fit_key is None or self._fit_key[1] != pane_px:
            self._need_seen = 0
        if need is not None:
            self._need_seen = need = max(self._need_seen, need)
        key = (len(self.rows), pane_px, need)
        if key == self._fit_key:
            return
        self._fit_key = key
        try:
            self.pane.sashpos(0, L.list_pane_px(len(self.rows), pane_px,
                                                need or self._placeholder_px(pane_px),
                                                row_px=self.tree.row_px,
                                                head_px=self.tree.head_px))
        except tk.TclError:
            pass

    def _placeholder_px(self, pane_px: int) -> int:
        """Detail height with no row selected: fixed (scaled), never above 40 % of the pane."""
        return min(self.px(L.DETAIL_PLACEHOLDER_PX), int(pane_px * 0.4))

    def _refit(self) -> None:
        """Detail content changed size: fit the panes again when idle."""
        if not self.closing:
            self.root.after_idle(self._fit_panes)

    def _render_sources(self) -> None:
        """"Kaynaklar": three side-by-side cards (Biletinial, Başiskele, VKV), each with a
        big status icon in the status colour as accent, last check / last update / item
        count (Başiskele also the reservation portal) and "Kaynağı aç" (+ "Portalı aç")."""
        fn = getattr(self.service, "sources", None)
        pfn = getattr(self.service, "portals", None)
        try:
            views = fn() if fn is not None else []
            portals = pfn() if pfn is not None else []
            links = L.source_links(self.service.config, self.service.state)
        except Exception:
            log.exception("sources() failed")
            return
        views = L.source_views_with_portals(views, portals)
        now = self.now()
        cards = [L.source_card(v, now) for v in views]
        sig = tuple((v["id"], v.get("portal_url"), links.get(v["id"])) for v in views)
        if sig != self._source_sig:        # set of cards / links changed: rebuild
            self._source_sig = sig
            for child in self.source_bar.winfo_children():
                child.destroy()
            self.source_cards, self.source_labels, self.source_titles = {}, [], []
            self.source_buttons, self.source_open_buttons = [], {}
            self._card_cols = 0
            if not views:
                self.source_bar.pack_forget()
                return
            for v in views:
                self._build_card(v, links.get(v["id"]))
            self.source_bar.pack(fill="x", pady=(0, self.px(4)), before=self.legend)
            self._layout_cards()
        for card in cards:                 # same cards: only update texts and colours
            w = self.source_cards.get(card.id)
            if w is None:
                continue
            w["frame"].configure(highlightbackground=card.color, highlightcolor=card.color)
            w["stripe"].configure(background=card.color)
            w["icon"].configure(text=card.icon, foreground=card.color)
            w["title"].configure(text=card.title)
            w["body"].configure(text="\n".join(card.lines))
            w["error"].configure(text=f"Hata: {card.error}" if card.error else "")
            if card.error:
                w["error"].grid()
            else:
                w["error"].grid_remove()

    def _build_card(self, view: dict, link: str | None) -> None:
        wrap = self.px(CARD_MIN_PX) - self.px(90)
        frame = tk.Frame(self.source_bar, background=CARD_BG, highlightthickness=2,
                         highlightbackground=L.GRAY, highlightcolor=L.GRAY)
        stripe = tk.Frame(frame, background=L.GRAY, width=self.px(6))
        stripe.grid(row=0, column=0, rowspan=3, sticky="ns")
        icon = tk.Label(frame, text="", font=self.icon_font, background=CARD_BG,
                        foreground=L.GRAY)
        icon.grid(row=0, column=1, rowspan=2, sticky="n", padx=(self.px(10), self.px(6)),
                  pady=self.px(6))
        title = tk.Label(frame, text="", font=self.value_font, background=CARD_BG,
                         foreground=VALUE_COLOR, anchor="w")
        title.grid(row=0, column=2, sticky="we", pady=(self.px(6), 0), padx=(0, self.px(8)))
        # "Kaynağı aç" / "Portalı aç" sit on the title row, right-aligned beside the name.
        links = tk.Frame(frame, background=CARD_BG)
        links.grid(row=0, column=3, sticky="ne", padx=(0, self.px(8)), pady=(self.px(6), 0))
        body = tk.Label(frame, text="", background=CARD_BG, foreground=VALUE_COLOR,
                        anchor="nw", justify="left", wraplength=wrap)
        body.grid(row=1, column=2, columnspan=2, sticky="we", padx=(0, self.px(8)),
                  pady=(0, self.px(6)))
        error = tk.Label(frame, text="", background=CARD_BG, foreground=L.RED, anchor="w",
                         justify="left", wraplength=wrap)
        error.grid(row=2, column=2, columnspan=2, sticky="we", padx=(0, self.px(8)),
                   pady=(0, self.px(6)))
        error.grid_remove()
        frame.columnconfigure(2, weight=1)

        def rewrap(event, labels=(body, error)):   # text wraps to the card, never clips
            room = max(self.px(120), event.width - self.px(90))
            for lab in labels:
                if int(lab.cget("wraplength")) != room:
                    lab.configure(wraplength=room)
        frame.bind("<Configure>", rewrap)
        sid = view["id"]
        if link:
            button = LinkButton(links, "Kaynağı aç", lambda u=link: self.open_url(u),
                                background=CARD_BG)
            button.pack(side="left", padx=(0, self.px(10)))
            self.source_open_buttons[sid] = button
        if view.get("portal_url"):
            button = LinkButton(links, "Portalı aç",
                                lambda u=view["portal_url"]: self.open_url(u),
                                background=CARD_BG)
            button.pack(side="left")
            self.source_buttons.append(button)
        self.source_cards[sid] = {"frame": frame, "stripe": stripe, "icon": icon,
                                  "title": title, "body": body, "error": error,
                                  "links": links}
        self.source_labels.append(body)
        self.source_titles.append(title)

    def _layout_cards(self) -> None:
        """Cards side by side (one equal-width column each, up to 3); fewer columns, so
        the cards wrap onto further rows, when the window is narrow."""
        frames = [self.source_cards[k]["frame"] for k in self.source_cards]
        if not frames:
            return
        width = self.source_bar.winfo_width()
        cols = 3 if width <= 1 else max(1, min(len(frames), 3, width // self.px(CARD_MIN_PX)))
        if cols == self._card_cols:
            return
        self._card_cols = cols
        for c in range(3):
            self.source_bar.columnconfigure(c, weight=1 if c < cols else 0,
                                            uniform="card" if c < cols else "")
        for n, frame in enumerate(frames):
            frame.grid(row=n // cols, column=n % cols, sticky="nsew",
                       padx=(0 if n % cols == 0 else self.px(6), 0),
                       pady=(0 if n < cols else self.px(6), 0))

    def sort_by(self, column: str) -> None:
        """Header click: ascending -> descending -> default order (see ``L.next_sort``)."""
        self.sort = L.next_sort(self.sort, column)
        self.save_ui()
        self._render_rows()

    def _render_rows(self) -> None:
        rows = L.sort_rows(self._default_rows, self.sort)
        for key, label, _w in COLUMNS:
            self.tree.heading(key, text=L.heading_text(label, key, self.sort))
        self.rows = rows
        self.rows_by_slug = {r.slug: r for r in rows}
        top = self.tree.yview()[0]
        self.tree.delete(*self.tree.get_children())
        for r in rows:
            self.tree.insert("", "end", iid=r.slug, cells=L.row_cells(r), tags=L.row_tags(r))
        if self.selected in self.rows_by_slug:
            self.tree.selection_set(self.selected)
        else:
            self.selected = None
        self.tree.yview_moveto(top)
        self.show_detail()

    def _cell_click(self, iid: str, key: str) -> None:
        """Click on a ☑/☐ cell (toggle following) or an "Oyun ↗" button (open the page)."""
        row = self.rows_by_slug.get(iid)
        if row is None:
            return
        if key == "link":
            if row.url:
                self.open_url(row.url)
            return
        if key != "follow":
            return
        fn = self.service.set_event_follow if row.is_event else self.service.set_active
        pend = ("ef" if row.is_event else "active", row.slug)
        if pend in self._pending:   # a save for this row is still running
            return
        new = not row.active
        row.active, row.watched = new, new and not row.is_removed   # shown at once; the save
        self._render_rows()             # below re-reads the real state when it is done
        self.edit(fn, row.slug, new, key=pend)

    def select_slug(self, slug: str) -> None:
        self.tree.selection_set(slug)
        self.tree.see(slug)
        self._on_select()

    def _on_select(self, _e=None) -> None:
        sel = self.tree.selection()
        new = sel[0] if sel else None
        if new == self.selected and self._detail_sig is not None:
            return  # reselect after a list rebuild: show_detail decides by signature
        self.selected = new
        if new is not None and self.seen.mark(new):   # opened once: no longer "new"
            row = self.rows_by_slug.get(new)
            if row is not None:
                row.is_new = False
                self.tree.set_tags(new, L.row_tags(row))
            self.save_ui()
        self.show_detail(force=True)

    def _detail_placeholder(self) -> None:
        for child in self.detail.winfo_children():
            child.destroy()
        self.detail_body = None
        ttk.Label(self.detail, text="Bir oyun seçin.").pack(padx=10, pady=10, anchor="w")

    def show_detail(self, force: bool = False) -> None:
        row = self.rows_by_slug.get(self.selected)
        if row is None:
            self._detail_sig = None
            self._detail_placeholder()
            return
        if row.is_event:
            self._show_event_detail(row, force)
            return
        sessions = L.session_lines(self.entry(row.slug), self.now())
        sig = (L.row_values(row), tuple(row.target_lines), row.active,
               tuple((s.when, s.status, s.opens, s.url) for s in sessions), row.url,
               row.summary, tuple(row.cast), tuple(row.credits))
        if sig == self._detail_sig and not force:
            return
        self._detail_sig = sig
        body = self._detail_body()

        head = ttk.Frame(body)
        head.pack(fill="x", padx=8, pady=4)
        ttk.Label(head, text=row.title, font=self.bold_font).pack(side="left")
        self.open_btn = ttk.Button(head, text="Sayfayı aç",
                                   command=lambda: row.url and self.open_url(row.url),
                                   state="normal" if row.url else "disabled")
        self.open_btn.pack(side="right")
        self.meta_label = RichLabel(body, self.italic_font, DERIVED_COLOR)
        self.meta_label.set_parts(L.meta_parts(row))
        self.meta_label.pack(padx=8, anchor="w")

        acts = ttk.Frame(body)
        acts.pack(fill="x", padx=8)
        self.active_var = tk.BooleanVar(value=row.active)
        self.active_chk = ttk.Checkbutton(
            acts, text="Aktif (takip ediliyor)", variable=self.active_var,
            command=lambda: self.edit(self.service.set_active, row.slug,
                                      self.active_var.get(), key=("active", row.slug),
                                      widget=self.active_chk))
        self.active_chk.pack(side="left")
        cfg_watch = self.service.config.plays.get(row.slug) if self.service.config else None
        have = {t.id for t in (cfg_watch.targets if cfg_watch else [])}
        self.add_buttons = {}
        for kind, label in (("y", "Yetişkin hedefi ekle"), ("c", "Çocuk hedefi ekle")):
            btn = ttk.Button(acts, text=label, state="disabled" if kind in have else "normal")
            btn.configure(command=lambda k=kind, b=btn: self.edit(
                self.service.ensure_target, row.slug, k, key=("add", row.slug, k), widget=b))
            btn.pack(side="left", padx=6)
            self.add_buttons[kind] = btn

        cols = ttk.Frame(body)
        cols.pack(fill="both", expand=True, padx=8, pady=6)
        left = ttk.Frame(cols)
        left.pack(side="left", fill="both", expand=True)
        self._session_links: dict[str, str] = {}
        self.session_tree = GridTable(
            left, (("when", "Seans", 160), ("status", "Durum", 100),
                   ("opens", "Satış açılışı", 170), ("link", "Bağlantı", 96)),
            on_cell=self._session_click, sortable=False, row_px=L.ROW_PX, head_px=L.HEADING_PX,
            height_rows=min(max(len(sessions), 2), 8))
        missing = False
        for n, s in enumerate(sessions):
            text, url, note = L.session_link(s, row.url)
            missing = missing or bool(note)
            self._session_links[str(n)] = url
            self.session_tree.insert("", "end", iid=str(n), cells=[
                L.Cell(s.when), L.Cell(f"{s.icon} {s.status}"), L.Cell(s.opens),
                L.Cell(text or L.BLANK, False, "link" if url else "text")])
        if not sessions:
            self.session_tree.insert("", "end", iid="none", cells=[
                L.Cell("yaklaşan seans yok"), L.Cell(""), L.Cell(""), L.Cell("")])
        self.session_tree.pack(side="top", fill="both", expand=True)
        self.session_note = ttk.Label(left, text="Seans bağlantısı olmayan satırda düğme oyun "
                                      "sayfasını açar (\"Oyun ↗\")." if missing else "",
                                      foreground=L.GRAY)
        if missing:
            self.session_note.pack(side="top", anchor="w")
        sess_sb = AutoScrollbar(cols, orient="vertical", command=self.session_tree.yview)
        self.session_tree.configure(yscrollcommand=sess_sb.set)
        sess_sb.pack(side="left", fill="y")

        targets = ttk.LabelFrame(cols, text="Hedefler")
        targets.pack(side="left", fill="both", expand=True, padx=(8, 0))
        self.target_buttons = {}
        watch = self.service.config.plays.get(row.slug) if self.service.config else None
        if watch is None or not watch.targets:
            implicit = watch is not None and watch.active
            text = L.NO_TARGET_TEXT if implicit else "Hedef yok."
            ttk.Label(targets, text=text, **(   # "tüm seanslar" is our default, not data
                {"font": self.italic_font, "foreground": DERIVED_COLOR} if implicit else {})
                      ).pack(anchor="w", padx=6, pady=4)
        for t in (watch.targets if watch else []):
            line = ttk.Frame(targets)
            line.pack(fill="x", padx=6, pady=2)
            ttk.Label(line, text=L.target_summary_text(t), wraplength=360,
                      justify="left").pack(side="left", fill="x", expand=True)
            delete = ttk.Button(line, text="Sil", width=5)
            delete.configure(command=lambda tid=t.id, nm=t.name, b=delete:
                             self.delete_target(row.slug, tid, nm, b))
            delete.pack(side="right")
            edit = ttk.Button(line, text="Düzenle", width=8,
                              command=lambda tt=t: self.open_editor(row.slug, tt.id))
            edit.pack(side="right", padx=4)
            self.target_buttons[t.id] = edit

        self._build_info(body, row)
        self._refit()

    def _session_click(self, iid: str, key: str) -> None:
        url = self._session_links.get(iid)
        if key == "link" and url:
            self.open_url(url)

    def _show_event_detail(self, row, force: bool) -> None:
        """Detail of a Başiskele / VKV event: source, event date, reservation opening,
        contact block (VKV), follow switch, page link."""
        sig = (L.row_values(row), row.seans, row.reservation_opens, row.venue,
               tuple(row.contact), row.url, row.active)
        if sig == self._detail_sig and not force:
            return
        self._detail_sig = sig
        body = self._detail_body()
        head = ttk.Frame(body)
        head.pack(fill="x", padx=8, pady=4)
        ttk.Label(head, text=row.title, font=self.bold_font).pack(side="left")
        self.open_btn = ttk.Button(head, text="Sayfayı aç",
                                   command=lambda: row.url and self.open_url(row.url),
                                   state="normal" if row.url else "disabled")
        self.open_btn.pack(side="right")
        self.meta_label = RichLabel(body, self.italic_font, DERIVED_COLOR)
        self.meta_label.set_parts(L.meta_parts(row))
        self.meta_label.pack(padx=8, anchor="w")
        acts = ttk.Frame(body)
        acts.pack(fill="x", padx=8, pady=(2, 0))
        self.active_var = tk.BooleanVar(value=row.active)
        self.active_chk = ttk.Checkbutton(
            acts, text="Aktif (takip ediliyor)", variable=self.active_var,
            command=lambda: self.edit(self.service.set_event_follow, row.slug,
                                      self.active_var.get(), key=("ef", row.slug),
                                      widget=self.active_chk))
        self.active_chk.pack(side="left")
        self.add_buttons, self.target_buttons = {}, {}
        self.follow_help = ttk.Label(body, text=FOLLOW_HELP_EVENT, foreground=L.GRAY,
                                     wraplength=700, justify="left")
        self.follow_help.pack(fill="x", padx=8, anchor="w")
        links = ttk.Frame(body)       # no per-session pages at these sources: one page button
        links.pack(fill="x", padx=8, pady=(4, 0))
        self.event_page_btn = ttk.Button(links, text="Etkinlik sayfası ↗",
                                         command=lambda: row.url and self.open_url(row.url),
                                         state="normal" if row.url else "disabled")
        self.event_page_btn.pack(side="left")
        ttk.Label(links, text=NO_SESSION_PAGE, foreground=L.GRAY).pack(side="left", padx=8)
        grid = ttk.Frame(body)
        grid.pack(fill="x", padx=8, pady=6)
        der = row.derived
        facts = [("Kaynak", row.source, False),
                 ("Etkinlik tarihi", row.seans or "Bilgi yok", not row.seans),
                 ("Rezervasyon açılışı", row.reservation_opens or "Bilgi yok",
                  not row.reservation_opens),
                 ("Durum", row.status_summary or "Bilgi yok", "summary" in der),
                 ("Mekan", row.venue or "Bilgi yok", not row.venue)]
        facts += [("İletişim" if i == 0 else "", line, False)
                  for i, line in enumerate(row.contact)]
        if row.contact and row.url:
            facts.append(("Etkinlik sayfası", row.url, False))
        self.event_fact_labels = []
        for i, (name, value, derived) in enumerate(facts):
            ttk.Label(grid, text=name, font=self.bold_font).grid(row=i, column=0, sticky="nw",
                                                                 padx=(0, 12), pady=1)
            extra = {"font": self.italic_font, "foreground": DERIVED_COLOR} if derived else {}
            lbl = ttk.Label(grid, text=value, wraplength=700, justify="left", **extra)
            lbl.grid(row=i, column=1, sticky="w", pady=1)
            self.event_fact_labels.append(lbl)
        self._refit()

    def _detail_body(self) -> ttk.Frame:
        """Empty, scrollable content frame of the detail pane (small windows stay usable)."""
        for child in self.detail.winfo_children():
            child.destroy()
        canvas = tk.Canvas(self.detail, highlightthickness=0, borderwidth=0)
        vsb = AutoScrollbar(self.detail, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        body = ttk.Frame(canvas)
        window = canvas.create_window((0, 0), window=body, anchor="nw")
        body.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(window, width=e.width))
        self.detail_canvas, self.detail_vsb, self.detail_body = canvas, vsb, body
        return body

    def _detail_wheel(self, event) -> None:
        """Mouse wheel over the detail pane scrolls it (not over its own scrolling widgets)."""
        widget = event.widget
        try:
            if widget.winfo_class() in ("Text", "Treeview", "Listbox"):
                return
            node = widget
            while node is not None and node is not self.detail:
                node = node.master
        except (AttributeError, tk.TclError, KeyError):
            return
        canvas = getattr(self, "detail_canvas", None)
        if node is self.detail and canvas is not None and canvas.winfo_exists():
            canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

    def _build_info(self, body, row) -> None:
        """Özet / Oyuncular / Yapım below the session list; "Bilgi yok" when unknown."""
        info = ttk.Frame(body)
        info.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Label(info, text="Özet", font=self.bold_font).pack(anchor="w")
        box = ttk.Frame(info)
        box.pack(fill="x")
        self.summary_text = tk.Text(box, height=1, wrap="word", borderwidth=1, relief="solid",
                                    font="TkDefaultFont")
        sb = AutoScrollbar(box, orient="vertical", command=self.summary_text.yview)
        self.summary_text.configure(yscrollcommand=sb.set)
        self.summary_text.insert("1.0", row.summary or NO_INFO)
        self.summary_text.configure(state="disabled")
        self.summary_text.pack(side="left", fill="x", expand=True)
        sb.pack(side="right", fill="y")
        # as tall as the text needs, up to ~40 % of the pane (then it scrolls)
        autosize_text(self.summary_text,
                      lambda: max(6, int(self.pane.winfo_height() * 0.4 / 17)),
                      on_change=self._refit)
        ttk.Label(info, text="Oyuncular", font=self.bold_font).pack(anchor="w", pady=(6, 0))
        self.cast_label = ttk.Label(info, text=", ".join(row.cast) or NO_INFO,
                                    wraplength=600, justify="left")
        self.cast_label.pack(anchor="w", fill="x")
        ttk.Label(info, text="Yapım", font=self.bold_font).pack(anchor="w", pady=(6, 0))
        self.credits_label = ttk.Label(info, text="\n".join(credit_lines(row.credits)) or NO_INFO,
                                       wraplength=600, justify="left")
        self.credits_label.pack(anchor="w", fill="x")

        def wrap(event):
            for lbl in (self.cast_label, self.credits_label):
                lbl.configure(wraplength=max(200, event.width - 20))
        info.bind("<Configure>", wrap)


    # ---- config edits -----------------------------------------------------------------------
    def edit(self, fn, *args, key=None, widget=None) -> None:
        """Network-backed action: its button is disabled ("bekleniyor…") until it is
        done; a repeated click while pending is ignored."""
        key = key or (getattr(fn, "__name__", "edit"), args)
        if key in self._pending:
            return
        self._pending.add(key)
        old = None
        if widget is not None:
            old = widget.cget("text")
            widget.configure(state="disabled", text="bekleniyor…")

        def done(_r, exc):
            self._pending.discard(key)
            if widget is not None:
                try:
                    if widget.winfo_exists():
                        widget.configure(state="normal", text=old)
                except tk.TclError:
                    pass
            if exc is not None:
                self.mb.showerror(APP_TITLE, f"Kaydedilemedi: {exc}")
            self.refresh_rows()
            if exc is not None:
                self.show_detail(force=True)  # undo a toggled checkbox etc.
            self.update_status()
        self.run_bg(lambda: fn(*args), done)

    def open_editor(self, slug: str, tid: str):
        watch = self.service.config.plays.get(slug) if self.service.config else None
        target = next((t for t in (watch.targets if watch else []) if t.id == tid), None)
        if target is None:
            return None
        return TargetEditor(self, slug, target)

    def delete_target(self, slug: str, tid: str, name: str, widget=None) -> None:
        if ("del", slug, tid) in self._pending:
            return
        if self.mb.askyesno(APP_TITLE, f"'{name}' hedefi silinsin mi?"):
            self.edit(self.service.remove_target, slug, tid, key=("del", slug, tid),
                      widget=widget)

    def toggle_pause(self) -> None:
        paused = bool(self.service.config.paused) if self.service.config else False
        self.edit(self.service.resume if paused else self.service.pause, key="pause",
                  widget=self.pause_btn)

    # ---- refresh ("Listeyi güncelle") ---------------------------------------------------------
    def refresh_list(self) -> None:
        if self.busy:
            return
        self.busy += 1
        self.refresh_btn.configure(state="disabled", text="Güncelleniyor…")
        self.progress.pack(side="left", padx=(0, self.px(6)), before=self.refresh_btn)
        self.progress.start(12)
        self.set_message("Oyun listesi güncelleniyor…")
        self.run_bg(self.service.refresh_now, self._refresh_done)

    def _refresh_done(self, plays, exc) -> None:
        self.busy -= 1
        self.progress.stop()
        self.progress.pack_forget()
        self.refresh_btn.configure(state="normal", text="Listeyi güncelle")
        if exc is not None:
            text = (str(exc) if isinstance(exc, RefreshError)
                    else f"Liste güncellenemedi: {type(exc).__name__}: {exc}")
            self.set_message(f"{text} (önceki liste korunuyor)", error=True)
            self.mb.showwarning(APP_TITLE, f"{text}\n\nÖnceki liste korunuyor.")
        else:
            new, gone = len(self.service.new_slugs), len(self.service.removed_slugs)
            report = getattr(self.service, "sources_report", lambda: "")()
            self.set_message(f"Liste güncellendi · {len(plays)} oyun · {new} yeni · "
                             f"{gone} kalktı" + (f" · Kaynaklar: {report}" if report else ""))
        self.refresh_rows()
        self.update_status()
        if self.worker is not None:
            self.worker.wake()

    # ---- status -------------------------------------------------------------------------------
    def _set_errors(self, errors: list[str]) -> None:
        self._err_text = "\n".join(errors)
        shown = errors[0] if errors else ""
        more = f" (+{len(errors) - 1})" if len(errors) > 1 else ""
        self.err_label.configure(text=(shown[:80] + ("…" if len(shown) > 80 else "") + more)
                                 if shown else "")
        if shown:
            self.err_label.grid()
        else:
            self.err_label.grid_remove()

    def update_status(self) -> None:
        try:
            st = self.service.status()
        except Exception:
            log.exception("status() failed")
            return
        v = L.status_view(st, self.now(), self.service.refresh_failures)
        labels = self.status_labels
        labels["last"].configure(text=v.last_scan)
        labels["mode"].configure(text=v.mode)
        labels["next"].configure(text=v.next_opening)
        labels["gh"].configure(text=v.github, foreground=v.github_color)
        labels["hb"].configure(text=v.heartbeat, foreground=v.heartbeat_color)
        labels["pending"].configure(text=v.pending)
        self.warn_label.configure(text=v.warning)
        if v.warning:
            self.warn_label.grid()
        else:
            self.warn_label.grid_remove()
        if v.paused:
            self.paused_badge.grid(row=0, column=1, padx=8, sticky="e")
        else:
            self.paused_badge.grid_remove()
        self.pause_btn.configure(text="Devam" if v.paused else "Duraklat")
        self._set_errors(v.errors or self.last_tick_errors
                         or ([self.worker_error] if self.worker_error else []))

    def _status_timer(self) -> None:
        if self.closing:
            return
        self.update_status()
        self._render_sources()
        self.root.after(STATUS_MS, self._status_timer)

    def _activity_timer(self) -> None:
        if self.closing:
            return
        self.activity_tab.poll()
        self.root.after(ACTIVITY_MS, self._activity_timer)

    def _reload_timer(self) -> None:
        if self.closing:
            return

        def done(_r, exc):
            if exc is not None:
                log.warning("config reload failed: %s", exc)
            else:
                self.refresh_rows()
        self.run_bg(self.service.reload_config, done)
        self.root.after(RELOAD_MS, self._reload_timer)

    # ---- notifications --------------------------------------------------------------------------
    def show_popup(self, title: str, text: str, events) -> None:
        url = L.event_url(events, {r.slug: r.url for r in self.rows})
        self.popup_wait.append((title, text, url))
        self._drain_popups()

    def _drain_popups(self) -> None:
        while self.popup_wait and len(self.popups) < MAX_POPUPS:
            title, text, url = self.popup_wait.popleft()
            pop = Popup(self, title, text, url)
            self.popups.append(pop)
            self._place_popups()
            self.alert()

    def alert(self) -> None:
        """A new notification: restart the beep series (one shared timer for all popups)."""
        self.beep_count = 0
        if self._beep_after is None:
            self._beep_tick()

    def _beep_tick(self) -> None:
        self._beep_after = None
        if self.closing or not self.popups \
                or not L.should_beep(self.beep_count, self.beep_enabled, False):
            return
        self.beep_count += 1
        self.beep()
        self._beep_after = self.root.after(L.BEEP_INTERVAL_S * 1000, self._beep_tick)

    def _stop_beeps(self) -> None:
        if self._beep_after is not None:
            try:
                self.root.after_cancel(self._beep_after)
            except tk.TclError:
                pass
            self._beep_after = None

    def popup_closed(self, pop: Popup) -> None:
        if pop in self.popups:
            self.popups.remove(pop)
        if not self.popups and not self.popup_wait:
            self._stop_beeps()
        self._place_popups()
        self._drain_popups()

    def _place_popups(self) -> None:
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        y = sh - 70
        for pop in self.popups:
            pop.update_idletasks()
            w, h = pop.winfo_reqwidth(), pop.winfo_reqheight()
            y -= h + 10
            pop.geometry(f"+{max(0, sw - w - 16)}+{max(0, y)}")

    def _beep_toggled(self) -> None:
        self.beep_enabled = bool(self.beep_var.get())
        self.save_ui()

    # ---- Windows integration (Settings menu) --------------------------------------------------
    def _autostart_state(self) -> bool:
        try:
            return bool(self.win.autostart_enabled())
        except Exception:
            log.exception("autostart state unreadable")
            return False

    def _sync_autostart(self) -> None:
        """Menu is about to open: show what the registry says right now."""
        self.autostart_var.set(self._autostart_state())

    def _autostart_toggled(self) -> None:
        want = bool(self.autostart_var.get())
        try:
            actual = bool(self.win.set_autostart(want))
        except Exception as exc:
            log.exception("autostart not changed")
            actual, error = self._autostart_state(), f"{type(exc).__name__}: {exc}"
        else:
            error = "" if actual == want else "Windows kayıt defteri değeri beklenen durumda değil."
        self.autostart_var.set(actual)
        if error:
            self.mb.showerror(APP_TITLE, "Windows açılışında başlatma ayarı değiştirilemedi.\n\n"
                              + error)

    def _apply_keep_awake(self, on: bool) -> bool:
        ok = bool(self.win.set_keep_awake(on))
        self._awake_on = on and ok
        return ok

    def _keep_awake_toggled(self) -> None:
        want = bool(self.keep_awake_var.get())
        ok = self._apply_keep_awake(want)
        if want and not ok:
            self.keep_awake_var.set(False)
            self.mb.showerror(APP_TITLE, "Bilgisayarın uyumasını engelleme etkinleştirilemedi.")
            want = False
        self.keep_awake = want
        self.save_ui()

    # ---- lifecycle ----------------------------------------------------------------------------------
    def save_ui(self) -> None:
        try:
            geom = self.root.geometry() if self.root.state() in ("normal", "withdrawn") else None
            old = L.load_ui(self.local_dir / "ui.json")
            L.save_ui(self.local_dir / "ui.json",
                      {"geometry": geom if geom and "x" in geom else old["geometry"],
                       "geometry_v": L.GEOMETRY_VERSION,
                       "beep": self.beep_enabled,
                       "keep_awake": self.keep_awake,
                       "sort": list(self.sort) if self.sort else None,
                       "seen": self.seen.to_json(),
                       "activity": self.activity_tab.filters()})
        except Exception:
            log.exception("ui.json not saved")

    def start(self) -> None:
        """Start the scan worker and the timers (not used by tests)."""
        if self.keep_awake and not self._apply_keep_awake(True):
            log.warning("keep awake could not be enabled")
        self.refresh_rows()
        self.update_status()
        self._service_hook("log_startup")
        self.worker = ScanWorker(self.service, self.q.put)
        self.worker.start()
        self.root.after(POLL_MS, self._poll)
        self.root.after(STATUS_MS, self._status_timer)
        self.root.after(ACTIVITY_MS, self._activity_timer)
        self.root.after(RELOAD_MS, self._reload_timer)

    def _service_hook(self, name: str) -> None:
        hook = getattr(self.service, name, None)
        if hook is not None:
            try:
                hook()
            except Exception:
                log.exception("%s failed", name)

    def close(self, destroy: bool = True) -> None:
        """Window closed = app exits (no tray). The service writes its files atomically
        after every change, so there is nothing left to flush besides ui.json."""
        if self.closing:
            return
        self.closing = True
        self._stop_beeps()
        self.save_ui()
        if self._awake_on:   # give the power state back (also reset by Windows on exit)
            self._apply_keep_awake(False)
        for pop in list(self.popups):
            try:
                pop.destroy()
            except tk.TclError:
                pass
        if self.worker is not None:
            self.worker.stop()
            self.worker.join(timeout=2.0)
        self._service_hook("log_shutdown")
        if destroy:
            self.root.destroy()


def main() -> int:
    winsetup.enable_dpi_awareness()   # before the first Tk window (crisp text, real DPI)
    guard = L.SingleInstance()
    if not guard.acquire():           # another copy runs: show it instead of starting a second
        if not winsetup.focus_existing_window(APP_TITLE):
            winsetup.info_box(APP_TITLE, "Uygulama zaten çalışıyor.")
        return 0
    root = tk.Tk()
    root.withdraw()
    secrets: list[str] = []
    try:
        setup_logging(DEFAULT_LOCAL_DIR, secrets)
        try:
            settings = load_settings()
        except SettingsError as exc:
            log.error("settings: %s", exc)
            readme = Path(__file__).with_name("README.md")
            messagebox.showerror(APP_TITLE, f"Ayar hatası: {exc}\n\nAyrıntı: {readme}")
            return 2
        secrets.extend([settings.gh_token, settings.tg_token, settings.chat_id])

        from tiyatro.claims import ClaimStore
        from tiyatro.fetcher import fetch
        from tiyatro.notify import Telegram
        from tiyatro.store import Store
        from pc.service import Service

        def clock() -> datetime:
            return datetime.now(ISTANBUL)

        store = Store(settings.repo, settings.gh_token)
        service = Service(store, Telegram(settings.tg_token, settings.chat_id),
                          ClaimStore(store, "pc", clock), clock=clock,
                          local_dir=settings.local_dir, portal_fetch_fn=fetch,
                          activity=A.ActivityLog(Path(settings.local_dir) / "activity.jsonl",
                                                 secrets=secrets))
        app = App(root, service, settings.local_dir)
        root.deiconify()
        app.start()
        root.mainloop()
        return 0
    except Exception:
        log.exception("fatal")
        raise  # pc.app.run() writes crash.log and shows the message box
    finally:
        guard.release()

