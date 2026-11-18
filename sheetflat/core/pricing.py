"""Price a job the way the shop's สูตรคิดราคา workbook does, with a better sheet choice.

Per flat piece (x, y = flat size, t = thickness, qty):

    sheet price  = ROUNDUP(W * L * kg_m2_mm * price_kg) * t          (W x L sheet, kg per mm2 at 1 mm)
    fit          = floor(W / (x + m)) * floor(L / (y + m))           (arrangement 1)
                   floor(W / (y + m)) * floor(L / (x + m))           (arrangement 2, part turned 90 deg)
    metal        = ROUNDUP(sheet price / best fit)                   per piece
    paint        = ROUNDUP(x * y * paint_per_mm2)                    per piece (not for unpainted steel)
    labour       = bends * bend_price + labor_per_piece              per piece
    plating      = weight * plating_per_kg                           shown; added only when switched on
    total        = (metal + paint + labour [+ plating]) * qty

m = clamp/spacing margin. The workbook picks the sheet with a fixed rule (longest side between 1220 and
1628 -> 1220x1628, else 1220x2440); here every enabled sheet size and both arrangements are tried and the
lowest metal cost per piece wins (ties -> the bigger sheet, which is the one the shop stocks).
"""
from __future__ import annotations

import math


def roundup(v: float) -> int:
    """Excel ROUNDUP(v, 0) for the non-negative numbers used here."""
    return int(math.ceil(round(v, 9)))


def r2(v: float) -> float:
    """Round to 2 decimals, half away from zero (like Excel ROUND)."""
    return math.floor(abs(v) * 100 + 0.5 + 1e-9) / 100 * (1 if v >= 0 else -1)


def price_row(pricing: dict, material: str, t: float) -> dict | None:
    """First price row for this material whose thickness range contains t (0 = no limit)."""
    for r in pricing["prices"]:
        if r["material"] != material:
            continue
        if r["t_min"] and t < r["t_min"] - 1e-9:
            continue
        if r["t_max"] and t > r["t_max"] + 1e-9:
            continue
        return r
    return None


def sheet_price(sheet: dict, row: dict, t: float) -> float:
    per_mm = roundup(sheet["w"] * sheet["l"] * 1e-6 * row["kg_m2_mm"] * row["price_kg"])
    return per_mm * t


def fits(sheet: dict, x: float, y: float, margin: float) -> dict:
    W, L = sheet["w"], sheet["l"]
    a = (math.floor(W / (x + margin)), math.floor(L / (y + margin)))
    b = (math.floor(W / (y + margin)), math.floor(L / (x + margin)))
    na, nb = a[0] * a[1], b[0] * b[1]
    if na >= nb:
        return {"count": na, "arr": 1, "nx": a[0], "ny": a[1], "count1": na, "count2": nb,
                "used": [a[0] * (x + margin), a[1] * (y + margin)]}
    return {"count": nb, "arr": 2, "nx": b[0], "ny": b[1], "count1": na, "count2": nb,
            "used": [b[0] * (y + margin), b[1] * (x + margin)]}


def price_piece(pricing: dict, material: str, t: float, x: float, y: float, qty: int, bends: int) -> dict:
    """Price one flat piece. Returns every number the price sheet shows."""
    x, y = float(x), float(y)
    row = price_row(pricing, material, t)
    out = {"material": material, "t": t, "x": round(x, 2), "y": round(y, 2), "qty": qty, "bends": bends,
           "options": [], "sheet": None, "error": ""}
    if row is None:
        out["error"] = f"ไม่มีราคาเหล็ก {material} หนา {t:g} มม. ในตารางราคา"
        return _zero(out)
    out["price_kg"], out["kg_m2_mm"] = row["price_kg"], row["kg_m2_mm"]
    m = pricing["margin"]
    best = None
    for s in pricing["sheets"]:
        f = fits(s, x, y, m)
        sp = sheet_price(s, row, t)
        opt = {"w": s["w"], "l": s["l"], "on": s["on"], "sheet_price": round(sp, 2), **f,
               "metal": roundup(sp / f["count"]) if f["count"] else None}
        out["options"].append(opt)
        if s["on"] and f["count"]:
            k = (opt["metal"], -(s["w"] * s["l"]))
            if best is None or k < best[0]:
                best = (k, opt)
    if best is None:
        out["error"] = f"ชิ้น {x:.0f} × {y:.0f} มม. ใหญ่กว่าแผ่นเหล็กทุกขนาดที่เปิดใช้"
        return _zero(out)
    b = best[1]
    out["sheet"] = {k: b[k] for k in ("w", "l", "sheet_price", "count", "arr", "nx", "ny", "count1", "count2", "used")}
    paint_rate = pricing["paint_per_m2"] * 1e-6
    weight1 = row["kg_m2_mm"] * 1e-6 * t * x * y
    out["metal"] = b["metal"]
    out["paint"] = 0 if material in pricing["no_paint"] else roundup(x * y * paint_rate)
    out["labor"] = round(bends * pricing["bend_price"] + pricing["labor_per_piece"], 2)
    out["plating"] = round(weight1 * pricing["plating_per_kg"], 2)
    out["weight"] = round(weight1 * qty, 4)
    unit = out["metal"] + out["paint"] + out["labor"] + (out["plating"] if pricing["include_plating"] else 0)
    out["unit"] = round(unit, 2)
    out["total"] = round(unit * qty, 2)
    return out


def _zero(out):
    out.update(metal=0, paint=0, labor=0, plating=0, weight=0, unit=0, total=0)
    return out


def price_project(pr, pricing: dict) -> dict:
    """Price every part that goes into a DXF, grouped like the DXF files."""
    byid = {r.id: r for r in pr.parts}
    rows, groups = [], []
    for g in pr.groups:
        grows = []
        for pid in g.part_ids:
            rec = byid[pid]
            qty = rec.qty
            for k, pc in enumerate(rec.pieces):
                w, h = pc.bbox[2] - pc.bbox[0], pc.bbox[3] - pc.bbox[1]
                x, y = max(w, h), min(w, h)
                r = price_piece(pricing, g.material_id, g.thickness, x, y, qty, len(pc.bends))
                r.update(part_id=rec.id, name=rec.name if len(rec.pieces) == 1 else f"{rec.name}-{k + 1}",
                         group=g.id, code=g.code)
                grows.append(r)
        sheets_needed = {}
        for r in grows:
            if r["sheet"]:
                key = f'{r["sheet"]["w"]:g}x{r["sheet"]["l"]:g}'
                sheets_needed[key] = sheets_needed.get(key, 0.0) + r["qty"] / r["sheet"]["count"]
        tot = lambda k: round(sum(r[k] * (1 if k in ("weight", "total") else r["qty"]) for r in grows), 2)  # noqa: E731
        groups.append({"id": g.id, "code": g.code, "material_id": g.material_id, "thickness": g.thickness,
                       "pieces": sum(r["qty"] for r in grows), "weight": tot("weight"), "metal": tot("metal"),
                       "paint": tot("paint"), "labor": tot("labor"), "plating": tot("plating"), "total": tot("total"),
                       "sheets": {k: math.ceil(v - 1e-9) for k, v in sheets_needed.items()},
                       "errors": sum(1 for r in grows if r["error"])})
        rows += grows
    subtotal = round(sum(g["total"] for g in groups), 2)
    markup = r2(subtotal * pricing["markup_pct"] / 100.0)
    return {"rows": rows, "groups": groups, "subtotal": subtotal, "markup": markup,
            "total": r2(subtotal + markup), "weight": round(sum(g["weight"] for g in groups), 3),
            "errors": [r["error"] for r in rows if r["error"]]}
