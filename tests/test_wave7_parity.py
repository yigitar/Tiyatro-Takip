"""Wave 7 parity: the Worker's unified Telegram list derives the same Kitle / Tür / Zaman /
Hedef / Rez. açılış (and the same "derived" marking) as the PC list for the same state.json /
config.json. Runs ``node`` (skipped when unavailable); no network."""
from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
from datetime import datetime, timedelta

import pytest

from pc.viewmodel import build_rows, with_events
from tiyatro import srcevents as S
from tiyatro.config import config_from_dict
from tiyatro.models import ISTANBUL

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
MENU = (ROOT / "worker" / "src" / "menu.js").as_uri()
NOW = datetime(2026, 11, 15, 12, 0, tzinfo=ISTANBUL)

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

SCRIPT = """
import { listItems } from %s;
let raw = "";
for await (const chunk of process.stdin) raw += chunk;
const job = JSON.parse(raw);
process.stdout.write(JSON.stringify(job.nows.map((now) => listItems(job.config, job.state, now))));
""" % json.dumps(MENU)

KEYS = ("audience", "genre", "zaman", "target", "rez")


def worker_items(config: dict, state: dict, nows: list[datetime]) -> list[list[dict]]:
    job = {"config": config, "state": state, "nows": [int(n.timestamp() * 1000) for n in nows]}
    out = subprocess.run(["node", "--input-type=module", "-e", SCRIPT],
                         input=json.dumps(job, ensure_ascii=False), capture_output=True,
                         text=True, encoding="utf-8", timeout=60, check=True)
    return json.loads(out.stdout)


def python_rows(config: dict, state: dict, now: datetime):
    cfg = config_from_dict(config)
    rows = build_rows(state, cfg, set(), set(), now)
    return with_events(rows, S.event_views(state, cfg, now), now)


def check(config: dict, state: dict, nows: list[datetime]) -> int:
    compared = 0
    for now, items in zip(nows, worker_items(config, state, nows)):
        rows = python_rows(config, state, now)
        assert [i["id"] for i in items] == [r.slug for r in rows], now
        for item, row in zip(items, rows):
            where = f"{item['id']} @ {now}"
            assert item["kind"] == ("event" if row.is_event else "play"), where
            assert item["kitle"] == row.audience, where
            assert item["tur"] == row.genre, where
            assert item["zaman"] == row.zaman, where
            assert item["hedef"] == row.target_text, where
            assert item["rez"] == row.rez_text, where
            assert set(item["derived"]) == set(row.derived) & set(KEYS), where
            assert item["followed"] == row.active, where
            if row.is_event:   # wave 15: Özet / Oyuncular / Yapım / Süre / Yaş / Ücret as stated
                assert item["ozet"] == row.summary, where
                assert item["oyuncular"] == row.cast, where
                assert [tuple(c) for c in item["yapim"]] == list(row.credits), where
                assert (item["sure"], item["yas"], item["ucret"]) == (
                    row.duration, row.age, row.price), where
                # wave 17: fields borrowed from the same-named play, derived venue
                assert set(item["borrowed"]) == set(row.borrowed), where
                assert set(item["borrowedCredits"]) == set(row.borrowed_credits), where
                assert item["borrowedFrom"] == row.borrowed_from, where
                assert item["venueDerived"] == row.venue_derived, where
            compared += 1
    return compared


def _fixture() -> tuple[dict, dict]:
    return (json.loads((FIXTURES / "parity_config.json").read_text(encoding="utf-8")),
            json.loads((FIXTURES / "parity_state.json").read_text(encoding="utf-8")))


def test_same_state_fixtures_give_identical_derived_fields_at_several_times():
    config, state = _fixture()
    nows = [NOW, NOW + timedelta(hours=5), NOW.replace(hour=0, minute=10),
            NOW + timedelta(days=1, hours=1), NOW + timedelta(days=3), NOW + timedelta(days=14)]
    assert check(config, state, nows) > 60


def test_fixture_covers_every_branch_it_claims_to():
    config, state = _fixture()
    rows = {r.slug: r for r in python_rows(config, state, NOW)}
    rez = {r.rez_text.split(" (")[0] if "(" in r.rez_text else r.rez_text for r in rows.values()}
    for wanted in ("açık", "tükendi", "—", "doldu", "duyuru bekleniyor", "açılmış olabilir"):
        assert wanted in rez, wanted
    assert any("(yarın)" in r.rez_text for r in rows.values())
    assert any("(bugün)" in r.rez_text for r in rows.values())
    assert {r.zaman for r in rows.values()} >= {"gündüz", "gece", "gündüz+gece", ""}
    assert {r.audience for r in rows.values()} == {"Çocuk", "Yetişkin"}
    assert "" in {r.genre for r in rows.values()}            # theatre event without a genre word
    other = rows["bk:20261126-1500-atolye"]                  # wave 10: clearly not theatre
    assert other.genre == "Diğer – Atölye" and "genre" in other.derived
    assert rows["bk:20261126-1700-tiyatro-atolyesi"].genre == ""   # "Tiyatro" vetoes it
    assert rows["bk:20261120-1100-satir-arasi-ebeveynlik"].genre == ""  # unknown theatre keeps "—"
    assert {"Müzikal", "Komedi", "Stand-up", "Dram"} <= {r.genre for r in rows.values()}
    assert "arsivlenmis" not in rows and "arsivlenmis-takipte" in rows  # archived rule
    assert rows["kalkti"].is_removed is True  # shown with the KALKTI marker on both sides
    assert "vkv:548" not in rows and "vkv:547" in rows       # 3 h window
    assert rows["bozuk-veri"].audience in ("Çocuk", "Yetişkin")


def test_events_scanned_from_real_page_fixtures_match(tmp_path):
    from tests.test_wave4d_vkv import LIST, T0, Web
    from tiyatro.runner import _prepare
    from tiyatro.state import empty_state
    config, _ = _fixture()
    state = _prepare(empty_state())
    scan = S.scan_vkv(Web().fetch, T0)
    S.fold_listings(state, config_from_dict(config), "vkv", scan.listings, T0)
    state = json.loads(json.dumps(state))
    assert state["source_events"], LIST[:0]
    nows = [T0, T0 + timedelta(days=2), T0 + timedelta(days=9)]
    assert check(config, state, nows) >= 3


def test_biletinial_play_fixtures_match():
    from tests.test_contract import _python_state
    state, raw_cfg, _plays = _python_state()
    nows = [NOW.replace(year=2026, month=11, day=13, hour=9), NOW]
    assert check(raw_cfg, state, nows) >= 3


def test_known_gap_fixed_worker_no_longer_blanks_kitle_for_plays_without_age():
    """Wave 5 caveat: the Worker used the strict rule (blank Kitle without age/tag)."""
    config, state = _fixture()
    items = {i["id"]: i for i in worker_items(config, state, [NOW])[0]}
    assert items["genre-yok-yas-yok"]["kitle"] == "Yetişkin"
    assert items["genre-yok-yas-yok"]["tur"] == ""   # no "Tiyatro" filler (wave 9)
    assert items["yetiskin-oyunu-sadece-kitle"]["tur"] == "Dram"   # genre word found in the summary


def test_non_theatre_event_gets_diger_tur_on_both_sides():
    config, state = _fixture()
    items = {i["id"]: i for i in worker_items(config, state, [NOW])[0]}
    assert items["bk:20261126-1500-atolye"]["tur"] == "Diğer – Atölye"
    assert "genre" in items["bk:20261126-1500-atolye"]["derived"]
    assert items["bk:20261126-1700-tiyatro-atolyesi"]["tur"] == ""
    assert items["bk:20261120-1100-satir-arasi-ebeveynlik"]["tur"] == ""


def test_event_info_fields_match_including_odd_whitespace_and_bad_types():
    config, state = _fixture()
    items = {i["id"]: i for i in worker_items(config, state, [NOW])[0]}
    rows = {r.slug: r for r in python_rows(config, state, NOW)}
    full = items["vkv:541"]
    assert full["ozet"] == "Ceviz Adam sahnede.\n\nIkinci paragraf" == rows["vkv:541"].summary
    assert full["oyuncular"] == ["Ayşe Yılmaz", "Mehmet Kaya"] == rows["vkv:541"].cast
    assert full["yapim"] == [["Yönetmen", "Ali Veli"]]
    assert (full["sure"], full["yas"], full["ucret"]) == ("60 dk", "4+", "Ücretsiz")
    # ("summary" in Row.derived marks the list's status column, not the Özet text)
    bad = items["vkv:542"]                                   # hand-edited wrong types: own info all empty
    # (wave 17: the event shares its title with the play "kabare-basligi", whose age is borrowed)
    assert (bad["ozet"], bad["oyuncular"], bad["yapim"], bad["sure"], bad["ucret"]) == (
        "", [], [], "", "")
    assert bad["yas"] == "13+" and bad["borrowed"] == ["age"] == list(rows["vkv:542"].borrowed)


def test_wave17_borrowing_and_derived_venue_match_on_both_sides():
    config, state = _fixture()
    items = {i["id"]: i for i in worker_items(config, state, [NOW])[0]}
    rows = {r.slug: r for r in python_rows(config, state, NOW)}
    u, row = items["bk:20261117-2000-unutmak"], rows["bk:20261117-2000-unutmak"]
    assert u["borrowedFrom"] == row.borrowed_from == "Unutmak"
    assert set(u["borrowed"]) == set(row.borrowed) == {"summary", "cast", "credits", "age", "genre"}
    assert u["sure"] == row.duration == "80 dk"                # own value wins, not borrowed
    assert u["tur"] == row.genre == "Dram" and "genre" in u["derived"]
    assert [k for k, _v in u["yapim"]][0] == "Sahneleyen"      # own entry first
    assert dict(map(tuple, u["yapim"]))["Sahneleyen"] == "Kocaeli Şehir Tiyatroları"
    assert set(u["borrowedCredits"]) == set(row.borrowed_credits) == {"Yazan", "Yönetmen"}
    assert u["venueDerived"] is True and row.venue_derived is True
    twin = items["bk:20261118-2000-ikiz-ad"]                   # two plays with that title: never
    assert twin["borrowed"] == [] and twin["borrowedFrom"] == "" and twin["ozet"] == ""
