"""Extract a plain-numpy description of a B-rep solid (faces / edges / loops).

Everything downstream (thickness detection, skin classification, unfolding)
works on these dataclasses only, so the OpenCASCADE specific API surface is
confined to this one module.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCP.BRepGProp import BRepGProp
from OCP.BRepTools import BRepTools, BRepTools_WireExplorer
from OCP.GCPnts import GCPnts_QuasiUniformDeflection
from OCP.GProp import GProp_GProps
from OCP.GeomAbs import (GeomAbs_Circle, GeomAbs_Cone, GeomAbs_Cylinder, GeomAbs_Line, GeomAbs_Plane,
                         GeomAbs_Sphere, GeomAbs_Torus, GeomAbs_Ellipse)
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_REVERSED, TopAbs_WIRE
from OCP.TopExp import TopExp, TopExp_Explorer
from OCP.TopoDS import TopoDS
import OCP.collections as _c

SAMPLE_DEFLECTION = 0.004  # mm, chord error used when polylining curved edges


@dataclass
class Edge:
    id: int
    kind: str                       # line | circle | ellipse | spline
    p0: np.ndarray                  # start point (natural orientation)
    p1: np.ndarray
    pts: np.ndarray                 # (n,3) sampled polyline, natural orientation
    length: float
    closed: bool
    # circle data (kind == 'circle')
    center: np.ndarray | None = None
    axis: np.ndarray | None = None
    radius: float = 0.0
    face_ids: list[int] = field(default_factory=list)


@dataclass
class Face:
    id: int
    kind: str                       # plane | cylinder | cone | torus | sphere | other
    area: float
    reversed: bool
    # planes
    origin: np.ndarray | None = None
    normal: np.ndarray | None = None        # OUTWARD normal (face orientation applied)
    # cylinders
    axis_pt: np.ndarray | None = None
    axis_dir: np.ndarray | None = None
    radius: float = 0.0
    xdir: np.ndarray | None = None
    ydir: np.ndarray | None = None
    out_sign: int = 1                       # +1: outward normal points away from the axis
    u0: float = 0.0
    u1: float = 0.0
    v0: float = 0.0
    v1: float = 0.0
    # topology
    loops: list[list[tuple[int, bool]]] = field(default_factory=list)  # (edge id, forward?)
    edge_ids: list[int] = field(default_factory=list)

    # cached helpers
    _poly: object = None


@dataclass
class SolidData:
    faces: list[Face]
    edges: list[Edge]
    volume: float
    area: float
    bbox: tuple[np.ndarray, np.ndarray]

    def edge_faces(self, e: Edge) -> list[Face]:
        return [self.faces[i] for i in e.face_ids]


def _v(p) -> np.ndarray:
    return np.array([p.X(), p.Y(), p.Z()], dtype=float)


def _d(d) -> np.ndarray:
    return np.array([d.X(), d.Y(), d.Z()], dtype=float)


def _sample_edge(edge) -> Edge | None:
    ad = BRepAdaptor_Curve(edge)
    t = ad.GetType()
    u0, u1 = ad.FirstParameter(), ad.LastParameter()
    p0, p1 = _v(ad.Value(u0)), _v(ad.Value(u1))
    kind = "spline"
    center = axis = None
    radius = 0.0
    if t == GeomAbs_Line:
        kind = "line"
        pts = np.array([p0, p1])
    else:
        if t == GeomAbs_Circle:
            kind = "circle"
            c = ad.Circle()
            center, axis, radius = _v(c.Location()), _d(c.Axis().Direction()), c.Radius()
        elif t == GeomAbs_Ellipse:
            kind = "ellipse"
        q = GCPnts_QuasiUniformDeflection(ad, SAMPLE_DEFLECTION)
        if q.IsDone() and q.NbPoints() >= 2:
            pts = np.array([_v(q.Value(i)) for i in range(1, q.NbPoints() + 1)])
        else:
            n = 32
            pts = np.array([_v(ad.Value(u0 + (u1 - u0) * i / n)) for i in range(n + 1)])
        pts[0], pts[-1] = p0, p1
    length = float(np.sum(np.linalg.norm(np.diff(pts, axis=0), axis=1)))
    return Edge(id=-1, kind=kind, p0=p0, p1=p1, pts=pts, length=length,
                closed=bool(np.linalg.norm(p0 - p1) < 1e-7), center=center, axis=axis, radius=radius)


def extract_solid(solid) -> SolidData:
    """Convert a TopoDS shape (solid / compound) to :class:`SolidData`."""
    fmap = _c.IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher()
    emap = _c.IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher()
    TopExp.MapShapes_s(solid, TopAbs_FACE, fmap)
    TopExp.MapShapes_s(solid, TopAbs_EDGE, emap)
    e2f = _c.IndexedDataMap_TopoDS_Shape_List_TopoDS_Shape_TopTools_ShapeMapHasher()
    TopExp.MapShapesAndAncestors_s(solid, TopAbs_EDGE, TopAbs_FACE, e2f)

    edges: list[Edge | None] = []
    for i in range(1, emap.Extent() + 1):
        e = _sample_edge(TopoDS.Edge(emap.FindKey(i)))
        if e is not None:
            e.id = i - 1
        edges.append(e)
        if e is not None:
            lst = e2f.FindFromIndex(i)
            ids = []
            it = lst.begin() if hasattr(lst, "begin") else None
            # iterate the list of ancestors faces
            for f in _iter_list(lst):
                fi = fmap.FindIndex(f) - 1
                if fi not in ids:
                    ids.append(fi)
            e.face_ids = ids

    faces: list[Face] = []
    for i in range(1, fmap.Extent() + 1):
        f = TopoDS.Face(fmap.FindKey(i))
        faces.append(_extract_face(f, i - 1, emap))

    gp = GProp_GProps()
    BRepGProp.VolumeProperties_s(solid, gp)
    vol = gp.Mass()
    gp2 = GProp_GProps()
    BRepGProp.SurfaceProperties_s(solid, gp2)
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    bb = Bnd_Box()
    BRepBndLib.Add_s(solid, bb, False)
    return SolidData(faces=faces, edges=edges, volume=abs(vol), area=gp2.Mass(),
                     bbox=(_v(bb.CornerMin()), _v(bb.CornerMax())))


def _iter_list(lst):
    """Iterate an OCP NCollection list of shapes (API differs between builds)."""
    try:
        return list(lst)
    except TypeError:
        pass
    out = []
    n = lst.Extent()
    # destructive-free walk using First/RemoveFirst on a copy
    cp = type(lst)()
    cp.Assign(lst)
    for _ in range(n):
        out.append(cp.First())
        cp.RemoveFirst()
    return out


def _extract_face(face, fid: int, emap) -> Face:
    ad = BRepAdaptor_Surface(face)
    st = ad.GetType()
    reversed_ = face.Orientation() == TopAbs_REVERSED
    gp = GProp_GProps()
    BRepGProp.SurfaceProperties_s(face, gp)
    area = gp.Mass()
    umin, umax, vmin, vmax = BRepTools.UVBounds_s(face)
    kind = {GeomAbs_Plane: "plane", GeomAbs_Cylinder: "cylinder", GeomAbs_Cone: "cone",
            GeomAbs_Torus: "torus", GeomAbs_Sphere: "sphere"}.get(st, "other")
    f = Face(id=fid, kind=kind, area=area, reversed=reversed_, u0=umin, u1=umax, v0=vmin, v1=vmax)

    if kind in ("plane", "cylinder", "cone", "torus", "sphere", "other"):
        # surface normal from parametrisation (robust to left handed frames)
        um, vm = 0.5 * (umin + umax), 0.5 * (vmin + vmax)
        if kind == "other" or kind in ("cone", "torus", "sphere"):
            pass
    if kind == "plane":
        pl = ad.Plane()
        pos = pl.Position()
        n = _d(pos.XDirection()).copy()
        n = np.cross(_d(pos.XDirection()), _d(pos.YDirection()))
        n /= np.linalg.norm(n)
        if reversed_:
            n = -n
        f.normal = n
        f.origin = _v(pl.Location())
    elif kind == "cylinder":
        cy = ad.Cylinder()
        pos = cy.Position()
        f.axis_pt = _v(cy.Location())
        f.axis_dir = _d(pos.Direction())
        f.xdir = _d(pos.XDirection())
        f.ydir = _d(pos.YDirection())
        f.radius = cy.Radius()
        direct = float(np.dot(np.cross(f.xdir, f.ydir), f.axis_dir)) > 0
        sgn = 1 if direct else -1           # surface normal (dU x dV) relative to radial
        if reversed_:
            sgn = -sgn
        f.out_sign = sgn
    # loops
    wires = []
    ex = TopExp_Explorer(face, TopAbs_WIRE)
    while ex.More():
        wires.append(TopoDS.Wire(ex.Current()))
        ex.Next()
    for w in wires:
        loop = []
        we = BRepTools_WireExplorer(w, face)
        while we.More():
            e = we.Current()
            eid = emap.FindIndex(e) - 1
            fwd = e.Orientation() != TopAbs_REVERSED
            loop.append((eid, fwd))
            we.Next()
        f.loops.append(loop)
        f.edge_ids.extend(eid for eid, _ in loop)
    return f


def mesh_solid(solid, edges=None) -> dict:
    """Triangles of the solid for the 3D view, plus its edges as polylines (part coordinates, mm).

    ``edges`` = the Edge list of the same solid (already sampled), drawn as the outline lines."""
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    from OCP.TopLoc import TopLoc_Location

    bb = Bnd_Box()
    BRepBndLib.Add_s(solid, bb)
    lo, hi = _v(bb.CornerMin()), _v(bb.CornerMax())
    diag = float(np.linalg.norm(hi - lo)) or 1.0
    BRepMesh_IncrementalMesh(solid, max(0.02, diag * 0.0006), False, 0.35, True)
    pos, idx = [], []
    exp = TopExp_Explorer(solid, TopAbs_FACE)
    while exp.More():
        face = TopoDS.Face(exp.Current())
        loc = TopLoc_Location()
        tri = BRep_Tool.Triangulation_s(face, loc)
        if tri is not None:
            trsf = loc.Transformation()
            base = len(pos) // 3
            for i in range(1, tri.NbNodes() + 1):
                q = tri.Node(i).Transformed(trsf)
                pos += [q.X(), q.Y(), q.Z()]
            rev = face.Orientation() == TopAbs_REVERSED
            for i in range(1, tri.NbTriangles() + 1):
                a, b, c = tri.Triangle(i).Get()
                if rev:
                    b, c = c, b
                idx += [base + a - 1, base + b - 1, base + c - 1]
        exp.Next()
    lines = []
    for e in edges or []:
        pts = np.asarray(e.pts, dtype=float)
        if e.kind == "line" or len(pts) <= 2:
            pts = np.array([e.p0, e.p1], dtype=float)
        elif len(pts) > 64:                                   # display only: 64 points per curve are plenty
            keep = np.unique(np.linspace(0, len(pts) - 1, 64).round().astype(int))
            pts = pts[keep]
        lines.append({"pts": np.round(pts, 3).ravel().tolist(), "len": round(float(e.length), 3),
                      "kind": e.kind, "r": round(float(e.radius), 3) if getattr(e, "radius", None) else None})
    if pos:                                                   # exact size from the mesh (the quick
        P = np.asarray(pos).reshape(-1, 3)                     # bounding box over-estimates curved faces)
        lo, hi = P.min(axis=0), P.max(axis=0)
    return {"pos": [round(v, 3) for v in pos], "idx": idx, "edges": lines,
            "bbox": [np.round(lo, 3).tolist(), np.round(hi, 3).tolist()]}
