"""Build synthetic test solids with OpenCASCADE and write them as STEP files."""
import math

from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
from OCP.BRepBuilderAPI import (BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakeWire,
                                BRepBuilderAPI_Transform)
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder, BRepPrimAPI_MakePrism
from OCP.GC import GC_MakeArcOfCircle
from OCP.IFSelect import IFSelect_RetDone
from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec


def _P(x, y):
    return gp_Pnt(x, y, 0.0)


def bent_sheet(t, L1, L2, angle_deg, r_in, width):
    """One bend. L1/L2 = straight flange lengths measured from the tangent lines. Profile in XY, extruded along Z."""
    a = math.radians(angle_deg)
    rho = r_in + t / 2.0
    ro, ri = r_in + t, r_in
    cx, cy = 0.0, rho
    d2 = (math.cos(a), math.sin(a))

    def pt_on(r, ang):
        return (cx + r * math.sin(ang), cy - r * math.cos(ang))

    o0, o1 = (-L1, -t / 2), pt_on(ro, 0)
    i0, i1 = (-L1, +t / 2), pt_on(ri, 0)
    om, im = pt_on(ro, a / 2), pt_on(ri, a / 2)
    oe, ie = pt_on(ro, a), pt_on(ri, a)
    oe2 = (oe[0] + L2 * d2[0], oe[1] + L2 * d2[1])
    ie2 = (ie[0] + L2 * d2[0], ie[1] + L2 * d2[1])
    segs = []

    def line(p, q):
        segs.append(BRepBuilderAPI_MakeEdge(_P(*p), _P(*q)).Edge())

    def arc(p, m, q):
        segs.append(BRepBuilderAPI_MakeEdge(GC_MakeArcOfCircle(_P(*p), _P(*m), _P(*q)).Value()).Edge())

    line(o0, o1); arc(o1, om, oe); line(oe, oe2); line(oe2, ie2); line(ie2, ie); arc(ie, im, i1); line(i1, i0); line(i0, o0)
    w = BRepBuilderAPI_MakeWire()
    for e in segs:
        w.Add(e)
    face = BRepBuilderAPI_MakeFace(w.Wire()).Face()
    return BRepPrimAPI_MakePrism(face, gp_Vec(0, 0, width)).Shape()


def bent_profile(t, r_in, lengths, turns, width):
    """Several bends: a centre line of straight lengths with turns between them (degrees, + = left),
    every bend with inner radius r_in. Profile in XY, extruded along Z (e.g. a U or hat channel)."""
    rho = r_in + t / 2.0
    x, y, h = 0.0, 0.0, 0.0
    left, right = [], []

    def off(px, py, hh, s):
        return (px - s * math.sin(hh), py + s * math.cos(hh))

    def rot(p, c, ang):
        dx, dy = p[0] - c[0], p[1] - c[1]
        return (c[0] + dx * math.cos(ang) - dy * math.sin(ang), c[1] + dx * math.sin(ang) + dy * math.cos(ang))

    for i, L in enumerate(lengths):
        nx, ny = x + L * math.cos(h), y + L * math.sin(h)
        left.append(("line", off(x, y, h, t / 2), off(nx, ny, h, t / 2)))
        right.append(("line", off(x, y, h, -t / 2), off(nx, ny, h, -t / 2)))
        x, y = nx, ny
        if i < len(turns):
            a = math.radians(turns[i])
            c = off(x, y, h, rho if a > 0 else -rho)
            for side, lst in ((t / 2, left), (-t / 2, right)):
                p0 = off(x, y, h, side)
                lst.append(("arc", p0, rot(p0, c, a / 2), rot(p0, c, a)))
            x, y = rot((x, y), c, a)
            h += a
    segs = []

    def add(e):
        if e[0] == "line":
            segs.append(BRepBuilderAPI_MakeEdge(_P(*e[1]), _P(*e[2])).Edge())
        else:
            segs.append(BRepBuilderAPI_MakeEdge(GC_MakeArcOfCircle(_P(*e[1]), _P(*e[2]), _P(*e[3])).Value()).Edge())

    for e in left:
        add(e)
    add(("line", left[-1][-1], right[-1][-1]))
    for e in reversed(right):
        add(("line", e[2], e[1]) if e[0] == "line" else ("arc", e[3], e[2], e[1]))
    add(("line", right[0][1], left[0][1]))
    w = BRepBuilderAPI_MakeWire()
    for e in segs:
        w.Add(e)
    face = BRepBuilderAPI_MakeFace(w.Wire()).Face()
    return BRepPrimAPI_MakePrism(face, gp_Vec(0, 0, width)).Shape()


def flat_plate(t, w, h, hole_d=None):
    s = BRepPrimAPI_MakeBox(w, h, t).Shape()
    if hole_d:
        cyl = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(w / 2, h / 2, -1), gp_Dir(0, 0, 1)), hole_d / 2, t + 2).Shape()
        s = BRepAlgoAPI_Cut(s, cyl).Shape()
    return s


def tube(od, wall, length):
    outer = BRepPrimAPI_MakeCylinder(od / 2.0, length).Shape()
    inner = BRepPrimAPI_MakeCylinder(od / 2.0 - wall, length).Shape()
    return BRepAlgoAPI_Cut(outer, inner).Shape()


def rect_tube(w, h, wall, length):
    outer = BRepPrimAPI_MakeBox(w, h, length).Shape()
    inner = BRepPrimAPI_MakeBox(w - 2 * wall, h - 2 * wall, length).Shape()
    tr = gp_Trsf()
    tr.SetTranslation(gp_Vec(wall, wall, 0))
    inner = BRepBuilderAPI_Transform(inner, tr, True).Shape()
    return BRepAlgoAPI_Cut(outer, inner).Shape()


def bolt():
    shank = BRepPrimAPI_MakeCylinder(4.0, 30).Shape()
    head = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(0, 0, 30), gp_Dir(0, 0, 1)), 7.0, 5).Shape()
    return BRepAlgoAPI_Fuse(shank, head).Shape()


def write_step(shape, path):
    w = STEPControl_Writer()
    w.Transfer(shape, STEPControl_AsIs)
    assert w.Write(str(path)) == IFSelect_RetDone


def sharp_profile(points, length):
    """A profile with sharp corners (no bend radius, like an L/U drawn as an extrusion), extruded along Z."""
    w = BRepBuilderAPI_MakeWire()
    for i in range(len(points)):
        p, q = points[i], points[(i + 1) % len(points)]
        w.Add(BRepBuilderAPI_MakeEdge(_P(*p), _P(*q)).Edge())
    face = BRepBuilderAPI_MakeFace(w.Wire()).Face()
    return BRepPrimAPI_MakePrism(face, gp_Vec(0, 0, length)).Shape()


def z_outer_round(t, a, h, b, length):
    """A Z (outside sizes: bottom flange a, web h, top flange b + t) whose two 90 degree bends are sharp inside and
    rounded R = t outside, around the inside edge (inner radius 0 drawn as a bend). The bends turn opposite ways,
    so each side of the sheet is broken at one corner. Profile in XY, extruded along Z."""
    s = math.sqrt(0.5)
    w = BRepBuilderAPI_MakeWire()

    def line(p, q):
        w.Add(BRepBuilderAPI_MakeEdge(_P(*p), _P(*q)).Edge())

    def arc(p, m, q):
        w.Add(BRepBuilderAPI_MakeEdge(GC_MakeArcOfCircle(_P(*p), _P(*m), _P(*q)).Value()).Edge())

    line((0, 0), (a - t, 0))
    arc((a - t, 0), (a - t + t * s, t - t * s), (a, t))              # outside of bend 1, around (a - t, t)
    line((a, t), (a, h - t))                                         # inside corner of bend 2 at (a, h - t)
    line((a, h - t), (a + b, h - t))
    line((a + b, h - t), (a + b, h))
    line((a + b, h), (a, h))
    arc((a, h), (a - t * s, h - t + t * s), (a - t, h - t))          # outside of bend 2, around (a, h - t)
    line((a - t, h - t), (a - t, t))                                 # inside corner of bend 1 at (a - t, t)
    line((a - t, t), (0, t))
    line((0, t), (0, 0))
    face = BRepBuilderAPI_MakeFace(w.Wire()).Face()
    return BRepPrimAPI_MakePrism(face, gp_Vec(0, 0, length)).Shape()
