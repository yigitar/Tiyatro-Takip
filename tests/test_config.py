import pytest

from tiyatro.config import config_from_dict, config_to_dict, default_config
from tiyatro.models import Config, PlayWatch, Rule, Target

VENUE = "https://biletinial.com/tr-tr/mekan/kocaeli-buyuksehir-belediyesi-sehir-tiyatrolari"


def _config(people=2, from_time="18:00"):
    rule = Rule(
        days=[2, 3],
        from_time=from_time,
        to_time=None,
        overrides={3: {"from": "20:00", "to": None}},
        exclude_dates=["2026-12-18"],
    )
    target = Target(id="t1", name="Hedef", people=people, rule=rule)
    watch = PlayWatch(active=True, targets=[target], tag_overrides={"cocuk": False})
    return Config(plays={"kusursuz": watch}, sources=[VENUE], paused=False, version=1)


def test_roundtrip_and_defaults():
    d = default_config()
    assert d.sources == [VENUE]
    assert d.paused is False
    c = _config()
    data = config_to_dict(c)
    assert data["plays"]["kusursuz"]["targets"][0]["rule"]["overrides"] == {
        "3": {"from": "20:00", "to": None}
    }
    assert config_from_dict(data) == c


def test_rejects_bad_time():
    with pytest.raises(ValueError):
        config_from_dict(config_to_dict(_config(from_time="25:00")))


@pytest.mark.parametrize("people", [0, 5])
def test_people_bounds(people):
    with pytest.raises(ValueError):
        config_from_dict(config_to_dict(_config(people=people)))


@pytest.mark.parametrize("people", [1, 4])
def test_people_valid(people):
    assert config_from_dict(config_to_dict(_config(people=people))) == _config(people=people)
