@echo off
REM 一键打包: 生成无控制台的单文件 dist\AutoClicker.exe
setlocal
cd /d "%~dp0"

python -m pip install --upgrade pyinstaller pynput pystray pillow

python -m PyInstaller --noconfirm --clean --onefile --noconsole ^
  --icon "%~dp0src\icon.ico" ^
  --name AutoClicker ^
  --hidden-import pynput.keyboard._win32 ^
  --hidden-import pynput.mouse._win32 ^
  --hidden-import pystray._win32 ^
  "%~dp0src\autoclicker.py"

echo.
echo 完成: %~dp0dist\AutoClicker.exe
pause
