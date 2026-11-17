"""Unfold a sheet-metal skin (planar + cylindrical faces) into a flat pattern.

The unfolding skin is a graph of faces joined by tangent edges.  A breadth
first walk gives every face a *chart* (3D -> 2D map):

* planar face   : rigid motion
* bend cylinder : (axial position, bend angle) -> (y, s) with ``s = BA * phi/theta``

The flat contour is then the set of skin edges that are *not* shared with
another skin face (i.e. the edges against the thickness side faces) mapped
through the chart of the face that owns them.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

import numpy as np
from shapely.geometry import Polygon

from .analysis import SheetAnalysis, edge_mid, is_tangent, unit
from .bend import BendModel
from .brep import Edge, Face, SolidData

TWO_PI = 2 * math.pi
CLOSE_TOL = 5e-3   # mm - endpoint matching when chaining contours


class UnfoldError(Exception):
    pass


# ----------------------------------------------------------------------------- charts
class PlaneChart:
    kind = "plane"

    def __init__(self, Y0, A, W, Q, D2, M2):
        self.Y0, self.A, self.W, self.Q, self.D2, self.M2 = Y0, A, W, np.asarray(Q, float), np.asarray(D2, float), np.asarray(M2, float)

    def map(self, X):
        d = np.asarray(X) - self.Y0
        return self.Q + float(d @ self.A) * self.D2 + float(d @ self.W) * self.M2

    def map_vec(self, v):
        return float(v @ self.A) * self.D2 + float(v @ self.W) * self.M2


def _wrap(a: float) -> float:
    return (a + math.pi) % TWO_PI - math.pi


def cyl_u(face: Face, X) -> float:
    r = np.asarray(X) - face.axis_pt
    r = r - float(r @ face.axis_dir) * face.axis_dir
    return math.atan2(float(r @ face.ydir), float(r @ face.xdir))


def cyl_tangent(face: Face, u: float) -> np.ndarray:
    return -math.sin(u) * face.xdir + math.cos(u) * face.ydir


class CylChart:
    kind = "cyl"

    def __init__(self, face: Face, Y0, q0, d2_axis, m2, uE, sgn, ba, theta):
        self.face, self.Y0, self.q0 = face, Y0, np.asarray(q0, float)
        self.d2, self.m2 = np.asarray(d2_axis, float), np.asarray(m2, float)
        self.uE, self.sgn, self.ba, self.theta = uE, sgn, ba, theta

    def phi(self, X) -> float:
        u = cyl_u(self.face, X)
        return ((self.sgn * (u - self.uE)) + 0.3) % TWO_PI - 0.3

    def map(self, X):
        z = float((np.asarray(X) - self.Y0) @ self.face.axis_dir)
        s = self.ba * self.phi(X) / self.theta
        return self.q0 + z * self.d2 + s * self.m2

    def t_in(self):
        """3D unit tangent (direction of increasing phi) at the entry line."""
        return self.sgn * cyl_tangent(self.face, self.uE)

    def t_out(self):
        """3D unit tangent (direction of increasing phi) at the exit line."""
        return self.sgn * cyl_tangent(self.face, self.uE + self.sgn * self.theta)


def entry_info(face: Face, Y0):
    """For a cylinder entered through the line through Y0: (u at entry, +-1 direction of increasing phi)."""
    uE = cyl_u(face, Y0)
    d0 = abs(_wrap(uE - face.u0))
    d1 = abs(_wrap(uE - face.u1))
    sgn = 1 if d0 <= d1 else -1
    return uE, sgn


# ----------------------------------------------------------------------------- result types
@dataclass
class BendRecord:
    face_id: int
    angle_deg: float
    r_inner: float
    method: str                  # table | k
    ba: float                    # width of the bend strip in the flat pattern
    bd: float | None
    k: float | None
    direction: str               # up | down  (seen from the viewing side)
    length: float
    line: tuple                  # ((x0,y0),(x1,y1)) centre line in the flat (before translation)
    tan_lines: list = field(default_factory=list)   # the two tangent lines (bend zone borders)


@dataclass
class FlatPiece:
    prims: list                  # drawing primitives (cut contour)
    bends: list                  # BendRecord
    loops: list                  # [{'pts': ndarray(n,2), 'role': 'outer'|'hole'}]
    area: float
    bbox: tuple                  # (xmin, ymin, xmax, ymax)
    faces: set
    flat_faces: int = 0
    warnings: list = field(default_factory=list)


# ----------------------------------------------------------------------------- primitives
def _arc_pts(cx, cy, r, a0, a1, step_deg=2.0):
    """ccw arc a0->a1 (deg) sampled."""
    if a1 <= a0:
        a1 += 360.0
    n = max(4, int(math.ceil((a1 - a0) / step_deg)))
    ang = np.radians(np.linspace(a0, a1, n + 1))
    return np.column_stack([cx + r * np.cos(ang), cy + r * np.sin(ang)])


def prim_points(p) -> np.ndarray:
    t = p["t"]
    if t == "line":
        return np.array([p["a"], p["b"]], float)
    if t == "arc":
        return _arc_pts(p["c"][0], p["c"][1], p["r"], p["a0"], p["a1"])
    if t == "circle":
        return _arc_pts(p["c"][0], p["c"][1], p["r"], 0.0, 360.0)
    return np.array(p["pts"], float)


def prim_ends(p):
    t = p["t"]
    if t == "line":
        return np.array(p["a"], float), np.array(p["b"], float)
    if t == "arc":
        pts = prim_points(p)
        return pts[0], pts[-1]
    if t == "circle":
        c = np.array(p["c"], float)
        return c + [p["r"], 0], c + [p["r"], 0]
    pts = np.array(p["pts"], float)
    return pts[0], pts[-1]


def transform_prim(p, dx=0.0, dy=0.0, mirror=False):
    q = dict(p)
    f = (lambda x, y: (-x + dx, y + dy)) if mirror else (lambda x, y: (x + dx, y + dy))
    t = p["t"]
    if t == "line":
        q["a"], q["b"] = f(*p["a"]), f(*p["b"])
    elif t == "arc":
        q["c"] = f(*p["c"])
        if mirror:
            q["a0"], q["a1"] = (180.0 - p["a1"]) % 360.0, (180.0 - p["a0"]) % 360.0
    elif t == "circle":
        q["c"] = f(*p["c"])
    else:
        q["pts"] = [f(x, y) for x, y in p["pts"]]
    return q


# ----------------------------------------------------------------------------- unfolding
def unfold(sd: SolidData, an: SheetAnalysis, model: BendModel, piece_idx: int | None = None) -> list[FlatPiece]:
    out = []
    for k, pc in enumerate(an.pieces):
        if piece_idx is not None and k != piece_idx:
            continue
        out.append(_unfold_piece(sd, set(pc["faces"]), model))
    return out


def _unfold_piece(sd: SolidData, fs: set, model: BendModel) -> FlatPiece:
    faces = {i: sd.faces[i] for i in fs}
    warnings: list[str] = []
    t = model.thickness

    # adjacency over tangent edges
    adj: dict[int, list[tuple[int, Edge]]] = {i: [] for i in fs}
    for e in sd.edges:
        if len(e.face_ids) == 2:
            a, b = e.face_ids
            if a in fs and b in fs and a != b and is_tangent(sd, e):
                adj[a].append((b, e))
                adj[b].append((a, e))

    jog = _find_jogs(sd, faces, adj, model) if model.jog_use_k else set()
    planes = [f for f in faces.values() if f.kind == "plane"]
    if not planes:
        raise UnfoldError("ไม่มีผิวระนาบให้เป็นฐานของการคลี่")
    base = max(planes, key=lambda f: f.area)
    from .analysis import plane_basis
    U, V = plane_basis(base.normal)
    charts: dict[int, object] = {base.id: PlaneChart(base.origin, U, V, (0.0, 0.0), (1.0, 0.0), (0.0, 1.0))}
    bends: dict[int, BendRecord] = {}
    queue = deque([base.id])
    seen_edges = set()
    while queue:
        fid = queue.popleft()
        fch = charts[fid]
        f = faces[fid]
        for gid, e in adj[fid]:
            g = faces[gid]
            if gid in charts:
                if e.id not in seen_edges:
                    seen_edges.add(e.id)
                    # loop closure check
                    for P in (e.p0, e.p1):
                        d = np.linalg.norm(fch.map(P) - charts[gid].map(P))
                        if d > 0.05:
                            raise UnfoldError(
                                "ชิ้นงานมีวงปิดของผิวโค้ง/รอยพับ (เช่น กล่อง/ท่อที่ไม่มีรอยเปิด) จึงคลี่เป็นแผ่นเรียบไม่ได้")
                continue
            seen_edges.add(e.id)
            ch = _chart_across(sd, fch, f, e, g, model, bends, warnings, jog)
            charts[gid] = ch
            queue.append(gid)
    missing = fs - set(charts)
    if missing:
        warnings.append(f"มี {len(missing)} ผิวที่เชื่อมต่อไม่ถึง (ไม่ถูกคลี่)")

    # ---- boundary edges -> primitives
    prims: list[dict] = []
    for fid, ch in charts.items():
        f = faces[fid]
        for eid in set(f.edge_ids):
            e = sd.edges[eid]
            others = [x for x in e.face_ids if x != fid]
            if others and all(x in charts for x in others):
                continue      # internal tangent edge between two skin faces
            if not others and len(e.face_ids) >= 1 and e.face_ids.count(fid) >= 1 and len(set(e.face_ids)) == 1:
                continue      # seam edge of a periodic face
            prims.extend(_map_edge(e, f, ch))

    prims = _merge_arcs(prims)

    # ---- bend lines (centre line + tangent lines) in flat coordinates
    for fid, ch in charts.items():
        if ch.kind != "cyl":
            continue
        f = faces[fid]
        zs = []
        for eid in set(f.edge_ids):
            P = sd.edges[eid].pts
            zs.extend((P - ch.Y0) @ f.axis_dir)
        z0, z1 = min(zs), max(zs)
        rec = bends[fid]
        c0 = ch.q0 + z0 * ch.d2 + 0.5 * ch.ba * ch.m2
        c1 = ch.q0 + z1 * ch.d2 + 0.5 * ch.ba * ch.m2
        rec.line = (tuple(c0), tuple(c1))
        rec.length = z1 - z0
        rec.tan_lines = [
            (tuple(ch.q0 + z0 * ch.d2), tuple(ch.q0 + z1 * ch.d2)),
            (tuple(ch.q0 + z0 * ch.d2 + ch.ba * ch.m2), tuple(ch.q0 + z1 * ch.d2 + ch.ba * ch.m2)),
        ]

    loops, area, loop_warn = _build_loops(prims)
    warnings.extend(loop_warn)
    allpts = np.vstack([l["pts"] for l in loops]) if loops else np.zeros((1, 2))
    bbox = (allpts[:, 0].min(), allpts[:, 1].min(), allpts[:, 0].max(), allpts[:, 1].max())
    return FlatPiece(prims=prims, bends=list(bends.values()), loops=loops, area=area, bbox=bbox, faces=set(charts),
                     flat_faces=sum(1 for c in charts.values() if c.kind == "plane"), warnings=warnings)


def _find_jogs(sd, faces, adj, model) -> set:
    """Bend faces that form a jog: two opposite bends joined by a very short flat."""
    jog = set()
    for pid, f in faces.items():
        if f.kind != "plane":
            continue
        cyl = [(g, e) for g, e in ((faces[x], ed) for x, ed in adj[pid]) if g.kind == "cylinder"]
        for i in range(len(cyl)):
            for j in range(i + 1, len(cyl)):
                (g1, e1), (g2, e2) = cyl[i], cyl[j]
                if g1.out_sign == g2.out_sign:
                    continue
                a1, a2 = unit(e1.p1 - e1.p0), unit(e2.p1 - e2.p0)
                if abs(float(a1 @ a2)) < 0.999:
                    continue
                d = 0.5 * (e2.p0 + e2.p1) - 0.5 * (e1.p0 + e1.p1)
                dist = float(np.linalg.norm(d - float(d @ a1) * a1))
                if dist <= model.jog_max_flat_ratio * model.thickness:
                    jog.add(g1.id)
                    jog.add(g2.id)
    return jog


def _chart_across(sd, fch, f: Face, e: Edge, g: Face, model: BendModel, bends: dict, warnings: list, jog=frozenset()):
    if e.kind != "line":
        raise UnfoldError("รอยต่อระหว่างผิวไม่ใช่เส้นตรง (ผิวโค้งแบบอื่นที่ยังไม่รองรับ)")
    Y0 = 0.5 * (e.p0 + e.p1)
    a = unit(e.p1 - e.p0)

    if g.kind == "cylinder":
        uE, sgn = entry_info(g, Y0)
        out3 = sgn * cyl_tangent(g, uE)
    elif fch.kind == "cyl":
        out3 = None
    else:   # plane -> plane (coplanar split faces)
        out3 = unit(np.cross(f.normal, a))
        gpts = np.vstack([sd.edges[i].pts for i in g.edge_ids])
        if float((gpts.mean(axis=0) - Y0) @ out3) < 0:
            out3 = -out3

    if fch.kind == "plane":
        if out3 is None:
            raise UnfoldError("โครงสร้างผิวไม่รองรับ")
        D2 = fch.map_vec(a)
        M2 = fch.map_vec(out3)
    else:
        phi = fch.phi(Y0)
        at_exit = abs(phi - fch.theta) < abs(phi)
        out_f = fch.t_out() if at_exit else -fch.t_in()
        M2 = fch.m2 if at_exit else -fch.m2
        D2 = fch.d2 * (1.0 if float(a @ fch.face.axis_dir) >= 0 else -1.0)
        if out3 is None:
            out3 = out_f
    D2 = unit(D2)
    M2 = unit(M2)
    out3 = unit(out3 - float(out3 @ a) * a)
    q = fch.map(Y0)

    if g.kind == "plane":
        return PlaneChart(Y0, a, out3, q, D2, M2)

    # cylinder g
    theta = g.u1 - g.u0
    if theta <= 1e-6:
        raise UnfoldError("มุมพับเป็นศูนย์")
    skin_outer = g.out_sign > 0
    r_in = g.radius - model.thickness if skin_outer else g.radius
    if r_in < 0:
        warnings.append(f"รัศมีในของรอยพับติดลบ ({r_in:.3f}) ปรับเป็น 0")
        r_in = 0.0
    ba, method, bd, k = model.allowance(theta, r_in, force_k=g.id in jog)
    d2_axis = D2 * (1.0 if float(a @ g.axis_dir) >= 0 else -1.0)
    ch = CylChart(g, Y0, q, d2_axis, M2, uE, sgn, ba, theta)
    bends[g.id] = BendRecord(face_id=g.id, angle_deg=math.degrees(theta), r_inner=r_in, method=method, ba=ba, bd=bd, k=k,
                             direction="down" if skin_outer else "up", length=0.0, line=((0, 0), (0, 0)))
    return ch


def _map_edge(e: Edge, f: Face, ch) -> list[dict]:
    if ch.kind == "plane":
        if e.kind == "line":
            return [{"t": "line", "a": tuple(ch.map(e.p0)), "b": tuple(ch.map(e.p1))}]
        if e.kind == "circle":
            c2 = ch.map(e.center)
            if e.closed:
                return [{"t": "circle", "c": tuple(c2), "r": e.radius}]
            p0, p1 = ch.map(e.p0), ch.map(e.pts[len(e.pts) // 2]),
            p0, pm, p1 = ch.map(e.p0), ch.map(e.pts[len(e.pts) // 2]), ch.map(e.p1)
            a0 = math.degrees(math.atan2(p0[1] - c2[1], p0[0] - c2[0]))
            a1 = math.degrees(math.atan2(p1[1] - c2[1], p1[0] - c2[0]))
            cross = (pm[0] - p0[0]) * (p1[1] - pm[1]) - (pm[1] - p0[1]) * (p1[0] - pm[0])
            if cross < 0:   # clockwise travel -> arc is p1 -> p0 ccw
                a0, a1 = a1, a0
            return [{"t": "arc", "c": tuple(c2), "r": e.radius, "a0": a0 % 360.0, "a1": a1 % 360.0}]
        return [{"t": "poly", "pts": [tuple(ch.map(P)) for P in e.pts]}]
    # cylinder
    ax = f.axis_dir
    if e.kind == "line" and abs(float(unit(e.p1 - e.p0) @ ax)) > 0.9999:
        return [{"t": "line", "a": tuple(ch.map(e.p0)), "b": tuple(ch.map(e.p1))}]
    if e.kind == "circle" and abs(float(e.axis @ ax)) > 0.9999:
        d = e.center - f.axis_pt
        if np.linalg.norm(d - float(d @ ax) * ax) < 1e-4 and not e.closed:
            return [{"t": "line", "a": tuple(ch.map(e.p0)), "b": tuple(ch.map(e.p1))}]
    return [{"t": "poly", "pts": [tuple(ch.map(P)) for P in e.pts]}]


def _merge_arcs(prims: list[dict]) -> list[dict]:
    """STEP files split holes into two half arcs (seam edges). Re-join arcs that close into a full circle."""
    groups: dict[tuple, list[int]] = {}
    for i, p in enumerate(prims):
        if p["t"] == "arc":
            groups.setdefault((round(p["c"][0], 2), round(p["c"][1], 2), round(p["r"], 2)), []).append(i)
    drop, add = set(), []
    for key, idx in groups.items():
        span = sum(((prims[i]["a1"] - prims[i]["a0"]) % 360.0) or 360.0 for i in idx)
        if len(idx) >= 2 and abs(span - 360.0) < 0.05:
            p0 = prims[idx[0]]
            add.append({"t": "circle", "c": p0["c"], "r": p0["r"]})
            drop.update(idx)
    if not drop:
        return prims
    return [p for i, p in enumerate(prims) if i not in drop] + add


def _build_loops(prims: list[dict]):
    """Chain primitives into closed loops; returns (loops, area, warnings)."""
    warn = []
    n = len(prims)
    used = [False] * n
    ends = [prim_ends(p) for p in prims]
    loops = []
    for i in range(n):
        if used[i]:
            continue
        used[i] = True
        if prims[i]["t"] == "circle":
            loops.append(prim_points(prims[i]))
            continue
        pts = list(prim_points(prims[i]))
        start = np.array(pts[0])
        cur = np.array(pts[-1])
        guard = 0
        while np.linalg.norm(cur - start) > CLOSE_TOL and guard < n + 5:
            guard += 1
            found = False
            for j in range(n):
                if used[j] or prims[j]["t"] == "circle":
                    continue
                a, b = ends[j]
                if np.linalg.norm(a - cur) <= CLOSE_TOL:
                    seg = prim_points(prims[j])
                elif np.linalg.norm(b - cur) <= CLOSE_TOL:
                    seg = prim_points(prims[j])[::-1]
                else:
                    continue
                used[j] = True
                pts.extend(list(seg[1:]))
                cur = np.array(pts[-1])
                found = True
                break
            if not found:
                break
        if np.linalg.norm(cur - start) > CLOSE_TOL:
            warn.append(f"เส้นขอบไม่ปิดสนิท (ช่องว่าง {np.linalg.norm(cur - start):.3f} มม. ที่ {cur[0]:.1f},{cur[1]:.1f})")
        loops.append(np.array(pts))
    polys = []
    for L in loops:
        try:
            polys.append(Polygon(L).buffer(0))
        except Exception:
            polys.append(Polygon())
    depth = []
    for i, p in enumerate(polys):
        d = 0
        if not p.is_empty:
            rp = p.representative_point()
            for j, q in enumerate(polys):
                if i != j and not q.is_empty and q.area > p.area and q.contains(rp):
                    d += 1
        depth.append(d)
    out = []
    area = 0.0
    for L, p, d in zip(loops, polys, depth):
        role = "outer" if d % 2 == 0 else "hole"
        out.append({"pts": L, "role": role})
        area += p.area if role == "outer" else -p.area
    return out, area, warn
