@echo off
rem Live market-flow guard for this PC. Put a copy of this file in the Windows Startup folder (Win+R, shell:startup).
rem It downloads the latest pc_live_guard.py with gh (already logged in; avoids the curl revocation-check error on this PC),
rem waits a minute, then runs it (needs: gh auth login, python).
rem The guard restarts closed runner windows and restarts the live collector if it stops (weekdays 08:10-16:10).
rem Log: %TEMP%\pc_live_guard.log
setlocal
set "GH=C:\Program Files\GitHub CLI\gh.exe"
set "G=%TEMP%\pc_live_guard.py"
"%GH%" api repos/Sunbong-oh/kb-market-report/contents/pc_live_guard.py -H "Accept: application/vnd.github.raw" > "%G%.new" && move /y "%G%.new" "%G%" >nul
timeout /t 60 /nobreak >nul
python "%G%"
endlocal
