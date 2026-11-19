"""Text from customer files (part names, group names, notes ...) must never become an Excel formula.

openpyxl writes any string that starts with "=" as a formula, so a part named "=HYPERLINK(...)" in a
STEP file would turn into a live formula in the Excel we hand out. Wrap such values in ``text()``; the
writers then store them as plain text. Formulas the program builds itself are plain ``str``.
"""


class Text(str):
    """A string that is always stored as text, never as a formula."""


def text(v) -> Text:
    return Text("" if v is None else str(v))


def put(cell, v):
    """Set a cell value; ``Text`` stays text even when it starts with = + - @."""
    cell.value = v
    if isinstance(v, Text):
        cell.data_type = "s"
    return cell
