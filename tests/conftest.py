"""Tests use their own data folder, so they never change the bend tables and prices saved on this PC."""
import os
import tempfile

os.environ.setdefault("SHEETFLAT_DATA", tempfile.mkdtemp(prefix="sheetflat_test_data_"))
