from datetime import datetime, timedelta, timezone

from tiyatro.claims import ClaimStore, claim_path, send_with_claim
from tiyatro.notify import TelegramError


def make(store, who="gh"):
    return ClaimStore(store, who, lambda: datetime(2000, 1, 1, tzinfo=timezone.utc))


class FakeTG:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    def send(self, text):
        if self.fail:
            raise TelegramError("boom")
        self.sent.append(text)
        return 77


def test_first_claim_wins_second_loses(store):
    a, b = make(store, "gh"), make(store, "pc")
    assert a.claim("ON_SALE:s1:1") is True
    assert b.claim("ON_SALE:s1:1") is False
    data, _ = store.get(claim_path("ON_SALE:s1:1"))
    assert data["status"] == "claimed" and data["claimed_by"] == "gh"
    assert data["attempts"] == 1
    lease = datetime.fromisoformat(data["lease_until"])
    assert lease - store.now == timedelta(seconds=45)


def test_sent_blocks_everyone(store):
    a, b = make(store, "gh"), make(store, "pc")
    assert a.claim("k")
    a.mark_sent("k", 5)
    data, _ = store.get(claim_path("k"))
    assert data["status"] == "sent" and data["message_id"] == 5 and data["sent_at"]
    store.advance(10_000)
    assert b.claim("k") is False
    assert a.claim("k") is False


def test_expired_lease_takeover_increments_attempts(store):
    a, b = make(store, "gh"), make(store, "pc")
    assert a.claim("k")
    store.advance(30)
    assert b.claim("k") is False
    store.advance(16)
    assert b.claim("k") is True
    data, _ = store.get(claim_path("k"))
    assert data["claimed_by"] == "pc" and data["attempts"] == 2


def test_takeover_loses_cas(store):
    a, b = make(store, "gh"), make(store, "pc")
    a.claim("k")
    store.advance(60)
    real_put = store.put

    def racing_put(path, data, sha):
        real_put(path, {**data, "claimed_by": "third"}, sha)  # someone wins first
        return real_put(path, data, sha)  # our sha is now stale

    store.put = racing_put
    assert b.claim("k") is False


def test_failed_is_reclaimable_immediately(store):
    a, b = make(store, "gh"), make(store, "pc")
    a.claim("k")
    a.mark_failed("k", "tg down")
    data, _ = store.get(claim_path("k"))
    assert data["status"] == "failed" and data["note"]
    assert b.claim("k") is True
    assert store.get(claim_path("k"))[0]["attempts"] == 2


def test_claim_fail_open_when_store_down(store):
    store.fail = True
    assert make(store).claim("k") is True


def test_send_with_claim_fail_open_sends(store):
    store.fail = True
    tg = FakeTG()
    assert send_with_claim(make(store), tg, "k", "hello") is True
    assert tg.sent == ["hello"]


def test_send_with_claim_happy_and_duplicate_suppressed(store):
    tg = FakeTG()
    assert send_with_claim(make(store, "gh"), tg, "k", "hi") is True
    assert send_with_claim(make(store, "pc"), tg, "k", "hi") is False
    assert tg.sent == ["hi"]
    assert store.get(claim_path("k"))[0]["status"] == "sent"


def test_send_failure_marks_failed_and_returns_false(store):
    tg = FakeTG(fail=True)
    assert send_with_claim(make(store), tg, "k", "hi") is False
    assert store.get(claim_path("k"))[0]["status"] == "failed"
    assert send_with_claim(make(store, "pc"), FakeTG(), "k", "hi") is True


def test_mark_sent_error_after_send_is_swallowed(store):
    tg = FakeTG()
    cs = make(store)

    def boom(*a, **k):
        raise RuntimeError("x")

    cs.mark_sent = boom
    assert send_with_claim(cs, tg, "k", "hi") is True
    assert tg.sent == ["hi"]


def test_clock_fallback_when_server_now_fails(store):
    fixed = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)
    cs = ClaimStore(store, "gh", lambda: fixed)

    def down():
        raise RuntimeError("no Date header")

    store.server_now = down
    assert cs.claim("k") is True
    data, _ = store.get(claim_path("k"))
    assert datetime.fromisoformat(data["claimed_at"]) == fixed
    assert datetime.fromisoformat(data["lease_until"]) == fixed + timedelta(seconds=45)


def test_late_mark_failed_does_not_flip_sent(store):
    a, b = make(store, "gh"), make(store, "pc")
    a.claim("k")
    store.advance(60)
    assert b.claim("k") is True
    b.mark_sent("k", 9)
    a.mark_failed("k", "late")  # stale owner
    assert store.get(claim_path("k"))[0]["status"] == "sent"
    assert make(store, "third").claim("k") is False


def test_mark_failed_noop_when_not_owner(store):
    a, b = make(store, "gh"), make(store, "pc")
    a.claim("k")
    store.advance(60)
    b.claim("k")  # pc owns it now, status claimed
    a.mark_failed("k", "late")
    data, _ = store.get(claim_path("k"))
    assert data["status"] == "claimed" and data["claimed_by"] == "pc"


def test_purge_survives_store_errors(store):
    cs = make(store)
    cs.claim("old1")
    cs.claim("old2")
    store.advance(49 * 3600)
    real_delete, n = store.delete, [0]

    def flaky(path, sha):
        n[0] += 1
        if n[0] == 2:
            raise RuntimeError("boom")
        real_delete(path, sha)

    store.delete = flaky
    assert cs.purge(48) == 1
    store.fail = True
    assert cs.purge(48) == 0


def test_claim_path_is_injective_and_safe():
    keys = ["a:b", "a_b", "a/b", "a b", "ON_SALE:s1:1", "ON_SALE_s1_1", "ç", "c"]
    paths = {claim_path(k) for k in keys}
    assert len(paths) == len(keys)
    for p in paths:
        assert p.startswith("claims/") and p.endswith(".json")
        assert all(c.isalnum() or c in "._-/" for c in p)


def test_purge_deletes_old_only(store):
    cs = make(store)
    cs.claim("old")
    store.advance(49 * 3600)
    cs.claim("new")
    assert cs.purge(48) == 1
    assert store.get(claim_path("old"))[0] is None
    assert store.get(claim_path("new"))[0] is not None
