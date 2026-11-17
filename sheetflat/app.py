"""FastAPI application: upload -> analyse -> preview -> export."""
from __future__ import annotations

import io
import json
import shutil
import threading
import traceback
import zipfile
from collections import OrderedDict
from pathlib import Path
from urllib.parse import quote

import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .core.dxf_export import build_dxf, dxf_bytes
from .core.profiles import ProfileStore
from .core.project import Project
from .core.step_reader import StepReadError
from .core.sw_convert import SolidWorksUnavailable, convert_to_step, solidworks_available
from .core.xlsx_export import build_xlsx

ROOT = Path(__file__).resolve().parent
DATA = ROOT.parent / "data"
WEB = ROOT / "web"
DATA.mkdir(exist_ok=True)
(DATA / "uploads").mkdir(exist_ok=True)

store = ProfileStore(DATA / "profiles.json")
jobs: "OrderedDict[str, Project]" = OrderedDict()
MAX_JOBS = 8


def _enc(o):
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, (set, frozenset)):
        return sorted(o)
    raise TypeError(type(o))


class J(JSONResponse):
    def render(self, content) -> bytes:
        return json.dumps(content, ensure_ascii=False, default=_enc, allow_nan=False).encode("utf-8")


app = FastAPI(title="SheetFlat", default_response_class=J)


def get_job(jid: str) -> Project:
    pr = jobs.get(jid)
    if pr is None:
        raise HTTPException(404, "ไม่พบงานนี้ (อาจถูกล้างออกจากหน่วยความจำแล้ว) กรุณาอัปโหลดใหม่")
    jobs.move_to_end(jid)
    return pr


# ------------------------------------------------------------------ upload / open
def _run(pr: Project, path: Path, tmpdir: Path | None):
    try:
        suffix = path.suffix.lower()
        if suffix in (".sldasm", ".sldprt"):
            pr.progress.update(state="converting", msg="กำลังให้ SolidWorks แปลงเป็น STEP")
            step = convert_to_step(path, path.with_suffix(".step"))
            pr.load_step(str(step))
        else:
            pr.load_step(str(path))
    except (StepReadError, SolidWorksUnavailable) as ex:
        pr.error = str(ex)
        pr.progress.update(state="error", msg=str(ex))
    except Exception as ex:   # noqa
        traceback.print_exc()
        pr.error = f"{type(ex).__name__}: {ex}"
        pr.progress.update(state="error", msg=pr.error)


def _start(path: Path, source_name: str, material: str, profile: str | None) -> Project:
    pr = Project(store, Path(source_name).stem, source_name,
                 {"material": material, "profile": profile or store.data["active"]})
    jobs[pr.id] = pr
    while len(jobs) > MAX_JOBS:
        old_id, _ = jobs.popitem(last=False)
        shutil.rmtree(DATA / "uploads" / old_id, ignore_errors=True)
    threading.Thread(target=_run, args=(pr, path, None), daemon=True).start()
    return pr


@app.post("/api/upload")
async def upload(file: UploadFile = File(...), material: str = Form("ZN"), profile: str = Form("")):
    name = Path(file.filename or "model.step").name
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in ("step", "stp", "sldasm", "sldprt"):
        raise HTTPException(400, "รองรับเฉพาะไฟล์ .step / .stp / .sldasm / .sldprt")
    if ext in ("sldasm", "sldprt") and not solidworks_available():
        raise HTTPException(
            400, "ไฟล์ SolidWorks (.sldasm) อ่านได้เฉพาะเครื่องที่ติดตั้ง SolidWorks - "
                 "กรุณา Save As เป็น .step แล้วอัปโหลดไฟล์ STEP (รอยพับ/ความหนาอ่านจากไฟล์ STEP ได้ครบ)")
    import uuid
    tmp = DATA / "uploads" / ("u" + uuid.uuid4().hex[:10])
    tmp.mkdir(parents=True, exist_ok=True)
    path = tmp / name
    with open(path, "wb") as fh:
        while chunk := await file.read(1 << 20):
            fh.write(chunk)
    pr = _start(path, name, material, profile)
    return {"id": pr.id}


@app.post("/api/open-path")
async def open_path(body: dict):
    """Open a file that is already on this PC (the server runs locally) - no upload needed."""
    p = Path(str(body.get("path", "")).strip().strip('"'))
    if not p.is_file():
        raise HTTPException(400, "ไม่พบไฟล์ตามพาธที่ระบุ")
    if p.suffix.lower() not in (".step", ".stp", ".sldasm", ".sldprt"):
        raise HTTPException(400, "รองรับเฉพาะไฟล์ .step / .stp / .sldasm / .sldprt")
    pr = _start(p, p.name, body.get("material", "ZN"), body.get("profile"))
    return {"id": pr.id}


@app.get("/api/jobs/{jid}")
def job_state(jid: str):
    pr = get_job(jid)
    st = dict(pr.progress)
    if st["state"] == "done":
        with pr.lock:
            return {"state": "done", "progress": st, "project": pr.to_json()}
    return {"state": st["state"], "progress": st, "error": pr.error}


# ------------------------------------------------------------------ edits
@app.patch("/api/jobs/{jid}/parts/{pid}")
def edit_part(jid: str, pid: str, body: dict):
    pr = get_job(jid)
    try:
        pr.update_part(pid, body)
    except StopIteration:
        raise HTTPException(404, "ไม่พบชิ้นส่วน")
    return pr.to_json()


@app.patch("/api/jobs/{jid}/settings")
def edit_settings(jid: str, body: dict):
    pr = get_job(jid)
    if "material_all" in body:
        pr.set_material_all(body.pop("material_all"))
    if body:
        pr.update_settings(body)
    return pr.to_json()


@app.get("/api/jobs/{jid}/parts/{pid}/geometry")
def part_geometry(jid: str, pid: str):
    pr = get_job(jid)
    rec = next((r for r in pr.parts if r.id == pid), None)
    if rec is None:
        raise HTTPException(404, "ไม่พบชิ้นส่วน")
    with pr.lock:
        return pr.geometry_json(rec)


@app.get("/api/jobs/{jid}/groups/{gid}/layout/{idx}")
def group_layout(jid: str, gid: str, idx: int):
    pr = get_job(jid)
    with pr.lock:
        g = next((x for x in pr.groups if x.id == gid), None)
        if g is None or idx >= len(g.sheets):
            raise HTTPException(404, "ไม่พบกลุ่ม/แผ่น")
        return pr.layout_json(g, idx)


# ------------------------------------------------------------------ profiles
@app.get("/api/profiles")
def get_profiles():
    return store.data


@app.put("/api/profiles")
def put_profiles(body: dict):
    try:
        store.replace(body)
    except (ValueError, KeyError) as ex:
        raise HTTPException(400, f"ข้อมูลโหมดค่ายืดไม่ถูกต้อง: {ex}")
    for pr in jobs.values():           # profile edits take effect on open jobs
        try:
            pr.recompute()
        except Exception:   # noqa
            traceback.print_exc()
    return store.data


# ------------------------------------------------------------------ export
def _attach(data: bytes, filename: str, media: str) -> Response:
    return Response(data, media_type=media,
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"})


def _dxf_for(pr: Project, g, idx: int) -> bytes:
    s = g.sheets[idx]
    doc = build_dxf(s, bend_lines=bool(pr.settings["bend_lines"]), labels=bool(pr.settings["labels"]))
    return dxf_bytes(doc)


@app.get("/api/jobs/{jid}/export/dxf/{gid}/{idx}")
def export_dxf(jid: str, gid: str, idx: int):
    pr = get_job(jid)
    with pr.lock:
        g = next((x for x in pr.groups if x.id == gid), None)
        if g is None or idx >= len(g.sheets):
            raise HTTPException(404, "ไม่พบกลุ่ม/แผ่น")
        return _attach(_dxf_for(pr, g, idx), pr.sheet_filename(g, idx) + ".dxf", "application/dxf")


@app.get("/api/jobs/{jid}/export/xlsx")
def export_xlsx(jid: str):
    pr = get_job(jid)
    with pr.lock:
        return _attach(build_xlsx(pr), f"{pr.job_name}_สรุป.xlsx",
                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.get("/api/jobs/{jid}/export/all")
def export_all(jid: str):
    pr = get_job(jid)
    buf = io.BytesIO()
    with pr.lock, zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for g in pr.groups:
            for s in g.sheets:
                z.writestr(pr.sheet_filename(g, s.index) + ".dxf", _dxf_for(pr, g, s.index))
        z.writestr(f"{pr.job_name}_สรุป.xlsx", build_xlsx(pr))
    return _attach(buf.getvalue(), f"{pr.job_name}_DXF.zip", "application/zip")


@app.get("/api/info")
def info():
    return {"solidworks": solidworks_available()}


# ------------------------------------------------------------------ static
app.mount("/static", StaticFiles(directory=str(WEB)), name="static")


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")
