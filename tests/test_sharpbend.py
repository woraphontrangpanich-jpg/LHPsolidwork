"""A part drawn with sharp corners (no bend radius) is one bent part: each corner is a bend with inner
radius 0, so a 90 degree corner uses the bend table (flat = outside a + b - BD)."""
import pytest

import shapes
from sheetflat.core.profiles import ProfileStore
from sheetflat.core.project import Project


def _part(tmp_path, shape, name):
    step = tmp_path / f"{name}.step"
    shapes.write_step(shape, step)
    pr = Project(ProfileStore(tmp_path / "p.json"), name, step.name)
    pr.load_step(str(step))
    assert len(pr.parts) == 1
    return pr.to_json()["parts"][0]


@pytest.mark.parametrize("t,a,b,bd", [(3.0, 50, 50, 4.8), (1.2, 40, 25, 2.0)])
def test_sharp_angle_is_one_part_with_a_table_bend(tmp_path, t, a, b, bd):
    L = shapes.sharp_profile([(0, 0), (a, 0), (a, t), (t, t), (t, b), (0, b)], 300)
    p = _part(tmp_path, L, "angle")
    f = p["flat"]
    assert p["include"] and len(f["bends"]) == 1
    assert f["bends"][0]["method"] == "table" and f["bends"][0]["bd"] == bd
    assert sorted([round(f["w"], 2), round(f["h"], 2)]) == sorted([round(a + b - bd, 2), 300.0])
    assert any("มุมพับคม" in w for w in p["warnings"])
    assert not any("แยกกัน" in w for w in p["warnings"])


def test_sharp_channel_has_two_table_bends(tmp_path):
    t, w, h = 1.2, 60.0, 30.0
    U = shapes.sharp_profile([(0, 0), (w, 0), (w, h), (w - t, h), (w - t, t), (t, t), (t, h), (0, h)], 200)
    f = _part(tmp_path, U, "channel")["flat"]
    assert len(f["bends"]) == 2 and all(b["method"] == "table" for b in f["bends"])
    assert sorted([round(f["w"], 2), round(f["h"], 2)]) == sorted([round(h + w + h - 2 * 2.0, 2), 200.0])


def test_plain_flat_plate_is_untouched(tmp_path):
    p = _part(tmp_path, shapes.flat_plate(2.0, 100, 50), "plate")
    assert not any("มุมพับคม" in w for w in p["warnings"])


def test_z_sharp_inside_round_outside_is_one_flat(tmp_path):
    """Bends sharp inside, rounded R = t outside (around the inside edge), turning opposite ways (a Z): no side of
    the sheet runs through, but it is still one bent part with two R 0 bends."""
    t, a, h, b, L = 2.0, 40.0, 30.0, 35.0, 150.0
    p = _part(tmp_path, shapes.z_outer_round(t, a, h, b, L), "z")
    f = p["flat"]
    assert p["include"], p["reason"]
    assert len(f["bends"]) == 2 and all(x["method"] == "table" and x["bd"] == 3.2 for x in f["bends"])
    # outside sizes a, h, b + t, minus two BD
    assert sorted([round(f["w"], 2), round(f["h"], 2)]) == sorted([round(a + h + b + t - 2 * 3.2, 2), L])
    assert not any("คลี่ได้ไม่ครบ" in w for w in p["warnings"])


def test_part_unfolded_only_in_part_says_so(tmp_path, monkeypatch):
    """When the corners cannot be joined, the part is reported as unfolded only in part (it is sheet metal),
    not as a screw or casting, and forcing it in keeps a warning."""
    import sheetflat.core.project as project
    monkeypatch.setattr(project, "sharp_corners_to_bends", lambda *a: None)
    step = tmp_path / "z.step"
    shapes.write_step(shapes.z_outer_round(2.0, 40.0, 30.0, 35.0, 150.0), step)
    pr = Project(ProfileStore(tmp_path / "p.json"), "z", step.name)
    pr.load_step(str(step))
    p = pr.to_json()["parts"][0]
    assert not p["include"] and p["reason_code"] == "unfold_incomplete"
    assert "น็อต" not in p["reason"]
    pr.update_part(p["id"], {"include": True})
    p = pr.to_json()["parts"][0]
    assert p["include"] and any("คลี่ได้ไม่ครบ" in w for w in p["warnings"])
    assert p["checks"]["lost_area"] > 0
