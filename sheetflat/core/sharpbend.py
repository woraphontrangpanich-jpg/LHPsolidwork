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

A corner can also be sharp on the inside only, with the outside already rounded at R = thickness around the
inside edge (inner radius 0 drawn as a bend, as a sheet-metal model with bend radius 0 gives). Then only the
inside edge is rounded with R_IN. Such corners matter most when the bends alternate direction (a Z): every
side of the sheet is then broken at some corner, and without this the unfold reaches only one stretch of it.
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
# OpenCASCADE cannot always round a corner with the tiniest radius (where the corner meets other rounded
# faces it fails): larger ones are tried in turn. Whatever radius is used, the unfold reads it as 0
# (SolidData.r0_faces), so the flat pattern does not depend on it.
R_TRY = (R_IN, 0.05, 0.1, 0.2)


def _sharp_edges(solid, sd, an):
    """Straight edges where two flat sheet faces of different pieces meet at an angle:
    returns [(edge id, concave?)]."""
    # surface group of each face: faces of one piece can still be in different groups (a sheet side broken
    # at a corner that is sharp on that side), and a corner between two groups is what joins them
    comp_of = {}
    for k, pc in enumerate(an.pieces):
        for j, comp in enumerate([pc["faces"], *pc["others"]]):
            for fid in comp:
                comp_of[fid] = (k, j)
    t = an.thickness
    clf = BRepClass3d_SolidClassifier(solid)
    out = []
    for e in sd.edges:
        if e is None or e.kind != "line" or len(e.face_ids) != 2:
            continue
        a, b = (sd.faces[i] for i in e.face_ids)
        if a.kind != "plane" or b.kind != "plane" or a.id not in an.skin_ids or b.id not in an.skin_ids:
            continue
        if comp_of.get(a.id) is None or comp_of.get(a.id) == comp_of.get(b.id):
            continue
        c = abs(float(np.dot(a.normal, b.normal)))
        if c > 0.999 or e.length < 2 * t:                 # parallel faces (no corner) or a tiny edge
            continue
        # inside corner: a point just off the edge, along a's normal and against b's, is in the material
        m = 0.5 * (e.p0 + e.p1) + 0.3 * t * (a.normal - b.normal)
        clf.Perform(gp_Pnt(*map(float, m)), 1e-6)
        out.append((e.id, clf.State() == TopAbs_IN))
    return out


def _line(sd, eid):
    e = sd.edges[eid]
    d = e.p1 - e.p0
    return e.p0, d / (np.linalg.norm(d) or 1.0)


def _near(sd, i, j, t):
    """Edges i and j are parallel and about one thickness apart (the two edges of one sharp corner)."""
    p, d = _line(sd, i)
    q, f = _line(sd, j)
    if abs(abs(float(np.dot(d, f))) - 1) > 1e-3:
        return False
    w = q - p
    dist = np.linalg.norm(w - np.dot(w, d) * d)
    return dist <= 2.0 * t + 1e-6


def _paired(sd, edges, t):
    """Keep corners that go through the sheet: every inside edge needs an outside edge next to it
    (parallel, about one thickness away) and the other way round."""
    ins = [i for i, c in edges if c]
    outs = [i for i, c in edges if not c]
    keep_in = [i for i in ins if any(_near(sd, i, j, t) for j in outs)]
    keep_out = [j for j in outs if any(_near(sd, i, j, t) for i in keep_in)]
    return keep_in, keep_out


def _outer_arc(sd, eid, t):
    """True when the inside edge ``eid`` already has its outside rounded: a cylinder of radius ~t whose axis is
    the edge itself (inner radius 0) and which runs along the edge."""
    e = sd.edges[eid]
    d = e.p1 - e.p0
    d = d / (np.linalg.norm(d) or 1.0)
    mid = 0.5 * (e.p0 + e.p1)
    for f in sd.faces:
        if f.kind != "cylinder" or f.axis_dir is None or f.axis_pt is None:
            continue
        if abs(f.radius - t) > 0.03 * t + 0.02:
            continue
        if abs(abs(float(np.dot(f.axis_dir, d))) - 1) > 1e-3:
            continue
        w = mid - f.axis_pt
        if np.linalg.norm(w - np.dot(w, f.axis_dir) * f.axis_dir) > 0.02 * t + 0.01:   # axis on the edge
            continue
        # the cylinder runs along the edge (not one further along the same line)
        s0, s1 = (float(np.dot(p - f.axis_pt, f.axis_dir)) for p in (e.p0, e.p1))
        lo, hi = min(f.v0, f.v1), max(f.v0, f.v1)
        if min(s0, s1) < hi - 1e-6 and max(s0, s1) > lo + 1e-6:
            return True
    return False


def _split_score(an):
    """How broken the part is: number of pieces, then the sheet area the unfold would not reach."""
    return len(an.pieces), sum(pc.get("lost_area", 0.0) for pc in an.pieces)


def needs_join(an) -> bool:
    """The analysis split the part (several pieces, or sheet faces the unfold of its piece does not reach)."""
    n, lost = _split_score(an)
    return an.ok and (n > 1 or lost > 0)


def sharp_corners_to_bends(solid, sd, an):
    """A copy of the part with its sharp sheet corners rounded as R 0 bends, analysed again.
    Returns (solid, sd, analysis, n_corners) when that joins the part (fewer pieces, or less of the sheet left
    out of the unfold), else None."""
    if not needs_join(an):
        return None
    t = an.thickness
    edges = _sharp_edges(solid, sd, an)
    ins, outs = _paired(sd, edges, t)
    # inside edges whose outside is already rounded (no outside edge to pair with)
    arc_ins = [i for i, c in edges if c and i not in ins and _outer_arc(sd, i, t)]
    ins = ins + arc_ins
    if not ins:
        return None
    # one corner = its inside edge + the outside edges next to it (none when the outside is already rounded)
    corners = [(i, [j for j in outs if _near(sd, i, j, t)]) for i in ins]
    emap = _c.IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher()   # same edge numbering as brep.extract_solid
    TopExp.MapShapes_s(solid, TopAbs_EDGE, emap)

    def rounded(sel):
        fil = BRepFilletAPI_MakeFillet(solid)
        for (i, js), r in sel:
            fil.Add(r, TopoDS.Edge(emap.FindKey(i + 1)))
            for j in js:
                fil.Add(r + t, TopoDS.Edge(emap.FindKey(j + 1)))
        try:
            fil.Build()
            if not fil.IsDone():
                return None
            shp = fil.Shape()
        except Exception:   # noqa
            return None
        return shp if BRepCheck_Analyzer(shp).IsValid() else None

    sel = [(c, R_IN) for c in corners]
    new = rounded(sel)
    if new is None:
        # all at once failed: take the corners one by one, each with the first radius that works
        sel = []
        for c in corners:
            for r in R_TRY:
                shp = rounded(sel + [(c, r)])
                if shp is not None:
                    sel.append((c, r))
                    new = shp
                    break
        if not sel:
            return None
    sd2 = extract_solid(new)
    sd2.r0_faces = _rounding_faces(sd, sd2, sel, t)
    an2 = analyze(sd2)
    if not an2.ok or abs(an2.thickness - t) > 0.02 * t + 0.01:
        return None
    (n1, lost1), (n2, lost2) = _split_score(an), _split_score(an2)
    if not (n2 < n1 or (n2 == n1 and lost2 < lost1 - 1e-6)):     # the corners must join something
        return None
    return new, sd2, an2, len(sel)


def _rounding_faces(sd, sd2, sel, t):
    """The cylinders this module added (inside R, outside R + t of each rounded corner): ids in sd2."""
    out = set()
    for (i, js), r in sel:
        p, d = _line(sd, i)
        for f in sd2.faces:
            if f.kind != "cylinder" or f.axis_dir is None or f.axis_pt is None:
                continue
            if abs(f.radius - r) > 1e-4 and not (js and abs(f.radius - (r + t)) <= 1e-4):
                continue
            if abs(abs(float(np.dot(f.axis_dir, d))) - 1) > 1e-3:
                continue
            w = f.axis_pt - p
            if np.linalg.norm(w - np.dot(w, d) * d) <= 1.5 * r + 0.05:     # axis just inside the corner
                out.add(f.id)
    return out
