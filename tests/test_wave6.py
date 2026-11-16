"""Wave 6: default follow OFF, Takip/Hedef/Zaman/Rez. açılış columns, session summary,
provenance (derived) flags, new-item (seen) store, link helpers, source links."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from pc import app_logic as L
from pc.viewmodel import (Row, _row, event_row, relative_day, rez_when, sessions_summary,
                          short_when)
from tiyatro import srcevents as S
from tiyatro.config import config_from_dict, default_config
from tiyatro.models import ISTANBUL
from tiyatro.runner import _prepare
from tiyatro.tags import audience_is_stated, genre_is_stated, time_of_day

NOW = datetime(2026, 11, 15, 12, 0, tzinfo=ISTANBUL)
FX = Path(__file__).parent / "fixtures"


def at(days: float, hour: int, minute: int = 0) -> datetime:
    return (NOW + timedelta(days=days)).replace(hour=hour, minute=minute)


def sess(days, hour, status="upcoming", opens=None, url=None, minute=0):
    d = at(days, hour, minute)
    rec = {"start": d.isoformat(), "status": status}
    if opens:
        rec["opens_at"] = opens.isoformat()
    if url:
        rec["url"] = url
    return rec


def play(sessions: dict, **kw) -> dict:
    base = {"title": "Oyun", "url": "https://example.test/p", "genre": "Komedi",
            "age_limit": 13, "price_from": 100.0, "sessions": sessions}
    base.update(kw)
    return base


def row_of(entry, cfg=None, slug="p") -> Row:
    return _row(slug, entry, cfg or default_config(), set(), set(), NOW)


def followed_cfg(slug="p"):
    return config_from_dict({"plays": {slug: {"active": True, "tag_overrides": {},
                                              "targets": []}}})


# ---- 8. default follow OFF ----------------------------------------------------------------------

def listing(eid="bk:a", kind="Tiyatro Oyunu", title="Oyun", start=None, default=False):
    return S.Listing("basiskele", eid, title, kind, start or at(5, 20), "Salon",
                     "https://example.test/x", follow_default=default)


def with_opening(eid, opens):
    ls = listing(eid)
    ls.opens_at, ls.opens_pending, ls.detail_ok = opens, True, True
    return ls


def new_state():
    return _prepare({"plays": {}, "meta": {}, "emitted": {}})


def test_every_listing_defaults_to_not_followed():
    assert S.Listing("vkv", "v", "T", "Tiyatro").follow_default is False
    html = (FX / "bk_art.html").read_bytes().decode("iso-8859-9")
    events, _o, _p = S.parse_article(html, "https://example.test/a", NOW)
    assert events and not any(e.follow_default for e in events)     # theatre included
    vkv = S.parse_vkv_list((FX / "vkv_list.html").read_text(encoding="utf-8"))
    assert vkv and not any(v.follow_default for v in vkv)           # Tiyatro / Çocuk Tiyatrosu too


def test_new_event_is_not_followed_until_the_user_says_so():
    st, cfg = new_state(), default_config()
    S.fold_listings(st, cfg, "basiskele", [listing("bk:old")], NOW)           # baseline
    ev = S.fold_listings(st, cfg, "basiskele", [listing("bk:old"), listing("bk:new")],
                         NOW + timedelta(minutes=5))
    # no reminder/opening alert for an unfollowed event; since wave 9 only the discovery notice
    assert [e.kind for e in ev] == ["SRC_NEW"] and ev[0].payload["followed"] is False
    assert st["source_events"]["bk:new"]["follow_default"] is False
    assert {v["id"]: v["followed"] for v in S.event_views(st, cfg, NOW)}["bk:new"] is False
    cfg.event_follow = {"bk:new": True}                  # the user opts in
    ev = S.fold_listings(st, cfg, "basiskele",
                         [listing("bk:old"), with_opening("bk:new", at(2, 12))],
                         NOW + timedelta(minutes=10))
    assert [e.kind for e in ev] == ["SRC_OPENS"]


def test_existing_record_keeps_its_stored_default_and_its_alerts():
    """Wave 6 must not switch off what was followed (by the old ON default) before."""
    st, cfg = new_state(), default_config()
    S.fold_listings(st, cfg, "basiskele", [listing("bk:x", default=True)], NOW)   # stored ON
    assert st["source_events"]["bk:x"]["follow_default"] is True
    ls = with_opening("bk:x", at(2, 12))   # this scan carries the new default (False)
    ev = S.fold_listings(st, cfg, "basiskele", [ls], NOW + timedelta(minutes=5))
    assert st["source_events"]["bk:x"]["follow_default"] is True
    assert [e.kind for e in ev] == ["SRC_OPENS"]
    assert {v["id"]: v["followed"] for v in S.event_views(st, cfg, NOW)}["bk:x"] is True


def test_record_without_the_field_counts_as_followed_like_before():
    st, cfg = new_state(), default_config()
    S.fold_listings(st, cfg, "basiskele", [listing("bk:x")], NOW)
    del st["source_events"]["bk:x"]["follow_default"]    # stored before the field existed
    S.fold_listings(st, cfg, "basiskele", [listing("bk:x")], NOW + timedelta(minutes=5))
    assert st["source_events"]["bk:x"]["follow_default"] is True


def test_explicit_choice_beats_any_default_and_survives_a_scan():
    st = new_state()
    cfg = config_from_dict({"event_follow": {"bk:new": True, "bk:old": False}})
    S.fold_listings(st, cfg, "basiskele", [listing("bk:old")], NOW)
    S.fold_listings(st, cfg, "basiskele", [listing("bk:old"), listing("bk:new")],
                    NOW + timedelta(minutes=5))
    views = {v["id"]: v["followed"] for v in S.event_views(st, cfg, NOW)}
    assert views == {"bk:old": False, "bk:new": True}


def test_biletinial_plays_are_inactive_without_a_config_entry():
    r = row_of(play({"a": sess(5, 20, "on_sale")}))
    assert r.active is False and r.watched is False
    assert L.row_cells(r)[0].text == L.UNCHECKED


# ---- 1. regression: the follow toggle round-trips through the real Service ----------------------

def test_follow_toggle_of_a_default_off_event_is_saved_shown_and_survives_reload_and_scan(
        monkeypatch, tmp_path):
    """Live data (data branch, 2026-11-15): config.json held ``event_follow[id] = true`` and
    the list showed the event as followed, i.e. a toggle on a default-OFF event persists.
    Guard: save -> row followed -> reload from the store -> still followed -> a later scan of
    the same listing does not reset it, and the opening alert then fires."""
    from tests.test_pc_service import make
    e = make(monkeypatch, tmp_path)
    eid = "bk:20261120-1100-satir-arasi-ebeveynlik"
    e.store.files["config.json"] = ({"plays": {}, "portals": [], "event_follow": {}}, "cfg")
    e.svc._state = new_state()
    e.svc._reconciled = True
    ls = listing(eid, kind="", title="Satır Arası Ebeveynlik", start=at(5, 11))
    S.fold_listings(e.svc._state, default_config(), "basiskele", [ls], NOW)
    e.svc.reload_config()

    def pick():
        return next(r for r in e.svc.rows(NOW) if r.slug == eid)

    assert pick().watched is False
    e.svc.set_event_follow(eid, True)
    assert e.store.files["config.json"][0]["event_follow"] == {eid: True}
    assert pick().watched is True and L.row_cells(pick())[0].text == L.CHECKED
    e.svc.reload_config()
    assert pick().watched is True
    ev = S.fold_listings(e.svc._state, e.svc.config, "basiskele",
                         [with_opening(eid, at(2, 12))], NOW + timedelta(minutes=5))
    assert [x.kind for x in ev] == ["SRC_OPENS"]
    assert pick().watched is True


# ---- 5/3. Zaman, Hedef -------------------------------------------------------------------------

def test_time_of_day_rule():
    assert time_of_day([15]) == "gündüz" and time_of_day([20]) == "gece"
    assert time_of_day([20, 15]) == "gündüz+gece" and time_of_day([]) == ""
    assert time_of_day([16, 17]) == "gündüz+gece"       # 17:00 is already "gece"


def test_play_zaman_holds_only_the_time_of_day():
    r = row_of(play({"a": sess(5, 15), "b": sess(5, 20)}, tags=["çocuk"]))
    assert r.zaman == "gündüz+gece" and "çocuk" not in r.zaman
    assert row_of(play({"a": sess(5, 20)})).zaman == "gece"
    assert row_of(play({})).zaman == ""
    assert [c for c in L.LIST_COLUMNS if c[0] == "zaman"][0][1] == "Zaman"
    assert "tags" not in [c[0] for c in L.LIST_COLUMNS]


def test_hedef_column_text_and_sorting():
    cfg = config_from_dict({"plays": {
        "p": {"active": True, "tag_overrides": {}, "targets": [
            {"id": "y", "name": "Yetişkin", "people": 2, "rule": {}},
            {"id": "c", "name": "Çocuk", "people": 2, "rule": {}}]},
        "q": {"active": True, "tag_overrides": {}, "targets": []}}})
    with_t = row_of(play({"a": sess(5, 20)}), cfg, "p")
    without = row_of(play({"a": sess(5, 20)}), cfg, "q")
    other = row_of(play({"a": sess(5, 20)}), cfg, "z")          # not in the config at all
    assert with_t.target_text == "var (2)" and without.target_text == "yok → tüm seanslar"
    assert other.target_text == "yok → tüm seanslar"
    ev = event_row(_view(), NOW)
    assert ev.target_text == "—"                                # targets do not exist for events
    asc = L.sort_rows([without, with_t, ev], ("target", "desc"))
    assert asc[0] is with_t and asc[-1] is ev                   # events have no value: last
    assert L.next_sort(None, "target") == ("target", "desc")    # "var" first on the first click


# ---- 4. Seans summary --------------------------------------------------------------------------

def test_sessions_summary_format_and_sort_is_the_earliest_session():
    starts = [at(13, 20), at(13, 15), at(20, 20)]
    assert sessions_summary(starts) == "3 seans: 28 Kas 15:00, 28 Kas 20:00, +1"
    assert sessions_summary(starts[:2]) == "2 seans: 28 Kas 15:00, 28 Kas 20:00"
    assert sessions_summary([at(1, 20)]) == "1 seans: 16 Kas 20:00" and sessions_summary([]) == ""
    assert short_when(at(13, 15)) == "28 Kas 15:00"
    early = row_of(play({"a": sess(3, 20), "b": sess(9, 20)}), slug="early")
    late = row_of(play({"a": sess(8, 20)}), slug="late")
    many_late = row_of(play({"a": sess(8, 21), "b": sess(9, 20), "c": sess(10, 20)}), slug="many")
    none = row_of(play({}), slug="none")
    assert early.sessions_text.startswith("2 seans: 18 Kas 20:00")
    order = [r.slug for r in L.sort_rows([many_late, none, late, early], ("sessions", "asc"))]
    assert order == ["early", "late", "many", "none"]           # by earliest datetime; none last
    assert [r.slug for r in L.sort_rows([late, early], ("sessions", "desc"))] == ["late", "early"]
    assert L.row_cells(none)[L.SORT_COLUMNS.index("sessions")].text == "—"


# ---- 7. Rez. açılış ----------------------------------------------------------------------------

def test_relative_day_and_format():
    assert relative_day(at(0, 20), NOW) == "bugün" and relative_day(at(1, 1), NOW) == "yarın"
    assert relative_day(at(4, 10), NOW) == "4 gün sonra" and relative_day(at(-1, 10), NOW) == "geçti"
    assert rez_when(at(1, 10), NOW) == "16 Kas Pzt 10:00 (yarın)"


def test_play_rez_ladder():
    opening = row_of(play({"a": sess(8, 20, "upcoming", opens=at(1, 10)),
                           "b": sess(9, 20, "upcoming", opens=at(3, 10)),
                           "c": sess(10, 20, "sold_out")}))
    assert opening.rez_text == "16 Kas Pzt 10:00 (yarın)" and "rez" not in opening.derived
    on_sale = row_of(play({"a": sess(8, 20, "on_sale"), "b": sess(9, 20, "sold_out")}))
    assert on_sale.rez_text == "açık" and on_sale.rez_key == (0, 0.0)
    sold = row_of(play({"a": sess(8, 20, "sold_out"), "b": sess(9, 20, "sold_out")}))
    assert sold.rez_text == "tükendi" and sold.rez_key is None
    blank = row_of(play({"a": sess(8, 20, "upcoming"), "b": sess(9, 20, "sold_out")}))
    assert blank.rez_text == "—" and "rez" in blank.derived
    past_open = row_of(play({"a": sess(8, 20, "upcoming", opens=at(-1, 10))}))
    assert past_open.rez_text == "—"                           # a passed time is not an opening
    assert row_of(play({})).rez_text == "—"
    sold_past = row_of(play({"a": sess(-3, 20, "sold_out"), "b": sess(8, 20, "on_sale")}))
    assert sold_past.rez_text == "açık"                         # past sessions are ignored


def _view(source="vkv", eid="vkv:541", kind="Çocuk Tiyatrosu", title="Ceviz Adam Müzikali",
          opens=None, pending=True, full=False, followed=True, start=None):
    rec = {"id": eid, "source": source, "title": title, "kind": kind, "venue": "KSYM",
           "url": "https://example.test/" + eid, "full": full,
           "start": (start or at(13, 11)).isoformat(),
           "opens_at": opens.isoformat() if opens else None,
           "opens_pending": pending if opens else None, "follow_default": False}
    cfg = default_config()
    cfg.event_follow = {eid: followed}
    return S.event_views({"source_events": {eid: rec}}, cfg, NOW)[0]


def test_event_rez_texts_and_sort_keys():
    assert event_row(_view(opens=at(4, 12)), NOW).rez_text == "19 Kas Per 12:00 (4 gün sonra)"
    assert event_row(_view(full=True), NOW).rez_text == "doldu"
    nothing = event_row(_view(), NOW)
    assert nothing.rez_text == "duyuru bekleniyor" and {"rez", "summary"} <= nothing.derived
    opened = event_row(_view(opens=at(-1, 12), pending=False), NOW)
    assert opened.rez_text == "açık" and opened.rez_key == (0, 0.0) and "rez" in opened.derived
    maybe = event_row(_view(opens=at(-1, 12)), NOW)
    assert maybe.rez_text == "açılmış olabilir"
    dated = event_row(_view(opens=at(4, 12)), NOW)
    assert "rez" not in dated.derived                           # the page states the time


def test_rez_sorting_acik_first_then_by_datetime():
    a = row_of(play({"a": sess(8, 20, "on_sale")}), slug="acik")
    soon = row_of(play({"a": sess(8, 20, "upcoming", opens=at(1, 10))}), slug="soon")
    later = row_of(play({"a": sess(8, 20, "upcoming", opens=at(5, 10))}), slug="later")
    sold = row_of(play({"a": sess(8, 20, "sold_out")}), slug="sold")
    ev = event_row(_view(opens=at(2, 12)), NOW)
    rows = [sold, later, ev, soon, a]
    asc = [r.slug for r in L.sort_rows(rows, ("rez", "asc"))]
    assert asc == ["acik", "soon", "vkv:541", "later", "sold"]
    assert L.sort_rows(rows, ("rez", "desc"))[-1] is sold       # no value: last either way


# ---- 10. provenance ----------------------------------------------------------------------------

def test_stated_vs_derived_helpers():
    assert audience_is_stated("Çocuk Oyunu") and audience_is_stated("Yetişkin Tiyatrosu")
    assert not audience_is_stated("Komedi") and not audience_is_stated("") \
        and not audience_is_stated("Çocuk Yetişkin")
    assert genre_is_stated("Komedi") and not genre_is_stated("Yetişkin Oyunu") \
        and not genre_is_stated("") and not genre_is_stated("Tiyatro")


def test_play_provenance_flags():
    comedy = row_of(play({"a": sess(5, 20)}))   # genre says Komedi, audience by the age rule
    assert comedy.derived == {"audience", "zaman", "target", "rez"}
    child = row_of(play({"a": sess(5, 15, "on_sale")}, genre="Çocuk Oyunu"))
    assert child.derived == {"genre", "zaman", "target"}   # audience stated, genre blank
    assert child.genre == "" and child.audience == "Çocuk"
    cells = dict(zip(L.SORT_COLUMNS, L.row_cells(comedy)))
    assert cells["genre"].derived is False and cells["audience"].derived is True
    assert cells["zaman"].derived and cells["target"].derived and cells["rez"].derived
    for plain in ("title", "source", "duration", "age", "price", "sessions", "summary"):
        assert cells[plain].derived is False, plain
    nosession = dict(zip(L.SORT_COLUMNS, L.row_cells(row_of(play({})))))
    assert nosession["zaman"].derived and nosession["sessions"].derived   # "—" is never "read"
    targeted = config_from_dict({"plays": {"p": {"active": True, "tag_overrides": {}, "targets": [
        {"id": "y", "name": "Yetişkin", "people": 2, "rule": {}}]}}})
    assert "target" not in row_of(play({"a": sess(5, 20)}), targeted).derived  # the user's choice


def test_event_provenance_per_source():
    vkv_child = event_row(_view(), NOW)                       # category "Çocuk Tiyatrosu"
    assert "audience" not in vkv_child.derived and "genre" in vkv_child.derived
    assert vkv_child.genre == "Müzikal"                       # from the title word: derived
    vkv_plain = event_row(_view(eid="vkv:540", kind="Tiyatro", title="Tek Kişilik Gösteri"), NOW)
    assert {"audience", "genre", "zaman"} <= vkv_plain.derived and vkv_plain.genre == ""
    bk_kind = event_row(_view("basiskele", "bk:1", "Yetişkin Tiyatrosu", "Hiç Kimsenin Öyküsü"), NOW)
    assert bk_kind.audience == "Yetişkin" and "audience" not in bk_kind.derived
    bk_none = event_row(_view("basiskele", "bk:2", "", "Satır Arası Ebeveynlik"), NOW)
    assert "audience" in bk_none.derived and bk_none.audience == "Yetişkin"
    film = event_row(_view("basiskele", "bk:3", "Animasyon Filmi", "X"), NOW)
    assert film.genre == "Animasyon Filmi" and "genre" not in film.derived
    for r in (vkv_child, vkv_plain, bk_kind, bk_none, film):
        cells = dict(zip(L.SORT_COLUMNS, L.row_cells(r)))
        assert cells["sessions"].derived is False and cells["source"].derived is False
        assert cells["zaman"].derived is True
        assert cells["target"].text == "—" and cells["target"].derived is True


def test_meta_parts_flag_derived_segments():
    parts = L.meta_parts(row_of(play({"a": sess(5, 20)})))
    assert [(c.text, c.derived) for c in parts[:3]] == [("Kaynak: Biletinial", False),
                                                        ("Kitle: Yetişkin", True),
                                                        ("Tür: Komedi", False)]
    assert L.meta_line(row_of(play({"a": sess(5, 20)}))).startswith(
        "Kaynak: Biletinial  ·  Kitle: Yetişkin  ·  Tür: Komedi")
    assert L.meta_parts(event_row(_view(), NOW))[0].text.startswith("Kaynak: VKV")


def test_legend_text():
    assert L.LEGEND == "normal = kaynaktan alınan, italik = türetilmiş/varsayım"


# ---- 2/6. cells: follow checkbox, link column --------------------------------------------------

def test_follow_cell_is_a_checkbox_and_link_cell_a_button():
    r = row_of(play({"a": sess(5, 20)}), followed_cfg())
    cells = dict(zip(L.SORT_COLUMNS, L.row_cells(r)))
    assert (cells["follow"].text, cells["follow"].kind) == (L.CHECKED, "check")
    assert (cells["link"].text, cells["link"].kind) == ("Oyun ↗", "link")
    nolink = row_of(play({"a": sess(5, 20)}, url=""))
    assert dict(zip(L.SORT_COLUMNS, L.row_cells(nolink)))["link"].kind == "text"
    assert L.SORT_COLUMNS[0] == "follow"
    off = row_of(play({"a": sess(5, 20)}), slug="off")
    assert [r.slug for r in L.sort_rows([off, r], ("follow", "desc"))][0] == "p"
    assert L.next_sort(None, "follow") == ("follow", "desc")


def test_every_column_is_sortable_and_sorting_never_raises():
    rows = [row_of(play({"a": sess(5, 20)})), row_of(play({}), slug="q"),
            event_row(_view(), NOW), event_row(_view(eid="bk:1", source="basiskele", kind=""), NOW)]
    assert [c[0] for c in L.LIST_COLUMNS] == list(L.SORT_COLUMNS)
    for column in L.SORT_COLUMNS:
        for direction in ("asc", "desc"):
            assert len(L.sort_rows(rows, (column, direction))) == 4


def test_session_link_prefers_the_session_page():
    entry = play({"a": sess(5, 20, url="https://example.test/p/1"), "b": sess(6, 20)})
    lines = L.session_lines(entry, NOW)
    assert [ln.url for ln in lines] == ["https://example.test/p/1", ""]
    assert L.session_link(lines[0], "https://example.test/p") == (
        "Seans ↗", "https://example.test/p/1", "")
    text, url, note = L.session_link(lines[1], "https://example.test/p")
    assert (text, url) == ("Oyun ↗", "https://example.test/p") and "seans bağlantısı yok" in note
    assert L.session_link(lines[1], "") == ("", "", "")
    bad = play({"a": {**sess(5, 20), "url": "javascript:alert(1)"}})
    assert L.session_lines(bad, NOW)[0].url == ""              # only http(s) is ever opened


# ---- 6. source links ---------------------------------------------------------------------------

def test_source_links():
    cfg = config_from_dict({"sources": ["https://biletinial.test/mekan/x"]})
    st = {"src_cache": {"basiskele": {"articles": {
        "https://www.basiskele.bel.tr/haber/old": {"pub": "2026-10-01T10:00:00+03:00"},
        "https://www.basiskele.bel.tr/haber/new": {"pub": "2026-11-01T10:00:00+03:00"}}}}}
    assert L.source_links(cfg, st) == {
        "biletinial": "https://biletinial.test/mekan/x",
        "basiskele": "https://www.basiskele.bel.tr/haber/new", "vkv": S.VKV_LIST_URL}
    bare = L.source_links(default_config(), {})
    assert bare["basiskele"] == S.BASISKELE_BASE and bare["biletinial"].startswith("https://")
    nosrc = default_config()
    nosrc.sources = []
    assert "biletinial" not in L.source_links(nosrc, {})


# ---- 9. new-item (seen) store ------------------------------------------------------------------

def test_seen_store_baseline_marks_everything_then_only_later_items_are_new(tmp_path):
    path = tmp_path / "ui.json"
    s = L.SeenStore.load(path)                                 # first run after the upgrade
    assert s.initialised is False and s.is_new("a") is False   # nothing is new before baseline
    assert s.baseline([]) is False and s.initialised is False  # an empty list is no baseline
    assert s.baseline(["a", "b"]) is True and s.initialised
    assert s.is_new("a") is False and s.is_new("c") is True
    assert s.baseline(["x"]) is False                          # once only
    assert s.mark("c") is True and s.is_new("c") is False and s.mark("c") is False
    assert s.mark("") is False
    L.save_ui(path, {"seen": s.to_json()})
    again = L.SeenStore.load(path)
    assert again.initialised and again.ids.keys() == {"a", "b", "c"}
    assert again.is_new("d") is True


def test_seen_store_survives_damaged_files_and_is_bounded(tmp_path):
    for text in ("", "not json", '{"seen": "x"}', "[]"):
        (tmp_path / "ui.json").write_text(text, encoding="utf-8")
        assert L.SeenStore.load(tmp_path / "ui.json").initialised is False
    (tmp_path / "ui.json").write_text('{"seen": ["a", 3, null, "b"]}', encoding="utf-8")
    assert list(L.SeenStore.load(tmp_path / "ui.json").ids) == ["a", "b"]
    big = L.SeenStore([f"i{n}" for n in range(L.SEEN_MAX + 50)])
    assert len(big.to_json()) == L.SEEN_MAX and big.to_json()[-1] == f"i{L.SEEN_MAX + 49}"
    assert L.SeenStore().to_json() is None


def test_new_flag_decides_the_title_style_tag():
    r = row_of(play({"a": sess(5, 20)}))
    r.is_new = True
    assert "new" in L.row_tags(r) and L.row_cells(r)[1].text == "Oyun"   # no "YENİ ·" prefix
    r.is_removed = True
    assert "new" not in L.row_tags(r) and L.row_cells(r)[1].text.startswith("KALKTI · ")
