"""Wave 3c: charset-aware fetcher and config.portals."""
from __future__ import annotations

import json
import pathlib

import pytest

from tiyatro.config import (DEFAULT_PORTALS, MAX_PORTALS, config_from_dict, config_to_dict,
                            default_config)
from tiyatro.fetcher import FetchResult, fetch

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
PORTAL_URL = "https://rezervasyon.basiskele.bel.tr/etkinlikler"
TR = "şığİçöüŞĞÇÖÜI ı"


# ---- fetcher ------------------------------------------------------------------------

def test_header_charset_iso_8859_9_roundtrip():
    body = f"<html>{TR}</html>".encode("iso-8859-9")
    r = fetch("u", transport=lambda u, h, t: (200, body, "text/html; charset=iso-8859-9"))
    assert isinstance(r, FetchResult) and TR in r.html


def test_header_charset_wins_over_meta_utf8():
    body = f'<meta charset="utf-8"><p>{TR}</p>'.encode("iso-8859-9")
    r = fetch("u", transport=lambda u, h, t: (200, body, "text/html; charset=ISO-8859-9"))
    assert TR in r.html


def test_meta_charset_used_without_header():
    body = f'<html><head><meta charset="iso-8859-9"></head>{TR}</html>'.encode("iso-8859-9")
    assert TR in fetch("u", transport=lambda u, h, t: (200, body)).html
    assert TR in fetch("u", transport=lambda u, h, t: (200, body, "text/html")).html


def test_meta_http_equiv_charset():
    body = ('<meta http-equiv="Content-Type" content="text/html; charset=windows-1254">'
            + TR).encode("cp1254")
    assert TR in fetch("u", transport=lambda u, h, t: (200, body)).html


def test_default_is_utf8_and_unknown_charset_falls_back():
    body = TR.encode("utf-8")
    assert fetch("u", transport=lambda u, h, t: (200, body)).html == TR
    r = fetch("u", transport=lambda u, h, t: (200, body, "text/html; charset=nonsense-9"))
    assert r.html == TR
    r = fetch("u", transport=lambda u, h, t: (200, b"ok \xff", "text/html"))
    assert r.html.startswith("ok ")


def test_utf8_bom_still_stripped_with_utf8_header():
    body = "﻿<html>ş".encode("utf-8")
    r = fetch("u", transport=lambda u, h, t: (200, body, "text/html; charset=utf-8"))
    assert r.html == "<html>ş"


def test_real_fixture_bytes_decode_as_iso_8859_9():
    raw = (FIXTURES / "basiskele_etkinlikler_empty.html").read_bytes()
    html = fetch("u", transport=lambda u, h, t: (
        200, raw, "text/html; charset=iso-8859-9")).html
    assert "Başiskele Belediyesi Rezervasyon" in html
    assert "Yazılım K7" in html and "�" not in html
    # meta says utf-8 and no header: the old behaviour produces replacement characters,
    # which is exactly why the header has to win
    assert "�" in fetch("u", transport=lambda u, h, t: (200, raw)).html


def test_biletinial_style_response_is_unchanged():
    body = "<html>Kuğu Gölü</html>".encode("utf-8")
    r = fetch("u", transport=lambda u, h, t: (200, body, "text/html; charset=utf-8"))
    assert r.html == "<html>Kuğu Gölü</html>"


# ---- config.portals -----------------------------------------------------------------

def test_default_portals_when_key_absent():
    c = config_from_dict({"plays": {}})
    assert c.portals == DEFAULT_PORTALS
    assert c.portals == [{"id": "basiskele", "name": "Başiskele Belediyesi rezervasyon",
                          "url": PORTAL_URL, "enabled": True}]
    assert default_config().portals == DEFAULT_PORTALS


def test_default_portals_are_not_shared_between_configs():
    a, b = config_from_dict({}), config_from_dict({})
    a.portals[0]["enabled"] = False
    assert b.portals[0]["enabled"] is True and DEFAULT_PORTALS[0]["enabled"] is True


def test_explicit_empty_list_means_no_portals():
    assert config_from_dict({"portals": []}).portals == []


def test_roundtrip():
    portals = [{"id": "a", "name": "A", "url": "https://a.example/x", "enabled": False},
               {"id": "b-2", "name": "B", "url": "https://b.example/", "enabled": True}]
    c = config_from_dict({"portals": portals})
    assert c.portals == portals
    assert config_to_dict(c)["portals"] == portals
    assert config_from_dict(json.loads(json.dumps(config_to_dict(c)))) == c


def test_old_config_without_portals_roundtrips_with_default():
    d = config_to_dict(config_from_dict({"plays": {}, "sources": ["https://x.test/"]}))
    assert d["portals"] == DEFAULT_PORTALS


@pytest.mark.parametrize("bad", [
    "https://ok.example/",                                    # not a list
    [{"id": "a", "name": "A", "url": "http://a.example/", "enabled": True}],   # http
    [{"id": "a", "name": "A", "url": "ftp://a.example/", "enabled": True}],
    [{"id": "a", "name": "A", "url": "https://", "enabled": True}],
    [{"id": "a", "name": "A", "url": "javascript:alert(1)", "enabled": True}],
    [{"id": "", "name": "A", "url": "https://a.example/", "enabled": True}],
    [{"id": "A b", "name": "A", "url": "https://a.example/", "enabled": True}],
    [{"id": "a", "name": "", "url": "https://a.example/", "enabled": True}],
    [{"id": "a", "name": "A", "url": "https://a.example/", "enabled": "yes"}],
    ["nope"],
    [{"id": "a", "name": "A", "url": "https://a.example/", "enabled": True},
     {"id": "a", "name": "B", "url": "https://b.example/", "enabled": True}],   # duplicate id
])
def test_invalid_portals_rejected(bad):
    with pytest.raises(ValueError):
        config_from_dict({"portals": bad})


def test_at_most_five_portals():
    mk = lambda i: {"id": f"p{i}", "name": f"P{i}", "url": f"https://p{i}.example/",
                    "enabled": True}
    assert MAX_PORTALS == 5
    assert len(config_from_dict({"portals": [mk(i) for i in range(5)]}).portals) == 5
    with pytest.raises(ValueError):
        config_from_dict({"portals": [mk(i) for i in range(6)]})


def test_enabled_defaults_to_true_when_missing():
    c = config_from_dict({"portals": [{"id": "a", "name": "A", "url": "https://a.example/"}]})
    assert c.portals[0]["enabled"] is True
