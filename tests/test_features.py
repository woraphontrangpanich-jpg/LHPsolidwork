"""Regroup parts, pricing, save/load job files, per-steel bend rows (needs the sample STEP)."""
import io
import json
import time
import zipfile
from pathlib import Path

import openpyxl
import pytest
from fastapi.testclient import TestClient

SAMPLE = Path(__file__).resolve().parents[2] / "Assem.STEP"
pytestmark = pytest.mark.skipif(not SAMPLE.exists(), reason="sample STEP not found")


@pytest.fixture(scope="module")
def client():
    from sheetflat.app import app
    return TestClient(app)


def wait(client, jid):
    for _ in range(300):
        j = client.get(f"/api/jobs/{jid}").json()
        if j["state"] in ("done", "error"):
            assert j["state"] == "done", j
            return j["project"]
        time.sleep(0.2)
    raise AssertionError("timeout")


@pytest.fixture()
def job(client):
    jid = client.post("/api/open-path", json={"path": str(SAMPLE), "material": "ZN"}).json()["id"]
    return jid, wait(client, jid)


def groups(P):
    return {g["code"]: sorted(P["parts"][[p["id"] for p in P["parts"]].index(i)]["name"] for i in g["part_ids"])
            for g in P["groups"]}


def test_move_parts_between_groups_and_to_a_custom_group(client, job):
    jid, P = job
    hook = next(p for p in P["parts"] if p["name"] == "Hook")
    front = next(p for p in P["parts"] if p["name"] == "Front")
    # Hook -> bought-in item (typed group name): leaves the DXF, listed under that name
    P = client.post(f"/api/jobs/{jid}/move", json={"ids": [hook["id"]], "target": {"category": "ซื้อสำเร็จรูป"}}).json()
    h = next(p for p in P["parts"] if p["id"] == hook["id"])
    assert not h["include"] and h["category"] == "ซื้อสำเร็จรูป"
    assert "Hook" not in sum(groups(P).values(), [])
    # Front (ZN 0.7) -> ST: a new ST#22 group
    P = client.post(f"/api/jobs/{jid}/move", json={"ids": [front["id"]], "target": {"material_id": "ST", "thickness": 0.7}}).json()
    assert groups(P)["ST#22"] == ["Front"]
    # Hook back into ZN 2.0
    P = client.post(f"/api/jobs/{jid}/move", json={"ids": [hook["id"]], "target": {"material_id": "ZN", "thickness": 2.0}}).json()
    assert "Hook" in groups(P)["ZN#14"]
    assert client.post(f"/api/jobs/{jid}/move", json={"ids": [hook["id"]], "target": {"material_id": "XX"}}).status_code == 400


def test_pricing_follows_the_workbook_formulas(client, job):
    jid, P = job
    r = client.get(f"/api/jobs/{jid}/pricing").json()
    assert not r["errors"] and r["total"] > 0
    for row in r["rows"]:
        sh = row["sheet"]
        assert sh["count"] == max(sh["count1"], sh["count2"]) >= 1
        assert row["metal"] == -(-sh["sheet_price"] // sh["count"])          # ROUNDUP(sheet price / pieces)
        assert row["total"] == pytest.approx((row["metal"] + row["paint"] + row["extra"]) * row["qty"])
        assert row["extra"] == pytest.approx(5 * row["bends"])                 # default extra: ค่าพับ 5 / bend
    assert r["total"] == pytest.approx(sum(g["total"] for g in r["groups"]))
    # change prices: 10 % markup, only the 1220x2440 sheet
    pr = r["pricing"]
    pr["markup_pct"] = 10
    for s in pr["sheets"]:
        s["on"] = s["l"] == 2440
    r2 = client.put(f"/api/jobs/{jid}/pricing", json={"pricing": pr, "meta": {"customer": "ลูกค้า ก"}}).json()
    assert r2["markup"] == pytest.approx(r2["subtotal"] * 0.1, abs=0.01)
    assert all(row["sheet"]["l"] == 2440 for row in r2["rows"])
    assert r2["meta"]["customer"] == "ลูกค้า ก"
    # prices follow the bend table thicknesses: ZN#22 (0.7) and ZN#14 (2.0) exist
    codes = {(x["material"], x["thickness"]) for x in r2["pricing"]["prices"]}
    assert ("ZN", 0.7) in codes and ("ZN", 2.0) in codes and ("ST", 3.0) in codes
    # a per-job extra is added once, a per-hole extra per hole
    pr = r2["pricing"]
    pr["markup_pct"] = 0
    pr["extras"] = [{"name": "ขนส่ง", "rate": 300, "unit": "job", "on": True},
                    {"name": "เจาะรู", "rate": 2, "unit": "hole", "on": True}]
    r3 = client.put(f"/api/jobs/{jid}/pricing", json={"pricing": pr}).json()
    assert r3["total"] == pytest.approx(sum(g["total"] for g in r3["groups"]) + 300, abs=0.01)
    assert all(row["extra"] == pytest.approx(2 * row["holes"]) for row in r3["rows"])


def test_new_steel_from_the_move_bar(client, job):
    jid, P = job
    front = next(p for p in P["parts"] if p["name"] == "Bottom")      # all its bends use the table
    import sheetflat.app as A
    keep = json.loads(json.dumps(A.store.data))
    try:
        r = client.post(f"/api/jobs/{jid}/materials", json={
            "material": {"id": "SUSX", "name": "สแตนเลสทดสอบ", "prefix": "SUSX"}, "base": "ZN",
            "rows": [{"thickness": 0.7, "bd": 1.4, "gauge": "#22"}], "price_kg": 120})
        assert r.status_code == 200
        prof = next(p for p in r.json()["profiles"]["profiles"] if p["id"] == r.json()["project"]["profile"]["id"])
        assert {"gauge": "#22", "thickness": 0.7, "bd": 1.4, "material": "SUSX"} in prof["table"]
        P2 = client.post(f"/api/jobs/{jid}/move", json={"ids": [front["id"]], "target": {"material_id": "SUSX"}}).json()
        f2 = next(p for p in P2["parts"] if p["id"] == front["id"])
        assert f2["code"] == "SUSX#22" and f2["thickness"]["used"] == 0.7 and f2["thickness"]["bd"] == 1.4
        assert f2["flat"]["w"] != front["flat"]["w"] or f2["flat"]["h"] != front["flat"]["h"]    # new bend value -> new flat size
        row = next(x for x in client.get(f"/api/jobs/{jid}/pricing").json()["rows"] if x["part_id"] == front["id"])
        assert row["price_kg"] == 120 and not row["error"]
        assert client.post(f"/api/jobs/{jid}/materials", json={"material": {"id": "ZN"}}).status_code == 400
    finally:
        A.store.replace(keep)


def test_pricing_workbook(client, job):
    jid, _ = job
    res = client.get(f"/api/jobs/{jid}/export/pricing")
    assert res.status_code == 200
    wb = openpyxl.load_workbook(io.BytesIO(res.content))
    assert wb.sheetnames == ["สรุป", "รายละเอียด", "ราคาเหล็ก", "ค่าแรงและอื่นๆ", "เทียบขนาดแผ่น"]
    d = wb["รายละเอียด"]
    # the workbook's own columns and formulas
    assert [d[c].value for c in ("A4", "B4", "D4", "F4", "H4", "I4", "J4", "K4", "M4", "R4", "U4")] == [
        "NO.", "วัตถุดิบ", "SIZE", "แผ่นที่ใช้", "ค่าโลหะ", "ค่าสี", "ค่าชุบ", "จำนวน", "ราคารวม", "เรียงแบบ 1", "เรียงแบบ 2"]
    assert d["R2"].value == 20 and d["H6"].value == '=IFERROR(ROUNDUP(G6/MAX(T6,W6),0),"")'
    assert d["B6"].value.startswith("ZN#") and d["T6"].value == '=IFERROR(R6*S6,"")'
    pr = wb["ราคาเหล็ก"]
    assert [pr[c].value for c in ("A2", "B2", "C2", "D2", "E2")] == [
        "Material", "ความหนา (มม.)", "ราคา/กิโลกรัม", "น้ำหนักแผ่น 1220x2440 (กก.)", "1220x2440"]
    assert pr["A3"].value == "ZN#22" and pr["B3"].value == 0.7 and pr["D3"].value == pytest.approx(16.8)
    assert "*C6" not in d["G6"].value                                    # sheet price is already at this thickness
    assert "ราคา.xlsx" in " ".join(zipfile.ZipFile(io.BytesIO(client.get(f"/api/jobs/{jid}/export/all").content)).namelist())


def test_save_and_load_job_file(client, job):
    jid, P = job
    hook = next(p for p in P["parts"] if p["name"] == "Hook")
    client.post(f"/api/jobs/{jid}/move", json={"ids": [hook["id"]], "target": {"category": "ซื้อสำเร็จรูป"}})
    client.put(f"/api/jobs/{jid}/pricing", json={"meta": {"customer": "บริษัท ข"}})
    before = client.get(f"/api/jobs/{jid}").json()["project"]
    blob = client.get(f"/api/jobs/{jid}/save")
    assert blob.status_code == 200
    names = zipfile.ZipFile(io.BytesIO(blob.content)).namelist()
    assert "job.json" in names and any(n.startswith("model/") for n in names)
    jid2 = client.post("/api/load", files={"file": ("Assem.sheetflat", blob.content)}).json()["id"]
    after = wait(client, jid2)
    assert groups(after) == groups(before)
    h = next(p for p in after["parts"] if p["name"] == "Hook")
    assert h["category"] == "ซื้อสำเร็จรูป" and not h["include"]
    assert client.get(f"/api/jobs/{jid2}/pricing").json()["meta"]["customer"] == "บริษัท ข"
    bad = client.post("/api/load", files={"file": ("x.sheetflat", b"not a zip")})
    assert bad.status_code == 400


def test_bend_row_for_one_steel_only(tmp_path):
    from sheetflat.core.profiles import DEFAULT_PROFILE, match_row
    prof = dict(DEFAULT_PROFILE)
    prof["table"] = DEFAULT_PROFILE["table"] + [{"gauge": "#22", "thickness": 0.7, "bd": 1.4, "material": "SUS"}]
    assert match_row(prof, 0.7, "SUS")["bd"] == 1.4
    assert match_row(prof, 0.7, "ZN")["bd"] == 1.25
    assert match_row(prof, 0.7, None)["bd"] == 1.25


def test_rename_groups(client, job):
    jid, P = job
    g = next(x for x in P["groups"] if x["code"] == "ZN#22")
    P = client.post(f"/api/jobs/{jid}/rename", json={"key": g["key"], "name": "ตัวตู้"}).json()
    g2 = next(x for x in P["groups"] if x["key"] == g["key"])
    assert g2["name"] == "ตัวตู้" and g2["renamed"] and "ตัวตู้" in g2["filename"] and g2["code"] == "ZN#22"
    other = next(x for x in P["groups"] if x["key"] != g["key"])
    assert client.post(f"/api/jobs/{jid}/rename", json={"key": other["key"], "name": "ตัวตู้"}).status_code == 400
    # the name stays after recomputing and in a saved job file
    client.patch(f"/api/jobs/{jid}/settings", json={"gap": 12})
    blob = client.get(f"/api/jobs/{jid}/save").content
    jid2 = client.post("/api/load", files={"file": ("a.sheetflat", blob)}).json()["id"]
    after = wait(client, jid2)
    assert next(x for x in after["groups"] if x["key"] == g["key"])["name"] == "ตัวตู้"
    # pricing still finds the steel price; the summary page shows the name
    r = client.get(f"/api/jobs/{jid}/pricing").json()
    assert not r["errors"] and any(x["name"] == "ตัวตู้" for x in r["groups"])
    wb = openpyxl.load_workbook(io.BytesIO(client.get(f"/api/jobs/{jid}/export/pricing").content))
    assert any(c.value == "ตัวตู้ (ZN#22)" for row in wb["สรุป"].iter_rows() for c in row)
    # empty name -> back to the steel code
    P = client.post(f"/api/jobs/{jid}/rename", json={"key": g["key"], "name": ""}).json()
    assert next(x for x in P["groups"] if x["key"] == g["key"])["name"] == "ZN#22"
    # not-cut groups
    hook = next(p for p in P["parts"] if p["name"] == "Hook")
    client.post(f"/api/jobs/{jid}/move", json={"ids": [hook["id"]], "target": {"category": "ซื้อสำเร็จรูป"}})
    P = client.post(f"/api/jobs/{jid}/rename", json={"category": "ซื้อสำเร็จรูป", "name": "ลูกค้าส่งมาเอง"}).json()
    assert next(p for p in P["parts"] if p["name"] == "Hook")["category"] == "ลูกค้าส่งมาเอง"


def test_quantities_sets_paint_and_plating(client, job):
    jid, P = job
    body = next(p for p in P["parts"] if p["name"] == "Body")
    assert body["qty_asm"] == 1 and body["qty"] == 1
    P = client.patch(f"/api/jobs/{jid}/parts/{body['id']}", json={"qty": 5}).json()
    b = next(p for p in P["parts"] if p["id"] == body["id"])
    assert b["qty"] == 5 and b["qty_override"] == 5
    g = next(x for x in P["groups"] if body["id"] in x["part_ids"])
    layout = client.get(f"/api/jobs/{jid}/groups/{g['id']}/layout/0").json()
    assert sum(1 for it in layout["items"] if it["part_id"] == body["id"]) == 5           # 5 copies in the DXF
    assert client.patch(f"/api/jobs/{jid}/parts/{body['id']}", json={"qty": 0}).status_code == 400
    # 3 sets: every quantity x 3
    P = client.patch(f"/api/jobs/{jid}/settings", json={"sets": 3}).json()
    assert P["sets"] == 3 and next(p for p in P["parts"] if p["id"] == body["id"])["qty"] == 15
    hook = next(p for p in P["parts"] if p["name"] == "Hook")
    assert hook["qty"] == hook["qty_asm"] * 3
    # paint off, plating on for Body
    client.patch(f"/api/jobs/{jid}/parts/{body['id']}", json={"paint": False, "plate": True})
    r = client.get(f"/api/jobs/{jid}/pricing").json()
    row = next(x for x in r["rows"] if x["part_id"] == body["id"])
    assert row["paint"] == 0 and not row["paint_on"] and row["plate_on"] and row["plating"] > 0
    assert row["total"] == pytest.approx((row["metal"] + row["extra"]) * 15 + row["plating"], abs=1e-6)
    # paint is priced on the full flat rectangle; the real outline (holes not deducted) is shown too
    other = next(x for x in r["rows"] if x["name"] == "Bottom")
    assert other["paint_on"] and other["area_rect"] == pytest.approx(other["x"] * other["y"], rel=1e-6)
    assert 0 < other["area_real"] <= other["area_rect"] + 1e-6 and other["paint_real"] <= other["paint"]
    # survives save / load
    blob = client.get(f"/api/jobs/{jid}/save").content
    after = wait(client, client.post("/api/load", files={"file": ("q.sheetflat", blob)}).json()["id"])
    b2 = next(p for p in after["parts"] if p["name"] == "Body")
    assert after["sets"] == 3 and b2["qty"] == 15 and not b2["paint"] and b2["plate"]
    # Excel: tick columns drive paint and plating
    wb = openpyxl.load_workbook(io.BytesIO(client.get(f"/api/jobs/{jid}/export/pricing").content))
    d = wb["รายละเอียด"]
    assert d["AC4"].value.startswith("ทาสี") and d["AD4"].value.startswith("ชุบ")
    assert d["I6"].value.startswith("=IF(AC6=1") and d["J6"].value.startswith("=IF(AND(AD6=1")
