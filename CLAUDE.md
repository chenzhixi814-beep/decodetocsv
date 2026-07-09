# CLAUDE.md

本文件为 Claude Code 在本仓库工作时的指导说明。

## 项目概述

**decode2csv**：由协议配置文件驱动的通用二进制解码工具。用户通过 GUI 选择协议配置
（JSON）和原始二进制数据文件，软件按协议逐帧解析并输出 CSV。最终以 PyInstaller
打包为单文件可执行程序发布，在无 Python 环境的电脑上直接运行。

**完整需求见 `REQUIREMENTS.md`（权威依据，实现前必读）**，包括：功能需求编号
（FR-1 界面 / FR-2 解码 / FR-3 协议格式）、协议 JSON 的完整键定义与示例、
帧定位与异常处理精确规范（第 5 节）、校验算法定义与测试向量（第 6 节）、
非功能需求、验收标准。仓库内《COMMUNICATION_PROTOCOL.pdf》是目标协议原始文档，其关键内容
（帧结构、CRC、"计算机字"帧）已完整转录进需求说明附录 A/B，实现时以需求说明为准。

## 当前状态

- [x] 需求说明（`REQUIREMENTS.md`）
- [x] 解码核心（protocol.py / decoder.py）
- [x] tkinter GUI（gui.py）
- [x] CLI 模式（main.py）
- [x] 示例协议 + 示例数据生成脚本（examples/）
- [x] 自动化测试（tests/，35 项全绿）
- [x] 打包脚本（build.sh / build.bat）+ README.md
- [x] Git 版本控制 + VS Code 双平台调试配置

## 目标结构（需求文档第 4 节）

```
main.py                  # 入口：无参数启动 GUI，带参数走 CLI（argparse）
decode2csv/
├── __init__.py          # __version__
├── protocol.py          # 协议 JSON 加载与校验 → Protocol 对象（含帧长计算、struct 格式）
├── decoder.py           # 流式解码：bytes → 帧 → CSV 行（与 GUI 完全解耦）
└── gui.py               # tkinter 界面，调用 decoder
examples/                # sample_protocol.json + gen_sample_data.py
tests/test_decoder.py
```

## 硬性约束

- **Python ≥ 3.10，运行时仅用标准库**（tkinter/struct/csv/json/threading 等）。
  PyInstaller 只是打包期依赖，不得引入 numpy/pandas 等第三方运行时依赖。
- **协议与代码分离**：任何协议细节不得硬编码进程序；新协议 = 新 JSON 文件。
- **UI 与报错文案用中文**；代码标识符、注释用英文或中文均可，保持一致。
- **流式解码**：分块读取（如 1 MB/块），跨块保留残余缓冲；禁止一次性读入整个文件。
- GUI 解码放后台线程，通过 `queue` + `after()` 回传进度，不得阻塞主循环。
- CSV 输出编码为 `utf-8-sig`（带 BOM，Excel 直接打开不乱码）。
- 解码核心（protocol/decoder）不得 import tkinter，保证 CLI 与测试可脱离显示环境运行。

## 双平台开发（Ubuntu + Windows，长期硬性约束）

用户日常在 Ubuntu 笔记本上开发调试，同时需要在 Windows 上开发调试并发布 Windows
可执行程序。**后续所有功能开发都必须保证在两个平台上都能正常开发、调试、运行**，
这条约束不因功能大小而豁免。具体要求：

- **代码层面**：路径拼接一律用 `os.path`（已用，勿改用手写 `/` 或 `\` 拼接）；
  禁止 import 任何 POSIX-only 标准库模块（`fcntl`/`pwd`/`grp`/`os.fork` 等）；
  禁止硬编码 `/tmp`、`/home/...` 等 Linux 专属路径；临时文件用 `tempfile` 模块。
- **`.vscode/` 配置是共享文件（已入库）**：不得在 `launch.json`/`settings.json`
  里写绝对路径（如 `/usr/bin/python3`、`/tmp/xxx`）——两边签出后必须原样可用。
  Python 解释器路径不写死，靠 VS Code 的 "Python: Select Interpreter" 由每台机器
  各自选择（存在 VS Code 的本机状态里，不进 git）。
- **Git 换行符**：仓库根目录 `.gitattributes` 已强制文本文件 `eol=lf`、二进制
  文件（`.DAT`/`.bin`/`.pdf`）标记为 `binary`，防止 Windows 端 `core.autocrlf`
  悄悄转换换行符（轻则整文件级别假 diff，重则损坏二进制协议数据/弄坏
  `build.sh` 的 shebang）。新增文本类型的文件后缀如需要，同步补充规则。
- **打包**：PyInstaller 不支持交叉编译，`.exe` 必须在 Windows 机器上跑
  `build.bat` 产出，`build.sh` 只能在 Linux 上产出 ELF；两个脚本长期都要维护。
- **命令差异**：Windows 命令行用 `python`（一般没有 `python3` 别名），
  虚拟环境激活是 `.venv\Scripts\activate`（非 `source .venv/bin/activate`）；
  写文档/示例命令时两边都要给出。

## 关键实现要点（来自需求）

- 字段类型：整型/浮点各宽度、`bytes[N]`（hex 输出）、`char[N]`、`padding[N]`（跳过）；
  `count` 数组展开为 `name[0]…name[N-1]` 多列；`scale`/`offset` 线性换算；单字段可覆盖字节序。
- 帧定位两种模式：无 `frame.sync` 时定长顺序切分；有 sync 时在字节流中搜索同步字，
  容忍帧间垃圾字节（同步头搜索失败只前移 1 字节重试，不整帧跳过）。
- 校验：`sum8`/`xor8`/`crc16-ccitt`/`crc16-modbus`，`range: frame|payload`（frame 含
  同步字，默认），`on_fail: drop|keep`（keep 时 CSV 追加"校验"列 OK/FAIL）。
  `crc16-ccitt` = CRC-16/XMODEM（poly 0x1021、**初值 0x0000**、不反转），即协议 PDF
  附录 A 算法，测试向量 `b"abcdefg1234567HIJKLMN"` → `0xEEF7`。
- 同步模式下校验失败且 drop 时视为**伪同步**：指针只前进 1 字节重新搜索，
  保证真帧不被吞掉；keep 时输出标记行并前进整帧。
- 帧长自动计算 = sync 长度 + Σ字段长度 + 校验长度。
- 协议加载错误要指明第几个字段、哪个键不合法（中文报错）。

## 常用命令

```bash
# Ubuntu / Linux
python3 main.py                                   # 启动 GUI
python3 main.py examples/sample_protocol.json data.bin -o out.csv   # CLI 解码
python3 examples/gen_sample_data.py               # 生成测试用二进制数据
python3 -m pytest tests/ -v                       # 跑测试（或 unittest）
./build.sh                                        # 打包 Linux 可执行文件

# Windows（cmd/PowerShell，命令是 python 不是 python3）
python main.py
python main.py examples\sample_protocol.json data.bin -o out.csv
python examples\gen_sample_data.py
python -m pytest tests\ -v
build.bat                                          # 打包 Windows .exe
```

## 打包注意

- PyInstaller 命令：`pyinstaller --onefile --windowed --name decode2csv main.py`
- 不支持交叉编译：Windows `.exe` 必须在 Windows 上用 `build.bat` 打包；
  Linux 上只能用 `build.sh` 产出 ELF 可执行文件。两条打包路径长期并存，
  不能因为在其中一个平台上开发就让另一个平台的打包脚本失修。
- Ubuntu 开发机 Python 3.12 / tkinter 8.6 已可用；PyInstaller 未安装（Ubuntu 有
  PEP 668 限制，建议在 venv 中 `pip install pyinstaller`）。Windows 机器同样建议
  用 venv 安装 PyInstaller，避免污染全局环境。

## 验收口径

以 `REQUIREMENTS.md` 第 6 节为准：示例协议 + 生成数据解码结果与写入值一致
（含 scale/offset 换算）；插入垃圾字节后同步搜索仍解出全部有效帧；校验失败帧
按配置丢弃/标记；tests 全绿。
