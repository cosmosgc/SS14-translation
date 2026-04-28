@echo off
setlocal
cd /d "%~dp0"

echo Starting FTL translation...

where py >nul 2>&1
if %errorlevel%==0 (
    py -3 translate_ftl.py
) else (
    python translate_ftl.py
)

if errorlevel 1 (
    echo.
    echo Translation failed.
    pause
    exit /b 1
)

echo.
echo Translation finished successfully.
pause
