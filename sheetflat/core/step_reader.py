"""Read a STEP assembly (AP203/AP214/AP242) and return unique parts with instance counts."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from OCP.IFSelect import IFSelect_RetDone
from OCP.STEPCAFControl import STEPCAFControl_Reader
from OCP.TCollection import TCollection_ExtendedString
from OCP.TDF import TDF_Label
from OCP.TDataStd import TDataStd_Name
from OCP.TDocStd import TDocStd_Document
from OCP.TopAbs import TopAbs_SOLID
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS
from OCP.XCAFApp import XCAFApp_Application
from OCP.XCAFDoc import XCAFDoc_DocumentTool
from OCP.collections import Sequence_TDF_Label as _Seq


@dataclass
class PartDef:
    """A unique part (one referred product) - possibly used many times."""
    key: str                          # unique id inside the file
    name: str
    solids: list = field(default_factory=list)       # TopoDS_Solid objects
    qty: int = 0
    paths: list[str] = field(default_factory=list)   # assembly paths where it is used


class StepReadError(Exception):
    pass


def _name(label) -> str:
    n = TDataStd_Name()
    if label.FindAttribute(TDataStd_Name.GetID_s(), n):
        return n.Get().ToExtString()
    return ""


def read_step(path: str | Path) -> tuple[str, list[PartDef]]:
    """Returns (root assembly name, parts)."""
    path = str(path)
    app = XCAFApp_Application.GetApplication_s()
    doc = TDocStd_Document(TCollection_ExtendedString("MDTV-XCAF"))
    app.InitDocument(doc)
    reader = STEPCAFControl_Reader()
    reader.SetNameMode(True)
    reader.SetColorMode(False)
    if reader.ReadFile(path) != IFSelect_RetDone:
        raise StepReadError("อ่านไฟล์ STEP ไม่ได้ (ไฟล์เสียหรือรูปแบบไม่รองรับ)")
    if not reader.Transfer(doc):
        raise StepReadError("แปลงข้อมูล STEP ไม่สำเร็จ")
    st = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    roots = _Seq()
    st.GetFreeShapes(roots)
    if roots.Length() == 0:
        raise StepReadError("ไม่พบรูปทรงในไฟล์ STEP")

    parts: dict[str, PartDef] = {}
    root_name = ""

    def tag_key(label) -> str:
        return label.Tag().__str__() if False else _label_entry(label)

    def visit(label, mult_path: str):
        nonlocal root_name
        if st.IsReference_s(label):
            ref = TDF_Label()
            st.GetReferredShape_s(label, ref)
            visit(ref, mult_path)
            return
        if st.IsAssembly_s(label):
            comps = _Seq()
            st.GetComponents_s(label, comps)
            nm = _name(label)
            for i in range(1, comps.Length() + 1):
                c = comps.Value(i)
                visit(c, mult_path + "/" + nm)
            return
        key = _label_entry(label)
        pd = parts.get(key)
        if pd is None:
            shp = st.GetShape_s(label)
            solids = []
            ex = TopExp_Explorer(shp, TopAbs_SOLID)
            while ex.More():
                solids.append(TopoDS.Solid(ex.Current()))
                ex.Next()
            pd = PartDef(key=key, name=_name(label) or f"Part{len(parts) + 1}", solids=solids)
            parts[key] = pd
        pd.qty += 1
        pd.paths.append(mult_path)

    for i in range(1, roots.Length() + 1):
        r = roots.Value(i)
        if not root_name:
            root_name = _name(r)
        visit(r, "")
    return root_name, [p for p in parts.values() if p.solids]


def _label_entry(label) -> str:
    from OCP.TCollection import TCollection_AsciiString
    from OCP.TDF import TDF_Tool
    s = TCollection_AsciiString()
    TDF_Tool.Entry_s(label, s)
    return s.ToCString()
