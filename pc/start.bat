@echo off
rem Tiyatro Takip PC uygulamasini baslatir (konsol penceresi acmadan).
setlocal
cd /d "%~dp0.."
if exist ".venv\Scripts\pythonw.exe" goto venv
where pythonw >nul 2>nul
if not errorlevel 1 goto pythonw
start "" pyw -m pc.app
goto end
:venv
start "" ".venv\Scripts\pythonw.exe" -m pc.app
goto end
:pythonw
start "" pythonw -m pc.app
:end
endlocal
