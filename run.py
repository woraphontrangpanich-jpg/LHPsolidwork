"""Start SheetFlat:  python run.py   (opens http://127.0.0.1:8765)"""
import os
import socket
import sys
import threading
import webbrowser

HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8765"))


def port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host if host != "0.0.0.0" else "127.0.0.1", port)) == 0


if __name__ == "__main__":
    url = f"http://127.0.0.1:{PORT}"
    open_browser = "--no-browser" not in sys.argv
    if port_in_use(HOST, PORT):
        # already running (e.g. start.bat double-clicked twice): just open the browser
        print(f"SheetFlat is already running at {url}")
        if open_browser:
            webbrowser.open(url)
        sys.exit(0)
    import uvicorn
    if open_browser:
        threading.Timer(2.0, lambda: webbrowser.open(url)).start()
    uvicorn.run("sheetflat.app:app", host=HOST, port=PORT, log_level="warning")
