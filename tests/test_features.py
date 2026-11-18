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
    assert wb["ราคาเหล็ก"]["B3"].value == "=24/(1220*2440)"
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
