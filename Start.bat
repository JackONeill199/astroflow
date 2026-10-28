@echo off
rem AstroFlow starten: Server im Hintergrund (ohne Fenster), danach Browser öffnen.
rem Anpassbar per Umgebungsvariable: ASTROFLOW_PORT, ASTROFLOW_DATA, ASTROFLOW_NO_BROWSER=1
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if not defined ASTROFLOW_PORT set "ASTROFLOW_PORT=8766"
if not defined ASTROFLOW_DATA set "ASTROFLOW_DATA=%~dp0data"
set "PY=%~dp0.venv\Scripts\python.exe"
set "URL=http://localhost:%ASTROFLOW_PORT%"
set "LOG=%ASTROFLOW_DATA%\server.log"

if not exist "%PY%" (
  echo Python-Umgebung fehlt: %PY%
  echo Einrichten:  python -m venv .venv  ^&^&  .venv\Scripts\pip install -r requirements.txt
  pause
  exit /b 1
)

rem Läuft schon? Dann nur den Browser öffnen.
powershell -NoProfile -Command "if (Get-NetTCPConnection -LocalPort %ASTROFLOW_PORT% -State Listen -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }"
if %errorlevel%==0 (
  echo AstroFlow läuft bereits: %URL%
  goto browser
)

if not exist "%ASTROFLOW_DATA%" mkdir "%ASTROFLOW_DATA%"
echo AstroFlow wird gestartet (Port %ASTROFLOW_PORT%, Daten: %ASTROFLOW_DATA%) ...
powershell -NoProfile -Command "Start-Process -FilePath '%PY%' -ArgumentList '-m','astroflow','serve','--port','%ASTROFLOW_PORT%','--data','\"%ASTROFLOW_DATA%\"' -WorkingDirectory '%~dp0.' -WindowStyle Hidden -RedirectStandardOutput '%LOG%' -RedirectStandardError '%LOG%.err'"

rem Warten, bis der Server antwortet (max. 60 s)
powershell -NoProfile -Command "for ($i=0; $i -lt 120; $i++) { if (Get-NetTCPConnection -LocalPort %ASTROFLOW_PORT% -State Listen -ErrorAction SilentlyContinue) { exit 0 }; Start-Sleep -Milliseconds 500 }; exit 1"
if not %errorlevel%==0 (
  echo Der Server antwortet nicht. Details: %LOG%.err
  pause
  exit /b 1
)
echo AstroFlow läuft: %URL%   (beenden mit Stop.bat)

:browser
if not "%ASTROFLOW_NO_BROWSER%"=="1" start "" "%URL%"
endlocal
exit /b 0
