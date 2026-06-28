#!/usr/bin/env python3
"""Ejecuta sync_venezuelareporta.py en bucle hasta completar la descarga."""
import subprocess
import sys
from pathlib import Path

script = Path(__file__).resolve().parent / "sync_venezuelareporta.py"
python = Path(sys.executable)
log = Path(__file__).resolve().parent / "vr_sync_bg.log"

while True:
    result = subprocess.run(
        [str(python), str(script)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    with log.open("a", encoding="utf-8") as f:
        f.write(result.stdout)
    if "No hay más datos" in result.stdout or result.returncode != 0:
        break
