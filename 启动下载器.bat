@echo off
cd /d "%~dp0"
where pyw >nul 2>nul
if %errorlevel%==0 (
    start "" pyw gui_download_qt.py
) else (
    start "" py gui_download_qt.py
)
