@echo off
title Pokebot3DS-CFW mGBA Gen 3 Starter Hunter v0p1 HF5
cd /d "%~dp0"
echo.
echo Pokebot3DS-CFW mGBA Gen 3 Starter Hunter v0p1 HF5
echo Target: 192.168.1.21:4953
echo.
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 ruby_starter_hunter.py 192.168.1.21
) else (
    python ruby_starter_hunter.py 192.168.1.21
)
echo.
echo Hunter exited with code %errorlevel%.
pause
