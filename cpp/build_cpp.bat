@echo off
REM ============================================================
REM  AutoClicker C++ Studio 1.0.0 —— 一键构建
REM  需要 MinGW-w64 / TDM-GCC 的 g++.exe (产物本身零依赖, 编译才需要)
REM ============================================================
setlocal
cd /d "%~dp0"

set "GXX="
where g++ >nul 2>nul
if not errorlevel 1 set "GXX=g++"
if not defined GXX if exist "C:\Program Files (x86)\Dev-Cpp\MinGW64\bin\g++.exe" set "GXX=C:\Program Files (x86)\Dev-Cpp\MinGW64\bin\g++.exe"
if not defined GXX if exist "C:\msys64\mingw64\bin\g++.exe" set "GXX=C:\msys64\mingw64\bin\g++.exe"
if not defined GXX if exist "C:\mingw64\bin\g++.exe" set "GXX=C:\mingw64\bin\g++.exe"
if not defined GXX (
  echo [!] 未找到 g++.exe, 请把 MinGW-w64 的 bin 目录加入 PATH
  echo     或在工作台里点「编译器…」手动指定。
  pause
  exit /b 1
)

echo.
echo [1/3] 从 studio.cpp 导出完整模板 runtime_template.cpp
python export_template.py
if errorlevel 1 goto fail

echo [2/3] 编译工作台 AutoClickerCPP.exe
"%GXX%" -O2 -s -static -mwindows -std=c++11 studio.cpp -luser32 -lcomdlg32 -lshell32 -o AutoClickerCPP.exe
if errorlevel 1 goto fail

echo [3/3] 编译示例脚本产物 out\AutoClickerScript.exe
if not exist out mkdir out
"%GXX%" -O2 -s -static -mwindows -std=c++11 runtime_template.cpp -luser32 -o out\AutoClickerScript.exe
if errorlevel 1 goto fail

echo.
echo 完成:
echo   工作台   %~dp0AutoClickerCPP.exe
echo   示例产物 %~dp0out\AutoClickerScript.exe
echo   完整模板 %~dp0runtime_template.cpp
echo.
echo 直接双击 AutoClickerCPP.exe 打开代码工作台, 写 C++ 后点「编译」即可。
pause
exit /b 0

:fail
echo.
echo [X] 构建失败, 请检查上面的编译器输出。
pause
exit /b 1
