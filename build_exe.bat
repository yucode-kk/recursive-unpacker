@echo off
setlocal
cd /d "%~dp0"
py -3 -m pip install -r requirements.txt pyinstaller
if errorlevel 1 (
  echo Dependency installation failed.
  pause
  exit /b 1
)
py -3 -m PyInstaller --noconfirm --clean --windowed --name RecursiveUnpacker --collect-all py7zr --collect-all pyzipper main.py
if errorlevel 1 (
  echo Build failed.
  pause
  exit /b 1
)
echo Built executable: dist\RecursiveUnpacker\RecursiveUnpacker.exe
pause
endlocal
