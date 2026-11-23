"""Files the program hands out follow the language of the page (?lang=th|en): Excel, ZIP folders, file names, bend table."""
import io
import re
import zipfile
from pathlib import Path

import pytest
from openpyxl import load_workbook

from sheetflat.core.bend_xlsx import read_profiles, write_profiles
from sheetflat.core.i18n import fname, norm, tr, tr_exact
from sheetflat.core.profiles import DEFAULT_PROFILE, ProfileStore
from sheetflat.core.project import Project
from sheetflat.core.pricing_xlsx import build_pricing_xlsx
from sheetflat.core.xlsx_export import build_xlsx

SAMPLE = Path(__file__).resolve().parents[2] / "Assem.STEP"
THAI = re.compile("[฀-๿]")


def test_phrases_and_names():
    assert norm("EN-us") == "en" and norm("") == "th" and norm("fr") == "th"
    assert tr("ใบสั่งตัด", "en") == "Cutting order" and tr("ใบสั่งตัด", "th") == "ใบสั่งตัด"
    assert tr("ZN#22 ชิ้น", "en").startswith("ZN#22")                 # codes stay
    assert tr_exact("ซื้อสำเร็จรูป", "en") == "Bought-in" and tr_exact("ซื้อสำเร็จรูปของฉัน", "en") == "ซื้อสำเร็จรูปของฉัน"
    assert fname("summary", "en") == "summary" and fname("summary", "th") == "สรุป"


def all_strings(wb):
    for ws in wb.worksheets:
        yield ws.title
        for row in ws.iter_rows():
            for c in row:
                if isinstance(c.value, str):
                    yield c.value


@pytest.fixture(scope="module")
def pr(tmp_path_factory):
    if not SAMPLE.exists():
        pytest.skip("sample STEP not found")
    st = ProfileStore(tmp_path_factory.mktemp("p") / "p.json")
    p = Project(st, "Assem", "Assem.step")
    p.load_step(str(SAMPLE))
    return p


@pytest.mark.parametrize("build", [build_xlsx, build_pricing_xlsx])
def test_english_workbooks_have_no_thai_and_formulas_still_point_at_sheets(pr, build):
    th = load_workbook(io.BytesIO(build(pr, "th")))
    en = load_workbook(io.BytesIO(build(pr, "en")))
    assert any(THAI.search(s) for s in all_strings(th))               # the Thai one is Thai
    left = [s[:60] for s in all_strings(en) if THAI.search(s)]
    assert not left, left[:10]
    assert len(th.sheetnames) == len(en.sheetnames)
    names = set(en.sheetnames)
    for ws in en.worksheets:                                          # every sheet a formula mentions exists
        for row in ws.iter_rows():
            for c in row:
                if isinstance(c.value, str) and c.value.startswith("="):
                    for ref in re.findall(r"'([^']+)'!", c.value):
                        assert ref in names, (ws.title, c.coordinate, ref)


def test_customer_text_is_never_translated(pr):
    pr.parts[0].name = "แผ่นฝาครอบ"                                     # Thai words that are in the dictionary
    en = load_workbook(io.BytesIO(build_xlsx(pr, "en")))
    assert any(s == "แผ่นฝาครอบ" for s in all_strings(en))


def test_bend_table_file_in_english_reads_back_the_same(tmp_path):
    write_profiles(tmp_path / "th.xlsx", [DEFAULT_PROFILE], "th")
    write_profiles(tmp_path / "en.xlsx", [DEFAULT_PROFILE], "en")
    en = load_workbook(tmp_path / "en.xlsx")
    assert not [s for s in all_strings(en) if THAI.search(s) and "ค่ายืด" not in s]    # only the shop's own mode name may stay
    a, b = read_profiles(tmp_path / "th.xlsx"), read_profiles(tmp_path / "en.xlsx")
    assert a[0]["table"] == b[0]["table"]
    for k in ("k_factor", "angle_tol", "table_max_r", "match_tol", "jog_use_k", "simplify_bends", "id"):
        assert a[0][k] == b[0][k]


def test_api_downloads_follow_the_language(pr):
    from fastapi.testclient import TestClient

    from sheetflat.app import app, jobs
    jobs[pr.id] = pr
    c = TestClient(app)
    r = c.get(f"/api/jobs/{pr.id}/export/xlsx?lang=en")
    assert "Assem_summary.xlsx" in r.headers["content-disposition"]
    r = c.get(f"/api/jobs/{pr.id}/export/xlsx?lang=th")
    assert "%E0%B8%AA%E0%B8%A3%E0%B8%B8%E0%B8%9B" in r.headers["content-disposition"]        # สรุป
    z = zipfile.ZipFile(io.BytesIO(c.get(f"/api/jobs/{pr.id}/export/steps?lang=en").content))
    assert all(n.startswith(("cut/", "not cut/")) for n in z.namelist())
    z = zipfile.ZipFile(io.BytesIO(c.get(f"/api/jobs/{pr.id}/export/all?lang=en").content))
    assert {n for n in z.namelist() if n.endswith(".xlsx")} == {"Assem_summary.xlsx", "Assem_price.xlsx"}
    r = c.get("/api/profiles/xlsx?lang=en")
    assert r.status_code == 200 and not [s for s in all_strings(load_workbook(io.BytesIO(r.content))) if THAI.search(s) and "ค่ายืด" not in s]
