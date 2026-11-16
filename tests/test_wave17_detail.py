"""Wave 17: ONE detail template for plays and source events (same sections, labels, placeholder,
box style) and every read-only text selectable / copyable in a real window."""
from __future__ import annotations

from datetime import datetime

import pytest

tk = pytest.importorskip("tkinter")
from pc import app_logic as L  # noqa: E402
from pc.ui import SOURCE_NO_INFO  # noqa: E402
from pc.viewmodel import with_events  # noqa: E402
from pc.widgets import SelectableLabel, SelectableText  # noqa: E402
from tests.test_pc_app_smoke import Box, FakeService, iso, tk_root, ui  # noqa: E402,F401
from tests.test_wave8 import _show  # noqa: E402,F401
from tiyatro import srcevents as S  # noqa: E402
from tiyatro.config import config_from_dict  # noqa: E402
from tiyatro.models import ISTANBUL  # noqa: E402

TYGM = "Türkiye Yüzyılı Gençlik Merkezi"


def _setup(ui):
    ui.svc._state["plays"]["unutmak"] = {
        "title": "Unutmak", "url": "https://example.test/unutmak", "tags": [], "genre": "Dram",
        "age_limit": 16, "duration_min": 95, "price_from": 35.0,
        "summary": "Bir kayıp, bir hatırlama.\r\n\r\n\r\nİkinci paragraf.",
        "cast": ["Senem Akman", "Levent Muratoğlu"],
        "credits": {"Yazan": "Tristan Bernays", "Yönetmen": "İbrahim Şahin"},
        "sessions": {"a": {"start": iso(1), "status": "on_sale"}}}
    state = dict(ui.svc._state)
    state["source_events"] = {
        "bk:u": {"id": "bk:u", "source": "basiskele", "title": "Unutmak", "kind": "Tiyatro Oyunu",
                 "start": iso(2), "venue": TYGM, "venue_derived": True,
                 "url": "https://www.basiskele.bel.tr/x", "follow_default": False,
                 "info": {"credits": {"Sahneleyen": "Kocaeli Şehir Tiyatroları"}}},
        "bk:k": {"id": "bk:k", "source": "basiskele", "title": "Kitaplar Ülkesi",
                 "kind": "Çocuk Tiyatrosu", "start": iso(3), "venue": TYGM,
                 "url": "https://www.basiskele.bel.tr/y", "follow_default": False, "info": {}}}
    views = S.event_views(state, config_from_dict({"sources": ["x"]}), datetime.now(ISTANBUL))
    base = ui.svc.rows
    ui.svc.rows = lambda now=None: with_events(base(), views, datetime.now(ISTANBUL))
    ui.refresh_rows()


def _pump(ui, n=8):
    for _ in range(n):
        ui.root.update()


def _sections(ui):
    """Structure of the open detail: the section titles of every part, in order."""
    out = {}
    for name in ("left", "side", "summary", "people"):
        titles = []

        def walk(w):
            if w is ui.event_facts_grid:        # the fact NAMES are bold too: not section titles
                return
            for c in w.winfo_children():
                cls = c.winfo_class()
                if cls in ("TLabel",) and str(c.cget("font")) == str(ui.bold_font):
                    titles.append(c.cget("text"))
                elif cls == "TLabelframe":
                    titles.append(c.cget("text"))
                walk(c)
        walk(ui._detail_parts[name])
        out[name] = titles
    return out


def test_plays_and_events_share_sections_labels_and_placeholder(ui):
    _setup(ui)
    _show(ui, 1900, 1000)
    shapes = {}
    for slug in ("unutmak", "bk:u", "bk:k"):
        ui.select_slug(slug)
        _pump(ui)
        shapes[slug] = _sections(ui)
        # same widget classes for the three info blocks
        assert isinstance(ui.event_info_labels["Özet"], SelectableText)
        assert isinstance(ui.cast_label, SelectableLabel) and isinstance(ui.credits_label, SelectableLabel)
        assert ui.summary_text is ui.event_info_labels["Özet"]
        # same Özet box style: white, bordered
        assert str(ui.summary_text.cget("background")) == "SystemWindow"
        assert str(ui.summary_text.cget("relief")) == "solid" and int(ui.summary_text.cget("borderwidth")) == 1
        # same facts labels
        labels = [w.cget("text") for w in ui.event_facts_grid.winfo_children()
                  if w.winfo_class() == "TLabel"]
        assert labels[0] == "Kaynak" and "Rezervasyon açılışı" in labels and "Durum" in labels
        assert "Mekan" in labels and "Etkinlik sayfası" in labels
        # same meta line start
        assert ui.meta_label.cget("text").startswith("Kaynak: ")
    # section titles are identical for every row; plays additionally have Hedefler
    for part in ("summary", "people"):
        assert shapes["unutmak"][part] == shapes["bk:u"][part] == shapes["bk:k"][part]
    assert shapes["bk:u"]["side"] == shapes["bk:k"]["side"] == ["Bilgiler"]
    assert sorted(shapes["unutmak"]["side"]) == ["Bilgiler", "Hedefler"]
    # the plain event: the same grey placeholder, in the Özet BOX, for every missing part
    ui.select_slug("bk:k")
    _pump(ui)
    assert ui.summary_text.get("1.0", "end-1c") == SOURCE_NO_INFO
    assert str(ui.summary_text.cget("foreground")) == "SystemGrayText"
    for lbl in (ui.cast_label, ui.credits_label):
        assert lbl.cget("text") == SOURCE_NO_INFO and str(lbl.cget("foreground")) == "SystemGrayText"


def test_right_column_structure_is_the_same_wide_and_stacked(ui):
    _setup(ui)
    for width, wide in ((1900, True), (1010, False)):
        _show(ui, width, 900)
        for slug in ("unutmak", "bk:u"):
            ui.select_slug(slug)
            _pump(ui)
            facts = ui._detail_parts["facts"]
            people = ui._detail_parts["people"]
            left = ui._detail_parts["left"]
            if wide:
                assert facts.winfo_rootx() > left.winfo_rootx() + 300
                assert abs(people.winfo_rootx() - ui._detail_parts["side"].winfo_rootx()) <= 12
            else:
                assert abs(facts.winfo_rootx() - left.winfo_rootx()) <= 24
            assert facts.winfo_rooty() < people.winfo_rooty()


def test_borrowed_fields_are_italic_with_a_provenance_line(ui):
    _setup(ui)
    ui.select_slug("bk:u")
    _pump(ui)
    box = ui.summary_text
    assert box.get("1.0", "end-1c").startswith("Bir kayıp, bir hatırlama.")
    assert box.tag_ranges("derived")                              # whole Özet italic grey-blue
    assert ui.cast_label.tag_ranges("derived") and ui.cast_label.cget("text") == "Senem Akman, Levent Muratoğlu"
    credits = ui.credits_label
    ranges = credits.tag_ranges("derived")
    italic = credits.get(str(ranges[0]), str(ranges[1]))
    assert italic.startswith("Yazan: Tristan Bernays")            # borrowed entry italic ...
    assert credits.get("1.0", "1.8") == "Sahneley"                # ... the municipality's own entry is not
    assert ui.provenance_label is not None
    prov = ui.provenance_label.cget("text")
    assert prov.startswith("Biletinial kaydından (aynı ad): ") and "Özet" in prov and "“Unutmak”" in prov
    assert "Süre: 95 dk" in ui.meta_label.cget("text")
    assert any("Süre" in ui.meta_label.get(str(a), str(b))
               for a, b in zip(ui.meta_label.tag_ranges("derived")[::2], ui.meta_label.tag_ranges("derived")[1::2]))
    # the venue is derived (resolved from "aynı merkezde"): italic in Bilgiler
    mekan = [w for w in ui.event_fact_labels if w.cget("text") == TYGM][0]
    assert mekan.tag_ranges("derived")
    # a stated venue and a play are not marked
    ui.select_slug("bk:k")
    _pump(ui)
    assert ui.provenance_label is None
    mekan = [w for w in ui.event_fact_labels if w.cget("text") == TYGM][0]
    assert not mekan.tag_ranges("derived")
    ui.select_slug("unutmak")
    _pump(ui)
    assert ui.provenance_label is None and not ui.summary_text.tag_ranges("derived")


# ---- selection / copy ------------------------------------------------------------------------

def _copy_roundtrip(ui, widget, expected_start):
    ui.root.clipboard_clear()
    ui.root.clipboard_append("SENTINEL")
    widget.focus_force()
    ui.root.update()
    widget.event_generate("<Button-1>", x=2, y=8)                # press, drag, release = a mouse selection
    widget.event_generate("<B1-Motion>", x=max(8, widget.winfo_width() // 2), y=8, state=0x100)
    widget.event_generate("<ButtonRelease-1>", x=max(8, widget.winfo_width() // 2), y=8)
    ui.root.update()
    picked = widget.selected_text()
    assert picked and picked in expected_start
    widget.event_generate("<Control-c>")
    ui.root.update()
    assert ui.root.clipboard_get() == picked
    ui.root.clipboard_clear()
    ui.root.clipboard_append("SENTINEL")
    widget.event_generate("<Control-a>")
    ui.root.update()
    widget.event_generate("<Control-c>")
    ui.root.update()
    assert ui.root.clipboard_get() == widget.get("1.0", "end-1c")


def test_every_detail_text_can_be_selected_and_copied(ui):
    _setup(ui)
    _show(ui, 1900, 1000)
    for slug in ("unutmak", "bk:u"):
        ui.select_slug(slug)
        _pump(ui)
        targets = [ui.summary_text, ui.cast_label, ui.credits_label, ui.meta_label,
                   *ui.event_fact_labels]
        if ui.follow_help is not None:
            targets += [ui.follow_help, ui.event_no_page_label]
        for w in targets:
            assert isinstance(w, SelectableText), w
            _copy_roundtrip(ui, w, w.get("1.0", "end-1c"))


def test_contact_phone_email_and_url_values_are_selectable(ui):
    state_views = S.event_views(
        {"source_events": {"vkv:1": {"id": "vkv:1", "source": "vkv", "title": "Ceviz Adam",
                                     "kind": "Çocuk Tiyatrosu", "start": iso(3),
                                     "url": "https://www.vkvfordkultur.org.tr/EtkinlikDetay/1/x"}}},
        config_from_dict({}), datetime.now(ISTANBUL))
    base = ui.svc.rows
    ui.svc.rows = lambda now=None: with_events(base(), state_views, datetime.now(ISTANBUL))
    ui.refresh_rows()
    ui.select_slug("vkv:1")
    _pump(ui)
    values = {w.cget("text"): w for w in ui.event_fact_labels}
    phone = [t for t in values if "435 6700" in t][0]
    url = [t for t in values if t.startswith("https://www.vkvfordkultur")][0]
    for text in (phone, url):
        w = values[text]
        w.select_all()
        ui.root.update()
        assert w.selected_text() == text and w.copy_selection()
        assert ui.root.clipboard_get() == text
    assert "info@vkvfordkultur.org.tr" in phone


def test_typing_and_paste_never_change_the_text_and_the_menu_has_kopyala_and_tumunu_sec(ui):
    _setup(ui)
    ui.select_slug("unutmak")
    _pump(ui)
    box = ui.summary_text
    before = box.get("1.0", "end-1c")
    ui.root.clipboard_clear()
    ui.root.clipboard_append("EVIL")
    box.focus_force()
    ui.root.update()
    for ev in ("<Key>", "<BackSpace>", "<Delete>", "<Return>", "<<Paste>>", "<<Cut>>", "<Control-v>", "<Control-x>"):
        box.event_generate(ev, **({"keysym": "q"} if ev == "<Key>" else {}))
    ui.root.update()
    assert box.get("1.0", "end-1c") == before
    menu = box.context_menu()
    assert [menu.entrycget(i, "label") for i in range(2)] == ["Kopyala", "Tümünü seç"]
    box.select_all()
    menu.invoke(1)                                               # Tümünü seç
    assert box.selected_text() == before
    ui.root.clipboard_clear()
    menu.invoke(0)                                               # Kopyala
    assert ui.root.clipboard_get() == before


def test_wheel_over_a_selectable_text_still_scrolls_the_pane(ui):
    _setup(ui)
    ui.select_slug("unutmak")
    _pump(ui)
    assert ui.cast_label.scrolls() is False and ui.summary_text.scrolls() is False


def test_status_block_and_source_cards_are_selectable(ui):
    for lbl in ui.status_labels.values():
        assert isinstance(lbl, SelectableText)
    assert isinstance(ui.warn_label, SelectableText) and isinstance(ui.msg_label, SelectableText)
    ui.set_message("Merhaba mesaj")
    ui.root.update()
    assert ui.msg_label.cget("text") == "Merhaba mesaj"
    ui.msg_label.select_all()
    assert ui.msg_label.copy_selection() and ui.root.clipboard_get() == "Merhaba mesaj"
    # the error line keeps its click action; it gets a right-click "Kopyala" instead
    assert ui.err_label.copy_menu.entrycget(0, "label") == "Kopyala"
    for card in ui.source_cards.values():
        assert isinstance(card["body"], SelectableText) and isinstance(card["title"], SelectableText)
        assert isinstance(card["error"], SelectableText)


def test_activity_log_rows_and_detail_dialog_can_be_copied(ui):
    tab = ui.activity_tab
    ui.activity.add("Liste kontrolü", "Deneme kaydı", "Ayrıntı metni burada")
    tab.refresh()
    rows = tab.tree.get_children()
    if not rows:
        pytest.skip("no activity rows to copy")
    tab.tree.selection_set(rows[0])
    ui.root.clipboard_clear()
    tab.copy_selected()
    ui.root.update()
    assert ui.root.clipboard_get() == tab.row_text(rows[0]) and "\t" in tab.row_text(rows[0])
    dlg = tab.open_detail()
    try:
        assert isinstance(dlg.body, SelectableText)
        dlg.body.select_all()
        assert dlg.body.copy_selection()
        assert dlg.body.selected_text() == dlg.body.get("1.0", "end-1c")
        assert dlg.body.context_menu().entrycget(0, "label") == "Kopyala"
    finally:
        dlg.destroy()
