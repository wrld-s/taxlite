@echo off
title TaxLite
setlocal

set "INSTALL_DIR=%LOCALAPPDATA%\TaxLite"
set "PYTHON=%INSTALL_DIR%\python\python.exe"

REM ==============================================================
REM   First run: extract app to %LOCALAPPDATA%\TaxLite
REM ==============================================================
if not exist "%PYTHON%" (
    echo.
    echo   TaxLite - First Run Setup
    echo   ============================================
    echo   Extracting to %INSTALL_DIR%...
    echo.

    REM Find the zip next to this script
    set "ZIP=%~dp0TaxLite.zip"
    if not exist "%ZIP%" (
        echo   ERROR: TaxLite.zip not found next to this script.
        pause
        exit /b 1
    )

    REM Extract using PowerShell (works on all Windows 10/11, x64 and ARM)
    powershell -NoProfile -Command "Expand-Archive -Path '%ZIP%' -DestinationPath '%INSTALL_DIR%' -Force"
    if errorlevel 1 (
        echo   ERROR: Extraction failed.
        pause
        exit /b 1
    )

    REM Create desktop shortcut for next time
    powershell -NoProfile -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut([IO.Path]::Combine([Environment]::GetFolderPath('Desktop'), 'TaxLite.lnk')); $s.TargetPath = '%INSTALL_DIR%\TaxLite.cmd'; $s.WorkingDirectory = '%INSTALL_DIR%'; $s.IconLocation = 'shell32.dll,144'; $s.Save()"

    echo.
    echo   Setup complete! A shortcut has been added to your Desktop.
    echo.
)

REM ==============================================================
REM   Launch
REM ==============================================================
cd /d "%INSTALL_DIR%"
set "PYTHONPATH=%INSTALL_DIR%\src;%INSTALL_DIR%"

REM Skip streamlit first-run prompt
if not exist "%USERPROFILE%\.streamlit\credentials.toml" (
    mkdir "%USERPROFILE%\.streamlit" 2>nul
    (echo [general]) > "%USERPROFILE%\.streamlit\credentials.toml"
    (echo email = "") >> "%USERPROFILE%\.streamlit\credentials.toml"
)

echo.
echo   ============================================
echo       TaxLite is starting...
echo       Opening http://localhost:8501
echo       Press Ctrl+C in this window to stop.
echo   ============================================
echo.
"%PYTHON%" -m streamlit run "%INSTALL_DIR%\app.py" --server.maxUploadSize 50

echo.
echo   TaxLite has stopped.
pause
