"""Thai / English for everything the program writes: Excel workbooks, file names, ZIP folders, the bend-table file.

The program is written in Thai, so Thai is the source language and English is a dictionary of Thai phrases
(``web/i18n-en.js``, the same file the web page uses for its language switch: one place to translate).

* ``tr(text, lang)``      phrase by phrase: every Thai phrase of the dictionary inside ``text`` becomes its English
                          phrase, longest phrase first; anything else (numbers, codes, names) stays as it is.
* ``tr_exact(text, lang)``  only when the WHOLE text is a dictionary phrase: for names a user may have typed (a cost item,
                          a group, a mode name) that must never be changed in the middle.
* ``translate_workbook``  turns every label of a finished workbook into English, but never the customer's own text
                          (cells written with ``xlsx_safe.text()``), formulas keep working (sheet names in them follow).
* ``fname(kind, lang)``   the fixed part of a file name ("summary", "price" ...).

Adding a language: add its dictionary next to ``i18n-en.js`` (``window.SF_<LANG>``), list it in ``LANGS`` and ``_FILES``
below, and add its entry to the language menu of ``index.html``.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

LANGS = ("th", "en")
DEFAULT = "th"
THAI = re.compile("[฀-๿]")
_WEB = Path(__file__).resolve().parent.parent / "web"

# fixed words of file names / ZIP folders, per language
_FILES = {
    "summary": {"th": "สรุป", "en": "summary"},
    "price": {"th": "ราคา", "en": "price"},
    "parts_step": {"th": "ชิ้นส่วน_STEP", "en": "parts_STEP"},
    "cut": {"th": "ตัด", "en": "cut"},
    "not_cut": {"th": "ไม่ตัด", "en": "not cut"},
}

_dicts: dict = {}


def norm(lang) -> str:
    """'en', 'en-US', 'EN' -> 'en'; anything else (or nothing) -> the default language."""
    l = str(lang or "").strip().lower()[:2]
    return l if l in LANGS else DEFAULT


def _dictionary(lang: str):
    """(dict, compiled regex) of a language, None for the source language."""
    if lang == DEFAULT:
        return None
    if lang not in _dicts:
        src = (_WEB / f"i18n-{lang}.js").read_text(encoding="utf-8")
        body = src[src.index("= {") + 2:].rstrip().rstrip(";")
        d = json.loads(body)
        keys = sorted(d, key=len, reverse=True)
        _dicts[lang] = (d, re.compile("|".join(re.escape(k) for k in keys)) if keys else None)
    return _dicts[lang]


def tr(text, lang) -> str:
    lang = norm(lang)
    if lang == DEFAULT or not isinstance(text, str) or not THAI.search(text):
        return text
    d, rx = _dictionary(lang)
    return rx.sub(lambda m: d[m.group(0)], text) if rx else text


def tr_exact(text, lang) -> str:
    lang = norm(lang)
    if lang == DEFAULT or not isinstance(text, str):
        return text
    d, _ = _dictionary(lang)
    return d.get(text.strip(), text)


def fname(kind: str, lang) -> str:
    return _FILES[kind][norm(lang)]


# ---------------------------------------------------------------- workbooks
_BAD_TITLE = re.compile(r"[\[\]:*?/\\]")


def _title(s: str, used: set) -> str:
    base = (_BAD_TITLE.sub("-", s).strip() or "Sheet")[:31]
    t, n = base, 2
    while t.lower() in used:
        suffix = f" ({n})"
        t, n = base[:31 - len(suffix)] + suffix, n + 1
    used.add(t.lower())
    return t


def translate_workbook(wb, lang, titles: bool = True) -> None:
    """Translate the labels of a finished openpyxl workbook in place (see the module doc).
    ``titles=False`` keeps the sheet names (they are the user's own, e.g. the bend-table modes)."""
    lang = norm(lang)
    if lang == DEFAULT:
        return
    from .xlsx_safe import customer_cells
    used: set = set()
    rename = {}
    for ws in wb.worksheets:
        rename[ws.title] = _title(tr(ws.title, lang), used) if titles else ws.title
    for ws in wb.worksheets:
        skip = customer_cells(ws)
        for row in ws.iter_rows():
            for c in row:
                v = c.value
                if not isinstance(v, str) or c.coordinate in skip:
                    continue
                if v.startswith("="):                     # formulas stay; only the names of renamed sheets inside them
                    new = v
                    for old, nw in rename.items():
                        if old != nw:
                            new = new.replace(f"'{old}'!", f"'{nw}'!")
                            new = re.sub("(?<!')" + re.escape(old) + "!", lambda m, nw=nw: f"'{nw}'!", new)
                    if new != v:
                        c.value = new
                elif THAI.search(v):
                    c.value = tr(v, lang)
        for dv in getattr(ws.data_validations, "dataValidation", []):
            if dv.formula1 and THAI.search(dv.formula1):
                dv.formula1 = tr(dv.formula1, lang)
    for ws in wb.worksheets:
        ws.title = rename[ws.title]
