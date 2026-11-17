"""Compare SheetFlat flat patterns with reference DXF files (e.g. drawn by hand in SolidWorks).

    python tools/compare_reference.py <assembly.step> <reference1.dxf> [reference2.dxf ...] [--png outdir]

Every closed outline in the reference is matched to a part of the assembly (by size),
aligned (rotation 0/90/180/270, mirror) and compared:
size, area, holes (count, diameters, positions), cut length and the largest boundary
deviation in both directions, with where it happens.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import ezdxf
import numpy as np
import shapely
from shapely import affinity
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import polygonize, unary_union

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sheetflat.core.profiles import ProfileStore  # noqa: E402
from sheetflat.core.project import Project  # noqa: E402
from sheetflat.core.unfold import prim_points  # noqa: E402

SNAP = 1e-3


def ref_lines(path):
    out = []
    for e in ezdxf.readfile(str(path)).modelspace():
        t = e.dxftype()
        if t == "LINE":
            out.append(("line", LineString([(e.dxf.start.x, e.dxf.start.y), (e.dxf.end.x, e.dxf.end.y)])))
        elif t == "CIRCLE":
            out.append(("circle", Point(e.dxf.center.x, e.dxf.center.y).buffer(e.dxf.radius, 256).exterior, e.dxf.radius,
                        (e.dxf.center.x, e.dxf.center.y)))
        elif t in ("ARC", "ELLIPSE", "SPLINE"):
            out.append(("arc", LineString([(p.x, p.y) for p in e.flattening(0.002)])))
        elif t == "LWPOLYLINE":
            pts = [(p[0], p[1]) for p in e.get_points()]
            if e.closed:
                pts.append(pts[0])
            out.append(("poly", LineString(pts)))
    return out


def regions_from_lines(geoms):
    """Filled regions (outer minus holes) built only from line work."""
    faces = list(polygonize(unary_union([shapely.set_precision(g, SNAP) for g in geoms])))
    shells = [Polygon(f.exterior) for f in faces]
    depth = []
    for i, s in enumerate(shells):
        rp = s.representative_point()
        depth.append(sum(1 for j, o in enumerate(shells) if j != i and o.area > s.area and o.contains(rp)))
    outers = [s for s, d in zip(shells, depth) if d % 2 == 0]
    holes = [s for s, d in zip(shells, depth) if d % 2 == 1]
    parts = []
    for o in outers:
        inner = [h for h in holes if o.contains(h.representative_point())]
        poly = o
        for h in inner:
            poly = poly.difference(h)
        parts.append((o, inner, poly))
    return parts


def our_part_geometry(pc):
    lines = [LineString(prim_points(q)) for q in pc.prims]
    circles = [(q["r"], tuple(q["c"])) for q in pc.prims if q["t"] == "circle"]
    return lines, circles


def transforms():
    for mirror in (False, True):
        for rot in (0, 90, 180, 270):
            yield mirror, rot


def apply(g, mirror, rot, dx=0.0, dy=0.0):
    if mirror:
        g = affinity.scale(g, xfact=-1, yfact=1, origin=(0, 0))
    g = affinity.rotate(g, rot, origin=(0, 0))
    return affinity.translate(g, dx, dy)


def max_dev(a, b, n=4000):
    """largest distance from points of line-set a to line-set b, and where."""
    worst, where = 0.0, None
    L = a.length
    for i in range(n + 1):
        p = a.interpolate(L * i / n)
        d = b.distance(p)
        if d > worst:
            worst, where = d, (p.x, p.y)
    return worst, where


def compare(step, refs, png_dir=None):
    store = ProfileStore(Path(ROOT / "data" / "_cmp_profiles.json"))
    pr = Project(store, Path(step).stem, Path(step).name)
    pr.load_step(str(step))
    ours = {}
    for rec in pr.parts:
        if rec.pieces:
            ours[rec.name] = rec
    report = []
    for ref in refs:
        items = ref_lines(ref)
        geoms = [it[1] for it in items]
        circles_ref = [(it[2], it[3]) for it in items if it[0] == "circle"]
        for outer, holes, region in regions_from_lines(geoms):
            if outer.area < 50:
                continue
            bx = outer.bounds
            rw, rh = bx[2] - bx[0], bx[3] - bx[1]
            best = None
            for name, rec in ours.items():
                pc = rec.pieces[0]
                w, h = pc.bbox[2] - pc.bbox[0], pc.bbox[3] - pc.bbox[1]
                d = min(abs(w - rw) + abs(h - rh), abs(w - rh) + abs(h - rw))
                if best is None or d < best[0]:
                    best = (d, name)
            if best is None or best[0] > 20:
                report.append({"ref": Path(ref).name, "part": None, "size": (rw, rh), "note": "no matching part"})
                continue
            rec = ours[best[1]]
            pc = rec.pieces[0]
            o_lines, o_circles = our_part_geometry(pc)
            o_region = Polygon()
            from shapely.geometry import Polygon as P
            outs = unary_union([P(l["pts"]).buffer(0) for l in pc.loops if l["role"] == "outer"])
            hls = unary_union([P(l["pts"]).buffer(0) for l in pc.loops if l["role"] == "hole"])
            o_region = outs.difference(hls)
            ref_set = unary_union([g for g in geoms if outer.buffer(0.5).contains(g)])
            our_set = unary_union(o_lines)
            # best alignment by IoU
            top = None
            for mirror, rot in transforms():
                r = apply(o_region, mirror, rot)
                dx, dy = outer.bounds[0] - r.bounds[0], outer.bounds[1] - r.bounds[1]
                r = affinity.translate(r, dx, dy)
                iou = r.intersection(region).area / max(1e-9, r.union(region).area)
                if top is None or iou > top[0]:
                    top = (iou, mirror, rot, dx, dy)
            iou, mirror, rot, dx, dy = top
            our_al = apply(our_set, mirror, rot, dx, dy)
            d1, w1 = max_dev(our_al, ref_set)
            d2, w2 = max_dev(ref_set, our_al)
            # holes: our circles vs reference circles inside this outline
            rc = [(r, c) for r, c in circles_ref if outer.contains(Point(c))]
            oc = []
            for r, c in o_circles:
                p = apply(Point(c), mirror, rot, dx, dy)
                oc.append((r, (p.x, p.y)))
            hole_err = 0.0
            unmatched = 0
            for r, c in rc:
                cand = [(math.dist(c, c2) + abs(r - r2), r2, c2) for r2, c2 in oc]
                if not cand:
                    unmatched += 1
                    continue
                e = min(cand)[0]
                hole_err = max(hole_err, e)
                if e > 0.05:
                    unmatched += 1
            row = {
                "ref": Path(ref).name, "part": rec.name,
                "ours_wh": tuple(sorted((round(pc.bbox[2] - pc.bbox[0], 3), round(pc.bbox[3] - pc.bbox[1], 3)))),
                "ref_wh": tuple(sorted((round(rw, 3), round(rh, 3)))),
                "area_ours": round(o_region.area, 2), "area_ref": round(region.area, 2), "iou": round(iou, 6),
                "len_ours": round(our_set.length, 2), "len_ref": round(ref_set.length, 2),
                "holes_ours": len(o_circles), "holes_ref": len(rc), "hole_max_err": round(hole_err, 4),
                "holes_unmatched": unmatched,
                "dev_ours_to_ref": round(d1, 4), "at_ours": w1, "dev_ref_to_ours": round(d2, 4), "at_ref": w2,
                "mirror": mirror, "rot": rot,
            }
            report.append(row)
            if png_dir:
                _png(png_dir, row, our_al, ref_set, w1, w2)
    return report


def _png(out_dir, row, ours, ref, w1, w2):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    fig, axs = plt.subplots(1, 3, figsize=(15, 5))
    for ax, (cx, cy, span) in zip(axs, [(None, None, None), (*(w1 or (0, 0)), 6), (*(w2 or (0, 0)), 6)]):
        for g, col, lw in ((ref, "tab:red", 2.2), (ours, "k", 0.9)):
            for part in getattr(g, "geoms", [g]):
                x, y = part.xy
                ax.plot(x, y, color=col, lw=lw)
        ax.set_aspect("equal")
        if span:
            ax.set_xlim(cx - span, cx + span)
            ax.set_ylim(cy - span, cy + span)
    axs[0].set_title(f"{row['part']}  red=reference black=ours")
    axs[1].set_title(f"max ours->ref {row['dev_ours_to_ref']}")
    axs[2].set_title(f"max ref->ours {row['dev_ref_to_ours']}")
    fig.savefig(out_dir / f"{row['part']}.png", dpi=70)
    plt.close(fig)


if __name__ == "__main__":
    args = sys.argv[1:]
    png = None
    if "--png" in args:
        i = args.index("--png")
        png = args[i + 1]
        del args[i:i + 2]
    rows = compare(args[0], args[1:], png)
    for r in rows:
        print(r)
