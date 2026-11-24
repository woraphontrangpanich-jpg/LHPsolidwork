"""Passcodes: staff gets everything except pricing, owner gets everything.

The rest of the tests run with test-only codes from SHEETFLAT_STAFF_CODE / SHEETFLAT_OWNER_CODE (conftest.py);
the file-based codes of a real install (first-start setup, staff installer) are tested with their own folder."""
import io
import json
import time
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import sheetflat.app as appmod
from sheetflat.app import app
from sheetflat.core import access

DEMO = Path(__file__).resolve().parents[1] / "docs" / "demo" / "DemoCabinet.step"


def client(code=None):
    c = TestClient(app, login=False)
    if code is not None:
        assert c.post("/api/login", json={"code": code}).status_code == 200
    return c


# ------------------------------------------------------------------ codes kept in the data folder
@pytest.fixture
def local(tmp_path, monkeypatch):
    """A fresh install: no codes yet, none from the environment."""
    monkeypatch.delenv("SHEETFLAT_STAFF_CODE", raising=False)
    monkeypatch.delenv("SHEETFLAT_OWNER_CODE", raising=False)
    a = access.Access(tmp_path / "access.json")
    monkeypatch.setattr(appmod, "ACCESS", a)
    return a


def test_codes_are_stored_as_hashes(local):
    assert not local.configured()
    local.set_codes("ABC", "ABC 99")
    raw = local.path.read_text(encoding="utf-8")
    assert "ABC" not in raw
    assert local.role_for_code("ABC") == "staff"
    assert local.role_for_code("ABC 99") == "owner"
    assert local.role_for_code("ABC99") == "owner"           # spaces do not matter
    for bad in ("", "abc", "ABC 98", "99"):
        assert local.role_for_code(bad) is None, bad
    with pytest.raises(ValueError):
        local.set_codes("same", "same")
    with pytest.raises(ValueError):
        local.set_codes("", "x")


def test_first_start_sets_the_codes(local):
    c = client()
    info = c.get("/api/info").json()
    assert info["setup_needed"] is True and info["logged_in"] is False
    assert c.post("/api/access/setup", json={"owner": "X1", "staff": "X1"}).status_code == 400
    r = c.post("/api/access/setup", json={"owner": "BOSS 1", "staff": "TEAM"})
    assert r.status_code == 200 and r.json()["owner"] is True
    assert c.get("/api/info").json()["owner"] is True          # logged in as the owner right away
    # nobody can set them again from the setup page
    assert client().post("/api/access/setup", json={"owner": "A", "staff": "B"}).status_code == 409
    assert client().get("/api/info").json()["setup_needed"] is False
    assert client().post("/api/login", json={"code": "TEAM"}).json()["owner"] is False


def test_owner_changes_codes_staff_cannot(local):
    local.set_codes("TEAM", "BOSS")
    staff = client("TEAM")
    assert staff.put("/api/access", json={"owner": "A", "staff": "B"}).status_code == 404
    assert staff.get("/api/access/installer").status_code == 404
    owner = client("BOSS")
    assert owner.put("/api/access", json={"owner": "NEW BOSS", "staff": "NEW TEAM"}).status_code == 200
    assert local.role_for_code("BOSS") is None and local.role_for_code("NEW TEAM") == "staff"


def test_staff_installer_carries_the_codes(local, tmp_path, monkeypatch):
    setup = tmp_path / "setup" / "SheetFlat-Setup.exe"
    setup.parent.mkdir()
    setup.write_bytes(b"MZ" + b"x" * 5000)
    monkeypatch.setattr(appmod, "SETUP_COPY", setup)
    local.set_codes("TEAM", "BOSS")
    owner = client("BOSS")
    assert owner.get("/api/info").json()["installer"] is True
    r = owner.get("/api/access/installer")
    assert r.status_code == 200 and r.content.startswith(b"MZ" + b"x" * 5000)
    assert b"TEAM" not in r.content and b"BOSS" not in r.content     # hashes only
    made = tmp_path / "staff-setup.exe"
    made.write_bytes(r.content)
    assert access.installer_size(made) == 5002                       # the plain installer is found again

    # a staff PC that installs it takes the owner's codes
    staff_pc = access.Access(tmp_path / "staff_pc" / "access.json")
    assert staff_pc.import_from_installer(made) is True
    assert staff_pc.role_for_code("TEAM") == "staff" and staff_pc.role_for_code("BOSS") == "owner"
    assert staff_pc.import_from_installer(made) is False             # only once
    # the owner's PC does not take its own installer again
    assert local.import_from_installer(made) is False
    # an installer made from a staff installer carries one set of codes, not two
    setup.write_bytes(r.content)
    again = owner.get("/api/access/installer").content
    assert again.count(access.MARK) == 1


def test_plain_installer_carries_no_codes(tmp_path):
    f = tmp_path / "SheetFlat-Setup.exe"
    f.write_bytes(b"MZ" + b"y" * 100)
    assert access.read_block(f) is None
    assert access.Access(tmp_path / "a.json").import_from_installer(f) is False


# ------------------------------------------------------------------ what each role may see
def test_nothing_works_before_login():
    c = client()
    info = c.get("/api/info").json()
    assert info["logged_in"] is False and info["owner"] is False and info["setup_needed"] is False
    assert c.get("/api/profiles").status_code == 401
    assert c.post("/api/open-path", json={"path": "x"}).status_code == 401
    assert c.get("/").status_code == 200                 # the page itself loads, to show the passcode box
    assert c.post("/api/login", json={"code": "wrong"}).status_code == 401
    assert c.get("/api/profiles").status_code == 401
    # codes from the environment (a cloud server) cannot be replaced from the page
    assert c.post("/api/access/setup", json={"owner": "A", "staff": "B"}).status_code == 403


def test_forged_cookie_is_refused():
    c = client()
    c.cookies.set("sf_session", "owner.0000")
    assert c.get("/api/profiles").status_code == 401


@pytest.fixture(scope="module")
def job():
    c = client("TEST-OWNER 1")
    jid = c.post("/api/open-path", json={"path": str(DEMO)}).json()["id"]
    for _ in range(300):
        if c.get(f"/api/jobs/{jid}").json()["state"] in ("done", "error"):
            break
        time.sleep(0.2)
    assert c.get(f"/api/jobs/{jid}").json()["state"] == "done"
    return jid


def names(resp):
    return zipfile.ZipFile(io.BytesIO(resp.content)).namelist()


def test_staff_sees_no_prices(job):
    c = client("TEST-STAFF")
    info = c.get("/api/info").json()
    assert info["logged_in"] is True and info["owner"] is False
    assert "pricing" not in c.get("/api/profiles").json()
    assert c.get(f"/api/jobs/{job}/pricing").status_code == 404
    assert c.put(f"/api/jobs/{job}/pricing", json={}).status_code == 404
    assert c.get(f"/api/jobs/{job}/export/pricing").status_code == 404
    z = names(c.get(f"/api/jobs/{job}/export/all"))
    assert any(n.endswith(".dxf") for n in z)
    assert not any("ราคา" in n or "price" in n.lower() for n in z), z
    assert "pricing" not in c.get(f"/api/jobs/{job}").json()["project"]


def test_staff_edit_keeps_owner_prices(job):
    owner, staff = client("TEST-OWNER 1"), client("TEST-STAFF")
    before = owner.get("/api/profiles").json()["pricing"]
    data = staff.get("/api/profiles").json()
    data["pricing"] = {"prices": []}                     # even if sent, staff cannot change prices
    assert staff.put("/api/profiles", json=data).status_code == 200
    assert owner.get("/api/profiles").json()["pricing"] == before


def test_owner_sees_prices(job):
    c = client("TEST-OWNER 1")
    assert c.get("/api/info").json()["owner"] is True
    assert "pricing" in c.get("/api/profiles").json()
    assert c.get(f"/api/jobs/{job}/pricing").status_code == 200
    assert c.get(f"/api/jobs/{job}/export/pricing").status_code == 200
    z = names(c.get(f"/api/jobs/{job}/export/all"))
    assert any("ราคา" in n or "price" in n.lower() for n in z), z


def test_logout():
    c = client("TEST-STAFF")
    assert c.post("/api/logout").status_code == 200
    assert c.get("/api/profiles").status_code == 401
