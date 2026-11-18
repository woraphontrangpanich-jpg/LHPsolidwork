"""Fold model of a flat piece, for the 3D fold animation.

The flat outline is split into flanges (rigid) and bend zones (the strip between a bend's two tangent
lines, width BA). Flanges and bends form a tree from the largest flange. The browser folds the sheet by
mapping every flat point through the bends between it and the root (see web/viewer3d.js):

    bend zone point at u (0..BA across the zone)  ->  on an arc of length u, radius BA / angle
    point beyond the zone                          ->  carried rigidly, turned by the bend angle

"up" bends fold towards +Z, the side the 2D drawing is seen from ("พับขึ้น" in the drawing).
"""
from __future__ import annotations

import math

import numpy as np
import shapely
from shapely.geometry import LineString, MultiPolygon, Point, Polygon, box
from shapely.ops import unary_union

from .unfold import prim_points

SLICES = 12          # sub-strips per bend zone (smooth curve while folding)


def _polys(g):
    if g.is_empty:
        return []
    if isinstance(g, Polygon):
        return [g]
    if isinstance(g, MultiPolygon):
        return list(g.geoms)
    return [p for p in getattr(g, "geoms", []) if isinstance(p, Polygon)]


def _poly_json(p: Polygon):
    return {"outer": np.round(np.asarray(p.exterior.coords)[:-1], 4).tolist(),
            "holes": [np.round(np.asarray(h.coords)[:-1], 4).tolist() for h in p.interiors]}


def fold_model(pc, thickness: float) -> dict:
    outers = [Polygon(l["pts"]).buffer(0) for l in pc.loops if l["role"] == "outer"]
    holes = [Polygon(l["pts"]).buffer(0) for l in pc.loops if l["role"] == "hole"]
    sheet = unary_union(outers)
    if holes:
        sheet = sheet.difference(unary_union(holes))
    x0, y0, x1, y1 = sheet.bounds
    far = 4 * max(x1 - x0, y1 - y0, 1.0)

    # ---- bend frames: start line = first tangent line, d = across the zone, a = along the bend
    bends = []
    for i, b in enumerate(pc.bends):
        if len(b.tan_lines) != 2:
            continue
        a0, a1 = np.array(b.tan_lines[0][0], float), np.array(b.tan_lines[0][1], float)
        c0 = np.array(b.tan_lines[1][0], float)
        ax = a1 - a0
        if np.linalg.norm(ax) < 1e-9:
            continue
        ax /= np.linalg.norm(ax)
        d = (c0 - a0) - float((c0 - a0) @ ax) * ax
        ba = float(np.linalg.norm(d))
        if ba < 1e-6:
            continue
        d /= ba
        L = float((a1 - a0) @ ax)
        e = 0.05                                     # mm past the ends of the bend (rounding of the model)
        corners = [a0 - e * ax, a0 + (L + e) * ax, a0 + (L + e) * ax + ba * d, a0 - e * ax + ba * d]
        strip = Polygon(corners)
        bends.append({"src": i, "rec": b, "a0": a0, "ax": ax, "d": d, "ba": ba, "strip": strip, "L": L,
                      "zone": sheet.intersection(strip)})
    strips = unary_union([b["strip"] for b in bends]) if bends else None
    flange_geom = sheet.difference(strips) if strips is not None else sheet
    flanges = [p for p in _polys(flange_geom) if p.area > 1e-3]
    if not flanges:
        flanges = _polys(sheet)

    # ---- which flanges each bend joins (side 0 = before the zone, side 1 = after)
    def side_of(b, poly):
        u = float((np.array(poly.representative_point().coords[0]) - b["a0"]) @ b["d"])
        return 0 if u < b["ba"] / 2 else 1
    for b in bends:
        near = [k for k, f in enumerate(flanges) if f.distance(b["zone"]) < 1e-3]
        b["sides"] = {0: [k for k in near if side_of(b, flanges[k]) == 0], 1: [k for k in near if side_of(b, flanges[k]) == 1]}

    # ---- tree from the largest flange
    root = max(range(len(flanges)), key=lambda k: flanges[k].area)
    chain = {root: []}               # flange -> bends between it and the root, deepest first
    frames = {}                      # bend index -> frame as seen from its parent flange
    queue = [root]
    while queue:
        f = queue.pop(0)
        for j, b in enumerate(bends):
            if j in frames:
                continue
            side = 0 if f in b["sides"][0] else 1 if f in b["sides"][1] else None
            if side is None:
                continue
            if side == 0:
                start, d = b["a0"], b["d"]
            else:
                start, d = b["a0"] + b["ba"] * b["d"], -b["d"]
            frames[j] = {"start": start, "d": d, "parent": f, "chain": [j] + chain[f]}
            for g in b["sides"][1 - side]:
                if g not in chain:
                    chain[g] = [j] + chain[f]
                    queue.append(g)
    for k in range(len(flanges)):
        chain.setdefault(k, [])

    # ---- regions: flanges whole, bend zones sliced along u
    regions = []
    for k, f in enumerate(flanges):
        regions.append({"kind": "flange", "flange": k, "bend": None, "chain": chain[k], "polys": [_poly_json(f)],
                        "size": [round(f.bounds[2] - f.bounds[0], 2), round(f.bounds[3] - f.bounds[1], 2)]})
    for j, b in enumerate(bends):
        if j not in frames:
            continue
        fr = frames[j]
        for s in range(SLICES):
            u0, u1 = b["ba"] * s / SLICES, b["ba"] * (s + 1) / SLICES
            st, dd = fr["start"], fr["d"]
            quad = Polygon([st - far * b["ax"] + u0 * dd, st + far * b["ax"] + u0 * dd,
                            st + far * b["ax"] + u1 * dd, st - far * b["ax"] + u1 * dd]).intersection(b["strip"])
            part = [p for p in _polys(b["zone"].intersection(quad)) if p.area > 1e-6]
            if part:
                regions.append({"kind": "bend", "flange": None, "bend": j, "chain": fr["chain"],
                                "polys": [_poly_json(p) for p in part]})

    # ---- outline loops for the side walls, split where they cross a slice line
    cuts = []                                        # slice boundaries: segments across each bend zone
    for j, b in enumerate(bends):
        if j in frames:
            for s in range(SLICES + 1):
                c = frames[j]["start"] + b["ba"] * s / SLICES * frames[j]["d"]
                lo_, hi_ = float((b["a0"] - c) @ b["ax"]) - 0.06, float((b["a0"] - c) @ b["ax"]) + b["L"] + 0.06
                cuts.append((c, frames[j]["d"], b["ax"], lo_, hi_))
    region_shapes = [unary_union([Polygon(p["outer"], p["holes"]).buffer(0) for p in r["polys"]]) for r in regions]
    tree = shapely.STRtree(region_shapes)

    def nearest_region(xy):
        if not len(region_shapes):
            return [-1] * len(xy)
        idx = tree.query_nearest(shapely.points(xy), return_distance=False)
        out = [-1] * len(xy)
        for i_pt, i_reg in zip(idx[0], idx[1]):
            if out[i_pt] < 0:
                out[i_pt] = int(i_reg)
        return out
    def split_at_bends(pts, closed):
        """Insert a point wherever the polyline crosses a slice line of a bend zone, so that after folding
        it follows the bent sheet (a segment that only had its two ends would be drawn as a chord)."""
        n = len(pts)
        Q = np.roll(pts, -1, axis=0)
        nseg = n if closed else n - 1
        extra = [[] for _ in range(n)]                       # crossings (t along the segment) per segment
        for c, nrm, axv, lo_, hi_ in cuts:
            dp, dq = (pts - c) @ nrm, (Q - c) @ nrm
            hit = np.nonzero(dp * dq < 0)[0]
            if not len(hit):
                continue
            tt = dp[hit] / (dp[hit] - dq[hit])
            X = pts[hit] + tt[:, None] * (Q[hit] - pts[hit])
            along = (X - c) @ axv
            for i_seg, t_, ok in zip(hit, tt, (along >= lo_) & (along <= hi_)):
                if ok and i_seg < nseg:
                    extra[i_seg].append(float(t_))
        out = []
        for i in range(n):
            out.append(pts[i])
            for tt in sorted(extra[i]):
                out.append(pts[i] + tt * (Q[i] - pts[i]))
        return np.array(out)

    walls = []
    for l in pc.loops:
        pts = np.asarray(l["pts"], float)
        if len(pts) > 1 and np.allclose(pts[0], pts[-1]):
            pts = pts[:-1]
        out = split_at_bends(pts, True)
        walls.append({"pts": np.round(out, 4).tolist(), "reg": nearest_region(out), "role": l["role"]})

    # ---- drawing primitives (for length / hole / radius labels and picking), with their region
    prims = []
    for q in pc.prims:
        pts = np.asarray(prim_points(q), float)
        if len(pts) < 2:
            continue
        L = float(np.sum(np.linalg.norm(np.diff(pts, axis=0), axis=1)))
        if len(pts) > 40:
            pts = pts[np.unique(np.linspace(0, len(pts) - 1, 40).round().astype(int))]
        closed = q["t"] == "circle" or (len(pts) > 2 and np.allclose(pts[0], pts[-1]))
        pts = split_at_bends(pts, False) if not closed else pts
        prims.append({"t": q["t"], "len": round(L, 3), "r": round(float(q["r"]), 3) if "r" in q else None,
                      "pts": np.round(pts, 4).tolist(), "reg": nearest_region(pts)})

    out_bends = []
    for j, b in enumerate(bends):
        rec, fr = b["rec"], frames.get(j)
        if fr is None:
            continue
        mid = (np.array(rec.line[0]) + np.array(rec.line[1])) / 2
        # where the neutral layer sits: radius ba/theta, i.e. (ba/theta - r) from the inner face. In the flat
        # it is at height wn (the sheet is -t/2..+t/2; the inner face is +t/2 for an "up" bend).
        theta = math.radians(rec.angle_deg)
        s_ = 1 if rec.direction == "up" else -1
        rho_n = b["ba"] / theta if theta > 1e-9 else rec.r_inner + thickness / 2
        from_inner = min(max(rho_n - rec.r_inner, 0.0), thickness)
        out_bends.append({"wn": round(s_ * (thickness / 2 - from_inner), 5),
            "id": j, "n": b["src"] + 1, "start": np.round(fr["start"], 4).tolist(), "d": np.round(fr["d"], 6).tolist(),
            "ax": np.round(b["ax"], 6).tolist(), "ba": round(b["ba"], 4), "theta": math.radians(rec.angle_deg),
            "s": 1 if rec.direction == "up" else -1, "angle": round(rec.angle_deg, 2),
            "angle_in": round(180.0 - rec.angle_deg, 2), "dir": rec.direction, "r": round(rec.r_inner, 3),
            "method": rec.method, "bd": rec.bd, "k": rec.k, "len": round(rec.length, 2),
            "mid": np.round(mid, 4).tolist(), "chain": fr["chain"]})
    # bends are referenced by position in out_bends from here on
    pos = {b["id"]: i for i, b in enumerate(out_bends)}
    for r in regions:
        r["chain"] = [pos[j] for j in r["chain"] if j in pos]
        r["bend"] = pos.get(r["bend"]) if r["bend"] is not None else None
    for b in out_bends:
        b["chain"] = [pos[j] for j in b["chain"] if j in pos]
        b["id"] = pos[b["id"]]
    return {"t": thickness, "regions": regions, "bends": out_bends, "walls": walls, "prims": prims,
            "bbox": [round(x0, 3), round(y0, 3), round(x1, 3), round(y1, 3)]}
