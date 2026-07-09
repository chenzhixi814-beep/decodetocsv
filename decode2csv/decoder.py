"""流式解码：bytes -> 帧 -> CSV 行。与 GUI 完全解耦，不得 import tkinter。"""

from __future__ import annotations

import csv
import os
from dataclasses import dataclass
from typing import Callable, Optional

from .protocol import CHECKSUM_FUNCS, Protocol


class DecodeError(Exception):
    """文件级致命错误：数据文件打不开/输出文件不可写等（中文提示）。"""


@dataclass
class Stats:
    total_bytes: int = 0
    ok_frames: int = 0
    checksum_fail_dropped: int = 0
    checksum_fail_kept: int = 0
    garbage_bytes: int = 0
    tail_residual_bytes: int = 0

    def summary_text(self) -> str:
        return (
            f"文件总字节数={self.total_bytes} "
            f"成功帧数={self.ok_frames} "
            f"校验失败丢弃帧数={self.checksum_fail_dropped} "
            f"校验失败保留帧数={self.checksum_fail_kept} "
            f"垃圾字节数={self.garbage_bytes} "
            f"尾部残余字节数={self.tail_residual_bytes}"
        )


ProgressCallback = Callable[[int, int], None]
CancelCallback = Callable[[], bool]


def decode_file(
    protocol: Protocol,
    input_path: str,
    output_path: str,
    progress_cb: Optional[ProgressCallback] = None,
    cancel_cb: Optional[CancelCallback] = None,
) -> Stats:
    """按协议解码单个数据文件为 CSV。致命错误抛出 DecodeError（中文）。"""
    try:
        total_size = os.path.getsize(input_path)
    except OSError as e:
        raise DecodeError(f"无法读取数据文件 \"{input_path}\"：{e}")

    try:
        fin = open(input_path, "rb")
    except OSError as e:
        raise DecodeError(f"无法打开数据文件 \"{input_path}\"：{e}")

    stats = Stats()
    try:
        with fin:
            try:
                fout = open(output_path, "w", newline="", encoding="utf-8-sig")
            except OSError as e:
                raise DecodeError(f"无法创建输出文件 \"{output_path}\"：{e}")
            with fout:
                writer = csv.writer(fout)
                header = protocol.csv_header()
                if protocol.has_check_column:
                    header = header + ["校验"]
                writer.writerow(header)

                def emit(field_bytes: bytes, fail: bool):
                    cells = protocol.decode_fields(field_bytes)
                    if protocol.has_check_column:
                        cells = cells + (["FAIL"] if fail else ["OK"])
                    writer.writerow(cells)

                chunk_size = max(1024 * 1024, protocol.frame_len * 2)
                _run_core(protocol, fin, total_size, stats, emit, chunk_size, progress_cb, cancel_cb)
    except OSError as e:
        raise DecodeError(f"读写过程中发生错误：{e}")

    return stats


def _verify(protocol: Protocol, frame: bytes) -> Optional[bool]:
    """返回 True/False（校验通过/失败），无校验配置时返回 None。"""
    checksum = protocol.checksum
    if checksum is None:
        return None
    frame_len = protocol.frame_len
    checksum_len = protocol.checksum_len
    sync_len = protocol.sync_len
    if checksum.range == "payload":
        data = frame[sync_len: frame_len - checksum_len]
    else:  # "frame"：同步字起到最后一个字段末尾
        data = frame[0: frame_len - checksum_len]
    expected = CHECKSUM_FUNCS[checksum.type](data)
    stored_bytes = frame[frame_len - checksum_len: frame_len]
    stored = int.from_bytes(stored_bytes, byteorder=protocol.endian)
    return expected == stored


def _run_core(
    protocol: Protocol,
    fin,
    total_size: int,
    stats: Stats,
    emit: Callable[[bytes, bool], None],
    chunk_size: int,
    progress_cb: Optional[ProgressCallback],
    cancel_cb: Optional[CancelCallback],
) -> None:
    sync = protocol.sync
    sync_len = protocol.sync_len
    frame_len = protocol.frame_len
    checksum_len = protocol.checksum_len
    checksum = protocol.checksum

    buffer = b""
    processed = 0
    eof = False

    while True:
        if cancel_cb is not None and cancel_cb():
            return

        if not eof:
            chunk = fin.read(chunk_size)
            if chunk:
                buffer += chunk
                processed += len(chunk)
                stats.total_bytes = processed
                if progress_cb is not None:
                    progress_cb(processed, total_size)
            else:
                eof = True

        if sync is None:
            while len(buffer) >= frame_len:
                frame = buffer[:frame_len]
                buffer = buffer[frame_len:]
                field_bytes = frame[0: frame_len - checksum_len]
                ok = _verify(protocol, frame)
                if ok is None or ok:
                    emit(field_bytes, False)
                    stats.ok_frames += 1
                elif checksum.on_fail == "keep":
                    emit(field_bytes, True)
                    stats.checksum_fail_kept += 1
                else:
                    stats.checksum_fail_dropped += 1
            if eof:
                stats.tail_residual_bytes += len(buffer)
                buffer = b""
                break
        else:
            while True:
                idx = buffer.find(sync)
                if idx == -1:
                    if eof:
                        stats.garbage_bytes += len(buffer)
                        buffer = b""
                    else:
                        keep = max(0, len(buffer) - (len(sync) - 1))
                        stats.garbage_bytes += keep
                        buffer = buffer[keep:]
                    break

                stats.garbage_bytes += idx
                buffer = buffer[idx:]

                if len(buffer) < frame_len:
                    if eof:
                        stats.tail_residual_bytes += len(buffer)
                        buffer = b""
                    break

                frame = buffer[:frame_len]
                field_bytes = frame[sync_len: frame_len - checksum_len]
                ok = _verify(protocol, frame)
                if ok is None or ok:
                    emit(field_bytes, False)
                    stats.ok_frames += 1
                    buffer = buffer[frame_len:]
                elif checksum.on_fail == "keep":
                    emit(field_bytes, True)
                    stats.checksum_fail_kept += 1
                    buffer = buffer[frame_len:]
                else:
                    stats.checksum_fail_dropped += 1
                    stats.garbage_bytes += 1
                    buffer = buffer[1:]

        if eof:
            break
