@echo off
rem Keep this file ASCII-only: cmd.exe misparses UTF-8 multibyte text in batch files.
rem Always-on background server (started hidden by start_server.vbs at Windows logon).
rem Output goes to data\server.log; if the server stops, it restarts after 10 seconds.
cd /d "%~dp0"
if not exist "C:\Python314\Lib\os.py" if exist "%LOCALAPPDATA%\Programs\Python\Python314\Lib\os.py" set "PYTHONHOME=%LOCALAPPDATA%\Programs\Python\Python314"
set PYTHONIOENCODING=utf-8
if not exist data mkdir data

rem Already running? then do nothing (prevents duplicate servers)
curl -s -o nul -m 3 http://127.0.0.1:8000/api/status && exit /b 0

:loop
echo [%date% %time%] server start >> data\server.log
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 >> data\server.log 2>&1
echo [%date% %time%] server stopped (exit %ERRORLEVEL%), restarting in 10s >> data\server.log
timeout /t 10 /nobreak >nul
curl -s -o nul -m 3 http://127.0.0.1:8000/api/status && exit /b 0
goto loop
