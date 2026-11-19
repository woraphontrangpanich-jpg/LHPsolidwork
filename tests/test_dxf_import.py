"""Opening a DXF: closed outlines become flat parts, identical ones are counted, thickness comes from the user."""
import io
import time
import zipfile

import ezdxf
import pytest

from sheetflat.core.dxf_import import DxfReadError, read_dxf
from sheetflat.core.profiles import ProfileStore
from sheetflat.core.project import Project


def make_dxf(path, copies=3):
    doc = ezdxf.new("R2010")
    doc.units = 4
    doc.layers.add("BEND")
    msp = doc.modelspace()

    def plate(x0, y0, w=100.0, h=60.0, holes=((20, 30), (80, 30)), label=None):
        pts = [(x0, y0), (x0 + w, y0), (x0 + w, y0 + h), (x0, y0 + h)]
        for a, b in zip(pts, pts[1:] + pts[:1]):
            msp.add_line(a, b)
        for hx, hy in holes:
            msp.add_circle((x0 + hx, y0 + hy), 5.0)
        if label:
            msp.add_text(label, dxfattribs={"insert": (x0 + 40, y0 + 10), "height": 4})

    for i in range(copies):                                   # equal plates in a row
        plate(i * 130.0, 0.0, label="BRACKET" if i == 0 else None)
    plate(0.0, 200.0, w=200.0, h=50.0, holes=())              # a different one
    # a rounded one: lines + arcs
    msp.add_line((0, 400), (80, 400)); msp.add_arc((80, 410), 10, 270, 360)
    msp.add_line((90, 410), (90, 450)); msp.add_line((90, 450), (0, 450)); msp.add_line((0, 450), (0, 400))
    msp.add_line((40, 0), (40, 60), dxfattribs={"layer": "BEND"})      # bend line: ignored
    msp.add_line((500, 500), (520, 520))                              # open line: reported
    doc.saveas(path)


def test_outlines_holes_counts_and_names(tmp_path):
    p = tmp_path / "a.dxf"
    make_dxf(p, copies=3)
    imp = read_dxf(p)
    assert imp.outlines == 5 and imp.open_lines == 1 and imp.skipped == 1
    by = {(round(x.piece.bbox[2] - x.piece.bbox[0], 1), round(x.piece.bbox[3] - x.piece.bbox[1], 1)): x for x in imp.parts}
    plate = by[(100.0, 60.0)]
    assert plate.count == 3 and plate.name == "BRACKET"
    assert sum(1 for lp in plate.piece.loops if lp["role"] == "hole") == 2
    import math
    assert plate.piece.area == pytest.approx(100 * 60 - 2 * math.pi * 25, abs=0.05)
    assert by[(200.0, 50.0)].count == 1
    rounded = by[(90.0, 50.0)]
    assert any(q["t"] == "arc" for q in rounded.piece.prims)          # arcs kept as arcs, not chopped into lines
    assert len(imp.parts) == 3


def test_no_outline_is_an_error(tmp_path):
    doc = ezdxf.new()
    doc.modelspace().add_line((0, 0), (10, 10))
    doc.saveas(tmp_path / "x.dxf")
    with pytest.raises(DxfReadError):
        read_dxf(tmp_path / "x.dxf")


def test_project_groups_by_thickness_and_exports_dxf(tmp_path):
    p = tmp_path / "a.dxf"
    make_dxf(p, copies=3)
    st = ProfileStore(tmp_path / "p.json")
    pr = Project(st, "a", "a.dxf", {"dxf_thickness": 1.2})
    pr.load_dxf(str(p))
    assert [g.code for g in pr.groups] == ["ZN#18"]
    part = next(x for x in pr.to_json()["parts"] if x["name"] == "BRACKET")
    assert part["qty"] == 3 and part["thickness"]["used"] == 1.2
    assert part["flat"]["w"] == pytest.approx(100.0, abs=0.01) and part["flat"]["holes"] == 2
    assert part["flat"]["weight_kg"] == pytest.approx((6000 - 157.08) * 1.2 * 7.85e-6, rel=1e-3)
    pr.update_part(part["id"], {"t_override": 2.0})              # a different thickness moves it to its own group
    assert sorted(g.code for g in pr.groups) == ["ZN#14", "ZN#18"]


def test_api_opens_a_dxf(tmp_path):
    from fastapi.testclient import TestClient

    from sheetflat.app import app
    p = tmp_path / "a.dxf"
    make_dxf(p, copies=2)
    c = TestClient(app)
    jid = c.post("/api/upload", files={"file": ("a.dxf", p.read_bytes())},
                 data={"material": "ZN", "dxf_thickness": "1.6"}).json()["id"]
    for _ in range(100):
        j = c.get(f"/api/jobs/{jid}").json()
        if j["state"] in ("done", "error"):
            break
        time.sleep(0.2)
    assert j["state"] == "done", j
    assert {g["code"] for g in j["project"]["groups"]} == {"ZN#16"}
    z = zipfile.ZipFile(io.BytesIO(c.get(f"/api/jobs/{jid}/export/all").content))
    assert any(n.endswith(".dxf") for n in z.namelist())
