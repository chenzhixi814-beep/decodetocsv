"""覆盖第 6 节校验向量、5.4 异常矩阵 E-01~E-08、伪同步场景、协议错误场景。"""

from __future__ import annotations

import os
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decode2csv.decoder import decode_file
from decode2csv.protocol import (
    ProtocolError,
    crc16_ccitt,
    crc16_modbus,
    loads_protocol,
    sum8,
    xor8,
)

EXAMPLES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "examples")
sys.path.insert(0, EXAMPLES_DIR)


# ---------------------------------------------------------------------------
# 第 6 节：校验算法测试向量
# ---------------------------------------------------------------------------

class ChecksumVectorTests(unittest.TestCase):
    def test_sum8(self):
        self.assertEqual(sum8(b"123456789"), 0xDD)

    def test_xor8(self):
        self.assertEqual(xor8(b"123456789"), 0x31)

    def test_crc16_ccitt(self):
        self.assertEqual(crc16_ccitt(b"123456789"), 0x31C3)

    def test_crc16_modbus(self):
        self.assertEqual(crc16_modbus(b"123456789"), 0x4B37)

    def test_crc16_ccitt_pdf_appendix_vector(self):
        self.assertEqual(crc16_ccitt(b"abcdefg1234567HIJKLMN"), 0xEEF7)


# ---------------------------------------------------------------------------
# 测试用协议：sync=AA55，crc16-ccitt(frame, on_fail=drop)
#   id: uint8 | val: int16 scale=0.1 | tag: char[4] | raw: bytes[2]
#   _rsv: padding[1] | f: float32
#   字段区 = 1+2+4+2+1+4 = 14；帧长 = 2(sync)+14+2(crc) = 18
# ---------------------------------------------------------------------------

SYNC_PROTOCOL_JSON = """
{
  "name": "测试协议（同步字）",
  "endian": "little",
  "frame": {
    "sync": "AA55",
    "checksum": { "type": "crc16-ccitt", "range": "frame", "on_fail": "drop" }
  },
  "fields": [
    { "name": "id",  "type": "uint8" },
    { "name": "val", "type": "int16", "scale": 0.1, "unit": "V" },
    { "name": "tag", "type": "char", "size": 4 },
    { "name": "raw", "type": "bytes", "size": 2 },
    { "name": "_rsv", "type": "padding", "size": 1 },
    { "name": "f",   "type": "float32" }
  ]
}
"""

SYNC_PROTOCOL_KEEP_JSON = SYNC_PROTOCOL_JSON.replace('"on_fail": "drop"', '"on_fail": "keep"')

SYNC = b"\xAA\x55"


def encode_field_bytes(id_=1, val=123, tag=b"AB\x00\x00", raw=b"\xDE\xAD", f=1.5):
    return struct.pack("<Bh4s2sxf", id_, val, tag, raw, f)


def encode_frame(field_bytes=None, good_crc=True):
    if field_bytes is None:
        field_bytes = encode_field_bytes()
    body = SYNC + field_bytes
    crc = crc16_ccitt(body)
    if not good_crc:
        crc ^= 0xFFFF
    return body + struct.pack("<H", crc)


def write_temp(data: bytes) -> str:
    fd, path = tempfile.mkstemp()
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    return path


def decode_to_rows(protocol, data: bytes):
    in_path = write_temp(data)
    out_path = in_path + ".csv"
    try:
        stats = decode_file(protocol, in_path, out_path)
        with open(out_path, "r", encoding="utf-8-sig", newline="") as f:
            rows = f.read().splitlines()
        return stats, rows
    finally:
        os.remove(in_path)
        if os.path.exists(out_path):
            os.remove(out_path)


class SyncModeTests(unittest.TestCase):
    def setUp(self):
        self.protocol = loads_protocol(SYNC_PROTOCOL_JSON)
        self.protocol_keep = loads_protocol(SYNC_PROTOCOL_KEEP_JSON)
        self.assertEqual(self.protocol.frame_len, 18)

    def _decode(self, protocol, data: bytes):
        return decode_to_rows(protocol, data)

    def test_e01_empty_file(self):
        stats, rows = self._decode(self.protocol, b"")
        self.assertEqual(stats.total_bytes, 0)
        self.assertEqual(stats.ok_frames, 0)
        self.assertEqual(len(rows), 1)  # 仅表头

    def test_e02_file_smaller_than_one_frame(self):
        data = b"\x00\x01\x02"
        stats, rows = self._decode(self.protocol, data)
        self.assertEqual(stats.ok_frames, 0)
        self.assertEqual(stats.garbage_bytes, len(data))
        self.assertEqual(len(rows), 1)

    def test_single_valid_frame_roundtrip(self):
        frame = encode_frame(encode_field_bytes(id_=7, val=1234, tag=b"HI\x00\x00", raw=b"\x01\x02", f=2.5))
        stats, rows = self._decode(self.protocol, frame)
        self.assertEqual(stats.ok_frames, 1)
        self.assertEqual(stats.garbage_bytes, 0)
        header, row = rows
        cells = row.split(",")
        # id, val(0.1 scale), tag, raw(hex), f
        self.assertEqual(cells[0], "7")
        self.assertEqual(cells[1], repr(1234 * 0.1 + 0))
        self.assertEqual(cells[2], "HI")
        self.assertEqual(cells[3], "0102")
        self.assertEqual(cells[4], repr(2.5))

    def test_e03_garbage_tolerated_between_frames(self):
        junk1 = b"\x00\x11\x22\x33\x44"
        frame1 = encode_frame(encode_field_bytes(id_=1))
        junk2 = b"\x99\x88\x77"
        frame2 = encode_frame(encode_field_bytes(id_=2))
        data = junk1 + frame1 + junk2 + frame2
        stats, rows = self._decode(self.protocol, data)
        self.assertEqual(stats.ok_frames, 2)
        self.assertEqual(stats.garbage_bytes, len(junk1) + len(junk2))
        self.assertEqual(len(rows), 3)

    def test_e04_pseudo_sync_does_not_swallow_real_frame(self):
        # 垃圾区中埋入 SYNC 图样，但后续数据不构成合法帧（CRC 必然错误）
        fake = SYNC + b"\x00" * 20
        real = encode_frame(encode_field_bytes(id_=42))
        data = fake + real
        stats, rows = self._decode(self.protocol, data)
        self.assertEqual(stats.ok_frames, 1)
        self.assertGreaterEqual(stats.checksum_fail_dropped, 1)
        header, row = rows
        self.assertEqual(row.split(",")[0], "42")
        # 字节记账不变式：drop 重试消耗的字节最终计入垃圾字节数
        self.assertEqual(
            stats.total_bytes,
            stats.ok_frames * self.protocol.frame_len
            + stats.checksum_fail_kept * self.protocol.frame_len
            + stats.garbage_bytes
            + stats.tail_residual_bytes,
        )

    def test_e05_foreign_frame_bytes_skipped(self):
        other = b"\xEB\x3E\x85\x00" + b"\x00" * 10  # 完全不同的帧型
        real = encode_frame(encode_field_bytes(id_=9))
        data = other + real
        stats, rows = self._decode(self.protocol, data)
        self.assertEqual(stats.ok_frames, 1)
        self.assertEqual(rows[1].split(",")[0], "9")

    def test_e06_truncated_tail(self):
        good = encode_frame(encode_field_bytes(id_=3))
        partial = SYNC + b"\x01\x02\x03"  # 不足一帧
        data = good + partial
        stats, rows = self._decode(self.protocol, data)
        self.assertEqual(stats.ok_frames, 1)
        self.assertEqual(stats.tail_residual_bytes, len(partial))

    def test_e07_nan_inf_float(self):
        for val, expected in ((float("nan"), "nan"), (float("inf"), "inf"), (float("-inf"), "-inf")):
            frame = encode_frame(encode_field_bytes(f=val))
            stats, rows = self._decode(self.protocol, frame)
            self.assertEqual(stats.ok_frames, 1)
            self.assertEqual(rows[1].split(",")[-1], expected)

    def test_e08_char_non_printable_falls_back_to_hex(self):
        frame = encode_frame(encode_field_bytes(tag=b"\xff\xfe\x00\x00"))
        stats, rows = self._decode(self.protocol, frame)
        self.assertEqual(rows[1].split(",")[2], "FFFE0000")

    def test_checksum_fail_drop(self):
        bad = encode_frame(encode_field_bytes(id_=5), good_crc=False)
        stats, rows = self._decode(self.protocol, bad)
        self.assertEqual(stats.ok_frames, 0)
        self.assertEqual(stats.checksum_fail_dropped, 1)
        self.assertEqual(len(rows), 1)

    def test_checksum_fail_keep(self):
        bad = encode_frame(encode_field_bytes(id_=5), good_crc=False)
        stats, rows = self._decode(self.protocol_keep, bad)
        self.assertEqual(stats.ok_frames, 0)
        self.assertEqual(stats.checksum_fail_kept, 1)
        header, row = rows
        self.assertEqual(header.split(",")[-1], "校验")
        self.assertEqual(row.split(",")[-1], "FAIL")

    def test_checksum_ok_keep_marks_ok(self):
        good = encode_frame(encode_field_bytes(id_=6))
        stats, rows = self._decode(self.protocol_keep, good)
        self.assertEqual(stats.ok_frames, 1)
        self.assertEqual(rows[1].split(",")[-1], "OK")


# ---------------------------------------------------------------------------
# 定长顺序模式（无 frame.sync）
# ---------------------------------------------------------------------------

FIXED_PROTOCOL_JSON = """
{
  "name": "测试协议（定长）",
  "endian": "little",
  "fields": [
    { "name": "id", "type": "uint8" },
    { "name": "val", "type": "uint16" }
  ]
}
"""


class FixedModeTests(unittest.TestCase):
    def setUp(self):
        self.protocol = loads_protocol(FIXED_PROTOCOL_JSON)
        self.assertEqual(self.protocol.frame_len, 3)

    def _decode(self, data: bytes):
        return decode_to_rows(self.protocol, data)

    def test_sequential_frames(self):
        data = struct.pack("<BH", 1, 100) + struct.pack("<BH", 2, 200)
        stats, rows = self._decode(data)
        self.assertEqual(stats.ok_frames, 2)
        self.assertEqual(rows[1], "1,100")
        self.assertEqual(rows[2], "2,200")

    def test_tail_residual(self):
        data = struct.pack("<BH", 1, 100) + b"\x01"
        stats, rows = self._decode(data)
        self.assertEqual(stats.ok_frames, 1)
        self.assertEqual(stats.tail_residual_bytes, 1)


# ---------------------------------------------------------------------------
# 协议加载校验（E-09）
# ---------------------------------------------------------------------------

class ProtocolValidationTests(unittest.TestCase):
    def _expect_error(self, json_text, *substrings):
        with self.assertRaises(ProtocolError) as ctx:
            loads_protocol(json_text)
        msg = str(ctx.exception)
        for s in substrings:
            self.assertIn(s, msg)

    def test_json_syntax_error(self):
        self._expect_error("{not valid json", "JSON 语法错误")

    def test_unknown_top_level_key(self):
        self._expect_error(
            '{"name":"x","fields":[{"name":"a","type":"uint8"}],"foo":1}', "未知键"
        )

    def test_missing_name(self):
        self._expect_error('{"fields":[{"name":"a","type":"uint8"}]}', "name")

    def test_invalid_endian(self):
        self._expect_error(
            '{"name":"x","endian":"middle","fields":[{"name":"a","type":"uint8"}]}', "endian"
        )

    def test_empty_fields(self):
        self._expect_error('{"name":"x","fields":[]}', "fields")

    def test_unknown_field_type(self):
        self._expect_error(
            '{"name":"x","fields":[{"name":"a","type":"uint9"}]}', "fields[0]", "type"
        )

    def test_missing_size_for_bytes(self):
        self._expect_error(
            '{"name":"x","fields":[{"name":"a","type":"bytes"}]}', "fields[0]", "size"
        )

    def test_size_not_allowed_for_numeric(self):
        self._expect_error(
            '{"name":"x","fields":[{"name":"a","type":"uint8","size":1}]}', "size"
        )

    def test_duplicate_field_name(self):
        self._expect_error(
            '{"name":"x","fields":[{"name":"a","type":"uint8"},{"name":"a","type":"uint8"}]}',
            "fields[1]",
            "重复",
        )

    def test_unknown_field_key(self):
        self._expect_error(
            '{"name":"x","fields":[{"name":"a","type":"uint8","sacle":2}]}', "fields[0]", "sacle"
        )

    def test_scale_not_allowed_on_bytes(self):
        self._expect_error(
            '{"name":"x","fields":[{"name":"a","type":"bytes","size":2,"scale":2}]}', "scale"
        )

    def test_padding_with_count_rejected(self):
        self._expect_error(
            '{"name":"x","fields":[{"name":"a","type":"padding","size":1,"count":2}]}', "count"
        )

    def test_invalid_checksum_type(self):
        self._expect_error(
            '{"name":"x","frame":{"sync":"AA","checksum":{"type":"crc32"}},'
            '"fields":[{"name":"a","type":"uint8"}]}',
            "checksum.type",
        )

    def test_invalid_sync_hex(self):
        self._expect_error(
            '{"name":"x","frame":{"sync":"ABC"},"fields":[{"name":"a","type":"uint8"}]}', "sync"
        )


# ---------------------------------------------------------------------------
# examples/sample_protocol.json 端到端验收（附录 B）
# ---------------------------------------------------------------------------

class SampleProtocolAcceptanceTests(unittest.TestCase):
    def test_frame_length_is_139(self):
        from decode2csv.protocol import load_protocol

        path = os.path.join(EXAMPLES_DIR, "sample_protocol.json")
        protocol = load_protocol(path)
        self.assertEqual(protocol.frame_len, 139)

    def test_generated_sample_data_roundtrip(self):
        import gen_sample_data
        from decode2csv.protocol import load_protocol

        protocol = load_protocol(os.path.join(EXAMPLES_DIR, "sample_protocol.json"))
        frame0 = gen_sample_data.build_frame(0)
        frame1 = gen_sample_data.build_frame(1)
        data = frame0 + frame1
        stats, rows = decode_to_rows(protocol, data)
        self.assertEqual(stats.ok_frames, 2)
        header, row0, row1 = rows
        self.assertEqual(row0.split(",")[0], "0")
        self.assertEqual(row1.split(",")[0], "1")


if __name__ == "__main__":
    unittest.main()
