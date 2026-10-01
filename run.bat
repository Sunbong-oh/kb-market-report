@echo off
rem Keep this file ASCII-only: cmd.exe misparses UTF-8 multibyte text in batch files.
cd /d "%~dp0"

rem This PC's C:\Python314 has no standard library (Lib); point PYTHONHOME at the per-user install.
if not exist "C:\Python314\Lib\os.py" if exist "%LOCALAPPDATA%\Programs\Python\Python314\Lib\os.py" set "PYTHONHOME=%LOCALAPPDATA%\Programs\Python\Python314"

if not exist ".venv\Scripts\python.exe" (
  python -m venv .venv
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)

echo Open http://127.0.0.1:8000 in your browser.
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
