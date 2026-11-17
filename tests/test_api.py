"""End-to-end test through the HTTP API with the sample assembly (skipped if the sample is missing)."""
import io
import time
import zipfile
from pathlib import Path

import ezdxf
import pytest
from fastapi.testclient import TestClient

SAMPLE = Path(__file__).resolve().parents[2] / "Assem.STEP"
pytestmark = pytest.mark.skipif(not SAMPLE.exists(), reason="sample STEP not found")


@pytest.fixture(scope="module")
def client():
    from sheetflat.app import app
    return TestClient(app)


@pytest.fixture(scope="module")
def job(client):
    r = client.post("/api/open-path", json={"path": str(SAMPLE), "material": "ZN"})
    jid = r.json()["id"]
    for _ in range(100):
        j = client.get(f"/api/jobs/{jid}").json()
        if j["state"] in ("done", "error"):
            break
        time.sleep(0.3)
    assert j["state"] == "done", j
    return jid, j["project"]


def test_groups_by_thickness(job):
    _, p = job
    codes = {g["code"]: g for g in p["groups"]}
    assert set(codes) == {"ZN#22", "ZN#14"}
    assert codes["ZN#22"]["thickness"] == 0.7 and codes["ZN#14"]["thickness"] == 2.0
    assert p["summary"]["pieces"] == 10


def test_flat_sizes_match_solidworks(job):
    """Reference sizes read from the SolidWorks drawings (ZN#22 / ZN#14 DXF) in the sample folder."""
    _, p = job
    ref = {"Body": (213.36, 962.92), "Front": (188.36, 339.66), "Bottom": (286.50, 396.10), "Hood2": (308.51, 411.41),
           "BackHook_v2": (403.80, 40.00), "BackHook_part_v2": (56.80, 58.60)}
    for part in p["parts"]:
        if part["name"] in ref:
            w, h = sorted((part["flat"]["w"], part["flat"]["h"]))
            rw, rh = sorted(ref[part["name"]])
            assert abs(w - rw) < 0.02 and abs(h - rh) < 0.02, part["name"]


def test_volume_check(job):
    _, p = job
    for part in p["parts"]:
        assert abs(part["checks"]["volume_dev_pct"]) < 0.01


def test_dxf_download_is_valid(client, job):
    jid, p = job
    g = p["groups"][0]
    r = client.get(f"/api/jobs/{jid}/export/dxf/{g['id']}/0")
    assert r.status_code == 200
    doc = ezdxf.read(io.StringIO(r.content.decode("utf-8")))
    layers = {e.dxf.layer for e in doc.modelspace()}
    assert "CUT" in layers


def test_material_change_regroups(client, job):
    jid, p = job
    pid = next(x["id"] for x in p["parts"] if x["name"] == "Hood2")
    p2 = client.patch(f"/api/jobs/{jid}/parts/{pid}", json={"material_id": "ST"}).json()
    codes = {g["code"] for g in p2["groups"]}
    assert "ST#22" in codes and "ZN#22" in codes
    p3 = client.patch(f"/api/jobs/{jid}/parts/{pid}", json={"material_id": "ZN"}).json()
    assert {g["code"] for g in p3["groups"]} == {"ZN#22", "ZN#14"}


def test_excel_and_zip(client, job):
    jid, _ = job
    z = client.get(f"/api/jobs/{jid}/export/all")
    names = zipfile.ZipFile(io.BytesIO(z.content)).namelist()
    assert sum(n.endswith(".dxf") for n in names) == 2 and any(n.endswith(".xlsx") for n in names)
    x = client.get(f"/api/jobs/{jid}/export/xlsx")
    assert x.status_code == 200 and x.content[:2] == b"PK"


def test_upload_multipart_and_sldasm_message(client):
    with open(SAMPLE, "rb") as fh:
        r = client.post("/api/upload", files={"file": ("Assem.STEP", fh)}, data={"material": "ST"})
    assert r.status_code == 200
    jid = r.json()["id"]
    for _ in range(100):
        j = client.get(f"/api/jobs/{jid}").json()
        if j["state"] in ("done", "error"):
            break
        time.sleep(0.3)
    assert j["state"] == "done"
    assert {g["code"] for g in j["project"]["groups"]} == {"ST#22", "ST#14"}
    bad = client.post("/api/upload", files={"file": ("x.sldasm", b"not real")})
    assert bad.status_code == 400 and "STEP" in bad.json()["detail"]
    junk = client.post("/api/upload", files={"file": ("x.txt", b"hello")})
    assert junk.status_code == 400


def test_cloud_mode_hardening(client, monkeypatch):
    import sheetflat.app as A
    monkeypatch.setattr(A, "CLOUD", True)
    assert client.post("/api/open-path", json={"path": str(SAMPLE)}).status_code == 403
    assert client.get("/api/info").json()["cloud"] is True
    before = {p for p in (A.DATA / "uploads").rglob("*") if p.is_file()}
    with open(SAMPLE, "rb") as fh:
        jid = client.post("/api/upload", files={"file": ("Assem.STEP", fh)}).json()["id"]
    for _ in range(100):
        if client.get(f"/api/jobs/{jid}").json()["state"] in ("done", "error"):
            break
        time.sleep(0.3)
    # the uploaded customer file must be gone from the server
    left = [p for p in (A.DATA / "uploads").rglob("*") if p.is_file() and p not in before]
    assert left == []
    monkeypatch.setattr(A, "MAX_UPLOAD_MB", 0)
    with open(SAMPLE, "rb") as fh:
        assert client.post("/api/upload", files={"file": ("Assem.STEP", fh)}).status_code == 413
