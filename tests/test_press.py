"""Press brake simulation (web/press.js): bend order, how the part sits on the die, tools.

Runs press.js with Node on synthetic channels (U and hat, made here) and on the customer sample when
present. The U and hat channels must be planned without hitting the tools."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

import shapes
from sheetflat.core.profiles import ProfileStore
from sheetflat.core.project import Project

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT.parent / "Assem.STEP"
node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def _folds(step, tmp_path, must_fit):
    pr = Project(ProfileStore(tmp_path / "p.json"), step.stem, step.name)
    pr.load_step(str(step))
    return [{"name": r.name, "fold": pr.fold_json(r), "mustFit": must_fit} for r in pr.parts if r.pieces and len(r.pieces) == 1]


def _run(data, tmp_path):
    f = tmp_path / "folds.json"
    f.write_text(json.dumps(data), encoding="utf-8")
    out = subprocess.run(["node", str(ROOT / "tests" / "press_check.mjs"), str(f)], capture_output=True, text=True, check=True).stdout
    return out


@node
def test_channels_are_planned_without_collisions(tmp_path):
    parts = {
        "U_Channel": shapes.bent_profile(1.0, 1.0, [40, 80, 40], [90, 90], 300),
        "Hat_Channel": shapes.bent_profile(1.0, 1.0, [25, 40, 60, 40, 25], [-90, 90, 90, -90], 300),
        "Z_Bracket": shapes.bent_profile(2.0, 2.0, [50, 60, 50], [90, -90], 120),
    }
    data = []
    for name, shp in parts.items():
        step = tmp_path / f"{name}.step"
        shapes.write_step(shp, step)
        got = _folds(step, tmp_path, True)
        assert len(got) == 1
        got[0]["name"] = name
        data += got
    out = _run(data, tmp_path)
    assert out.strip().splitlines()[-1] == "FAILS 0", out
    nb = {d["name"]: len(d["fold"]["pieces"][0]["bends"]) for d in data}
    assert nb == {"U_Channel": 2, "Hat_Channel": 4, "Z_Bracket": 2}, nb
    print(out)


@node
@pytest.mark.skipif(not SAMPLE.exists(), reason="customer sample not present")
def test_customer_sample_plans(tmp_path):
    out = _run(_folds(SAMPLE, tmp_path, False), tmp_path)
    assert out.strip().splitlines()[-1] == "FAILS 0", out
