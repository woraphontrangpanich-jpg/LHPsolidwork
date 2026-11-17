"""Audit: every number that ends up in the DXF / Excel is re-derived independently and compared.

Checks
- quantities: nested sub-assemblies multiply, DXF contains exactly qty copies of every part
- each DXF: one closed outer contour per piece, CUT area == sum(flat area x qty), pieces do not overlap
- bend lines in DXF == bends x qty
- every bend: BA re-computed by hand from the rule (table for 90 deg small radius, K-factor otherwise)
- mirrored (flipped) parts: same shape, mirrored (arc angle handling)
- sheet-size split: all pieces inside the sheet
- Excel: quantities / sizes / excluded list agree with the project
"""
import io
import math
from pathlib import Path

import ezdxf
import pytest
from openpyxl import load_workbook
from shapely import affinity
from shapely.geometry import LineString, Polygon
from shapely.ops import polygonize, unary_union

import shapes
from sheetflat.core.dxf_export import build_dxf, dxf_bytes
from sheetflat.core.layout import place_geometry
from sheetflat.core.profiles import ProfileStore
from sheetflat.core.project import Project
from sheetflat.core.unfold import prim_points
from sheetflat.core.xlsx_export import build_xlsx

SAMPLE = Path(__file__).resolve().parents[2] / "Assem.STEP"


# ----------------------------------------------------------------------------- helpers
def write_nested_step(path):
    """top = 2 x SubAsm ; SubAsm = 3 x Bracket (L 1.0mm) + 1 x Plate (2mm) ; top also has 1 x Bolt"""
    from OCP.STEPCAFControl import STEPCAFControl_Writer
    from OCP.STEPControl import STEPControl_AsIs
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDataStd import TDataStd_Name
    from OCP.TDocStd import TDocStd_Document
    from OCP.TopLoc import TopLoc_Location
    from OCP.XCAFApp import XCAFApp_Application
    from OCP.XCAFDoc import XCAFDoc_DocumentTool
    from OCP.gp import gp_Trsf, gp_Vec

    app = XCAFApp_Application.GetApplication_s()
    doc = TDocStd_Document(TCollection_ExtendedString("MDTV-XCAF"))
    app.InitDocument(doc)
    st = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())

    def name(lbl, s):
        TDataStd_Name.Set_s(lbl, TCollection_ExtendedString(s))

    def loc(x):
        t = gp_Trsf()
        t.SetTranslation(gp_Vec(x, 0, 0))
        return TopLoc_Location(t)

    bracket = st.AddShape(shapes.bent_sheet(1.0, 40, 25, 90, 1.0, 30), False)
    name(bracket, "Bracket")
    plate = st.AddShape(shapes.flat_plate(2.0, 80, 50, hole_d=10), False)
    name(plate, "Plate")
    bolt = st.AddShape(shapes.bolt(), False)
    name(bolt, "M8 Bolt")
    sub = st.NewShape()
    name(sub, "SubAsm")
    for i in range(3):
        st.AddComponent(sub, bracket, loc(100 * i))
    st.AddComponent(sub, plate, loc(500))
    top = st.NewShape()
    name(top, "Top")
    st.AddComponent(top, sub, loc(0))
    st.AddComponent(top, sub, loc(2000))
    st.AddComponent(top, bolt, loc(5000))
    st.UpdateAssemblies()
    w = STEPCAFControl_Writer()
    w.SetNameMode(True)
    assert w.Transfer(doc, STEPControl_AsIs)
    w.Write(str(path))


def max_endpoint_gap(doc):
    import numpy as np
    ends = []
    for e in dxf_entities(doc, "CUT"):
        t = e.dxftype()
        if t == "LINE":
            ends += [(e.dxf.start.x, e.dxf.start.y), (e.dxf.end.x, e.dxf.end.y)]
        elif t == "ARC":
            ends += [(p.x, p.y) for p in (e.start_point, e.end_point)]
        elif t == "LWPOLYLINE":
            p = list(e.get_points())
            ends += [(p[0][0], p[0][1]), (p[-1][0], p[-1][1])]
    E = np.array(ends)
    worst = 0.0
    for i, p in enumerate(E):
        d = np.linalg.norm(E - p, axis=1)
        d[i] = 1e9
        worst = max(worst, float(d.min()))
    return worst


def dxf_entities(doc, layer):
    return [e for e in doc.modelspace() if e.dxf.layer == layer]


def dxf_geoms(doc, layer="CUT"):
    gs = []
    for e in dxf_entities(doc, layer):
        t = e.dxftype()
        if t == "LINE":
            gs.append(LineString([(e.dxf.start.x, e.dxf.start.y), (e.dxf.end.x, e.dxf.end.y)]))
        elif t in ("ARC", "CIRCLE", "LWPOLYLINE"):
            pts = [(p.x, p.y) for p in e.flattening(0.002)] if t != "LWPOLYLINE" else [(p[0], p[1]) for p in e.get_points()]
            gs.append(LineString(pts))
    return gs


def dxf_regions(doc):
    """Filled regions (outer minus holes) reconstructed purely from the DXF CUT lines."""
    import shapely
    # snap to 0.1 micron so float noise at shared endpoints does not break the noding (real gaps are checked separately)
    faces = list(polygonize(unary_union([shapely.set_precision(g, 1e-4) for g in dxf_geoms(doc)])))
    # depth parity: a face inside an odd number of other faces is a hole
    out = []
    for f in faces:
        rp = f.representative_point()
        depth = sum(1 for g in faces if g is not f and g.area > f.area and Polygon(g.exterior).contains(rp))
        if depth % 2 == 0:
            out.append(Polygon(f.exterior))
    holes = [Polygon(f.exterior) for f in faces if f not in out]
    return out, faces


@pytest.fixture(scope="module")
def nested(tmp_path_factory):
    d = tmp_path_factory.mktemp("nested")
    p = d / "Top.step"
    write_nested_step(p)
    store = ProfileStore(d / "p.json")
    pr = Project(store, "Top", "Top.step")
    pr.load_step(str(p))
    return pr


@pytest.fixture(scope="module")
def sample(tmp_path_factory):
    if not SAMPLE.exists():
        pytest.skip("sample STEP not found")
    store = ProfileStore(tmp_path_factory.mktemp("s") / "p.json")
    pr = Project(store, "Assem", "Assem.STEP")
    pr.load_step(str(SAMPLE))
    return pr


# ----------------------------------------------------------------------------- quantities
def test_nested_quantities(nested):
    q = {p.name: p.qty for p in nested.parts}
    assert q == {"Bracket": 6, "Plate": 2, "M8 Bolt": 1}
    js = nested.to_json()
    assert js["summary"]["pieces"] == 9 and js["summary"]["cut_pieces"] == 8 and js["summary"]["excluded_pieces"] == 1
    g = {x["code"]: x for x in js["groups"]}
    assert g["ZN#19"]["pieces"] == 6 and len(g["ZN#19"]["part_ids"]) == 1
    assert g["ZN#14"]["pieces"] == 2


def _audit_project(pr):
    byid = {p.id: p for p in pr.parts}
    for g in pr.groups:
        expect_pieces = sum(byid[pid].qty * len(byid[pid].pieces) for pid in g.part_ids)
        assert sum(len(s.placed) for s in g.sheets) == expect_pieces, g.code
        for s in g.sheets:
            doc = ezdxf.read(io.StringIO(dxf_bytes(build_dxf(s)).decode("utf-8")))
            assert max_endpoint_gap(doc) < 1e-4, f"{g.code}: contour not joined in DXF"
            outers, faces = dxf_regions(doc)
            assert len(outers) == len(s.placed), f"{g.code}: closed outer contours in DXF"
            # area from DXF lines == flat areas
            region = sum(f.area for f in faces if any(Polygon(f.exterior).equals(o) for o in outers))
            want = sum(pl.item.piece.area for pl in s.placed)
            assert region == pytest.approx(want, rel=2e-3), g.code
            # bend lines
            nb = sum(len(pl.item.piece.bends) for pl in s.placed)
            assert len(dxf_entities(doc, "BEND")) == nb
            # no overlaps, gap respected
            boxes = [(pl.x, pl.y, pl.x + pl.w, pl.y + pl.h) for pl in s.placed]
            for i in range(len(boxes)):
                for j in range(i + 1, len(boxes)):
                    a, b = boxes[i], boxes[j]
                    sep = max(b[0] - a[2], a[0] - b[2], b[1] - a[3], a[1] - b[3])
                    assert sep >= float(pr.settings["gap"]) - 1e-6
            # every piece's geometry lies inside its own box
            for pl in s.placed:
                prims, _ = place_geometry(pl)
                for q in prims:
                    for x, y in prim_points(q):
                        assert pl.x - 1e-3 <= x <= pl.x + pl.w + 1e-3 and pl.y - 1e-3 <= y <= pl.y + pl.h + 1e-3


def test_sample_dxf_contents(sample):
    _audit_project(sample)


def test_nested_dxf_contents(nested):
    _audit_project(nested)


# ----------------------------------------------------------------------------- bend allowances by hand
def test_every_bend_follows_the_rule(sample, nested):
    for pr in (sample, nested):
        prof = pr.profile
        for rec in pr.parts:
            if not rec.pieces:
                continue
            t = rec.thickness_used(prof)
            row = next((r for r in prof["table"] if abs(r["thickness"] - t) < 1e-9), None)
            for pc in rec.pieces:
                for b in pc.bends:
                    th = math.radians(b.angle_deg)
                    if b.method == "table":
                        assert row is not None
                        assert abs(b.angle_deg - 90) <= prof["angle_tol"]
                        assert b.r_inner <= prof["table_max_ratio"] * t + 1e-9
                        assert b.bd == row["bd"]
                        assert b.ba == pytest.approx(2 * (b.r_inner + t) - row["bd"], abs=1e-9)
                    else:
                        assert b.ba == pytest.approx(th * (b.r_inner + prof["k_factor"] * t), abs=1e-9)
                        is_right = abs(b.angle_deg - 90) <= prof["angle_tol"]
                        # a right-angle bend may only use K when: no table row, big radius, or jog
                        if is_right and row is not None and b.r_inner <= prof["table_max_ratio"] * t:
                            assert prof["jog_use_k"], f"{rec.name}: 90deg bend used K without reason"


def test_sample_bend_inventory(sample):
    """Bend inventory of the sample, checked by eye in SolidWorks (angles, radii, method)."""
    inv = {}
    for rec in sample.parts:
        for b in rec.pieces[0].bends:
            inv.setdefault(rec.name, []).append((round(b.angle_deg), round(b.r_inner, 2), b.method))
    assert sorted(inv["Bottom"]) == [(90, 0.2, "table")] * 6
    assert sorted(inv["BackHook_v2"]) == [(90, 0.2, "table")]
    assert sorted(inv["Hook"]) == [(45, 0.2, "k"), (90, 0.2, "table"), (90, 0.2, "table")]
    assert sorted(inv["Hood2"]) == [(90, 5.0, "k")] * 2 + [(180, 0.05, "k")] * 4
    # Front: jog (2 x 2 opposite 90 deg bends, 0.1 mm apart) -> K like SolidWorks, + one hem
    assert sorted(inv["Front"]) == [(90, 0.2, "k")] * 4 + [(180, 0.05, "k")]


def test_thickness_measurement(sample):
    for rec in sample.parts:
        assert min(abs(rec.measured_t - 0.7), abs(rec.measured_t - 2.0)) < 0.002, rec.name


# ----------------------------------------------------------------------------- mirror
def test_flip_is_exact_mirror(sample):
    rec = next(r for r in sample.parts if r.name == "BackHook_part_v2")   # asymmetric, has arcs
    from sheetflat.core.layout import Item, Placed

    def region(flip):
        pc = rec.pieces[0]
        pl = Placed(Item(rec.id, rec.name, 1, pc, flip), 0, 0, pc.bbox[2] - pc.bbox[0], pc.bbox[3] - pc.bbox[1])
        from sheetflat.core.layout import Sheet
        doc = ezdxf.read(io.StringIO(dxf_bytes(build_dxf(Sheet(0, [pl]))).decode("utf-8")))
        outers, faces = dxf_regions(doc)
        return unary_union([Polygon(f.exterior) for f in faces if f.area > 1]), pl.w
    a, w = region(False)
    b, _ = region(True)
    m = affinity.translate(affinity.scale(a, xfact=-1, origin=(0, 0)), w, 0)
    assert m.symmetric_difference(b).area < 0.05


# ----------------------------------------------------------------------------- sheets
def test_sheet_split_respects_sheet_size(nested):
    nested.update_settings({"sheet": [100, 200], "gap": 5})
    try:
        for g in nested.groups:
            for s in g.sheets:
                for pl in s.placed:
                    if not s.oversize:
                        assert pl.x + pl.w <= 100 + 1e-6 and pl.y + pl.h <= 200 + 1e-6
        assert sum(len(s.placed) for g in nested.groups for s in g.sheets) == 8
    finally:
        nested.update_settings({"sheet": None, "gap": 10.0})


# ----------------------------------------------------------------------------- excel
def test_excel_matches_project(nested):
    wb = load_workbook(io.BytesIO(build_xlsx(nested)))
    js = nested.to_json()
    ws = wb["ชิ้นส่วนที่ตัด"]
    rows = [r for r in ws.iter_rows(min_row=2, values_only=True) if r[0]]
    byname = {p["name"]: p for p in js["parts"]}
    assert {r[1]: r[2] for r in rows} == {"Bracket": 6, "Plate": 2}
    for r in rows:
        f = byname[r[1]]["flat"]
        assert (r[5], r[6]) == (f["w"], f["h"])
    ex = [r for r in wb["ไม่นำไปตัด"].iter_rows(min_row=2, values_only=True) if r[0]]
    assert [(r[1], r[2]) for r in ex] == [("M8 Bolt", 1)]
    summ = [r for r in wb["สรุป"].iter_rows(values_only=True) if r[0] and str(r[0]).endswith(".dxf")]
    assert sorted((r[1], r[4]) for r in summ) == [("ZN#14", 2), ("ZN#19", 6)]
    bends = [r for r in wb["รอยพับ"].iter_rows(min_row=2, values_only=True) if r[0]]
    assert bends and all(r[5].startswith("ตาราง") for r in bends)
