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
