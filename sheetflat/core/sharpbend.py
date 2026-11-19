"""Bends drawn without a radius (a sharp corner) in a single part.

Some models are drawn like an extrusion of an L (or Z, U ...) profile: the two flanges meet at a sharp
edge on the outside and on the inside, with no curved face between them. As one solid it is still one
bent part (a press brake makes the corner), so the sharp corner is read as a bend with inner radius 0:

    inside edge  -> rounded with a tiny radius R_IN (stands for 0; any R below the table limit
                    of 0.2 mm uses the bend table for 90 degree bends)
    outside edge -> rounded with R_IN + thickness

The outside of the part (the "a" and "b" sizes a bend table works from) does not change, so the flat
length is a + b - BD as for any other table bend. This is only used when the part is one solid that the
analysis otherwise splits into several flat pieces at such corners.
"""
from __future__ import annotations

import numpy as np
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepClass3d import BRepClass3d_SolidClassifier
from OCP.BRepFilletAPI import BRepFilletAPI_MakeFillet
from OCP.TopAbs import TopAbs_EDGE, TopAbs_IN
from OCP.TopExp import TopExp
from OCP.TopoDS import TopoDS
from OCP.gp import gp_Pnt
import OCP.collections as _c

from .analysis import analyze
from .brep import extract_solid

R_IN = 0.01                 # stands for a sharp (0 mm) inner radius


def _sharp_edges(solid, sd, an):
    """Straight edges where two flat sheet faces of different pieces meet at an angle:
    returns [(edge id, concave?)]."""
    piece_of = {}
    for k, pc in enumerate(an.pieces):
        for comp in [pc["faces"], *pc["others"]]:
            for fid in comp:
                piece_of[fid] = k
    t = an.thickness
    clf = BRepClass3d_SolidClassifier(solid)
    out = []
    for e in sd.edges:
        if e is None or e.kind != "line" or len(e.face_ids) != 2:
            continue
        a, b = (sd.faces[i] for i in e.face_ids)
        if a.kind != "plane" or b.kind != "plane" or a.id not in an.skin_ids or b.id not in an.skin_ids:
            continue
        if piece_of.get(a.id) is None or piece_of.get(a.id) == piece_of.get(b.id):
            continue
        c = abs(float(np.dot(a.normal, b.normal)))
        if c > 0.999 or e.length < 2 * t:                 # parallel faces (no corner) or a tiny edge
            continue
        # inside corner: a point just off the edge, along a's normal and against b's, is in the material
        m = 0.5 * (e.p0 + e.p1) + 0.3 * t * (a.normal - b.normal)
        clf.Perform(gp_Pnt(*map(float, m)), 1e-6)
        out.append((e.id, clf.State() == TopAbs_IN))
    return out


def _paired(sd, edges, t):
    """Keep corners that go through the sheet: every inside edge needs an outside edge next to it
    (parallel, about one thickness away) and the other way round."""
    def line(eid):
        e = sd.edges[eid]
        d = e.p1 - e.p0
        return e.p0, d / (np.linalg.norm(d) or 1.0)

    def near(i, j):
        p, d = line(i)
        q, f = line(j)
        if abs(abs(float(np.dot(d, f))) - 1) > 1e-3:
            return False
        w = q - p
        dist = np.linalg.norm(w - np.dot(w, d) * d)
        return dist <= 2.0 * t + 1e-6
    ins = [i for i, c in edges if c]
    outs = [i for i, c in edges if not c]
    keep_in = [i for i in ins if any(near(i, j) for j in outs)]
    keep_out = [j for j in outs if any(near(i, j) for i in keep_in)]
    return keep_in, keep_out


def sharp_corners_to_bends(solid, sd, an):
    """A copy of the part with its sharp sheet corners rounded as R 0 bends, analysed again.
    Returns (solid, sd, analysis, n_corners) when that joins the part into fewer pieces, else None."""
    if not an.ok or len(an.pieces) < 2:
        return None
    t = an.thickness
    ins, outs = _paired(sd, _sharp_edges(solid, sd, an), t)
    if not ins:
        return None
    emap = _c.IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher()   # same edge numbering as brep.extract_solid
    TopExp.MapShapes_s(solid, TopAbs_EDGE, emap)
    fil = BRepFilletAPI_MakeFillet(solid)
    for i in ins:
        fil.Add(R_IN, TopoDS.Edge(emap.FindKey(i + 1)))
    for i in outs:
        fil.Add(R_IN + t, TopoDS.Edge(emap.FindKey(i + 1)))
    try:
        fil.Build()
        if not fil.IsDone():
            return None
        new = fil.Shape()
    except Exception:   # noqa
        return None
    if not BRepCheck_Analyzer(new).IsValid():
        return None
    sd2 = extract_solid(new)
    an2 = analyze(sd2)
    if not an2.ok or len(an2.pieces) >= len(an.pieces) or abs(an2.thickness - t) > 0.02 * t + 0.01:
        return None
    return new, sd2, an2, len(ins)
