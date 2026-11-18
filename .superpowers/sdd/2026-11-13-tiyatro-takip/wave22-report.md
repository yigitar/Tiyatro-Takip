# Wave 22 report ("Yeni gelişmeler" banner + Başiskele portal link)

Local commit only, nothing pushed or deployed, no secrets.

## A. What changed in the app (private window; the popup text stays generic)
- Source of the list: the very `Event` objects the popup is fed with (`Service.on_notify` -> `App.show_popup`). `show_popup` now also records them in `AlertStore` (`pc/alerts.py`, pure logic), at once (not after the 3 s popup coalescing). The popup text/behaviour is unchanged (test asserts the secret title is not in it).
- `pc/alerts.py`: `describe(ev)` -> icon, label, title, detail, row id; `line_text()`; `AlertStore` (add dedupes by event key, `ack_all/ack_key/ack_slug`, newest-first `open()`, cap 100, acknowledged ones kept 3 days only so a replayed key is not shown twice). Persisted in the PC-local `ui.json` under `alerts` (survives restart; garbage entries ignored).
- Banner (`App._build_alert_bar`, `_render_alerts`): strip above the list in the "Oyunlar" tab (`before=pane`), packed only while there are unread alerts. Header "Yeni gelişmeler (N)" + `Tümünü okundu say`; one line per alert (newest first, max 6, then "… ve N gelişme daha"), e.g. `🎟 Bilet açıldı — On İkinci Gece · 19 Kas Per 20:00 · son 3 bilet`, `🆕 Yeni etkinlik — Püsküllü Muamma · Başiskele · 20 Kas Cum 20:00`, `⏰ Rezervasyon açılıyor — … · açılış 19 Kas Per 12:00 · etkinlik …`. Each line is a `SelectableLabel` (selectable / copyable, Ctrl+C, right click) with a `Göster ›` link; double click on the text does the same. Click -> Oyunlar tab, row selected, detail opened, alert counted as read. A line whose row no longer exists shows a status message and is read.
- Layout: pool of pre-built lines (never destroyed), wrap width computed from the strip width only and re-applied only when it changed (no feedback loop), `_fit_panes` re-run 60 ms after show/hide. Native colours: new `T.ALERT_ROW` (window colour mixed with blue) and `T.ALERT_EDGE` (= `T.BLUE`) in `pc/theme.py`.
- Row marks: `Row.alert`; `row_tags` adds tag `alert` (faint blue row background, separate from the purple bold "new" mechanism) until the alert is read. Rows are re-rendered only when the set changes (`GridTable.matches`), unchanged alerts rebuild nothing (test).
- Read semantics (my decision, not asked explicitly): an alert is read when its banner line is clicked, when the user opens its row from the list by any route, or via `Tümünü okundu say`.
- Popup `Aç` / tray `Aç` -> `show_main()` -> `reveal_alerts()`: selects the Oyunlar tab when anything is unread and repaints the banner.
- Alert kinds covered: everything the popup receives (ON_SALE, OPENED_SOLD_OUT, OPENING_SOON, DAY_BEFORE, NEW_PLAY, NEW_SESSIONS, REMOVED_PLAY, UNKNOWN_STATUS, SRC_*, PORTAL_*, and the health kinds, which have no row).

## B. Başiskele reservation portal link: where it now appears
Portal = `config.portals` entry `basiskele` (default `https://rezervasyon.basiskele.bel.tr/etkinlikler`, `tiyatro/config.py DEFAULT_PORTALS`); disabled / missing -> no link anywhere. One helper per language, checked for parity: `tiyatro.notify.portal_url` / worker `portalUrl`.
1. PC event detail, "Bilgiler" block, Başiskele events only: row `Rezervasyon` = selectable URL (before `Etkinlik sayfası`), and the `Rezervasyon portalı ↗` button next to `Etkinlik sayfası ↗`. VKV unchanged (İletişim phone/e-mail block), Biletinial plays unchanged (play page). (`Row.source_id` added to tell sources apart.)
2. Telegram event screen `e:` (Başiskele only): line `🏛️ Rezervasyon portalı: <url>` and URL button `🏛️ Rezervasyon portalı`.
3. Telegram notices for Başiskele events (`SRC_NEW` single and digest, `SRC_OPENS`, `SRC_DAY_BEFORE`, `SRC_SOON`, `SRC_OPENED`, `SRC_REOPENED`): last line `🏛️ Rezervasyon portalı: <url>` (one line in a digest). Other sources get none.
4. PC source card: `Portalı aç` kept, now with a tooltip ("Rezervasyon portalını tarayıcıda aç" + URL). The card already reserves the link area before wrapping the name (existing `rewrap`), so the button does not clip at narrow sidebar widths; I did not change that code. No legend entry added (not asked; legends are checked against the Telegram text).
5. Telegram `Kaynaklar` (`src` / `rf`): was missing; now a button `🏛️ Başiskele rezervasyon portalı` under the three `aç` buttons.

## Evidence
- `tests/test_wave22_alerts.py` (18) and `tests/test_wave22_portal.py` (17): wording, store persistence/dedupe/cap/garbage, banner hidden/visible, popup stays generic, row tag, click/double-click/open-row/gone-row, restart restores unread only, popup `Aç` selects the tab, copyable text, "+N", real-window geometry (inside the strip, nothing clipped, repeated renders do not change any size, narrow window wraps without widening, list keeps a usable height), notices of every Başiskele kind, VKV none, config-driven, digest, PC detail row/button/toggle on row change, source-card button + tooltip, Python/JS parity of the portal URL (`node`).
- Worker: 2 new tests, 1 existing updated (Kaynaklar now has 4 URL buttons): `npm test` 100 pass.
- Full `.venv/Scripts/python -m pytest -q`: 1324 passed, 1 failed on first run (`tests/test_contract.py` counted 3 URL buttons on the Kaynaklar screen; now 4 with the portal button, test updated, file re-run: 26 passed). The rest of the suite was not re-run after that one-line test change. `npm test`: 100 pass.

## Not verified / limits
- I did not SEE the window: PrintWindow cannot capture Tk, so colours (blue tint on rows and banner), the look of the banner and the real appearance of the buttons are unseen; only geometry via `winfo_*` is asserted.
- "Satış durumu değişti" from the brief: no such event kind exists in the code (grep of all emitters), so no line with that wording; the nearest kinds are ON_SALE / OPENED_SOLD_OUT / UNKNOWN_STATUS ("Bilinmeyen durum").
- Alerts are recorded only while the PC app runs (the popup source); alerts handled by GitHub while the PC was off are not in the banner (they were never in the popup either).
- Tooltip placement / `Double-Button-1` binding cannot be synthesised in Tk; tested through the bound call and `bind()` presence.
- Telegram button / line text for VKV and Biletinial events intentionally unchanged.
