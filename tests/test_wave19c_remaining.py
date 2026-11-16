"""Wave 19C: the Biletinial 'Son N Bilet' badge is parsed per session as ``remaining`` and only
shown (alert text, PC/Telegram session lines); ON_SALE classification is unchanged."""
from pathlib import Path

from tests.test_state import T0, WATCH, play, sess
from tiyatro import parser as P
from tiyatro.models import Config, Event, Play, PlayWatch, Status
from tiyatro.notify import format_message
from tiyatro.parser import parse_play
from tiyatro.state import diff, empty_state

FIX = Path(__file__).parent / "fixtures"
BASE = "https://biletinial.com/tr-tr/tiyatro/on-ikinci-gece"


def live():
    return parse_play((FIX / "play_on_ikinci_gece_son3_live_2026-11-16.html")
                      .read_text(encoding="utf-8"), BASE)


def test_live_page_remaining_only_on_the_badged_session():
    p = live()
    by = {s.id: s for s in p.sessions}
    assert by["18699574"].remaining == 3
    assert by["18699574"].status is Status.ON_SALE
    others = [s for s in p.sessions if s.id != "18699574"]
    assert others and all(s.remaining is None for s in others)
    assert all(s.status is Status.SOLD_OUT for s in others)


def test_classification_unchanged_by_badge():
    html = (FIX / "play_on_ikinci_gece_son3_live_2026-11-16.html").read_text(encoding="utf-8")
    stripped = html.replace('<div class="warn"><span class="tg ye">Son 3 Bilet</span></div>', "")
    assert stripped != html
    a, b = parse_play(html, BASE), parse_play(stripped, BASE)
    assert [(s.id, s.status, s.opens_at, s.start) for s in a.sessions] ==         [(s.id, s.status, s.opens_at, s.start) for s in b.sessions]
    assert all(s.remaining is None for s in b.sessions)
    old = parse_play((FIX / "play_on_ikinci_gece.html").read_text(encoding="utf-8"), BASE)
    assert all(s.remaining is None for s in old.sessions)


def test_remaining_variants():
    r = P._remaining
    assert r('<div class="warn"><span class="tg ye">Son 1 Bilet</span></div>') == 1
    assert r('<div class="warn"><span class="tg">  son   12  bilet </span></div>') == 12
    assert r('<div class="warn"><span class="tg ye">SON 5 BİLET</span></div>') == 5
    assert r('<div class="warn"><span class="tg ye">Son 3 Bilet &amp;x</span></div>') is None
    assert r('<div class="warn"><span class="tg">Yeni</span><span class="tg">Son 4 Bilet</span></div>') == 4
    assert r('<div class="warn"><span class="tg">Yeni</span></div>') is None
    assert r('<span class="tg">Son 3 Bilet</span>') is None          # not inside div.warn
    assert r('<div class="warn"><span class="tg">Son 0 Bilet</span></div>') is None
    assert r('<div class="warn"><span class="tg">Son 99999999999 Bilet</span></div>') is None
    assert r('<div class="warn"><span class="tg">Son 2 Bilet</span><span class="tg">Son 7 Bilet</span></div>') is None
    assert r("") is None and r("<div class='warn'><span class='tg'>") is None


def test_remaining_never_raises(monkeypatch):
    assert P._remaining(None) is None  # type: ignore[arg-type]
    monkeypatch.setattr(P, "_tags", lambda *a, **k: 1 / 0)
    assert P._remaining('<div class="warn"><span class="tg">Son 3 Bilet</span></div>') is None


def test_state_stores_small_int_and_clears_it():
    st, _ = diff(empty_state(), [play(sessions=[_with(sess(status=Status.ON_SALE), 3)])], WATCH, T0)
    assert st["plays"]["p"]["sessions"]["s1"]["remaining"] == 3
    st, _ = diff(st, [play(sessions=[sess(status=Status.ON_SALE)])], WATCH, T0.replace(minute=5))
    assert st["plays"]["p"]["sessions"]["s1"]["remaining"] is None


def _with(s, n):
    from dataclasses import replace
    return replace(s, remaining=n)


def test_on_sale_event_payload_carries_remaining_and_same_events():
    base, ev0 = diff(empty_state(), [play(sessions=[sess(status=Status.SOLD_OUT)])], WATCH, T0)
    t1 = T0.replace(minute=10)
    _s, ev_a = diff(base, [play(sessions=[sess(status=Status.ON_SALE)])], WATCH, t1)
    _s, ev_b = diff(base, [play(sessions=[_with(sess(status=Status.ON_SALE), 3)])], WATCH, t1)
    assert [e.kind for e in ev_a] == [e.kind for e in ev_b] == ["ON_SALE"]
    assert ev_a[0].key == ev_b[0].key
    assert "remaining" not in ev_a[0].payload
    assert ev_b[0].payload["remaining"] == 3


CFG = Config(plays={"p": PlayWatch(active=True, targets=[])}, sources=[], paused=False, version=1)
PLAYS = {"p": Play("p", "Oyun", "https://example.test/p", None, None, None, None)}


def alert(**extra):
    payload = {"title": "Oyun", "url": "https://example.test/s1",
               "start": "2026-11-27T20:00:00+03:00", "status": "on_sale", **extra}
    return Event("ON_SALE", "ON_SALE:s1:1", "p", "s1", [], payload)


def test_alert_text_with_remaining_keeps_first_line():
    t = format_message([alert(remaining=3)], PLAYS, CFG)[0]
    assert t.splitlines()[0] == "\U0001f39f️ Bilet alınabilir!"
    assert "Son 3 bilet" in t
    assert "Sayfa “Son 3 bilet” diyor; koltuk haritası farklı olabilir." in t
    assert "sepette" not in t


def test_alert_text_without_or_bad_remaining_unchanged():
    plain = format_message([alert()], PLAYS, CFG)[0]
    assert "Son " not in plain and "koltuk" not in plain
    for bad in (None, "3", True, 0, -1, 10**9, 3.5):
        assert format_message([alert(remaining=bad)], PLAYS, CFG)[0] == plain


def test_alert_large_remaining_shows_count_without_caution():
    t = format_message([alert(remaining=40)], PLAYS, CFG)[0]
    assert "Son 40 bilet" in t and "koltuk haritası" not in t


# ---- PC <-> Worker parity of the session status text -------------------------------------

import json
import shutil
import subprocess

import pytest

from pc import app_logic as L
from tiyatro.models import ISTANBUL
from datetime import datetime

ROOT = Path(__file__).resolve().parent.parent
MENU = (ROOT / "worker" / "src" / "menu.js").as_uri()
SCRIPT = """
import { render, h } from %s;
let raw = "";
for await (const c of process.stdin) raw += c;
const job = JSON.parse(raw);
process.stdout.write(JSON.stringify(render("p:" + h(job.slug), job.config, job.state, job.now).text));
""" % json.dumps(MENU)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_session_status_text_matches_between_pc_and_worker():
    now = datetime(2026, 11, 16, 12, 0, tzinfo=ISTANBUL)
    sessions = {
        "a": {"start": "2026-11-19T20:00:00+03:00", "status": "on_sale", "remaining": 3},
        "b": {"start": "2026-11-20T20:00:00+03:00", "status": "on_sale"},
        "c": {"start": "2026-11-21T20:00:00+03:00", "status": "on_sale", "remaining": 0},
        "d": {"start": "2026-11-22T20:00:00+03:00", "status": "sold_out", "remaining": 4},
        "e": {"start": "2026-11-23T20:00:00+03:00", "status": "on_sale", "remaining": True},
        "f": {"start": "2026-11-24T20:00:00+03:00", "status": "on_sale", "remaining": 12},
    }
    state = {"plays": {"p": {"title": "Oyun", "sessions": sessions}}, "meta": {}}
    config = {"version": 1, "paused": False, "sources": [], "plays": {}}
    job = {"slug": "p", "config": config, "state": state, "now": int(now.timestamp() * 1000)}
    out = subprocess.run(["node", "--input-type=module", "-e", SCRIPT],
                         input=json.dumps(job, ensure_ascii=False), capture_output=True,
                         text=True, encoding="utf-8", timeout=60, check=True)
    text = json.loads(out.stdout)
    lines = L.session_lines(state["plays"]["p"], now)
    assert [x.status for x in lines] == ["satışta · son 3 bilet", "satışta", "satışta",
                                         "tükendi", "satışta", "satışta · son 12 bilet"]
    worker_status = [ln.split("— ", 1)[1] for ln in text.splitlines() if " — " in ln]
    assert worker_status == [x.status for x in lines]
