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
    assert len(wb.sheetnames) == 2                                    # the steel types + one mode
    ws = wb.worksheets[1]
    assert ws["A1"].value == "ชื่อโหมด" and ws["B3"].value == 0.5
    rows = [(r[0].value, r[1].value, r[2].value, r[3].value) for r in ws.iter_rows(min_row=11, max_col=4) if r[2].value]
    assert ("ZN", "#22", 0.7, 1.25) in rows and ("ST", "#22", 0.7, 1.25) in rows and ("ST", "#3mm", 3.0, 4.8) in rows
    steels = wb.worksheets[0]                                          # how many steel types the file has, from the file itself
    assert [steels.cell(r, 1).value for r in (4, 5)] == ["ZN", "ST"] and steels["B1"].value == "=COUNTA(A4:A200)"


def test_roundtrip_keeps_every_field(tmp_path):
    p2 = dict(DEFAULT_PROFILE, id="m2", name="โหมด 2", k_factor=0.42, angle_tol=1.5, table_max_r=0.5,
              match_tol=0.1, jog_use_k=False, simplify_bends=False,
              table=[{"gauge": "#14", "thickness": 2.0, "bd": 3.0, "material": "SUS"},
                     {"gauge": "#14", "thickness": 2.0, "bd": 3.2, "material": ""}])
    write_profiles(tmp_path / "b.xlsx", [DEFAULT_PROFILE, p2])
    back = read_profiles(tmp_path / "b.xlsx")
    assert [p["id"] for p in back] == ["factory", "m2"]
    norm = lambda p: {**p, "table": [{**r, "material": r.get("material", "")} for r in p["table"]]}   # noqa: E731
    srt = lambda p: {**p, "table": sorted(p["table"], key=lambda r: (r["thickness"], r["material"]))}   # noqa: E731
    assert back[1] == srt(norm(p2)) and back[0] == norm(DEFAULT_PROFILE)


def test_excel_edit_is_used_by_the_program(tmp_path):
    st = ProfileStore(tmp_path / "profiles.json")
    assert bend_model(st.get(), 1.0).bd90 == 1.75
    wb = load_workbook(st.bend_path)
    ws = wb.worksheets[1]
    for r in range(11, 40):
        if ws.cell(r, 3).value == 1.0:
            ws.cell(r, 4).value = 1.8
    wb.save(st.bend_path)
    _touch_later(st.bend_path)
    assert bend_model(st.get(), 1.0).bd90 == 1.8          # read from Excel at the moment of use


def test_copied_sheet_becomes_a_new_mode(tmp_path):
    st = ProfileStore(tmp_path / "profiles.json")
    wb = load_workbook(st.bend_path)
    cp = wb.copy_worksheet(wb.worksheets[1])
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
    wb.worksheets[1]["B3"].value = "abc"
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


def _steel_workbook(tmp_path, steels, rows):
    """A workbook written by the program, then changed in Excel: the steel list and the rows of the mode sheet."""
    st = ProfileStore(tmp_path / "profiles.json")
    wb = load_workbook(st.bend_path)
    sh, ws = wb.worksheets
    for r in range(4, 12):
        for c in range(1, 4):
            sh.cell(r, c).value = None
    for i, (mid, name) in enumerate(steels, start=4):
        sh.cell(i, 1).value, sh.cell(i, 2).value, sh.cell(i, 3).value = mid, name, mid
    for r in range(11, 60):
        for c in range(1, 5):
            ws.cell(r, c).value = None
    for i, row in enumerate(rows, start=11):
        for c, v in enumerate(row, start=1):
            ws.cell(i, c).value = v
    wb.save(st.bend_path)
    _touch_later(st.bend_path)
    return st


def test_steel_types_are_read_from_the_workbook(tmp_path):
    st = _steel_workbook(tmp_path, [("ZN", "ซิงค์"), ("ST", "ขาว"), ("SUS", "สแตนเลส")],
                         [("ZN", "#19", 1.0, 1.75), ("ST", "#19", 1.0, 1.75), ("SUS", "#19", 1.0, 1.9),
                          ("ZN", "#14", 2.0, 3.2), ("ST", "#14", 2.0, 3.2), ("SUS", "#14", 2.0, 3.2)])
    st.refresh()
    assert [m["id"] for m in st.data["materials"]] == ["ZN", "ST", "SUS"]
    t = st.get()["table"]
    assert bend_model(st.get(), 1.0, material_id="SUS").bd90 == 1.9 and bend_model(st.get(), 1.0, material_id="ZN").bd90 == 1.75
    # same number for every steel = one "all steels" row, so a steel added later inherits it
    assert [r for r in t if r["thickness"] == 2.0] == [{"gauge": "#14", "thickness": 2.0, "bd": 3.2, "material": ""}]
    assert sorted(r["material"] for r in t if r["thickness"] == 1.0) == ["ST", "SUS", "ZN"]   # SUS differs: every steel stays visible


def test_row_of_an_unknown_steel_is_reported(tmp_path):
    st = _steel_workbook(tmp_path, [("ZN", "ซิงค์")], [("ZN", "#19", 1.0, 1.75), ("XX", "#19", 1.0, 1.9)])
    st.refresh()
    assert "XX" in st.bend_error and "ชนิดเหล็ก" in st.bend_error


def test_older_layout_still_reads(tmp_path):
    wb = Workbook()
    ws = wb.active
    ws.title = "เก่า"
    for i, (k, v) in enumerate([("ชื่อโหมด", "เก่า"), ("รหัส", "old"), ("K-factor", 0.5)], start=1):
        ws.cell(i, 1, k), ws.cell(i, 2, v)
    for j, h in enumerate(("เบอร์", "ความหนา (มม.)", "ค่ายืด (มม.)", "ใช้กับเหล็ก (ว่าง = ทุกชนิด)"), start=1):
        ws.cell(10, j, h)
    for i, row in enumerate([("#19", 1.0, 1.75, None), ("#19", 1.0, 1.9, "SUS")], start=11):
        for j, v in enumerate(row, start=1):
            ws.cell(i, j, v)
    wb.save(tmp_path / "old.xlsx")
    t = read_profiles(tmp_path / "old.xlsx")[0]["table"]
    assert [(r["thickness"], r["bd"], r["material"]) for r in t] == [(1.0, 1.75, ""), (1.0, 1.9, "SUS")]


def test_english_workbook_reads_back(tmp_path):
    from sheetflat.core.bend_xlsx import read_materials
    from sheetflat.core.profiles import DEFAULT_MATERIALS
    write_profiles(tmp_path / "en.xlsx", [DEFAULT_PROFILE], "en", materials=DEFAULT_MATERIALS)
    assert load_workbook(tmp_path / "en.xlsx").sheetnames[0] == "Steel type"
    assert [m["id"] for m in read_materials(tmp_path / "en.xlsx")] == [m["id"] for m in DEFAULT_MATERIALS]
    assert read_profiles(tmp_path / "en.xlsx")[0]["table"] == [{**r, "material": ""} for r in DEFAULT_PROFILE["table"]]
