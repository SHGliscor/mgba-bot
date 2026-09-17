@echo off
setlocal
cd /d "%~dp0"
title Pokebot3DS-CFW Gen 3 UI Requirements
where py >nul 2>nul
if not errorlevel 1 (
    py -3 -m pip install --upgrade PySide6
) else (
    python -m pip install --upgrade PySide6
)
echo.
echo Python UI requirements finished.
pause
