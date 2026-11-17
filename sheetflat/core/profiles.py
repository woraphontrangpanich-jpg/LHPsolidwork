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
    "table": DEFAULT_TABLE,
}

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
        }

    def _load(self):
        if self.path.exists():
            try:
                d = json.loads(self.path.read_text(encoding="utf-8"))
                base = self._fresh()
                base.update(d)
                if not any(p["id"] == "factory" for p in base["profiles"]):
                    base["profiles"].insert(0, copy.deepcopy(DEFAULT_PROFILE))
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
    rows = []
    for r in p.get("table", []):
        rows.append({"gauge": str(r.get("gauge", "")), "thickness": float(r["thickness"]), "bd": float(r["bd"])})
    rows.sort(key=lambda r: r["thickness"])
    p["table"] = rows


def match_row(profile: dict, thickness: float):
    """Nearest table row within the profile's match tolerance, else None."""
    best, bd = None, 1e9
    for r in profile["table"]:
        d = abs(r["thickness"] - thickness)
        if d < bd:
            best, bd = r, d
    if best is not None and bd <= profile.get("match_tol", 0.06):
        return best
    return None


def material_code(material: dict, row: dict | None, thickness: float) -> str:
    """e.g. ZN#22 / ST#3mm / ZN#4mm (when the thickness is not in the table)."""
    if row is not None and row.get("gauge"):
        return f"{material['prefix']}{row['gauge']}"
    return f"{material['prefix']}#{thickness:g}mm"


def bend_model(profile: dict, thickness: float, k_override: float | None = None) -> BendModel:
    row = match_row(profile, thickness)
    t = row["thickness"] if row else thickness
    return BendModel(
        thickness=t,
        k_factor=k_override if k_override is not None else profile["k_factor"],
        bd90=row["bd"] if row else None,
        angle_tol_deg=profile.get("angle_tol", 1.0),
        table_max_ratio=profile.get("table_max_ratio", 3.0),
        jog_use_k=profile.get("jog_use_k", True),
    )
