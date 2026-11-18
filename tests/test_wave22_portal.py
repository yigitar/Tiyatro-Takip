"""Wave 22 B: the Başiskele reservation portal link is in every place the user looks."""
from __future__ import annotations

from datetime import datetime, timedelta

import json
import pathlib
import shutil
import subprocess

import pytest

tk = pytest.importorskip("tkinter")

from pc.viewmodel import with_events  # noqa: E402
from tests.test_pc_app_smoke import Box, FakeService, tk_root, ui  # noqa: E402,F401
from tests.test_wave6 import _view  # noqa: E402
from tiyatro import notify as N  # noqa: E402
from tiyatro.config import DEFAULT_PORTALS, config_from_dict  # noqa: E402
from tiyatro.models import ISTANBUL, Event  # noqa: E402

PORTAL = "https://rezervasyon.basiskele.bel.tr/etkinlikler"


def _payload(source="basiskele", **extra):
    return {"source": source, "event_id": "bk:1", "title": "Püsküllü Muamma", "kind": "Tiyatro",
            "start": "2026-11-20T20:00:00+03:00", "venue": "Başiskele", "url": "https://x.test/a",
            "opens_at": "2026-11-19T12:00:00+03:00", **extra}


def _text(kind, source="basiskele", cfg=None, **extra):
    ev = Event(kind, f"{kind}:1", "", None, [], _payload(source, **extra))
    return N.format_message([ev], {}, cfg or config_from_dict({}))[0]


def test_default_portal_url_is_the_documented_one():
    assert DEFAULT_PORTALS[0]["url"] == PORTAL
    assert N.portal_url(config_from_dict({})) == PORTAL


@pytest.mark.parametrize("kind", ["SRC_NEW", "SRC_OPENED", "SRC_REOPENED", "SRC_SOON",
                                  "SRC_DAY_BEFORE", "SRC_OPENS"])
def test_basiskele_notices_carry_the_portal_line(kind):
    assert f"Rezervasyon portalı: {PORTAL}" in _text(kind)


@pytest.mark.parametrize("kind", ["SRC_NEW", "SRC_OPENED", "SRC_SOON"])
def test_other_sources_get_no_portal_line(kind):
    assert "Rezervasyon portalı" not in _text(kind, source="vkv")


def test_portal_line_follows_config_and_is_absent_when_disabled():
    off = config_from_dict({"portals": [{"id": "basiskele", "name": "x", "url": PORTAL,
                                         "enabled": False}]})
    assert "Rezervasyon portalı" not in _text("SRC_NEW", cfg=off)
    other = config_from_dict({"portals": [{"id": "basiskele", "name": "x",
                                           "url": "https://example.test/r", "enabled": True}]})
    assert "Rezervasyon portalı: https://example.test/r" in _text("SRC_NEW", cfg=other)


def test_digest_of_several_new_events_has_one_portal_line():
    evs = [Event("SRC_NEW", f"SRC_NEW:{i}", "", None, [], _payload(title=f"Oyun {i}"))
           for i in range(2)]
    text = N.format_message(evs, {}, config_from_dict({}))[0]
    assert text.count("Rezervasyon portalı") == 1 and PORTAL in text


# ---- PC detail ------------------------------------------------------------------------------
def _select(ui, **kw):
    ui.views = [_view(**kw)]
    base = ui.svc.rows
    ui.svc.rows = lambda now=None: with_events(base(), ui.views, datetime.now(ISTANBUL))
    ui.refresh_rows()
    ui.select_slug(ui.views[0]["id"])
    ui.root.update()


def test_basiskele_event_detail_has_portal_row_and_button(ui):
    _select(ui, source="basiskele", eid="bk:1", title="Püsküllü Muamma")
    facts = {nl.cget("text"): lbl.cget("text") for nl, lbl in ui._fact_pool[:ui._facts_shown]}
    assert facts["Rezervasyon"] == PORTAL
    assert ui.event_portal_btn.winfo_ismapped()
    assert ui.event_portal_btn.cget("text") == "Rezervasyon portalı ↗"
    ui.event_portal_btn.invoke()
    assert ui.opened[-1] == PORTAL
    # the page button is still there and still opens the event page
    ui.event_page_btn.invoke()
    assert ui.opened[-1] == "https://example.test/bk:1"


def test_vkv_event_and_plays_have_no_portal_row_or_button(ui):
    _select(ui, source="vkv", eid="vkv:541")
    facts = [nl.cget("text") for nl, _l in ui._fact_pool[:ui._facts_shown]]
    assert "Rezervasyon" not in facts and not ui.event_portal_btn.winfo_ismapped()
    assert "İletişim" in facts                      # VKV: the phone / e-mail block
    ui.select_slug("p1")
    ui.root.update()
    assert ui.portal_url_for(ui.rows_by_slug["p1"]) == ""


def test_portal_button_toggles_when_switching_rows_and_follows_config(ui):
    _select(ui, source="basiskele", eid="bk:1")
    assert ui.event_portal_btn.winfo_ismapped()
    ui.select_slug("p1")
    ui.select_slug("bk:1")
    ui.root.update()
    assert ui.event_portal_btn.winfo_ismapped()
    ui.svc._cfg = config_from_dict({**ui.svc.raw, "portals": []})
    ui.show_detail(force=True)
    ui.root.update()
    assert not ui.event_portal_btn.winfo_ismapped()
    assert "Rezervasyon" not in [nl.cget("text") for nl, _l in ui._fact_pool[:ui._facts_shown]]


def test_source_card_portal_button_has_a_tooltip_and_opens_the_portal(ui):
    ui.svc.sources = lambda: [{"id": "basiskele", "name": "Başiskele Belediyesi", "status": "ok",
                               "icon": "✅", "last_check": "—", "last_change": "—",
                               "items_count": 3, "error": None}]

    class P:
        id, text, url, state = "basiskele", "🏛️ Başiskele rezervasyon portalı: boş", PORTAL, "empty"
    ui.svc.portals = lambda: [P()]
    ui._source_sig = None
    ui.refresh_rows()
    ui.root.update()
    assert len(ui.source_buttons) == 1 and ui.source_buttons[0].cget("text") == "Portalı aç"
    ui.source_buttons[0].invoke()
    assert ui.opened[-1] == PORTAL


# ---- parity: Python and Worker pick the same portal URL for the same config.json ---------------
ROOT = pathlib.Path(__file__).resolve().parent.parent
MENU = (ROOT / "worker" / "src" / "menu.js").as_uri()
SCRIPT = """
import { portalUrl } from %s;
let raw = "";
for await (const chunk of process.stdin) raw += chunk;
process.stdout.write(JSON.stringify(JSON.parse(raw).map((c) => portalUrl(c))));
""" % json.dumps(MENU)

PORTAL_CONFIGS = [
    {},
    {"portals": []},
    {"portals": [{"id": "basiskele", "name": "x", "url": PORTAL, "enabled": False}]},
    {"portals": [{"id": "basiskele", "name": "x", "url": "https://example.test/r", "enabled": True}]},
    {"portals": [{"id": "basiskele", "name": "x", "url": "https://example.test/r"}]},
    {"portals": [{"id": "other", "name": "y", "url": "https://example.test/o", "enabled": True}]},
]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_worker_and_python_choose_the_same_portal_url():
    out = subprocess.run(["node", "--input-type=module", "-e", SCRIPT],
                         input=json.dumps(PORTAL_CONFIGS), capture_output=True, text=True,
                         encoding="utf-8", timeout=60, check=True)
    js = json.loads(out.stdout)
    py = [N.portal_url(config_from_dict(c)) for c in PORTAL_CONFIGS]
    assert js == py == [PORTAL, "", "", "https://example.test/r", "https://example.test/r", ""]
