"""Cross-language contract: the Worker (worker/src/menu.js) and the Python side read and
write the same config.json / state.json. Runs ``node`` (skipped when unavailable); no
network."""
from __future__ import annotations

import copy
import json
import pathlib
import shutil
import subprocess
from datetime import datetime, timedelta

import pytest

from pc import configedit
from pc.viewmodel import until_text
from tiyatro.config import config_from_dict
from tiyatro.models import ISTANBUL
from tiyatro.parser import parse_play
from tiyatro.state import diff, empty_state

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
MENU = (ROOT / "worker" / "src" / "menu.js").as_uri()
BASE = "https://biletinial.com/tr-tr/tiyatro/"
NOW = datetime(2026, 11, 13, 9, 0, tzinfo=ISTANBUL)

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

# Reads {"config", "state", "applies": [[route...]], "renders": [route...]} from stdin.
# Route strings may use "{h:<slug>}" for the play hash. Prints the results as JSON.
SCRIPT = """
import { apply, render, h } from %s;
let raw = "";
for await (const chunk of process.stdin) raw += chunk;
const job = JSON.parse(raw);
const fill = (r) => r.replace(/\\{h:([^}]+)\\}/g, (_, s) => h(s));
const applied = [];
let cfg = job.config;
for (const route of job.applies) { cfg = apply(fill(route), cfg, job.state); applied.push(cfg); }
const rendered = job.renders.map((r) => render(fill(r), job.config, job.state, job.now ?? Date.now()));
process.stdout.write(JSON.stringify({ applied, rendered }));
""" % json.dumps(MENU)


def node(job: dict) -> dict:
    out = subprocess.run(["node", "--input-type=module", "-e", SCRIPT],
                         input=json.dumps(job, ensure_ascii=False), capture_output=True,
                         text=True, encoding="utf-8", timeout=60, check=True)
    return json.loads(out.stdout)


START_CONFIG = {
    "plays": {
        "kusursuz-bir-aile": {
            "active": True,
            "targets": [{"id": "y", "name": "Yetişkin", "people": 2,
                         "rule": {"days": [4, 5], "from_time": "18:00", "to_time": None,
                                  "overrides": {"5": {"from": "14:00", "to": None}},
                                  "exclude_dates": ["2030-03-02"]}}],
            "tag_overrides": {},
        },
        "kucuk-prens": {"active": False, "targets": [], "tag_overrides": {}},
    },
    "sources": ["https://biletinial.com/tr-tr/mekan/ornek-mekan"],
    "paused": False,
    "version": 1,
}
START_STATE = {"plays": {"kusursuz-bir-aile": {"title": "Kusursuz", "tags": [],
                                               "sessions": {}},
                         "kucuk-prens": {"title": "Küçük Prens", "tags": ["çocuk"],
                                         "sessions": {}},
                         "yeni-oyun": {"title": "Yeni", "tags": [], "sessions": {}}},
               "meta": {}, "emitted": {}}
KB = "{h:kusursuz-bir-aile}"
KP = "{h:kucuk-prens}"
YN = "{h:yeni-oyun}"
ROUTES = [f"ta:{KP}:1", f"tg:{KP}:y", f"hp:{KB}:y:4", f"hd:{KB}:y:wd", f"hd:{KB}:y:d6:1",
          f"hd:{KB}:y:all", f"hf:{KB}:y:19", f"ht:{KB}:y:20", f"ht:{KB}:y:none",
          f"hx:{KB}:y:301128:1", f"hs:{KP}:y:rm", f"ta:{YN}:1", f"ta:{KB}:0", "pz:1", "pz:0"]


def test_worker_apply_outputs_are_valid_python_configs():
    res = node({"config": START_CONFIG, "state": START_STATE, "applies": ROUTES,
                "renders": []})
    assert len(res["applied"]) == len(ROUTES)
    for route, cfg in zip(ROUTES, res["applied"]):
        config_from_dict(cfg)  # must not raise
    final = config_from_dict(res["applied"][-1])
    # activation adds no default target (wave 4 A1); the y target added then removed is gone
    assert final.plays["kucuk-prens"].active and final.plays["kucuk-prens"].targets == []
    assert final.plays["yeni-oyun"].active and final.plays["yeni-oyun"].targets == []
    assert final.plays["kusursuz-bir-aile"].targets[0].people == 4
    assert final.plays["kusursuz-bir-aile"].targets[0].rule.exclude_dates[-1] == "2030-11-28"


def test_activation_creates_no_target_parity_with_pc():  # wave 4 A1
    res = node({"config": START_CONFIG, "state": START_STATE,
                "applies": [f"ta:{KP}:1"], "renders": []})
    py = copy.deepcopy(START_CONFIG)
    configedit.set_active(py, "kucuk-prens", True, START_STATE["plays"]["kucuk-prens"]["tags"])
    assert res["applied"][0] == py
    res = node({"config": START_CONFIG, "state": START_STATE,
                "applies": [f"ta:{YN}:1"], "renders": []})
    py = copy.deepcopy(START_CONFIG)
    configedit.set_active(py, "yeni-oyun", True, [])
    assert res["applied"][0] == py


def _python_state():
    files = {"on-ikinci-gece-kocaeli-bb": "play_on_ikinci_gece.html",
             "hic-kimsenin-oykusu-kocaeli-buyuksehir-sehir-tiyatrolari":
                 "play_hic_kimsenin.html",
             "kusursuz-dunyaya-yolculuk-kocaeli-bb": "play_kusursuz.html"}
    plays = [parse_play((FIXTURES / name).read_text(encoding="utf-8"), BASE + slug)
             for slug, name in files.items()]
    raw_cfg = {"plays": {p.slug: {"active": True, "targets": [
        {"id": "y", "name": "Yetişkin", "people": 2,
         "rule": {"days": None, "from_time": None, "to_time": "20:00", "overrides": {},
                  "exclude_dates": []}}]} for p in plays}}
    state, _events = diff(empty_state(), plays, config_from_dict(raw_cfg), NOW)
    return json.loads(json.dumps(state)), raw_cfg, plays


def test_worker_renders_a_python_state():
    state, raw_cfg, plays = _python_state()
    routes = [f"p:{{h:{p.slug}}}" for p in plays] + ["tl", "up", "m"]
    res = node({"config": raw_cfg, "state": state, "applies": [], "renders": routes})
    screens = res["rendered"]
    for p, screen in zip(plays, screens):
        assert "bulunamadı" not in screen["text"]
        title = p.title.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        assert title in screen["text"]
        assert "Seanslar" in screen["text"] and until_text("20:00") in screen["text"]
        assert screen["keyboard"]
    tl, up, main = screens[len(plays):]
    assert all(p.title.split()[0] in tl["text"] for p in plays)
    assert "Yaklaşan açılışlar" in up["text"]
    assert "Takip edilen oyun: 3" in main["text"]


@pytest.mark.parametrize("hhmm", ["00:00", "09:30", "12:00", "16:00", "18:00", "19:00",
                                  "20:00", "21:15", "22:00", "23:59"])
def test_until_text_parity(hhmm):  # final review C2: same Turkish suffix on both sides
    raw_cfg = copy.deepcopy(START_CONFIG)
    raw_cfg["plays"]["kusursuz-bir-aile"]["targets"][0]["rule"]["to_time"] = hhmm
    res = node({"config": raw_cfg, "state": START_STATE, "applies": [],
                "renders": [f"ts:{KB}:y"]})
    assert until_text(hhmm) in res["rendered"][0]["text"]


def test_worker_play_screen_shows_summary_and_cast_from_python_state():  # wave 3a
    state, raw_cfg, plays = _python_state()
    by_slug = {p.slug: p for p in plays}
    gece = by_slug["on-ikinci-gece-kocaeli-bb"]
    hic = by_slug["hic-kimsenin-oykusu-kocaeli-buyuksehir-sehir-tiyatrolari"]
    assert state["plays"][gece.slug]["cast"][0] == "İbrahim Aydın"
    res = node({"config": raw_cfg, "state": state, "applies": [],
                "renders": [f"p:{{h:{gece.slug}}}", f"p:{{h:{hic.slug}}}"]})
    text = res["rendered"][0]["text"]
    assert "📝 Rüzgâr uğuldar, deniz yırtılır Gece ikiye" in text
    assert "Oyuncular: İbrahim Aydın, İlker Bağlam, Sarper Saydam, Senem Akman Muratoğlu, " \
           "Sevcan Kuş Efe, Su Özdemir (+5)" in text
    assert len(text) <= 4096
    hic_text = res["rendered"][1]["text"]
    assert "📝 Bir tren kompartımanında" in hic_text and "Oyuncular:" not in hic_text


# ---- wave 3c: portals ------------------------------------------------------------------

PORTALS = [{"id": "basiskele", "name": "Başiskele Belediyesi rezervasyon",
            "url": "https://rezervasyon.basiskele.bel.tr/etkinlikler", "enabled": True},
           {"id": "diger", "name": "Diğer", "url": "https://d.example/x", "enabled": False}]


def test_worker_write_keeps_portals_untouched():
    cfg = copy.deepcopy(START_CONFIG)
    cfg["portals"] = copy.deepcopy(PORTALS)
    res = node({"config": cfg, "state": START_STATE, "applies": ROUTES, "renders": []})
    for route, out in zip(ROUTES, res["applied"]):
        assert out["portals"] == PORTALS, route
        assert config_from_dict(out).portals == PORTALS
    # a config that never had the key stays without it (Python then supplies the default)
    res = node({"config": START_CONFIG, "state": START_STATE, "applies": ROUTES,
                "renders": []})
    assert all("portals" not in out for out in res["applied"])
    from tiyatro.config import DEFAULT_PORTALS
    assert config_from_dict(res["applied"][-1]).portals == DEFAULT_PORTALS


def test_pc_edits_keep_portals_untouched():
    raw = copy.deepcopy(START_CONFIG)
    raw["portals"] = copy.deepcopy(PORTALS)
    configedit.set_paused(raw, True)
    configedit.set_active(raw, "kucuk-prens", True, ["çocuk"])
    configedit.set_people(raw, "kusursuz-bir-aile", "y", 3)
    assert raw["portals"] == PORTALS and config_from_dict(raw).portals == PORTALS


def test_worker_renders_python_produced_portal_state():
    from tests.test_wave3c_portals import page, real_empty
    from tiyatro.portals import parse_portal
    from tiyatro.runner import _apply_portals, _prepare
    state, raw_cfg, _plays = _python_state()
    cfg = config_from_dict({**raw_cfg, "portals": PORTALS})
    state = _prepare(state)
    empty = parse_portal(real_empty())
    _apply_portals(state, cfg, {"basiskele": empty}, NOW)
    res = node({"config": raw_cfg, "state": json.loads(json.dumps(state)), "applies": [],
                "renders": ["m", "up"]})
    for screen in res["rendered"]:
        assert "🏛️ Başiskele portalı: boş" in screen["text"] and len(screen["text"]) <= 4096
    opened = parse_portal(page('<div class="c"><h4>Kuğu Gölü</h4>'
                               '<a href="/e/1">Rezervasyon Yap</a></div>'
                               '<div class="c"><h4>Pinokyo</h4>'
                               '<a href="/e/2">Rezervasyon Yap</a></div>'))
    _apply_portals(state, cfg, {"basiskele": opened}, NOW + timedelta(minutes=5))
    _apply_portals(state, cfg, {"basiskele": opened}, NOW + timedelta(minutes=10))  # confirmed
    res = node({"config": raw_cfg, "state": json.loads(json.dumps(state)), "applies": [],
                "renders": ["m", "up"]})
    for screen in res["rendered"]:
        assert "🏛️ Başiskele portalı: açık (2)" in screen["text"]


def test_worker_renders_python_produced_source_state():  # wave 4 B
    from datetime import datetime
    from tiyatro import sources as S
    from tiyatro.models import ISTANBUL
    from tiyatro.runner import _prepare
    state, raw_cfg, _plays = _python_state()
    state = _prepare(state)
    now = datetime.now(ISTANBUL)
    S.record_check(state, "biletinial", now, True, items=[["a", "A", []]])
    S.record_check(state, "vkv", now, False, error="Timeout")
    res = node({"config": raw_cfg, "state": json.loads(json.dumps(state)), "applies": [],
                "renders": ["src", "rf"]})
    for screen in res["rendered"]:
        assert "✅ <b>Biletinial</b>" in screen["text"]
        assert "❌ <b>VKV Ford Otosan Gölcük</b>" in screen["text"]
        assert "Hata: Timeout" in screen["text"] and len(screen["text"]) <= 4096


def test_worker_follow_route_matches_python_and_renders_python_events():  # wave 4 D+E
    from tests.test_wave4d_vkv import D541, LIST, T0, Web
    from tiyatro import srcevents as S
    from tiyatro.runner import _prepare
    state, raw_cfg, _plays = _python_state()
    state = _prepare(state)
    scan = S.scan_vkv(Web().fetch, T0)
    S.fold_listings(state, config_from_dict(raw_cfg), "vkv", scan.listings, T0)
    state = json.loads(json.dumps(state))
    eid = "vkv:541"
    route = "ef:{h:%s}:0:0" % eid
    res = node({"config": raw_cfg, "state": state, "applies": [route, route], "now": int(T0.timestamp() * 1000),
                "renders": ["pl:0", "ef:{h:%s}:1:0" % eid]})
    py = copy.deepcopy(raw_cfg)
    configedit.set_event_follow(py, eid, False)
    assert res["applied"][0] == py == res["applied"][1]
    config_from_dict(res["applied"][0])  # a valid Python config
    text = res["rendered"][0]["text"]
    # wave 7: one unified list; Tür is derived (kind holds no genre word) -> italic
    assert "Ceviz Adam Müzikali" in text and "Kitle: Çocuk" in text and "Tür: <i>Müzikal</i>" in text
    assert "Rez. açılış: 19 Kas Per 12:00 (4 gün sonra)" in text and len(text) <= 4096
    assert "Diğer kaynaklar" not in text and "VKV" in text  # source label, no separate screen


# ---- wave 7: unified list, seen list, link buttons -------------------------------------------

def test_worker_seen_list_is_ignored_by_python_and_survives_pc_edits():
    state, raw_cfg, plays = _python_state()
    slug = plays[0].slug
    res = node({"config": raw_cfg, "state": state, "applies": [f"p:{{h:{slug}}}:0:0", "pl:0"],
                "renders": []})
    cfg = res["applied"][0]
    assert sorted(cfg["seen"]) == sorted(p.slug for p in plays)      # first use: all seen
    config_from_dict(cfg)                                              # a valid Python config
    assert res["applied"][1] == cfg                                    # idempotent
    py = copy.deepcopy(cfg)
    configedit.set_active(py, slug, False)
    configedit.set_event_follow(py, "vkv:1", True)
    assert py["seen"] == cfg["seen"]                                   # PC edits keep it


@pytest.mark.parametrize("ret", ["", ":0", ":3", ":i2"])
def test_ta_and_ef_routes_with_return_hint_equal_the_pc_edit(ret):
    res = node({"config": START_CONFIG, "state": START_STATE,
                "applies": [f"ta:{KP}:1{ret}"], "renders": []})
    py = copy.deepcopy(START_CONFIG)
    configedit.set_active(py, "kucuk-prens", True)
    assert res["applied"][0] == py


def test_worker_renders_python_state_lists_and_item_screens():
    state, raw_cfg, plays = _python_state()
    now = int(NOW.timestamp() * 1000)
    routes = ["pl:0", "m"] + [f"p:{{h:{p.slug}}}:0:0" for p in plays] + ["src"]
    raw_cfg = {**raw_cfg, "sources": ["https://biletinial.com/tr-tr/mekan/ornek-mekan"]}
    res = node({"config": raw_cfg, "state": state, "applies": [], "renders": routes, "now": now})
    lst, main = res["rendered"][:2]
    assert all(p.title.split()[0] in lst["text"] for p in plays) and len(lst["text"]) <= 4096
    assert any("Oyunlar ve etkinlikler" in b["text"] for row in main["keyboard"] for b in row)
    for p, screen in zip(plays, res["rendered"][2:-1]):
        urls = [b["url"] for row in screen["keyboard"] for b in row if "url" in b]
        assert p.url in urls                                            # Oyun sayfası
        session_urls = {s.url for s in p.sessions if s.url}
        assert set(urls) - {p.url} and set(urls) - {p.url} <= session_urls  # Seans buttons (page 1)
    src = res["rendered"][-1]
    assert len([b for row in src["keyboard"] for b in row if "url" in b]) == 3
