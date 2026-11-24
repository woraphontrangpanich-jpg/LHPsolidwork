"""Passcode login with two roles.

* staff - everything except pricing
* owner - everything, including pricing (the pricing dialog, the pricing workbook, prices in exports)

No codes are written in the program. Each PC keeps its own in <data>/access.json (salted, slow hashes):
* the first person who opens a fresh install sets an owner code and a staff code;
* the owner can then make a "staff installer": the program's own Setup.exe with the codes added at its
  end. Whoever installs from it gets the owner's codes and goes straight to the passcode screen;
* SHEETFLAT_STAFF_CODE / SHEETFLAT_OWNER_CODE (both set) override the file - used on a cloud server,
  where a first-visitor setup page would let anyone claim the server.

The page asks for a passcode and never says which role it gives. The login lives in a cookie signed with
a key made when the program starts, so opening the program again asks for the passcode again.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import threading
from pathlib import Path

STAFF, OWNER = "staff", "owner"
COOKIE = "sf_session"
ITER = 200_000
MARK, END = b"\nSFACCESS1", b"SFEND"        # the codes added to the end of a staff installer

_KEY = secrets.token_bytes(32)
_lock = threading.Lock()


def _norm(code: str) -> str:
    return re.sub(r"\s+", "", str(code or ""))


def _hash(code: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", _norm(code).encode("utf-8"), bytes.fromhex(salt), ITER).hex()


def _entry(code: str) -> dict:
    salt = secrets.token_hex(16)
    return {"salt": salt, "hash": _hash(code, salt)}


def _valid(d) -> bool:
    try:
        return all(isinstance(d[r]["salt"], str) and len(d[r]["hash"]) == 64 for r in (STAFF, OWNER))
    except (KeyError, TypeError):
        return False


class Access:
    def __init__(self, path: Path):
        self.path = Path(path)

    # ---------------------------------------------------------------- stored codes
    def _env(self) -> dict | None:
        s, o = os.environ.get("SHEETFLAT_STAFF_CODE", ""), os.environ.get("SHEETFLAT_OWNER_CODE", "")
        if _norm(s) and _norm(o):
            return {"env": True, STAFF: s, OWNER: o}
        return None

    def _load(self) -> dict | None:
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return d if _valid(d) else None

    def from_env(self) -> bool:
        return self._env() is not None

    def configured(self) -> bool:
        return self.from_env() or self._load() is not None

    def set_codes(self, staff: str, owner: str, source_id: str | None = None):
        """Replace both codes (raises ValueError when a code is empty or both are the same)."""
        if not _norm(staff) or not _norm(owner):
            raise ValueError("กรุณาใส่รหัสทั้ง 2 ช่อง")
        if _norm(staff) == _norm(owner):
            raise ValueError("รหัสทั้ง 2 ช่องต้องไม่เหมือนกัน")
        if len(_norm(staff)) > 100 or len(_norm(owner)) > 100:
            raise ValueError("รหัสยาวเกินไป")
        d = {"version": 1, "id": secrets.token_hex(8), STAFF: _entry(staff), OWNER: _entry(owner)}
        if source_id:
            d["from_installer"] = source_id
        self._save(d)

    def _save(self, d: dict):
        with _lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(d, indent=1), encoding="utf-8")
            tmp.replace(self.path)

    def role_for_code(self, code: str) -> str | None:
        """The role a passcode gives (owner checked first), or None. Letters must match exactly;
        spaces are ignored, so "ABC 123" and "ABC123" are the same code."""
        if not _norm(code):
            return None
        env = self._env()
        if env:
            for role in (OWNER, STAFF):
                if hmac.compare_digest(_norm(code).encode(), _norm(env[role]).encode()):
                    return role
            return None
        d = self._load()
        if d is None:
            return None
        for role in (OWNER, STAFF):
            if hmac.compare_digest(_hash(code, d[role]["salt"]), d[role]["hash"]):
                return role
        return None

    # ---------------------------------------------------------------- staff installer
    def block(self) -> bytes:
        """The codes as added to the end of a staff installer (hashes only, never the codes)."""
        d = self._load()
        if d is None:
            raise ValueError("ยังไม่ได้ตั้งรหัส")
        out = {"id": d.get("id") or secrets.token_hex(8), STAFF: d[STAFF], OWNER: d[OWNER]}
        return MARK + json.dumps(out).encode("ascii") + END

    def import_from_installer(self, setup: Path) -> bool:
        """Take the codes a staff installer carries, once per installer; True when they were taken."""
        d = read_block(setup)
        if d is None:
            return False
        cur = self._load()
        if cur is not None and d["id"] in (cur.get("id"), cur.get("from_installer")):
            return False                    # already taken (or this PC made that installer)
        self._save({"version": 1, "id": secrets.token_hex(8), "from_installer": d["id"],
                    STAFF: d[STAFF], OWNER: d[OWNER]})
        return True


def _tail(setup: Path, n: int = 4096) -> bytes:
    with open(setup, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - n))
        return f.read()


def read_block(setup: Path) -> dict | None:
    try:
        tail = _tail(setup)
    except OSError:
        return None
    if not tail.endswith(END) or MARK not in tail:
        return None
    raw = tail[tail.rindex(MARK) + len(MARK):-len(END)]
    try:
        d = json.loads(raw.decode("ascii"))
    except (ValueError, UnicodeDecodeError):
        return None
    return d if _valid(d) and isinstance(d.get("id"), str) else None


def installer_size(setup: Path) -> int:
    """Length of the plain installer (without codes another PC may have added)."""
    size = setup.stat().st_size
    try:
        tail = _tail(setup)
    except OSError:
        return size
    if tail.endswith(END) and MARK in tail:
        return size - (len(tail) - tail.rindex(MARK))
    return size


# ------------------------------------------------------------------ session cookie
def make_token(role: str) -> str:
    sig = hmac.new(_KEY, role.encode(), hashlib.sha256).hexdigest()
    return f"{role}.{sig}"


def role_from_token(token: str | None) -> str | None:
    if not token or "." not in token:
        return None
    role, sig = token.split(".", 1)
    if role not in (STAFF, OWNER):
        return None
    good = hmac.new(_KEY, role.encode(), hashlib.sha256).hexdigest()
    return role if hmac.compare_digest(sig, good) else None


# API paths that only the owner may use
_OWNER_ONLY = re.compile(r"^/api/(jobs/[^/]+/(pricing|export/pricing)|access|access/installer)$")
# API paths anyone may use before logging in
_OPEN = {"/api/login", "/api/logout", "/api/info", "/api/access/setup"}


def needs_login(path: str) -> bool:
    return path.startswith("/api/") and path not in _OPEN


def owner_only(path: str) -> bool:
    return bool(_OWNER_ONLY.match(path))
