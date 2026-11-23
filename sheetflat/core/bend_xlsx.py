"""Bend tables as an Excel workbook: one sheet = one table (one bend "mode" / profile).

The program reads this workbook whenever it needs a bend table, so the shop can edit it in Excel,
copy it to another PC, or send it to a customer and everyone works from the same numbers.

Layout of every sheet (columns A-B for the settings, A-D for the table):

    A1  ชื่อโหมด                       B1  <mode name>
    A2  รหัส                           B2  <id, keep it stable; blank = the sheet name>
    A3  K-factor                       B3  0.5
    A4  มุมที่ถือว่าเป็น 90° (± องศา)     B4  0
    A5  ใช้ตารางได้เมื่อ R ใน ≤ (มม.)      B5  0.2
    A6  จับคู่ความหนากับตาราง ± (มม.)     B6  0.06
    A7  รอยพับ jog ใช้ K-factor           B7  ใช่
    A8  เส้นโค้งในแนวพับ -> เส้นตรง        B8  ใช่
    A10 เบอร์ | ความหนา (มม.) | ค่ายืด (มม.) | ใช้กับเหล็ก (ว่าง = ทุกชนิด)
    A11 ... one row per thickness

Settings are found by their label in column A (the order may change) and the table by its header row.
"""
from __future__ import annotations

import re
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.worksheet.datavalidation import DataValidation

from .i18n import norm, tr, tr_exact, translate_workbook
from .xlsx_safe import put, text

HEADER = ("เบอร์", "ความหนา (มม.)", "ค่ายืด (มม.)", "ใช้กับเหล็ก (ว่าง = ทุกชนิด)")
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
YES, NO = "ใช่", "ไม่"
_TRUE = {"ใช่", "yes", "y", "true", "1", "on", "จริง", "เปิด"}
_FALSE = {"ไม่", "ไม่ใช่", "no", "n", "false", "0", "off", "เท็จ", "ปิด"}
_BAD_TITLE = re.compile(r"[\[\]:*?/\\]")


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


def write_profiles(path: Path, profiles: list, lang: str = "th") -> None:
    """Write one sheet per profile. Raises PermissionError if the file is open in Excel."""
    wb = Workbook()
    wb.remove(wb.active)
    used: set = set()
    yes_no = (YES, NO) if norm(lang) == "th" else ("Yes", "No")
    head_fill = PatternFill("solid", fgColor="DDEBF7")
    key_fill = PatternFill("solid", fgColor="F2F2F2")
    thin = Side(style="thin", color="999999")
    box = Border(left=thin, right=thin, top=thin, bottom=thin)
    for p in profiles:
        ws = wb.create_sheet(_sheet_title(tr_exact(p.get("name"), lang), used))
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
        for i, r in enumerate(p.get("table", []), start=TABLE_ROW + 1):
            put(ws.cell(i, 1), text(r.get("gauge", "")))
            ws.cell(i, 2, float(r["thickness"]))
            ws.cell(i, 3, float(r["bd"]))
            put(ws.cell(i, 4), text(r.get("material", "")))
            for j in range(1, 5):
                ws.cell(i, j).border = box
        dv = DataValidation(type="list", formula1=f'"{yes_no[0]},{yes_no[1]}"', allow_blank=False)
        ws.add_data_validation(dv)
        dv.add("B7:B8")
        for i, msg in enumerate((
                "แก้ตัวเลขในไฟล์นี้ได้เลย โปรแกรมอ่านค่าจากไฟล์นี้ทุกครั้งที่คำนวณ",
                "1 ชีต = 1 ตาราง (1 โหมด) · เพิ่มโหมดใหม่ด้วยการคัดลอกชีตแล้วแก้ชื่อโหมด (B1) และรหัส (B2)",
                "ค่ายืด: แผ่นก่อนพับ = ผลรวมขนาดด้านนอกสองด้าน − ค่ายืด (ใช้กับรอยพับ 90° เท่านั้น)"), start=1):
            ws.cell(i, 6, msg).font = Font(italic=True, color="666666")
        ws.column_dimensions["A"].width = 42
        ws.column_dimensions["B"].width = 22
        ws.column_dimensions["C"].width = 16
        ws.column_dimensions["D"].width = 28
        ws.freeze_panes = ws.cell(TABLE_ROW + 1, 1)
    if not wb.sheetnames:
        raise ValueError("ต้องมีโหมดค่ายืดอย่างน้อย 1 โหมด")
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


def read_profiles(path: Path) -> list:
    """Read every sheet as a profile. Raises ValueError (Thai message naming the sheet and cell) on bad data."""
    try:
        wb = load_workbook(path, data_only=True)
    except PermissionError:
        raise
    except Exception as ex:
        raise ValueError(f"เปิดไฟล์ Excel ค่ายืดไม่ได้: {ex}") from None
    out, ids = [], set()
    for ws in wb.worksheets:
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
        hdr = next((r for r in range(1, ws.max_row + 1) if str(ws.cell(r, 1).value or "").strip() in (HEADER[0], tr(HEADER[0], "en"))), None)
        if hdr is None:
            raise ValueError(f"ชีต “{title}”: ไม่พบหัวตาราง “{HEADER[0]}” ในคอลัมน์ A")
        rows = []
        for r in range(hdr + 1, ws.max_row + 1):
            g, t, bd, m = (ws.cell(r, c).value for c in (1, 2, 3, 4))
            if t is None and bd is None and not str(g or "").strip():
                continue
            if t is None or bd is None:
                raise ValueError(f"ชีต “{title}” แถว {r}: ต้องมีทั้งความหนาและค่ายืด")
            rows.append({"gauge": str(g or "").strip(), "thickness": _num(t, "ความหนา", f"{title}!B{r}"),
                         "bd": _num(bd, "ค่ายืด", f"{title}!C{r}"), "material": str(m or "").strip()})
        if not rows:
            raise ValueError(f"ชีต “{title}”: ตารางค่ายืดไม่มีแถวข้อมูล")
        p["table"] = rows
        p["name"] = p["name"] or title
        pid = p["id"] or re.sub(r"\s+", "_", title)
        base, n = pid, 2
        while pid in ids:                          # a copied sheet keeps the old id: make it unique
            pid, n = f"{base}_{n}", n + 1
        ids.add(pid)
        p["id"] = pid
        out.append(p)
    if not out:
        raise ValueError("ไฟล์ Excel ค่ายืดไม่มีชีตเลย")
    return out
