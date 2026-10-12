@echo off
rem Keep this file ASCII-only: cmd.exe misparses UTF-8 multibyte text in batch files.
rem Target-price change report from Hankyung Consensus (run by hand or Task Scheduler on weekday mornings).
cd /d "%~dp0"
if not exist "C:\Python314\Lib\os.py" if exist "%LOCALAPPDATA%\Programs\Python\Python314\Lib\os.py" set "PYTHONHOME=%LOCALAPPDATA%\Programs\Python\Python314"
set PYTHONIOENCODING=utf-8
if not exist reports mkdir reports
".venv\Scripts\python.exe" -m pip install -q beautifulsoup4
".venv\Scripts\python.exe" consensus_report.py %* >> reports\consensus.log 2>&1
