#!/usr/bin/env python3
"""按《通讯协议文档.pdf》生成“计算机字”帧的示例二进制数据。

配合 examples/sample_protocol.json 使用：用本脚本生成数据，再用
decode2csv 解码，CSV 数值应与生成时写入的物理值一致（经 LSB 换算）。
"""

from __future__ import annotations

import argparse
import math
import os
import random
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decode2csv.protocol import crc16_ccitt  # noqa: E402

SYNC = bytes.fromhex("EB3E8500")
LSB = 0.001


def _q(value: float, lsb: float = LSB) -> int:
    """发送方按 LSB 缩小并向下取整后发送（协议文档 1.6）。"""
    return int(math.floor(value / lsb))


def build_frame(seq: int) -> bytes:
    year, month, day, hour, minute = 2026, 7, 9, 12, 0
    second_phys = 30.0 + 0.1 * seq  # utc_second，scale=0.001，单位 s

    head = struct.pack(
        "<BHBBBBIII",
        seq & 0xFF,
        year, month, day, hour, minute,
        _q(second_phys) & 0xFFFFFFFF,
        1000 * seq,           # task_time，无 scale，单位 ms
        500 * seq,            # pulse_task_time，无 scale，单位 ms
    )
    mid = struct.pack(
        "<B3BBB",
        1,                     # gnss_fix_flag
        1, 1, 0,               # effective_flg[3]
        8 + (seq % 4),         # common_star_num
        0,                     # nav_err_code
    )

    groups = b""
    for g in range(8):
        vals = (1.0 * g + 0.1 * seq, -2.0 * g - 0.2 * seq, 0.001 * seq)
        groups += struct.pack("<3i", *(_q(v) for v in vals))

    tail = struct.pack(
        "<3i",
        _q(-28481.125 + seq),
        _q(46806.784 + seq),
        _q(35364.257 + seq),
    )

    payload = head + mid + groups + tail
    assert len(payload) == 133, f"字段区长度应为 133，实际为 {len(payload)}"

    frame_wo_crc = SYNC + payload
    crc = crc16_ccitt(frame_wo_crc)
    frame = frame_wo_crc + struct.pack("<H", crc)
    assert len(frame) == 139, f"帧长应为 139，实际为 {len(frame)}"
    return frame


def _random_junk(n: int, rng: random.Random) -> bytes:
    """生成不以 SYNC 开头的垃圾字节，避免意外形成真同步。"""
    while True:
        data = bytes(rng.randrange(0, 256) for _ in range(n))
        if SYNC not in data:
            return data


def main():
    parser = argparse.ArgumentParser(description="生成 decode2csv 示例数据（计算机字帧）")
    parser.add_argument("-n", "--count", type=int, default=5, help="生成帧数，默认 5")
    parser.add_argument(
        "-o", "--output",
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "sample_data.bin"),
        help="输出文件路径，默认 examples/sample_data.bin",
    )
    parser.add_argument("--garbage", action="store_true", help="在头部/帧间插入垃圾字节（含一处伪同步图样）")
    parser.add_argument("--bad-crc", action="store_true", help="注入一个 CRC 错误的帧（用于验证 on_fail 行为）")
    parser.add_argument("--seed", type=int, default=0, help="随机种子，默认 0（保证可重复）")
    args = parser.parse_args()

    rng = random.Random(args.seed)
    chunks = []

    if args.garbage:
        chunks.append(_random_junk(7, rng))

    for seq in range(args.count):
        frame = build_frame(seq)
        if args.bad_crc and seq == args.count // 2:
            # 翻转 CRC 的最后一个字节，制造校验失败帧
            frame = frame[:-1] + bytes([frame[-1] ^ 0xFF])
        chunks.append(frame)

        if args.garbage and seq < args.count - 1:
            junk = _random_junk(5, rng)
            if seq == 0:
                # 在垃圾区中人为埋入一份 SYNC 图样（后随错误数据），验证伪同步不吞真帧
                junk = SYNC + _random_junk(20, rng)
            chunks.append(junk)

    data = b"".join(chunks)
    with open(args.output, "wb") as f:
        f.write(data)

    print(f"已生成 {args.count} 帧，共 {len(data)} 字节 -> {args.output}")
    if args.garbage:
        print("已插入垃圾字节（含一处伪同步图样）")
    if args.bad_crc:
        print("已注入一个 CRC 错误帧")


if __name__ == "__main__":
    main()
