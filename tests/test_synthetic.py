"""Rules from the brief, checked on synthetic parts with known answers."""
import math

import pytest

import shapes
from OCP.TopAbs import TopAbs_SOLID
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS
from sheetflat.core.profiles import ProfileStore
from sheetflat.core.project import Project


@pytest.fixture()
def store(tmp_path):
    return ProfileStore(tmp_path / "p.json")


def run(store, solids):
    pr = Project(store, "t", "t.step")
    kw = [k.lower() for k in store.data["exclude_keywords"]]
    for i, (nm, sh) in enumerate(solids):
        ex = TopExp_Explorer(sh, TopAbs_SOLID)
        pr.parts.append(pr._analyze_part(f"p{i + 1}", nm, 1, TopoDS.Solid(ex.Current()), kw))
    pr.recompute()
    return pr


def flat_len(pr, name):
    p = next(x for x in pr.to_json()["parts"] if x["name"] == name)
    return p, max(p["flat"]["w"], p["flat"]["h"]), min(p["flat"]["w"], p["flat"]["h"])


def test_right_angle_uses_table(store):
    # t=1.0 -> BD 1.75, r_in=1: outside dims a=L1+2, b=L2+2 ; flat = a+b-BD
    L1, L2, W = 40.0, 25.0, 30.0
    pr = run(store, [("L90", shapes.bent_sheet(1.0, L1, L2, 90, 1.0, W))])
    p, long, short = flat_len(pr, "L90")
    assert long == pytest.approx((L1 + 2.0) + (L2 + 2.0) - 1.75, abs=0.01)
    assert short == pytest.approx(W, abs=0.01)
    assert p["flat"]["bends"][0]["method"] == "table"
    assert p["thickness"]["gauge"] == "#19" and p["code"] == "ZN#19"


def test_non_right_angle_uses_k_factor(store):
    L1, L2 = 40.0, 25.0
    pr = run(store, [("L45", shapes.bent_sheet(1.0, L1, L2, 45, 1.0, 30))])
    p, long, _ = flat_len(pr, "L45")
    assert long == pytest.approx(L1 + L2 + math.radians(45) * (1.0 + 0.5), abs=0.01)
    assert p["flat"]["bends"][0]["method"] == "k"
    assert abs(p["flat"]["bends"][0]["angle"] - 45) < 0.01


def test_large_radius_arc_uses_k_factor(store):
    pr = run(store, [("R20", shapes.bent_sheet(1.0, 30, 30, 90, 20.0, 40))])
    p, long, _ = flat_len(pr, "R20")
    assert long == pytest.approx(60 + math.radians(90) * (20.0 + 0.5), abs=0.01)
    assert p["flat"]["bends"][0]["method"] == "k"


def test_k_factor_is_editable(store):
    store.data["profiles"][0]["k_factor"] = 0.4
    pr = run(store, [("L45", shapes.bent_sheet(1.0, 40, 25, 45, 1.0, 30))])
    _, long, _ = flat_len(pr, "L45")
    assert long == pytest.approx(65 + math.radians(45) * (1.0 + 0.4), abs=0.01)


def test_table_is_editable(store):
    for r in store.data["profiles"][0]["table"]:
        if r["thickness"] == 1.0:
            r["bd"] = 2.0
    pr = run(store, [("L90", shapes.bent_sheet(1.0, 40, 25, 90, 1.0, 30))])
    _, long, _ = flat_len(pr, "L90")
    assert long == pytest.approx(42 + 27 - 2.0, abs=0.01)


def test_thickness_not_in_table_falls_back_to_k(store):
    pr = run(store, [("T13", shapes.bent_sheet(1.3, 40, 25, 90, 1.3, 30))])
    p, long, _ = flat_len(pr, "T13")
    assert not p["thickness"]["matched"]
    assert p["flat"]["bends"][0]["method"] == "k"
    assert long == pytest.approx(65 + math.pi / 2 * (1.3 + 0.65), abs=0.01)


def test_flat_plate_with_hole(store):
    pr = run(store, [("Plate", shapes.flat_plate(2.0, 80, 50, hole_d=10))])
    p, long, short = flat_len(pr, "Plate")
    assert long == pytest.approx(80, abs=0.01) and short == pytest.approx(50, abs=0.01)
    assert p["flat"]["holes"] == 1 and p["flat"]["bends"] == []
    assert p["code"] == "ZN#14"


def test_tube_and_bolt_are_excluded(store):
    pr = run(store, [("RoundTube", shapes.tube(30, 2, 100)), ("RectTube", shapes.rect_tube(30, 20, 2, 100)),
                     ("Thing", shapes.bolt())])
    js = pr.to_json()
    for p in js["parts"]:
        assert not p["include"], p["name"]
    assert js["groups"] == []


def test_groups_split_by_thickness_and_material(store):
    pr = run(store, [("A", shapes.flat_plate(0.7, 50, 50)), ("B", shapes.flat_plate(0.7, 60, 40)),
                     ("C", shapes.flat_plate(2.0, 50, 50))])
    pr.update_part("p2", {"material_id": "ST"})
    codes = sorted(g["code"] for g in pr.to_json()["groups"])
    assert codes == ["ST#22", "ZN#14", "ZN#22"]
