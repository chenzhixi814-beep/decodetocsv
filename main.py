#!/usr/bin/env python3
"""decode2csv 入口：无参数启动 GUI，带参数走 CLI。"""

from __future__ import annotations

import argparse
import os
import sys


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) == 0:
        return _run_gui()
    return _run_cli(argv)


def _run_gui() -> int:
    from decode2csv.gui import run_gui

    run_gui()
    return 0


def _run_cli(argv) -> int:
    parser = argparse.ArgumentParser(
        prog="decode2csv",
        description="按协议配置文件将二进制数据文件解码为 CSV",
    )
    parser.add_argument("protocol", help="协议配置文件（.json）")
    parser.add_argument("data_files", nargs="+", help="原始数据文件（可指定多个）")
    parser.add_argument("-o", "--output", help="输出 CSV 文件路径（仅单个数据文件时可用）")
    args = parser.parse_args(argv)

    if args.output and len(args.data_files) > 1:
        print("错误：-o 仅在单个数据文件时允许使用", file=sys.stderr)
        return 2

    from decode2csv.decoder import DecodeError, decode_file
    from decode2csv.protocol import ProtocolError, load_protocol

    try:
        protocol = load_protocol(args.protocol)
    except ProtocolError as e:
        print(str(e), file=sys.stderr)
        return 1

    for data_path in args.data_files:
        if args.output:
            out_path = args.output
        else:
            base, _ext = os.path.splitext(data_path)
            out_path = base + ".csv"

        try:
            stats = decode_file(protocol, data_path, out_path)
        except DecodeError as e:
            print(str(e), file=sys.stderr)
            return 1

        print(f"{data_path} -> {out_path}")
        print(stats.summary_text())
        if stats.ok_frames == 0 and stats.checksum_fail_kept == 0:
            print(f"警告：{data_path} 未解出任何帧", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
