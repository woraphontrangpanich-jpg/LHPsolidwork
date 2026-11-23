"""The English dictionary of the language switch (sheetflat/web/i18n-en.js: Thai phrase -> English phrase)."""
import json
import re
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "sheetflat" / "web"
THAI = re.compile("[฀-๿]")


def load():
    s = (WEB / "i18n-en.js").read_text(encoding="utf-8")
    body = s[s.index("window.SF_EN = ") + len("window.SF_EN = "):].rstrip().rstrip(";")
    return json.loads(body)


def test_dictionary_is_complete_and_english():
    d = load()
    assert len(d) > 700
    for k, v in d.items():
        assert THAI.search(k), k
        assert v.strip(), f"empty English text for {k!r}"
        assert not THAI.search(v), f"Thai left in the English text of {k!r}"


def test_the_page_loads_the_language_files_in_order():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    a, b, c = (html.index(f"/static/{n}") for n in ("i18n-en.js", "i18n.js", "app.js"))
    assert a < b < c                                   # dictionary, then the switch, then the program
    assert 'data-lang-pick="en"' in html and 'data-lang-pick="th"' in html
