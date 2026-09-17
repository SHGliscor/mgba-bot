@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Pokebot3DS-CFW Frozen Starter UI

echo Starting the frozen v0p21 HF1 starter-only UI...
where py >nul 2>nul
if not errorlevel 1 (
    py -3 ruby_starter_ui.py
    set "RC=%ERRORLEVEL%"
) else (
    where python >nul 2>nul
    if errorlevel 1 (
        echo ERROR: Python 3 was not found.
        pause
        exit /b 9009
    )
    python ruby_starter_ui.py
    set "RC=%ERRORLEVEL%"
)
if not "%RC%"=="0" pause
exit /b %RC%
