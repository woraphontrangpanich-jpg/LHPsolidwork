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
from urllib.parse import quote, urlsplit

import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

from .core.dxf_export import build_dxf, dxf_bytes
from .core.profiles import ProfileStore
from .core.project import Project
from .core.step_reader import StepReadError
from .core.sw_convert import SolidWorksUnavailable, convert_to_step, solidworks_available
from .core.xlsx_export import build_xlsx

import os
import time

ROOT = Path(__file__).resolve().parent
# CLOUD mode (set SHEETFLAT_CLOUD=1 in the container): the server is reachable by other people, so
# - opening files by path on the server is disabled
# - uploaded files are deleted as soon as they are read, jobs expire after JOB_TTL seconds
CLOUD = os.environ.get("SHEETFLAT_CLOUD", "") not in ("", "0", "false")
MAX_UPLOAD_MB = int(os.environ.get("SHEETFLAT_MAX_UPLOAD_MB", "100"))
JOB_TTL = int(os.environ.get("SHEETFLAT_JOB_TTL", str(2 * 3600)))
DATA = Path(os.environ.get("SHEETFLAT_DATA", str(ROOT.parent / "data")))
WEB = ROOT / "web"
DATA.mkdir(parents=True, exist_ok=True)
(DATA / "uploads").mkdir(exist_ok=True)
# Local mode: only pages served by this program may talk to it. Blocks other web sites the user has
# open from driving the local API (cross-site form posts, DNS rebinding). Extra names for LAN use:
# SHEETFLAT_ALLOWED_HOSTS="192.168.1.20,shop-pc"
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "testserver"} | {
    h.strip().lower() for h in os.environ.get("SHEETFLAT_ALLOWED_HOSTS", "").split(",") if h.strip()}

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


def _hostname(value: str | None) -> str:
    try:
        return (urlsplit(value if "//" in value else "//" + value).hostname or "").lower() if value else ""
    except ValueError:
        return ""


@app.middleware("http")
async def local_only(request, call_next):
    if not CLOUD:
        if _hostname(request.headers.get("host")) not in LOCAL_HOSTS:
            return PlainTextResponse("SheetFlat: host not allowed", status_code=403)
        origin = request.headers.get("origin")
        if origin is not None and request.method not in ("GET", "HEAD", "OPTIONS") \
                and _hostname(origin) not in LOCAL_HOSTS:
            return PlainTextResponse("SheetFlat: request from another site blocked", status_code=403)
    return await call_next(request)


def _expire_jobs():
    now = time.time()
    for jid in [j for j, p in jobs.items() if now - p.created > JOB_TTL]:
        jobs.pop(jid, None)


def get_job(jid: str) -> Project:
    _expire_jobs()
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
        if CLOUD and tmpdir is not None:
            shutil.rmtree(tmpdir, ignore_errors=True)       # do not keep customer files on the server
    except (StepReadError, SolidWorksUnavailable) as ex:
        pr.error = str(ex)
        pr.progress.update(state="error", msg=str(ex))
    except Exception as ex:   # noqa
        traceback.print_exc()
        pr.error = f"{type(ex).__name__}: {ex}"
        pr.progress.update(state="error", msg=pr.error)
    finally:
        if CLOUD and tmpdir is not None:
            shutil.rmtree(tmpdir, ignore_errors=True)


def _start(path: Path, source_name: str, material: str, profile: str | None, tmpdir: Path | None = None) -> Project:
    pr = Project(store, Path(source_name).stem, source_name,
                 {"material": material, "profile": profile or store.data["active"]})
    _expire_jobs()
    jobs[pr.id] = pr
    while len(jobs) > MAX_JOBS:
        old_id, _ = jobs.popitem(last=False)
        shutil.rmtree(DATA / "uploads" / old_id, ignore_errors=True)
    threading.Thread(target=_run, args=(pr, path, tmpdir), daemon=True).start()
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
    size = 0
    with open(path, "wb") as fh:
        while chunk := await file.read(1 << 20):
            size += len(chunk)
            if size > MAX_UPLOAD_MB * 1024 * 1024:
                fh.close()
                shutil.rmtree(tmp, ignore_errors=True)
                raise HTTPException(413, f"ไฟล์ใหญ่เกิน {MAX_UPLOAD_MB} MB")
            fh.write(chunk)
    pr = _start(path, name, material, profile, tmpdir=tmp)
    return {"id": pr.id}


@app.post("/api/open-path")
async def open_path(body: dict):
    """Open a file that is already on this PC (the server runs locally) - no upload needed."""
    if CLOUD:
        raise HTTPException(403, "เวอร์ชันออนไลน์เปิดไฟล์ด้วยพาธไม่ได้ กรุณาอัปโหลดไฟล์")
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
    return {"solidworks": solidworks_available(), "cloud": CLOUD, "max_upload_mb": MAX_UPLOAD_MB}


@app.get("/healthz")
def healthz():
    return {"ok": True}


# ------------------------------------------------------------------ static
app.mount("/static", StaticFiles(directory=str(WEB)), name="static")


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")
