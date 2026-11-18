"""Project = one uploaded assembly: parts, analysis, user decisions, flat patterns, groups."""
from __future__ import annotations

import copy
import math
import re
import threading
import time
import uuid
from dataclasses import dataclass, field

import numpy as np

from .analysis import SheetAnalysis, analyze
from .bend import BendModel
from .brep import SolidData, extract_solid
from .layout import Item, Placed, Sheet, pack, place_geometry, place_loops
from .profiles import ProfileStore, bend_model, match_row, material_code, validate_pricing
from .step_reader import PartDef, read_step
from .unfold import FlatPiece, UnfoldError, prim_points, unfold

DEFAULT_SETTINGS = {
    "profile": None,              # profile id (None -> store active)
    "material": "ZN",             # default material for every part
    "copies": "qty",              # qty | one
    "gap": 10.0,
    "max_width": 2500.0,
    "sheet": None,                # [w, l] raw sheet size or None
    "bend_lines": False,          # the shop's own DXF files have cut lines only
    "labels": False,
    "filename": "{job}_{code}_{t}mm",
    "sets": 1,                    # how many assemblies are made: every quantity is multiplied by this
}


def safe_name(s: str) -> str:
    return re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", s).strip(" .") or "job"


def fmt_t(t: float) -> str:
    return f"{t:g}"


@dataclass
class PartRecord:
    id: str
    name: str
    qty: int
    sd: SolidData
    analysis: SheetAnalysis | None
    measured_t: float
    status: str = "sheet"                 # sheet | excluded
    auto_reason: str = ""                 # why excluded by default
    reason_code: str = ""                 # keyword | not_sheet | unfold_failed | ""
    include: bool = True                  # user decision (default follows status)
    material_id: str = "ZN"
    t_override: float | None = None
    flip: bool = False
    category: str = ""                    # user group for parts that are not cut (e.g. "ซื้อสำเร็จรูป")
    qty_asm: int = 0                      # quantity found in the assembly
    qty_override: int | None = None       # quantity typed by the user (per set)
    paint: bool | None = None             # None = automatic (painted unless the steel is in "no paint")
    plate: bool = False                   # plated (ชุบ)
    # computed
    pieces: list[FlatPiece] = field(default_factory=list)
    unfold_error: str = ""
    flat_key: tuple | None = None
    checks: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    bbox3: tuple = ()

    def thickness_used(self, profile) -> float:
        t = self.t_override if self.t_override else self.measured_t
        row = match_row(profile, t, self.material_id)
        return row["thickness"] if row else round(t, 2)


@dataclass
class Group:
    id: str
    material_id: str
    thickness: float
    code: str                             # steel code from the bend table, e.g. ZN#22 (price lookups)
    name: str = ""                        # shown name and DXF file name part; the user can rename it
    key: str = ""                         # "ZN|0.7" - identifies the group across recomputes
    part_ids: list[str] = field(default_factory=list)
    filename: str = ""
    sheets: list[Sheet] = field(default_factory=list)


class Project:
    def __init__(self, store: ProfileStore, job_name: str, source_name: str, settings: dict | None = None):
        self.id = uuid.uuid4().hex[:12]
        self.store = store
        self.job_name = job_name
        self.source_name = source_name
        self.settings = dict(DEFAULT_SETTINGS)
        if settings:
            self.settings.update({k: v for k, v in settings.items() if k in DEFAULT_SETTINGS})
        self.parts: list[PartRecord] = []
        self.groups: list[Group] = []
        self.created = time.time()
        self.lock = threading.RLock()
        self.progress = {"state": "queued", "done": 0, "total": 0, "msg": ""}
        self.error = ""
        self.root_name = ""
        self.source_path = ""                 # STEP file kept for "save job"
        self.pricing = copy.deepcopy(store.data.get("pricing")) or validate_pricing(None)
        self.pricing_meta = {"date": "", "customer": "", "note": ""}
        self.group_names: dict[str, str] = {}   # "ZN|0.7" -> name the user gave the DXF group

    # ------------------------------------------------------------------ profile
    @property
    def profile(self) -> dict:
        return self.store.get(self.settings.get("profile"))

    # ------------------------------------------------------------------ build
    def load_step(self, path: str, state: dict | None = None):
        self.progress.update(state="reading", msg="กำลังอ่านไฟล์ STEP")
        root, defs = read_step(path)
        self.root_name = root
        entries = []
        for pd in defs:
            for i, solid in enumerate(pd.solids):
                nm = pd.name if len(pd.solids) == 1 else f"{pd.name}.{i + 1}"
                entries.append((nm, pd.qty, solid))
        self.progress.update(state="analyzing", total=len(entries), done=0)
        kw = [k.lower() for k in self.store.data.get("exclude_keywords", [])]
        for n, (nm, qty, solid) in enumerate(entries):
            self.progress.update(msg=f"วิเคราะห์ {nm}", done=n)
            self.parts.append(self._analyze_part(f"p{n + 1}", nm, qty, solid, kw))
        self.progress.update(state="unfolding", done=0)
        self.recompute()
        if state:
            self.apply_state(state)
        self.progress.update(state="done", done=len(entries), msg="เสร็จแล้ว")

    def _analyze_part(self, pid, name, qty, solid, kw) -> PartRecord:
        sd = extract_solid(solid)
        rec = PartRecord(id=pid, name=name, qty=qty, sd=sd, analysis=None, measured_t=0.0,
                         material_id=self.settings["material"], qty_asm=qty)
        rec.bbox3 = tuple(float(x) for x in (sd.bbox[1] - sd.bbox[0]))
        try:
            an = analyze(sd)
        except Exception as ex:   # noqa
            an = SheetAnalysis(ok=False, reason=f"วิเคราะห์ไม่สำเร็จ: {ex}")
        rec.analysis = an
        rec.measured_t = round(an.thickness, 3) if an.thickness else 0.0
        low = name.lower()
        hit = next((k for k in kw if k and k in low), None)
        if not an.ok:
            rec.status, rec.reason_code, rec.auto_reason = "excluded", "not_sheet", an.reason
        elif hit:
            rec.status, rec.reason_code = "excluded", "keyword"
            rec.auto_reason = f"ชื่อมีคำว่า “{hit}” - น่าจะเป็นชิ้นส่วนซื้อ/ท่อ ไม่ใช่แผ่นตัด"
        rec.include = rec.status == "sheet"
        return rec

    # ------------------------------------------------------------------ unfolding / checks
    def _ensure_flat(self, rec: PartRecord):
        """(Re)compute the flat pattern when the inputs changed."""
        if rec.status != "sheet" or rec.analysis is None or not rec.analysis.ok:
            return
        prof = self.profile
        t = rec.thickness_used(prof)
        model = bend_model(prof, t, material_id=rec.material_id)
        key = (prof["id"], rec.material_id, t, model.bd90, model.k_factor, model.angle_tol_deg, model.table_max_r, model.jog_use_k,
               model.simplify_bends,
               tuple(tuple((r["thickness"], r["bd"], r.get("material", "")) for r in prof["table"])))
        if rec.flat_key == key and (rec.pieces or rec.unfold_error):
            return
        rec.flat_key = key
        rec.pieces, rec.unfold_error, rec.warnings = [], "", []
        try:
            pieces = unfold(rec.sd, rec.analysis, model)
        except UnfoldError as ex:
            rec.unfold_error = str(ex)
            rec.status, rec.reason_code, rec.auto_reason = "excluded", "unfold_failed", f"คลี่ไม่ได้: {ex}"
            rec.include = False
            return
        except Exception as ex:   # noqa
            rec.unfold_error = f"{type(ex).__name__}: {ex}"
            rec.status, rec.reason_code, rec.auto_reason = "excluded", "unfold_failed", f"คลี่ไม่ได้ ({rec.unfold_error})"
            rec.include = False
            return
        # normalise every piece so its bbox starts at 0,0
        for pc in pieces:
            for w in pc.warnings:
                rec.warnings.append(w)
        rec.pieces = pieces
        # ---- independent geometric check: neutral-surface (K=0.5, no table) area x t must equal the solid volume
        chk_model = BendModel(thickness=t, k_factor=0.5, bd90=None, jog_use_k=False)
        try:
            ref = unfold(rec.sd, rec.analysis, chk_model)
            area_ref = sum(p.area for p in ref)
        except Exception:   # noqa
            area_ref = None
        dev = None
        if area_ref is not None and rec.sd.volume > 0:
            dev = (area_ref * rec.analysis.thickness - rec.sd.volume) / rec.sd.volume * 100.0
        flat_area = sum(p.area for p in pieces)
        tab = sum(1 for p in pieces for b in p.bends if b.method == "table")
        kf = sum(1 for p in pieces for b in p.bends if b.method == "k")
        rec.checks = {
            "volume_dev_pct": dev,
            "volume": rec.sd.volume,
            "flat_area": flat_area,
            "bends_total": tab + kf,
            "bends_table": tab,
            "bends_k": kf,
            "closed": not any("ไม่ปิด" in w for w in rec.warnings),
            "pieces": len(pieces),
            "thickness_weight": rec.analysis.thickness_weight,
        }
        if dev is not None and abs(dev) > 1.5:
            rec.warnings.append(f"พื้นที่คลี่ไม่ตรงกับปริมาตรชิ้นงาน ({dev:+.2f}%) - ตรวจสอบว่ามีรูปทรงพิเศษ (ปั๊มนูน/เฉียง/ผิวโค้ง)")
        uns = rec.analysis.unsupported_faces
        if uns:
            uns_area = sum(rec.sd.faces[i].area for i in uns)
            if uns_area > 0.003 * rec.sd.area or (dev is not None and abs(dev) > 0.5):
                rec.warnings.append(f"มีผิวที่ไม่รองรับ {len(uns)} ผิว (กรวย/ผิวโค้งอิสระ) - ตรวจเทียบกับแบบ")
        if len(pieces) > 1:
            rec.warnings.append(f"ชิ้นงานนี้คลี่ได้ {len(pieces)} ชิ้นแยกกัน")
        wmax = max(p.bbox[2] - p.bbox[0] for p in pieces)
        hmax = max(p.bbox[3] - p.bbox[1] for p in pieces)
        if min(wmax, hmax) > 0 and max(wmax, hmax) / min(wmax, hmax) > 15:
            rec.warnings.append("สัดส่วนยาวมาก (>15:1) - ถ้าเป็นเหล็กฉาก/โปรไฟล์ที่ซื้อมา ให้ติ๊กไม่ตัด")

    def sets(self) -> int:
        try:
            return max(1, int(self.settings.get("sets") or 1))
        except (TypeError, ValueError):
            return 1

    def recompute(self):
        with self.lock:
            n_sets = self.sets()
            for rec in self.parts:
                rec.qty = (rec.qty_override if rec.qty_override else rec.qty_asm) * n_sets
            for rec in self.parts:
                self._ensure_flat(rec)
            self._rebuild_groups()

    # ------------------------------------------------------------------ groups
    def _rebuild_groups(self):
        prof = self.profile
        gmap: dict[tuple, Group] = {}
        order = []
        for rec in self.parts:
            if not rec.include or rec.status != "sheet" or not rec.pieces:
                continue
            t = rec.thickness_used(prof)
            row = match_row(prof, t, rec.material_id)
            mat = self.store.material(rec.material_id)
            code = material_code(mat, row, t)
            key = (mat["id"], t)
            g = gmap.get(key)
            if g is None:
                gkey = f"{mat['id']}|{t:g}"
                g = Group(id=f"g{len(gmap) + 1}", material_id=mat["id"], thickness=t, code=code,
                          name=self.group_names.get(gkey) or code, key=gkey)
                gmap[key] = g
                order.append(g)
            g.part_ids.append(rec.id)
        order.sort(key=lambda g: (g.material_id, g.thickness))
        for i, g in enumerate(order):
            g.id = f"g{i + 1}"
        used_names: dict[str, int] = {}
        for g in order:
            base = self.settings["filename"].format(job=safe_name(self.job_name), code=g.name, t=fmt_t(g.thickness),
                                                    material=g.material_id)
            base = safe_name(base)
            n = used_names.get(base, 0) + 1
            used_names[base] = n
            g.filename = base if n == 1 else f"{base}_{n}"
            self._layout_group(g)
        self.groups = order

    def _layout_group(self, g: Group):
        items = []
        byid = {r.id: r for r in self.parts}
        for pid in g.part_ids:
            rec = byid[pid]
            n = rec.qty if self.settings["copies"] == "qty" else 1
            for k, pc in enumerate(rec.pieces):
                nm = rec.name if len(rec.pieces) == 1 else f"{rec.name}-{k + 1}"
                for c in range(n):
                    items.append(Item(part_id=rec.id, name=nm, copy=c + 1, piece=pc, flip=rec.flip))
        sheet = tuple(self.settings["sheet"]) if self.settings.get("sheet") else None
        g.sheets = pack(items, gap=float(self.settings["gap"]), max_width=float(self.settings["max_width"]), sheet=sheet)

    def sheet_filename(self, g: Group, idx: int) -> str:
        return g.filename if len(g.sheets) == 1 else f"{g.filename}_{idx + 1}"

    # ------------------------------------------------------------------ user edits
    def update_settings(self, patch: dict):
        with self.lock:
            for k, v in patch.items():
                if k in DEFAULT_SETTINGS:
                    self.settings[k] = v
            # a material change in settings applies to parts that were not customised
            self.recompute()

    def update_part(self, pid: str, patch: dict):
        with self.lock:
            rec = next(r for r in self.parts if r.id == pid)
            if "include" in patch:
                rec.include = bool(patch["include"])
                if rec.include and rec.status == "excluded" and rec.reason_code in ("keyword", "not_sheet", "unfold_failed"):
                    # user forces inclusion: try to treat as sheet
                    if rec.analysis and rec.analysis.ok:
                        rec.status = "sheet"
                        rec.flat_key = None
                    else:
                        rec.include = False
                if not rec.include and rec.status == "sheet":
                    pass
            if "material_id" in patch:
                rec.material_id = patch["material_id"]
            if "t_override" in patch:
                v = patch["t_override"]
                rec.t_override = float(v) if v not in (None, "", 0) else None
                rec.flat_key = None
            if "flip" in patch:
                rec.flip = bool(patch["flip"])
            if "qty" in patch:
                v = patch["qty"]
                try:
                    v = int(v) if v not in (None, "") else None
                except (TypeError, ValueError):
                    raise ValueError("จำนวนต้องเป็นตัวเลข")
                if v is not None and v < 1:
                    raise ValueError("จำนวนต้องมากกว่า 0 (ถ้าไม่ต้องตัด ให้ติ๊กไม่ตัดแทน)")
                rec.qty_override = None if v in (None, rec.qty_asm) else v
            if "paint" in patch:
                rec.paint = None if patch["paint"] is None else bool(patch["paint"])
            if "plate" in patch:
                rec.plate = bool(patch["plate"])
            if "category" in patch:
                rec.category = str(patch["category"] or "").strip()[:60]
                if rec.category:
                    rec.include = False
            if rec.include:
                rec.category = ""
            self.recompute()
            return rec

    def move_parts(self, ids: list[str], target: dict):
        """Regroup several parts at once.

        target = {"material_id": "ZN", "thickness": 0.7}  -> cut with that steel / thickness
                 {"category": "ซื้อสำเร็จรูป"}            -> not cut, listed under that name
                 {"include": False}                        -> not cut"""
        with self.lock:
            byid = {r.id: r for r in self.parts}
            recs = [byid[i] for i in ids if i in byid]
            if not recs:
                raise ValueError("ไม่ได้เลือกชิ้นส่วน")
            mat_ids = {m["id"] for m in self.store.data["materials"]}
            for rec in recs:
                if target.get("category") or target.get("include") is False:
                    rec.include = False
                    rec.category = str(target.get("category") or "").strip()[:60]
                    continue
                mid = target.get("material_id")
                if mid not in mat_ids:
                    raise ValueError(f"ไม่มีชนิดเหล็ก {mid}")
                if not (rec.analysis and rec.analysis.ok):
                    raise ValueError(f"{rec.name} ไม่ใช่แผ่นเหล็ก จึงย้ายไปกลุ่มตัดไม่ได้")
                rec.material_id = mid
                t = target.get("thickness")
                if t:
                    t = float(t)
                    cur = rec.thickness_used(self.profile)
                    if abs(cur - t) > 1e-6:
                        rec.t_override = t
                        rec.flat_key = None
                rec.category = ""
                if not rec.include or rec.status != "sheet":
                    rec.include = True
                    if rec.status == "excluded":
                        rec.status = "sheet"
                        rec.flat_key = None
            self.recompute()

    def paints(self, rec: PartRecord) -> bool:
        """Is this part painted? The user's tick, else: unless its steel is in the price list's "no paint"."""
        if rec.paint is not None:
            return rec.paint
        return rec.material_id not in (self.pricing.get("no_paint") or [])

    def rename_group(self, key: str, name: str):
        """Name a DXF group (empty -> back to the steel code). The name is used for the DXF file name."""
        with self.lock:
            name = re.sub(r'[\\/:*?"<>|\r\n\t]+', "", str(name or "")).strip()[:60]
            if not any(g.key == key for g in self.groups):
                raise ValueError("ไม่พบกลุ่มนี้")
            other = {g.name for g in self.groups if g.key != key}
            if name and name in other:
                raise ValueError(f"มีกลุ่มชื่อ “{name}” อยู่แล้ว")
            if name:
                self.group_names[key] = name
            else:
                self.group_names.pop(key, None)
            self._rebuild_groups()

    def rename_category(self, old: str, name: str):
        """Rename a not-cut group (e.g. ซื้อสำเร็จรูป -> ลูกค้าส่งมาเอง)."""
        with self.lock:
            name = str(name or "").strip()[:60]
            if not name:
                raise ValueError("กรุณาใส่ชื่อกลุ่ม")
            hit = [r for r in self.parts if r.category == old]
            if not hit:
                raise ValueError("ไม่พบกลุ่มนี้")
            for r in hit:
                r.category = name
            self.recompute()

    # ------------------------------------------------------------------ pricing
    def set_pricing(self, pricing: dict | None = None, meta: dict | None = None):
        with self.lock:
            if pricing is not None:
                self.pricing = validate_pricing(pricing)
            if meta:
                for k in self.pricing_meta:
                    if k in meta:
                        self.pricing_meta[k] = str(meta[k] or "")[:200]

    def price(self) -> dict:
        from .pricing import price_project
        from .profiles import fill_price_rows
        with self.lock:
            fill_price_rows(self.pricing, self.profile, self.store.data["materials"],
                            [(g.material_id, g.thickness) for g in self.groups])
            out = price_project(self, self.pricing)
            out["pricing"] = self.pricing
            out["meta"] = self.pricing_meta
            return out

    # ------------------------------------------------------------------ save / load ("job file")
    def state(self) -> dict:
        """Everything the user decided, to rebuild the same job from the same STEP file."""
        return {
            "format": "sheetflat-job", "version": 1,
            "job": self.job_name, "source": self.source_name,
            "settings": self.settings, "profile": self.profile,
            "materials": self.store.data["materials"],
            "pricing": self.pricing, "pricing_meta": self.pricing_meta, "group_names": self.group_names,
            "parts": [{"id": r.id, "name": r.name, "include": r.include, "material_id": r.material_id,
                       "t_override": r.t_override, "flip": r.flip, "category": r.category,
                       "qty_override": r.qty_override, "paint": r.paint, "plate": r.plate} for r in self.parts],
        }

    def apply_state(self, st: dict):
        """Apply a saved state after load_step() read the same STEP file."""
        with self.lock:
            byid = {r.id: r for r in self.parts}
            for d in st.get("parts", []):
                rec = byid.get(d.get("id"))
                if rec is None or rec.name != d.get("name"):
                    rec = next((r for r in self.parts if r.name == d.get("name")), None)
                if rec is None:
                    continue
                rec.material_id = d.get("material_id") or rec.material_id
                rec.t_override = float(d["t_override"]) if d.get("t_override") else None
                rec.flip = bool(d.get("flip"))
                rec.category = str(d.get("category") or "")
                rec.qty_override = int(d["qty_override"]) if d.get("qty_override") else None
                rec.paint = None if d.get("paint") is None else bool(d["paint"])
                rec.plate = bool(d.get("plate"))
                want = bool(d.get("include", rec.include))
                if want and rec.status == "excluded" and rec.analysis and rec.analysis.ok:
                    rec.status = "sheet"
                rec.include = want and rec.status == "sheet"
                rec.flat_key = None
            if st.get("pricing"):
                self.pricing = validate_pricing(st["pricing"])
            if isinstance(st.get("group_names"), dict):
                self.group_names = {str(k): str(v)[:60] for k, v in st["group_names"].items() if str(v).strip()}
            if st.get("pricing_meta"):
                self.set_pricing(meta=st["pricing_meta"])
            self.recompute()

    def set_material_all(self, material_id: str):
        with self.lock:
            self.settings["material"] = material_id
            for rec in self.parts:
                rec.material_id = material_id
            self.recompute()

    # ------------------------------------------------------------------ views (JSON)
    def part_json(self, rec: PartRecord) -> dict:
        prof = self.profile
        t_used = rec.thickness_used(prof) if rec.measured_t else 0.0
        row = match_row(prof, t_used, rec.material_id) if rec.measured_t else None
        mat = self.store.material(rec.material_id)
        flat = None
        if rec.pieces:
            w = max(p.bbox[2] - p.bbox[0] for p in rec.pieces)
            h = max(p.bbox[3] - p.bbox[1] for p in rec.pieces)
            holes = sum(1 for p in rec.pieces for l in p.loops if l["role"] == "hole")
            cut = 0.0
            for p in rec.pieces:
                for q in p.prims:
                    pts = prim_points(q)
                    cut += float(np.sum(np.linalg.norm(np.diff(pts, axis=0), axis=1)))
            area = sum(p.area for p in rec.pieces)
            dens = float(self.store.data.get("density", 7.85))
            flat = {
                "w": round(w, 2), "h": round(h, 2), "area": round(area, 1), "holes": holes, "cut_len": round(cut, 1),
                "bends": [{"angle": round(b.angle_deg, 2), "r": round(b.r_inner, 3), "method": b.method,
                           "bd": b.bd, "k": b.k, "ba": round(b.ba, 3), "dir": b.direction, "len": round(b.length, 2)}
                          for p in rec.pieces for b in p.bends],
                "weight_kg": round(rec.sd.volume * dens * 1e-6, 4),
            }
        return {
            "thumb": self._thumb(rec),
            "id": rec.id, "name": rec.name, "qty": rec.qty, "qty_asm": rec.qty_asm, "qty_override": rec.qty_override,
            "paint": self.paints(rec), "paint_auto": rec.paint is None, "plate": rec.plate,
            "status": rec.status, "include": rec.include,
            "reason": rec.auto_reason, "reason_code": rec.reason_code,
            "material_id": rec.material_id, "category": rec.category,
            "thickness": {"measured": rec.measured_t, "used": t_used, "override": rec.t_override,
                          "gauge": row["gauge"] if row else None, "matched": row is not None,
                          "bd": row["bd"] if row else None},
            "code": material_code(mat, row, t_used) if rec.measured_t else "",
            "flat": flat, "checks": rec.checks, "warnings": rec.warnings, "flip": rec.flip,
            "bbox3": [round(x, 2) for x in rec.bbox3],
            "volume": round(rec.sd.volume, 1),
            "faces": len(rec.sd.faces),
        }

    @staticmethod
    def _thumb(rec: PartRecord):
        """Tiny SVG path of the flat outline (first piece), y flipped, in its own w x h box."""
        if not rec.pieces:
            return None
        from shapely.geometry import LineString
        pc = rec.pieces[0]
        xmin, ymin, xmax, ymax = pc.bbox
        tol = max(xmax - xmin, ymax - ymin) / 300.0
        parts = []
        for lp in pc.loops:
            pts = lp["pts"]
            if len(pts) > 8:
                pts = list(LineString(pts).simplify(tol).coords)
            xs = [(x - xmin) if not rec.flip else (xmax - x) for x, _ in pts]
            ys = [(ymax - y) for _, y in pts]
            parts.append("M" + "L".join(f"{a:.1f} {b:.1f}" for a, b in zip(xs, ys)) + "Z")
        return {"w": round(xmax - xmin, 1), "h": round(ymax - ymin, 1), "d": "".join(parts)}

    def to_json(self) -> dict:
        prof = self.profile
        parts = [self.part_json(r) for r in self.parts]
        groups = []
        byid = {r.id: r for r in self.parts}
        for g in self.groups:
            sheets = []
            for s in g.sheets:
                sheets.append({"index": s.index, "filename": self.sheet_filename(g, s.index) + ".dxf",
                               "w": round(s.width, 1), "h": round(s.height, 1), "count": len(s.placed), "oversize": s.oversize})
            pieces = sum(len(s.placed) for s in g.sheets)
            area = sum(pl.item.piece.area for s in g.sheets for pl in s.placed)
            weight = sum(byid[pid].sd.volume * (byid[pid].qty if self.settings["copies"] == "qty" else 1)
                         for pid in g.part_ids) * float(self.store.data.get("density", 7.85)) * 1e-6
            groups.append({"id": g.id, "code": g.code, "material_id": g.material_id, "thickness": g.thickness,
                           "filename": g.filename + ".dxf", "part_ids": g.part_ids, "sheets": sheets,
                           "name": g.name, "key": g.key, "renamed": g.name != g.code,
                           "pieces": pieces, "area": round(area, 0), "weight_kg": round(weight, 2)})
        excl = [p for p in parts if not p["include"]]
        total_pieces = sum(p["qty"] for p in parts)
        cut_pieces = sum(p["qty"] for p in parts if p["include"])
        return {
            "id": self.id, "job": self.job_name, "source": self.source_name, "root": self.root_name,
            "settings": self.settings, "profile": prof, "materials": self.store.data["materials"],
            "profiles": [{"id": p["id"], "name": p["name"]} for p in self.store.data["profiles"]],
            "parts": parts, "groups": groups, "can_save": bool(self.source_path),
            "sets": self.sets(),
            "summary": {"unique": len(parts), "pieces": total_pieces, "cut_unique": len(parts) - len(excl),
                        "cut_pieces": cut_pieces, "excluded_unique": len(excl),
                        "excluded_pieces": total_pieces - cut_pieces},
        }

    def geometry_json(self, rec: PartRecord) -> dict:
        """Flat pattern geometry of one part for the preview (normalised to 0,0)."""
        out = []
        for k, pc in enumerate(rec.pieces):
            xmin, ymin, xmax, ymax = pc.bbox
            dx, dy = -xmin, -ymin
            W = xmax - xmin
            from .unfold import transform_prim
            if rec.flip:
                prims = [transform_prim(q, xmax, dy, mirror=True) for q in pc.prims]
                mp = lambda p: (xmax - p[0], p[1] + dy)
            else:
                prims = [transform_prim(q, dx, dy) for q in pc.prims]
                mp = lambda p: (p[0] + dx, p[1] + dy)
            bends = [{"a": mp(b.line[0]), "b": mp(b.line[1]), "angle": round(b.angle_deg, 2), "r": round(b.r_inner, 3),
                      "dir": b.direction, "method": b.method, "ba": round(b.ba, 3), "bd": b.bd, "k": b.k,
                      "tan": [[mp(a), mp(c)] for a, c in b.tan_lines]} for b in pc.bends]
            fake = Placed(Item(rec.id, rec.name, 1, pc, rec.flip), 0.0, 0.0, xmax - xmin, ymax - ymin)
            out.append({"w": round(xmax - xmin, 3), "h": round(ymax - ymin, 3), "prims": prims, "bends": bends,
                        "loops": place_loops(fake), "area": round(pc.area, 2)})
        return {"id": rec.id, "name": rec.name, "pieces": out}

    def layout_json(self, g: Group, idx: int) -> dict:
        s = g.sheets[idx]
        items = []
        for pl in s.placed:
            prims, bends = place_geometry(pl)
            items.append({"part_id": pl.item.part_id, "name": pl.item.name, "copy": pl.item.copy,
                          "x": round(pl.x, 2), "y": round(pl.y, 2), "w": round(pl.w, 2), "h": round(pl.h, 2),
                          "prims": prims, "loops": place_loops(pl), "bends": [{"a": b["line"][0], "b": b["line"][1], "angle": round(b["angle"], 1),
                                                     "dir": b["dir"]} for b in bends]})
        return {"group": g.id, "code": g.code, "thickness": g.thickness, "index": idx, "w": round(s.width, 2),
                "h": round(s.height, 2), "items": items,
                "filename": self.sheet_filename(g, idx) + ".dxf"}
