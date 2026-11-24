"""Build the ready-to-run Windows package:  dist/SheetFlat-windows.zip

    python tools/build_portable.py [--test]

The ZIP holds the official embeddable Python (checked against the SHA-256 python.org
publishes), every package from requirements.txt (pip checks each download against the
hash in that file) and the SheetFlat code. The customer extracts it and double-clicks
SheetFlat.cmd: no installer, no PowerShell, no internet, no administrator rights.

--test  runs the packed program on docs/demo/DemoCabinet.step and checks the result.
--installer  also builds dist/SheetFlat-Setup.exe with Inno Setup (installer/sheetflat.iss).
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"
DIST = ROOT / "dist"

PY_VERSION = "3.14.7"
PY_TAG = "314"
PY_URL = f"https://www.python.org/ftp/python/{PY_VERSION}/python-{PY_VERSION}-embed-amd64.zip"
# "Windows embeddable package (64-bit)" on https://www.python.org/downloads/release/python-3147/
PY_SHA256 = "d297e5ff019966817ad8502465176139f2d3d840fa4ed84b13bed399a6ab1f15"

# cadquery-ocp needs the vtk DLLs (OCP.pyd links them), but matplotlib - pulled in by vtk's
# Python plotting helpers - is never imported by SheetFlat. --test proves the program runs without it.
SKIP = {"matplotlib", "contourpy", "cycler", "kiwisolver", "packaging", "pillow",
        "python-dateutil", "tzdata"}

APP_FILES = ["run.py", "desktop.py", "LICENSE", "README.md", "INSTALL.md"]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def version() -> str:
    """sheetflat.__version__; a release tag (SHEETFLAT_VERSION=v1.2.0) must say the same."""
    import re as _re
    src = (ROOT / "sheetflat" / "__init__.py").read_text(encoding="utf-8")
    code_v = _re.search(r'__version__ = "([^"]+)"', src).group(1)
    tag = os.environ.get("SHEETFLAT_VERSION", "").strip().lstrip("v")
    if tag and tag != code_v:
        sys.exit(f"release tag v{tag} does not match sheetflat.__version__ {code_v} - stopped")
    return code_v


def fetch_python(cache: Path) -> Path:
    cache.mkdir(parents=True, exist_ok=True)
    z = cache / Path(PY_URL).name
    if not z.exists() or sha256(z) != PY_SHA256:
        print("downloading", PY_URL)
        urllib.request.urlretrieve(PY_URL, z)
    got = sha256(z)
    if got != PY_SHA256:
        z.unlink()
        sys.exit(f"Python download does not match the published SHA-256 ({got}) - stopped")
    return z


def runtime_requirements(dest: Path) -> list[str]:
    """requirements.txt without the SKIP packages (hashes kept)."""
    blocks, cur = [], None
    for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Za-z0-9_.-]+)==", line)
        if m:
            cur = [m.group(1).lower().replace("_", "-"), [line]]
            blocks.append(cur)
        elif cur and line.startswith((" ", "\t")):
            cur[1].append(line)
        else:
            cur = None
    kept = [b for b in blocks if b[0] not in SKIP]
    dest.write_text("\n".join(l for _, ls in kept for l in ls if not l.strip().startswith("#")) + "\n",
                    encoding="utf-8")
    return [b[0] for b in kept]


def build() -> Path:
    ver = version()
    pkg = BUILD / "SheetFlat"
    if pkg.exists():
        shutil.rmtree(pkg)
    pydir = pkg / "python"
    with zipfile.ZipFile(fetch_python(BUILD / "cache")) as z:
        z.extractall(pydir)

    # search path of the embedded Python: its own stdlib, the packages, the app
    (pydir / f"python{PY_TAG}._pth").write_text(
        f"python{PY_TAG}.zip\n.\nLib\\site-packages\n..\\app\n", encoding="utf-8")

    req = BUILD / "requirements-portable.txt"
    names = runtime_requirements(req)
    print("packages:", ", ".join(names))
    subprocess.run([
        sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--no-input",
        "--require-hashes", "--no-deps", "--only-binary=:all:",
        "--platform", "win_amd64", "--python-version", PY_VERSION.rsplit(".", 1)[0],
        "--implementation", "cp", "--abi", f"cp{PY_TAG}",
        "--target", str(pydir / "Lib" / "site-packages"), "-r", str(req),
    ], check=True)
    for junk in (pydir / "Lib" / "site-packages").glob("bin"):
        shutil.rmtree(junk)

    app = pkg / "app"
    shutil.copytree(ROOT / "sheetflat", app / "sheetflat",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for f in APP_FILES:
        if (ROOT / f).exists():
            shutil.copy(ROOT / f, app / f)
    (app / "VERSION").write_text(ver + "\n", encoding="utf-8")

    # double-click launcher; settings live outside the program folder so a new version keeps them
    (pkg / "SheetFlat.cmd").write_bytes((
        "@echo off\r\n"
        "rem SheetFlat - double-click to start. Keep this window open while you work.\r\n"
        "title SheetFlat\r\n"
        "cd /d \"%~dp0\"\r\n"
        "if not exist \"%~dp0python\\python.exe\" (\r\n"
        "  echo.\r\n"
        "  echo  Extract the ZIP first: right-click SheetFlat-windows.zip, choose Extract All,\r\n"
        "  echo  then open the SheetFlat folder and double-click SheetFlat.cmd there.\r\n"
        "  echo.\r\n"
        "  pause\r\n"
        "  exit /b 1\r\n"
        ")\r\n"
        "if not defined SHEETFLAT_DATA set \"SHEETFLAT_DATA=%LOCALAPPDATA%\\SheetFlat\\data\"\r\n"
        "echo.\r\n"
        f"echo  SheetFlat {ver}\r\n"
        "echo  Running at http://127.0.0.1:8765 - the browser opens by itself.\r\n"
        "echo  Keep this window open while you work. Close it to stop SheetFlat.\r\n"
        "echo.\r\n"
        "\"%~dp0python\\python.exe\" -s \"%~dp0app\\run.py\" %*\r\n"
        "if errorlevel 1 pause\r\n").encode("ascii"))
    (pkg / "อ่านก่อน.txt").write_text(
        f"SheetFlat {ver}\r\n\r\n"
        "เปิดโปรแกรม: ดับเบิลคลิก SheetFlat.cmd\r\n"
        "เบราว์เซอร์จะเปิดหน้า http://127.0.0.1:8765 ให้เอง ห้ามปิดหน้าต่างสีดำระหว่างใช้งาน\r\n"
        "ไฟล์ทั้งหมดอยู่ในเครื่องนี้เท่านั้น ไม่ต้องต่ออินเทอร์เน็ต\r\n\r\n"
        "อัปเดต: ดาวน์โหลด ZIP ใหม่ แตกไฟล์ทับโฟลเดอร์เดิม (ตารางค่ายืดเก็บไว้ที่\r\n"
        "%LOCALAPPDATA%\\SheetFlat\\data จึงไม่หาย)\r\n"
        "ถอนการติดตั้ง: ลบโฟลเดอร์นี้\r\n", encoding="utf-8-sig")

    DIST.mkdir(exist_ok=True)
    out = DIST / "SheetFlat-windows.zip"
    if out.exists():
        out.unlink()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in sorted(pkg.rglob("*")):
            if p.is_file():
                z.write(p, Path("SheetFlat") / p.relative_to(pkg))
    digest = sha256(out)
    (DIST / "SHA256SUMS.txt").write_text(f"{digest}  {out.name}\n", encoding="ascii")
    size = sum(p.stat().st_size for p in pkg.rglob("*") if p.is_file())
    print(f"built {out}  {out.stat().st_size / 1e6:.0f} MB (unpacked {size / 1e6:.0f} MB)  version {ver}")
    print(f"sha256 {digest}")
    return pkg


SMOKE = r"""
import sys, tempfile, pathlib
from sheetflat.core.profiles import ProfileStore
from sheetflat.core.project import Project
from sheetflat.app import app
pr = Project(ProfileStore(pathlib.Path(tempfile.mkdtemp()) / "p.json"), "Demo", "DemoCabinet.step")
pr.load_step(sys.argv[1])
got = {g.code: sum(len(s.placed) for s in g.sheets) for g in pr.groups}
print("groups", got, "python", sys.version.split()[0], "exe", sys.executable)
assert got == {"ZN#22": 6, "ZN#19": 4, "ZN#14": 5}, got
assert "matplotlib" not in sys.modules
print("SMOKE OK")
"""


def smoke(pkg: Path):
    """Run the packed Python the way SheetFlat.cmd does, with a clean environment."""
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("PYTHON", "VIRTUAL_ENV"))}
    env["PATH"] = os.environ.get("SystemRoot", r"C:\Windows") + r"\System32"
    step = ROOT / "docs" / "demo" / "DemoCabinet.step"
    r = subprocess.run([str(pkg / "python" / "python.exe"), "-s", "-c", SMOKE, str(step)],
                       cwd=pkg, env=env, capture_output=True, text=True)
    print(r.stdout, r.stderr)
    if r.returncode != 0 or "SMOKE OK" not in r.stdout:
        sys.exit("smoke test failed")


def find_iscc() -> str:
    for base in (os.environ.get("LOCALAPPDATA"), os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles")):
        for sub in (r"Programs\Inno Setup 6\ISCC.exe", r"Inno Setup 6\ISCC.exe"):
            if base and (Path(base) / sub).is_file():
                return str(Path(base) / sub)
    found = shutil.which("ISCC")
    if not found:
        sys.exit("Inno Setup 6 not found - install it: winget install -e --id JRSoftware.InnoSetup")
    return found


def installer():
    """dist/SheetFlat-Setup.exe: installs per user, Start menu + desktop icon, uninstall."""
    subprocess.run([sys.executable, str(ROOT / "tools" / "make_icon.py")], check=True)
    ver = version()
    subprocess.run([find_iscc(), f"/DAppVersion={ver}", "/Q", str(ROOT / "installer" / "sheetflat.iss")], check=True)
    out = DIST / "SheetFlat-Setup.exe"
    digest = sha256(out)
    with open(DIST / "SHA256SUMS.txt", "a", encoding="ascii") as fh:
        fh.write(f"{digest}  {out.name}\n")
    print(f"built {out}  {out.stat().st_size / 1e6:.0f} MB")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--installer", action="store_true")
    a = ap.parse_args()
    p = build()
    if a.test:
        smoke(p)
    if a.installer:
        installer()
