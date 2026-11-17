# Wave 20 report

Local commit only (no push/deploy, no secrets).

## A. Activity log (pc/activity_ui.py, pc/theme.py)
- Kept ttk.Treeview (header drag-resize built in). Own style `Activity.Treeview` (other widgets untouched).
- Row height = font linespace + padding scaled with Tk DPI scaling (`row_height`). Measured in a real mapped Tk window at scaling 1.33 / 2.0 / 2.67: linespace 21/32/45 px, row bbox height 29/44/61 px (always >= linespace + 8).
- Columns from the real font: Zaman = width of "00.00 00:00" + padding (full "gg.aa SS:dd"; today's entries still show "SS:dd" per existing `when_text`), Kategori = longest category, Sonuc fixed 34 chars (resizable), only Ozet stretches.
- Colours (all from pc/theme.py, new `BLUE`, `rgb_of`; no hex outside theme.py): error = red on faint red tint, warn = amber on faint amber tint (both checked >= 4.5 contrast on their tint), "bilet acildi" = green bold, Bildirim = blue, "degisiklik var" = bold, "degisiklik yok" / Hizli mod = grey, others normal; zebra stripe on non-tinted rows. One-line legend under the list.
- A tag background hides the native selection colour in ttk, so the selected row swaps to a `sel` tag (SystemHighlight / SystemHighlightText) and gets its colour tag back on deselect.
- Selection, filters, search, Temizle, Dosyayi goster, Ctrl+C, context menu, detail dialog and log file format unchanged (existing tests green).
- Tests: tests/test_wave20_activity.py (row height, time text, class/tag mapping, contrast, real-window geometry, selection tag swap).

## B. Basiskele "<Ad> programi" (tiyatro/srcevents.py `_PROGRAM`)
- Rule: after an explicit date+time hit (or "Ayni gun saat" resolved by the existing rule), in the same sentence, a locative suffix (’te/’de/’ta/’da/’nde/’nda) directly followed by 1-4 Capitalised words and "programi", not followed by "kapsam". Title = the name WITHOUT "programi" ("Evlilik Okulu"); kind empty; venue = the existing venue rule. Notices follow existing rules (digest for several).
- Negative tests: header sentence "... Kultur Sanat Basiskele programi kapsaminda ...", no date, no time, lowercase name, 6-word name, name in a different sentence than the time.
- Events now parsed from bk_art_12879_live_2026-11-16.html (published 2026-11-02 assumed for year):
  01.11 16:00 Kitaplar Ulkesi; 06.11 19:30 Rapunzel; 17.11 20:00 Unutmak; 20.11 11:00 Satir Arasi Ebeveynlik; 20.11 15:00 Kediler Sokagi; 20.11 20:00 Puskullu Muamma; 22.11 14:00 Evlilik Okulu (venue Basiskele Belediyesi Antik Kofte Kafe & Restoran); 22.11 16:00 Tas Prenses; 25.11 20:00 Kitaphane Kultur Soyleskileri. (9; was 8; wave-18 test updated.)

## Unverified
1. Colours/layout were not seen (PrintWindow cannot capture Tk); only geometry numbers and tag/colour values checked.
2. Selection-tag swap behaviour on the user's Tk build/theme not eyeballed.
3. The article's year is inferred by the existing weekday rule (22 Kasim 2026 is a Sunday = "Pazar", consistent).
4. Other real articles may contain "<Name> programi" sentences that are not events (only this fixture was checked).
