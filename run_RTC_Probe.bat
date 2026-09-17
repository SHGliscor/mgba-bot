@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Pokebot3DS-CFW mGBA Ruby RTC Probe v0p2
echo.
echo Pokebot3DS-CFW mGBA Ruby RTC Probe v0p2
echo Target: 192.168.1.21:4953
echo.
where py >nul 2>nul
if not errorlevel 1 (
    py -3 rtc_probe.py 192.168.1.21
) else (
    where python >nul 2>nul
    if errorlevel 1 (
        echo ERROR: Python 3 was not found.
        pause
        exit /b 9009
    )
    python rtc_probe.py 192.168.1.21
)
echo.
pause
