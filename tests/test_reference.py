"""Cross-check against the DXF files SolidWorks produced for the same assembly (the shop's answer files).

Skipped when the customer sample files are not on this machine (they are not in the repository).
"""
import collections
import io
import sys
from pathlib import Path

import ezdxf
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import compare_reference as C  # noqa: E402

from sheetflat.core.dxf_export import build_dxf, dxf_bytes  # noqa: E402
from sheetflat.core.layout import Sheet  # noqa: E402
from sheetflat.core.profiles import ProfileStore  # noqa: E402
from sheetflat.core.project import Project  # noqa: E402

BASE = ROOT.parent
SAMPLE = BASE / "Assem.STEP"
REFDIR = BASE / "380x270x200 (Small Slope)" / "380x270x200 (Small Slope)"
REF22 = REFDIR / "ZN#22_แบบตัด_BigParcelBasket.DXF"
REF14 = REFDIR / "ZN#14_แบบตัด_BackHook_BigParcelBasket.DXF"
pytestmark = pytest.mark.skipif(not (SAMPLE.exists() and REF22.exists() and REF14.exists()), reason="sample files missing")


@pytest.fixture(scope="module")
def rows():
    return C.compare(SAMPLE, [REF22, REF14])


def test_every_answer_outline_is_matched(rows):
    parts = sorted(r["part"] for r in rows)
    assert parts == sorted(["Body", "Front", "Bottom", "Hood2", "BackHook_v2", "BackHook_v2",
                            "BackHook_part_v2", "BackHook_part_v2"])


@pytest.mark.parametrize("key", ["Body", "Front", "Bottom", "Hood2", "BackHook_v2", "BackHook_part_v2"])
def test_part_matches_answer(rows, key):
    for r in [x for x in rows if x["part"] == key]:
        ow, oh = r["ours_wh"]
        rw, rh = r["ref_wh"]
        assert abs(ow - rw) < 0.005 and abs(oh - rh) < 0.005, r          # flat size
        assert r["iou"] > 0.9999, r                                        # same shape (area sampling noise ~0.03 mm2)
        assert abs(r["len_ours"] - r["len_ref"]) < 0.05, r                 # same cut length
        assert r["holes_ours"] == r["holes_ref"] and r["holes_unmatched"] == 0 and r["hole_max_err"] < 0.005, r
        assert r["dev_ours_to_ref"] < 0.01 and r["dev_ref_to_ours"] < 0.01, r   # no line more than 0.01 mm off


def test_entity_counts_match_answer(tmp_path):
    pr = Project(ProfileStore(tmp_path / "p.json"), "Assem", "Assem.STEP")
    pr.load_step(str(SAMPLE))
    answers = {"ZN#22": REF22, "ZN#14": REF14}
    for g in pr.groups:
        keep = [pl for pl in g.sheets[0].placed if pl.item.name != "Hook"]     # the answer file has no Hook
        doc = ezdxf.read(io.StringIO(dxf_bytes(build_dxf(Sheet(0, keep), bend_lines=pr.settings["bend_lines"])).decode()))
        ours = collections.Counter(e.dxftype() for e in doc.modelspace())
        ref = collections.Counter(e.dxftype() for e in ezdxf.readfile(str(answers[g.code])).modelspace())
        assert ours == ref, (g.code, ours, ref)
