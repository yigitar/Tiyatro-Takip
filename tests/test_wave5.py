"""Wave 5: one unified list (Biletinial + Başiskele + VKV), blank Kitle/Tür/Etiketler
filled by documented rules, portal merged into the source rows, sized Özet text."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from pc import app_logic as L
from pc.viewmodel import Row, _row, event_row, with_events
from tiyatro import parser
from tiyatro import srcevents as S
from tiyatro.config import default_config
from tiyatro.models import ISTANBUL
from tiyatro.tags import (derive_tags, derived_tags, genre_from_text, resolve_audience,
                          resolve_genre)

FX = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 11, 15, 12, 0, tzinfo=ISTANBUL)


def play_entry(name: str) -> dict:
    p = parser.parse_play((FX / name).read_text(encoding="utf-8"), "https://x.test/p")
    return {"title": p.title, "genre": p.genre, "age_limit": p.age_limit,
            "price_from": p.price_from, "tags": sorted(derive_tags(p)), "summary": p.summary,
            "sessions": {}}


# ---- blanks: why they were empty and how they are filled -------------------------------------

def test_row_without_age_and_child_marker_is_yetiskin():
    """"On İkinci Gece": genre Komedi, no age limit -> Kitle used to be blank."""
    e = play_entry("play_on_ikinci_gece.html")
    assert e["age_limit"] is None
    r = _row("p", e, default_config(), set(), set(), NOW)
    assert (r.audience, r.genre) == ("Yetişkin", "Komedi")
    assert r.tags                                                   # Etiketler not blank


def test_genre_that_is_only_the_audience_falls_back_to_description_then_tiyatro():
    """"Hiç Kimsenin Öyküsü": 'Yetişkin Oyunu' held only the audience -> Tür was blank."""
    e = play_entry("play_hic_kimsenin.html")
    assert e["genre"] == "Yetişkin Oyunu"
    r = _row("p", e, default_config(), set(), set(), NOW)
    assert r.audience == "Yetişkin" and r.genre == "Tiyatro"       # no genre word in the text
    assert r.tags == ["yetişkin"]                                    # no tag at all before
    e["summary"] = "Hüzünlü bir dram."
    assert _row("p", e, default_config(), set(), set(), NOW).genre == "Dram"


def test_child_play_keeps_cocuk_and_its_genre():
    r = _row("p", play_entry("play_kusursuz.html"), default_config(), set(), set(), NOW)
    assert (r.audience, r.genre) == ("Çocuk", "Fantastik") and "çocuk" in r.tags


def test_resolver_rules():
    assert resolve_audience("Komedi") == "Yetişkin"                 # no child marker
    assert resolve_audience("Komedi", age_limit=7) == "Çocuk"
    assert resolve_audience("", (), None, "Çocuk Tiyatrosu") == "Çocuk"
    assert resolve_audience("Yetişkin Oyunu", ["çocuk"]) == "Yetişkin"   # the page wins
    assert resolve_genre("Komedi") == "Komedi"
    assert resolve_genre("Çocuk Oyunu", "Ceviz Adam Müzikali") == "Müzikal"
    assert resolve_genre("Çocuk Oyunu", "X") == "Tiyatro"
    assert resolve_genre("", "Söyleşi", theatre=False) == ""        # only truly unknown stays blank
    assert genre_from_text("Dramaturji ve program") == ""           # no substring accidents
    assert genre_from_text("Bir komedi ve dram") == "Komedi"        # earliest keyword
    assert derived_tags([], "Yetişkin") == ["yetişkin"]
    assert derived_tags(["gündüz"], "Çocuk", 11) == ["gündüz", "çocuk"]
    assert derived_tags([], "Çocuk", 11) == ["çocuk", "gündüz"]


def test_vkv_and_basiskele_events_always_get_kitle_and_tur():
    listings = S.parse_vkv_list((FX / "vkv_list.html").read_text(encoding="utf-8"))
    assert listings
    for ls in listings:
        assert S.audience_for(ls.kind, ls.title) in ("Çocuk", "Yetişkin")
        assert S.genre_of(ls.kind, ls.title)
    by_kind = {ls.kind: S.audience_for(ls.kind, ls.title) for ls in listings}
    assert by_kind["Çocuk Tiyatrosu"] == "Çocuk" and by_kind["Tiyatro"] == "Yetişkin"
    raw = (FX / "bk_art.html").read_bytes().decode("iso-8859-9")
    found = S.parse_article(raw, "https://x.test/a", datetime(2025, 11, 1, tzinfo=ISTANBUL))[0]
    assert found
    for ls in found:
        assert S.audience_for(ls.kind, ls.title)
        if S.is_theatre(ls.kind, ls.title) or ls.kind:
            assert S.genre_of(ls.kind, ls.title)
        else:                       # no category at all ("Evlilik Okulu"): truly unknown -> "—"
            assert S.genre_of(ls.kind, ls.title) == ""
    kids = [ls for ls in found if "çocuk" in ls.kind.lower()]
    assert kids and all(S.audience_for(ls.kind, ls.title) == "Çocuk" for ls in kids)


def test_stored_event_records_are_recomputed_so_old_state_is_filled_too():
    state = {"source_events": {"vkv:1": {
        "id": "vkv:1", "source": "vkv", "title": "Unutmak", "kind": "Tiyatro",
        "start": (NOW + timedelta(days=3)).isoformat(), "audience": "", "genre": ""}}}
    (v,) = S.event_views(state, default_config(), NOW)
    assert (v["audience"], v["genre"]) == ("Yetişkin", "Tiyatro")


# ---- unified list rows --------------------------------------------------------------------------

def view(eid="vkv:541", source="vkv", followed=True, full=False, opens=None,
         kind="Çocuk Tiyatrosu", title="Ceviz Adam Müzikali", pending=True):
    rec = {"id": eid, "source": source, "title": title, "kind": kind, "venue": "KSYM",
           "url": "https://example.test/" + eid, "full": full,
           "start": (NOW + timedelta(days=13, hours=-1)).isoformat(),
           "opens_at": opens.isoformat() if opens else None,
           "opens_pending": pending if opens else None}
    cfg = default_config()
    cfg.event_follow = {eid: followed}
    return S.event_views({"source_events": {eid: rec}}, cfg, NOW)[0]


def test_event_row_has_source_seans_status_and_follow():
    r = event_row(view(opens=NOW + timedelta(days=4)))
    assert r.is_event and r.source.startswith("VKV")
    assert r.audience == "Çocuk" and r.genre == "Müzikal" and r.tags
    assert r.seans == "28 Kasım Cumartesi 11:00"
    assert r.status_summary == "rezervasyon 19 Kas 12:00'da açılacak"
    assert r.reservation_opens == "19 Kasım Perşembe 12:00"
    assert r.contact and r.watched
    values = L.row_values(r)
    assert values[2] == r.source and values[8] == r.seans and values[9] == r.status_summary
    assert values[10] == "Takipte"
    assert L.row_values(event_row(view(followed=False)))[10] == "Pasif"
    assert event_row(view(full=True)).status_summary == "doldu"
    assert event_row(view()).status_summary == "duyuru bekleniyor"
    assert event_row(view(opens=NOW - timedelta(days=1), pending=False)).status_summary         == "rezervasyon açık"
    assert event_row(view(opens=NOW - timedelta(days=1))).status_summary         == "rezervasyon açılmış olabilir"       # page still says "açılacaktır"
    bk = event_row(view("bk:1", "basiskele"))
    assert bk.contact == [] and bk.source.startswith("Başiskele")


def test_unified_list_sorts_by_kaynak_and_keeps_followed_first():
    play = Row("p1", "Biletinial Oyun", source="Biletinial", watched=False, nearest=NOW)
    rows = with_events([play], [view("bk:1", "basiskele", title="A"), view()])
    assert {r.source[:3] for r in rows} == {"Bil", "Baş", "VKV"}
    assert "source" in L.SORT_COLUMNS
    asc = [r.source for r in L.sort_rows(rows, ("source", "asc"))]
    assert asc == sorted(asc, key=L.tr_sort_key)
    assert rows[-1].slug == "p1"                    # followed events before an unfollowed play


# ---- Tk: the unified list, event detail, merged portal ------------------------------------------

tk = pytest.importorskip("tkinter")
from tests.test_pc_app_smoke import Box, FakeService, tk_root, ui  # noqa: E402,F401


@pytest.fixture
def mixed(ui):
    ui.views = [view(opens=NOW + timedelta(days=4)), view("bk:1", "basiskele", title="Çöp Atlas")]
    ui.follow_calls = []
    base = ui.svc.rows
    ui.svc.rows = lambda now=None: with_events(base(), ui.views)

    def set_follow(eid, on):
        ui.follow_calls.append((eid, on))
        for i, v in enumerate(ui.views):
            if v["id"] == eid:
                ui.views[i] = dict(v, followed=on)
    ui.svc.set_event_follow = set_follow
    ui.refresh_rows()
    return ui


def fact_texts(ui):
    return [w.cget("text") for w in ui.detail_body.winfo_children()[-1].winfo_children()]


def test_diger_kaynaklar_tab_is_gone_and_events_are_in_the_one_list(mixed):
    ui = mixed
    assert [ui.notebook.tab(t, "text") for t in ui.notebook.tabs()] == [
        "Oyunlar", "Etkinlik günlüğü"]
    assert not hasattr(ui, "events_tab")
    assert {"p1", "p2", "vkv:541", "bk:1"} <= set(ui.tree.get_children())
    cols = list(ui.tree["columns"])
    vals = dict(zip(cols, ui.tree.item("vkv:541", "values")))
    assert vals["source"].startswith("VKV") and vals["audience"] == "Çocuk"
    assert vals["sessions"] == "28 Kasım Cumartesi 11:00"
    assert vals["summary"].startswith("rezervasyon 19 Kas") and vals["follow"] == "Takipte"
    assert dict(zip(cols, ui.tree.item("p1", "values")))["source"] == "Biletinial"


def test_event_detail_shows_labelled_dates_contact_and_follow_toggle(mixed):
    ui = mixed
    ui.select_slug("vkv:541")
    texts = fact_texts(ui)
    assert "Etkinlik tarihi" in texts and "Rezervasyon açılışı" in texts
    assert "28 Kasım Cumartesi 11:00" in texts and "19 Kasım Perşembe 12:00" in texts
    assert any("435 6700" in t for t in texts) and any("@" in t for t in texts)
    assert "https://example.test/vkv:541" in texts
    assert ui.meta_label.cget("text").startswith("Kaynak: VKV")
    ui.open_btn.invoke()
    assert ui.opened == ["https://example.test/vkv:541"]
    ui.active_chk.invoke()                       # same switch as plays, routed to event_follow
    ui.pump()
    assert ui.follow_calls == [("vkv:541", False)]
    assert ui.tree.item("vkv:541", "values")[-1] == "Pasif"
    ui.select_slug("bk:1")                       # Başiskele: no contact block
    texts = fact_texts(ui)
    assert not any("435 6700" in t for t in texts) and "Rezervasyon açılışı" in texts


def test_play_detail_still_works_after_an_event_detail(mixed):
    mixed.select_slug("vkv:541")
    mixed.select_slug("p1")
    assert len(mixed.session_tree.get_children()) == 3 and mixed.add_buttons


def test_sorting_the_list_by_kaynak(mixed):
    mixed.sort_by("source")
    sources = [mixed.tree.item(k, "values")[2] for k in mixed.tree.get_children()]
    assert sources == sorted(sources, key=L.tr_sort_key)
    assert mixed.tree.heading("source")["text"].endswith("▲")


def test_ozet_text_is_as_tall_as_its_content_up_to_a_cap(ui):
    from pc.widgets import autosize_text
    frame = tk.Frame(ui.root)
    text = tk.Text(frame, width=40, height=1, wrap="word")
    text.insert("1.0", "kelime " * 400)
    text.configure(state="disabled")
    text.pack()
    frame.pack()
    ui.root.update_idletasks()
    changed = []
    fit = autosize_text(text, 10, on_change=lambda: changed.append(1))
    fit()
    assert int(text.cget("height")) == 10 and changed          # long text: capped (scrolls)
    short = tk.Text(frame, width=40, height=6, wrap="word")
    short.insert("1.0", "tek satır")
    short.pack()
    ui.root.update_idletasks()
    autosize_text(short, 10)
    assert int(short.cget("height")) == 1                      # short text: no empty space
    frame.destroy()


def test_app_uses_the_default_92_percent_window_and_ignores_old_saved_size(tmp_path, tk_root):
    from pc import ui as appmod
    (tmp_path / "ui.json").write_text(json.dumps({"geometry": "500x400+0+0", "beep": True}),
                                      encoding="utf-8")
    for w in tk_root.winfo_children():
        w.destroy()
    tk_root.configure(menu=tk.Menu(tk_root))
    app = appmod.App(tk_root, FakeService(), tmp_path, open_url=lambda u: None,
                     beep=lambda: None, msgbox=Box(), inline=True)
    try:
        tk_root.update_idletasks()
        want = L.default_geometry(tk_root.winfo_screenwidth(), tk_root.winfo_screenheight())
        assert tk_root.geometry().split("+")[0] == want.split("+")[0]
        app.save_ui()
        saved = json.loads((tmp_path / "ui.json").read_text(encoding="utf-8"))
        assert saved["geometry_v"] == L.GEOMETRY_VERSION
        assert L.load_ui(tmp_path / "ui.json")["geometry"]            # kept from now on
    finally:
        app.closing = True
        for w in list(tk_root.winfo_children()):
            w.destroy()


def test_divider_does_not_jump_while_clicking_through_rows(mixed, monkeypatch):
    ui = mixed
    ui._sash_user, ui._fit_key, ui._need_seen = False, None, 0
    ui.root.update_idletasks()
    if ui.pane.winfo_height() < 100:
        pytest.skip("window not laid out (withdrawn)")
    asked = []
    real = ui.pane.sashpos
    monkeypatch.setattr(ui.pane, "sashpos", lambda i, pos=None: asked.append(pos) or real(i)
                        if pos is not None else real(i))
    ui.select_slug("p1")
    ui._fit_panes()
    ui.select_slug("vkv:541")                      # a smaller detail must not move the divider
    ui._fit_panes()
    ui.select_slug("p1")
    ui._fit_panes()
    assert len(asked) == 1                         # fitted once, then left alone
