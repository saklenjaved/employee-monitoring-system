@echo off
rem Double-click to start WinAgent on this PC. Keep the window open while monitoring runs.
cd /d "%~dp0"
echo Starting WinAgent. Keep this window open.
.venv\Scripts\python.exe -m winagent run --config config.toml
echo.
echo WinAgent stopped. Press any key to close this window.
pause >nul
