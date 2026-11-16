"""Wave 17: Başiskele program discovery does not depend on the article title alone (content-based
fallback, cached and capped) and a source-health warning replaces a silent check mark."""
from __future__ import annotations

from datetime import datetime, timedelta

from pc import app_logic as L
from tiyatro import sources as SRC
from tiyatro import srcevents as S
from tiyatro.config import config_from_dict
from tiyatro.fetcher import FetchError, FetchResult
from tiyatro.models import ISTANBUL
from tiyatro.runner import _apply_sources
from tiyatro.state import empty_state

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=ISTANBUL)
BASE = S.BASISKELE_BASE

EVENT_HTML = ('<span id="ContentPlaceHolder1_lbaciklama"><p>Aralık ayının ilk oyunu, 3 Aralık Perşembe '
              'günü saat 20.00’de Kültür Merkezi’nde sahnelenecek “Kış Masalı” adlı tiyatro oyunu '
              'olacak.</p></span>')
NEWS_HTML = ('<span id="ContentPlaceHolder1_lbaciklama"><p>Başkan bugün okulu ziyaret etti. '
             'Çocuklar için yeni oyun parkı açıldı.</p></span>')


def item(title, n, desc="", days=2):
    pub = (NOW - timedelta(days=days)).strftime("%a, %d %b %Y 10:00:00 GMT")
    return (f"<item><title>{title}</title><link>/haber/1/{n}/x.aspx</link><pubDate>{pub}</pubDate>"
            f"<description>{desc}</description></item>")


def rss(*items):
    return '<?xml version="1.0"?><rss version="2.0"><channel>' + "".join(items) + "</channel></rss>"


class Web:
    def __init__(self, feed, pages=None):
        self.feed, self.pages, self.calls = feed, pages or {}, []

    def fetch(self, url):
        self.calls.append(url)
        if url == S.BASISKELE_RSS:
            return FetchResult(self.feed, 200, 1)
        if url in self.pages:
            return FetchResult(self.pages[url], 200, 1)
        raise FetchError("HTTP 404")


def url(n):
    return f"{BASE}/haber/1/{n}/x.aspx"


def test_normal_title_is_the_fast_path_no_extra_fetch():
    web = Web(rss(item("Aralık Ayı Kültür Sanat Programı", 1), item("Okulda tören", 2)),
              {url(1): EVENT_HTML})
    scan = S.scan_basiskele(web.fetch, None, NOW)
    assert web.calls == [S.BASISKELE_RSS, url(1)] and scan.extra_fetches == 0
    assert [l.title for l in scan.listings] == ["Kış Masalı"] and "via" not in scan.articles[url(1)]


def test_renamed_title_is_found_by_content_when_the_description_hints_culture():
    feed = rss(item("Aralıkta sahne ışıkları yanıyor", 7, "Çocuklar ve yetişkinler için tiyatro, "
                    "söyleşi ve sergi dolu bir ay"), item("Okulda tören", 8, "Başkan okulu gezdi"))
    web = Web(feed, {url(7): EVENT_HTML, url(8): NEWS_HTML})
    scan = S.scan_basiskele(web.fetch, None, NOW)
    assert web.calls == [S.BASISKELE_RSS, url(7)]                  # read once, parsed once
    assert url(8) not in web.calls                              # unrelated news is never fetched
    assert scan.extra_fetches == 1
    assert [l.title for l in scan.listings] == ["Kış Masalı"]
    assert scan.articles[url(7)]["via"] == "content"
    assert scan.checked[url(7)]["ok"] is True


def test_hinted_item_without_an_event_sentence_is_rejected_and_remembered():
    feed = rss(item("Çocuk oyun parkı açıldı", 9, "Çocuklar için yeni oyun alanı"))
    web = Web(feed, {url(9): NEWS_HTML})
    first = S.scan_basiskele(web.fetch, None, NOW)
    assert first.listings == [] and first.articles == {} and first.extra_fetches == 1
    assert first.checked[url(9)]["ok"] is False
    web.calls.clear()
    again = S.scan_basiskele(web.fetch, first.articles, NOW + timedelta(minutes=5), first.checked)
    assert web.calls == [S.BASISKELE_RSS] and again.extra_fetches == 0     # cached: no refetch


def test_accepted_article_is_kept_via_the_cache_and_costs_one_fetch_per_scan_afterwards():
    feed = rss(item("Aralıkta sahne ışıkları yanıyor", 7, "tiyatro dolu bir ay"))
    web = Web(feed, {url(7): EVENT_HTML})
    first = S.scan_basiskele(web.fetch, None, NOW)
    web.calls.clear()
    again = S.scan_basiskele(web.fetch, first.articles, NOW + timedelta(minutes=5), first.checked)
    assert web.calls == [S.BASISKELE_RSS, url(7)] and again.extra_fetches == 0
    assert len(again.listings) == 1 and again.articles[url(7)]["via"] == "content"


def test_old_items_and_the_per_scan_cap():
    feed = rss(item("Eski sahne haberi", 20, "tiyatro", days=60),
               *[item(f"Sahne haberi {n}", 30 + n, "tiyatro gösterisi") for n in range(6)])
    web = Web(feed, {})
    scan = S.scan_basiskele(web.fetch, None, NOW)
    assert url(20) not in web.calls                                  # older than FALLBACK_DAYS
    assert scan.extra_fetches == S.FALLBACK_MAX_FETCH == len(web.calls) - 1
    # unreadable pages are not remembered (tried again), still capped
    assert scan.checked == {}


def test_culture_hint_words():
    assert S.has_culture_hint("Atölye başlıyor") and S.has_culture_hint("x", "Çocuk şenliği")
    assert S.has_culture_hint("Söyleşi var") and not S.has_culture_hint("Park ihalesi", "yol")


# ---- health warning ----------------------------------------------------------------------------

def ls(day, hour=20, month=11):
    return S.Listing(S.BASISKELE, f"bk:{month}{day}", "T", start=datetime(2026, month, day, hour, tzinfo=ISTANBUL))


def test_warning_rules():
    w = S.PROGRAM_MISSING_TEXT
    assert w == "Program haberi bulunamadı (başlık değişmiş olabilir)"
    assert S.program_warning(NOW, [], {}) == w                                   # nothing found at all
    assert S.program_warning(NOW, [ls(10)], {"a": {}}) == w                      # nothing upcoming
    assert S.program_warning(NOW, [ls(20)], {"a": {}}) is None                   # healthy mid month
    late = datetime(2026, 11, 22, 12, tzinfo=ISTANBUL)
    assert S.program_warning(late, [ls(25)], {"a": {}}) is None
    assert S.program_warning(late, [ls(5, month=12)], {"a": {}}) == w            # after the 20th, none this month
    assert S.program_warning(late, [ls(25, month=11), ls(5, month=12)], {}) is None


def test_scan_sets_the_warning_only_when_unhealthy():
    healthy = Web(rss(item("Kasım Kültür Sanat Programı", 1)), {url(1): EVENT_HTML.replace("3 Aralık Perşembe", "20 Kasım Cuma")})
    assert S.scan_basiskele(healthy.fetch, None, NOW).warning is None
    empty = Web(rss(item("Okulda tören", 2, "Başkan okulu gezdi")), {})
    assert S.scan_basiskele(empty.fetch, None, NOW).warning == S.PROGRAM_MISSING_TEXT


def _scan_with_warning(text):
    scan = S.SourceScan(S.BASISKELE)
    scan.warning = text
    return scan


def test_runner_marks_card_warning_and_sends_one_notice_per_month():
    state = empty_state()
    cfg = config_from_dict({})
    r1 = _apply_sources(state, cfg, {S.BASISKELE: _scan_with_warning(S.PROGRAM_MISSING_TEXT)}, NOW,
                        (S.BASISKELE,))
    assert [e.kind for e in r1] == ["SRC_PROGRAM_MISSING"]
    assert r1[0].key == "SRC_PROGRAM_MISSING:basiskele:2026-11"
    assert state["sources"]["basiskele"]["warning"] == S.PROGRAM_MISSING_TEXT
    view = SRC.describe(state, NOW)[0]
    assert view["status"] == "stale" and view["icon"] == "⚠️" and view["warning"] == S.PROGRAM_MISSING_TEXT
    assert S.PROGRAM_MISSING_TEXT in SRC.line(view)
    card = L.source_card(view, NOW)
    assert any(S.PROGRAM_MISSING_TEXT in line for line in card.lines) and card.status == "stale"
    # same month, again and later: no second notice; the card keeps warning
    for minutes in (5, 60 * 24 * 3):
        again = _apply_sources(state, cfg, {S.BASISKELE: _scan_with_warning(S.PROGRAM_MISSING_TEXT)},
                               NOW + timedelta(minutes=minutes), (S.BASISKELE,))
        assert again == []
    # next month: one more
    dec = _apply_sources(state, cfg, {S.BASISKELE: _scan_with_warning(S.PROGRAM_MISSING_TEXT)},
                         datetime(2026, 12, 2, 12, tzinfo=ISTANBUL), (S.BASISKELE,))
    assert [e.key for e in dec] == ["SRC_PROGRAM_MISSING:basiskele:2026-12"]
    # healthy again: warning cleared, icon back to a check mark
    later = datetime(2026, 12, 2, 12, 1, tzinfo=ISTANBUL)
    assert _apply_sources(state, cfg, {S.BASISKELE: _scan_with_warning(None)}, later, (S.BASISKELE,)) == []
    assert state["sources"]["basiskele"]["warning"] is None
    assert SRC.describe(state, later)[0]["icon"] == "✅"


def test_notice_text_is_short_and_has_no_empty_title():
    from tiyatro.models import Config  # noqa: F401
    from tiyatro.notify import format_message
    state = empty_state()
    ev = _apply_sources(state, config_from_dict({}), {S.BASISKELE: _scan_with_warning(S.PROGRAM_MISSING_TEXT)},
                        NOW, (S.BASISKELE,))[0]
    text = format_message([ev], {}, config_from_dict({}))[0]
    assert "program haberi bulunamadı" in text and S.PROGRAM_MISSING_TEXT in text and "<b></b>" not in text
