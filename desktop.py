"""SheetFlat as a desktop program (used by the installed version, started with pythonw.exe).

* no console window: output goes to a log file in the data folder
* the program shows in its own window (Microsoft Edge in app mode, which every Windows 10/11 has)
* closing that window stops SheetFlat
* starting it again while it runs just opens another window on the running program
"""
from __future__ import annotations

import ctypes
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
import webbrowser
from pathlib import Path

APP_NAME = "SheetFlat"
LOCAL = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / APP_NAME
os.environ.setdefault("SHEETFLAT_DATA", str(LOCAL / "data"))
DATA = Path(os.environ["SHEETFLAT_DATA"])
# Edge profile used only for the SheetFlat window, one per program folder: two copies of SheetFlat on
# one PC must not share it, or closing one window would not stop that copy
PROFILE = LOCAL / ("window-" + __import__("hashlib").sha1(str(Path(__file__).resolve().parent).lower().encode()).hexdigest()[:8])
LOG = LOCAL / "sheetflat.log"
PREFERRED_PORT = 8765


def message(text: str, error: bool = False):
    flags = 0x10 if error else 0x40          # MB_ICONERROR / MB_ICONINFORMATION
    try:
        ctypes.windll.user32.MessageBoxW(None, text, APP_NAME, flags | 0x10000)   # MB_SETFOREGROUND
    except Exception:
        pass


def is_sheetflat(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/info", timeout=1.5) as r:
            return "solidworks" in json.loads(r.read().decode("utf-8"))
    except Exception:
        return False


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def pick_port() -> int:
    for p in range(PREFERRED_PORT, PREFERRED_PORT + 20):
        if port_free(p):
            return p
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def find_browser() -> str | None:
    """Edge first (always on Windows 10/11), then Chrome."""
    roots = [os.environ.get(k) for k in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA")]
    rel = [r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe"]
    for r in rel:
        for root in roots:
            if root and (Path(root) / r).is_file():
                return str(Path(root) / r)
    return shutil.which("msedge") or shutil.which("chrome")


def open_window(url: str) -> subprocess.Popen | None:
    exe = find_browser()
    if not exe:
        return None
    PROFILE.mkdir(parents=True, exist_ok=True)
    args = [exe, f"--app={url}", f"--user-data-dir={PROFILE}", "--no-first-run",
            "--no-default-browser-check", "--disable-background-mode",
            "--disable-features=msEdgeStartupBoost,msImplicitSignin", "--window-size=1440,900"]
    return subprocess.Popen(args, creationflags=0x08000000)   # CREATE_NO_WINDOW


def wait_ready(port: int, server_thread: threading.Thread, seconds: float = 120) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        if not server_thread.is_alive():
            return False
        if is_sheetflat(port):
            return True
        time.sleep(0.25)
    return False


def window_open() -> bool:
    """True while any browser process still uses the SheetFlat window profile."""
    try:
        out = subprocess.run(
            ["wmic", "process", "where", "name='msedge.exe' or name='chrome.exe'", "get", "CommandLine"],
            capture_output=True, text=True, timeout=10, creationflags=0x08000000).stdout
    except Exception:
        out = None
    if out is None:
        try:   # wmic is missing on newer Windows: ask PowerShell instead
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-CimInstance Win32_Process -Filter \"name='msedge.exe' or name='chrome.exe'\" | "
                 "ForEach-Object CommandLine"],
                capture_output=True, text=True, timeout=20, creationflags=0x08000000).stdout
        except Exception:
            return False
    return str(PROFILE).lower() in out.lower()


def main():
    LOCAL.mkdir(parents=True, exist_ok=True)
    DATA.mkdir(parents=True, exist_ok=True)
    log = open(LOG, "w", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = log
    print(APP_NAME, "starting", time.ctime(), "python", sys.version.split()[0])

    # already running: just open another window on it
    if is_sheetflat(PREFERRED_PORT):
        if not open_window(f"http://127.0.0.1:{PREFERRED_PORT}"):
            webbrowser.open(f"http://127.0.0.1:{PREFERRED_PORT}")
        return

    port = pick_port()
    url = f"http://127.0.0.1:{port}"
    import uvicorn
    config = uvicorn.Config("sheetflat.app:app", host="127.0.0.1", port=port, log_level="warning",
                            log_config=None)
    server = uvicorn.Server(config)
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    if not wait_ready(port, t):
        message("SheetFlat เปิดไม่สำเร็จ\nSheetFlat could not start.\n\nLog: " + str(LOG), error=True)
        return
    print("ready at", url)

    proc = open_window(url)
    if proc is None:
        webbrowser.open(url)
        message("SheetFlat กำลังทำงานในเบราว์เซอร์ กด OK เพื่อปิดโปรแกรม\n"
                "SheetFlat is running in your browser. Press OK to close it.")
    else:
        proc.wait()
        # Edge can hand the window to a process it already had open for this profile;
        # keep running until no process uses the SheetFlat profile any more
        time.sleep(2)
        while window_open():
            time.sleep(3)
    print("window closed, stopping")
    server.should_exit = True
    t.join(timeout=10)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        try:
            traceback.print_exc()
        finally:
            message("SheetFlat เกิดข้อผิดพลาด\nSheetFlat hit an error.\n\nLog: " + str(LOG), error=True)
