"""3D fold animation: folding the flat pattern completely must give the part as modelled.

The folding runs in the browser (web/fold.js); this test runs that same file with Node on every
sheet part of the demo cabinet (in the repository) and of the customer sample (when present)."""
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from sheetflat.core.profiles import ProfileStore
from sheetflat.core.project import Project

ROOT = Path(__file__).resolve().parents[1]
FILES = [ROOT / "docs" / "demo" / "DemoCabinet.step", ROOT.parent / "Assem.STEP"]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.parametrize("step", [f for f in FILES if f.exists()], ids=lambda f: f.name)
def test_folding_the_flat_gives_the_modelled_part(step, tmp_path):
    pr = Project(ProfileStore(tmp_path / "p.json"), step.stem, step.name)
    pr.load_step(str(step))
    data = []
    for r in pr.parts:
        if not r.pieces or len(r.pieces) > 1:
            continue
        m = pr.mesh_json(r)
        data.append({"name": r.name, "fold": pr.fold_json(r), "real": sorted(h - l for l, h in zip(*m["bbox"]))})
    assert data
    f = tmp_path / "folds.json"
    f.write_text(json.dumps(data), encoding="utf-8")
    out = subprocess.run(["node", str(ROOT / "tests" / "fold_check.mjs"), str(f)], capture_output=True, text=True, check=True).stdout
    worst = float(out.strip().splitlines()[-1].split()[-1])
    assert worst < 0.05, out


def test_fold_model_structure(tmp_path):
    step = FILES[0]
    pr = Project(ProfileStore(tmp_path / "p.json"), step.stem, step.name)
    pr.load_step(str(step))
    for r in pr.parts:
        if not r.pieces:
            continue
        m = pr.fold_json(r)["pieces"][0]
        assert len(m["bends"]) == len(r.pieces[0].bends)                     # every bend is in the fold tree
        assert sum(1 for x in m["regions"] if x["kind"] == "flange") == len(m["bends"]) + 1   # a tree: n bends, n + 1 flanges
        for b in m["bends"]:
            assert b["chain"][0] == b["id"] and b["s"] in (1, -1) and b["ba"] > 0
