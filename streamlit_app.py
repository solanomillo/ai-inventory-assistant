
"""Punto de entrada de Streamlit Cloud."""

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.chdir(PROJECT_ROOT)

import runpy  # noqa: E402

runpy.run_path(
    str(PROJECT_ROOT / "app" / "ui" / "main.py"),
    run_name="__main__",
)
