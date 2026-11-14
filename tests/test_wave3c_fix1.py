"""Wave 3c fix round 1: volatile text, false openings, garbled pages, confirmation."""
from __future__ import annotations

import time
from datetime import timedelta

import pytest

from tests.test_wave3c_portals import page, real_empty
from tests.test_wave3c_runner import (CARD, at, go, kinds, make, opened_html, pm, pstate)
from tiyatro.portals import parse_portal

CLOCK = "<p>Güncelleme: {}</p><p>Ziyaretçi sayısı: {}</p>"


def clocked(hhmm, n=1000, *titles):
    # the id belongs to the event, not to its position, so reordering keeps every URL
    return page("".join(CARD.format(t=t, i=sum(map(ord, t))) for t in (titles or ("Konser",)))
                + CLOCK.format(hhmm, n))


# ---- I1 signature stability --------------------------------------------------------------

def test_signature_ignores_clock_counter_order_and_volatile_params():
    a = parse_portal(clocked("14:35", 1000, "Konser", "Pinokyo"))
    b = parse_portal(clocked("14:40", 1001, "Konser", "Pinokyo"))
    c = parse_portal(clocked("14:45", 1002, "Pinokyo", "Konser"))
    assert a.signature == b.signature == c.signature

    def mk(href):
        return parse_portal(page(
            f'<div><h4>Konser</h4><a href="{href}">Rezervasyon Yap</a></div>')).signature

    base = mk("/EtkinlikDetay.aspx?id=3")
    assert mk("/EtkinlikDetay.aspx?id=3&ts=1699999999") == base
    assert mk("/EtkinlikDetay.aspx?ts=5&id=3&_=77") == base
    assert mk("/(S(abc123xyz))/EtkinlikDetay.aspx?id=3") == base
    assert mk("/EtkinlikDetay.aspx?id=4") != base
    new = parse_portal(clocked("14:35", 1000, "Konser", "Pinokyo", "Yeni Etkinlik"))
    assert new.signature != a.signature


def test_no_item_signature_drops_clock_lines():
    def mk(t):
        return parse_portal(page(
            "<p>Yeni duyuru: salon dolu olduğundan kayıtlar kapanmıştır.</p>"
            f"<p>Saat: {t}</p><p>Başka bir uzun açıklama satırı burada yer alıyor.</p>"))

    assert mk("14:35:01").signature == mk("14:35:09").signature
    assert mk("14:35:01").empty is False


def test_probe_open_with_changing_clock_alerts_once(monkeypatch):
    env = make(monkeypatch)
    go(env, 0)
    seq = []
    for n, hhmm in enumerate(["14:35", "14:35", "14:40", "14:45", "14:50"], 1):
        env.portal.html = clocked(hhmm, 1000 + n, "Konser")
        seq.append(kinds(go(env, n)))
    assert seq == [[], ["PORTAL_OPENED"], [], [], []]
    assert len(pm(env)) == 1


def test_changed_once_for_new_item_and_cooldown(monkeypatch):
    env = make(monkeypatch)
    go(env, 0)
    env.portal.html = opened_html("Konser")
    go(env, 1)
    go(env, 2)
    env.portal.html = opened_html("Konser", "Pinokyo")
    assert kinds(go(env, 3)) == ["PORTAL_CHANGED"]
    assert pstate(env)["last_changed_alert"] == at(3).isoformat()
    env.portal.html = opened_html("Konser")              # an event disappears: churn
    assert kinds(go(env, 4)) == []                       # inside the 6 h cooldown
    assert len(pstate(env)["items"]) == 1                # but the change is stored
    env.portal.html = opened_html("Konser", "Dördüncü")  # a NEW event is never swallowed
    assert kinds(go(env, 5)) == ["PORTAL_CHANGED"]
    env.portal.html = opened_html("Dördüncü")
    res = env.run(at(5) + timedelta(hours=6, minutes=1), portal_fetch_fn=env.portal.fetch)
    assert kinds(res) == ["PORTAL_CHANGED"]              # churn alerts again after 6 h


# ---- I2 classification ------------------------------------------------------------------

OPEN = {
    "cards": CARD.format(t="Kuğu Gölü Balesi", i=1) + CARD.format(t="Pinokyo Çocuk Oyunu", i=2),
    "table": "<table><tr><td>Sirk Gösterisi</td><td>20.11.2026</td><td>"
             '<a href="rezervasyon.aspx?e=7">Rezervasyon Yap</a></td></tr></table>',
    "li-links": '<ul><li><a href="/e/12">Kuğu Gölü Balesi 15 Kasım</a></li></ul>',
    "li-plain": "<ul><li>Kuğu Gölü Balesi - 15 Kasım 15:00</li>"
                "<li>Pinokyo Çocuk Oyunu - 16 Kasım 14:00</li></ul>",
    "postback": "<div><a href=\"javascript:__doPostBack('ctl00$c$rpt$ctl00$lnk','')\">"
                "Kuğu Gölü Balesi</a></div>",
    "single-card": CARD.format(t="Tek Etkinlik", i=9),
}
EMPTY = {
    "none": "<p>Aktif etkinlik bulunmamaktadır.</p>",
    "not-found": "<p>Kayıt bulunamadı. Lütfen daha sonra tekrar deneyiniz.</p>"
                 "<p>Şu anda rezervasyona açık etkinlik bulunmamaktadır.</p>",
    "not-found-plus-chatter": "<p>Kayıt bulunamadı. Lütfen daha sonra tekrar deneyiniz.</p>"
                              "<p>Rezervasyonlar belediyemizce takip edilmektedir ve duyurulur.</p>",
    "error": "<p>Bir hata oluştu. Lütfen daha sonra tekrar deneyin.</p>"
             "<p>Sunucu hatası: işleminiz gerçekleştirilemedi.</p>",
    "maintenance": "<p>Sitemiz bakım çalışması nedeniyle geçici olarak hizmet verememektedir.</p>"
                   "<p>Erişim bakım süresince kapalıdır, anlayışınız için teşekkürler.</p>",
    "yellow-screen": "<h1>Server Error in '/' Application.</h1><h2>Runtime Error</h2>"
                     "<p>Description: An application error occurred on the server. The current "
                     "custom error settings for this application prevent the details.</p>",
    "waf": "<p>Access denied. Your request was blocked by the firewall (forbidden).</p>"
           "<p>Doğrulama için captcha çözmeniz gerekmektedir, lütfen bekleyin.</p>",
    "cookie": "<div>Sitemizde çerez kullanılmaktadır. Gizlilik politikamızı inceleyin.</div>"
              '<a href="/gizlilik">Detaylı bilgi</a><a href="#">Kabul et</a>',
    "pager": '<a href="/etkinlikler?page=2">Sonraki sayfa</a><a href="/etkinlikler?page=1">1</a>'
             "<a href=\"javascript:__doPostBack('gv','Page$2')\">2</a>"
             '<a href="/etkinlikler?page=1">Önceki</a>',
    "footer-clock": "<p>Ziyaretçi sayısı: 123456</p><p>Saat: 14:35:10</p>"
                    "<p>Güncelleme: 13.11.2026 14:35</p>",
}


@pytest.mark.parametrize("name", OPEN)
def test_open_classification(name):
    assert parse_portal(page(OPEN[name])).empty is False, name


@pytest.mark.parametrize("name", EMPTY)
def test_empty_classification(name):
    snap = parse_portal(page(EMPTY[name]))
    assert snap.empty is True and snap.items == [], (name, snap.items, snap.lines)


# ---- I3 garbage + confirmation ------------------------------------------------------------

GARBLED = "".join("\x01\x02\x03<<b \x00 broken <a href= \x1f junk text here !!\n"
                  for _ in range(50))


def test_garbled_page_is_invalid_not_content():
    snap = parse_portal(page(GARBLED))
    assert snap.valid is False and snap.empty is True
    assert parse_portal(real_empty()).valid is True
    assert parse_portal("a\x00b").valid is False
    assert parse_portal("ok " + "�" * 500 + " tail" * 50).valid is False


def test_garbled_fetch_counts_as_failure_never_opens(monkeypatch):
    env = make(monkeypatch)
    go(env, 0)
    env.portal.html = page(GARBLED)
    for n in (1, 2, 3):
        res = go(env, n)
    assert kinds(res) == ["PORTAL_FAILING"] and pstate(env)["empty"] is True
    assert pstate(env)["fail_streak"] == 3


def test_blip_does_not_alert(monkeypatch):
    env = make(monkeypatch)
    go(env, 0)
    env.portal.html = opened_html("Konser")
    assert kinds(go(env, 1)) == []
    rec = pstate(env)
    assert rec["pending_signature"] and rec["empty"] is True and rec["pending_since"]
    env.portal.html = real_empty()
    assert kinds(go(env, 2)) == [] and "pending_signature" not in pstate(env)
    env.portal.html = opened_html("Konser")
    assert kinds(go(env, 3)) == []                      # a new blip needs its own confirmation
    assert pm(env) == []


def test_confirmed_opening_alerts_once(monkeypatch):
    env = make(monkeypatch)
    go(env, 0)
    env.portal.html = opened_html("Konser")
    assert kinds(go(env, 1)) == []
    assert kinds(go(env, 2)) == ["PORTAL_OPENED"]
    assert "pending_signature" not in pstate(env) and pstate(env)["empty"] is False
    assert kinds(go(env, 3)) == [] and len(pm(env)) == 1


def test_different_content_between_scans_restarts_confirmation(monkeypatch):
    env = make(monkeypatch)
    go(env, 0)
    env.portal.html = opened_html("Alfa Gösterisi")
    go(env, 1)
    env.portal.html = opened_html("Beta Gösterisi")
    assert kinds(go(env, 2)) == []
    assert kinds(go(env, 3)) == ["PORTAL_OPENED"]


def test_first_ever_non_empty_observation_also_needs_confirmation(monkeypatch):
    env = make(monkeypatch, html=opened_html("Konser"))
    assert kinds(go(env, 0)) == [] and pm(env) == []
    assert kinds(go(env, 1)) == ["PORTAL_OPENED"]


# ---- size / depth -------------------------------------------------------------------

def test_huge_deeply_nested_page_is_fast():
    html = "<div>" * 400_000 + "<a href='/e/1'>Rezervasyon Yap</a>" + "x" * 1_000_000
    assert len(html) > 2_000_000
    start = time.monotonic()
    snap = parse_portal(html)
    assert time.monotonic() - start < 1.0
    assert len(snap.signature) == 40
    start = time.monotonic()
    parse_portal("</div>" * 300_000 + "<li>" * 100_000)
    assert time.monotonic() - start < 1.0
