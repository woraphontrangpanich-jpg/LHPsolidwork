"""Place flat patterns of one (material, thickness) group on one or more sheets.

Simple shelf packing on bounding boxes: the real nesting is left to the laser CAM,
this only has to put everything in one DXF without overlaps.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .unfold import FlatPiece, transform_prim


@dataclass
class Item:
    part_id: str
    name: str
    copy: int
    piece: FlatPiece
    flip: bool = False

    @property
    def size(self):
        b = self.piece.bbox
        return b[2] - b[0], b[3] - b[1]


@dataclass
class Placed:
    item: Item
    x: float
    y: float
    w: float
    h: float


@dataclass
class Sheet:
    index: int
    placed: list[Placed] = field(default_factory=list)
    width: float = 0.0
    height: float = 0.0
    oversize: bool = False


def pack(items: list[Item], gap: float = 10.0, max_width: float = 2500.0,
         sheet: tuple[float, float] | None = None, margin: float = 0.0) -> list[Sheet]:
    """Shelf-pack ``items``.  ``sheet`` = (width, length) of the raw sheet; rows run along the width."""
    if sheet:
        W, L = sheet
        W, L = min(W, L), max(W, L)      # rows run along the short side
        row_limit = W - 2 * margin
        col_limit = L - 2 * margin
    else:
        row_limit = max_width
        col_limit = None
    order = sorted(items, key=lambda it: (-it.size[1], -it.size[0], it.name, it.copy))
    sheets: list[Sheet] = [Sheet(0)]
    x = y = margin
    row_h = 0.0
    for it in order:
        w, h = it.size
        cur = sheets[-1]
        # new row?
        if x > margin and x + w > row_limit + margin + 1e-9:
            x = margin
            y += row_h + gap
            row_h = 0.0
        # new sheet?
        if col_limit is not None and y > margin and y + h > col_limit + margin + 1e-9:
            sheets.append(Sheet(len(sheets)))
            cur = sheets[-1]
            x = y = margin
            row_h = 0.0
        if col_limit is not None and (w > row_limit + 1e-9 or h > col_limit + 1e-9):
            cur.oversize = True
        cur.placed.append(Placed(it, x, y, w, h))
        x += w + gap
        row_h = max(row_h, h)
    for s in sheets:
        if s.placed:
            s.width = max(p.x + p.w for p in s.placed) + margin
            s.height = max(p.y + p.h for p in s.placed) + margin
    return [s for s in sheets if s.placed]


def place_geometry(p: Placed):
    """Return (prims, bend_lines) of a placed item in sheet coordinates."""
    it = p.item
    pc = it.piece
    xmin, ymin, xmax, ymax = pc.bbox
    if it.flip:
        dx, dy = p.x + xmax, p.y - ymin       # x' = xmax - x  (mirror about the bbox centre line)
        prims = [transform_prim(q, dx, dy, mirror=True) for q in pc.prims]
        mp = lambda pt: (dx - pt[0], pt[1] + dy)
    else:
        dx, dy = p.x - xmin, p.y - ymin
        prims = [transform_prim(q, dx, dy) for q in pc.prims]
        mp = lambda pt: (pt[0] + dx, pt[1] + dy)
    bends = []
    for b in pc.bends:
        bends.append({"line": (mp(b.line[0]), mp(b.line[1])),
                      "tan": [(mp(a), mp(c)) for a, c in b.tan_lines],
                      "angle": b.angle_deg, "r": b.r_inner, "dir": b.direction, "method": b.method, "ba": b.ba})
    return prims, bends


def place_loops(p: Placed):
    """Closed contours (outer/hole) of a placed item in sheet coordinates, for filled previews."""
    it = p.item
    pc = it.piece
    xmin, ymin, xmax, ymax = pc.bbox
    out = []
    for lp in pc.loops:
        pts = lp["pts"]
        if it.flip:
            xs = (p.x + xmax) - pts[:, 0]
        else:
            xs = pts[:, 0] + (p.x - xmin)
        ys = pts[:, 1] + (p.y - ymin)
        out.append({"role": lp["role"], "pts": [[round(float(a), 3), round(float(b), 3)] for a, b in zip(xs, ys)]})
    return out
