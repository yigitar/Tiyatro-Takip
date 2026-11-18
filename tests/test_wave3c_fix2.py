"""Wave 3c fix round 2: event links beat link-text words, new events beat the cooldown."""
from __future__ import annotations

from datetime import timedelta

import pytest

from tests.test_wave3c_portals import page
from tests.test_wave3c_runner import CARD, at, go, go2, kinds, make, pm, pstate
from tiyatro.portals import parse_portal


def opened_html(*titles):
    """Cards whose URL id belongs to the title (so reordering/removal keeps every URL)."""
    return page("".join(CARD.format(t=t, i=sum(map(ord, t))) for t in titles))


# ---- 1. event path overrides link-text exclusions ---------------------------------------

@pytest.mark.parametrize("title", ["Çerez Kutusu çocuk atölyesi",
                                   "Gizlilik ve Dijital Dünya Söyleşisi",
                                   "KVKK Farkındalık Semineri", "Oturum Açma Sanatı"])
def test_event_titles_with_consent_words_are_open(title):
    snap = parse_portal(page(f'<ul><li><a href="/etkinlik/12">{title}</a></li></ul>'))
    assert snap.empty is False and [i["title"] for i in snap.items] == [title]


@pytest.mark.parametrize("button", ["Detaylı Bilgi", "Daha Fazla", "Kabul Et"])
def test_card_with_generic_button_to_an_event_path_is_open(button):
    html = f'<div class="card"><h3>Kukla Atölyesi</h3><a href="/etkinlik/12">{button}</a></div>'
    snap = parse_portal(page(html))
    assert snap.empty is False
    assert [(i["title"], i["url"].rsplit("/", 1)[-1]) for i in snap.items] == [
        ("Kukla Atölyesi", "12")]


def test_cookie_banner_links_stay_excluded():
    for href in ("/gizlilik", "/cerez-politikasi", "#"):
        html = ("<div>Sitemizde çerez kullanılmaktadır. Gizlilik politikamızı inceleyin.</div>"
                f'<a href="{href}">Detaylı bilgi</a><a href="#">Kabul et</a>')
        snap = parse_portal(page(html))
        assert snap.empty is True and snap.items == [], href


def test_pager_links_are_excluded_even_under_an_event_path():
    html = ('<a href="/etkinlik/liste?page=2">Sonraki sayfa</a><a href="/etkinlik/liste?p=1">1</a>'
            '<a href="/etkinlik/liste?p=0">Önceki</a>')
    assert parse_portal(page(html)).empty is True


# ---- 3. dated lines are never "negative" -------------------------------------------------

def test_dated_plain_lines_with_negative_words_are_open():
    html = ("<p>22 Kasım 16:00 Çerez atölyesi çocuklar için ücretsiz</p>"
            "<p>20.11.2026 Gizlilik ve dijital dünya söyleşisi</p>")
    assert parse_portal(page(html)).empty is False
    still = ("<p>Kayıt bulunamadı. Lütfen daha sonra tekrar deneyiniz.</p>"
             "<p>Bir hata oluştu, işlem gerçekleştirilemedi.</p>")
    assert parse_portal(page(still)).empty is True


# ---- 2. wave 23: the generic watcher never announces a changed list --------------------------

def test_new_events_after_the_opening_are_silent_for_the_generic_watcher(monkeypatch):
    env = make(monkeypatch)
    go(env, 0)
    env.portal.html = opened_html("Alfa Gösterisi")
    assert kinds(go2(env, 1)) == ["PORTAL_OPENED"]
    env.portal.html = opened_html("Alfa Gösterisi", "Beta Gösterisi")
    assert kinds(go(env, 5)) == []
    env.portal.html = opened_html("Alfa Gösterisi", "Beta Gösterisi", "Gama Gösterisi")
    res = env.run(at(5) + timedelta(hours=2), portal_fetch_fn=env.portal.fetch)
    assert kinds(res) == []
    assert len(pm(env)) == 1                                       # only the opening was sent
    assert len(pstate(env)["items"]) == 3                          # the list itself is tracked


def test_a_reorder_of_many_items_is_still_stored_without_a_notice(monkeypatch):
    env = make(monkeypatch)
    go(env, 0)
    names = [f"Etkinlik numara {i:02d} gösterisi" for i in range(30)]
    env.portal.html = opened_html(*names)
    go2(env, 1)
    assert len(pstate(env)["items"]) == 20                         # stored list is capped
    env.portal.html = opened_html(*reversed(names))
    assert kinds(go(env, 3)) == []
