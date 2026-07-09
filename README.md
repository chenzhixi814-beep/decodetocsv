# decode2csv

由协议配置文件驱动的通用二进制解码工具。选择一个协议 JSON 和一个（或多个）原始
二进制数据文件，按协议逐帧解析并输出 CSV。完整需求见 `软件需求说明.md`。

## 快速开始

本项目在 Ubuntu 和 Windows 上均可开发/运行/打包，命令基本一致，唯一区别是
Windows 下 Python 解释器一般叫 `python` 而不是 `python3`。

```bash
# Ubuntu / Linux（python3）
python3 main.py                                                      # 启动 GUI
python3 main.py examples/sample_protocol.json examples/sample_data.bin
python3 main.py examples/sample_protocol.json examples/sample_data.bin -o out.csv
python3 main.py examples/sample_protocol.json a.bin b.bin            # 多文件，各自输出同名 .csv
python3 examples/gen_sample_data.py                                  # 生成示例数据（5 帧）
python3 examples/gen_sample_data.py --garbage --bad-crc              # 含垃圾字节与一个坏 CRC 帧
python3 -m pytest tests/ -v
```

```bat
:: Windows（cmd / PowerShell，命令是 python）
python main.py
python main.py examples\sample_protocol.json examples\sample_data.bin
python main.py examples\sample_protocol.json examples\sample_data.bin -o out.csv
python main.py examples\sample_protocol.json a.bin b.bin
python examples\gen_sample_data.py
python examples\gen_sample_data.py --garbage --bad-crc
python -m pytest tests\ -v
```

## 依赖

运行时仅使用 Python 标准库（`tkinter`/`struct`/`csv`/`json`/`threading`/`queue` 等），
无需安装任何第三方包。`pyinstaller` 仅在打包成单文件可执行程序时需要。

## 双平台开发（Ubuntu + Windows）

本仓库同时在 Ubuntu 和 Windows 上开发调试，`.vscode/` 下的调试配置已入库共享：

- 首次在某台机器上打开本项目，需手动执行一次命令面板 `Python: Select Interpreter`
  选择本机的 Python（`.vscode/settings.json` 里刻意不写死解释器路径，避免另一个
  平台打开时失效）。
- `.vscode/launch.json` 提供 GUI/CLI/生成示例数据/pytest 等调试配置，全部用
  `${workspaceFolder}`/`${file}` 等 VS Code 变量，两个平台无需改动即可直接用。
- 仓库根目录的 `.gitattributes` 强制文本文件用 LF 换行、二进制文件
  （`.DAT`/`.bin`/`.pdf`）禁止换行符转换——这是为了防止 Windows 端 Git 的
  `core.autocrlf` 悄悄改写文件，破坏 `build.sh` 的 shebang 或损坏二进制协议数据。
  新增文本类文件类型时，如有需要请同步更新 `.gitattributes`。
- 新代码请只用 `os.path`（或 `pathlib`）处理路径，不要硬编码 `/tmp`、`C:\...`
  之类平台专属路径，也不要引入 `fcntl`/`pwd` 等 POSIX-only 标准库模块。

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

**协议变更后要怎么手动改这个 JSON、改完怎么验证，见 [`协议修改指南.md`](协议修改指南.md)**
（按场景给了操作步骤 + 真实报错文案对照表 + 一个完整改造示例）。

仓库根目录的 `protocol.schema.json` 是配套的 JSON Schema，在 VS Code 里编辑
**文件名以 `_protocol.json` 结尾**的协议文件（如 `sample_protocol.json`）会
自动获得实时校验和字段名/取值自动补全（映射配置在 `.vscode/settings.json`
的 `json.schemas` 里），不需要在协议文件里加任何额外的键。这只是编辑器侧的
提前预警，权威校验仍然是 `decode2csv/protocol.py` 在真正加载协议时做的那一遍
（有些跨字段约束如"帧长必须 ≥1"，JSON Schema 表达不了）。新建协议文件时按
这个命名规则起名就能自动享受到校验。

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
