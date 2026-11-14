"""Wave 3c: portal watcher inside run_once (state, events, outbox, failure isolation)."""
from __future__ import annotations

import copy
import threading
import time
from datetime import timedelta

import pytest

import tiyatro.runner as runner
from tests.test_runner import NOW, WATCH_CFG, Env, mkplay, sess
from tests.test_wave3c_portals import page, real_empty
from tiyatro.claims import claim_path
from tiyatro.config import config_from_dict
from tiyatro.fetcher import FetchError, FetchResult
from tiyatro.models import EVENT_KINDS, Event, Status
from tiyatro.notify import MAX_LEN, format_message
from tiyatro.runner import _fingerprint, _prepare, run_once

PORTAL_URL = "https://rezervasyon.basiskele.bel.tr/etkinlikler"
PORTAL_CFG = {"portals": [{"id": "basiskele", "name": "Başiskele Belediyesi rezervasyon",
                           "url": PORTAL_URL, "enabled": True}]}
CFG = {**WATCH_CFG, **PORTAL_CFG}
CARD = ('<div class="card"><h4>{t}</h4><p>15 Kasım 2026 15:00</p>'
        '<a href="/EtkinlikDetay.aspx?id={i}">Rezervasyon Yap</a></div>')


def opened_html(*titles):
    return page("".join(CARD.format(t=t, i=n) for n, t in enumerate(titles, 1)))


class PortalWeb:
    def __init__(self, html=None):
        self.html = html if html is not None else real_empty()
        self.error: Exception | None = None
        self.calls: list[str] = []
        self.block: threading.Event | None = None

    def fetch(self, url):
        self.calls.append(url)
        if self.block is not None:
            self.block.wait(30)
        if self.error is not None:
            raise self.error
        return FetchResult(self.html, 200, 1)


def make(monkeypatch, who="github", config=CFG, plays=None, html=None):
    env = Env(monkeypatch, plays if plays is not None else [mkplay("zz")], copy.deepcopy(config), who=who)
    env.portal = PortalWeb(html)
    return env


def at(n):
    return NOW + timedelta(minutes=5 * n)


def go(env, n, **kw):
    kw.setdefault("portal_fetch_fn", env.portal.fetch)
    return env.run(at(n), **kw)


def pstate(env):
    return env.saved_state()["portals"]["basiskele"]


def kinds(res):
    return [e.kind for e in res.events if e.kind.startswith("PORTAL_")]


def pm(env):
    """Telegram messages about the portal (the Biletinial ones are not interesting here)."""
    return [m for m in env.tg.sent if "🏛️" in m]


def texts(env):
    return "\n".join(pm(env))


# ---- kinds -----------------------------------------------------------------------------

def test_event_kinds_registered():
    for k in ("PORTAL_OPENED", "PORTAL_CHANGED", "PORTAL_FAILING"):
        assert k in EVENT_KINDS


# ---- first observation ----------------------------------------------------------------

def test_first_observation_empty_is_a_silent_baseline(monkeypatch):
    env = make(monkeypatch)
    res = go(env, 0)
    assert kinds(res) == [] and pm(env) == []
    rec = pstate(env)
    assert rec["empty"] is True and rec["items"] == [] and rec["lines"] == []
    assert rec["fail_streak"] == 0 and len(rec["signature"]) == 40
    assert rec["first_seen"] == rec["last_change"] == rec["last_check"] == at(0).isoformat()
    assert env.portal.calls == [PORTAL_URL]


def test_first_observation_non_empty_opens(monkeypatch):
    env = make(monkeypatch, html=opened_html("Kuğu Gölü Balesi", "Pinokyo"))
    res = go(env, 0)
    assert kinds(res) == ["PORTAL_OPENED"]
    [ev] = [e for e in res.events if e.kind == "PORTAL_OPENED"]
    rec = pstate(env)
    assert ev.key == f"PORTAL_OPENED:basiskele:{rec['signature'][:12]}"
    assert rec["empty"] is False and [i["title"] for i in rec["items"]] == [
        "Kuğu Gölü Balesi", "Pinokyo"]
    msg = texts(env)
    assert "🏛️ Başiskele rezervasyon portalı: etkinlik açıldı" in msg
    assert "Kuğu Gölü Balesi" in msg and "EtkinlikDetay.aspx?id=1" in msg
    assert PORTAL_URL in msg
    assert env.saved_state()["outbox"] == []


# ---- transitions ---------------------------------------------------------------------

def test_empty_to_open_then_deduped_across_runs(monkeypatch):
    env = make(monkeypatch)
    go(env, 0)
    env.portal.html = opened_html("Konser")
    res = go(env, 1)
    assert kinds(res) == ["PORTAL_OPENED"] and len(pm(env)) == 1
    before = pstate(env)
    assert before["last_change"] == at(1).isoformat() and before["first_seen"] == at(0).isoformat()
    for n in (2, 3):
        res = go(env, n)
        assert kinds(res) == []
    assert len(pm(env)) == 1
    after = pstate(env)
    assert after["signature"] == before["signature"]
    assert after["last_change"] == at(1).isoformat() and after["last_check"] == at(3).isoformat()


def test_open_to_changed_alerts_once_per_signature(monkeypatch):
    env = make(monkeypatch)
    go(env, 0)
    env.portal.html = opened_html("Konser")
    go(env, 1)
    env.portal.html = opened_html("Konser", "Tiyatro Gecesi")
    res = go(env, 2)
    assert kinds(res) == ["PORTAL_CHANGED"]
    assert "güncellendi" in pm(env)[-1] and "Tiyatro Gecesi" in pm(env)[-1]
    assert kinds(go(env, 3)) == []
    env.portal.html = opened_html("Konser")        # back to a signature already announced
    assert kinds(go(env, 4)) == []
    assert len(pm(env)) == 2


def test_open_to_empty_only_updates_state(monkeypatch):
    env = make(monkeypatch)
    go(env, 0)
    env.portal.html = opened_html("Konser")
    go(env, 1)
    env.tg.sent.clear()
    env.portal.html = real_empty()
    res = go(env, 2)
    assert kinds(res) == [] and pm(env) == []
    rec = pstate(env)
    assert rec["empty"] is True and rec["items"] == [] and rec["last_change"] == at(2).isoformat()
    env.portal.html = opened_html("Başka Etkinlik")  # a different list: a fresh opening
    assert kinds(go(env, 3)) == ["PORTAL_OPENED"]


# ---- failures ------------------------------------------------------------------------

def test_failure_streak_alerts_once_and_resets(monkeypatch):
    env = make(monkeypatch)
    go(env, 0)
    env.portal.error = FetchError("HTTP 503")
    seen = []
    for n in range(1, 6):
        seen.append(kinds(go(env, n)))
        assert pstate(env)["fail_streak"] == n
    assert seen == [[], [], ["PORTAL_FAILING"], [], []]
    assert len([m for m in pm(env) if "okunamıyor" in m]) == 1
    keys = [k for k in env.saved_state()["emitted"] if k.startswith("PORTAL_FAILING:")]
    assert keys == [f"PORTAL_FAILING:basiskele:{at(3).date().isoformat()}"]
    env.portal.error = None
    assert kinds(go(env, 6)) == [] and pstate(env)["fail_streak"] == 0
    assert pstate(env)["empty"] is True


def test_failures_before_any_observation_still_count(monkeypatch):
    env = make(monkeypatch)
    env.portal.error = FetchError("x")
    for n in range(3):
        res = go(env, n)
    assert kinds(res) == ["PORTAL_FAILING"]
    assert pstate(env)["empty"] is None and pstate(env)["fail_streak"] == 3
    env.portal.error = None
    assert kinds(go(env, 3)) == []        # first real observation: empty -> silent baseline
    assert pstate(env)["empty"] is True


def test_portal_failure_never_touches_biletinial_result(monkeypatch):
    env = make(monkeypatch, config=CFG, plays=[mkplay()])
    go(env, 0)
    env.tg.sent.clear()
    env.web.set(mkplay(sessions=[sess(status=Status.ON_SALE)]))
    env.portal.error = RuntimeError("boom")
    res = go(env, 1)
    assert [e.kind for e in res.events] == ["ON_SALE"]
    assert res.failures == 0 and res.scanned == 1
    assert len(env.tg.sent) == 1 and "Bilet alınabilir" in env.tg.sent[0]


def test_portal_timeout_does_not_delay_biletinial(monkeypatch):
    monkeypatch.setattr(runner, "PORTAL_WAIT_S", 0.3)
    env = make(monkeypatch, config=CFG, plays=[mkplay()])
    go(env, 0)
    env.tg.sent.clear()
    env.web.set(mkplay(sessions=[sess(status=Status.ON_SALE)]))
    env.portal.block = threading.Event()
    sent_at = []
    real_send = env.tg.send
    env.tg.send = lambda t: (sent_at.append(time.monotonic()), real_send(t))[1]
    start = time.monotonic()
    try:
        res = go(env, 1)
        total = time.monotonic() - start
        assert [e.kind for e in res.events] == ["ON_SALE"]
        assert sent_at and sent_at[0] - start < 0.25      # delivered before waiting for it
        assert total < 5
        assert pstate(env)["fail_streak"] == 1
    finally:
        env.portal.block.set()


def test_portal_code_error_is_swallowed(monkeypatch):
    env = make(monkeypatch, config=CFG, plays=[mkplay()])
    monkeypatch.setattr(runner, "parse_portal",
                        lambda *a, **k: (_ for _ in ()).throw(ValueError("bug")))
    res = go(env, 0)
    assert res.scanned == 1 and res.failures == 0
    assert env.saved_state()["plays"]["p"]


# ---- scheduling / opt-in -------------------------------------------------------------

def test_not_checked_in_burst_or_when_not_injected_or_disabled(monkeypatch):
    env = make(monkeypatch, config=CFG, plays=[mkplay()])
    go(env, 0)
    assert len(env.portal.calls) == 1
    go(env, 1, only_slugs=["p"])
    assert len(env.portal.calls) == 1                       # burst: never
    env.run(at(2))                                           # no portal_fetch_fn: never
    assert len(env.portal.calls) == 1
    off = copy.deepcopy(CFG)
    off["portals"][0]["enabled"] = False
    env2 = make(monkeypatch, config=off)
    go(env2, 0)
    assert env2.portal.calls == [] and "portals" not in env2.saved_state() or \
        env2.saved_state().get("portals") == {}


def test_old_config_gets_default_portal_and_old_state_works(monkeypatch):
    env = make(monkeypatch, config=WATCH_CFG, plays=[mkplay()])      # no "portals" key
    env.store.files["state.json"] = ({"plays": {}, "meta": {}, "emitted": {}}, "s")
    go(env, 0)
    assert env.portal.calls == [PORTAL_URL]


def test_prepare_coerces_garbage_portal_state():
    for bad in (None, [], "x", 5):
        s = _prepare({"portals": bad})
        assert s["portals"] == {}
    s = _prepare({"portals": {"a": "junk", "b": {"empty": True}}})
    assert s["portals"] == {"b": {"empty": True}}


# ---- fingerprint ---------------------------------------------------------------------

def test_fingerprint_ignores_last_check_only(monkeypatch):
    env = make(monkeypatch)
    go(env, 0)
    a = copy.deepcopy(env.saved_state())
    go(env, 1)
    b = env.saved_state()
    assert a["portals"]["basiskele"]["last_check"] != b["portals"]["basiskele"]["last_check"]
    assert _fingerprint(a) == _fingerprint(b) or (
        # last_scan/last_seen are ignored already; nothing else may differ
        _fingerprint(a) == _fingerprint(b))
    c = copy.deepcopy(b)
    c["portals"]["basiskele"]["signature"] = "x" * 40
    assert _fingerprint(c) != _fingerprint(b)
    d = copy.deepcopy(b)
    d["portals"]["basiskele"]["fail_streak"] = 2
    assert _fingerprint(d) != _fingerprint(b)


# ---- paused / outbox / claims / pc ------------------------------------------------------

def test_paused_discards_portal_events(monkeypatch):
    cfg = {**CFG, "paused": True}
    env = make(monkeypatch, config=cfg)
    go(env, 0)
    env.portal.html = opened_html("Konser")
    res = go(env, 1)
    assert kinds(res) == ["PORTAL_OPENED"] and pm(env) == []
    st = env.saved_state()
    assert st["outbox"] == [] and st["meta"]["paused_discarded"] == 2  # NEW_PLAY + PORTAL_OPENED
    assert any(k.startswith("PORTAL_OPENED:basiskele:") for k in st["emitted"])
    assert kinds(go(env, 2)) == []                       # never re-announced


def test_delivery_through_outbox_retry_and_claim(monkeypatch):
    env = make(monkeypatch)
    go(env, 0)
    env.portal.html = opened_html("Konser")
    env.tg.fail = True
    res = go(env, 1)
    key = [e.key for e in res.events][0]
    [entry] = env.saved_state()["outbox"]
    assert entry["key"] == key and entry["event"]["kind"] == "PORTAL_OPENED"
    env.tg.fail = False
    env.store.advance(1)
    go(env, 2)                                          # retried after the back-off
    for n in range(3, 6):
        go(env, n)
    assert len(pm(env)) == 1 and env.saved_state()["outbox"] == []
    claim = env.store.files[claim_path(key)][0]
    assert claim["status"] == "sent"


def test_pc_runner_waits_60s_and_defers_to_github_claim(monkeypatch):
    env = make(monkeypatch, who="pc")
    go(env, 0)
    env.portal.html = opened_html("Konser")
    res = env.run(at(1), portal_fetch_fn=env.portal.fetch)
    [entry] = env.saved_state()["outbox"]
    assert entry["not_before"] == (at(1) + timedelta(seconds=60)).isoformat()
    assert pm(env) == []
    key = [e.key for e in res.events if e.kind.startswith("PORTAL_")][0]
    # GitHub delivers in between: the PC's later attempt finds the claim "sent"
    other = Env(monkeypatch, [], copy.deepcopy(CFG), who="github", store=env.store)
    from tiyatro.claims import send_with_claim
    assert send_with_claim(other.claims, other.tg, key, "x")
    env.run(at(1) + timedelta(seconds=61), portal_fetch_fn=env.portal.fetch)
    assert pm(env) == [] and env.saved_state()["outbox"] == []


def test_pc_runner_sends_after_grace_when_nobody_else_did(monkeypatch):
    env = make(monkeypatch, who="pc")
    go(env, 0)
    env.portal.html = opened_html("Konser")
    env.run(at(1), portal_fetch_fn=env.portal.fetch)
    env.run(at(1) + timedelta(seconds=61), portal_fetch_fn=env.portal.fetch)
    assert len(pm(env)) == 1 and "etkinlik açıldı" in pm(env)[0]


# ---- notify texts ------------------------------------------------------------------------

def _ev(kind, payload, key="K"):
    return Event(kind, key, "", None, [], payload)


def test_notify_texts_and_escaping():
    cfg = config_from_dict({})
    opened = _ev("PORTAL_OPENED", {
        "portal_id": "basiskele", "title": "Başiskele Belediyesi rezervasyon",
        "url": PORTAL_URL,
        "items": [{"title": "A <b>&</b> Şov", "url": "https://x.test/r?a=1&b=2"}],
        "lines": []})
    [msg] = format_message([opened], {}, cfg)
    assert msg.startswith("🏛️ Başiskele rezervasyon portalı: etkinlik açıldı")
    assert "A &lt;b&gt;&amp;&lt;/b&gt; Şov" in msg and "a=1&amp;b=2" in msg
    assert msg.rstrip().endswith(PORTAL_URL) or PORTAL_URL in msg
    changed = _ev("PORTAL_CHANGED", {"portal_id": "basiskele", "title": "x", "url": PORTAL_URL,
                                     "items": [], "lines": ["Yeni duyuru satırı 1", "Satır <2>"]})
    [msg] = format_message([changed], {}, cfg)
    assert "güncellendi" in msg and "Yeni duyuru satırı 1" in msg and "Satır &lt;2&gt;" in msg
    failing = _ev("PORTAL_FAILING", {"portal_id": "basiskele", "title": "x", "url": PORTAL_URL,
                                     "detail": "3 taramadır okunamıyor"})
    [msg] = format_message([failing], {}, cfg)
    assert "okunamıyor" in msg and "🏛️" in msg
    other = _ev("PORTAL_OPENED", {"portal_id": "x1", "title": "Başka Portal",
                                  "url": "https://x.test/", "items": [], "lines": []})
    assert "Başka Portal" in format_message([other], {}, cfg)[0]


def test_notify_limits_items_and_stays_under_4096():
    cfg = config_from_dict({})
    items = [{"title": "T" * 500 + str(i), "url": "https://x.test/" + "u" * 900 + str(i)}
             for i in range(30)]
    ev = _ev("PORTAL_OPENED", {"portal_id": "basiskele", "title": "x", "url": PORTAL_URL,
                               "items": items, "lines": ["L" * 900] * 30})
    parts = format_message([ev], {}, cfg)
    assert parts and all(len(p) <= MAX_LEN for p in parts)
    assert "+25" in "".join(parts) and "T" * 500 + "5" not in "".join(parts)
