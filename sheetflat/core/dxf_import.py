"""Open a DXF file: every closed outline is a flat part (holes = closed outlines inside it).

For flat patterns that already exist (drawn in SolidWorks, sent by the customer, or exported by this program):
view them, measure, group by thickness and steel, price them, lay them out again, export a DXF.
A DXF has no thickness, so the caller supplies it.

* Entities become the same primitives the unfolder produces (line / arc / circle / poly), so everything after
  the import (preview, measuring, layout, DXF export) is shared with unfolded parts.
* Closed regions are found by noding all lines and polygonising them; outlines inside an outline's area (odd
  depth) are holes, outlines inside a hole are parts of their own.
* Identical outlines (same area, size, holes and cut length, in any orientation) are counted as one part with a
  quantity, so a nested sheet of 10 equal brackets opens as one part x 10.
* Ignored: dimension / bend / centre / hidden / text layers, text itself (the first label inside an outline names
  the part), open lines that close no outline (reported).
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

import ezdxf
import numpy as np
import shapely
from shapely import STRtree
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import polygonize, unary_union

from .unfold import FlatPiece, prim_points

SNAP = 1e-3
SKIP_LAYER = re.compile(r"bend|dim|label|text|note|center|centre|hidden|phantom|defpoints|hatch|พับ|ขนาด", re.I)
SKIP_TEXT = re.compile(r"^\s*=?\s*\d+\s*:\s*\d+\s*$")        # drawing scale notes such as "=1:4"
UNITS = {0: 1.0, 1: 25.4, 2: 304.8, 4: 1.0, 5: 10.0, 6: 1000.0}


class DxfReadError(Exception):
    pass


@dataclass
class DxfPart:
    piece: FlatPiece
    name: str = ""
    count: int = 1


@dataclass
class DxfImport:
    parts: list = field(default_factory=list)          # DxfPart, identical outlines merged
    outlines: int = 0                                  # closed outlines found (before merging)
    open_lines: int = 0                                # lines that close no outline
    skipped: int = 0                                   # entities on ignored layers
    notes: list = field(default_factory=list)


def _explode(e, depth=0):
    t = e.dxftype()
    if t in ("INSERT",) and depth < 6:
        for v in e.virtual_entities():
            yield from _explode(v, depth + 1)
    elif t in ("LWPOLYLINE", "POLYLINE"):
        yield from e.virtual_entities()
    else:
        yield e


def _prims(path, scale_override=None):
    try:
        doc = ezdxf.readfile(str(path))
    except (IOError, ezdxf.DXFStructureError) as ex:
        raise DxfReadError(f"อ่านไฟล์ DXF ไม่ได้: {ex}") from None
    except Exception as ex:   # noqa
        raise DxfReadError(f"อ่านไฟล์ DXF ไม่ได้ ({type(ex).__name__}: {ex})") from None
    k = scale_override or UNITS.get(int(doc.header.get("$INSUNITS", 4) or 0), 1.0)
    prims, texts, skipped = [], [], 0
    for top in doc.modelspace():
        for e in _explode(top):
            if SKIP_LAYER.search(e.dxf.layer or ""):
                skipped += 1
                continue
            t = e.dxftype()
            try:
                if t == "LINE":
                    a, b = e.dxf.start, e.dxf.end
                    if (a.x - b.x) ** 2 + (a.y - b.y) ** 2 > 1e-12:
                        prims.append({"t": "line", "a": (a.x * k, a.y * k), "b": (b.x * k, b.y * k)})
                elif t == "CIRCLE":
                    c, flip = e.dxf.center, e.dxf.extrusion.z < 0
                    prims.append({"t": "circle", "c": ((-c.x if flip else c.x) * k, c.y * k), "r": e.dxf.radius * k})
                elif t == "ARC":
                    c, flip = e.dxf.center, e.dxf.extrusion.z < 0
                    a0, a1 = e.dxf.start_angle % 360.0, e.dxf.end_angle % 360.0
                    if flip:
                        a0, a1 = (180.0 - a1) % 360.0, (180.0 - a0) % 360.0
                    prims.append({"t": "arc", "c": ((-c.x if flip else c.x) * k, c.y * k), "r": e.dxf.radius * k, "a0": a0, "a1": a1})
                elif t in ("ELLIPSE", "SPLINE"):
                    pts = [(p.x * k, p.y * k) for p in e.flattening(0.01 / k)]
                    if len(pts) >= 2:
                        prims.append({"t": "poly", "pts": pts})
                elif t in ("TEXT", "MTEXT"):
                    txt = e.dxf.text if t == "TEXT" else e.plain_text()
                    ins = e.dxf.insert
                    if txt and not SKIP_TEXT.match(txt):
                        texts.append((ins.x * k, ins.y * k, txt.strip().splitlines()[0][:60]))
            except Exception:   # noqa
                skipped += 1
    return prims, texts, skipped


def _regions(lines):
    """[(shell polygon, [hole polygons])] of the closed outlines made by the lines."""
    noded = unary_union([shapely.set_precision(g, SNAP) for g in lines])
    faces = list(polygonize(noded))
    shells = [Polygon(f.exterior) for f in faces]
    if not shells:
        return []
    tree = STRtree(shells)
    depth = []
    for i, s in enumerate(shells):
        rp = s.representative_point()
        hit = tree.query(rp, predicate="within")
        depth.append(sum(1 for j in hit if j != i and shells[j].area > s.area))
    out = []
    holes = [s for s, d in zip(shells, depth) if d % 2 == 1]
    htree = STRtree(holes) if holes else None
    for s, d in zip(shells, depth):
        if d % 2:
            continue
        inner = []
        if htree is not None:
            for j in htree.query(s):
                h = holes[j]
                if s.contains(h.representative_point()) and h.area < s.area:
                    inner.append(h)
        out.append((s, inner))
    return out


def _signature(piece: FlatPiece):
    xmin, ymin, xmax, ymax = piece.bbox
    dims = sorted((round(xmax - xmin, 2), round(ymax - ymin, 2)))
    holes = sum(1 for lp in piece.loops if lp["role"] == "hole")
    cut = sum(float(np.sum(np.linalg.norm(np.diff(prim_points(q), axis=0), axis=1))) for q in piece.prims)
    return dims, holes, piece.area, cut


def _same(a, b) -> bool:
    (da, ha, aa, ca), (db, hb, ab, cb) = a, b
    return (ha == hb and abs(da[0] - db[0]) <= 0.05 and abs(da[1] - db[1]) <= 0.05
            and abs(aa - ab) <= 3e-4 * max(aa, ab, 1e-9) + 0.01 and abs(ca - cb) <= 1e-3 * max(ca, cb, 1e-9) + 0.05)


def read_dxf(path, stem: str = "", merge: bool = True) -> DxfImport:
    prims, texts, skipped = _prims(path)
    res = DxfImport(skipped=skipped)
    if not prims:
        raise DxfReadError("ไม่พบเส้นในไฟล์ DXF (หรือเส้นอยู่ในเลเยอร์ที่ข้ามไป เช่น BEND / DIM / TEXT)")
    lines = [LineString(prim_points(q)) for q in prims]
    regs = _regions(lines)
    if not regs:
        raise DxfReadError("ไม่พบเส้นขอบปิดในไฟล์ DXF (เส้นต้องต่อกันเป็นรูปปิดจึงจะนับเป็นชิ้นงาน)")
    # which primitives belong to which region: the region whose boundary passes through its midpoint
    bounds, owner = [], []
    for ri, (s, inner) in enumerate(regs):
        for ring in [s.exterior] + [h.exterior for h in inner]:
            bounds.append(ring)
            owner.append(ri)
    tree = STRtree(bounds)
    assigned = [[] for _ in regs]
    used = 0
    for q, ls in zip(prims, lines):
        mid = ls.interpolate(0.5, normalized=True)
        cand = tree.query(mid.buffer(0.02))
        best = min(((bounds[i].distance(mid), i) for i in cand), default=None)
        if best is not None and best[0] <= 0.02:
            assigned[owner[best[1]]].append(q)
            used += 1
    res.open_lines = len(prims) - used
    res.outlines = len(regs)
    pieces = []
    for (s, inner), pr in zip(regs, assigned):
        loops = [{"pts": np.array(s.exterior.coords)[:-1], "role": "outer"}] + \
                [{"pts": np.array(h.exterior.coords)[:-1], "role": "hole"} for h in inner]
        area = s.area - sum(h.area for h in inner)
        pieces.append(FlatPiece(prims=pr, bends=[], loops=loops, area=area, bbox=tuple(float(v) for v in s.bounds),
                                faces=set(), flat_faces=0))
        res.parts.append(DxfPart(piece=pieces[-1]))
    # names from the text inside an outline
    if texts:
        shells = [s for s, _ in regs]
        stree = STRtree(shells)
        for x, y, txt in texts:
            for i in stree.query(Point(x, y), predicate="within"):
                if not res.parts[i].name:
                    res.parts[i].name = txt
    if not merge:
        return res
    # identical outlines -> one part x n (a named one is never merged into an unnamed one)
    sigs, merged = [], []
    order = sorted(range(len(res.parts)), key=lambda i: (-res.parts[i].piece.area, res.parts[i].piece.bbox[1], res.parts[i].piece.bbox[0]))
    for i in order:
        p = res.parts[i]
        sg = _signature(p.piece)
        for k, g in enumerate(sigs):
            if _same(sg, g) and (not p.name or not merged[k].name or p.name == merged[k].name):
                merged[k].count += 1
                if p.name and not merged[k].name:
                    merged[k].name = p.name
                break
        else:
            sigs.append(sg)
            merged.append(p)
    res.parts = merged
    if res.open_lines:
        res.notes.append(f"มีเส้น {res.open_lines} เส้นที่ไม่ได้ปิดเป็นรูป (ข้ามไป)")
    if skipped:
        res.notes.append(f"ข้ามเส้น {skipped} รายการในเลเยอร์ขนาด/เส้นพับ/ข้อความ")
    return res
