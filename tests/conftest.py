"""Tests use their own data folder, so they never change the bend tables and prices saved on this PC.

Every TestClient of the SheetFlat app starts logged in as the owner (the passcode screen has its own
tests in test_access.py, which make clients with login=False)."""
import os
import tempfile

os.environ.setdefault("SHEETFLAT_DATA", tempfile.mkdtemp(prefix="sheetflat_test_data_"))
# test-only passcodes (a real install sets its own on first start, see sheetflat/core/access.py)
os.environ["SHEETFLAT_STAFF_CODE"] = "TEST-STAFF"
os.environ["SHEETFLAT_OWNER_CODE"] = "TEST-OWNER 1"

from fastapi.testclient import TestClient  # noqa: E402

_init = TestClient.__init__


def _init_logged_in(self, app, *args, login=True, **kwargs):
    _init(self, app, *args, **kwargs)
    if login and getattr(app, "title", "") == "SheetFlat":
        r = self.post("/api/login", json={"code": "TEST-OWNER 1"})
        assert r.status_code == 200, r.text


TestClient.__init__ = _init_logged_in
