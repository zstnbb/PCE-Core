@echo off
REM PCE Emergency Restore — double-click wrapper for pce_restore.ps1.
REM
REM Bypasses execution policy so this works on a default Windows install.
REM Falls back to disabling the system proxy if the snapshot is missing.

cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0pce_restore.ps1" %*
echo.
echo Press any key to close...
pause >nul
