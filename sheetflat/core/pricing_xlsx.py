"""Price workbook ("ใบคิดราคา"), laid out like the shop's สูตรคิดราคา workbook.

Sheet 1  สรุปราคา      job details, date, the steel prices and costs used, totals per DXF group
Sheet 2  รายละเอียด    one row per flat piece; every money cell is a live formula that reads the
                        prices on sheet 1, so changing a price there updates the whole workbook
Sheet 3  เทียบขนาดแผ่น  every sheet size x both arrangements for every piece, and which one was chosen

The sheet size and arrangement are chosen by SheetFlat (cheapest metal per piece) and written as values.
"""
from __future__ import annotations

import datetime
import io

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

F = "Tahoma"
TITLE = Font(name=F, size=16, bold=True)
H2 = Font(name=F, size=12, bold=True)
BOLD = Font(name=F, size=10, bold=True)
BASE = Font(name=F, size=10)
HEAD_FILL = PatternFill("solid", fgColor="1F2A37")
HEAD_FONT = Font(name=F, size=10, bold=True, color="FFFFFF")
INPUT_FILL = PatternFill("solid", fgColor="FFF7D6")       # cells the user may change
TOTAL_FILL = PatternFill("solid", fgColor="E4F3EA")
ERR_FILL = PatternFill("solid", fgColor="FBE7E5")
PICK_FILL = PatternFill("solid", fgColor="E0F1F3")
THIN = Side(style="thin", color="C9CED6")
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
MONEY = '#,##0.00'
MONEY0 = '#,##0'


def _cell(ws, r, c, v, font=BASE, fill=None, fmt=None, align=None):
    x = ws.cell(row=r, column=c, value=v)
    x.font, x.border = font, BOX
    if fill:
        x.fill = fill
    if fmt:
        x.number_format = fmt
    x.alignment = align or Alignment(vertical="center")
    return x


def _head(ws, r, labels, c0=1):
    for i, h in enumerate(labels):
        x = ws.cell(row=r, column=c0 + i, value=h)
        x.font, x.fill, x.border = HEAD_FONT, HEAD_FILL, BOX
        x.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[r].height = 32


def _widths(ws, ws_widths):
    for i, w in enumerate(ws_widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w


def build_pricing_xlsx(project) -> bytes:
    res = project.price()
    P, meta = res["pricing"], res["meta"]
    wb = Workbook()
    S = wb.active
    S.title = "สรุปราคา"
    D = wb.create_sheet("รายละเอียด")
    C = wb.create_sheet("เทียบขนาดแผ่น")
    sname = "สรุปราคา"
    ref = lambda cell: f"'{sname}'!{cell}"  # noqa: E731

    # ================================================================ sheet 1: header
    _widths(S, [26, 14, 14, 14, 16, 16, 16, 16, 16, 16, 16])
    S["A1"] = f"ใบคิดราคา {project.job_name}"
    S["A1"].font = TITLE
    date = meta.get("date") or datetime.date.today().isoformat()
    info = [("ชื่องาน", project.job_name), ("ลูกค้า", meta.get("customer", "")), ("วันที่", date),
            ("ไฟล์ต้นฉบับ", project.source_name), ("หมายเหตุ", meta.get("note", ""))]
    r = 3
    for k, v in info:
        S.cell(row=r, column=1, value=k).font = BOLD
        S.cell(row=r, column=2, value=v).font = BASE
        r += 1

    # ---------------------------------------------------------------- costs used
    r += 1
    S.cell(row=r, column=1, value="ค่าใช้จ่ายที่ใช้คิด (แก้ช่องสีเหลืองได้ ราคาทั้งไฟล์จะคำนวณใหม่)").font = H2
    r += 1
    params = [
        ("margin", "เผื่อที่ clamp และระยะวางแบบ (มม. ต่อชิ้น)", P["margin"]),
        ("paint", "ค่าสี (บาท / ตร.ม.)", P["paint_per_m2"]),
        ("bend", "ค่าพับ (บาท / จุด)", P["bend_price"]),
        ("labor", "ค่าแรงอื่น (บาท / ชิ้น)", P["labor_per_piece"]),
        ("plating", "ค่าชุบ (บาท / กก.)", P["plating_per_kg"]),
        ("incplate", "รวมค่าชุบในราคา (1 = รวม, 0 = ไม่รวม)", 1 if P["include_plating"] else 0),
        ("markup", "บวกเพิ่ม (%)", P["markup_pct"]),
    ]
    cell_of = {}
    for key, label, val in params:
        _cell(S, r, 1, label)
        _cell(S, r, 2, val, fill=INPUT_FILL)
        cell_of[key] = ref(f"$B${r}")
        r += 1
    _cell(S, r, 1, "สีไม่ทาสำหรับ")
    _cell(S, r, 2, ", ".join(P["no_paint"]) or "-")
    r += 2

    # ---------------------------------------------------------------- steel prices
    S.cell(row=r, column=1, value="ราคาเหล็ก").font = H2
    r += 1
    on = [s for s in P["sheets"] if s["on"]]
    _head(S, r, ["วัสดุ", "หนาตั้งแต่ (มม.)", "หนาถึง (มม.)", "ราคา / กก.", "กก./ตร.ม. ที่หนา 1 มม."] +
          [f"ราคาแผ่น {s['w']:g}x{s['l']:g} (หนา 1 มม.)" for s in on])
    r += 1
    price_cells = []
    for pr in P["prices"]:
        _cell(S, r, 1, pr["material"])
        _cell(S, r, 2, pr["t_min"] or None, fill=INPUT_FILL)
        _cell(S, r, 3, pr["t_max"] or None, fill=INPUT_FILL)
        _cell(S, r, 4, pr["price_kg"], fill=INPUT_FILL, fmt=MONEY)
        _cell(S, r, 5, pr["kg_m2_mm"], fill=INPUT_FILL, fmt="0.0000")
        for i, s in enumerate(on):
            _cell(S, r, 6 + i, f"=ROUNDUP({s['w']:g}*{s['l']:g}/1000000*E{r}*D{r},0)", fmt=MONEY0)
        price_cells.append((pr, ref(f"$D${r}"), ref(f"$E${r}")))
        r += 1
    S.cell(row=r, column=1, value="ราคาแผ่นจริง = ราคาแผ่นหนา 1 มม. × ความหนา · แถวที่ไม่ระบุช่วงความหนาใช้ได้ทุกความหนา").font = BASE
    r += 2

    def price_ref(material, t):
        for pr, pc, kc in price_cells:
            if pr["material"] != material:
                continue
            if pr["t_min"] and t < pr["t_min"] - 1e-9:
                continue
            if pr["t_max"] and t > pr["t_max"] + 1e-9:
                continue
            return pc, kc
        return None, None

    # ================================================================ sheet 2: detail rows
    cols = ["ลำดับ", "ชิ้นส่วน", "กลุ่ม", "วัสดุ", "ความหนา (มม.)", "X (มม.)", "Y (มม.)", "จำนวน",
            "แผ่นที่ใช้", "แผ่น กว้าง", "แผ่น ยาว", "ราคาต่อแผ่น", "เรียงแบบ 1 (ชิ้น/แผ่น)", "เรียงแบบ 2 (ชิ้น/แผ่น)",
            "ชิ้น/แผ่น", "การวาง", "ค่าโลหะ / ชิ้น", "ค่าสี / ชิ้น", "รอยพับ", "ค่าแรง / ชิ้น", "ค่าชุบ / ชิ้น",
            "น้ำหนัก (กก.)", "ราคา / ชิ้น", "ราคารวม", "หมายเหตุ"]
    _head(D, 1, cols)
    _widths(D, [6, 26, 11, 8, 10, 10, 10, 8, 12, 9, 9, 12, 12, 12, 9, 22, 12, 11, 8, 12, 11, 11, 12, 13, 40])
    D.freeze_panes = "C2"
    m, paint, bend, labor, plating, incplate = (cell_of[k] for k in ("margin", "paint", "bend", "labor", "plating", "incplate"))
    r2 = 2
    for n, row in enumerate(res["rows"], 1):
        pc, kc = price_ref(row["material"], row["t"])
        sh = row["sheet"]
        err = row["error"]
        vals = [n, row["name"], row["code"], row["material"], row["t"], row["x"], row["y"], row["qty"]]
        for c, v in enumerate(vals, 1):
            _cell(D, r2, c, v)
        if sh and pc:
            R = r2
            arr = f"แบบ {sh['arr']}: {sh['nx']} × {sh['ny']}" + (" (หมุน 90°)" if sh["arr"] == 2 else "")
            _cell(D, R, 9, f"{sh['w']:g}x{sh['l']:g}", fill=PICK_FILL)
            _cell(D, R, 10, sh["w"])
            _cell(D, R, 11, sh["l"])
            _cell(D, R, 12, f"=ROUNDUP(J{R}*K{R}/1000000*{kc}*{pc},0)*E{R}", fmt=MONEY)
            _cell(D, R, 13, f"=INT(J{R}/(F{R}+{m}))*INT(K{R}/(G{R}+{m}))")
            _cell(D, R, 14, f"=INT(J{R}/(G{R}+{m}))*INT(K{R}/(F{R}+{m}))")
            _cell(D, R, 15, f"=MAX(M{R},N{R})")
            _cell(D, R, 16, arr)
            _cell(D, R, 17, f"=ROUNDUP(L{R}/O{R},0)", fmt=MONEY0)
            nopaint = row["material"] in P["no_paint"]
            _cell(D, R, 18, 0 if nopaint else f"=ROUNDUP(F{R}*G{R}*{paint}/1000000,0)", fmt=MONEY0)
            _cell(D, R, 19, row["bends"])
            _cell(D, R, 20, f"=S{R}*{bend}+{labor}", fmt=MONEY)
            _cell(D, R, 21, f"={kc}*E{R}*F{R}*G{R}/1000000*{plating}", fmt=MONEY)
            _cell(D, R, 22, f"={kc}*E{R}*F{R}*G{R}*H{R}/1000000", fmt="0.000")
            _cell(D, R, 23, f"=Q{R}+R{R}+T{R}+IF({incplate}=1,U{R},0)", fmt=MONEY)
            _cell(D, R, 24, f"=W{R}*H{R}", fmt=MONEY)
            _cell(D, R, 25, "")
        else:
            for c in range(9, 25):
                _cell(D, r2, c, 0 if c in (22, 24) else None, fill=ERR_FILL)
            _cell(D, r2, 25, err or "ไม่มีราคา", fill=ERR_FILL)
        r2 += 1
    last = r2 - 1
    _cell(D, r2, 2, "รวม", font=BOLD, fill=TOTAL_FILL)
    for c in (8, 22, 24):
        L = get_column_letter(c)
        _cell(D, r2, c, f"=SUM({L}2:{L}{last})" if last >= 2 else 0, font=BOLD, fill=TOTAL_FILL,
              fmt={8: "0", 22: "0.000", 24: MONEY}[c])

    # ================================================================ sheet 1: totals per group
    S.cell(row=r, column=1, value="สรุปราคาแยกตามไฟล์ DXF (วัสดุ + ความหนา)").font = H2
    r += 1
    _head(S, r, ["กลุ่ม", "วัสดุ", "ความหนา (มม.)", "จำนวนชิ้น", "น้ำหนัก (กก.)", "ค่าโลหะ", "ค่าสี", "ค่าแรง",
                 "ค่าชุบ", "ราคารวม", "แผ่นที่ต้องใช้ (ประมาณ)"])
    r += 1
    g0 = r
    dr = f"'รายละเอียด'!$C$2:$C${max(last, 2)}"
    col = lambda L: f"'รายละเอียด'!${L}$2:${L}${max(last, 2)}"  # noqa: E731
    for g in res["groups"]:
        _cell(S, r, 1, g["code"], font=BOLD)
        _cell(S, r, 2, g["material_id"])
        _cell(S, r, 3, g["thickness"])
        _cell(S, r, 4, f"=SUMIF({dr},A{r},{col('H')})")
        _cell(S, r, 5, f"=SUMIF({dr},A{r},{col('V')})", fmt="0.000")
        _cell(S, r, 6, f"=SUMPRODUCT(({dr}=A{r})*{col('Q')}*{col('H')})", fmt=MONEY)
        _cell(S, r, 7, f"=SUMPRODUCT(({dr}=A{r})*{col('R')}*{col('H')})", fmt=MONEY)
        _cell(S, r, 8, f"=SUMPRODUCT(({dr}=A{r})*{col('T')}*{col('H')})", fmt=MONEY)
        _cell(S, r, 9, f"=SUMPRODUCT(({dr}=A{r})*{col('U')}*{col('H')})", fmt=MONEY)
        _cell(S, r, 10, f"=SUMIF({dr},A{r},{col('X')})", fmt=MONEY)
        _cell(S, r, 11, ", ".join(f"{k} × {v}" for k, v in g["sheets"].items()) or "-")
        r += 1
    g1 = r - 1
    _cell(S, r, 1, "รวม", font=BOLD, fill=TOTAL_FILL)
    for c in range(2, 12):
        L = get_column_letter(c)
        v = f"=SUM({L}{g0}:{L}{g1})" if c >= 4 and c <= 10 and g1 >= g0 else None
        _cell(S, r, c, v, font=BOLD, fill=TOTAL_FILL, fmt="0.000" if c == 5 else MONEY if c >= 6 else None)
    tot = r
    r += 1
    _cell(S, r, 9, "บวกเพิ่ม", font=BOLD)
    _cell(S, r, 10, f"=ROUND(J{tot}*{cell_of['markup']}/100,2)", fmt=MONEY)
    r += 1
    _cell(S, r, 9, "ราคารวมสุทธิ", font=BOLD, fill=TOTAL_FILL)
    _cell(S, r, 10, f"=J{tot}+J{tot + 1}", font=BOLD, fill=TOTAL_FILL, fmt=MONEY)
    r += 2
    if res["errors"]:
        S.cell(row=r, column=1, value="ชิ้นที่ยังคิดราคาไม่ได้ (ดูแถวสีแดงในหน้ารายละเอียด):").font = BOLD
        for e in sorted(set(res["errors"])):
            r += 1
            S.cell(row=r, column=1, value="• " + e).font = BASE
        r += 2
    excl = [p for p in project.parts if not p.include]
    if excl:
        S.cell(row=r, column=1, value="ชิ้นที่ไม่ได้คิดราคา (ไม่นำไปตัด)").font = H2
        r += 1
        _head(S, r, ["ชิ้นส่วน", "จำนวน", "กลุ่ม / เหตุผล"])
        r += 1
        for p in excl:
            _cell(S, r, 1, p.name)
            _cell(S, r, 2, p.qty)
            _cell(S, r, 3, p.category or p.auto_reason or "ผู้ใช้เลือกไม่ตัด")
            r += 1

    # ================================================================ sheet 3: fit comparison
    _head(C, 1, ["ชิ้นส่วน", "X (มม.)", "Y (มม.)", "แผ่น", "เปิดใช้", "เรียงแบบ 1", "เรียงแบบ 2", "ชิ้น/แผ่น",
                 "ราคาแผ่น", "ค่าโลหะ / ชิ้น", "เลือก"])
    _widths(C, [26, 10, 10, 13, 9, 16, 16, 10, 12, 13, 8])
    C.freeze_panes = "B2"
    rc = 2
    m_val = P["margin"]
    for row in res["rows"]:
        for o in row["options"]:
            pick = row["sheet"] and o["w"] == row["sheet"]["w"] and o["l"] == row["sheet"]["l"]
            fill = PICK_FILL if pick else None
            x, y = row["x"], row["y"]
            a1 = f"{int(o['w'] // (x + m_val))} × {int(o['l'] // (y + m_val))} = {o['count1']}"
            a2 = f"{int(o['w'] // (y + m_val))} × {int(o['l'] // (x + m_val))} = {o['count2']}"
            vals = [row["name"], x, y, f"{o['w']:g}x{o['l']:g}", "ใช้" if o["on"] else "ไม่ใช้", a1, a2, o["count"],
                    o["sheet_price"], o["metal"], "✓" if pick else ""]
            for c, v in enumerate(vals, 1):
                _cell(C, rc, c, v, fill=fill, fmt=MONEY if c in (9, 10) else None)
            rc += 1
    C.cell(row=rc + 1, column=1, value=(f"เรียงแบบ 1 = ด้าน X ตามด้านกว้างของแผ่น, เรียงแบบ 2 = หมุนชิ้น 90° · ทุกชิ้นเผื่อ "
                                        f"{m_val:g} มม. · โปรแกรมเลือกแผ่นและแบบที่ค่าโลหะต่อชิ้นต่ำสุด")).font = BASE

    wb.calculation.fullCalcOnLoad = True
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
