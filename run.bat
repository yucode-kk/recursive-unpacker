@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  set "PYTHON=py -3"
) else (
  set "PYTHON=python"
)
%PYTHON% -c "import tkinter" >nul 2>nul
if errorlevel 1 (
  echo Python/Tkinter is unavailable. Install a standard Windows Python distribution with Tcl/Tk support.
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
if errorlevel 1 pause
endlocal
