@echo off
title TaxLite
cd /d "%~dp0"

REM ==============================================================
REM   Use bundled Python -- no installation needed
REM ==============================================================
set "PYTHON=%~dp0python\python.exe"
set "PYTHONPATH=%~dp0src;%~dp0"
set "STREAMLIT_HOME=%~dp0.streamlit"

echo.
echo   TaxLite
echo   ============================================

if not exist "%PYTHON%" (
    echo.
    echo   ERROR: Bundled Python not found at:
    echo   %PYTHON%
    echo.
    echo   Current directory: %CD%
    echo   Contents:
    dir /b
    echo.
    pause
    exit /b 1
)

if not exist "%~dp0app.py" (
    echo.
    echo   ERROR: app.py not found.
    echo   Current directory: %CD%
    echo.
    pause
    exit /b 1
)

REM ==============================================================
REM   Copy streamlit config to user home (skip first-run prompt)
REM ==============================================================
if not exist "%USERPROFILE%\.streamlit\credentials.toml" (
    mkdir "%USERPROFILE%\.streamlit" 2>nul
    echo [general] > "%USERPROFILE%\.streamlit\credentials.toml"
    echo email = "" >> "%USERPROFILE%\.streamlit\credentials.toml"
)

REM ==============================================================
REM   Launch TaxLite
REM ==============================================================
echo.
echo   Starting... this may take a moment on first launch.
echo   A browser tab will open at http://localhost:8501
echo   Press Ctrl+C in this window to stop.
echo   ============================================
echo.
"%PYTHON%" -m streamlit run "%~dp0app.py" --server.maxUploadSize 50

echo.
echo   TaxLite has stopped.
pause
