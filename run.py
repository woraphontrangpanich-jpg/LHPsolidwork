"""Start SheetFlat:  python run.py   (opens http://127.0.0.1:8765)"""
import os
import sys
import threading
import webbrowser

import uvicorn

HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8765"))

if __name__ == "__main__":
    if "--no-browser" not in sys.argv:
        threading.Timer(1.5, lambda: webbrowser.open(f"http://{HOST}:{PORT}")).start()
    uvicorn.run("sheetflat.app:app", host=HOST, port=PORT, log_level="warning")
