"""Bend tables as an Excel workbook: one sheet = one table (one bend "mode" / profile), plus one sheet that lists the steel types.

The program reads this workbook whenever it needs a bend table, so the shop can edit it in Excel,
copy it to another PC, or send it to a customer and everyone works from the same numbers.

Sheet "ชนิดเหล็ก" (steel types, first sheet): which steels exist, so the file says by itself how many there are.

    A3 รหัส | B3 ชื่อ | C3 คำนำหน้าชื่อไฟล์ | D3.. one column per mode: how many thickness rows that steel has in the mode
    A4 ... one row per steel

Every other sheet is one mode (columns A-B for the settings, A-D for the table):

    A1  ชื่อโหมด                       B1  <mode name>
    A2  รหัส                           B2  <id, keep it stable; blank = the sheet name>
    A3  K-factor                       B3  0.5
    A4  มุมที่ถือว่าเป็น 90° (± องศา)     B4  0
    A5  ใช้ตารางได้เมื่อ R ใน ≤ (มม.)      B5  0.2
    A6  จับคู่ความหนากับตาราง ± (มม.)     B6  0.06
    A7  รอยพับ jog ใช้ K-factor           B7  ใช่
    A8  เส้นโค้งในแนวพับ -> เส้นตรง        B8  ใช่
    A10 เหล็ก (รหัส) | เบอร์ | ความหนา (มม.) | ค่ายืด (มม.)
    A11 ... one row per steel and thickness: this steel, at this thickness, bends with this deduction

Settings are found by their label in column A (the order may change) and the table by its header row, whose
columns are found by their names (the older layout, gauge | thickness | deduction | steel, is read too).
A deduction that is the same for every steel is written once per steel so the file can be checked at a glance;
when the file is read back, a thickness that has the same number for all steels is one "all steels" row again,
so a steel added later gets it too.
"""
from __future__ import annotations

import re
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.utils import get_column_letter
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.worksheet.datavalidation import DataValidation

from .i18n import norm, tr, tr_exact, translate_workbook
from .xlsx_safe import put, text

STEEL_SHEET = "ชนิดเหล็ก"
STEEL_HEADER = ("รหัส", "ชื่อ", "คำนำหน้าชื่อไฟล์")
HEADER = ("เหล็ก (รหัส)", "เบอร์", "ความหนา (มม.)", "ค่ายืด (มม.)")
_OLD_STEEL_HEADER = "ใช้กับเหล็ก"            # older layout: the steel is the 4th column, blank = every steel
# (key, label, default, kind)
SETTINGS = [
    ("name", "ชื่อโหมด", "", "text"),
    ("id", "รหัส", "", "text"),
    ("k_factor", "K-factor", 0.5, "num"),
    ("angle_tol", "มุมที่ถือว่าเป็น 90° (± องศา)", 0.0, "num"),
    ("table_max_r", "ใช้ตารางได้เมื่อ R ใน ≤ (มม.)", 0.2, "num"),
    ("match_tol", "จับคู่ความหนากับตาราง ± (มม.)", 0.06, "num"),
    ("jog_use_k", "รอยพับสลับทิศติดกัน (jog) ใช้ K-factor", True, "bool"),
    ("simplify_bends", "เส้นโค้งที่พาดผ่านรอยพับ ทำเป็นเส้นตรง", True, "bool"),
]
TABLE_ROW = len(SETTINGS) + 2
STEEL_FIRST = 4                                  # first steel row of the steel sheet
YES, NO = "ใช่", "ไม่"
_TRUE = {"ใช่", "yes", "y", "true", "1", "on", "จริง", "เปิด"}
_FALSE = {"ไม่", "ไม่ใช่", "no", "n", "false", "0", "off", "เท็จ", "ปิด"}
_BAD_TITLE = re.compile(r"[\[\]:*?/\\]")
_SAME_T = 0.0005


def _sheet_title(name: str, used: set) -> str:
    base = _BAD_TITLE.sub("-", str(name or "โหมด")).strip().strip("'") or "โหมด"
    base = base[:31]
    title, n = base, 2
    while title.lower() in used:
        suffix = f" ({n})"
        title = base[:31 - len(suffix)] + suffix
        n += 1
    used.add(title.lower())
    return title


def _is_steel_sheet(title: str) -> bool:
    return title.strip() in (STEEL_SHEET, tr(STEEL_SHEET, "en"))


def _table_rows(table: list, steel_ids: list | None) -> list:
    """Rows to show in Excel: with a steel list, a row for "every steel" becomes one row per steel
    (unless that steel has its own row at that thickness)."""
    rows = sorted(table, key=lambda r: (float(r["thickness"]), str(r.get("material") or "")))
    if not steel_ids:
        return [(r.get("material", ""), r) for r in rows]
    own = {(m, round(float(r["thickness"]), 4)) for r in rows if (m := r.get("material") or "")}
    out = []
    for r in rows:
        m = r.get("material") or ""
        if m:
            out.append((m, r))
            continue
        for s in steel_ids:
            if (s, round(float(r["thickness"]), 4)) not in own:
                out.append((s, r))
    order = {s: i for i, s in enumerate(steel_ids)}
    out.sort(key=lambda x: (float(x[1]["thickness"]), order.get(x[0], 99), x[0]))
    return out


def write_profiles(path: Path, profiles: list, lang: str = "th", materials: list | None = None) -> None:
    """Write one sheet per profile (and, when ``materials`` is given, the sheet of steel types).
    Raises PermissionError if the file is open in Excel."""
    wb = Workbook()
    wb.remove(wb.active)
    used: set = set()
    yes_no = (YES, NO) if norm(lang) == "th" else ("Yes", "No")
    head_fill = PatternFill("solid", fgColor="DDEBF7")
    key_fill = PatternFill("solid", fgColor="F2F2F2")
    thin = Side(style="thin", color="999999")
    box = Border(left=thin, right=thin, top=thin, bottom=thin)
    steels = [m for m in (materials or []) if str(m.get("id") or "").strip()]
    steel_ids = [str(m["id"]).strip() for m in steels]
    steel_title = tr(STEEL_SHEET, norm(lang))
    if steels:
        used.add(steel_title.lower())
        ws0 = wb.create_sheet(steel_title)
    mode_titles = []
    for p in profiles:
        ws = wb.create_sheet(_sheet_title(tr_exact(p.get("name"), lang), used))
        mode_titles.append(ws.title)
        for i, (key, label, default, kind) in enumerate(SETTINGS, start=1):
            v = p.get(key, default)
            a = ws.cell(i, 1, label)
            a.fill, a.border, a.font = key_fill, box, Font(bold=True)
            c = ws.cell(i, 2)
            if kind == "bool":
                put(c, (YES if v else NO) if yes_no == (YES, NO) else (yes_no[0] if v else yes_no[1]))
            elif kind == "num":
                c.value = float(v)
            else:
                put(c, text(tr_exact(v, lang) if key == "name" else v))
            c.border = box
            c.alignment = Alignment(horizontal="left")
        for j, h in enumerate(HEADER, start=1):
            c = ws.cell(TABLE_ROW, j, h)
            c.font, c.fill, c.border = Font(bold=True), head_fill, box
            c.alignment = Alignment(horizontal="center", wrap_text=True)
        for i, (mat, r) in enumerate(_table_rows(p.get("table", []), steel_ids or None), start=TABLE_ROW + 1):
            put(ws.cell(i, 1), text(mat))
            put(ws.cell(i, 2), text(r.get("gauge", "")))
            ws.cell(i, 3, float(r["thickness"]))
            ws.cell(i, 4, float(r["bd"]))
            for j in range(1, 5):
                ws.cell(i, j).border = box
        dv = DataValidation(type="list", formula1=f'"{yes_no[0]},{yes_no[1]}"', allow_blank=False)
        ws.add_data_validation(dv)
        dv.add("B7:B8")
        if steels:
            ds = DataValidation(type="list", formula1=f"='{steel_title}'!$A${STEEL_FIRST}:$A$200", allow_blank=True)
            ws.add_data_validation(ds)
            ds.add(f"A{TABLE_ROW + 1}:A500")
        for i, msg in enumerate((
                "แก้ตัวเลขในไฟล์นี้ได้เลย โปรแกรมอ่านค่าจากไฟล์นี้ทุกครั้งที่คำนวณ",
                "1 ชีต = 1 ตาราง (1 โหมด) · เพิ่มโหมดใหม่ด้วยการคัดลอกชีตแล้วแก้ชื่อโหมด (B1) และรหัส (B2)",
                "ค่ายืด: แผ่นก่อนพับ = ผลรวมขนาดด้านนอกสองด้าน − ค่ายืด (ใช้กับรอยพับ 90° เท่านั้น)",
                "1 แถว = เหล็กชนิดนี้ ที่ความหนานี้ ใช้ค่ายืดเท่านี้ · เหล็กชนิดใหม่ให้เพิ่มแถวของมันในตารางนี้"), start=1):
            ws.cell(i, 6, msg).font = Font(italic=True, color="666666")
        ws.column_dimensions["A"].width = 42
        ws.column_dimensions["B"].width = 22
        ws.column_dimensions["C"].width = 16
        ws.column_dimensions["D"].width = 28
        ws.freeze_panes = ws.cell(TABLE_ROW + 1, 1)
    if not profiles:
        raise ValueError("ต้องมีโหมดค่ายืดอย่างน้อย 1 โหมด")
    if steels:
        a = ws0.cell(1, 1, "จำนวนชนิดเหล็ก")
        a.fill, a.border, a.font = key_fill, box, Font(bold=True)
        b = ws0.cell(1, 2, f"=COUNTA(A{STEEL_FIRST}:A200)")
        b.border = box
        ws0.cell(2, 1, "1 แถว = เหล็ก 1 ชนิด · คอลัมน์ D เป็นต้นไป = จำนวนแถวความหนาของเหล็กชนิดนั้นในแต่ละโหมด (เช็กว่าครบ)").font = \
            Font(italic=True, color="666666")
        heads = list(STEEL_HEADER)
        for j, h in enumerate(heads, start=1):
            c = ws0.cell(3, j, h)
            c.font, c.fill, c.border = Font(bold=True), head_fill, box
            c.alignment = Alignment(horizontal="center", wrap_text=True)
        for j, t in enumerate(mode_titles, start=len(heads) + 1):
            c = put(ws0.cell(3, j), text(t))
            c.font, c.fill, c.border = Font(bold=True), head_fill, box
            c.alignment = Alignment(horizontal="center", wrap_text=True)
        for i, m in enumerate(steels, start=STEEL_FIRST):
            put(ws0.cell(i, 1), text(str(m["id"]).strip()))
            put(ws0.cell(i, 2), text(tr_exact(str(m.get("name") or m["id"]).strip(), lang)))
            put(ws0.cell(i, 3), text(str(m.get("prefix") or m["id"]).strip()))
            for j, t in enumerate(mode_titles, start=len(heads) + 1):
                ws0.cell(i, j, f"=COUNTIF('{t}'!$A${TABLE_ROW + 1}:$A$500,$A{i})")
            for j in range(1, len(heads) + len(mode_titles) + 1):
                ws0.cell(i, j).border = box
        ws0.column_dimensions["A"].width = 24
        ws0.column_dimensions["B"].width = 30
        ws0.column_dimensions["C"].width = 22
        ws0.freeze_panes = ws0.cell(STEEL_FIRST, 1)
    translate_workbook(wb, lang, titles=False)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    wb.save(tmp)
    tmp.replace(path)              # PermissionError here = the workbook is open in Excel


def _num(v, label, sheet, *, allow_blank=False, default=None):
    if v is None or (isinstance(v, str) and not v.strip()):
        if allow_blank:
            return default
        raise ValueError(f"ชีต “{sheet}”: ช่อง {label} ว่างอยู่")
    if isinstance(v, bool):
        raise ValueError(f"ชีต “{sheet}”: {label} ต้องเป็นตัวเลข")
    try:
        return float(str(v).replace(",", "").strip()) if isinstance(v, str) else float(v)
    except ValueError:
        raise ValueError(f"ชีต “{sheet}”: {label} ต้องเป็นตัวเลข (พบ “{v}”)") from None


def _bool(v, label, sheet, default):
    if v is None or (isinstance(v, str) and not v.strip()):
        return default
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in _TRUE:
        return True
    if s in _FALSE:
        return False
    raise ValueError(f"ชีต “{sheet}”: {label} ต้องเป็น ใช่ หรือ ไม่ (พบ “{v}”)")


def _load(path):
    try:
        return load_workbook(path, data_only=True)
    except PermissionError:
        raise
    except Exception as ex:
        raise ValueError(f"เปิดไฟล์ Excel ค่ายืดไม่ได้: {ex}") from None


def _steel_sheet(wb):
    return next((ws for ws in wb.worksheets if _is_steel_sheet(ws.title)), None)


def _read_steels(wb) -> list | None:
    """[{id, name, prefix}] of the steel sheet, or None when the workbook has none (older files)."""
    ws = _steel_sheet(wb)
    if ws is None:
        return None
    heads = (STEEL_HEADER[0], tr(STEEL_HEADER[0], "en"))
    hdr = next((r for r in range(1, 15) if str(ws.cell(r, 1).value or "").strip() in heads), None)
    if hdr is None:
        raise ValueError(f"ชีต “{ws.title}”: ไม่พบหัวตาราง “{STEEL_HEADER[0]}” ในคอลัมน์ A")
    out, seen = [], set()
    for r in range(hdr + 1, ws.max_row + 1):
        mid = str(ws.cell(r, 1).value or "").strip()
        if not mid:
            continue
        if mid in seen:
            raise ValueError(f"ชีต “{ws.title}” แถว {r}: รหัสเหล็ก {mid} ซ้ำกัน")
        seen.add(mid)
        name = str(ws.cell(r, 2).value or "").strip() or mid
        prefix = str(ws.cell(r, 3).value or "").strip() or mid
        out.append({"id": mid, "name": name, "prefix": prefix})
    if not out:
        raise ValueError(f"ชีต “{ws.title}”: ไม่มีเหล็กสักชนิด")
    return out


def read_materials(path: Path) -> list | None:
    """The steel types listed in the workbook (None if it has no steel sheet). Raises ValueError on bad data."""
    return _read_steels(_load(path))


def _columns(ws, hdr) -> dict:
    """Which column holds what, from the header row (so the old and the new layout both read)."""
    names = {"material": (HEADER[0], "ใช้กับเหล็ก (ว่าง = ทุกชนิด)", _OLD_STEEL_HEADER, "เหล็ก"), "gauge": (HEADER[1],),
             "thickness": (HEADER[2],), "bd": (HEADER[3],)}
    found = {}
    for c in range(1, 9):
        h = str(ws.cell(hdr, c).value or "").strip()
        if not h:
            continue
        for key, fulls in names.items():
            if key in found:
                continue
            for s in fulls:
                if h in (s, tr(s, "en")) or h.startswith(s.split(" (")[0]):
                    found[key] = c
                    break
    missing = [k for k in ("gauge", "thickness", "bd") if k not in found]
    if missing:
        raise ValueError(f"ชีต “{ws.title}”: หัวตารางไม่ครบ (ต้องมี เบอร์ ความหนา ค่ายืด)")
    found.setdefault("material", None)
    return found


def _collapse(rows: list, steel_ids: list) -> list:
    """A thickness whose deduction is the same number for every steel is one row for all steels again."""
    want = set(steel_ids)
    by_t: dict = {}
    for r in rows:
        by_t.setdefault(round(r["thickness"], 4), []).append(r)
    out = []
    for t, grp in by_t.items():
        mats = [r["material"] for r in grp]
        if (len(grp) == len(want) and set(mats) == want
                and len({(r["gauge"], round(r["bd"], 6)) for r in grp}) == 1):
            out.append({**grp[0], "material": ""})
        else:
            out.extend(grp)
    return out


def read_profiles(path: Path) -> list:
    """Read every mode sheet as a profile. Raises ValueError (Thai message naming the sheet and cell) on bad data."""
    wb = _load(path)
    steels = _read_steels(wb)
    steel_ids = [s["id"] for s in steels] if steels else None
    out, ids = [], set()
    for ws in wb.worksheets:
        if _is_steel_sheet(ws.title):
            continue
        title = ws.title
        labels = {str(ws.cell(r, 1).value or "").strip(): r for r in range(1, TABLE_ROW + 1)}
        p: dict = {}
        for key, label, default, kind in SETTINGS:
            r = labels.get(label) or labels.get(tr(label, "en"))
            v = ws.cell(r, 2).value if r else None
            if kind == "num":
                p[key] = _num(v, label, title, allow_blank=True, default=default)
            elif kind == "bool":
                p[key] = _bool(v, label, title, default)
            else:
                p[key] = str(v).strip() if v is not None else ""
        gauge_heads = (HEADER[1], tr(HEADER[1], "en"))
        hdr = next((r for r in range(1, ws.max_row + 1)
                    if any(str(ws.cell(r, c).value or "").strip() in gauge_heads for c in range(1, 4))), None)
        if hdr is None:
            raise ValueError(f"ชีต “{title}”: ไม่พบหัวตาราง “{HEADER[1]}” ในแถวหัวตาราง")
        col = _columns(ws, hdr)
        rows = []
        for r in range(hdr + 1, ws.max_row + 1):
            g, t, bd = (ws.cell(r, col[k]).value for k in ("gauge", "thickness", "bd"))
            m = ws.cell(r, col["material"]).value if col["material"] else None
            m = str(m or "").strip()
            if t is None and bd is None and not str(g or "").strip() and not m:
                continue
            if t is None or bd is None:
                raise ValueError(f"ชีต “{title}” แถว {r}: ต้องมีทั้งความหนาและค่ายืด")
            if steel_ids is not None and m and m not in steel_ids:
                raise ValueError(f"ชีต “{title}” แถว {r}: ไม่มีเหล็กรหัส “{m}” ในชีต “{STEEL_SHEET}”")
            rows.append({"gauge": str(g or "").strip(), "thickness": _num(t, "ความหนา", f"{title}!{get_column_letter(col['thickness'])}{r}"),
                         "bd": _num(bd, "ค่ายืด", f"{title}!{get_column_letter(col['bd'])}{r}"), "material": m})
        if not rows:
            raise ValueError(f"ชีต “{title}”: ตารางค่ายืดไม่มีแถวข้อมูล")
        p["table"] = _collapse(rows, steel_ids) if steel_ids else rows
        p["name"] = p["name"] or title
        pid = p["id"] or re.sub(r"\s+", "_", title)
        base, n = pid, 2
        while pid in ids:                          # a copied sheet keeps the old id: make it unique
            pid, n = f"{base}_{n}", n + 1
        ids.add(pid)
        p["id"] = pid
        out.append(p)
    if not out:
        raise ValueError("ไฟล์ Excel ค่ายืดไม่มีชีตโหมดเลย")
    return out
