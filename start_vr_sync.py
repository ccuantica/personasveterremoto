#!/usr/bin/env python3
import os
import subprocess
import sys
from pathlib import Path

# Kill existing run_all_vr processes except ourselves
subprocess.run(
    ['powershell.exe', '-Command',
     'Get-WmiObject Win32_Process | Where-Object { $_.CommandLine -like \'*run_all_vr*\' -and $_.ProcessId -ne $PID } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }'],
    capture_output=True, text=True,
)

script = Path(__file__).resolve().parent / "run_all_vr.py"
python = Path(sys.executable)
log = Path(__file__).resolve().parent / "vr_sync_bg.log"

with log.open("a", encoding="utf-8") as f:
    subprocess.Popen(
        [str(python), str(script)],
        stdout=f,
        stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
print("Sincronización VR iniciada en segundo plano.")
