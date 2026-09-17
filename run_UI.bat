@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Pokebot3DS-CFW Gen 3 Bot

echo Starting Pokebot3DS-CFW Gen 3 Bot...
where py >nul 2>nul
if not errorlevel 1 (
    py -3 gen3_app.py
    set "RC=%ERRORLEVEL%"
) else (
    where python >nul 2>nul
    if errorlevel 1 (
        echo ERROR: Python 3 was not found.
        echo Run requirements.bat after installing Python 3.
        pause
        exit /b 9009
    )
    python gen3_app.py
    set "RC=%ERRORLEVEL%"
)
if not "%RC%"=="0" (
    echo.
    echo UI exited with code %RC%.
    pause
)
exit /b %RC%
