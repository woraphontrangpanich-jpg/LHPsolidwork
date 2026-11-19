"""A customer who sends the whole assembly as ONE fused solid: the sheets are found again (a guess from the
thicknesses in the bend table) and handled as separate parts."""
import io
import zipfile

import pytest

import shapes
from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain
from OCP.gp import gp_Pnt
from sheetflat.core.brep import extract_solid
from sheetflat.core.profiles import ProfileStore
from sheetflat.core.project import Project
from sheetflat.core.splitfused import split_fused
from sheetflat.core.step_reader import read_step

TS = [0.7, 0.9, 1.0, 1.2, 1.4, 1.6, 2.0, 3.0]


def box(x, y, z, dx, dy, dz):
    return BRepPrimAPI_MakeBox(gp_Pnt(x, y, z), dx, dy, dz).Shape()


def fuse(*shapes_):
    r = shapes_[0]
    for o in shapes_[1:]:
        f = BRepAlgoAPI_Fuse(r, o)
        f.Build()
        r = f.Shape()
    u = ShapeUpgrade_UnifySameDomain(r, True, True, True)    # what a "merge" export does: one solid, no seams
    u.Build()
    return u.Shape()


@pytest.fixture()
def store(tmp_path):
    return ProfileStore(tmp_path / "p.json")


def load(store, shape, tmp_path, **settings):
    path = tmp_path / "fused.step"
    shapes.write_step(shape, path)
    pr = Project(store, "fused", "fused.step", settings or None)
    pr.load_step(str(path))
    return pr


def flats(pr):
    out = {}
    for p in pr.to_json()["parts"]:
        if p["flat"]:
            out[p["name"]] = (round(max(p["flat"]["w"], p["flat"]["h"]), 2), round(min(p["flat"]["w"], p["flat"]["h"]), 2),
                              p["thickness"]["used"] if "used" in p["thickness"] else p["thickness"].get("measured"), p["qty"])
    return out


def test_t_joint_same_thickness_is_two_parts(store, tmp_path):
    r = split_fused(fuse(box(0, 0, 0, 100, 100, 1), box(40, 10, 1, 1, 80, 40)), TS)
    assert r.split and len(r.solids) == 2
    vols = sorted(round(extract_solid(s).volume) for s in r.solids)
    assert vols == [3200, 10000]


def test_t_joint_different_thickness_goes_to_two_dxf_groups(store, tmp_path):
    pr = load(store, fuse(box(0, 0, 0, 100, 100, 1), box(40, 10, 1, 2, 80, 40)), tmp_path)
    codes = sorted(g.code for g in pr.groups)
    assert codes == ["ZN#14", "ZN#19"]
    f = flats(pr)
    assert sorted((v[0], v[1]) for v in f.values()) == [(80.0, 40.0), (100.0, 100.0)]
    assert all(p["warnings"] for p in pr.to_json()["parts"])          # shown as a guess: "check against the drawing"


def test_identical_copies_are_counted_not_listed_twice(store, tmp_path):
    pr = load(store, fuse(box(0, 0, 0, 100, 100, 1.2), box(20, 10, 1.2, 1.2, 80, 40), box(70, 10, 1.2, 1.2, 80, 40)), tmp_path)
    parts = pr.to_json()["parts"]
    assert len(parts) == 2 and sorted(p["qty"] for p in parts) == [1, 2]


def test_part_passing_through_a_slot(store, tmp_path):
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    base = BRepAlgoAPI_Cut(box(0, 0, 0, 100, 100, 1), box(40, 40, -1, 3, 20, 3)).Shape()
    r = split_fused(fuse(base, box(40, 40, -10, 3, 20, 30)), TS)
    assert r.split and len(r.solids) == 2
    assert sorted(round(extract_solid(s).volume) for s in r.solids) == [1800, 9940]


def test_bent_channel_on_a_plate_keeps_its_flat_pattern(store, tmp_path):
    ch = shapes.bent_profile(1.0, 1.0, [40, 80, 40], [90, 90], 300)
    lo, hi = extract_solid(ch).bbox
    plate = box(hi[0], lo[1] - 30, lo[2] - 30, 1.0, (hi[1] - lo[1]) + 60, 360)
    alone = load(store, ch, tmp_path)
    fusedpr = load(store, fuse(ch, plate), tmp_path)
    a = next(iter(flats(alone).values()))
    names = flats(fusedpr)
    assert len(names) == 2
    assert any(abs(v[0] - a[0]) < 0.02 and abs(v[1] - a[1]) < 0.02 for v in names.values())    # channel unfolds the same


def test_setting_can_turn_it_off(store, tmp_path):
    pr = load(store, fuse(box(0, 0, 0, 100, 100, 1), box(40, 10, 1, 2, 80, 40)), tmp_path, split_fused=False)
    assert len(pr.parts) == 1


def test_one_ordinary_part_is_not_cut(store, tmp_path):
    pr = load(store, shapes.bent_profile(1.0, 1.0, [40, 80, 40], [90, 90], 300), tmp_path)
    assert len(pr.parts) == 1 and not pr.parts[0].split_note


def test_steps_zip_has_one_readable_step_per_part(store, tmp_path):
    pr = load(store, fuse(box(0, 0, 0, 100, 100, 1), box(40, 10, 1, 2, 80, 40)), tmp_path)
    z = zipfile.ZipFile(io.BytesIO(pr.steps_zip()))
    names = [n for n in z.namelist() if n.endswith(".step")]
    assert len(names) == 2 and all(n.startswith("ตัด/") for n in names)
    for n in names:
        p = tmp_path / "back.step"
        p.write_bytes(z.read(n))
        _, defs = read_step(p)
        assert len(defs) == 1 and len(defs[0].solids) == 1


def test_api_upload_splits_and_exports_steps(tmp_path):
    import time

    from fastapi.testclient import TestClient

    from sheetflat.app import app
    path = tmp_path / "fused.step"
    shapes.write_step(fuse(box(0, 0, 0, 100, 100, 1), box(40, 10, 1, 2, 80, 40)), path)
    c = TestClient(app)
    jid = c.post("/api/upload", files={"file": ("fused.step", path.read_bytes())}, data={"material": "ZN", "split_fused": "1"}).json()["id"]
    for _ in range(100):
        j = c.get(f"/api/jobs/{jid}").json()
        if j["state"] in ("done", "error"):
            break
        time.sleep(0.2)
    assert j["state"] == "done" and len(j["project"]["parts"]) == 2
    z = zipfile.ZipFile(io.BytesIO(c.get(f"/api/jobs/{jid}/export/steps").content))
    assert len([n for n in z.namelist() if n.endswith(".step")]) == 2
    jid = c.post("/api/upload", files={"file": ("fused.step", path.read_bytes())}, data={"material": "ZN", "split_fused": "0"}).json()["id"]
    for _ in range(100):
        j = c.get(f"/api/jobs/{jid}").json()
        if j["state"] in ("done", "error"):
            break
        time.sleep(0.2)
    assert len(j["project"]["parts"]) == 1
