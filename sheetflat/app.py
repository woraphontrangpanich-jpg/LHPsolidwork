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
from .core.pricing_xlsx import build_pricing_xlsx

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
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path.startswith("/static/"):
        # the page and its scripts must always match (after an update an old cached page would break)
        response.headers["Cache-Control"] = "no-cache"
    return response


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
def _run(pr: Project, path: Path, tmpdir: Path | None, state: dict | None = None):
    try:
        suffix = path.suffix.lower()
        if suffix in (".sldasm", ".sldprt"):
            pr.progress.update(state="converting", msg="กำลังให้ SolidWorks แปลงเป็น STEP")
            # convert into the program's own folder, never next to the customer's file (a STEP of the same
            # name there would be overwritten)
            import uuid
            out_dir = Path(tmpdir) if tmpdir else DATA / "uploads" / ("c" + uuid.uuid4().hex[:10])
            out_dir.mkdir(parents=True, exist_ok=True)
            if not tmpdir:
                pr.upload_dir = out_dir                      # removed with the job like an upload
            step = convert_to_step(path, out_dir / (path.stem + "_converted.step"))
            pr.source_path = "" if CLOUD else str(step)
            pr.load_step(str(step), state)
        else:
            pr.source_path = "" if CLOUD else str(path)
            pr.load_step(str(path), state)
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


def _start(path: Path, source_name: str, material: str, profile: str | None, tmpdir: Path | None = None,
           state: dict | None = None, job_name: str | None = None) -> Project:
    settings = {"material": material, "profile": profile or store.data["active"]}
    if state:
        settings.update({k: v for k, v in (state.get("settings") or {}).items() if k != "profile"})
    pr = Project(store, job_name or Path(source_name).stem, source_name, settings)
    pr.upload_dir = tmpdir
    _expire_jobs()
    jobs[pr.id] = pr
    while len(jobs) > MAX_JOBS:
        _, old = jobs.popitem(last=False)
        if getattr(old, "upload_dir", None):
            shutil.rmtree(old.upload_dir, ignore_errors=True)
    threading.Thread(target=_run, args=(pr, path, tmpdir, state), daemon=True).start()
    return pr


async def _receive(file: UploadFile, exts: tuple) -> tuple[Path, Path, str]:
    """Store an upload in its own folder (size-limited)."""
    import uuid
    name = Path(file.filename or "model.step").name
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in exts:
        raise HTTPException(400, "รองรับเฉพาะไฟล์ " + " / ".join("." + e for e in exts))
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
    return tmp, path, name


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
    tmp, path, name = await _receive(file, ("step", "stp", "sldasm", "sldprt"))
    pr = _start(path, name, material, profile, tmpdir=tmp)
    return {"id": pr.id}


# ------------------------------------------------------------------ job file (save / load)
JOB_EXT = "sheetflat"


@app.get("/api/jobs/{jid}/save")
def save_job(jid: str):
    """One file with the STEP model and every decision, to reopen later or send to someone else."""
    pr = get_job(jid)
    with pr.lock:
        if not pr.source_path or not Path(pr.source_path).is_file():
            raise HTTPException(400, "บันทึกงานไม่ได้ เพราะไม่มีไฟล์ STEP ต้นฉบับในเครื่องแล้ว กรุณาเปิดไฟล์ใหม่")
        st = pr.state()
        st["step_name"] = Path(pr.source_path).name
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("job.json", json.dumps(st, ensure_ascii=False, indent=1, default=_enc))
            z.write(pr.source_path, "model/" + st["step_name"])
    return _attach(buf.getvalue(), f"{pr.job_name}.{JOB_EXT}", "application/zip")


def _adopt_profile(prof: dict | None, materials: list | None) -> str | None:
    """Make the sender's bend profile and steels available here; returns the profile id to use."""
    if not prof:
        return None
    data = json.loads(json.dumps(store.data))
    changed = False
    have = {m["id"] for m in data["materials"]}
    for m in materials or []:
        if m.get("id") and m["id"] not in have:
            data["materials"].append({"id": m["id"], "name": m.get("name") or m["id"], "prefix": m.get("prefix") or m["id"]})
            changed = True

    def clean(p):
        return {k: v for k, v in p.items() if k not in ("id", "name")}

    same = next((p for p in data["profiles"] if p["id"] == prof.get("id")), None)
    if same is not None and clean(same) == clean(prof):
        pid = same["id"]
    else:
        twin = next((p for p in data["profiles"] if clean(p) == clean(prof)), None)
        if twin is not None:
            pid = twin["id"]
        else:
            import uuid
            new = dict(prof)
            pid = new["id"] = "f" + uuid.uuid4().hex[:8]
            new["name"] = f"{prof.get('name', 'โหมด')} (จากไฟล์งาน)"
            data["profiles"].append(new)
            changed = True
    if changed:
        active = data["active"]
        store.replace(data)
        store.data["active"] = active
        store.save()
    return pid


@app.post("/api/load")
async def load_job(file: UploadFile = File(...)):
    tmp, path, _ = await _receive(file, (JOB_EXT,))
    try:
        with zipfile.ZipFile(path) as z:
            st = json.loads(z.read("job.json").decode("utf-8"))
            if st.get("format") != "sheetflat-job":
                raise ValueError("ไม่ใช่ไฟล์งาน SheetFlat")
            step_name = Path(st.get("step_name") or "model.step").name
            if Path(step_name).suffix.lower() not in (".step", ".stp"):
                raise ValueError("ไฟล์งานไม่มีโมเดล STEP")
            step = tmp / step_name
            with z.open("model/" + step_name) as src, open(step, "wb") as dst:
                shutil.copyfileobj(src, dst)
    except (KeyError, ValueError, zipfile.BadZipFile, json.JSONDecodeError, UnicodeDecodeError) as ex:
        shutil.rmtree(tmp, ignore_errors=True)
        raise HTTPException(400, f"เปิดไฟล์งานไม่ได้: {ex}")
    path.unlink(missing_ok=True)
    try:
        pid = _adopt_profile(st.get("profile"), st.get("materials"))
    except (ValueError, KeyError) as ex:
        shutil.rmtree(tmp, ignore_errors=True)
        raise HTTPException(400, f"โหมดค่ายืดในไฟล์งานไม่ถูกต้อง: {ex}")
    pr = _start(step, st.get("source") or step_name, (st.get("settings") or {}).get("material", "ZN"),
                pid, tmpdir=tmp, state=st, job_name=st.get("job"))
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
    except ValueError as ex:
        raise HTTPException(400, str(ex))
    return pr.to_json()


@app.post("/api/jobs/{jid}/move")
def move_parts(jid: str, body: dict):
    """Regroup parts: body = {"ids": [...], "target": {"material_id", "thickness"} | {"category"} | {"include": false}}"""
    pr = get_job(jid)
    try:
        pr.move_parts(list(body.get("ids") or []), dict(body.get("target") or {}))
    except ValueError as ex:
        raise HTTPException(400, str(ex))
    return pr.to_json()


@app.post("/api/jobs/{jid}/materials")
def add_material(jid: str, body: dict):
    """A new steel type from the "move parts" bar, saved for later jobs.

    body = {"material": {"id", "name", "prefix"}, "base": "ZN" | "",
            "rows": [{"thickness", "bd", "gauge"}], "price_kg": 120}
    Bend values: copied from the base steel or typed by the user; written as rows of the job's bend
    profile that apply to this steel only. Price rows are added to the job and to the default prices."""
    pr = get_job(jid)
    m = dict(body.get("material") or {})
    mid = str(m.get("id") or "").strip()
    if not mid:
        raise HTTPException(400, "กรุณาใส่รหัสชนิดเหล็ก")
    data = json.loads(json.dumps(store.data))
    if any(x["id"].lower() == mid.lower() for x in data["materials"]):
        raise HTTPException(400, f"มีชนิดเหล็ก {mid} อยู่แล้ว เลือกจากรายการแทน")
    data["materials"].append({"id": mid, "name": str(m.get("name") or mid).strip(), "prefix": str(m.get("prefix") or mid).strip()})
    prof = next((x for x in data["profiles"] if x["id"] == pr.profile["id"]), None)
    rows = []
    for r in body.get("rows") or []:
        try:
            t, bd = float(r.get("thickness") or 0), float(r.get("bd") or 0)
        except (TypeError, ValueError):
            raise HTTPException(400, "ค่ายืดต้องเป็นตัวเลข")
        if t > 0 and bd > 0:
            rows.append({"gauge": str(r.get("gauge") or "").strip(), "thickness": t, "bd": bd, "material": mid})
    if prof is not None:
        prof["table"] = [x for x in prof["table"] if x.get("material") != mid] + rows
    base = str(body.get("base") or "")
    price = body.get("price_kg")
    try:
        price = float(price) if price not in (None, "") else None
    except (TypeError, ValueError):
        raise HTTPException(400, "ราคาต้องเป็นตัวเลข")

    def add_prices(pricing):
        src = [x for x in pricing["prices"] if x["material"] == base]
        for r in rows:
            near = min(src, key=lambda x: abs(x["thickness"] - r["thickness"])) if src else None
            pricing["prices"] = [x for x in pricing["prices"] if not (x["material"] == mid and abs(x["thickness"] - r["thickness"]) < 1e-6)]
            pricing["prices"].append({"material": mid, "gauge": r["gauge"], "thickness": r["thickness"],
                                      "price_kg": price if price is not None else (near["price_kg"] if near else 35),
                                      "kg_plate": round(near["kg_plate"] / near["thickness"] * r["thickness"], 4) if near
                                      else round(24 * r["thickness"], 4)})
    add_prices(data["pricing"])
    try:
        store.replace(data)
    except (ValueError, KeyError) as ex:
        raise HTTPException(400, str(ex))
    add_prices(pr.pricing)
    for job in jobs.values():
        try:
            job.recompute()
        except Exception:   # noqa
            traceback.print_exc()
    return {"profiles": store.data, "project": pr.to_json()}


@app.post("/api/jobs/{jid}/rename")
def rename_group(jid: str, body: dict):
    """body = {"key": "ZN|0.7", "name": "..."} for a DXF group, or {"category": "old", "name": "new"}"""
    pr = get_job(jid)
    try:
        if "category" in body:
            pr.rename_category(str(body["category"]), body.get("name"))
        else:
            pr.rename_group(str(body.get("key") or ""), body.get("name"))
    except ValueError as ex:
        raise HTTPException(400, str(ex))
    return pr.to_json()


@app.get("/api/jobs/{jid}/pricing")
def get_pricing(jid: str):
    return get_job(jid).price()


@app.put("/api/jobs/{jid}/pricing")
def put_pricing(jid: str, body: dict):
    """body = {"pricing": {...}, "meta": {"date", "customer", "note"}, "save_default": bool}"""
    pr = get_job(jid)
    try:
        pr.set_pricing(body.get("pricing"), body.get("meta"))
    except (ValueError, TypeError) as ex:
        raise HTTPException(400, f"ข้อมูลราคาไม่ถูกต้อง: {ex}")
    if body.get("save_default"):
        data = json.loads(json.dumps(store.data))
        data["pricing"] = pr.pricing
        store.replace(data)
    return pr.price()


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


@app.get("/api/jobs/{jid}/parts/{pid}/fold")
def part_fold(jid: str, pid: str):
    pr = get_job(jid)
    rec = next((r for r in pr.parts if r.id == pid), None)
    if rec is None:
        raise HTTPException(404, "ไม่พบชิ้นส่วน")
    try:
        with pr.lock:
            return pr.fold_json(rec)
    except ValueError as ex:
        raise HTTPException(404, str(ex))


@app.get("/api/jobs/{jid}/parts/{pid}/mesh")
def part_mesh(jid: str, pid: str):
    pr = get_job(jid)
    rec = next((r for r in pr.parts if r.id == pid), None)
    if rec is None:
        raise HTTPException(404, "ไม่พบชิ้นส่วน")
    try:
        return pr.mesh_json(rec)
    except ValueError as ex:
        raise HTTPException(404, str(ex))


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


@app.get("/api/jobs/{jid}/export/pricing")
def export_pricing(jid: str):
    pr = get_job(jid)
    with pr.lock:
        return _attach(build_pricing_xlsx(pr), f"{pr.job_name}_ราคา.xlsx",
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
        z.writestr(f"{pr.job_name}_ราคา.xlsx", build_pricing_xlsx(pr))
    return _attach(buf.getvalue(), f"{pr.job_name}_DXF.zip", "application/zip")


@app.get("/api/info")
def info():
    from . import __version__
    return {"solidworks": solidworks_available(), "cloud": CLOUD, "max_upload_mb": MAX_UPLOAD_MB, "version": __version__}


@app.get("/healthz")
def healthz():
    return {"ok": True}


# ------------------------------------------------------------------ static
app.mount("/static", StaticFiles(directory=str(WEB)), name="static")


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")
