"""Regenerate sheetflat/bend_tables.xlsx (the shop-standard bend table shipped with the program)
from DEFAULT_PROFILE.  Run after changing the defaults:  python tools/make_default_bend_xlsx.py
Edit the standard in Excel instead if you prefer: just save over sheetflat/bend_tables.xlsx and commit it."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sheetflat.core.bend_xlsx import write_profiles          # noqa: E402
from sheetflat.core.profiles import DEFAULT_PROFILE, SHIPPED_BEND_XLSX  # noqa: E402

write_profiles(SHIPPED_BEND_XLSX, [DEFAULT_PROFILE])
print("wrote", SHIPPED_BEND_XLSX)
