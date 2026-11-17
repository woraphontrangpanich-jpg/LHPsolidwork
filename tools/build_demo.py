"""Build the static demo for GitHub Pages into ../docs.

The real app needs Python + OpenCASCADE on the machine, which GitHub Pages cannot run.
This script builds a synthetic demo assembly, runs the real pipeline once through the
real HTTP API, and stores every response as a static file. The same front end then
reads those files (window.SHEETFLAT_DEMO), so visitors can click through the results.

    python tools/build_demo.py
"""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import shapes  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

DOCS = ROOT / "docs"
DEMO = DOCS / "demo"


def build_step(path: Path):
    from OCP.STEPCAFControl import STEPCAFControl_Writer
    from OCP.STEPControl import STEPControl_AsIs
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDataStd import TDataStd_Name
    from OCP.TDocStd import TDocStd_Document
    from OCP.TopLoc import TopLoc_Location
    from OCP.XCAFApp import XCAFApp_Application
    from OCP.XCAFDoc import XCAFDoc_DocumentTool
    from OCP.gp import gp_Trsf, gp_Vec

    app = XCAFApp_Application.GetApplication_s()
    doc = TDocStd_Document(TCollection_ExtendedString("MDTV-XCAF"))
    app.InitDocument(doc)
    st = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())

    def named(shape, nm):
        lbl = st.AddShape(shape, False)
        TDataStd_Name.Set_s(lbl, TCollection_ExtendedString(nm))
        return lbl

    def loc(x, y=0.0):
        t = gp_Trsf()
        t.SetTranslation(gp_Vec(x, y, 0))
        return TopLoc_Location(t)

    parts = [
        (named(shapes.bent_sheet(1.0, 380, 60, 90, 1.0, 420), "Side_Panel"), 2),
        (named(shapes.bent_sheet(1.0, 300, 30, 90, 1.0, 380), "Back_Panel"), 1),
        (named(shapes.bent_sheet(1.0, 200, 120, 90, 25.0, 380), "Curved_Top"), 1),
        (named(shapes.bent_sheet(2.0, 60, 40, 45, 2.0, 50), "Bracket_45"), 4),
        (named(shapes.flat_plate(2.0, 380, 300, hole_d=40), "Base_Plate"), 1),
        (named(shapes.bent_sheet(0.7, 30, 15, 90, 0.2, 40), "Clip"), 6),
        (named(shapes.bolt(), "M8 Bolt"), 8),
        (named(shapes.tube(25, 2, 400), "Leg_Pipe"), 4),
    ]
    top = st.NewShape()
    TDataStd_Name.Set_s(top, TCollection_ExtendedString("DemoCabinet"))
    x = 0.0
    for lbl, qty in parts:
        for i in range(qty):
            st.AddComponent(top, lbl, loc(x, 600.0 * i))
        x += 700.0
    st.UpdateAssemblies()
    w = STEPCAFControl_Writer()
    w.SetNameMode(True)
    w.Transfer(doc, STEPControl_AsIs)
    w.Write(str(path))


def main():
    if DOCS.exists():
        shutil.rmtree(DOCS)
    (DEMO / "geom").mkdir(parents=True)
    (DEMO / "layout").mkdir()
    (DEMO / "export").mkdir()
    step = DEMO / "DemoCabinet.step"
    build_step(step)

    from sheetflat.app import app
    c = TestClient(app)
    jid = c.post("/api/open-path", json={"path": str(step), "material": "ZN"}).json()["id"]
    for _ in range(200):
        j = c.get(f"/api/jobs/{jid}").json()
        if j["state"] in ("done", "error"):
            break
        time.sleep(0.2)
    assert j["state"] == "done", j
    j["project"]["id"] = "demo"
    j["project"]["source"] = "DemoCabinet.step"

    def save(rel, obj):
        (DEMO / rel).write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")

    save("job.json", j)
    prof = c.get("/api/profiles").json()
    save("profiles.json", prof)
    P = j["project"]
    for p in P["parts"]:
        if p["flat"]:
            save(f"geom/{p['id']}.json", c.get(f"/api/jobs/{jid}/parts/{p['id']}/geometry").json())
    files = {}
    for g in P["groups"]:
        for s in g["sheets"]:
            save(f"layout/{g['id']}_{s['index']}.json", c.get(f"/api/jobs/{jid}/groups/{g['id']}/layout/{s['index']}").json())
            rel = f"export/{g['id']}_{s['index']}.dxf"
            (DEMO / rel).write_bytes(c.get(f"/api/jobs/{jid}/export/dxf/{g['id']}/{s['index']}").content)
            files[f"dxf/{g['id']}/{s['index']}"] = {"path": rel, "name": s["filename"]}
    (DEMO / "export/summary.xlsx").write_bytes(c.get(f"/api/jobs/{jid}/export/xlsx").content)
    files["xlsx"] = {"path": "export/summary.xlsx", "name": f"{P['job']}_สรุป.xlsx"}
    (DEMO / "export/all.zip").write_bytes(c.get(f"/api/jobs/{jid}/export/all").content)
    files["all"] = {"path": "export/all.zip", "name": f"{P['job']}_DXF.zip"}
    save("files.json", files)

    web = ROOT / "sheetflat" / "web"
    shutil.copy(web / "style.css", DOCS / "style.css")
    shutil.copy(web / "app.js", DOCS / "app.js")
    html = (web / "index.html").read_text(encoding="utf-8")
    html = html.replace('href="/static/style.css"', 'href="style.css"')
    html = html.replace('<script src="/static/app.js" defer></script>',
                        '<script>window.SHEETFLAT_DEMO = "demo/";</script>\n<script src="app.js" defer></script>')
    html = html.replace('href="/" aria-label="SheetFlat"', 'href="./" aria-label="SheetFlat"')
    (DOCS / "index.html").write_text(html, encoding="utf-8")
    (DOCS / ".nojekyll").write_text("", encoding="utf-8")
    print("demo built:", DOCS, "| groups:", [(g["code"], g["pieces"]) for g in P["groups"]],
          "| excluded:", [(p["name"], p["qty"]) for p in P["parts"] if not p["include"]])


if __name__ == "__main__":
    main()
