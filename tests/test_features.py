"""Regroup parts, pricing, save/load job files, per-steel bend rows (needs the sample STEP)."""
import io
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
        assert row["total"] == pytest.approx((row["metal"] + row["paint"] + row["labor"]) * row["qty"])
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


def test_pricing_workbook(client, job):
    jid, _ = job
    res = client.get(f"/api/jobs/{jid}/export/pricing")
    assert res.status_code == 200
    wb = openpyxl.load_workbook(io.BytesIO(res.content))
    assert wb.sheetnames == ["สรุปราคา", "รายละเอียด", "เทียบขนาดแผ่น"]
    d = wb["รายละเอียด"]
    assert str(d["L2"].value).startswith("=ROUNDUP(") and str(d["X2"].value) == "=W2*H2"
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
