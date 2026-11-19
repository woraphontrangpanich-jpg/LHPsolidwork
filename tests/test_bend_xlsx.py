"""Bend tables live in an Excel workbook (one sheet = one mode) and the program reads them from there."""
import io
import os
import time

import pytest
from openpyxl import Workbook, load_workbook

from sheetflat.core.bend_xlsx import read_profiles, write_profiles
from sheetflat.core.profiles import DEFAULT_PROFILE, ProfileStore, bend_model


def _touch_later(path):
    t = time.time() + 5 * _touch_later.n
    _touch_later.n += 1
    os.utime(path, (t, t))
_touch_later.n = 1


def test_default_workbook_is_created_one_sheet_per_mode(tmp_path):
    st = ProfileStore(tmp_path / "profiles.json")
    wb = load_workbook(st.bend_path)
    assert len(wb.sheetnames) == 1
    ws = wb.worksheets[0]
    assert ws["A1"].value == "ชื่อโหมด" and ws["B3"].value == 0.5
    rows = [(r[0].value, r[1].value, r[2].value) for r in ws.iter_rows(min_row=11, max_col=3) if r[1].value]
    assert ("#22", 0.7, 1.25) in rows and ("#3mm", 3.0, 4.8) in rows


def test_roundtrip_keeps_every_field(tmp_path):
    p2 = dict(DEFAULT_PROFILE, id="m2", name="โหมด 2", k_factor=0.42, angle_tol=1.5, table_max_r=0.5,
              match_tol=0.1, jog_use_k=False, simplify_bends=False,
              table=[{"gauge": "#14", "thickness": 2.0, "bd": 3.0, "material": "SUS"},
                     {"gauge": "#14", "thickness": 2.0, "bd": 3.2, "material": ""}])
    write_profiles(tmp_path / "b.xlsx", [DEFAULT_PROFILE, p2])
    back = read_profiles(tmp_path / "b.xlsx")
    assert [p["id"] for p in back] == ["factory", "m2"]
    norm = lambda p: {**p, "table": [{**r, "material": r.get("material", "")} for r in p["table"]]}   # noqa: E731
    assert back[1] == norm(p2) and back[0] == norm(DEFAULT_PROFILE)


def test_excel_edit_is_used_by_the_program(tmp_path):
    st = ProfileStore(tmp_path / "profiles.json")
    assert bend_model(st.get(), 1.0).bd90 == 1.75
    wb = load_workbook(st.bend_path)
    ws = wb.worksheets[0]
    for r in range(11, 30):
        if ws.cell(r, 2).value == 1.0:
            ws.cell(r, 3).value = 1.8
    wb.save(st.bend_path)
    _touch_later(st.bend_path)
    assert bend_model(st.get(), 1.0).bd90 == 1.8          # read from Excel at the moment of use


def test_copied_sheet_becomes_a_new_mode(tmp_path):
    st = ProfileStore(tmp_path / "profiles.json")
    wb = load_workbook(st.bend_path)
    cp = wb.copy_worksheet(wb.worksheets[0])
    cp.title = "ร้านใหม่"
    cp["B1"].value = "ร้านใหม่"
    wb.save(st.bend_path)
    _touch_later(st.bend_path)
    st.refresh()
    ids = [p["id"] for p in st.data["profiles"]]
    assert len(ids) == 2 and len(set(ids)) == 2            # the duplicated id was made unique


def test_broken_workbook_keeps_last_good_tables(tmp_path):
    st = ProfileStore(tmp_path / "profiles.json")
    wb = load_workbook(st.bend_path)
    wb.worksheets[0]["B3"].value = "abc"
    wb.save(st.bend_path)
    _touch_later(st.bend_path)
    assert st.get()["k_factor"] == 0.5
    assert "K-factor" in st.bend_error


def test_saving_in_the_program_writes_the_workbook(tmp_path):
    st = ProfileStore(tmp_path / "profiles.json")
    d = {**st.data}
    d["profiles"][0]["table"][0]["bd"] = 9.9
    st.replace(d)
    assert read_profiles(st.bend_path)[0]["table"][0]["bd"] == 9.9
    assert ProfileStore(tmp_path / "profiles.json").get()["table"][0]["bd"] == 9.9


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    from sheetflat.app import app
    return TestClient(app)


def test_api_download_and_upload(client):
    r = client.get("/api/profiles/xlsx")
    assert r.status_code == 200 and load_workbook(io.BytesIO(r.content)).sheetnames
    wb = Workbook()
    wb.active.title = "x"
    bad = io.BytesIO()
    wb.save(bad)
    r = client.post("/api/profiles/xlsx", files={"file": ("b.xlsx", bad.getvalue())})
    assert r.status_code == 400 and "หัวตาราง" in r.json()["detail"]
    r = client.post("/api/profiles/xlsx", files={"file": ("b.xlsx", client.get("/api/profiles/xlsx").content)})
    assert r.status_code == 200 and r.json()["bend_file"]["error"] == ""


def test_shipped_workbook_is_the_shop_standard_and_seeds_a_fresh_install(tmp_path):
    from sheetflat.core.profiles import SHIPPED_BEND_XLSX
    assert SHIPPED_BEND_XLSX.exists()
    shipped = read_profiles(SHIPPED_BEND_XLSX)
    assert [p["id"] for p in shipped] == ["factory"]
    assert [(r["gauge"], r["thickness"], r["bd"]) for r in shipped[0]["table"]] == \
        [(r["gauge"], r["thickness"], r["bd"]) for r in DEFAULT_PROFILE["table"]]
    st = ProfileStore(tmp_path / "profiles.json")
    assert st.bend_path.read_bytes() == SHIPPED_BEND_XLSX.read_bytes()
