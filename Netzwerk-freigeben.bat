@echo off
rem AstroFlow im lokalen Netz erreichbar machen: Windows-Firewall-Regel „AstroFlow LAN“ für TCP %ASTROFLOW_PORT% (Standard 8766),
rem nur im privaten Netzwerkprofil und nur für Geräte im eigenen Subnetz – keine Freigabe ins Internet, keine Router-Weiterleitung.
rem Braucht Administratorrechte (Windows fragt nach). Rückgängig:  Netzwerk-freigeben.bat entfernen
chcp 65001 >nul
setlocal
if not defined ASTROFLOW_PORT set "ASTROFLOW_PORT=8766"
set "RULE=AstroFlow LAN"

net session >nul 2>&1
if not %errorlevel%==0 (
  echo Administratorrechte werden angefordert ...
  if "%~1"=="" (
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  ) else (
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -ArgumentList '%~1' -Verb RunAs"
  )
  exit /b 0
)

if /i "%~1"=="entfernen" (
  powershell -NoProfile -Command "Remove-NetFirewallRule -DisplayName '%RULE%' -ErrorAction SilentlyContinue"
  echo Freigabe „%RULE%“ entfernt. AstroFlow ist nur noch auf diesem PC erreichbar.
  pause
  exit /b 0
)

powershell -NoProfile -Command ^
  "Remove-NetFirewallRule -DisplayName '%RULE%' -ErrorAction SilentlyContinue;" ^
  "New-NetFirewallRule -DisplayName '%RULE%' -Description 'AstroFlow-Weboberfläche im Heimnetz (nur privates Profil, nur lokales Subnetz)' -Direction Inbound -Action Allow -Protocol TCP -LocalPort %ASTROFLOW_PORT% -Profile Private -RemoteAddress LocalSubnet | Out-Null;" ^
  "$pub = Get-NetConnectionProfile | Where-Object { $_.NetworkCategory -eq 'Public' -and $_.IPv4Connectivity -ne 'NoTraffic' };" ^
  "if ($pub) { Write-Host ('Hinweis: Netzwerk \"' + ($pub.InterfaceAlias -join ', ') + '\" ist als öffentlich eingestuft – dort bleibt AstroFlow gesperrt. Im Heimnetz in den Windows-Einstellungen auf \"Privat\" stellen.') }"
echo.
echo Freigabe „%RULE%“ eingerichtet: Port %ASTROFLOW_PORT%, privates Netzwerk, nur lokales Subnetz.
echo Andere Geräte im Heimnetz erreichen AstroFlow über die Adresse, die Start.bat anzeigt.
pause
endlocal
