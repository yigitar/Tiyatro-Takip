from pathlib import Path

from tiyatro.models import ISTANBUL, Play, PlayWatch, Session, Status
from tiyatro.parser import parse_play
from tiyatro.tags import apply_overrides, derive_tags
from datetime import datetime, timezone

FIX = Path(__file__).parent / "fixtures"


def _play(title="X", genre=None, age=None, price=None, hours=(20,)):
    ss = [Session(f"s{h}", datetime(2026, 11, 20, h, 0, tzinfo=ISTANBUL), None,
                  Status.ON_SALE, None, "u", "v") for h in hours]
    return Play("x", title, "u", genre, 90, age, price, ss)


def _real(name):
    return parse_play((FIX / name).read_text(encoding="utf-8"), "https://example.test/x")


def test_kusursuz_real():
    assert derive_tags(_real("play_kusursuz.html")) == {"çocuk", "gündüz"}


def test_on_ikinci_gece_real():
    tags = derive_tags(_real("play_on_ikinci_gece.html"))
    assert tags == {"gündüz"}


def test_adult_evening_empty():
    assert derive_tags(_play(genre="Komedi", age=16, price=300)) == set()


def test_cocuk_triggers():
    assert "çocuk" in derive_tags(_play(age=12))
    assert "çocuk" not in derive_tags(_play(age=13, price=21))
    assert "çocuk" in derive_tags(_play(price=20))
    assert "çocuk" in derive_tags(_play(title="ÇOCUK Masalı"))
    assert "çocuk" in derive_tags(_play(title="Çocuk Masalı"))
    assert "çocuk" in derive_tags(_play(genre="ÇOCUK TİYATROSU"))
    assert "çocuk" in derive_tags(_play(genre="çocuk"))


def test_gunduz_boundary_and_timezone():
    assert "gündüz" not in derive_tags(_play(hours=(17, 20)))
    assert "gündüz" in derive_tags(_play(hours=(16, 20)))
    p = _play()
    # 13:30 UTC = 16:30 Istanbul -> gündüz; 14:30 UTC = 17:30 -> not
    p.sessions = [Session("a", datetime(2026, 11, 20, 13, 30, tzinfo=timezone.utc),
                          None, Status.ON_SALE, None, "u", "v")]
    assert "gündüz" in derive_tags(p)
    p.sessions[0] = Session("a", datetime(2026, 11, 20, 14, 30, tzinfo=timezone.utc),
                            None, Status.ON_SALE, None, "u", "v")
    assert "gündüz" not in derive_tags(p)


def test_apply_overrides():
    base = {"gündüz"}
    assert apply_overrides(base, PlayWatch(True, tag_overrides={"çocuk": True})) == {"gündüz", "çocuk"}
    assert apply_overrides({"çocuk"}, PlayWatch(True, tag_overrides={"çocuk": False})) == set()
    assert apply_overrides(base, None) == base
    assert base == {"gündüz"}  # input not mutated
