@echo off
rem AstroFlow (portabel) starten: Server im Hintergrund, danach öffnet sich der Browser.
rem Daten und Einstellungen liegen in diesem Ordner (Daten\, config.json).
set "ASTROFLOW_HOME=%~dp0."
start "" "%~dp0python\pythonw.exe" -I -m astroflow start
