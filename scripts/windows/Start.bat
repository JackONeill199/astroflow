@echo off
rem AstroFlow starten: Server im Hintergrund (ohne Fenster), danach Browser öffnen.
rem Anpassbar per Umgebungsvariable: ASTROFLOW_PORT, ASTROFLOW_DATA, ASTROFLOW_NO_BROWSER=1, ASTROFLOW_LAN=1 (Heimnetz erzwingen)
rem Heimnetz (Handy/Tablet): Einstellungen -> Heimnetz einschalten, mit Stop.bat/Start.bat neu starten, Gerät per QR-Code koppeln.
chcp 65001 >nul
setlocal
rem Liegt in scripts\windows\ – Projektordner zwei Ebenen höher
for %%I in ("%~dp0..\..") do set "ROOT=%%~fI\"
cd /d "%ROOT%"
if not defined ASTROFLOW_PORT set "ASTROFLOW_PORT=8766"
if not defined ASTROFLOW_DATA set "ASTROFLOW_DATA=%ROOT%data"
set "PY=%ROOT%.venv\Scripts\python.exe"
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
rem Ohne --host: nur dieser Rechner, außer Einstellungen -> Heimnetz ist eingeschaltet (bzw. ASTROFLOW_LAN=1)
set "LANARG="
if "%ASTROFLOW_LAN%"=="1" set "LANARG=,'--lan'"
powershell -NoProfile -Command "Start-Process -FilePath '%PY%' -ArgumentList '-m','astroflow','serve','--port','%ASTROFLOW_PORT%','--data','\"%ASTROFLOW_DATA%\"'%LANARG% -WorkingDirectory '%ROOT%.' -WindowStyle Hidden -RedirectStandardOutput '%LOG%' -RedirectStandardError '%LOG%.err'"

rem Warten, bis der Server antwortet (max. 60 s)
powershell -NoProfile -Command "for ($i=0; $i -lt 120; $i++) { if (Get-NetTCPConnection -LocalPort %ASTROFLOW_PORT% -State Listen -ErrorAction SilentlyContinue) { exit 0 }; Start-Sleep -Milliseconds 500 }; exit 1"
if not %errorlevel%==0 (
  echo Der Server antwortet nicht. Details: %LOG%.err
  pause
  exit /b 1
)
echo AstroFlow läuft: %URL%   (beenden mit Stop.bat)

:browser
rem Heimnetz: Adresse für andere Geräte (Netzwerkkarten mit Standardgateway) + Hinweis, falls die Firewall noch sperrt.
rem Andere Geräte brauchen einmalig eine Kopplung (QR-Code am PC unter Einstellungen -> Heimnetz).
echo Handy/Tablet im Heimnetz: am PC Einstellungen -^> Heimnetz öffnen und das Gerät per QR-Code koppeln.
powershell -NoProfile -Command ^
  "$ips = Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway -and $_.NetAdapter.Status -eq 'Up' } | ForEach-Object { $_.IPv4Address.IPAddress };" ^
  "foreach ($ip in $ips) { Write-Host ('Im Heimnetz:     http://' + $ip + ':%ASTROFLOW_PORT%') };" ^
  "$ok = Get-NetFirewallRule -DisplayName 'AstroFlow LAN' -ErrorAction SilentlyContinue | Where-Object { $_.Enabled -eq 'True' };" ^
  "if (-not $ok) { Write-Host 'Hinweis: Die Windows-Firewall sperrt den Zugriff von anderen PCs noch – einmalig Netzwerk-freigeben.bat ausführen.' }"
if not "%ASTROFLOW_NO_BROWSER%"=="1" (
  start "" "%URL%"
  echo.
  echo Dieses Fenster schließt sich in 20 Sekunden – AstroFlow läuft im Hintergrund weiter.
  timeout /t 20 >nul
)
endlocal
exit /b 0
