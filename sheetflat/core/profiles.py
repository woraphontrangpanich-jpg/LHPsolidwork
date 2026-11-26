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
import shutil
import threading
from pathlib import Path

from .bend import BendModel
from .bend_xlsx import read_materials, read_profiles, write_profiles

SHIPPED_BEND_XLSX = Path(__file__).resolve().parent.parent / "bend_tables.xlsx"   # the shop standard, in git

DEFAULT_MATERIALS = [
    {"id": "ZN", "name": "เหล็กซิงค์ (ZN)", "prefix": "ZN"},
    {"id": "SUSHairline", "name": "สแตนเลส Hairline", "prefix": "SUSHairline"},
    {"id": "SUS", "name": "สแตนเลส", "prefix": "SUS"},
    {"id": "AL", "name": "อลูมิเนียม", "prefix": "AL"},
    {"id": "ALA", "name": "อลูมิเนียมชุบ", "prefix": "ALA"},
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
    "angle_tol": 0.0,          # deg: |angle-90| <= this -> right angle (the table is for 90 deg only)
    "table_max_r": 0.2,        # mm: the table is used for bends with inner radius up to this (shop tooling R 0.2)
    "jog_use_k": True,         # opposite bends with a very short flat between them (jog) -> K-factor
    "match_tol": 0.06,         # mm: measured thickness within this of a table row -> that row
    "simplify_bends": True,    # curved cut edges inside bend zones -> straight lines (SolidWorks default)
    "table": DEFAULT_TABLE,
}

# Price list (the "ราคาเหล็ก" sheet of the shop's สูตรคิดราคา workbook), one row per steel and
# bend-table thickness, keyed like the DXF files (ZN#22, ZN#3mm, SUS#14 ...).
#   kg_plate  weight of one 1220 x 2440 sheet AT THIS ROW'S THICKNESS (ZN 0.7 mm = 16.8 kg)
#   price_kg  baht per kg
# Rows are added automatically for every steel x thickness in the bend table (and in the job).
STEEL_DEFAULTS = {          # first values for a new row: (price/kg below 3 mm, from 3 mm, kg of 1220x2440 sheet per mm)
    "ZN": (35, 45, 24), "AL": (150, 150, 8), "SUS": (120, 120, 23),
    "SUSHairline": (120, 120, 23), "ALA": (150, 150, 8),     # same as SUS / AL until the shop types its own price
}
EXTRA_UNITS = {             # how an extra cost is counted
    "piece": "ชิ้น", "bend": "จุดพับ", "hole": "รู", "kg": "กก.", "m2": "ตร.ม.", "job": "งาน",
}
DEFAULT_PRICING = {
    "sheets": [
        {"w": 1220, "l": 2440, "on": True},
        {"w": 1220, "l": 1628, "on": True},
        {"w": 1220, "l": 1440, "on": False},
        {"w": 1219, "l": 1550, "on": False},
    ],
    "margin": 20.0,              # mm added to every part for clamping and spacing ("เผื่อที่ clamp & วางแบบ")
    "prices": [],                # filled from the bend table by fill_price_rows()
    "paint_per_m2": 0.0,         # ค่าสี (บาท / ตร.ม.): default is steel price only, the user adds paint / plating / labour
    "no_paint": ["SUS", "SUSHairline"],         # steels not painted by default (each part can be ticked)
    "plating_per_kg": 0.0,       # ค่าชุบ
    "markup_pct": 0.0,
    "extras": [],                # other costs are added by the user (e.g. ค่าพับ 5 บาท / จุดพับ, ค่าติ๊ก 5 บาท / ชิ้น)
}


def _steel_default(material: str, t: float):
    lo, hi, kg = STEEL_DEFAULTS.get(material, (35, 35, 24))
    return (hi if t >= 3 - 1e-9 else lo), kg


def validate_pricing(p) -> dict:
    base = copy.deepcopy(DEFAULT_PRICING)
    if isinstance(p, dict):
        base.update({k: p[k] for k in base if k in p})
        if "extras" not in p and ("bend_price" in p or "labor_per_piece" in p):     # older format
            base["extras"] = [{"name": "ค่าพับ", "rate": float(p.get("bend_price") or 0), "unit": "bend", "on": True},
                              {"name": "ค่าแรงอื่น", "rate": float(p.get("labor_per_piece") or 0), "unit": "piece", "on": True}]
    sheets = []
    for s in base["sheets"]:
        w, l = float(s.get("w") or 0), float(s.get("l") or 0)
        if w > 0 and l > 0:
            sheets.append({"w": min(w, l), "l": max(w, l), "on": bool(s.get("on", True))})
    if not sheets:
        raise ValueError("ต้องมีขนาดแผ่นเหล็กอย่างน้อย 1 ขนาด")
    base["sheets"] = sheets
    prices, seen = [], set()
    for r in base["prices"]:
        m = str(r.get("material") or "").strip()
        t = float(r.get("thickness") or 0)
        if not m or t <= 0:
            continue                                   # also drops rows of the older t_min/t_max format
        key = (m, round(t, 3))
        if key in seen:
            continue
        seen.add(key)
        prices.append({"material": m, "gauge": str(r.get("gauge") or "").strip(), "thickness": t,
                       "price_kg": max(0.0, float(r.get("price_kg") or 0)),
                       "kg_plate": round(max(0.0, float(r.get("kg_plate") or
                                                        (float(r["kg_sheet"]) * t if r.get("kg_sheet") else _steel_default(m, t)[1] * t))), 4)})
    base["prices"] = prices
    for k in ("margin", "paint_per_m2", "plating_per_kg", "markup_pct"):
        base[k] = max(0.0, float(base[k] or 0))
    base["no_paint"] = [str(x).strip() for x in base.get("no_paint") or [] if str(x).strip()]
    extras = []
    for e in base.get("extras") or []:
        name = str(e.get("name") or "").strip()[:40]
        if not name:
            continue
        unit = e.get("unit") if e.get("unit") in EXTRA_UNITS else "piece"
        extras.append({"name": name, "rate": max(0.0, float(e.get("rate") or 0)), "unit": unit, "on": bool(e.get("on", True))})
    base["extras"] = extras
    return base


def fill_price_rows(pricing: dict, profile: dict, materials: list, more: list | None = None) -> bool:
    """Make sure there is a price row for every steel x bend-table thickness (and every (steel, t) in
    ``more``). New rows copy the nearest row of the same steel, else the steel's default. True if rows were added."""
    want = []
    for m in materials:
        for r in profile.get("table", []):
            if r.get("material") and r["material"] != m["id"]:
                continue
            want.append((m["id"], r["thickness"], r.get("gauge", "")))
    for mid, t in more or []:
        row = match_row(profile, t, mid)
        want.append((mid, row["thickness"] if row else t, row["gauge"] if row else ""))
    have = {(r["material"], round(r["thickness"], 3)) for r in pricing["prices"]}
    added = False
    for mid, t, gauge in want:
        if (mid, round(t, 3)) in have:
            continue
        same = [r for r in pricing["prices"] if r["material"] == mid]
        if same:
            near = min(same, key=lambda r: abs(r["thickness"] - t))
            price, kg = near["price_kg"], near["kg_plate"] / near["thickness"]      # same steel, scaled to t
            if t >= 3 - 1e-9 > near["thickness"] and mid in STEEL_DEFAULTS:
                price = _steel_default(mid, t)[0]
        else:
            price, kg = _steel_default(mid, t)
        pricing["prices"].append({"material": mid, "gauge": gauge, "thickness": t, "price_kg": price,
                                  "kg_plate": round(kg * t, 4)})
        have.add((mid, round(t, 3)))
        added = True
    order = {m["id"]: i for i, m in enumerate(materials)}
    pricing["prices"].sort(key=lambda r: (order.get(r["material"], 99), r["material"], r["thickness"]))
    return added


# size hints that a part is a purchased item / tube and not a laser-cut blank
DEFAULT_EXCLUDE_KEYWORDS = [
    "bolt", "screw", "nut", "washer", "rivet", "pin", "pipe", "tube", "bearing", "spring",
    "สกรู", "น็อต", "แหวน", "ท่อ", "ลูกปืน", "สปริง", "นอต", "รีเวท",
]


class ProfileStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.bend_path = self.path.with_name("bend_tables.xlsx")     # one sheet = one bend table (profile)
        self.bend_error = ""                                          # why the workbook could not be read, if so
        self._bend_stamp = None
        self._lock = threading.Lock()
        fresh_install = not self.path.exists()
        self.data = self._load()
        self._sync_workbook(fresh_install)

    def _fresh(self):
        return {
            "version": 2,
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
                if int(d.get("version", 1)) < 2:
                    # v2 defaults: 90 deg means exactly 90 (+-0), table only for R in <= 0.2 mm.
                    # Settings still at the old defaults move to the new ones; values the user changed stay.
                    for p in base["profiles"]:
                        if float(p.get("angle_tol", 1.0)) == 1.0:
                            p["angle_tol"] = 0.0
                        if "table_max_r" not in p:
                            p["table_max_r"] = 0.2
                        p.pop("table_max_ratio", None)
                    base["version"] = 2
                base["pricing"] = validate_pricing(base.get("pricing"))
                return base
            except Exception:
                pass
        return self._fresh()

    def save(self):
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---- the Excel workbook with the bend tables is the source of the profiles
    def _stamp(self):
        try:
            st = self.bend_path.stat()
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return None

    def _sync_workbook(self, fresh_install=False):
        """Startup: read the workbook. Missing: a fresh install starts from the workbook shipped with the
        program (sheetflat/bend_tables.xlsx, the shop standard); an older install builds it from its profiles."""
        if not self.bend_path.exists() and fresh_install and SHIPPED_BEND_XLSX.exists():
            try:
                self.bend_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(SHIPPED_BEND_XLSX, self.bend_path)
            except OSError:
                pass
        if self.bend_path.exists():
            self._read_workbook()
        else:
            try:
                write_profiles(self.bend_path, self.data["profiles"], materials=self.data["materials"])
            except OSError as ex:
                self.bend_error = f"สร้างไฟล์ค่ายืด Excel ไม่ได้: {ex}"
            self._bend_stamp = self._stamp()

    def _read_workbook(self) -> bool:
        stamp = self._stamp()
        try:
            profs = read_profiles(self.bend_path)
            for p in profs:
                _validate_profile(p)
            mats = read_materials(self.bend_path)
            mats = _validate_materials(mats) if mats else None
        except (ValueError, KeyError, OSError) as ex:
            self.bend_error = str(ex)
            self._bend_stamp = stamp               # do not complain again until the file changes
            return False
        self.bend_error = ""
        self._bend_stamp = stamp
        with self._lock:
            self.data["profiles"] = profs
            if mats:
                self.data["materials"] = mats              # the steel types listed in the workbook
            ids = [p["id"] for p in profs]
            if self.data["active"] not in ids:
                self.data["active"] = ids[0]
        return True

    def refresh(self) -> bool:
        """Re-read the workbook if it changed on disk (edited in Excel, or a colleague's copy dropped in).
        True when the profiles changed. A broken workbook keeps the last good tables and sets bend_error."""
        stamp = self._stamp()
        if stamp is None or stamp == self._bend_stamp:
            return False
        before = json.dumps([self.data["profiles"], self.data["materials"]], sort_keys=True)
        ok = self._read_workbook()
        if ok:
            self.save()
        return ok and json.dumps([self.data["profiles"], self.data["materials"]], sort_keys=True) != before

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
            write_profiles(self.bend_path, base["profiles"], materials=base["materials"])      # PermissionError if it is open in Excel
            self._bend_stamp = self._stamp()
            self.bend_error = ""
            self.data = base
        self.save()

    def get(self, pid: str | None = None) -> dict:
        self.refresh()
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
    p["angle_tol"] = max(0.0, float(p.get("angle_tol", 0.0)))
    p["table_max_r"] = max(0.0, float(p.get("table_max_r", 0.2)))
    p.pop("table_max_ratio", None)
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
    """e.g. ZN#22 / SUS#3mm / ZN#4mm (when the thickness is not in the table)."""
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
        angle_tol_deg=profile.get("angle_tol", 0.0),
        table_max_r=profile.get("table_max_r", 0.2),
        jog_use_k=profile.get("jog_use_k", True),
        simplify_bends=profile.get("simplify_bends", True),
    )
