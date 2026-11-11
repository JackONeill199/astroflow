@echo off
rem AstroFlow beenden: stoppt den Server auf ASTROFLOW_PORT (Standard 8766) samt Python-Startprozess.
rem Laufende Stacking-Aufträge setzen beim nächsten Start am letzten Zwischenstand wieder an.
chcp 65001 >nul
setlocal
if not defined ASTROFLOW_PORT set "ASTROFLOW_PORT=8766"

powershell -NoProfile -Command ^
  "$c = Get-NetTCPConnection -LocalPort %ASTROFLOW_PORT% -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1;" ^
  "if (-not $c) { Write-Host 'AstroFlow läuft nicht (Port %ASTROFLOW_PORT%).'; exit 0 };" ^
  "$p = Get-CimInstance Win32_Process -Filter \"ProcessId=$($c.OwningProcess)\";" ^
  "if ($p.CommandLine -notmatch 'astroflow') { Write-Host \"Port %ASTROFLOW_PORT% gehört nicht zu AstroFlow: $($p.CommandLine)\"; exit 1 };" ^
  "$ids = @($p.ProcessId); if ($p.ParentProcessId) { $pp = Get-CimInstance Win32_Process -Filter \"ProcessId=$($p.ParentProcessId)\"; if ($pp.CommandLine -match 'astroflow') { $ids += $pp.ProcessId } };" ^
  "foreach ($i in $ids) { Stop-Process -Id $i -Force -ErrorAction SilentlyContinue };" ^
  "Write-Host 'AstroFlow wurde beendet.'"
set "RC=%errorlevel%"
endlocal & exit /b %RC%
