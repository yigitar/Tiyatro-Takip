@echo off
rem Tiyatro Takip PC uygulamasini konsolsuz baslatir ve bu pencereyi hemen kapatir.
rem Tercih: pc\tiyatro_takip.pyw dosyasina cift tikla (hic pencere acilmaz).
cd /d "%~dp0.."
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" "pc\tiyatro_takip.pyw"
) else (
    start "" pyw "pc\tiyatro_takip.pyw"
)
exit
