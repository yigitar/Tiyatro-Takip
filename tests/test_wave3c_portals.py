"""Wave 3c: generic parser of the Başiskele reservation portal page (no network)."""
from __future__ import annotations

import pathlib

import pytest

from tiyatro.fetcher import decode_body
from tiyatro.portals import PortalSnapshot, parse_portal

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
BASE = "https://rezervasyon.basiskele.bel.tr/etkinlikler"


def real_empty() -> str:
    return decode_body((FIXTURES / "basiskele_etkinlikler_empty.html").read_bytes(),
                       "text/html; charset=iso-8859-9")


def page(content: str, header: str = "Başiskele Belediyesi Rezervasyon") -> str:
    """The real empty page with ``content`` placed where the event list would be."""
    html = real_empty()
    marker = html.index("<body")
    body_open = html.index(">", marker) + 1
    return html[:body_open] + f"<div id='ctl00_ContentPlaceHolder1_liste'>{content}</div>" \
        + html[body_open:].replace("Başiskele Belediyesi Rezervasyon", header, 1)


# ---- the real empty page ----------------------------------------------------------

def test_real_empty_fixture_is_empty_and_stable():
    a, b = parse_portal(real_empty()), parse_portal(real_empty())
    assert isinstance(a, PortalSnapshot)
    assert a.empty is True and a.items == []
    assert a.lines == []                          # every line of the page is known chrome
    assert a.signature == b.signature and len(a.signature) == 40


def test_chrome_only_change_keeps_it_empty():
    changed = real_empty().replace("Başiskele Belediyesi Rezervasyon",
                                   "Başiskele Belediyesi Yeni Rezervasyon Sistemi", 1) \
        .replace(">Etkinlikler<", ">Tüm Etkinlikler Listesi Sayfası<")
    snap = parse_portal(changed)
    assert snap.empty is True and snap.items == []


def test_site_redesign_with_lots_of_text_but_no_items_is_not_empty():
    text = "<p>Yeni tasarımımıza hoş geldiniz, burada duyurular yer alır.</p>" \
           "<p>Rezervasyon kuralları ve açıklamalar bu sayfada listelenir.</p>"
    snap = parse_portal(page(text))
    assert snap.empty is False and snap.items == []
    assert len(snap.lines) == 2


def test_single_short_notice_does_not_open_the_portal():
    snap = parse_portal(page("<p>Aktif etkinlik bulunmamaktadır.</p>"))
    assert snap.empty is True


# ---- synthetic ACTIVE variants --------------------------------------------------------

def test_card_repeater():
    cards = """
    <div class="row">
     <div class="card"><div class="card-body"><h4>Kuğu Gölü Balesi</h4>
       <p>15 Kasım 2026 Pazar 15:00</p></div>
       <div class="card-footer"><a href="/EtkinlikDetay.aspx?id=12">Rezervasyon Yap</a></div></div>
     <div class="card"><div class="card-body"><h4>Çocuk Tiyatrosu: Pinokyo</h4>
       <p>16 Kasım 2026 Pazartesi 14:00</p></div>
       <div class="card-footer"><a href="/EtkinlikDetay.aspx?id=13">Rezervasyon Yap</a></div></div>
    </div>"""
    snap = parse_portal(page(cards))
    assert snap.empty is False
    assert [i["title"] for i in snap.items] == ["Kuğu Gölü Balesi", "Çocuk Tiyatrosu: Pinokyo"]
    assert [i["url"] for i in snap.items] == [
        "https://rezervasyon.basiskele.bel.tr/EtkinlikDetay.aspx?id=12",
        "https://rezervasyon.basiskele.bel.tr/EtkinlikDetay.aspx?id=13"]
    assert "15 Kasım 2026 Pazar 15:00" in snap.items[0]["info"]


def test_table_style():
    table = """<table class="grid"><tr><th>Etkinlik</th><th>Tarih</th><th></th></tr>
    <tr><td>Sirk Gösterisi</td><td>20.11.2026 18:00</td>
        <td><a href="rezervasyon.aspx?e=7">Rezervasyon Yap</a></td></tr>
    <tr><td>Şiir Akşamı</td><td>21.11.2026 19:30</td>
        <td><a href="rezervasyon.aspx?e=8">Rezervasyon Yap</a></td></tr></table>"""
    snap = parse_portal(page(table))
    assert snap.empty is False
    assert [i["title"] for i in snap.items] == ["Sirk Gösterisi", "Şiir Akşamı"]
    assert snap.items[0]["url"] == "https://rezervasyon.basiskele.bel.tr/rezervasyon.aspx?e=7"


def test_webforms_repeater_with_postback_and_submit_button():
    html = """<div><span id="ctl00_cp_rpt_ctl00_lbl">Yaz Konseri</span><br/>
      <span>25 Kasım 2026 20:00</span>
      <a id="ctl00_cp_rpt_ctl00_lnk" href="javascript:__doPostBack('ctl00$cp$rpt$ctl00$lnk','')">
        Rezervasyon Yap</a></div>
     <div><span>Kış Şenliği</span><br/><span>26 Kasım 2026 11:00</span>
      <input type="submit" name="ctl00$cp$rpt$ctl01$btn" value="Rezervasyon Yap" /></div>"""
    snap = parse_portal(page(html))
    assert snap.empty is False
    assert [i["title"] for i in snap.items] == ["Yaz Konseri", "Kış Şenliği"]
    assert all(i["url"] == BASE for i in snap.items)


def test_title_inside_the_link():
    snap = parse_portal(page('<ul><li><a href="/etkinlik/5">Tiyatro Şenliği - 30 Kasım</a></li></ul>'))
    assert snap.empty is False
    assert snap.items == [{"title": "Tiyatro Şenliği - 30 Kasım",
                           "url": "https://rezervasyon.basiskele.bel.tr/etkinlik/5",
                           "info": ""}]


def test_absolute_external_url_kept_and_duplicates_collapsed():
    html = ('<div><h3>Konser</h3><a href="https://ext.example/r?id=1">Rezervasyon Yap</a>'
            '<a href="https://ext.example/r?id=1">Rezervasyon Yap</a></div>')
    snap = parse_portal(page(html))
    assert [(i["title"], i["url"]) for i in snap.items] == [
        ("Konser", "https://ext.example/r?id=1")]


def test_nav_footer_and_self_links_are_not_items():
    html = ('<nav><a href="/Default">Ana Sayfa</a><a href="/hakkimizda">Hakkımızda</a></nav>'
            '<a href="/etkinlikler">Etkinlikler</a><a href="#">Rezervasyon</a>'
            '<a href="tel:4444110">444 41 10</a><a href="mailto:a@b.c">a@b.c</a>'
            '<footer><a href="/rezervasyonlarim">Rezervasyonlarım</a></footer>')
    snap = parse_portal(page(html))
    assert snap.items == [] and snap.empty is True


def test_hidden_content_and_scripts_are_ignored():
    html = ('<div style="display: none"><h3>Gizli Etkinlik</h3>'
            '<a href="/etkinlik/9">Rezervasyon Yap</a></div>'
            '<script>var x="<a href=\'/etkinlik/1\'>Rezervasyon Yap</a>";</script>'
            '<style>.a{content:"Rezervasyon Yap Etkinlik uzun metin burada"}</style>'
            '<input type="hidden" name="__VIEWSTATE" value="Çok uzun bir değer burada yer alıyor" />')
    snap = parse_portal(page(html))
    assert snap.items == [] and snap.empty is True and snap.lines == []


def test_signature_tracks_content_and_hrefs():
    a = parse_portal(page('<div><h3>Konser</h3><a href="/e/1">Rezervasyon Yap</a></div>'))
    b = parse_portal(page('<div><h3>Konser</h3><a href="/e/1">Rezervasyon Yap</a></div>'))
    c = parse_portal(page('<div><h3>Konser</h3><a href="/e/2">Rezervasyon Yap</a></div>'))
    d = parse_portal(page('<div><h3>Konser 2</h3><a href="/e/1">Rezervasyon Yap</a></div>'))
    assert a.signature == b.signature
    assert len({a.signature, c.signature, d.signature}) == 3


def test_header_change_does_not_change_signature_of_an_active_page():
    html = '<div><h3>Konser</h3><a href="/e/1">Rezervasyon Yap</a></div>'
    a = parse_portal(page(html))
    b = parse_portal(page(html).replace(">Etkinlikler<", ">Etkinlik Listesi<"))
    assert a.empty is False and b.empty is False


# ---- robustness ---------------------------------------------------------------------

@pytest.mark.parametrize("junk", [None, "", "   ", "<<<>>><a", "\x00\x01<div", 123, b"bytes",
                                  "<a href=" + "x" * 5000, "</div></div></span>",
                                  "<div>" * 5000 + "Rezervasyon Yap", "&#xZZ; &bogus; <p"])
def test_garbage_never_raises(junk):
    snap = parse_portal(junk)
    assert isinstance(snap, PortalSnapshot) and len(snap.signature) == 40


def test_oversized_html_is_bounded():
    big = "<div><p>" + "uzun satır içeriği " * 400000 + "</p></div>"
    snap = parse_portal(big)
    assert len(snap.lines) <= 200 and all(len(l) <= 300 for l in snap.lines)
    many = "".join(f'<div><h3>Etkinlik {i}</h3><a href="/e/{i}">Rezervasyon Yap</a></div>'
                   for i in range(500))
    assert len(parse_portal(many).items) <= 50
