"""Wave 3c: portal strip (viewmodel, service activity log, headless Tk smoke)."""
from __future__ import annotations

import copy
from datetime import timedelta

import pytest

from pc import activity as act
from pc.service import Service
from pc.viewmodel import format_event_for_desktop, headline, portal_views
from tests.test_pc_service import NOW, make, tick_at
from tests.test_runner import WATCH_CFG, mkplay, sess
from tests.test_wave3c_portals import page, real_empty
from tests.test_wave3c_runner import CARD, PORTAL_URL, PortalWeb, opened_html
from tiyatro.claims import ClaimStore
from tiyatro.config import config_from_dict
from tiyatro.fetcher import FetchError
from tiyatro.models import ISTANBUL, Event, Status

LABEL = "Başiskele rezervasyon portalı"


def cfg(extra=None):
    return config_from_dict(extra or {})


def rec(**kw):
    base = {"signature": "s" * 40, "empty": True, "lines": [], "items": [],
            "first_seen": NOW.isoformat(), "last_change": NOW.isoformat(),
            "last_check": NOW.isoformat(), "fail_streak": 0}
    base.update(kw)
    return base


# ---- viewmodel ------------------------------------------------------------------------

def test_portal_view_empty_open_and_unchecked():
    now = NOW.replace(hour=14, minute=40)
    chk = NOW.replace(hour=14, minute=35).isoformat()
    [v] = portal_views(cfg(), {"portals": {"basiskele": rec(last_check=chk)}}, now)
    assert v.text == f"🏛️ {LABEL}: boş · son kontrol 14:35"
    assert v.url == PORTAL_URL and v.state == "empty" and v.id == "basiskele"
    items = [{"title": "A", "url": "u"}, {"title": "B", "url": "v"}]
    [v] = portal_views(cfg(), {"portals": {"basiskele": rec(
        empty=False, items=items, last_check=chk)}}, now)
    assert v.text == f"🏛️ {LABEL}: 2 etkinlik açık · son kontrol 14:35" and v.state == "open"
    [v] = portal_views(cfg(), {"portals": {"basiskele": rec(empty=False, items=[],
                                                            last_check=chk)}}, now)
    assert v.text == f"🏛️ {LABEL}: açık · son kontrol 14:35"
    [v] = portal_views(cfg(), {}, now)
    assert v.text == f"🏛️ {LABEL}: henüz kontrol edilmedi" and v.state == "unknown"
    [v] = portal_views(cfg(), {"portals": {"basiskele": rec(empty=None, last_check=None,
                                                            fail_streak=2)}}, now)
    assert "okunamadı (2. kez)" in v.text and v.state == "failing"


def test_portal_view_old_check_shows_date_and_failure_note():
    now = NOW.replace(hour=9)
    old = (NOW - timedelta(days=2)).replace(hour=14, minute=35).isoformat()
    [v] = portal_views(cfg(), {"portals": {"basiskele": rec(last_check=old,
                                                            fail_streak=4)}}, now)
    assert "son kontrol 11.11 14:35" in v.text and "okunamadı (4. kez)" in v.text
    assert v.state == "failing"


def test_portal_views_skip_disabled_and_use_names_of_other_portals():
    c = cfg({"portals": [
        {"id": "basiskele", "name": "x", "url": PORTAL_URL, "enabled": False},
        {"id": "diger", "name": "Diğer Portal", "url": "https://d.example/", "enabled": True}]})
    [v] = portal_views(c, {}, NOW)
    assert v.id == "diger" and v.text.startswith("🏛️ Diğer Portal:")
    assert portal_views(cfg({"portals": []}), {}, NOW) == []


@pytest.mark.parametrize("bad", [None, [], "x", {"basiskele": "junk"}, {"basiskele": {}}])
def test_portal_views_tolerate_garbage_state(bad):
    [v] = portal_views(cfg(), {"portals": bad}, NOW)
    assert v.state in ("unknown", "failing")


def test_headline_and_desktop_text_for_portal_events():
    ev = Event("PORTAL_OPENED", "K", "", None, [], {
        "portal_id": "basiskele", "title": "x", "url": PORTAL_URL,
        "items": [{"title": "Konser", "url": "https://x.test/1"}], "lines": []})
    assert "etkinlik açıldı" in headline(ev) and LABEL in headline(ev)
    title, text = format_event_for_desktop(ev)
    assert "Konser" in text and PORTAL_URL in text and "<" not in text
    assert act.EVENT_CATEGORY["PORTAL_OPENED"] == act.CAT_TICKET == "Bilet açılışı"
    assert "PORTAL_CHANGED" not in act.EVENT_CATEGORY      # wave 23: the vague notice is gone
    assert "PORTAL_FAILING" in act.WARNING_KINDS


# ---- service -------------------------------------------------------------------------

def make_portal(monkeypatch, tmp_path, html=None):
    e = make(monkeypatch, tmp_path)
    e.portal = PortalWeb(html)
    e.svc = Service(e.store, e.tg, ClaimStore(e.store, "pc", e.clock), clock=e.clock,
                    fetch_fn=e.web.fetch, sleep=lambda s: None, local_dir=tmp_path,
                    rand=lambda a, b: 0.0, portal_fetch_fn=e.portal.fetch)
    e.notes = []
    e.svc.on_notify(lambda t, x, evs: e.notes.append((t, x, evs)))
    return e


def entries(e, cat=None):
    got = list(reversed(e.svc.activity.load()))
    return [x for x in got if cat is None or x["cat"] == cat]


def portal_lines(e, cat):
    return [x for x in entries(e, cat) if "portal" in x["title"].lower()]


def test_service_exposes_portal_views_and_logs_quiet_checks(monkeypatch, tmp_path):
    e = make_portal(monkeypatch, tmp_path)
    assert e.svc.portals()[0].state == "unknown"
    tick_at(e, NOW)
    [v] = e.svc.portals()
    assert v.state == "empty" and "boş" in v.text
    [first] = portal_lines(e, "Liste kontrolü")
    assert first["title"] == f"{LABEL}: boş" and first["level"] == "info"
    tick_at(e, NOW + timedelta(minutes=5))
    again = portal_lines(e, "Liste kontrolü")[-1]
    assert again["detail"].splitlines()[0] == "değişiklik yok"
    assert act.is_quiet(again)


def test_service_logs_opening_as_ticket_event_and_notifies(monkeypatch, tmp_path):
    e = make_portal(monkeypatch, tmp_path)
    tick_at(e, NOW)
    e.portal.html = opened_html("Kuğu Gölü Balesi")
    tick_at(e, NOW + timedelta(minutes=5))        # first sight: held back for confirmation
    assert not any(x for x in entries(e, "Bilet açılışı") if "portal" in x["title"].lower())
    res = tick_at(e, NOW + timedelta(minutes=10))
    assert [ev.kind for ev in res.events if ev.kind.startswith("PORTAL")] == ["PORTAL_OPENED"]
    [opened] = [x for x in entries(e, "Bilet açılışı") if "portal" in x["title"].lower()]
    assert "etkinlik açıldı" in opened["title"]
    scan = portal_lines(e, "Liste kontrolü")[-1]
    assert scan["title"] == f"{LABEL}: 1 etkinlik açık"
    assert scan["detail"].startswith("değişiklik var")
    assert any("Kuğu Gölü" in x for _t, x, _e in e.notes)
    [v] = e.svc.portals()
    assert v.state == "open" and "1 etkinlik açık" in v.text


def test_service_logs_close_and_failures(monkeypatch, tmp_path):
    e = make_portal(monkeypatch, tmp_path, html=opened_html("Konser"))
    tick_at(e, NOW)
    tick_at(e, NOW + timedelta(minutes=5))       # confirmation scan: now it is open
    e.portal.html = real_empty()
    tick_at(e, NOW + timedelta(minutes=10))
    closed = portal_lines(e, "Liste kontrolü")[-1]
    assert closed["title"] == f"{LABEL}: boş"
    assert closed["detail"].startswith("değişiklik var: etkinlik listesi boşaldı")
    e.portal.error = FetchError("HTTP 503")
    for n in (3, 4, 5):
        tick_at(e, NOW + timedelta(minutes=5 * n))
    errs = portal_lines(e, "Hata/Uyarı")
    assert [x["level"] for x in errs[:3]] == ["warn"] * 3
    assert "okunamadı (1. kez" in errs[0]["title"] and "3. kez" in errs[2]["title"]
    assert "Biletinial" in errs[0]["detail"]
    # the 3rd failed check also fires PORTAL_FAILING, which is logged as a warning too
    assert any("okunamıyor" in x["title"] and x["level"] == "warn" for x in errs)
    assert e.svc.portals()[0].state == "failing"


def test_service_without_portal_fetch_never_touches_portals(monkeypatch, tmp_path):
    e = make(monkeypatch, tmp_path)
    tick_at(e, NOW)
    assert e.svc.portals()[0].state == "unknown"
    assert portal_lines(e, "Liste kontrolü") == []


def test_manual_refresh_checks_and_logs_portals_once(monkeypatch, tmp_path):
    e = make_portal(monkeypatch, tmp_path)
    tick_at(e, NOW)
    n = len(e.portal.calls)
    lines = len(portal_lines(e, "Liste kontrolü"))
    e.clock.set(NOW + timedelta(minutes=1))
    e.svc.refresh_now()  # manual refresh is a normal scan: one more check
    assert len(e.portal.calls) == n + 1
    assert len(portal_lines(e, "Liste kontrolü")) == lines + 1


def test_remote_config_change_with_portals_is_unaffected(monkeypatch, tmp_path):
    e = make_portal(monkeypatch, tmp_path)
    tick_at(e, NOW)
    raw = copy.deepcopy(e.store.files["config.json"][0])
    raw["paused"] = True
    e.store.files["config.json"] = (raw, "cfg2")
    tick_at(e, NOW + timedelta(minutes=5))
    assert any(x["title"].startswith("Uzaktan") for x in entries(e, "Takip"))
    assert not any("portal" in x["title"].lower() for x in entries(e, "Takip"))


# ---- headless Tk smoke -----------------------------------------------------------------

tk = pytest.importorskip("tkinter")
from tests.test_pc_app_smoke import tk_root, ui  # noqa: E402,F401  (fixtures)


def _views(state="empty", text=None):
    from pc.viewmodel import PortalView
    return [PortalView("basiskele", text or f"🏛️ {LABEL}: boş · son kontrol 14:35",
                       PORTAL_URL, state)]


def _basiskele_sources():
    import tiyatro.sources as S
    from tiyatro.models import ISTANBUL
    from datetime import datetime
    st = {}
    S.record_check(st, "basiskele", datetime(2026, 11, 15, 12, 0, tzinfo=ISTANBUL), True, items=[1])
    return lambda now=None: S.describe(st, datetime(2026, 11, 15, 12, 1, tzinfo=ISTANBUL))


def test_portal_is_merged_into_the_basiskele_source_row_with_an_open_link(ui):
    ui.svc.sources = _basiskele_sources()
    ui.svc.portals = lambda now=None: _views()
    ui.refresh_rows()
    assert len(ui.source_labels) == 1                       # no separate portal strip
    text = ui.source_labels[0].cget("text")
    assert "Başiskele" in ui.source_titles[0].cget("text")
    assert text.endswith("Rezervasyon portalı: boş")
    assert [b.cget("text") for b in ui.source_buttons] == ["Portalı aç"]
    ui.source_buttons[0].invoke()
    assert ui.opened == [PORTAL_URL]
    assert not hasattr(ui, "portal_bar")


def test_portal_row_updates_only_on_change_and_survives_missing_source_row(ui):
    ui.svc.sources = _basiskele_sources()
    ui.svc.portals = lambda now=None: _views()
    ui.refresh_rows()
    first = ui.source_labels[0]
    ui.refresh_rows()
    assert ui.source_labels[0] is first                     # not rebuilt on every tick
    ui.svc.portals = lambda now=None: _views("open", f"🏛️ {LABEL}: 2 etkinlik açık")
    ui.refresh_rows()
    assert ui.source_labels[0].cget("text").endswith("Rezervasyon portalı: 2 etkinlik açık")
    # a portal with no source record yet still gets its own row (and link)
    ui.svc.sources = lambda now=None: []
    ui.refresh_rows()
    assert len(ui.source_labels) == 1 and ui.source_buttons
    ui.svc.portals = lambda now=None: []
    ui.refresh_rows()
    assert ui.source_labels == [] and ui.source_bar.winfo_manager() == ""


def test_strip_survives_a_failing_service_call(ui):
    def boom(now=None):
        raise RuntimeError("x")
    ui.svc.portals = boom
    ui.refresh_rows()                                # must not raise


def test_plain_fake_service_without_portals_method_still_works(ui):
    ui.refresh_rows()
    assert ui.source_buttons == []


def test_production_entry_point_enables_portal_checks():
    """The app's main() builds the Service itself (it opens a window, so it is not run in
    tests): make sure it injects the real fetcher, otherwise portals would never be read."""
    import inspect
    from pc import ui as appmod
    src = inspect.getsource(appmod.main)
    assert "portal_fetch_fn=fetch" in src
