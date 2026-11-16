@echo off
rem Start the live market-flow collector when this PC starts, if it is not already running or queued.
rem Needs gh login on this PC (gh auth login). Called from a shortcut/bat in the Windows Startup folder.
rem Log: %TEMP%\pc_start_live.log
setlocal
set "PATH=%PATH%;C:\Program Files\GitHub CLI"
set "REPO=Sunbong-oh/kb-market-report"
set "LOG=%TEMP%\pc_start_live.log"
echo [%date% %time%] start >> "%LOG%"
rem wait for the runner window to come up and the network to be ready
timeout /t 90 /nobreak >nul
set A=0
set Q=0
for /f %%i in ('gh run list -R %REPO% --workflow live-snapshot.yml --status in_progress --json databaseId --jq length 2^>nul') do set A=%%i
for /f %%i in ('gh run list -R %REPO% --workflow live-snapshot.yml --status queued --json databaseId --jq length 2^>nul') do set Q=%%i
echo [%date% %time%] in_progress=%A% queued=%Q% >> "%LOG%"
if "%A%"=="0" if "%Q%"=="0" (
  gh workflow run live-snapshot.yml -R %REPO% --ref main >> "%LOG%" 2>&1
  echo [%date% %time%] dispatched live-snapshot >> "%LOG%"
) else (
  echo [%date% %time%] already running or queued - nothing to do >> "%LOG%"
)
endlocal
