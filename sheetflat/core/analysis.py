"""Sheet-metal recognition on a B-rep (numpy) description.

Pipeline
--------
1. build 2D polygons for planar faces
2. estimate the sheet thickness ``t`` by casting a ray into the material from
   every planar face and histogramming the distance to the opposite (anti
   parallel) planar face
3. mark "skin" faces (planar faces having a partner at distance ``t`` plus the
   cylinders that are tangent to them = the bends)
4. group skin faces by tangent continuity -> the two skins of every sheet piece
5. choose the skin that will be unfolded
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from shapely.geometry import Point, Polygon
from shapely.validation import make_valid

from .brep import Edge, Face, SolidData

ANG_TOL = math.radians(2.0)


def unit(v):
    n = np.linalg.norm(v)
    return v / n if n > 0 else v


def plane_basis(n: np.ndarray):
    a = np.array([1.0, 0, 0]) if abs(n[0]) < 0.9 else np.array([0, 1.0, 0])
    u = unit(np.cross(n, a))
    v = np.cross(n, u)
    return u, v


def loop_points(sd: SolidData, loop) -> np.ndarray:
    pts = []
    for eid, fwd in loop:
        p = sd.edges[eid].pts
        if not fwd:
            p = p[::-1]
        pts.extend(p[:-1])
    # the last point of the last edge equals the first point of the loop
    return np.array(pts)


def face_polygon(sd: SolidData, f: Face):
    """Shapely polygon (in plane coordinates) of a planar face + the (origin,u,v) frame."""
    if f._poly is not None:
        return f._poly
    u, v = plane_basis(f.normal)
    rings = []
    for loop in f.loops:
        P = loop_points(sd, loop)
        if len(P) < 3:
            continue
        d = P - f.origin
        rings.append(np.column_stack([d @ u, d @ v]))
    if not rings:
        f._poly = (None, f.origin, u, v)
        return f._poly
    polys = [Polygon(r) for r in rings]
    areas = [abs(p.area) for p in polys]
    k = int(np.argmax(areas))
    shell = polys[k]
    holes = [polys[i].exterior.coords for i in range(len(polys)) if i != k]
    poly = Polygon(shell.exterior.coords, holes)
    if not poly.is_valid:
        poly = make_valid(poly)
    f._poly = (poly, f.origin, u, v)
    return f._poly


def normal_at(f: Face, X: np.ndarray):
    if f.kind == "plane":
        return f.normal
    if f.kind == "cylinder":
        r = X - f.axis_pt
        r = r - np.dot(r, f.axis_dir) * f.axis_dir
        n = np.linalg.norm(r)
        if n < 1e-12:
            return None
        return unit(r) * f.out_sign
    return None


def edge_mid(e: Edge) -> np.ndarray:
    if e.kind == "line":
        return 0.5 * (e.p0 + e.p1)
    return e.pts[len(e.pts) // 2]


def is_tangent(sd: SolidData, e: Edge) -> bool:
    fs = [sd.faces[i] for i in e.face_ids]
    if len(fs) != 2:
        return False
    m = edge_mid(e)
    n1, n2 = normal_at(fs[0], m), normal_at(fs[1], m)
    if n1 is None or n2 is None:
        return False
    return float(np.dot(n1, n2)) > math.cos(ANG_TOL)


@dataclass
class SheetAnalysis:
    ok: bool = False
    reason: str = ""                       # when not ok: why this is not sheet metal / cannot be handled
    thickness: float = 0.0
    thickness_weight: float = 0.0          # share of planar area supporting this thickness (0..1)
    skin_ids: set = field(default_factory=set)
    pieces: list = field(default_factory=list)          # list of dicts {faces:set, other:set}
    notes: list = field(default_factory=list)
    unsupported_faces: list = field(default_factory=list)


def estimate_thickness(sd: SolidData, max_t: float = 25.0):
    """Returns (t, weight, per-face partner distance dict)."""
    planes = [f for f in sd.faces if f.kind == "plane"]
    infos = {}
    for f in planes:
        poly, o, u, v = face_polygon(sd, f)
        if poly is None or poly.is_empty or poly.area < 1e-6:
            continue
        rp = poly.representative_point()
        P = o + u * rp.x + v * rp.y
        best = None
        for g in planes:
            if g.id == f.id:
                continue
            if np.dot(g.normal, f.normal) > -math.cos(math.radians(0.5)):
                continue
            s = -float(np.dot(P - g.origin, g.normal))   # distance along -n_f until the plane of g
            if s <= 1e-4 or s > max_t * 4:
                continue
            if best is not None and s >= best[0]:
                continue
            Q = P - f.normal * s
            gp, go, gu, gv = face_polygon(sd, g)
            if gp is None:
                continue
            d = Q - go
            if gp.buffer(1e-6).contains(Point(float(d @ gu), float(d @ gv))):
                best = (s, g.id)
        if best is not None:
            infos[f.id] = best
    # weighted histogram of the distances
    cand = sorted((d, sd.faces[fid].area) for fid, (d, _) in infos.items())
    total_planar = sum(f.area for f in planes) or 1.0
    best_t, best_w = 0.0, 0.0
    i = 0
    n = len(cand)
    while i < n:
        d0 = cand[i][0]
        tol = max(0.01, 0.01 * d0)
        j = i
        w = 0.0
        acc = 0.0
        while j < n and cand[j][0] - d0 <= tol:
            w += cand[j][1]
            acc += cand[j][0] * cand[j][1]
            j += 1
        if w > best_w:
            best_w, best_t = w, acc / w
        i += 1
    return best_t, best_w / total_planar, infos


def analyze(sd: SolidData, max_thickness: float = 12.7, min_thickness: float = 0.1) -> SheetAnalysis:
    res = SheetAnalysis()
    planes = [f for f in sd.faces if f.kind == "plane"]
    if len(planes) < 2:
        res.reason = "ไม่มีผิวระนาบพอที่จะเป็นแผ่นโลหะ (ทรงกลม/ทรงกระบอก/ผิวโค้งอิสระ)"
        return res
    t, wfrac, infos = estimate_thickness(sd)
    res.thickness, res.thickness_weight = t, wfrac
    if t <= 0 or wfrac < 0.30:
        res.reason = "ไม่พบความหนาคงที่สม่ำเสมอ (ไม่ใช่แผ่นโลหะ)"
        return res
    if t > max_thickness:
        res.reason = f"ความหนา {t:.2f} มม. มากกว่าเกณฑ์แผ่นโลหะ ({max_thickness:g} มม.) - น่าจะเป็นชิ้นงานตัน/โปรไฟล์"
        return res
    if t < min_thickness:
        res.reason = f"ความหนา {t:.3f} มม. บางผิดปกติ"
        return res

    tol_t = max(0.02, 0.03 * t)
    skin = {fid for fid, (d, _) in infos.items() if abs(d - t) <= tol_t}
    # cylinders tangent to skin faces (bends) - iterate to propagate through S-bends
    by_id = {f.id: f for f in sd.faces}
    changed = True
    while changed:
        changed = False
        for e in sd.edges:
            if len(e.face_ids) != 2:
                continue
            a, b = e.face_ids
            fa, fb = by_id[a], by_id[b]
            for x, y in ((fa, fb), (fb, fa)):
                if x.id in skin and y.id not in skin and y.kind in ("cylinder", "cone", "torus", "other") and is_tangent_any(sd, e):
                    if y.kind == "cylinder" and _is_bend_cylinder(sd, y, e):
                        skin.add(y.id)
                        changed = True
    res.skin_ids = skin
    if not skin:
        res.reason = "ไม่พบผิวหน้าแผ่นโลหะ"
        return res

    # tangent adjacency inside the skin
    adj: dict[int, list[tuple[int, int]]] = {i: [] for i in skin}
    for e in sd.edges:
        if len(e.face_ids) == 2:
            a, b = e.face_ids
            if a in skin and b in skin and a != b and is_tangent(sd, e):
                adj[a].append((b, e.id))
                adj[b].append((a, e.id))
    # components of tangent continuity
    comp_of: dict[int, int] = {}
    comps: list[set] = []
    for s in sorted(skin):
        if s in comp_of:
            continue
        stack, cur = [s], set()
        while stack:
            x = stack.pop()
            if x in cur:
                continue
            cur.add(x)
            comp_of[x] = len(comps)
            for y, _ in adj[x]:
                if y not in cur:
                    stack.append(y)
        comps.append(cur)

    # pair components (opposite skins): planar partners
    link = {i: set() for i in range(len(comps))}
    for fid, (d, gid) in infos.items():
        if fid in skin and gid in skin and abs(d - t) <= tol_t:
            a, b = comp_of[fid], comp_of[gid]
            if a != b:
                link[a].add(b)
                link[b].add(a)
    # pieces = connected sets of components
    seen, pieces = set(), []
    for i in range(len(comps)):
        if i in seen:
            continue
        st, grp = [i], set()
        while st:
            x = st.pop()
            if x in grp:
                continue
            grp.add(x)
            st.extend(link[x] - grp)
        seen |= grp
        pieces.append(grp)
    area = lambda c: sum(by_id[x].area for x in c)
    for grp in pieces:
        ordered = sorted(grp, key=lambda c: (-round(area(comps[c]), 3), min(comps[c])))
        main = ordered[0]
        res.pieces.append({"faces": comps[main], "others": [comps[c] for c in ordered[1:]]})

    # faces that are neither skin nor plain side faces (formed features, cones, ...)
    for f in sd.faces:
        if f.id in skin:
            continue
        if f.kind in ("cone", "torus", "sphere", "other"):
            res.unsupported_faces.append(f.id)
    res.ok = True
    return res


def is_tangent_any(sd: SolidData, e: Edge) -> bool:
    return is_tangent(sd, e)


def _is_bend_cylinder(sd: SolidData, cyl: Face, tangent_edge: Edge) -> bool:
    """A cylinder tangent to a skin face is a bend when the tangent edge is parallel to its axis."""
    if tangent_edge.kind != "line":
        return False
    d = unit(tangent_edge.p1 - tangent_edge.p0)
    return abs(float(np.dot(d, cyl.axis_dir))) > math.cos(math.radians(1.0))
