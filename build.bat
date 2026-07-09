@echo off
REM Windows 打包脚本：产出单文件 .exe。
REM PyInstaller 不支持交叉编译，.exe 必须在 Windows 上打包。
cd /d "%~dp0"

where pyinstaller >nul 2>nul
if errorlevel 1 (
    echo 未找到 pyinstaller，请先安装：
    echo   python -m venv .venv ^&^& .venv\Scripts\activate ^&^& pip install pyinstaller
    exit /b 1
)

pyinstaller --onefile --windowed --name decode2csv main.py

echo 打包完成：dist\decode2csv.exe
