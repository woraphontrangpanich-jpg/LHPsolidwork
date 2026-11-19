"""Take a solid that was fused from several sheet-metal parts apart again (a guess).

A customer who sends the whole assembly as ONE solid has thrown away where one part ends and the next
begins. What is left is sheet metal, and sheet metal can be recognised:

* the sheet thicknesses are the ones the shop has in its bend table (0.7, 1.0, 1.2 ... 3 mm), so every plane
  that has a parallel partner one such thickness behind it is the face of a sheet;
* the faces of one sheet part hang together through smooth bends (tangent), the faces of different parts
  meet at sharp edges or not at all;
* where one part stands on another, the face of the lower one is left with a hole shaped like the footprint
  of the upper one, and that hole is lined with the skin faces of the other part. A real hole is lined
  with its own wall faces. That footprint is where the solid is cut.

``split_fused`` cuts the solid along those footprints (OpenCASCADE general-fuse splitter) and returns the
separate solids, so everything after it (analysis, unfolding, 3D view, STEP export) treats them as ordinary parts.
If nothing convincing is found the solid is returned untouched. The result is a guess: the caller shows it as one.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from OCP.BRepAlgoAPI import BRepAlgoAPI_Splitter
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
from OCP.BRepGProp import BRepGProp
from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
from OCP.GProp import GProp_GProps
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_SOLID, TopAbs_WIRE
from OCP.TopExp import TopExp, TopExp_Explorer
from OCP.TopoDS import TopoDS
from OCP.gp import gp_Vec
import OCP.collections as _c

from .analysis import analyze, estimate_thickness, face_polygon, loop_points
from .brep import SolidData, extract_solid

MIN_SHARE = 0.03          # a thickness must carry this share of all planar area to count as a sheet thickness
MAX_T = 12.7


@dataclass
class SplitResult:
    solids: list                       # TopoDS solids (the input itself when nothing was split)
    split: bool = False
    notes: list = field(default_factory=list)
    thicknesses: list = field(default_factory=list)      # sheet thicknesses found (mm)


def _volume(shape) -> float:
    gp = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, gp)
    return abs(gp.Mass())


def _solids_of(shape) -> list:
    out, ex = [], TopExp_Explorer(shape, TopAbs_SOLID)
    while ex.More():
        out.append(TopoDS.Solid(ex.Current()))
        ex.Next()
    return out


def candidate_thicknesses(sd: SolidData, infos: dict, table_ts: list[float], match_tol: float = 0.06) -> list[float]:
    """Sheet thicknesses present in the solid: clusters of the plane-to-partner distances that carry enough
    area; a cluster is snapped to the shop's table thickness when one is close (that is the "guess from the
    steel we have")."""
    planar = sum(f.area for f in sd.faces if f.kind == "plane") or 1.0
    cand = sorted((d, sd.faces[fid].area) for fid, (d, _) in infos.items() if 0.1 <= d <= MAX_T)
    clusters, i = [], 0
    while i < len(cand):
        d0 = cand[i][0]
        tol = max(0.02, 0.03 * d0)
        j, w, acc = i, 0.0, 0.0
        while j < len(cand) and cand[j][0] - d0 <= tol:
            w += cand[j][1]
            acc += cand[j][0] * cand[j][1]
            j += 1
        clusters.append((acc / w, w))
        i = j
    out = []
    for d, w in sorted(clusters, key=lambda c: -c[1]):
        if w / planar < MIN_SHARE:
            continue
        near = [t for t in table_ts if abs(t - d) <= max(match_tol, 0.03 * d)]
        t = min(near, key=lambda x: abs(x - d)) if near else round(d, 3)
        if all(abs(t - o) > 1e-6 for o in out):
            out.append(t)
    return sorted(out)


def _sheet_pieces(sd: SolidData, infos: dict, ts: list[float]):
    """Every sheet piece of every candidate thickness: [{t, faces:set, others:[set], skin:set}]."""
    pieces = []
    used: set = set()
    for t in ts:
        an = analyze(sd, force_t=t, infos=infos, max_thickness=MAX_T)
        if not an.ok:
            continue
        for pc in an.pieces:
            faces = set(pc["faces"]).union(*[set(o) for o in pc["others"]]) if pc["others"] else set(pc["faces"])
            if faces & used:
                continue
            pieces.append({"t": an.thickness, "faces": faces, "planar_area": sum(sd.faces[i].area for i in faces if sd.faces[i].kind == "plane")})
        used |= {i for p in pieces for i in p["faces"]}
    return pieces


def _loop_areas(sd: SolidData, f):
    """(index of the outer loop, list of (loop index, points)) of a planar face."""
    from .analysis import plane_basis
    u, v = plane_basis(f.normal)
    res = []
    for k, loop in enumerate(f.loops):
        P = loop_points(sd, loop)
        if len(P) < 3:
            res.append((k, P, 0.0))
            continue
        d = P - f.origin
        x, y = d @ u, d @ v
        a = 0.5 * float(np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y))
        res.append((k, P, abs(a)))
    outer = max(res, key=lambda r: r[2])[0] if res else -1
    return outer, res


def split_fused(solid, table_ts: list[float], match_tol: float = 0.06) -> SplitResult:
    """See the module docstring. ``table_ts`` = the thicknesses of the shop's bend table."""
    res = SplitResult(solids=[solid])
    try:
        sd = extract_solid(solid)
    except Exception:    # noqa
        return res
    if len(sd.faces) < 10 or sum(1 for f in sd.faces if f.kind == "plane") < 4:
        return res
    try:
        _, _, infos = estimate_thickness(sd)
        ts = candidate_thicknesses(sd, infos, table_ts, match_tol)
        if not ts:
            return res
        res.thicknesses = ts
        pieces = _sheet_pieces(sd, infos, ts)
    except Exception:    # noqa
        return res
    if len(pieces) < 2:
        return res

    piece_of = {i: k for k, p in enumerate(pieces) for i in p["faces"]}
    fmap = _c.IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher()
    TopExp.MapShapes_s(solid, TopAbs_FACE, fmap)

    tools = _c.List_TopoDS_Shape()
    n_tools, done = 0, set()
    for k, pc in enumerate(pieces):
        for fid in pc["faces"]:
            f = sd.faces[fid]
            if f.kind != "plane" or len(f.loops) < 2:
                continue
            outer, loops = _loop_areas(sd, f)
            wires = []
            ex = TopExp_Explorer(TopoDS.Face(fmap.FindKey(fid + 1)), TopAbs_WIRE)
            while ex.More():
                wires.append(TopoDS.Wire(ex.Current()))
                ex.Next()
            for li, P, area in loops:
                if li == outer or area < 1e-6 or li >= len(wires) or (fid, li) in done:
                    continue
                # is this hole lined with the skin of ANOTHER sheet part?  (a real hole is lined with its own wall)
                total = other = 0.0
                for eid, _fwd in f.loops[li]:
                    e = sd.edges[eid]
                    nb = [i for i in e.face_ids if i != fid]
                    total += e.length
                    if any(piece_of.get(i, k) != k for i in nb):
                        other += e.length
                if total <= 0 or other / total < 0.4:
                    continue
                done.add((fid, li))
                try:
                    foot = BRepBuilderAPI_MakeFace(wires[li], True).Face()
                except Exception:    # noqa
                    continue
                # does the same footprint appear on the opposite skin (a part passing through a slot)?
                partner = infos.get(fid, (0, None))[1]
                tool = foot
                if partner is not None and sd.faces[partner].kind == "plane" and len(sd.faces[partner].loops) >= 2:
                    pf = sd.faces[partner]
                    c1 = P.mean(axis=0)
                    pouter, ploops = _loop_areas(sd, pf)
                    for pli, PP, parea in ploops:
                        if pli == pouter or abs(parea - area) > 0.02 * area + 1e-3:
                            continue
                        d = PP.mean(axis=0) - c1
                        d = d - float(np.dot(d, f.normal)) * f.normal
                        if float(np.linalg.norm(d)) < 0.1:
                            vec = -f.normal * infos[fid][0]
                            # only the walls of the slot (one face per footprint edge): the caps would also cut the
                            # part that passes through into pieces
                            walls = []
                            ex2 = TopExp_Explorer(wires[li], TopAbs_EDGE)
                            while ex2.More():
                                walls.append(BRepPrimAPI_MakePrism(ex2.Current(), gp_Vec(*[float(x) for x in vec])).Shape())
                                ex2.Next()
                            tool = None
                            for w in walls:
                                tools.Append(w)
                                n_tools += 1
                            done.add((partner, pli))
                            break
                if tool is not None:
                    tools.Append(tool)
                    n_tools += 1
    if n_tools == 0:
        res.notes.append("พบแผ่นหลายชิ้นในก้อนเดียว แต่ไม่พบรอยต่อที่ตัดแยกได้")
        return res

    try:
        sp = BRepAlgoAPI_Splitter()
        args = _c.List_TopoDS_Shape()
        args.Append(solid)
        sp.SetArguments(args)
        sp.SetTools(tools)
        sp.Build()
        if not sp.IsDone():
            return res
        out = _solids_of(sp.Shape())
    except Exception:    # noqa
        return res
    v0 = _volume(solid)
    v1 = sum(_volume(s) for s in out)
    if len(out) < 2 or v0 <= 0 or abs(v1 - v0) / v0 > 0.005:
        res.notes.append("ตัดแยกแล้วปริมาตรไม่ตรงกับก้อนเดิม จึงไม่แยก")
        return res
    # biggest first, deterministic order (so a saved job finds the same parts again)
    out.sort(key=lambda s: (-round(_volume(s), 1)))
    res.solids, res.split = out, True
    res.notes.append(f"แยกจากก้อนเดียวเป็น {len(out)} ชิ้น (เดาจากความหนาที่มีในตาราง {', '.join(f'{t:g}' for t in ts)} มม.)")
    return res


def same_part(a, b, tol: float = 2e-4) -> bool:
    """Two solids that are copies of each other (same volume and surface area, whatever their position)."""
    ga, gb = GProp_GProps(), GProp_GProps()
    BRepGProp.VolumeProperties_s(a, ga)
    BRepGProp.VolumeProperties_s(b, gb)
    va, vb = abs(ga.Mass()), abs(gb.Mass())
    if abs(va - vb) > tol * max(va, vb, 1e-9):
        return False
    sa, sb = GProp_GProps(), GProp_GProps()
    BRepGProp.SurfaceProperties_s(a, sa)
    BRepGProp.SurfaceProperties_s(b, sb)
    return abs(sa.Mass() - sb.Mass()) <= tol * max(sa.Mass(), sb.Mass(), 1e-9)


def merge_copies(solids: list) -> list[tuple]:
    """[(solid, count)] with identical copies merged."""
    out: list = []
    for s in solids:
        for k, (o, n) in enumerate(out):
            if same_part(o, s):
                out[k] = (o, n + 1)
                break
        else:
            out.append((s, 1))
    return out
