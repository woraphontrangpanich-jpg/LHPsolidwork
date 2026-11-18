"""Bend profiles ("modes"): bend-deduction table + K-factor, persisted as JSON.

The default profile is the shop table photographed in ``ค่ายืด.jpg``:

    gauge  thickness  bend deduction (90 deg)
    #22    0.7        1.25
    #20    0.9        1.50
    #19    1.0        1.75
    #18    1.2        2.00
    #17    1.4        2.25
    #16    1.6        2.50
    #14    2.0        3.20
    #3mm   3.0        4.80
"""
from __future__ import annotations

import copy
import json
import threading
from pathlib import Path

from .bend import BendModel

DEFAULT_MATERIALS = [
    {"id": "ZN", "name": "เหล็กซิงค์ (ZN)", "prefix": "ZN"},
    {"id": "ST", "name": "เหล็กขาว (ST)", "prefix": "ST"},
]

DEFAULT_TABLE = [
    {"gauge": "#22", "thickness": 0.7, "bd": 1.25},
    {"gauge": "#20", "thickness": 0.9, "bd": 1.5},
    {"gauge": "#19", "thickness": 1.0, "bd": 1.75},
    {"gauge": "#18", "thickness": 1.2, "bd": 2.0},
    {"gauge": "#17", "thickness": 1.4, "bd": 2.25},
    {"gauge": "#16", "thickness": 1.6, "bd": 2.5},
    {"gauge": "#14", "thickness": 2.0, "bd": 3.2},
    {"gauge": "#3mm", "thickness": 3.0, "bd": 4.8},
]

DEFAULT_PROFILE = {
    "id": "factory",
    "name": "มาตรฐานโรงงาน (ตามตารางค่ายืด)",
    "k_factor": 0.5,
    "angle_tol": 1.0,          # deg: |angle-90| <= this -> right angle
    "table_max_ratio": 3.0,    # r_in / t above this -> curved bend -> K-factor
    "jog_use_k": True,         # opposite bends with a very short flat between them (jog) -> K-factor
    "match_tol": 0.06,         # mm: measured thickness within this of a table row -> that row
    "simplify_bends": True,    # curved cut edges inside bend zones -> straight lines (SolidWorks default)
    "table": DEFAULT_TABLE,
}

# Price list ("ราคาเหล็ก" sheet of the shop's สูตรคิดราคา workbook).
#   kg_m2_mm  weight of 1 m2 at 1 mm   (the workbook uses 24 kg per 1220 x 2440 x 1 mm sheet)
#   price_kg  baht per kg; a row can be limited to a thickness range (e.g. ZN 3 mm and up = ZN#3mm)
_KG24 = round(24 / (1.22 * 2.44), 6)
_KG8 = round(8 / (1.22 * 2.44), 6)
_KG23 = round(23 / (1.22 * 2.44), 6)
DEFAULT_PRICING = {
    "sheets": [
        {"w": 1220, "l": 2440, "on": True},
        {"w": 1220, "l": 1628, "on": True},
        {"w": 1220, "l": 1440, "on": False},
        {"w": 1219, "l": 1550, "on": False},
    ],
    "margin": 20.0,              # mm added to every part for clamping and spacing ("เผื่อที่ clamp & วางแบบ")
    "prices": [
        {"material": "ZN", "t_min": 0, "t_max": 2.99, "price_kg": 35, "kg_m2_mm": _KG24},
        {"material": "ZN", "t_min": 3, "t_max": 0, "price_kg": 45, "kg_m2_mm": _KG24},
        {"material": "ST", "t_min": 0, "t_max": 0, "price_kg": 45, "kg_m2_mm": _KG24},
        {"material": "AL", "t_min": 0, "t_max": 0, "price_kg": 150, "kg_m2_mm": _KG8},
        {"material": "SUS", "t_min": 0, "t_max": 0, "price_kg": 120, "kg_m2_mm": _KG23},
    ],
    "paint_per_m2": 200.0,       # ค่าสี 0.0002 baht / mm2
    "no_paint": ["SUS"],         # materials that are not painted
    "plating_per_kg": 20.0,      # ค่าชุบ
    "include_plating": False,    # the workbook shows plating but does not add it to the total
    "bend_price": 5.0,           # ค่าพับ per bend
    "labor_per_piece": 0.0,      # other labour per piece (ติ๊ก, deburring, ...)
    "markup_pct": 0.0,
}


def validate_pricing(p) -> dict:
    base = copy.deepcopy(DEFAULT_PRICING)
    if isinstance(p, dict):
        base.update({k: p[k] for k in base if k in p})
    sheets = []
    for s in base["sheets"]:
        w, l = float(s.get("w") or 0), float(s.get("l") or 0)
        if w > 0 and l > 0:
            sheets.append({"w": min(w, l), "l": max(w, l), "on": bool(s.get("on", True))})
    if not sheets:
        raise ValueError("ต้องมีขนาดแผ่นเหล็กอย่างน้อย 1 ขนาด")
    base["sheets"] = sheets
    prices = []
    for r in base["prices"]:
        m = str(r.get("material") or "").strip()
        if not m:
            continue
        prices.append({"material": m, "t_min": float(r.get("t_min") or 0), "t_max": float(r.get("t_max") or 0),
                       "price_kg": float(r.get("price_kg") or 0), "kg_m2_mm": float(r.get("kg_m2_mm") or _KG24)})
    base["prices"] = prices
    for k in ("margin", "paint_per_m2", "plating_per_kg", "bend_price", "labor_per_piece", "markup_pct"):
        base[k] = max(0.0, float(base[k] or 0))
    base["include_plating"] = bool(base["include_plating"])
    base["no_paint"] = [str(x).strip() for x in base.get("no_paint") or [] if str(x).strip()]
    return base


# size hints that a part is a purchased item / tube and not a laser-cut blank
DEFAULT_EXCLUDE_KEYWORDS = [
    "bolt", "screw", "nut", "washer", "rivet", "pin", "pipe", "tube", "bearing", "spring",
    "สกรู", "น็อต", "แหวน", "ท่อ", "ลูกปืน", "สปริง", "นอต", "รีเวท",
]


class ProfileStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self.data = self._load()

    def _fresh(self):
        return {
            "version": 1,
            "active": "factory",
            "materials": copy.deepcopy(DEFAULT_MATERIALS),
            "profiles": [copy.deepcopy(DEFAULT_PROFILE)],
            "exclude_keywords": list(DEFAULT_EXCLUDE_KEYWORDS),
            "density": 7.85,   # g/cm3
            "pricing": copy.deepcopy(DEFAULT_PRICING),
        }

    def _load(self):
        if self.path.exists():
            try:
                d = json.loads(self.path.read_text(encoding="utf-8"))
                base = self._fresh()
                base.update(d)
                if not any(p["id"] == "factory" for p in base["profiles"]):
                    base["profiles"].insert(0, copy.deepcopy(DEFAULT_PROFILE))
                base["pricing"] = validate_pricing(base.get("pricing"))
                return base
            except Exception:
                pass
        return self._fresh()

    def save(self):
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")

    def replace(self, data: dict):
        with self._lock:
            base = self._fresh()
            base.update({k: data[k] for k in base if k in data})
            for p in base["profiles"]:
                _validate_profile(p)
            base["materials"] = _validate_materials(base["materials"])
            base["pricing"] = validate_pricing(base.get("pricing"))
            if not base["profiles"]:
                base["profiles"] = [copy.deepcopy(DEFAULT_PROFILE)]
            ids = [p["id"] for p in base["profiles"]]
            if base["active"] not in ids:
                base["active"] = ids[0]
            self.data = base
        self.save()

    def get(self, pid: str | None = None) -> dict:
        pid = pid or self.data["active"]
        for p in self.data["profiles"]:
            if p["id"] == pid:
                return p
        return self.data["profiles"][0]

    def material(self, mid: str) -> dict:
        for m in self.data["materials"]:
            if m["id"] == mid:
                return m
        return self.data["materials"][0]


def _validate_profile(p: dict):
    p["k_factor"] = float(p.get("k_factor", 0.5))
    if not 0.0 < p["k_factor"] < 1.0:
        raise ValueError("K-factor ต้องอยู่ระหว่าง 0 ถึง 1")
    p["angle_tol"] = float(p.get("angle_tol", 1.0))
    p["table_max_ratio"] = float(p.get("table_max_ratio", 3.0))
    p["jog_use_k"] = bool(p.get("jog_use_k", True))
    p["match_tol"] = float(p.get("match_tol", 0.06))
    p["simplify_bends"] = bool(p.get("simplify_bends", True))
    rows = []
    for r in p.get("table", []):
        t, bd = float(r.get("thickness") or 0), float(r.get("bd") or 0)
        if t <= 0 or bd <= 0:
            continue                       # empty rows the user added but did not fill
        rows.append({"gauge": str(r.get("gauge", "")).strip(), "thickness": t, "bd": bd,
                     "material": str(r.get("material") or "").strip()})
    rows.sort(key=lambda r: (r["thickness"], r["material"]))
    p["table"] = rows


def _validate_materials(mats) -> list:
    out, seen = [], set()
    for m in mats or []:
        mid = str(m.get("id", "")).strip()
        if not mid:
            continue
        if mid in seen:
            raise ValueError(f"รหัสชนิดเหล็ก {mid} ซ้ำกัน")
        seen.add(mid)
        out.append({"id": mid, "name": str(m.get("name") or mid).strip(), "prefix": str(m.get("prefix") or mid).strip()})
    if not out:
        raise ValueError("ต้องมีชนิดเหล็กอย่างน้อย 1 ชนิด")
    return out


def match_row(profile: dict, thickness: float, material_id: str | None = None):
    """Nearest table row within the profile's match tolerance, else None.

    A row with a material only applies to that steel; at the same thickness it wins over
    a row for every steel (material empty)."""
    tol = profile.get("match_tol", 0.06)
    best, key = None, None
    for r in profile["table"]:
        rm = r.get("material") or ""
        if rm and rm != material_id:
            continue
        d = abs(r["thickness"] - thickness)
        if d > tol:
            continue
        k = (round(d, 6), 0 if rm else 1)
        if key is None or k < key:
            best, key = r, k
    return best


def material_code(material: dict, row: dict | None, thickness: float) -> str:
    """e.g. ZN#22 / ST#3mm / ZN#4mm (when the thickness is not in the table)."""
    if row is not None and row.get("gauge"):
        return f"{material['prefix']}{row['gauge']}"
    return f"{material['prefix']}#{thickness:g}mm"


def bend_model(profile: dict, thickness: float, k_override: float | None = None,
               material_id: str | None = None) -> BendModel:
    row = match_row(profile, thickness, material_id)
    t = row["thickness"] if row else thickness
    return BendModel(
        thickness=t,
        k_factor=k_override if k_override is not None else profile["k_factor"],
        bd90=row["bd"] if row else None,
        angle_tol_deg=profile.get("angle_tol", 1.0),
        table_max_ratio=profile.get("table_max_ratio", 3.0),
        jog_use_k=profile.get("jog_use_k", True),
        simplify_bends=profile.get("simplify_bends", True),
    )
