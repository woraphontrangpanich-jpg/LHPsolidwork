"""Excel summary of an assembly: what is in it, how many of each, what was cut and what was left out."""
from __future__ import annotations

import datetime
import io

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

HEAD_FILL = PatternFill("solid", fgColor="1F2A37")
HEAD_FONT = Font(bold=True, color="FFFFFF", name="Tahoma")
BASE_FONT = Font(name="Tahoma", size=10)
THIN = Side(style="thin", color="C9CED6")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
WARN_FILL = PatternFill("solid", fgColor="FFF4CE")
EXCL_FILL = PatternFill("solid", fgColor="F3F4F6")


def _header(ws, row, headers, widths=None):
    for i, h in enumerate(headers, 1):
        c = ws.cell(row=row, column=i, value=h)
        c.fill, c.font = HEAD_FILL, HEAD_FONT
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        c.border = BORDER
    ws.row_dimensions[row].height = 30
    if widths:
        for i, w in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(i)].width = w


def _row(ws, row, values, fill=None, numfmt=None):
    for i, v in enumerate(values, 1):
        c = ws.cell(row=row, column=i, value=v)
        c.font = BASE_FONT
        c.border = BORDER
        c.alignment = Alignment(vertical="center", wrap_text=isinstance(v, str) and len(v) > 28)
        if fill:
            c.fill = fill
        if numfmt and i in numfmt:
            c.number_format = numfmt[i]


def build_xlsx(project) -> bytes:
    """``project`` is a :class:`sheetflat.core.project.Project`."""
    js = project.to_json()
    prof = js["profile"]
    wb = Workbook()

    # ------------------------------------------------------------ 1. summary
    ws = wb.active
    ws.title = "สรุป"
    ws["A1"] = "สรุปงานคลี่แผ่น"
    ws["A1"].font = Font(bold=True, size=16, name="Tahoma")
    info = [
        ("ชื่องาน", js["job"]),
        ("ไฟล์ต้นฉบับ", js["source"]),
        ("วันที่", datetime.datetime.now().strftime("%Y-%m-%d %H:%M")),
        ("โหมดค่ายืด", prof["name"]),
        ("K-factor (รอยพับไม่ฉาก/โค้ง)", prof["k_factor"]),
        ("จำนวนชิ้นส่วนทั้งหมดใน Assembly", js["summary"]["pieces"]),
        ("   - ชิ้นที่นำไปตัด", js["summary"]["cut_pieces"]),
        ("   - ชิ้นที่ไม่ตัด (ท่อ/ชิ้นส่วนซื้อ/อื่นๆ)", js["summary"]["excluded_pieces"]),
        ("จำนวนไฟล์ DXF", sum(len(g["sheets"]) for g in js["groups"])),
        ("จำนวนชิ้นใน DXF", "ตามจำนวนจริง (Qty)" if js["settings"]["copies"] == "qty" else "1 ชิ้นต่อพาร์ท"),
    ]
    for i, (k, v) in enumerate(info, 3):
        ws.cell(row=i, column=1, value=k).font = Font(bold=True, name="Tahoma", size=10)
        ws.cell(row=i, column=2, value=v).font = BASE_FONT
    r0 = 3 + len(info) + 2
    _header(ws, r0, ["ไฟล์ DXF", "วัสดุ", "ความหนา (มม.)", "จำนวนแบบ", "จำนวนชิ้น", "พื้นที่คลี่รวม (ตร.ม.)",
                     "น้ำหนักรวม (กก.)", "ขนาดไฟล์ (มม.)"],
            [44, 14, 14, 12, 12, 20, 18, 22])
    r = r0 + 1
    first = r
    for g in js["groups"]:
        for s in g["sheets"]:
            _row(ws, r, [s["filename"], g["code"], g["thickness"], len(g["part_ids"]), s["count"],
                         round(g["area"] / 1e6, 4) if len(g["sheets"]) == 1 else None,
                         g["weight_kg"] if len(g["sheets"]) == 1 else None, f"{s['w']:.0f} x {s['h']:.0f}"],
                 numfmt={6: "0.0000", 7: "0.00"})
            r += 1
    if r > first:
        _row(ws, r, ["รวม", "", "", f"=SUM(D{first}:D{r - 1})", f"=SUM(E{first}:E{r - 1})", "", "", ""])
        for c in range(1, 9):
            ws.cell(row=r, column=c).font = Font(bold=True, name="Tahoma", size=10)

    # ------------------------------------------------------------ 2. parts to cut
    ws2 = wb.create_sheet("ชิ้นส่วนที่ตัด")
    _header(ws2, 1, ["ลำดับ", "ชื่อชิ้นส่วน", "จำนวน", "วัสดุ", "ความหนา (มม.)", "ขนาดแผ่นคลี่ กว้าง (มม.)",
                     "ขนาดแผ่นคลี่ ยาว (มม.)", "พื้นที่ (ตร.มม.)", "ความยาวเส้นตัด (มม.)", "จำนวนรู",
                     "จำนวนรอยพับ", "น้ำหนัก/ชิ้น (กก.)", "น้ำหนักรวม (กก.)", "ไฟล์ DXF", "หมายเหตุ"],
            [7, 28, 9, 11, 12, 16, 16, 14, 16, 10, 12, 14, 14, 40, 60])
    fname = {}
    for g in js["groups"]:
        for pid in g["part_ids"]:
            fname[pid] = g["filename"] if len(g["sheets"]) == 1 else g["filename"] + " (หลายแผ่น)"
    r = 2
    n = 0
    for p in js["parts"]:
        if not p["include"] or not p["flat"]:
            continue
        n += 1
        f = p["flat"]
        _row(ws2, r, [n, p["name"], p["qty"], p["code"], p["thickness"]["used"], f["w"], f["h"], f["area"], f["cut_len"],
                      f["holes"], len(f["bends"]), f["weight_kg"], f"=C{r}*L{r}", fname.get(p["id"], ""),
                      "; ".join(p["warnings"])],
             fill=WARN_FILL if p["warnings"] else None, numfmt={12: "0.000", 13: "0.000"})
        r += 1
    if r > 2:
        _row(ws2, r, ["", "รวม", f"=SUM(C2:C{r - 1})", "", "", "", "", "", "", "", "", "", f"=SUM(M2:M{r - 1})", "", ""])
        for c in range(1, 16):
            ws2.cell(row=r, column=c).font = Font(bold=True, name="Tahoma", size=10)
    ws2.freeze_panes = "C2"

    # ------------------------------------------------------------ 3. excluded
    ws3 = wb.create_sheet("ไม่นำไปตัด")
    _header(ws3, 1, ["ลำดับ", "ชื่อชิ้นส่วน", "จำนวน", "กลุ่ม", "เหตุผลที่ไม่ตัด", "ขนาด 3D (มม.)"], [7, 30, 9, 22, 80, 24])
    r = 2
    n = 0
    for p in sorted((p for p in js["parts"] if not p["include"]), key=lambda p: (p.get("category") or "~")):
        n += 1
        why = p["reason"] or "ผู้ใช้เลือกไม่ตัด"
        _row(ws3, r, [n, p["name"], p["qty"], p.get("category") or "", why, " x ".join(f"{x:.0f}" for x in p["bbox3"])],
             fill=EXCL_FILL)
        r += 1
    if r > 2:
        _row(ws3, r, ["", "รวม", f"=SUM(C2:C{r - 1})", "", "", ""])
        for c in range(1, 7):
            ws3.cell(row=r, column=c).font = Font(bold=True, name="Tahoma", size=10)

    # ------------------------------------------------------------ 4. bends
    ws4 = wb.create_sheet("รอยพับ")
    _header(ws4, 1, ["ชิ้นส่วน", "ลำดับพับ", "มุมด้านใน (องศา)", "ทิศ (มองจากด้านบน)", "รัศมีใน (มม.)", "วิธีคำนวณ",
                     "ค่ายืด BD (มม.)", "K-factor", "ความกว้างแถบพับ BA (มม.)", "ความยาวรอยพับ (มม.)"],
            [30, 10, 12, 18, 14, 22, 15, 10, 22, 18])
    r = 2
    for p in js["parts"]:
        if not p["include"] or not p["flat"]:
            continue
        for i, b in enumerate(p["flat"]["bends"], 1):
            _row(ws4, r, [p["name"], i, b["angle_in"], "พับขึ้น" if b["dir"] == "up" else "พับลง", b["r"],
                          "ตารางค่ายืด (90°)" if b["method"] == "table" else "K-factor",
                          b["bd"] if b["bd"] is not None else "", b["k"] if b["k"] is not None else "", b["ba"], b["len"]])
            r += 1
    ws4.freeze_panes = "A2"

    # ------------------------------------------------------------ 5. table used
    ws5 = wb.create_sheet("ตารางค่ายืดที่ใช้")
    _header(ws5, 1, ["เบอร์", "ความหนา (มม.)", "ค่ายืด 90° (มม.)", "ใช้กับเหล็ก"], [14, 16, 18, 16])
    for i, row in enumerate(prof["table"], 2):
        _row(ws5, i, [row["gauge"], row["thickness"], row["bd"], row.get("material") or "ทุกชนิด"])
    ws5.cell(row=len(prof["table"]) + 3, column=1, value=f"K-factor = {prof['k_factor']}  (ใช้กับรอยพับที่ไม่ใช่ 90° หรือรัศมีใหญ่/โค้ง)").font = BASE_FONT

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
