@echo off
rem Live market-flow guard for this PC. Put a copy of this file in the Windows Startup folder (Win+R, shell:startup).
rem It downloads the latest pc_live_guard.py from GitHub, waits a minute, then runs it (needs: gh auth login, python).
rem The guard restarts closed runner windows and restarts the live collector if it stops (weekdays 08:10-16:10).
rem Log: %TEMP%\pc_live_guard.log
setlocal
set "G=%TEMP%\pc_live_guard.py"
curl.exe -fsSL --max-time 60 -o "%G%.new" https://raw.githubusercontent.com/Sunbong-oh/kb-market-report/main/pc_live_guard.py && move /y "%G%.new" "%G%" >nul
timeout /t 60 /nobreak >nul
python "%G%"
endlocal
