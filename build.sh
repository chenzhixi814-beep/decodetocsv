#!/usr/bin/env bash
# Linux 打包脚本：产出单文件可执行程序（ELF）。
# PyInstaller 不支持交叉编译，本脚本仅能在 Linux 上产出 Linux 可执行文件；
# Windows .exe 请在 Windows 上使用 build.bat。
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v pyinstaller >/dev/null 2>&1; then
    echo "未找到 pyinstaller，请先安装：" >&2
    echo "  python3 -m venv .venv && source .venv/bin/activate && pip install pyinstaller" >&2
    exit 1
fi

pyinstaller --onefile --windowed --name decode2csv main.py

echo "打包完成：dist/decode2csv"
