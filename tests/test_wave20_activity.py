"""Wave 20A: readable, coloured activity log (row height from the font, column widths, colours)."""
from __future__ import annotations

import pytest

tk = pytest.importorskip("tkinter")

from pc import activity as A  # noqa: E402
from pc import activity_ui as U  # noqa: E402
from pc import theme as T  # noqa: E402
from tests.test_pc_activity_ui import seed, ui  # noqa: E402,F401
from tests.test_pc_app_smoke import tk_root  # noqa: E402,F401


def e(cat, title, detail="", level="info"):
    return {"ts": "2026-11-16T10:00:00+03:00", "cat": cat, "title": title, "detail": detail,
            "level": level}


def test_row_height_covers_the_line_height_and_grows_with_dpi():
    assert U.row_height(16, 96 / 72) >= 16 + 6
    assert U.row_height(40, 2.0 * 96 / 72) > U.row_height(40, 96 / 72)   # 200 % > 100 %
    for ls in (12, 16, 24, 40):
        assert U.row_height(ls, 96 / 72) > ls


def test_level_and_category_colour_classes():
    assert U.row_class(e("Hata/Uyarı", "x", level="error")) == "error"
    assert U.row_class(e("Hata/Uyarı", "x", level="warn")) == "warn"
    assert U.row_class(e("Bilet açılışı", "Bilet alınabilir! — Hamlet")) == "open"
    assert U.row_class(e("Bilet açılışı", "Rezervasyon açıldı!")) == "open"
    assert U.row_class(e("Bilet açılışı", "Açılışa 1 gün kaldı")) == "plain"
    assert U.row_class(e("Bildirim", "Telegram'a gönderildi")) == "notify"
    assert U.row_class(e("Liste kontrolü", "6 oyun", "değişiklik var: 1 yeni")) == "changed"
    assert U.row_class(e("Liste kontrolü", "6 oyun", "değişiklik yok")) == "muted"
    assert U.row_class(e("Liste kontrolü", "Hızlı mod: 3 kontrol, değişiklik yok")) == "muted"
    for cat in ("Takip", "Uygulama", "GitHub"):
        assert U.row_class(e(cat, "x")) == "plain"


def test_tags_zebra_for_plain_rows_and_tint_for_error_warn():
    assert U.row_tag(e("Takip", "x"), 0) != U.row_tag(e("Takip", "x"), 1)
    assert U.row_tag(e("Hata/Uyarı", "x", level="error"), 3) == "error"
    cols = U.tag_colours()
    assert all(t in cols for t in (U.row_tag(e("Takip", "x"), i) for i in (0, 1)))
    assert cols["plain_0"]["background"] == T.WINDOW and cols["plain_1"]["background"] == T.ZEBRA
    for tag in ("error", "warn"):    # readable on its own tint
        c = cols[tag]
        assert T.contrast(T.rgb_of(c["foreground"]), T.rgb_of(c["background"])) >= 4.5


def test_time_text_is_full_for_other_days_and_short_today():
    from datetime import datetime
    from tiyatro.models import ISTANBUL
    now = datetime(2026, 11, 17, 12, 0, tzinfo=ISTANBUL)
    assert A.when_text("2026-11-16T17:04:00+03:00", now) == "16.11 17:04"
    assert A.when_text("2026-11-17T09:05:00+03:00", now) == "09:05"


def test_real_window_rows_not_clipped_and_columns_fit(ui):  # noqa: F811
    seed(ui)
    tab = ui.activity_tab
    ui.root.update_idletasks() if hasattr(ui, "root") else None
    tab.update_idletasks()
    ls = tab.row_font.metrics("linespace")
    height = int(ttk_style_rowheight(tab))
    assert height == tab.row_px and height >= ls + 6
    widths = U.column_widths(tab.row_font, 8)
    assert int(tab.tree.column("when", "width")) >= tab.row_font.measure("00.00 00:00")
    assert int(tab.tree.column("cat", "width")) >= max(tab.row_font.measure(c)
                                                       for c in A.CATEGORIES)
    assert int(tab.tree.column("when", "width")) == widths["when"]
    assert [bool(int(tab.tree.column(c, "stretch"))) for c in ("when", "cat", "title", "result")] == [
        False, False, True, False]
    # colours really reach the rows; the selected row carries the highlight tag and gives it back
    tags = {tab.tree.item(i, "values")[2]: tab.tree.item(i, "tags")[0] for i in tab.tree.get_children()}
    assert tags["Kayıt yazılamadı"] == "error" and tags["Biletinial okunamadı"] == "warn"
    assert tags["Bilet alınabilir! — Hamlet"].startswith("open_")
    iid = tab.tree.get_children()[0]
    tab.tree.selection_set(iid)
    tab.update()
    assert tab.tree.item(iid, "tags") == ("sel",)
    tab.tree.selection_set(tab.tree.get_children()[1])
    tab.update()
    assert tab.tree.item(iid, "tags") == tab._base_tags[iid]


def ttk_style_rowheight(tab):
    from tkinter import ttk
    return ttk.Style(tab).lookup(U.ROW_STYLE, "rowheight")
