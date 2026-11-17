"""DXF writer (R2000 / AC1015 - same dialect as the SolidWorks drawings the shop already uses)."""
from __future__ import annotations

import io
import math

import ezdxf
from ezdxf.enums import TextEntityAlignment

from .layout import Sheet, place_geometry

LAYER_CUT = "CUT"
LAYER_BEND = "BEND"
LAYER_LABEL = "LABEL"


def build_dxf(sheet: Sheet, bend_lines: bool = True, labels: bool = False, bend_tangents: bool = False):
    doc = ezdxf.new("R2000", setup=True)
    doc.units = ezdxf.units.MM
    doc.layers.add(LAYER_CUT, color=7)
    doc.layers.add(LAYER_BEND, color=1, linetype="DASHED")
    doc.layers.add(LAYER_LABEL, color=3)
    msp = doc.modelspace()
    for pl in sheet.placed:
        prims, bends = place_geometry(pl)
        for p in prims:
            t = p["t"]
            at = {"layer": LAYER_CUT}
            if t == "line":
                msp.add_line(p["a"], p["b"], dxfattribs=at)
            elif t == "arc":
                a0, a1 = p["a0"] % 360.0, p["a1"] % 360.0
                msp.add_arc(p["c"], p["r"], a0, a1, dxfattribs=at)
            elif t == "circle":
                msp.add_circle(p["c"], p["r"], dxfattribs=at)
            else:
                pts = p["pts"]
                if len(pts) >= 2:
                    msp.add_lwpolyline(pts, dxfattribs=at)
        if bend_lines:
            for b in bends:
                msp.add_line(b["line"][0], b["line"][1], dxfattribs={"layer": LAYER_BEND})
                if bend_tangents:
                    for a, c in b["tan"]:
                        msp.add_line(a, c, dxfattribs={"layer": LAYER_BEND})
        if labels:
            txt = pl.item.name + (f" #{pl.item.copy}" if pl.item.copy > 1 else "")
            msp.add_text(txt, height=5.0, dxfattribs={"layer": LAYER_LABEL}).set_placement(
                (pl.x + 2, pl.y + pl.h / 2), align=TextEntityAlignment.LEFT)
    return doc


def dxf_bytes(doc) -> bytes:
    buf = io.StringIO()
    doc.write(buf)
    return buf.getvalue().encode("utf-8")
