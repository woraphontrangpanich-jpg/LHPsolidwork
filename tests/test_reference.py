"""Cross-check against the DXF files SolidWorks produced for the same assembly (shop's current manual result)."""
import math
from pathlib import Path

import ezdxf
import pytest

from sheetflat.core.profiles import ProfileStore
from sheetflat.core.project import Project
from sheetflat.core.unfold import prim_points

BASE = Path(__file__).resolve().parents[2]
SAMPLE = BASE / "Assem.STEP"
REFDIR = BASE / "380x270x200 (Small Slope)" / "380x270x200 (Small Slope)"
REF22 = REFDIR / "ZN#22_แบบตัด_BigParcelBasket.DXF"
REF14 = REFDIR / "ZN#14_แบบตัด_BackHook_BigParcelBasket.DXF"
pytestmark = pytest.mark.skipif(not (SAMPLE.exists() and REF22.exists() and REF14.exists()), reason="sample files missing")


def dxf_cut_length(path):
    total = 0.0
    for e in ezdxf.readfile(str(path)).modelspace():
        t = e.dxftype()
        if t == "LINE":
            total += math.dist(e.dxf.start, e.dxf.end)
        elif t == "CIRCLE":
            total += 2 * math.pi * e.dxf.radius
        elif t == "ARC":
            d = (e.dxf.end_angle - e.dxf.start_angle) % 360
            total += math.radians(d) * e.dxf.radius
        elif t in ("LWPOLYLINE", "ELLIPSE", "SPLINE"):
            pts = [(p.x, p.y) for p in e.flattening(0.01)] if t != "LWPOLYLINE" else [(p[0], p[1]) for p in e.get_points()]
            total += sum(math.dist(a, b) for a, b in zip(pts, pts[1:]))
    return total


@pytest.fixture(scope="module")
def project(tmp_path_factory):
    store = ProfileStore(tmp_path_factory.mktemp("prof") / "p.json")
    pr = Project(store, "Assem", "Assem.STEP")
    pr.load_step(str(SAMPLE))
    return pr


def our_length(pr, names, mult):
    tot = 0.0
    for p in pr.parts:
        if p.name in names:
            for pc in p.pieces:
                for q in pc.prims:
                    pts = prim_points(q)
                    tot += sum(math.dist(a, b) for a, b in zip(pts, pts[1:])) * mult.get(p.name, 1)
    return tot


def test_zn22_total_cut_length(project):
    ref = dxf_cut_length(REF22)
    ours = our_length(project, {"Body", "Front", "Bottom", "Hood2"}, {})
    assert ours == pytest.approx(ref, rel=0.005)


def test_zn14_total_cut_length(project):
    ref = dxf_cut_length(REF14)
    # the reference file has 2x BackHook_v2 and 2x BackHook_part_v2 (no Hook)
    ours = our_length(project, {"BackHook_v2", "BackHook_part_v2"}, {"BackHook_v2": 2, "BackHook_part_v2": 2})
    assert ours == pytest.approx(ref, rel=0.005)
