@echo off
setlocal
cd /d "%~dp0"

rem Remember the console code page (zh-CN prints a localized "code page: 936" line).
for /f "tokens=2 delims=:" %%c in ('chcp') do set "OLDCP=%%c"
set "OLDCP=%OLDCP: =%"
set "OLDCP=%OLDCP:.=%"

rem UTF-8 pipeline: console code page + Python's own I/O.
rem Keep every echo string in this file ASCII; only Python emits non-ASCII output.
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

set "PYTHON=python"
where py >nul 2>nul && set "PYTHON=py -3"

rem Version banner read from the package - never hard-coded here.
set "APPVER="
for /f "usebackq delims=" %%v in (`%PYTHON% -c "import recursive_unpacker as app; print(app.__version__)" 2^>nul`) do set "APPVER=%%v"
if not defined APPVER set "APPVER=unknown"
echo RecursiveUnpacker v%APPVER%

%PYTHON% -c "import tkinter" >nul 2>nul
if errorlevel 1 (
  echo Python/Tkinter is unavailable. Install a standard Windows Python distribution with Tcl/Tk support.
  if defined OLDCP chcp %OLDCP% >nul 2>nul
  pause
  exit /b 1
)

%PYTHON% -c "import py7zr, pyzipper" >nul 2>nul
if errorlevel 1 (
  echo Installing optional Python archive libraries...
  %PYTHON% -m pip install -r requirements.txt
  if errorlevel 1 (
    echo Dependency installation failed. ZIP support may still work, but 7z/AES ZIP support may be limited.
    pause
  )
)

%PYTHON% main.py
rem chcp resets errorlevel, so capture the exit code before restoring the code page.
set "APPRC=%errorlevel%"
if defined OLDCP chcp %OLDCP% >nul 2>nul
if not "%APPRC%"=="0" pause
endlocal
