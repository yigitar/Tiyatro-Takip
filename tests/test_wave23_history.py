"""Wave 23: the scanner-written notification history (state["history"]), the quiet
full / sold-out kinds, the PC "Bildirim geçmişi" tab and the banner cap (no network)."""
from __future__ import annotations

import json
import shutil
import subprocess
import pathlib

import pytest

from tests.test_runner import NOW, WATCH_CFG, Env, mkplay, sess
from tests.test_wave23_portal import LIVE, SEATS, page
from tests.test_wave3c_runner import CFG, at, go, make
from tiyatro import history as H
from tiyatro.models import Event, Status
from tiyatro.notify import PORTAL_TEXT

ROOT = pathlib.Path(__file__).resolve().parent.parent


def texts(env, kind=None):
    return [h["text"] for h in env.saved_state().get("history", [])
            if kind is None or h["kind"] == kind]


def scan(env, n, html):
    env.portal.html = html
    return go(env, n)


# ---- the scanner writes the history itself -------------------------------------------------

def test_portal_facts_are_recorded_with_exact_text_and_quiet_full(monkeypatch):
    env = make(monkeypatch, html=page(245, 590))
    go(env, 0)
    assert sorted(texts(env, "PORTAL_NEW")) == [
        "Yeni etkinlik: KEDİLER SOKAĞI (20 Kas 15:00) · DOLU 590/590",
        "Yeni etkinlik: PÜSKÜLLÜ MUAMMA (20 Kas 20:00) · 345 boş"]
    scan(env, 1, page(245, 587))
    assert "KEDİLER SOKAĞI (20 Kas 15:00) — yer açıldı: 3 boş" in texts(env, "PORTAL_SEATS")
    env.tg.sent.clear()
    scan(env, 2, page(590, 587))                          # Püsküllü fills up: quiet
    assert env.tg.sent == []
    assert texts(env, "FULL") == ["PÜSKÜLLÜ MUAMMA (20 Kas 20:00) — doldu (DOLU 590/590)"]
    for e in env.saved_state()["history"]:
        assert set(e) == {"ts", "key", "kind", "source", "title", "start", "text", "row"}
        assert e["source"] == "basiskele" and e["row"].startswith("bkp")
    n = len(env.saved_state()["history"])
    for k in range(3, 8):                                 # counters moving add nothing
        scan(env, k, page(590, 587 - k))
    assert len(env.saved_state()["history"]) == n


def test_history_is_bounded_and_garbage_is_dropped(monkeypatch):
    import tiyatro.runner as runner
    st = runner._prepare({"history": [{"text": "x", "key": "a"}, "junk", {"key": "b"}, 5]})
    assert [h["key"] for h in st["history"]] == ["a"]
    state = {}
    for i in range(H.MAX_HISTORY + 30):
        H.add(state, kind="NEW_PLAY", source="biletinial", title="t", start=None, text=f"t{i}",
              row="r", key=f"k{i}", now=NOW)
    assert len(state["history"]) == H.MAX_HISTORY and state["history"][-1]["text"] == f"t{H.MAX_HISTORY + 29}"
    H.add(state, kind="NEW_PLAY", source="x", title="t", start=None, text="dup", row="r",
          key=f"k{H.MAX_HISTORY + 29}", now=NOW)
    assert state["history"][-1]["text"] != "dup"


def test_entries_for_every_notice_kind_say_exactly_what_changed():
    ev = lambda kind, **p: Event(kind, f"{kind}:1", "slug", "s1", [], p)   # noqa: E731
    start = "2026-11-19T20:00:00+03:00"
    cases = {
        "ON_SALE": ev("ON_SALE", title="On İkinci Gece", start=start, remaining=3),
        "SRC_NEW": ev("SRC_NEW", title="Evlilik Okulu", start="2026-11-22T14:00:00+03:00", source="basiskele"),
        "SRC_REOPENED": ev("SRC_REOPENED", title="Taş Prenses", start=start, source="vkv"),
        "OPENED_SOLD_OUT": ev("OPENED_SOLD_OUT", title="X", start=start),
    }
    got = {k: H.entries_for(e)[0]["text"] for k, e in cases.items()}
    assert got["ON_SALE"] == "On İkinci Gece (19 Kas 20:00) — bilet satışa çıktı (son 3 bilet)"
    assert got["SRC_NEW"] == "Yeni etkinlik: Evlilik Okulu (22 Kas 14:00)"
    assert got["SRC_REOPENED"] == "Taş Prenses (19 Kas 20:00) — yer açıldı"
    assert "görülmeden tükendi" in got["OPENED_SOLD_OUT"]
    assert H.entries_for(ev("FETCH_FAILING", detail="x")) == []     # health: not a change
    assert H.entries_for(ev("PORTAL_MARKUP", detail="x")) == []


def test_the_vague_portal_wording_no_longer_exists():
    assert "güncellendi" not in " ".join(PORTAL_TEXT.values())
    assert "PORTAL_CHANGED" not in PORTAL_TEXT
    import tiyatro.runner as runner
    assert not any(s in open(runner.__file__, encoding="utf-8").read()
                   for s in ('kind, key = "PORTAL_CHANGED"',))


# ---- sold out / full is quiet ------------------------------------------------------------------

def test_sold_out_after_opening_sends_nothing_but_is_recorded(monkeypatch):
    env = Env(monkeypatch, [mkplay()], WATCH_CFG)
    env.run(NOW)
    env.tg.sent.clear()
    env.web.set(mkplay(sessions=[sess(status=Status.SOLD_OUT)]))
    res = env.run(NOW.replace(minute=5))
    assert [e.kind for e in res.events] == ["OPENED_SOLD_OUT"]    # derived (burst keeps watching) ...
    assert env.tg.sent == []                                      # ... but never sent
    assert any("görülmeden tükendi" in t for t in texts(env, "OPENED_SOLD_OUT"))
    assert not env.saved_state()["outbox"]


def test_on_sale_then_sold_out_records_quietly(monkeypatch):
    env = Env(monkeypatch, [mkplay()], WATCH_CFG)
    env.run(NOW)
    env.web.set(mkplay(sessions=[sess(status=Status.ON_SALE)]))
    env.run(NOW.replace(minute=5))
    assert len(env.tg.sent) == 1 and "Bilet alınabilir" in env.tg.sent[0]
    env.tg.sent.clear()
    env.web.set(mkplay(sessions=[sess(status=Status.SOLD_OUT)]))
    env.run(NOW.replace(minute=10))
    assert env.tg.sent == []
    assert texts(env, "SOLD_OUT") == ["Oyun p (21 Kas 20:00) — seans doldu"]
    assert texts(env, "ON_SALE") == ["Oyun p (21 Kas 20:00) — bilet satışa çıktı"]


# ---- the PC tab (pure logic) ----------------------------------------------------------------------

def test_history_view_logic_filters_sorts_and_counts_unread():
    from pc import history_ui as V
    hist = [
        {"ts": "2026-11-18T10:00:00+03:00", "key": "a", "kind": "PORTAL_NEW", "source": "basiskele", "text": "A", "row": "r1"},
        {"ts": "2026-11-18T12:00:00+03:00", "key": "b", "kind": "ON_SALE", "source": "biletinial", "text": "B", "row": "r2"},
        {"ts": "2026-11-18T11:00:00+03:00", "key": "c", "kind": "FULL", "source": "basiskele", "text": "C", "row": "r1"},
        "junk", {"ts": 5},
    ]
    assert [h["key"] for h in V.visible(hist)] == ["b", "c", "a"]
    assert [h["key"] for h in V.visible(hist, "yeni")] == ["a"]
    assert [h["key"] for h in V.visible(hist, V.ALL, "Başiskele")] == ["c", "a"]
    assert V.unread(hist, "") == 2                      # FULL is quiet: not counted
    assert V.unread(hist, "2026-11-18T10:30:00+03:00") == 1
    assert V.when_text("2026-11-18T12:00:00+03:00") == "18.11 12:00"


def test_history_tab_widget(tmp_path):
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    try:
        from pc import history_ui as V
        hist = [{"ts": f"2026-11-18T1{i}:00:00+03:00", "key": f"k{i}", "kind": "SRC_NEW",
                 "source": "basiskele", "text": f"Yeni etkinlik: E{i}", "row": f"bk:{i}"}
                for i in range(5)]
        opened, unread, changes = [], [], []
        tab = V.HistoryTab(root, lambda: hist, {"read_ts": "", "group": V.ALL, "source": V.ALL},
                           lambda: changes.append(1), opened.append, unread.append)
        tab.pack(fill="both", expand=True)
        root.update()
        kids = tab.tree.get_children()
        assert len(kids) == 5 and unread[-1] == 5
        assert tab.tree.item(kids[0], "values")[3] == "Yeni etkinlik: E4"      # newest first
        n = tab.refresh_count
        tab.refresh()
        assert tab.refresh_count == n                                          # nothing changed: no rebuild
        tab.tree.selection_set(kids[1])
        tab.open_selected()
        assert opened == ["bk:3"]
        assert "E3" in tab.copy_selected()
        tab.mark_read()
        assert unread[-1] == 0 and tab.to_json()["read_ts"] == hist[-1]["ts"] and changes
        tab.group_var.set("doldu")
        tab._filters_changed()
        assert tab.tree.get_children() == ()
    finally:
        root.destroy()


# ---- Telegram "📜 Geçmiş" parity ---------------------------------------------------------------------

@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_telegram_history_screen_matches_the_python_entries():
    hist = [{"ts": f"2026-11-18T{10 + i:02d}:05:00+03:00", "key": f"k{i}", "kind": "SRC_NEW",
             "source": "basiskele", "title": "t", "start": None,
             "text": f"Yeni etkinlik: Evlilik Okulu {i} (22 Kas 14:00)", "row": "r"} for i in range(12)]
    script = ("import { render } from %s; let raw=''; for await (const c of process.stdin) raw+=c;"
              "const j=JSON.parse(raw); process.stdout.write(JSON.stringify(render('hi', j.config, j.state)));"
              % json.dumps((ROOT / "worker" / "src" / "menu.js").as_uri()))
    out = subprocess.run(["node", "--input-type=module", "-e", script],
                         input=json.dumps({"config": {}, "state": {"history": hist}}),
                         capture_output=True, text=True, encoding="utf-8", timeout=60, check=True)
    screen = json.loads(out.stdout)
    lines = [l for l in screen["text"].split("\n")[2:]]
    assert lines == H.telegram_lines(hist)
    assert len(lines) == H.TELEGRAM_LINES == 10
    assert lines[0].endswith("Evlilik Okulu 11 (22 Kas 14:00)")


# ---- Python <-> Worker parity of the portal facts --------------------------------------------------------

def portal_state():
    ev = lambda i, t, reserved, status: {   # noqa: E731
        "id": i, "title": t, "start": "2026-11-20T15:00:00+03:00" if i == "1083" else "2026-11-20T20:00:00+03:00",
        "total": 590, "reserved": reserved, "free": 590 - reserved, "status": status,
        "url": f"https://rezervasyon.basiskele.bel.tr/etkinlik-d/0/{i}/x.aspx", "present": True}
    return {
        "portals": {"basiskele": {"events": {
            "1083": ev("1083", "KEDİLER SOKAĞI", 590, "full"),
            "1084": ev("1084", "PÜSKÜLLÜ MUAMMA", 240, "open"),
            "1090": ev("1090", "GECE", 10, "closed")}}},
        "source_events": {
            "bk:20261120-1500-kediler-sokagi": {
                "id": "bk:20261120-1500-kediler-sokagi", "source": "basiskele", "title": "Kediler Sokağı",
                "start": "2026-11-20T15:00:00+03:00", "venue": "TYGM", "url": "https://www.basiskele.bel.tr/k",
                "full": None, "opens_at": None, "opens_pending": None, "follow_default": False},
            "bkp1084": {
                "id": "bkp1084", "source": "basiskele", "title": "Püsküllü Muamma", "portal_eid": "1084",
                "start": "2026-11-20T20:00:00+03:00", "venue": "TYGM", "url": "https://rezervasyon.basiskele.bel.tr/etkinlik-d/0/1084/x.aspx",
                "full": False, "opens_at": None, "opens_pending": False, "follow_default": False},
            "bk:other": {
                "id": "bk:other", "source": "basiskele", "title": "Başka", "start": "2026-11-21T20:00:00+03:00",
                "venue": "", "url": "https://www.basiskele.bel.tr/o", "full": None, "opens_at": None,
                "opens_pending": None, "follow_default": False}},
    }


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_portal_rez_seats_and_derived_flags_match_between_python_and_worker():
    from pc.viewmodel import build_rows, with_events
    from tiyatro import srcevents as S
    from tiyatro.config import config_from_dict
    state = portal_state()
    cfg = config_from_dict({})
    rows = {r.slug: r for r in with_events(build_rows(state, cfg, set(), set(), NOW),
                                           S.event_views(state, cfg, NOW), NOW)}
    script = ("import { listItems } from %s; let raw=''; for await (const c of process.stdin) raw+=c;"
              "const j=JSON.parse(raw); process.stdout.write(JSON.stringify(listItems(j.config, j.state, j.now)));"
              % json.dumps((ROOT / "worker" / "src" / "menu.js").as_uri()))
    out = subprocess.run(["node", "--input-type=module", "-e", script],
                         input=json.dumps({"config": {}, "state": state, "now": int(NOW.timestamp() * 1000)}),
                         capture_output=True, text=True, encoding="utf-8", timeout=60, check=True)
    items = {i["id"]: i for i in json.loads(out.stdout)}
    assert set(items) == set(rows)
    for slug, row in rows.items():
        it = items[slug]
        assert it["rez"] == row.rez_text, slug
        assert it["seats"] == row.portal_seats, slug
        assert it["portalUrl"] == row.portal_url, slug
        assert sorted(it["derived"]) == sorted(set(row.derived) - {"summary"}), slug
    assert rows["bk:20261120-1500-kediler-sokagi"].rez_text == "doldu"
    assert rows["bk:20261120-1500-kediler-sokagi"].portal_seats == "590/590 dolu (0 boş)"
    assert rows["bkp1084"].rez_text == "açık · 350 boş"
    assert rows["bkp1084"].portal_seats == "240/590 dolu (350 boş)"
    assert "rez" not in rows["bkp1084"].derived             # source-quoted, not derived
    assert rows["bk:other"].portal_seats == ""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_no_telegram_menu_text_repeats_a_url():
    import re
    state = portal_state()
    state["history"] = [{"ts": "2026-11-18T10:00:00+03:00", "key": "a", "kind": "SRC_NEW",
                         "source": "basiskele", "text": "Yeni etkinlik: A", "row": "r"}]
    script = ("import { render, h } from %s; let raw=''; for await (const c of process.stdin) raw+=c;"
              "const j=JSON.parse(raw); const out=[];"
              "for (const r of ['m','pl:0','tl','up','src','rf','hi',...Object.keys(j.state.source_events).map((id)=>'e:'+h(id)+':0')]) out.push(render(r, j.config, j.state, j.now).text);"
              "process.stdout.write(JSON.stringify(out));" % json.dumps((ROOT / "worker" / "src" / "menu.js").as_uri()))
    out = subprocess.run(["node", "--input-type=module", "-e", script],
                         input=json.dumps({"config": {}, "state": state, "now": int(NOW.timestamp() * 1000)}),
                         capture_output=True, text=True, encoding="utf-8", timeout=60, check=True)
    texts_ = json.loads(out.stdout)
    assert len(texts_) >= 9
    for t in texts_:
        urls = re.findall(r"https?://[^\s<>\"]+", t)
        assert len(urls) == len(set(urls)), t


# ---- detail screens (PC "Bilgiler", Telegram e:) -----------------------------------------------------------

def _row_for(slug):
    from pc.viewmodel import with_events
    from tiyatro import srcevents as S
    from tiyatro.config import config_from_dict
    state = portal_state()
    cfg = config_from_dict({})
    return {r.slug: r for r in with_events([], S.event_views(state, cfg, NOW), NOW)}[slug]


def test_pc_detail_facts_show_seats_and_the_direct_reservation_link():
    pytest.importorskip("tkinter")
    from pc.ui import App
    facts = App._facts(type("A", (), {"portal_url_for": App.portal_url_for,
                                      "service": None})(), _row_for("bkp1084"))
    d = {k: v for k, v, _derived in facts}
    assert d["Koltuk"] == "240/590 dolu (350 boş)"
    link = "https://rezervasyon.basiskele.bel.tr/etkinlik-d/0/1084/x.aspx"
    assert d["Rezervasyon yap"] == link and "Etkinlik sayfası" not in d     # one link, not two
    assert d["Durum"] == "rezervasyon açık · 350 boş"
    quoted = {k: derived for k, _v, derived in facts}
    assert quoted["Koltuk"] is False and quoted["Durum"] is False             # source-quoted, not derived
    other = {k for k, _v, _d in App._facts(type("A", (), {"portal_url_for": App.portal_url_for,
                                                          "service": None})(), _row_for("bk:other"))}
    assert "Koltuk" not in other


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_telegram_event_screen_shows_seats_and_the_direct_link():
    from tiyatro.config import config_from_dict
    state = portal_state()
    script = ("import { render, h } from %s; let raw=''; for await (const c of process.stdin) raw+=c;"
              "const j=JSON.parse(raw); const s=render('e:'+h('bkp1084')+':0', j.config, j.state, j.now);"
              "process.stdout.write(JSON.stringify(s));" % json.dumps((ROOT / "worker" / "src" / "menu.js").as_uri()))
    out = subprocess.run(["node", "--input-type=module", "-e", script],
                         input=json.dumps({"config": {}, "state": state, "now": int(NOW.timestamp() * 1000)}),
                         capture_output=True, text=True, encoding="utf-8", timeout=60, check=True)
    s = json.loads(out.stdout)
    link = "https://rezervasyon.basiskele.bel.tr/etkinlik-d/0/1084/x.aspx"
    assert "Koltuk: 240/590 dolu (350 boş)" in s["text"]
    assert f"Rezervasyon yap: {link}" in s["text"]
    buttons = [b for row in s["keyboard"] for b in row if b.get("url")]
    assert [b["url"] for b in buttons] == [link]
