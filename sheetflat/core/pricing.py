"""Price a job the way the shop's สูตรคิดราคา workbook does, with a better sheet choice.

Per flat piece (x, y = flat size, t = thickness, qty), same formulas as the workbook columns:

    G  sheet price  = ROUNDUP(kg_plate/(1220*2440) * W * L * price_kg)          (price row of this steel + thickness;
                                                                                kg_plate = weight of a 1220x2440 sheet
                                                                                at that thickness)
    T  arrangement 1 = floor(W / (x + m)) * floor(L / (y + m))
    W  arrangement 2 = floor(W / (y + m)) * floor(L / (x + m))                  (part turned 90 deg)
    H  metal        = ROUNDUP(G / MAX(T, W))                                    per piece
    I  paint        = ROUNDUP(x * y * paint_per_mm2)                            per piece, not for unpainted steel
    L  weight       = kg_plate/(1220*2440) * x * y * qty
       sheet use    = pieces on the sheet * x * y / (W * L)                     (rest = offcut)
    J  plating      = L * plating_per_kg                                        shown; in the total only when switched on
       extras       = user cost items per piece / bend / hole / kg / m2         (per job: added once)
    M  total        = (H + I + extras) * qty  [+ J]

m = clamp/spacing margin. The workbook picks the sheet with a fixed rule; here every enabled sheet size
and both arrangements are tried and the lowest metal cost per piece wins (ties -> the bigger sheet).
"""
from __future__ import annotations

import math

REF_AREA = 1220.0 * 2440.0       # the workbook's weight is given per 1220 x 2440 sheet


def roundup(v: float) -> int:
    """Excel ROUNDUP(v, 0) for the non-negative numbers used here."""
    return int(math.ceil(round(v, 9)))


def r2(v: float) -> float:
    """Round to 2 decimals, half away from zero (like Excel ROUND)."""
    return math.floor(abs(v) * 100 + 0.5 + 1e-9) / 100 * (1 if v >= 0 else -1)


def price_row(pricing: dict, material: str, t: float, tol: float = 0.06) -> dict | None:
    """Price row of this steel at this thickness (nearest within tol)."""
    best = None
    for r in pricing["prices"]:
        if r["material"] != material:
            continue
        d = abs(r["thickness"] - t)
        if d <= tol and (best is None or d < best[0]):
            best = (d, r)
    return best[1] if best else None


def kg_per_mm2(row: dict) -> float:
    """kg per mm2 of sheet at the row's own thickness."""
    return row["kg_plate"] / REF_AREA


def sheet_price(sheet: dict, row: dict, t: float = 0.0) -> float:
    return roundup(sheet["w"] * sheet["l"] * kg_per_mm2(row) * row["price_kg"])


def fits(sheet: dict, x: float, y: float, margin: float) -> dict:
    W, L = sheet["w"], sheet["l"]
    a = (math.floor(W / (x + margin)), math.floor(L / (y + margin)))
    b = (math.floor(W / (y + margin)), math.floor(L / (x + margin)))
    na, nb = a[0] * a[1], b[0] * b[1]
    if na >= nb:
        return {"count": na, "arr": 1, "nx": a[0], "ny": a[1], "count1": na, "count2": nb}
    return {"count": nb, "arr": 2, "nx": b[0], "ny": b[1], "count1": na, "count2": nb}


def extra_amount(e: dict, bends: int, holes: int, weight1: float, x: float, y: float) -> float:
    """Cost of one extra item for ONE piece (per-job items are not per piece -> 0)."""
    if not e["on"]:
        return 0.0
    n = {"piece": 1, "bend": bends, "hole": holes, "kg": weight1, "m2": x * y * 1e-6}.get(e["unit"], 0)
    return e["rate"] * n


def price_piece(pricing: dict, material: str, t: float, x: float, y: float, qty: int, bends: int,
                holes: int = 0) -> dict:
    """Price one flat piece. Returns every number the price sheet shows."""
    x, y = float(x), float(y)
    row = price_row(pricing, material, t)
    out = {"material": material, "t": t, "x": round(x, 2), "y": round(y, 2), "qty": qty, "bends": bends,
           "holes": holes, "options": [], "sheet": None, "error": "", "extras": []}
    if row is None:
        out["error"] = f"ไม่มีราคาเหล็ก {material} หนา {t:g} มม. ในตารางราคา"
        return _zero(out)
    out["price_kg"], out["kg_plate"], out["gauge"] = row["price_kg"], row["kg_plate"], row.get("gauge", "")
    m = pricing["margin"]
    best = None
    for s in pricing["sheets"]:
        f = fits(s, x, y, m)
        sp = sheet_price(s, row, t)
        opt = {"w": s["w"], "l": s["l"], "on": s["on"], "sheet_price": round(sp, 2), **f,
               "metal": roundup(sp / f["count"]) if f["count"] else None,
               "use": round(f["count"] * x * y / (s["w"] * s["l"]), 4)}
        out["options"].append(opt)
        if s["on"] and f["count"]:
            k = (opt["metal"], -(s["w"] * s["l"]))
            if best is None or k < best[0]:
                best = (k, opt)
    if best is None:
        out["error"] = f"ชิ้น {x:.0f} × {y:.0f} มม. ใหญ่กว่าแผ่นเหล็กทุกขนาดที่เปิดใช้"
        return _zero(out)
    b = best[1]
    out["sheet"] = {k: b[k] for k in ("w", "l", "sheet_price", "count", "arr", "nx", "ny", "count1", "count2", "use")}
    out["sheets_used"] = round(qty / b["count"], 4)                # fraction of sheets this part needs
    weight1 = kg_per_mm2(row) * x * y
    out["metal"] = b["metal"]
    out["paint"] = 0 if material in pricing["no_paint"] else roundup(x * y * pricing["paint_per_m2"] * 1e-6)
    out["extras"] = [round(extra_amount(e, bends, holes, weight1, x, y), 4) for e in pricing["extras"]]
    out["extra"] = round(sum(out["extras"]), 4)
    out["weight"] = round(weight1 * qty, 4)
    out["plating"] = round(weight1 * qty * pricing["plating_per_kg"], 4)
    unit = out["metal"] + out["paint"] + out["extra"]
    out["unit"] = round(unit, 4)
    out["total"] = round(unit * qty + (out["plating"] if pricing["include_plating"] else 0), 4)
    return out


def _zero(out):
    out.update(metal=0, paint=0, extra=0, plating=0, weight=0, unit=0, total=0)
    return out


def price_project(pr, pricing: dict) -> dict:
    """Price every part that goes into a DXF, grouped like the DXF files."""
    byid = {r.id: r for r in pr.parts}
    rows, groups = [], []
    for g in pr.groups:
        grows = []
        for pid in g.part_ids:
            rec = byid[pid]
            for k, pc in enumerate(rec.pieces):
                w, h = pc.bbox[2] - pc.bbox[0], pc.bbox[3] - pc.bbox[1]
                holes = sum(1 for lp in pc.loops if lp["role"] == "hole")
                r = price_piece(pricing, g.material_id, g.thickness, max(w, h), min(w, h), rec.qty, len(pc.bends), holes)
                r.update(part_id=rec.id, name=rec.name if len(rec.pieces) == 1 else f"{rec.name}-{k + 1}",
                         group=g.id, code=g.code, group_name=g.name)
                grows.append(r)
        sheets_needed, size_of = {}, {}
        for r in grows:
            if r["sheet"]:
                key = f'{r["sheet"]["w"]:g}x{r["sheet"]["l"]:g}'
                sheets_needed[key] = sheets_needed.get(key, 0.0) + r["qty"] / r["sheet"]["count"]
                size_of[key] = r["sheet"]["w"] * r["sheet"]["l"]
        sheet_count = {k: math.ceil(v - 1e-9) for k, v in sheets_needed.items()}
        sheet_area = sum(sheet_count[k] * size_of[k] for k in sheet_count)
        parts_area = sum(r["qty"] * r["x"] * r["y"] for r in grows if r["sheet"])

        def tot(k, per_piece=True, rows=grows):
            return round(sum(r[k] * (r["qty"] if per_piece else 1) for r in rows), 4)
        groups.append({"id": g.id, "code": g.code, "name": g.name, "material_id": g.material_id, "thickness": g.thickness,
                       "pieces": sum(r["qty"] for r in grows), "weight": tot("weight", False),
                       "metal": tot("metal"), "paint": tot("paint"), "extra": tot("extra"),
                       "plating": tot("plating", False), "total": tot("total", False),
                       "sheets": sheet_count, "sheet_area": round(sheet_area, 1), "parts_area": round(parts_area, 1),
                       "use": round(parts_area / sheet_area, 4) if sheet_area else 0,
                       "errors": sum(1 for r in grows if r["error"])})
        rows += grows
    job_extras = [{"name": e["name"], "amount": e["rate"]} for e in pricing["extras"] if e["on"] and e["unit"] == "job"]
    parts_total = sum(g["total"] for g in groups)
    subtotal = r2(parts_total + sum(e["amount"] for e in job_extras))
    markup = r2(subtotal * pricing["markup_pct"] / 100.0)
    return {"rows": rows, "groups": groups, "job_extras": job_extras, "subtotal": subtotal, "markup": markup,
            "total": r2(subtotal + markup), "weight": round(sum(g["weight"] for g in groups), 3),
            "errors": [r["error"] for r in rows if r["error"]]}
