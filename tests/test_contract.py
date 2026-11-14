"""Cross-language contract: the Worker (worker/src/menu.js) and the Python side read and
write the same config.json / state.json. Runs ``node`` (skipped when unavailable); no
network."""
from __future__ import annotations

import copy
import json
import pathlib
import shutil
import subprocess
from datetime import datetime

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
const rendered = job.renders.map((r) => render(fill(r), job.config, job.state));
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
    assert final.plays["kucuk-prens"].active and [t.id for t in
                                                  final.plays["kucuk-prens"].targets] == ["c"]
    assert final.plays["yeni-oyun"].targets[0].id == "y"
    assert final.plays["kusursuz-bir-aile"].targets[0].people == 4
    assert final.plays["kusursuz-bir-aile"].targets[0].rule.exclude_dates[-1] == "2030-11-28"


def test_activation_default_target_parity_with_pc():  # final review I8
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
