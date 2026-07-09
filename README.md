# decode2csv

由协议配置文件驱动的通用二进制解码工具。选择一个协议 JSON 和一个（或多个）原始
二进制数据文件，按协议逐帧解析并输出 CSV。完整需求见 `软件需求说明.md`。

## 快速开始

```bash
# 启动图形界面
python3 main.py

# 命令行解码（单文件，默认输出 <数据文件名>.csv）
python3 main.py examples/sample_protocol.json examples/sample_data.bin

# 命令行解码并指定输出路径（仅单文件时可用）
python3 main.py examples/sample_protocol.json examples/sample_data.bin -o out.csv

# 命令行解码多个文件（各自输出同名 .csv，不能加 -o）
python3 main.py examples/sample_protocol.json a.bin b.bin

# 生成示例二进制数据（“计算机字”帧，帧长 139 字节）
python3 examples/gen_sample_data.py                      # 5 帧，无垃圾/坏帧
python3 examples/gen_sample_data.py --garbage --bad-crc   # 含垃圾字节与一个坏 CRC 帧

# 运行测试
python3 -m pytest tests/ -v
```

## 依赖

运行时仅使用 Python 标准库（`tkinter`/`struct`/`csv`/`json`/`threading`/`queue` 等），
无需安装任何第三方包。`pyinstaller` 仅在打包成单文件可执行程序时需要。

## 协议配置文件

协议为 UTF-8 编码（允许带 BOM）的 JSON 文件，完整键定义见 `软件需求说明.md` 第 2.3
节，示例见 `examples/sample_protocol.json`（`软件需求说明.md` 附录 B）。核心结构：

```json
{
  "name": "协议名",
  "endian": "little",
  "frame": {
    "sync": "EB3E8500",
    "checksum": { "type": "crc16-ccitt", "range": "frame", "on_fail": "drop" }
  },
  "fields": [
    { "name": "counter", "type": "uint16" },
    { "name": "vel", "type": "int32", "count": 3, "scale": 0.001, "unit": "m/s" },
    { "name": "tag", "type": "char", "size": 4 },
    { "name": "_rsv", "type": "padding", "size": 2 }
  ]
}
```

顶层、`frame`、`checksum`、每个字段对象中出现表外的键都会被拒绝加载并给出中文错误
（含出错位置、字段名、键名），防止拼写错误静默失效。

## 帧定位模式

- **无 `frame.sync`（定长顺序模式）**：从文件第 0 字节起按帧长连续切分。
  **局限性**：对文件中间插入/丢失字节没有恢复能力——一旦错位，后续所有帧都会解析
  错误。这类数据务必配置同步字（`frame.sync`）。
- **有 `frame.sync`（同步字搜索模式）**：在字节流中搜索同步字，容忍帧间任意垃圾
  字节；校验失败且 `on_fail: "drop"` 时视为伪同步，仅前进 1 字节重新搜索，保证真帧
  不会被跳过。

## 打包为单文件可执行程序

```bash
./build.sh        # Linux -> dist/decode2csv（ELF）
build.bat         # Windows -> dist\decode2csv.exe（须在 Windows 上运行）
```

PyInstaller 不支持交叉编译，`.exe` 必须在 Windows 机器上打包；本仓库同时提供
`build.sh`（Linux）与 `build.bat`（Windows）。发布物为可执行文件 + `examples/` 目录。

## 项目结构

```
main.py                  # 入口：无参数启动 GUI，带参数走 CLI
decode2csv/
├── protocol.py          # 协议 JSON 加载与校验、校验算法、struct 编译
├── decoder.py           # 流式解码：bytes -> 帧 -> CSV 行（不依赖 GUI）
└── gui.py                # tkinter 界面
examples/
├── sample_protocol.json # 示例协议（“计算机字”帧，帧长 139 字节）
└── gen_sample_data.py    # 生成示例二进制数据
tests/test_decoder.py     # 自动化测试
```
