@echo off
rem AstroFlow (portabel) beenden. Laufende Aufträge setzen beim nächsten Start am letzten Zwischenstand fort.
chcp 65001 >nul
set "ASTROFLOW_HOME=%~dp0."
"%~dp0python\python.exe" -I -m astroflow stop
timeout /t 3 >nul
