"""Price workbook ("ใบคิดราคา") in the layout of the shop's สูตรคิดราคา_bank.xlsm, so it reads the same.

    สรุป            first page: job, customer, date, steel prices used, totals per steel/thickness
    รายละเอียด      the workbook's main sheet: same columns A..Y, same formulas, same colours and
                    highlights (NO, วัตถุดิบ, ความหนา, SIZE X/Y, แผ่นที่ใช้, ค่าโลหะ, ค่าสี, ค่าชุบ, จำนวน,
                    น้ำหนัก, ราคารวม, เทียบ, เรียงแบบ 1/2, Total Dimension, clamp margin in R2).
                    Added on the right: part name, bends, holes and one column per extra cost.
    ราคาเหล็ก       the workbook's price sheet: one row per steel + bend-table thickness (ZN#22, ZN#3mm ...)
    ค่าแรงและอื่นๆ   the workbook's Note sheet as a table: extra costs, paint, plating, markup
    เทียบขนาดแผ่น   every sheet size x both arrangements for every piece, and which one was chosen

Every money cell is a live formula reading ราคาเหล็ก / ค่าแรงและอื่นๆ, so a price changed there
updates the whole workbook. The sheet size (column F) is SheetFlat's choice, written as a value.
"""
from __future__ import annotations

import datetime
import io

from openpyxl import Workbook
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter as COL

from .profiles import EXTRA_UNITS
from .xlsx_safe import put, text

# ---- the workbook's look (theme colours of the original, as RGB) ----
ANG = "Angsana New"
CAL = "Calibri"
F_HEAD = Font(name=ANG, size=14, bold=True)
F_CELL = Font(name=ANG, size=14)
F_NUM = Font(name=CAL, size=11)
F_NUMB = Font(name=CAL, size=11, bold=True)
F_TITLE = Font(name=ANG, size=22, bold=True)
F_H2 = Font(name=ANG, size=18, bold=True)
YELLOW = PatternFill("solid", fgColor="FFFF00")      # input columns (วัตถุดิบ, ความหนา, X, Y, จำนวน)
GREY = PatternFill("solid", fgColor="F2F2F2")        # formula columns
GREY2 = PatternFill("solid", fgColor="D9D9D9")       # เทียบ columns
GOLD = PatternFill("solid", fgColor="FFF2CC")        # ราคารวม
BLUE = PatternFill("solid", fgColor="DEEBF7")        # chosen sheet in the comparison sheet
CF_BLUE = PatternFill("solid", fgColor="DEEBF7", bgColor="DEEBF7")   # chosen arrangement (conditional format)
SHEET_FILLS = ["C5E0B4", "F8CBAD", "BDD7EE", "E2EFDA", "FCE4D6"]
RED = PatternFill("solid", fgColor="FF9999")
ERR = PatternFill("solid", fgColor="FBE7E5")
THIN = Side(style="thin")
MED = Side(style="medium")
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
HBOX = Border(left=MED, right=MED, top=MED, bottom=MED)
CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
VCENTER = Alignment(vertical="center")
ACC0 = '_(* #,##0_);_(* \\(#,##0\\);_(* "-"??_);_(@_)'
ACC2 = '_(* #,##0.00_);_(* \\(#,##0.00\\);_(* "-"??_);_(@_)'
KGFMT = '_(* #,##0.0000000000000_);_(* \\(#,##0.0000000000000\\);_(* "-"??_);_(@_)'

DETAIL = "รายละเอียด"
PRICES = "ราคาเหล็ก"
NOTE = "ค่าแรงและอื่นๆ"


def _put(ws, ref, v, font=F_NUM, fill=None, fmt=None, align=VCENTER, border=BOX):
    c = ws[ref] if isinstance(ref, str) else ws.cell(row=ref[0], column=ref[1])
    put(c, v)
    c.font = font
    if border:
        c.border = border
    if fill:
        c.fill = fill
    if fmt:
        c.number_format = fmt
    c.alignment = align
    return c


def _head(ws, ref, v, fill=None, font=F_HEAD, merge=None):
    if merge:
        ws.merge_cells(merge)
        for row in ws[merge]:
            for c in row:
                c.border = HBOX
                if fill:
                    c.fill = fill
    _put(ws, ref, v, font=font, fill=fill, align=CENTER, border=HBOX)


def sheet_key(s) -> str:
    return f"{s['w']:g}x{s['l']:g}"


def build_pricing_xlsx(project) -> bytes:
    res = project.price()
    P, meta = res["pricing"], res["meta"]
    mats = {m["id"]: m for m in project.store.data["materials"]}
    wb = Workbook()
    S = wb.active
    S.title = "สรุป"
    D = wb.create_sheet(DETAIL)
    R = wb.create_sheet(PRICES)
    N = wb.create_sheet(NOTE)
    C = wb.create_sheet("เทียบขนาดแผ่น")
    date = meta.get("date") or datetime.date.today().isoformat()

    # ================================================================ ค่าแรงและอื่นๆ (Note sheet)
    N.column_dimensions["A"].width = 34
    N.column_dimensions["B"].width = 14
    N.column_dimensions["C"].width = 14
    N.column_dimensions["D"].width = 22
    _put(N, "A1", "คิดค่าแรง / ค่าใช้จ่ายอื่น", font=F_H2, border=None)
    _put(N, "A2", "แก้ตัวเลขช่องสีเหลืองได้ ราคาในหน้ารายละเอียดและหน้าสรุปจะคำนวณใหม่", font=F_CELL, border=None)
    for i, h in enumerate(["รายการ", "อัตรา (บาท)", "คิดต่อ", "ใช้ (1 = คิด, 0 = ไม่คิด)"]):
        _head(N, (4, 1 + i), h, fill=GREY2)
    xr = {}
    r = 5
    for k, e in enumerate(P["extras"]):
        _put(N, (r, 1), text(e["name"]), font=F_CELL)
        _put(N, (r, 2), e["rate"], fill=YELLOW, fmt=ACC2)
        _put(N, (r, 3), EXTRA_UNITS[e["unit"]], font=F_CELL, align=CENTER)
        _put(N, (r, 4), 1 if e["on"] else 0, fill=YELLOW, align=CENTER)
        xr[k] = (f"'{NOTE}'!$B${r}", f"'{NOTE}'!$D${r}")
        r += 1
    r += 1
    settings = [("paint", "ค่าสี (บาท / ตร.ม.)", P["paint_per_m2"]), ("plating", "ค่าชุบ (บาท / กก.)", P["plating_per_kg"]),
                ("markup", "บวกเพิ่ม (%)", P["markup_pct"])]
    cell = {}
    for key, label, val in settings:
        _put(N, (r, 1), label, font=F_CELL)
        _put(N, (r, 2), val, fill=YELLOW, fmt=ACC2)
        cell[key] = f"'{NOTE}'!$B${r}"
        r += 1
    _put(N, (r, 1), "ทาสี / ชุบ ชิ้นไหน: ช่อง “ทาสี” และ “ชุบ” (1 = ใช่, 0 = ไม่) ในหน้ารายละเอียด", font=F_CELL, border=None)
    r += 1
    _put(N, (r, 1), "ค่าสีคิดจากแผ่นคลี่สี่เหลี่ยมเต็ม (X × Y) ไม่หักรู · ค่าจริง (ตามขอบชิ้น ไม่หักรู) แสดงไว้เทียบ", font=F_CELL, border=None)
    r += 2
    _put(N, (r, 1), "เผื่อที่ clamp & วางแบบ อยู่ที่ช่อง R2 ของหน้ารายละเอียด", font=F_CELL, border=None)

    # ================================================================ ราคาเหล็ก
    # One row per steel and thickness. Price per kg and the weight of a 1220x2440 sheet AT THAT THICKNESS;
    # the price of each sheet size follows from them. No per-1-mm values.
    sheets = P["sheets"]
    nS = len(sheets)
    R.sheet_view.zoomScale = 85
    for col, w in zip("ABCD", (14, 14, 16.3, 22)):
        R.column_dimensions[col].width = w
    for i in range(nS):
        R.column_dimensions[COL(5 + i)].width = 15.4
    R.row_dimensions[1].height = R.row_dimensions[2].height = 33
    big = Font(name=ANG, size=20, bold=True)
    mid = Font(name=ANG, size=16, bold=True)
    _head(R, (1, 5), "ราคาต่อแผ่น (ที่ความหนาของแถว)", font=big, merge=f"E1:{COL(4 + nS)}1")
    _head(R, "A2", "Material", font=big)
    _head(R, "B2", "ความหนา (มม.)", font=mid)
    _head(R, "C2", "ราคา/กิโลกรัม", font=mid, fill=YELLOW)
    _head(R, "D2", "น้ำหนักแผ่น 1220x2440 (กก.)", font=mid, fill=YELLOW)
    for i, s_ in enumerate(sheets):
        _put(R, (2, 5 + i), sheet_key(s_), font=F_NUMB, align=CENTER, border=HBOX, fill=None if s_["on"] else GREY2)
    row_of = {}
    pr_ = 3
    rowfont = Font(name=ANG, size=18)
    for p in P["prices"]:
        code = _code(mats, p)
        row_of[(p["material"], round(p["thickness"], 3))] = pr_
        R.row_dimensions[pr_].height = 25.8
        _put(R, (pr_, 1), text(code), font=rowfont, align=Alignment(horizontal="center", vertical="center"))
        _put(R, (pr_, 2), p["thickness"], font=rowfont, align=Alignment(horizontal="center", vertical="center"))
        _put(R, (pr_, 3), p["price_kg"], font=rowfont, fill=YELLOW, fmt=ACC2)
        _put(R, (pr_, 4), p["kg_plate"], font=rowfont, fill=YELLOW, fmt="0.00")
        for i in range(nS):
            L = COL(5 + i)
            _put(R, (pr_, 5 + i), f'=ROUNDUP($D{pr_}/(1220*2440)*LEFT({L}$2,FIND("x",{L}$2)-1)*MID({L}$2,FIND("x",{L}$2)+1,9)*$C{pr_},0)',
                 fmt=ACC0, align=Alignment(horizontal="center"))
        pr_ += 1
    last_price = max(3, pr_ - 1)
    lc = COL(4 + nS)
    _put(R, (pr_ + 1, 1), ("ราคาต่อแผ่น = น้ำหนักแผ่นที่ความหนานั้น × (ขนาดแผ่น ÷ 1220x2440) × ราคา/กก. ปัดขึ้น · "
                           "แก้ราคา/กก. หรือน้ำหนักในช่องสีเหลืองได้ ทั้งไฟล์จะคำนวณใหม่"), font=F_CELL, border=None)
    TBL = f"{PRICES}!$A$3:${lc}${last_price}"
    HDR = f"{PRICES}!$A$2:${lc}$2"
    KG = f"{PRICES}!$A$3:$D${last_price}"

    # ================================================================ รายละเอียด (main sheet of the workbook)
    widths = {"A": 10.9, "B": 11, "C": 8.6, "D": 10, "E": 10, "F": 11.4, "G": 12.7, "H": 10, "I": 9, "J": 8.9, "K": 8,
              "L": 8.9, "M": 11, "N": 11.7, "O": 8.9, "P": 3, "Q": 3, "R": 6, "S": 6, "T": 5.7, "U": 8.7, "V": 6,
              "W": 5.7, "X": 10, "Y": 10, "Z": 26, "AA": 8, "AB": 8}
    for k, v in widths.items():
        D.column_dimensions[k].width = v
    extras = [(k, e) for k, e in enumerate(P["extras"]) if e["unit"] != "job"]
    PT, PL = "AC", "AD"                                               # ticks: ทาสี / ชุบ (1 or 0)
    ex0 = 31                                                         # AE: one column per extra cost
    ex_total = ex0 + len(extras)
    use_col = ex_total + 1                                           # ใช้เนื้อแผ่น %, เศษ %, ใช้กี่แผ่น
    rv_col = use_col + 3                                             # real vs flat: area, paint, plating
    note_col = rv_col + 6
    for i in range(len(extras) + 11):
        D.column_dimensions[COL(ex0 + i)].width = 12
    D.column_dimensions[COL(note_col)].width = 40
    _put(D, "A1", f"ใบคิดราคา {project.job_name}", font=Font(name=ANG, size=18, bold=True), border=None)
    _put(D, "A2", text(f"{meta.get('customer') or ''}  วันที่ {date}".strip()), font=F_CELL, border=None)
    D.merge_cells("R1:T1")
    D.merge_cells("R2:T2")
    _put(D, "R1", "เผื่อที่ clamp & วางแบบ", font=F_NUMB, fill=GREY, align=CENTER, border=HBOX)
    _put(D, "R2", P["margin"], align=CENTER, border=HBOX, fill=YELLOW)
    D.row_dimensions[4].height = D.row_dimensions[5].height = 30
    _head(D, "A4", "NO.", merge="A4:A5")
    _head(D, "B4", "วัตถุดิบ", fill=YELLOW, merge="B4:B5")
    _head(D, "C4", "ความหนา\nmm", fill=YELLOW, merge="C4:C5")
    _head(D, "D4", "SIZE", merge="D4:E4")
    _head(D, "D5", "X", fill=YELLOW)
    _head(D, "E5", "Y", fill=YELLOW)
    _head(D, "F4", "แผ่นที่ใช้", fill=GREY, font=F_NUMB, merge="F4:G4")
    _head(D, "F5", "แผ่นที่ใช้", fill=GREY, font=F_NUMB)
    _head(D, "G5", "ราคาต่อแผ่น", fill=GREY, font=F_NUMB)
    for ref, label, fill in (("H", "ค่าโลหะ", GREY), ("I", "ค่าสี", GREY), ("J", "ค่าชุบ", GREY), ("K", "จำนวน", YELLOW),
                             ("L", "น้ำหนัก\n(kg)", GREY), ("M", "ราคารวม", GOLD), ("N", "ราคาต่อแผ่น\n(เทียบ)", GREY2),
                             ("O", "ราคารวม\n(เทียบ)", GREY2)):
        _head(D, f"{ref}4", label, fill=fill, merge=f"{ref}4:{ref}5")
    _head(D, "R4", "เรียงแบบ 1", fill=GREY, merge="R4:T4")
    _head(D, "U4", "เรียงแบบ 2", fill=GREY, merge="U4:W4")
    for ref, label in (("R5", "X"), ("S5", "Y"), ("T5", "จำนวน"), ("U5", "Y"), ("V5", "X"), ("W5", "จำนวน")):
        _head(D, ref, label, fill=GREY)
    _head(D, "X5", "Total Dimension", fill=GREY, merge="X5:Y5")
    _head(D, "Z4", "ชิ้นส่วน", merge="Z4:Z5")
    _head(D, "AA4", "จุดพับ", merge="AA4:AA5")
    _head(D, "AB4", "รู", merge="AB4:AB5")
    _head(D, f"{PT}4", "ทาสี\n(1/0)", fill=YELLOW, merge=f"{PT}4:{PT}5")
    _head(D, f"{PL}4", "ชุบ\n(1/0)", fill=YELLOW, merge=f"{PL}4:{PL}5")
    for i, (k, e) in enumerate(extras):
        L = COL(ex0 + i)
        _head(D, f"{L}4", text(f"{e['name']}\n({e['rate']:g}/{EXTRA_UNITS[e['unit']]})"), fill=GREY, merge=f"{L}4:{L}5")
    _head(D, f"{COL(ex_total)}4", "ค่าใช้จ่ายอื่น\n/ชิ้น", fill=GREY, merge=f"{COL(ex_total)}4:{COL(ex_total)}5")
    _head(D, f"{COL(use_col)}4", "การใช้แผ่นเหล็ก", fill=GREY, merge=f"{COL(use_col)}4:{COL(use_col + 2)}4")
    _head(D, f"{COL(use_col)}5", "ใช้ (%)", fill=GREY)
    _head(D, f"{COL(use_col + 1)}5", "เศษเหลือ (%)", fill=GREY)
    _head(D, f"{COL(use_col + 2)}5", "ใช้ (แผ่น)", fill=GREY)
    for j, (top, cols) in enumerate((("พื้นที่ (ตร.ม.)", ("จริง", "คลี่")), ("ค่าสี / ชิ้น", ("จริง", "คลี่")),
                                     ("ค่าชุบ รวม", ("จริง", "คลี่")))):
        c0 = rv_col + 2 * j
        _head(D, f"{COL(c0)}4", top, fill=GREY, merge=f"{COL(c0)}4:{COL(c0 + 1)}4")
        _head(D, f"{COL(c0)}5", cols[0], fill=GREY)
        _head(D, f"{COL(c0 + 1)}5", cols[1], fill=GOLD)
    _head(D, f"{COL(note_col)}4", "หมายเหตุ", merge=f"{COL(note_col)}4:{COL(note_col)}5")
    D.freeze_panes = "B6"

    first = 6
    r = first
    EXT = COL(ex_total)
    for n, row in enumerate(res["rows"], 1):
        sh = row["sheet"]
        D.row_dimensions[r].height = 19.95
        _put(D, (r, 1), n, font=F_CELL, align=CENTER)
        _put(D, (r, 2), text(row["code"]), font=F_CELL, align=CENTER)
        _put(D, (r, 3), row["t"], font=F_CELL, align=CENTER)
        _put(D, (r, 4), row["x"], font=F_CELL, fmt="0.00", align=CENTER)
        _put(D, (r, 5), row["y"], font=F_CELL, fmt="0.00", align=CENTER)
        _put(D, (r, 11), row["qty"], fmt=ACC0, align=CENTER)
        _put(D, (r, 26), text(row["name"]), font=F_CELL)
        _put(D, (r, 27), row["bends"], align=CENTER)
        _put(D, (r, 28), row["holes"], align=CENTER)
        _put(D, f"{PT}{r}", 1 if row["paint_on"] else 0, fill=YELLOW, align=CENTER)
        _put(D, f"{PL}{r}", 1 if row["plate_on"] else 0, fill=YELLOW, align=CENTER)
        if sh is None:
            _put(D, (r, 6), "ตรวจดู", fill=ERR, align=CENTER)
            for c in (7, 8, 9, 10, 12, 13):
                _put(D, (r, c), None, fill=ERR)
            _put(D, (r, note_col), row["error"], font=F_CELL, fill=ERR)
            r += 1
            continue
        _put(D, (r, 6), sheet_key(sh), fill=GREY, align=CENTER)
        _put(D, (r, 7), f'=IFERROR(IF(AND(F{r}<>"",B{r}<>""),VLOOKUP(B{r},{TBL},MATCH(F{r},{HDR},0),FALSE),""),"")', fill=GREY, fmt=ACC0)
        _put(D, (r, 8), f'=IFERROR(ROUNDUP(G{r}/MAX(T{r},W{r}),0),"")', fill=GREY, fmt=ACC0)
        _put(D, (r, 9), f'=IF({PT}{r}=1,IF(OR(D{r}="",E{r}=""),"",ROUNDUP(D{r}*E{r}*{cell["paint"]}/1000000,0)),0)', fill=GREY, fmt=ACC0)
        _put(D, (r, 10), f'=IF(AND({PL}{r}=1,L{r}<>""),L{r}*{cell["plating"]},0)', fill=GREY, fmt=ACC2)
        _put(D, (r, 12), f'=IFERROR(VLOOKUP(B{r},{KG},4,FALSE)/(1220*2440)*D{r}*E{r}*K{r},"")', fill=GREY, fmt=ACC2)
        _put(D, (r, 13), f'=SUM(H{r}:I{r})*K{r}+{EXT}{r}*K{r}+J{r}', fill=GOLD, fmt=ACC0)
        _put(D, (r, 14), None, fill=GREY2, fmt=ACC0)
        _put(D, (r, 15), f'=IF(N{r}="","",(IFERROR(ROUNDUP(N{r}/MAX(T{r},W{r}),0),"")+IF({PT}{r}=1,ROUNDUP(D{r}*E{r}*{cell["paint"]}/1000000,0),0))*K{r})',
             fill=GREY2, fmt=ACC0)
        W_ = f'LEFT(F{r},FIND("x",F{r})-1)'
        L_ = f'RIGHT(F{r},LEN(F{r})-FIND("x",F{r}))'
        _put(D, (r, 18), f'=IF(F{r}<>"", ROUNDDOWN({W_}/(D{r}+$R$2),0),"")')
        _put(D, (r, 19), f'=IF(F{r}<>"", ROUNDDOWN({L_}/(E{r}+$R$2),0),"")')
        _put(D, (r, 20), f'=IFERROR(R{r}*S{r},"")')
        _put(D, (r, 21), f'=IF(F{r}<>"", ROUNDDOWN({W_}/(E{r}+$R$2),0),"")')
        _put(D, (r, 22), f'=IF(F{r}<>"", ROUNDDOWN({L_}/(D{r}+$R$2),0),"")')
        _put(D, (r, 23), f'=IFERROR(U{r}*V{r},"")')
        _put(D, (r, 24), f'=IFERROR(IF(T{r}>=W{r}, R{r}*(D{r}+$R$2),U{r}*(E{r}+$R$2)),"")', fmt="0")
        _put(D, (r, 25), f'=IFERROR(IF(T{r}>=W{r}, S{r}*(E{r}+$R$2),V{r}*(D{r}+$R$2)),"")', fmt="0")
        for i, (k, e) in enumerate(extras):
            rate, on = xr[k]
            qty_expr = {"piece": "1", "bend": f"AA{r}", "hole": f"AB{r}", "kg": f"L{r}/K{r}",
                        "m2": f"D{r}*E{r}/1000000"}[e["unit"]]
            _put(D, (r, ex0 + i), f"=IF({on}=1,{rate}*{qty_expr},0)", fill=GREY, fmt=ACC2)
        _put(D, (r, ex_total), f"=SUM({COL(ex0)}{r}:{COL(ex_total - 1)}{r})" if extras else 0, fill=GREY, fmt=ACC2)
        U, Ux, Un = COL(use_col), COL(use_col + 1), COL(use_col + 2)
        _put(D, (r, use_col), f'=IFERROR(MAX(T{r},W{r})*D{r}*E{r}/({W_}*{L_}),"")', fill=GREY, fmt="0.0%")
        _put(D, (r, use_col + 1), f'=IFERROR(1-{U}{r},"")', fill=GREY, fmt="0.0%")
        _put(D, (r, use_col + 2), f'=IFERROR(K{r}/MAX(T{r},W{r}),"")', fill=GREY, fmt="0.000")
        A1, A2 = COL(rv_col), COL(rv_col + 1)
        _put(D, (r, rv_col), round(row["area_real"] / 1e6, 6), fill=GREY, fmt="0.0000")             # outline, holes kept
        _put(D, (r, rv_col + 1), f"=D{r}*E{r}/1000000", fill=GOLD, fmt="0.0000")                 # full flat rectangle
        _put(D, (r, rv_col + 2), f'=IF({PT}{r}=1,ROUNDUP({A1}{r}*{cell["paint"]},0),0)', fill=GREY, fmt=ACC0)
        _put(D, (r, rv_col + 3), f"=I{r}", fill=GOLD, fmt=ACC0)
        _put(D, (r, rv_col + 4), f'=IF({PL}{r}=1,IFERROR(VLOOKUP(B{r},{KG},4,FALSE)/(1220*2440)*{A1}{r}*1000000*K{r}*{cell["plating"]},0),0)',
             fill=GREY, fmt=ACC2)
        _put(D, (r, rv_col + 5), f"=J{r}", fill=GOLD, fmt=ACC2)
        _put(D, (r, note_col), "", font=F_CELL)
        r += 1
    last = r - 1
    # totals
    _put(D, (r, 2), "รวม", font=F_HEAD, fill=GOLD, align=CENTER)
    for c, fmt in ((11, ACC0), (12, ACC2), (13, ACC0)):
        L = COL(c)
        _put(D, (r, c), f"=SUM({L}{first}:{L}{last})" if last >= first else 0, font=F_NUMB, fill=GOLD, fmt=fmt)
    total_row = r
    if last >= first:
        D.conditional_formatting.add(f"R{first}:T{last}", FormulaRule(formula=[f"$T{first}=MAX($T{first},$W{first})"], fill=CF_BLUE))
        D.conditional_formatting.add(f"U{first}:W{last}", FormulaRule(formula=[f"$W{first}>$T{first}"], fill=CF_BLUE))
        for i, s in enumerate(sheets):
            D.conditional_formatting.add(f"F{first}:F{last}", FormulaRule(
                formula=[f'$F{first}="{sheet_key(s)}"'], fill=PatternFill("solid", bgColor=SHEET_FILLS[i % len(SHEET_FILLS)])))
        wx = f'VALUE(LEFT($F{first},FIND("x",$F{first})-1))'
        lx = f'VALUE(MID($F{first},FIND("x",$F{first})+1,9))'
        D.conditional_formatting.add(f"X{first}:X{last}", FormulaRule(formula=[
            f"AND(IF(T{first}>=W{first}, (R{first}+1)*(D{first}+$R$2),(U{first}+1)*(E{first}+$R$2))<({wx}+10),IF(T{first}>=W{first},D{first},E{first})>=100)"],
            fill=PatternFill("solid", bgColor="FF9999")))
        D.conditional_formatting.add(f"Y{first}:Y{last}", FormulaRule(formula=[
            f"AND(IF(T{first}>=W{first}, (S{first}+1)*(E{first}+$R$2),(V{first}+1)*(D{first}+$R$2))<={lx}+10,IF(T{first}>=W{first},E{first},D{first})>=100)"],
            fill=PatternFill("solid", bgColor="FF9999")))
    _put(D, (total_row + 2, 1), ("แผ่นที่ใช้ = ขนาดที่ SheetFlat เลือก (ลองทุกขนาดที่มี ทั้งเรียงแบบ 1 และ 2 แล้วเลือกที่ค่าโลหะต่อชิ้นถูกที่สุด) "
                                 "· ช่องสีฟ้า = แบบที่วางได้มากกว่า · ใส่ราคาเทียบในช่อง N ได้เหมือนเดิม "
                                 "· การใช้แผ่นเหล็ก: ใช้ (%) = ชิ้นต่อแผ่น × X × Y ÷ พื้นที่แผ่น, ใช้ (แผ่น) = จำนวน ÷ ชิ้นต่อแผ่น "
                                 "· ทาสี/ชุบ: ใส่ 1 หรือ 0 ได้ · ราคาใช้ค่าคลี่ (สี่เหลี่ยมเต็ม ไม่หักรู) ค่าจริง (ตามขอบชิ้น ไม่หักรู) แสดงไว้เทียบ"), font=F_CELL, border=None)

    # ================================================================ สรุป (first page)
    S.sheet_view.showGridLines = False
    for col, w in zip("ABCDEFGHIJKL", (22, 14, 12, 14, 14, 14, 14, 14, 14, 24, 14, 14)):
        S.column_dimensions[col].width = w
    _put(S, "A1", "ใบคิดราคา", font=F_TITLE, border=None)
    info = [("ชื่องาน", text(project.job_name)), ("ลูกค้า", text(meta.get("customer", ""))), ("วันที่", text(date)),
            ("ไฟล์ต้นฉบับ", text(project.source_name)), ("หมายเหตุ", text(meta.get("note", "")))]
    r = 3
    for k, v in info:
        _put(S, (r, 1), k, font=F_HEAD, border=None)
        _put(S, (r, 2), v, font=F_CELL, border=None)
        S.merge_cells(start_row=r, start_column=2, end_row=r, end_column=6)
        r += 1
    r += 1
    _put(S, (r, 1), "ราคาเหล็กที่ใช้ในงานนี้", font=F_H2, border=None)
    r += 1
    on_sheets = [(i, s) for i, s in enumerate(sheets) if s["on"]]
    heads = ["วัตถุดิบ", "ความหนา (มม.)", "ราคา/กก.", "น้ำหนักแผ่น 1220x2440"] + [f"ราคาแผ่น {sheet_key(s)}" for _, s in on_sheets]
    for i, h in enumerate(heads):
        _head(S, (r, 1 + i), h, fill=GREY2)
    r += 1
    used = []
    for g in res["groups"]:
        k = (g["material_id"], round(g["thickness"], 3))
        if k not in used:
            used.append(k)
    for mid, t in used:
        prow = row_of.get((mid, t))
        _put(S, (r, 1), f"={PRICES}!A{prow}" if prow else text(mid), font=F_CELL, align=CENTER)
        _put(S, (r, 2), t, font=F_CELL, align=CENTER)
        _put(S, (r, 3), f"={PRICES}!C{prow}" if prow else None, fmt=ACC2)
        _put(S, (r, 4), f"={PRICES}!D{prow}" if prow else None, fmt="0.00")
        for j, (i, s) in enumerate(on_sheets):
            _put(S, (r, 5 + j), f"={PRICES}!{COL(5 + i)}{prow}" if prow else None, fmt=ACC0)
        r += 1
    _put(S, (r, 1), "ราคาและน้ำหนักเป็นของแผ่นที่ความหนานั้น (แก้ได้ในหน้า ราคาเหล็ก)", font=F_CELL, border=None)
    r += 2
    _put(S, (r, 1), "สรุปราคาแยกตามวัตถุดิบ", font=F_H2, border=None)
    r += 1
    heads = ["กลุ่ม / วัตถุดิบ", "ความหนา (มม.)", "จำนวนชิ้น", "น้ำหนัก (กก.)", "ค่าโลหะ", "ค่าสี", "ค่าใช้จ่ายอื่น", "ค่าชุบ",
             "ราคารวม", "แผ่นที่ต้องใช้", "ใช้เนื้อแผ่น (%)", "เศษเหลือ (%)"]
    for i, h in enumerate(heads):
        _head(S, (r, 1 + i), h, fill=GOLD if h == "ราคารวม" else GREY2)
    r += 1
    rng = lambda L: f"'{DETAIL}'!${L}${first}:${L}${max(last, first)}"  # noqa: E731
    g0 = r
    for g in res["groups"]:
        code = '"' + g["code"].replace('"', '""') + '"'             # detail column B holds the steel code
        label = g["code"] if g.get("name", g["code"]) == g["code"] else f'{g["name"]} ({g["code"]})'
        _put(S, (r, 1), text(label), font=F_HEAD, align=CENTER)
        _put(S, (r, 2), g["thickness"], font=F_CELL, align=CENTER)
        _put(S, (r, 3), f"=SUMIF({rng('B')},{code},{rng('K')})", fmt=ACC0)
        _put(S, (r, 4), f"=SUMIF({rng('B')},{code},{rng('L')})", fmt=ACC2)
        _put(S, (r, 5), f"=SUMPRODUCT(({rng('B')}={code})*N(+{rng('H')})*{rng('K')})", fmt=ACC0)
        _put(S, (r, 6), f"=SUMPRODUCT(({rng('B')}={code})*N(+{rng('I')})*{rng('K')})", fmt=ACC0)
        _put(S, (r, 7), f"=SUMPRODUCT(({rng('B')}={code})*N(+{rng(EXT)})*{rng('K')})", fmt=ACC2)
        _put(S, (r, 8), f"=SUMIF({rng('B')},{code},{rng('J')})", fmt=ACC2)
        _put(S, (r, 9), f"=SUMIF({rng('B')},{code},{rng('M')})", fill=GOLD, fmt=ACC2)
        _put(S, (r, 10), ", ".join(f"{k} × {v}" for k, v in g["sheets"].items()) or "-", font=F_CELL)
        _put(S, (r, 11), g["use"], fmt="0.0%")
        _put(S, (r, 12), (1 - g["use"]) if g["sheet_area"] else None, fmt="0.0%")
        r += 1
    g1 = r - 1
    job_rows = []
    for e_i, e in enumerate(P["extras"]):
        if e["unit"] != "job":
            continue
        rate, on = xr[e_i]
        _put(S, (r, 1), text(e["name"]), font=F_CELL)
        _put(S, (r, 2), "ต่องาน", font=F_CELL, align=CENTER)
        _put(S, (r, 9), f"=IF({on}=1,{rate},0)", fill=GOLD, fmt=ACC2)
        job_rows.append(r)
        r += 1
    _put(S, (r, 1), "รวม", font=F_HEAD, fill=GOLD, align=CENTER)
    for c, fmt in ((3, ACC0), (4, ACC2), (5, ACC0), (6, ACC0), (7, ACC2), (8, ACC2), (9, ACC2)):
        L = COL(c)
        parts = [f"SUM({L}{g0}:{L}{g1})"] if g1 >= g0 else []
        if c == 9:
            parts += [f"{L}{j}" for j in job_rows]
        f = "+".join(parts)
        if f and c == 9:
            f = f"ROUND({f},2)"
        _put(S, (r, c), "=" + f if parts else 0, font=F_NUMB, fill=GOLD, fmt=fmt)
    tot = r
    r += 1
    _put(S, (r, 8), "บวกเพิ่ม (%)", font=F_HEAD, align=CENTER)
    _put(S, (r, 9), f"=ROUND(I{tot}*{cell['markup']}/100,2)", fmt=ACC2)
    r += 1
    _put(S, (r, 8), "ราคารวมสุทธิ", font=F_HEAD, fill=GOLD, align=CENTER)
    _put(S, (r, 9), f"=ROUND(I{tot}+I{tot + 1},2)", font=Font(name=CAL, size=13, bold=True), fill=GOLD, fmt=ACC2)
    r += 2
    if res["errors"]:
        _put(S, (r, 1), "ชิ้นที่ยังคิดราคาไม่ได้ (แถวสีแดงในหน้ารายละเอียด)", font=F_HEAD, border=None)
        for e in sorted(set(res["errors"])):
            r += 1
            _put(S, (r, 1), "• " + e, font=F_CELL, border=None)
        r += 2
    excl = [p for p in project.parts if not p.include]
    if excl:
        _put(S, (r, 1), "ชิ้นที่ไม่ได้คิดราคา (ไม่นำไปตัด)", font=F_H2, border=None)
        r += 1
        for i, h in enumerate(["ชิ้นส่วน", "จำนวน", "กลุ่ม / เหตุผล"]):
            _head(S, (r, 1 + i), h, fill=GREY2)
        r += 1
        for p in excl:
            _put(S, (r, 1), text(p.name), font=F_CELL)
            _put(S, (r, 2), p.qty, align=CENTER)
            _put(S, (r, 3), text(p.category or p.auto_reason or "ผู้ใช้เลือกไม่ตัด"), font=F_CELL)
            S.merge_cells(start_row=r, start_column=3, end_row=r, end_column=8)
            r += 1

    # ================================================================ เทียบขนาดแผ่น
    heads = ["ชิ้นส่วน", "X (มม.)", "Y (มม.)", "แผ่น", "มีแผ่น", "เรียงแบบ 1", "เรียงแบบ 2", "ชิ้น/แผ่น", "ราคาแผ่น",
             "ค่าโลหะ / ชิ้น", "เลือก"]
    for i, h in enumerate(heads):
        _head(C, (1, 1 + i), h, fill=GREY2)
    for col, w in zip("ABCDEFGHIJK", (26, 10, 10, 12, 8, 16, 16, 10, 12, 13, 8)):
        C.column_dimensions[col].width = w
    C.freeze_panes = "B2"
    rc = 2
    m_val = P["margin"]
    for row in res["rows"]:
        for o in row["options"]:
            pick = bool(row["sheet"]) and o["w"] == row["sheet"]["w"] and o["l"] == row["sheet"]["l"]
            fill = BLUE if pick else None
            x, y = row["x"], row["y"]
            a1 = f"{int(o['w'] // (x + m_val))} × {int(o['l'] // (y + m_val))} = {o['count1']}"
            a2 = f"{int(o['w'] // (y + m_val))} × {int(o['l'] // (x + m_val))} = {o['count2']}"
            vals = [text(row["name"]), x, y, sheet_key(o), "มี" if o["on"] else "ไม่มี", a1, a2, o["count"], o["sheet_price"],
                    o["metal"], "✓" if pick else ""]
            for c, v in enumerate(vals, 1):
                _put(C, (rc, c), v, font=F_CELL if c == 1 else F_NUM, fill=fill, fmt=ACC0 if c in (9, 10) else None)
            rc += 1
    _put(C, (rc + 1, 1), f"ทุกชิ้นเผื่อ {m_val:g} มม. · เลือกแผ่นและแบบที่ค่าโลหะต่อชิ้นต่ำสุด (เท่ากันเลือกแผ่นใหญ่)",
         font=F_CELL, border=None)

    wb.calculation.fullCalcOnLoad = True
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _code(mats: dict, p: dict) -> str:
    m = mats.get(p["material"], {"prefix": p["material"]})
    return f"{m['prefix']}{p['gauge']}" if p.get("gauge") else f"{m['prefix']}#{p['thickness']:g}mm"
