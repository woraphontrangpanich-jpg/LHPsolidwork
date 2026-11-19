"""The local server must only answer its own page (no sample files needed, runs in CI)."""
from pathlib import Path
from fastapi.testclient import TestClient

from sheetflat.app import app

client = TestClient(app)


def test_foreign_host_name_is_refused():
    # DNS rebinding: a foreign name pointing at 127.0.0.1
    assert client.get("/api/info", headers={"host": "evil.example:8765"}).status_code == 403


def test_posts_from_other_sites_are_refused():
    # a page on another site posting to the local API (form posts need no CORS preflight)
    r = client.post("/api/upload", files={"file": ("x.step", b"x")}, headers={"origin": "https://evil.example"})
    assert r.status_code == 403
    assert client.put("/api/profiles", json={}, headers={"origin": "null"}).status_code == 403


def test_own_page_is_allowed_on_any_loopback_name():
    for host, origin in (("127.0.0.1:8765", "http://127.0.0.1:8765"), ("localhost:8765", "http://localhost:8765"),
                         ("[::1]:8765", "http://[::1]:8765")):
        assert client.get("/api/info", headers={"host": host}).status_code == 200
        r = client.post("/api/upload", files={"file": ("x.txt", b"x")}, headers={"host": host, "origin": origin})
        assert r.status_code == 400          # reached the endpoint (rejected only for the file type)


def test_cloud_mode_skips_the_host_check(monkeypatch):
    import sheetflat.app as A
    monkeypatch.setattr(A, "CLOUD", True)
    assert client.get("/healthz", headers={"host": "sheetflat.example.com"}).status_code == 200


def test_solidworks_conversion_never_writes_next_to_the_customer_file(tmp_path, monkeypatch):
    """Opening E:\job\A.SLDASM by path must not create or overwrite E:\job\A.step."""
    import time
    import sheetflat.app as A
    src = tmp_path / "job" / "A.SLDASM"
    src.parent.mkdir()
    src.write_bytes(b"not really solidworks")
    keep = src.parent / "A.step"
    keep.write_text("customer's own STEP", encoding="utf-8")
    seen = {}

    def fake_convert(s, d):
        seen["dst"] = str(d)
        raise A.SolidWorksUnavailable("test")
    monkeypatch.setattr(A, "convert_to_step", fake_convert)
    pr = A._start(src, src.name, "ZN", None)
    for _ in range(50):
        if pr.progress["state"] == "error":
            break
        time.sleep(0.05)
    assert seen and src.parent.resolve() not in Path(seen["dst"]).resolve().parents
    assert keep.read_text(encoding="utf-8") == "customer's own STEP"
