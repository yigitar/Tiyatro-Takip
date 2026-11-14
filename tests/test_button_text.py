"""A button without off/disabled but with 'Yakında'/'Tükendi' text is never ON_SALE."""
from __future__ import annotations

import pytest

from tiyatro.models import Status
from tiyatro.parser import _button_state

ACILIS = ' data-acilis="202611250700"'


def _b(cls, text, extra=""):
    return f'<button type="submit" class="cta {cls}"{extra}>{text}</button>'


@pytest.mark.parametrize("text", ["Yakında", "YAKINDA", "yakinda", "Çok <b>Yakında</b>",
                                  "TÜKENDİ", "Tükendi", "TUKENDI", "tükendi"])
@pytest.mark.parametrize("cls,extra", [("seanceSelect", ""), ("seanceSelect", ACILIS),
                                       ("seanceSelect off", " disabled"),
                                       ("seanceSelect off", ""),
                                       ("seanceSelect", " disabled")])
def test_closed_text_is_never_on_sale(text, cls, extra):
    status, _ = _button_state(_b(cls, text, extra))
    assert status is not Status.ON_SALE


def test_closed_text_without_off_or_disabled_is_unknown_and_keeps_opens_at():
    status, opens = _button_state(_b("seanceSelect", "Yakında", ACILIS))
    assert status is Status.UNKNOWN and opens is not None
    status, opens = _button_state(_b("seanceSelect", "TÜKENDİ"))
    assert status is Status.UNKNOWN and opens is None


@pytest.mark.parametrize("text", ["BİLETİNİ AL", "Bilet Al", "Satın Al", ""])
def test_other_text_stays_on_sale(text):
    assert _button_state(_b("seanceSelect", text))[0] is Status.ON_SALE
    assert _button_state(_b("seanceSelect", text, ACILIS))[0] is Status.ON_SALE


def test_upcoming_with_off_disabled_is_unchanged():
    assert _button_state(_b("seanceSelect off", "Yakında", ACILIS + " disabled"))[0] \
        is Status.UPCOMING
