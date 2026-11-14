import re

import pytest

from tiyatro.models import Config, Event, Play, PlayWatch, Rule, Target
from tiyatro.notify import Telegram, TelegramError, _split, format_message

URL = "https://example.test/s1"
T_A = Target("a", "Yetişkin", 2, Rule())
T_C = Target("c", "Çocuk", 3, Rule())
CFG = Config(plays={"p": PlayWatch(active=True, targets=[T_A, T_C])}, sources=[],
             paused=False, version=1)
PLAYS = {"p": Play("p", "Oyun", "https://example.test/p", None, None, None, None)}


def ev(kind="ON_SALE", ids=("a",), sid="s1", title="Oyun", opens="2026-11-27T10:00:00+03:00",
       start="2026-11-27T20:00:00+03:00", slug="p", key=None, **extra):
    payload = {"title": title, "url": URL, "start": start, "opens_at": opens,
               "status": "on_sale", **extra}
    return Event(kind, key or f"{kind}:{sid}", slug, sid, list(ids), payload)


def test_merged_targets_single_message():
    parts = format_message([ev(ids=("a",)), ev(ids=("c",), key="k2")], PLAYS, CFG)
    assert len(parts) == 1
    t = parts[0]
    assert "Yetişkin (2 kişi)" in t and "Çocuk (3 kişi)" in t
    assert t.count("Oyun") == 1
    assert "27 Kasım Cuma 20:00" in t and URL in t
    assert "\U0001f39f️ Bilet alınabilir!" in t


def test_html_escaped_title():
    t = format_message([ev(title="A <b> & Co")], PLAYS, CFG)[0]
    assert "A &lt;b&gt; &amp; Co" in t and "<b> &" not in t


def test_opening_soon_has_time():
    t = format_message([ev("OPENING_SOON", opens="2026-11-27T10:05:00+03:00")], PLAYS, CFG)[0]
    assert "10 dk sonra satış açılıyor" in t and "10:05" in t


def test_day_before_neutral():
    t = format_message([ev("DAY_BEFORE")], PLAYS, CFG)[0]
    assert "Satış açılış hatırlatması: Cuma, 10:00" in t
    assert "yarın" not in t


def test_other_kinds():
    for kind, text in [("UNKNOWN_STATUS", "Bilinmeyen durum"), ("PC_INACTIVE", "PC uygulaması"),
                       ("GH_LAGGING", "gecikiyor"), ("FETCH_FAILING", "başarısız")]:
        e = Event(kind, kind, "p", "s1", [], {"title": "Oyun", "url": URL,
                                              "start": "2026-11-27T20:00:00+03:00"})
        assert text in format_message([e], PLAYS, CFG)[0]
    r = Event("REMOVED_PLAY", "r", "p", None, [], {"title": "Oyun", "url": "https://x.test/p"})
    t = format_message([r], PLAYS, CFG)[0]
    assert "Listeden kalktı" in t and "Oyun" in t


def test_many_new_plays_folded():
    evs = [Event("NEW_PLAY", f"n{i}", f"s{i}", None, [],
                 {"title": f"Oyun{i}", "url": f"https://x.test/{i}"}) for i in range(12)]
    parts = format_message(evs, {}, CFG)
    assert len(parts) == 1 and parts[0].count("Yeni oyun") == 1
    assert all(f"Oyun{i}" in parts[0] for i in range(12))
    one = format_message(evs[:1], {}, CFG)[0]
    assert "\U0001f195 Yeni oyun" in one


def test_long_payload_split_under_limit():
    evs = [ev(title="T" * 60, sid=f"s{i}") for i in range(80)]
    parts = format_message(evs, PLAYS, CFG)
    assert len(parts) >= 2 and all(len(p) <= 4096 for p in parts)


def _balanced(part: str) -> bool:
    stack = []
    for m in re.finditer(r"<(/?)([a-z]+)[^>]*>", part):
        if m.group(1):
            if not stack or stack.pop() != m.group(2):
                return False
        else:
            stack.append(m.group(2))
    stripped = re.sub(r"<[^>]*>", "", part)
    return not stack and "<" not in stripped and ">" not in stripped


def _entities_ok(part: str) -> bool:
    text = re.sub(r"<[^>]*>", "", part)
    return not re.search(r"&(?![a-z#0-9]+;)", text)


def test_overlong_title_truncated_tags_balanced():
    for n in (5000, 4090, 4085):
        parts = format_message([ev(title="&" * n)], PLAYS, CFG)
        assert all(len(p) <= 4096 and _balanced(p) and _entities_ok(p) for p in parts)
        assert "…" in "".join(parts)


def test_many_long_targets_split_entities_intact():
    targets = [Target(f"t{i}", "&" * 100, 2, Rule()) for i in range(80)]
    cfg = Config(plays={"p": PlayWatch(active=True, targets=targets)}, sources=[],
                 paused=False, version=1)
    parts = format_message([ev(ids=[t.id for t in targets])], PLAYS, cfg)
    assert len(parts) >= 2
    assert all(len(p) <= 4096 and _balanced(p) and _entities_ok(p) for p in parts)


def test_split_line_reopens_cut_element():
    line = "<b>" + "x&amp;" * 2000 + "</b>"
    parts = _split(line, 4096)
    assert len(parts) >= 2
    assert all(len(p) <= 4096 and _balanced(p) and _entities_ok(p) for p in parts)
    assert re.sub(r"<[^>]*>", "", "".join(parts)) == "x&amp;" * 2000


def test_empty():
    assert format_message([], PLAYS, CFG) == []


class Transport:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def __call__(self, url, payload):
        self.calls.append((url, payload))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def test_send_ok_payload():
    tr = Transport((200, {"ok": True, "result": {"message_id": 9}}))
    assert Telegram("TOK", "42", tr).send("hi") == 9
    url, payload = tr.calls[0]
    assert url.endswith("/sendMessage")
    assert payload["parse_mode"] == "HTML" and payload["chat_id"] == "42"
    assert payload["disable_web_page_preview"] is True and payload["text"] == "hi"


def test_429_retry_then_success():
    tr = Transport((429, {"ok": False, "parameters": {"retry_after": 1}}),
                   (200, {"ok": True, "result": {"message_id": 3}}))
    slept = []
    assert Telegram("TOK", "1", tr, sleep=slept.append).send("x") == 3
    assert slept == [1] and len(tr.calls) == 2


def test_429_gives_up_after_three():
    tr = Transport(*[(429, {"parameters": {"retry_after": 1}})] * 3)
    with pytest.raises(TelegramError) as ei:
        Telegram("SECRET", "1", tr, sleep=lambda s: None).send("x")
    assert len(tr.calls) == 3 and "SECRET" not in str(ei.value)


def test_error_has_no_token():
    tr = Transport((400, {"ok": False, "description": "bad SECRET request"}))
    with pytest.raises(TelegramError) as ei:
        Telegram("SECRET", "1", tr).send("x")
    assert "SECRET" not in str(ei.value)


def _chain_repr(exc):
    seen, out = set(), []
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        out.append(repr(exc) + str(exc))
        exc = exc.__cause__ or exc.__context__
    return " ".join(out)


def test_transport_exception_scrubbed_whole_chain():
    tr = Transport(OSError("fail https://api.telegram.org/botSECRET/sendMessage"))
    with pytest.raises(TelegramError) as ei:
        Telegram("SECRET", "1", tr).send("x")
    assert "SECRET" not in _chain_repr(ei.value)
    assert ei.value.__cause__ is None and ei.value.__context__ is None


def test_telegramerror_from_transport_scrubbed_whole_chain():
    tr = Transport(TelegramError("leak SECRET"))
    with pytest.raises(TelegramError) as ei:
        Telegram("SECRET", "1", tr).send("x")
    assert "SECRET" not in _chain_repr(ei.value)


def test_retry_after_capped():
    tr = Transport((429, {"parameters": {"retry_after": 99999}}))
    slept = []
    with pytest.raises(TelegramError):
        Telegram("T", "1", tr, sleep=slept.append).send("x")
    assert slept == [] and len(tr.calls) == 1


def test_retry_after_within_total_cap_waits():
    tr = Transport((429, {"parameters": {"retry_after": 30}}),
                   (200, {"ok": True, "result": {"message_id": 1}}))
    slept = []
    assert Telegram("T", "1", tr, sleep=slept.append).send("x") == 1
    assert slept == [30]


def test_total_429_wait_stays_below_the_claim_lease():  # final review M4
    from tiyatro.claims import LEASE
    tr = Transport((429, {"parameters": {"retry_after": 20}}),
                   (429, {"parameters": {"retry_after": 20}}),
                   (200, {"ok": True, "result": {"message_id": 1}}))
    slept = []
    with pytest.raises(TelegramError):
        Telegram("T", "1", tr, sleep=slept.append).send("x")
    assert sum(slept) < LEASE.total_seconds() and slept == [20]
    tr = Transport((429, {"parameters": {"retry_after": 60}}))
    slept = []
    with pytest.raises(TelegramError):
        Telegram("T", "1", tr, sleep=slept.append).send("x")
    assert slept == []


def test_retry_after_string_parsed():
    tr = Transport((429, {"parameters": {"retry_after": "5"}}),
                   (200, {"ok": True, "result": {"message_id": 1}}))
    slept = []
    Telegram("T", "1", tr, sleep=slept.append).send("x")
    assert slept == [5]


def test_ok_without_message_id_is_success():
    for body in ({"ok": True}, {"ok": True, "result": {}}):
        assert Telegram("T", "1", Transport((200, body))).send("x") == 0


def test_ok_false_raises():
    with pytest.raises(TelegramError):
        Telegram("T", "1", Transport((200, {"ok": False}))).send("x")


# ==== final review M2 / M3 ==========================================================

def test_on_sale_and_opening_soon_lead_with_the_kind_line():
    for kind, text in (("ON_SALE", "Bilet alınabilir!"),
                       ("OPENING_SOON", "10 dk sonra satış açılıyor")):
        first = format_message([ev(kind)], PLAYS, CFG)[0].splitlines()[0]
        assert text in first, first
    first = format_message([ev("DAY_BEFORE")], PLAYS, CFG)[0].splitlines()[0]
    assert first == "<b>Oyun</b>"  # other kinds keep the title first


def test_opened_sold_out_text():
    t = format_message([ev("OPENED_SOLD_OUT")], PLAYS, CFG)[0]
    assert "Satış açıldı ve görülmeden tükendi — iade için izleniyor" in t
