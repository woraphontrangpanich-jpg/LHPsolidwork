"""SolidWorks files (.sldasm / .sldprt) -> STEP through the SolidWorks COM API.

SolidWorks' file format is proprietary: no library can read it, only SolidWorks itself.
This works only on a Windows PC with SolidWorks installed (and pywin32).  The assembly's
parts must sit next to it (or where SolidWorks finds them), because an .sldasm only stores
references to its .sldprt files.

NOTE: written against the public SolidWorks API but NOT tested here (no SolidWorks on the
development machine).  The STEP route is the tested one.
"""
from __future__ import annotations

import os
from pathlib import Path


class SolidWorksUnavailable(Exception):
    pass


def solidworks_available() -> bool:
    try:
        import win32com.client  # noqa: F401
    except Exception:
        return False
    try:
        import winreg
        winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, "SldWorks.Application").Close()
        return True
    except Exception:
        return False


def convert_to_step(src: str | Path, dst: str | Path) -> Path:
    if not solidworks_available():
        raise SolidWorksUnavailable(
            "เครื่องนี้ไม่มี SolidWorks (หรือไม่ได้ติดตั้ง pywin32) - ไฟล์ .sldasm/.sldprt เปิดได้เฉพาะด้วย SolidWorks "
            "กรุณา Save As เป็น .step แล้วอัปโหลดไฟล์ STEP แทน")
    import pythoncom
    import win32com.client as wc
    from win32com.client import VARIANT

    src = str(Path(src).resolve())
    dst = str(Path(dst).resolve())
    ext = Path(src).suffix.lower()
    doc_type = 2 if ext == ".sldasm" else 1       # swDocASSEMBLY / swDocPART
    pythoncom.CoInitialize()
    sw = wc.Dispatch("SldWorks.Application")
    sw.Visible = False
    err = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
    warn = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
    model = sw.OpenDoc6(src, doc_type, 1, "", err, warn)      # 1 = swOpenDocOptions_Silent
    if model is None:
        raise RuntimeError(f"SolidWorks เปิดไฟล์ไม่ได้ (error {err.value})")
    try:
        ok = model.Extension.SaveAs3(dst, 0, 1, None, None, err, warn)
        if not ok or not os.path.exists(dst):
            raise RuntimeError(f"SolidWorks บันทึกเป็น STEP ไม่สำเร็จ (error {err.value})")
    finally:
        try:
            sw.CloseDoc(model.GetTitle)
        except Exception:
            pass
    return Path(dst)
