@echo off
rem Keep this file ASCII-only: cmd.exe misparses UTF-8 multibyte text in batch files.
rem Run by Windows Task Scheduler (Mon-Fri 15:50): capture dashboard as JPG and send to Telegram.
cd /d "%~dp0"
if not exist "C:\Python314\Lib\os.py" if exist "%LOCALAPPDATA%\Programs\Python\Python314\Lib\os.py" set "PYTHONHOME=%LOCALAPPDATA%\Programs\Python\Python314"
set PYTHONIOENCODING=utf-8
if not exist reports mkdir reports
".venv\Scripts\python.exe" report.py >> reports\report.log 2>&1
