"""协议 JSON 加载与校验，产出 Protocol 对象。

本模块不得 import tkinter，保证可脱离显示环境运行（NFR-5）。
"""

from __future__ import annotations

import json
import re
import struct
from dataclasses import dataclass, field as _dc_field
from typing import List, Optional, Tuple


class ProtocolError(Exception):
    """协议配置文件不合法。message 为中文，包含定位信息。"""


# ---------------------------------------------------------------------------
# 校验算法（第 6 节）
# ---------------------------------------------------------------------------

def sum8(data: bytes) -> int:
    return sum(data) & 0xFF


def xor8(data: bytes) -> int:
    r = 0
    for b in data:
        r ^= b
    return r & 0xFF


def _make_crc16_table_msb_first(poly: int) -> List[int]:
    table = []
    for byte in range(256):
        crc = byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ poly) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
        table.append(crc)
    return table


def _make_crc16_table_lsb_first(poly_reflected: int) -> List[int]:
    table = []
    for byte in range(256):
        crc = byte
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ poly_reflected
            else:
                crc >>= 1
        table.append(crc & 0xFFFF)
    return table


_CRC16_CCITT_TABLE = _make_crc16_table_msb_first(0x1021)
_CRC16_MODBUS_TABLE = _make_crc16_table_lsb_first(0xA001)


def crc16_ccitt(data: bytes) -> int:
    """CRC-16/XMODEM：poly 0x1021，初值 0x0000，不反转，无最终异或。"""
    crc = 0x0000
    for b in data:
        crc = ((crc << 8) & 0xFFFF) ^ _CRC16_CCITT_TABLE[((crc >> 8) ^ b) & 0xFF]
    return crc & 0xFFFF


def crc16_modbus(data: bytes) -> int:
    """CRC-16/MODBUS：poly 0x8005（反射 0xA001），初值 0xFFFF，输入/输出反转。"""
    crc = 0xFFFF
    for b in data:
        crc = (crc >> 8) ^ _CRC16_MODBUS_TABLE[(crc ^ b) & 0xFF]
    return crc & 0xFFFF


CHECKSUM_FUNCS = {
    "sum8": sum8,
    "xor8": xor8,
    "crc16-ccitt": crc16_ccitt,
    "crc16-modbus": crc16_modbus,
}

CHECKSUM_LENGTHS = {
    "sum8": 1,
    "xor8": 1,
    "crc16-ccitt": 2,
    "crc16-modbus": 2,
}


# ---------------------------------------------------------------------------
# 字段类型表（4.2 节）：type -> (struct 码, 字节数, 值域)
# ---------------------------------------------------------------------------

# 值域为 None 的类型（bytes/char/padding）在 fields[].size 中指定字节数。
FIELD_TYPES = {
    "uint8": ("B", 1, "int"),
    "int8": ("b", 1, "int"),
    "uint16": ("H", 2, "int"),
    "int16": ("h", 2, "int"),
    "uint32": ("I", 4, "int"),
    "int32": ("i", 4, "int"),
    "uint64": ("Q", 8, "int"),
    "int64": ("q", 8, "int"),
    "float32": ("f", 4, "float"),
    "float64": ("d", 8, "float"),
    "bytes": (None, None, "bytes"),
    "char": (None, None, "char"),
    "padding": (None, None, "padding"),
}

NUMERIC_KINDS = ("int", "float")

ALLOWED_TOP_KEYS = {"name", "description", "endian", "frame", "fields"}
ALLOWED_FRAME_KEYS = {"sync", "checksum"}
ALLOWED_CHECKSUM_KEYS = {"type", "range", "on_fail"}
ALLOWED_FIELD_KEYS = {"name", "type", "size", "count", "scale", "offset", "unit", "endian"}


@dataclass
class ChecksumSpec:
    type: str
    range: str = "frame"
    on_fail: str = "drop"


@dataclass
class Field:
    name: str
    type: str
    size: Optional[int]
    count: int
    scale: float
    offset: float
    has_scale_or_offset: bool
    unit: Optional[str]
    endian: Optional[str]

    @property
    def kind(self) -> str:
        return FIELD_TYPES[self.type][2]

    @property
    def elem_size(self) -> int:
        table_size = FIELD_TYPES[self.type][1]
        return table_size if table_size is not None else self.size

    @property
    def total_len(self) -> int:
        return self.elem_size * self.count


@dataclass
class Protocol:
    name: str
    description: str
    endian: str
    sync: Optional[bytes]
    checksum: Optional[ChecksumSpec]
    fields: List[Field]
    frame_len: int
    sync_len: int
    checksum_len: int
    _runs: list = _dc_field(default_factory=list, repr=False)

    @property
    def has_check_column(self) -> bool:
        return self.checksum is not None and self.checksum.on_fail == "keep"

    def csv_header(self) -> List[str]:
        headers = []
        for f in self.fields:
            if f.type == "padding":
                continue
            unit_suffix = f"({f.unit})" if f.unit else ""
            if f.count > 1:
                for i in range(f.count):
                    headers.append(f"{f.name}[{i}]{unit_suffix}")
            else:
                headers.append(f"{f.name}{unit_suffix}")
        return headers

    def decode_fields(self, data: bytes) -> List[str]:
        """将字段区原始字节解码为按列顺序排列的 CSV 单元格字符串列表。"""
        cells: List[str] = []
        offset = 0
        for run in self._runs:
            st: struct.Struct = run["struct"]
            values = st.unpack_from(data, offset)
            offset += st.size
            vi = 0
            for f, n_values in run["items"]:
                if f.type == "padding":
                    continue
                for v in values[vi:vi + n_values]:
                    cells.append(_format_value(f, v))
                vi += n_values
        return cells


def _format_value(f: Field, raw) -> str:
    if f.kind == "int":
        if not f.has_scale_or_offset:
            return str(int(raw))
        phys = float(raw) * f.scale + f.offset
        return repr(phys)
    if f.kind == "float":
        phys = float(raw) * f.scale + f.offset
        return repr(phys)
    if f.kind == "bytes":
        return raw.hex().upper()
    if f.kind == "char":
        stripped = raw.rstrip(b"\x00")
        if all(0x20 <= b <= 0x7E for b in stripped):
            return stripped.decode("ascii")
        return raw.hex().upper()
    raise AssertionError(f"未知字段种类：{f.kind}")


def _build_runs(fields: List[Field], default_endian: str) -> list:
    runs: list = []
    cur_endian: Optional[str] = None
    cur_fmt_parts: List[str] = []
    cur_items: List[Tuple[Field, int]] = []

    def flush():
        nonlocal cur_endian, cur_fmt_parts, cur_items
        if cur_items:
            order_char = "<" if (cur_endian or default_endian) == "little" else ">"
            fmt = order_char + "".join(cur_fmt_parts)
            runs.append({"struct": struct.Struct(fmt), "items": cur_items})
        cur_endian = None
        cur_fmt_parts = []
        cur_items = []

    for f in fields:
        if f.type == "padding":
            cur_fmt_parts.append(f"{f.size * f.count}x")
            cur_items.append((f, 0))
            continue
        if f.type in ("bytes", "char"):
            cur_fmt_parts.append(f"{f.size}s" * f.count)
            cur_items.append((f, f.count))
            continue
        code, _elem_size, _kind = FIELD_TYPES[f.type]
        eff_endian = f.endian or default_endian
        if cur_endian is None:
            cur_endian = eff_endian
        elif cur_endian != eff_endian:
            flush()
            cur_endian = eff_endian
        cur_fmt_parts.append(f"{f.count}{code}")
        cur_items.append((f, f.count))
    flush()
    return runs


# ---------------------------------------------------------------------------
# 加载与校验
# ---------------------------------------------------------------------------

def _err(msg: str):
    raise ProtocolError(f"协议错误：{msg}")


def _is_plain_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _is_positive_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v >= 1


def _is_nonempty_str(v) -> bool:
    return isinstance(v, str) and v.strip() != ""


def _actual(v) -> str:
    """格式化报错信息里"实际为 ..."的值：字符串用双引号包裹，其余用 repr。"""
    if isinstance(v, str):
        return f'"{v}"'
    return repr(v)


def load_protocol(path: str) -> Protocol:
    """从 JSON 文件加载并校验协议，失败抛出 ProtocolError（中文，含定位信息）。"""
    try:
        with open(path, "r", encoding="utf-8-sig") as fp:
            text = fp.read()
    except OSError as e:
        raise ProtocolError(f"协议错误：无法读取协议文件 \"{path}\"：{e}")

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ProtocolError(f"协议错误：JSON 语法错误（第 {e.lineno} 行）：{e.msg}")

    return _build_protocol(data)


def loads_protocol(text: str) -> Protocol:
    """从 JSON 字符串加载并校验协议（供测试直接使用）。"""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ProtocolError(f"协议错误：JSON 语法错误（第 {e.lineno} 行）：{e.msg}")
    return _build_protocol(data)


def _build_protocol(data) -> Protocol:
    if not isinstance(data, dict):
        _err("顶层必须为 JSON 对象")

    extra = set(data) - ALLOWED_TOP_KEYS
    if extra:
        _err(f"存在未知键：{', '.join(sorted(extra))}")

    if "name" not in data or not _is_nonempty_str(data["name"]):
        _err(f"\"name\" 必须为非空字符串，实际为 {_actual(data.get('name'))}")
    name = data["name"]

    description = data.get("description", "")
    if "description" in data and not isinstance(description, str):
        _err(f"\"description\" 必须为字符串，实际为 {_actual(description)}")

    endian = data.get("endian", "little")
    if endian not in ("little", "big"):
        _err(f"\"endian\" 必须为 \"little\" 或 \"big\"，实际为 {_actual(endian)}")

    frame_data = data.get("frame", {})
    if not isinstance(frame_data, dict):
        _err(f"\"frame\" 必须为 JSON 对象，实际为 {_actual(frame_data)}")
    extra = set(frame_data) - ALLOWED_FRAME_KEYS
    if extra:
        _err(f"\"frame\" 中存在未知键：{', '.join(sorted(extra))}")

    sync_bytes: Optional[bytes] = None
    if "sync" in frame_data:
        sync_raw = frame_data["sync"]
        if not isinstance(sync_raw, str):
            _err(f"\"frame.sync\" 必须为十六进制字符串，实际为 {_actual(sync_raw)}")
        cleaned = re.sub(r"\s+", "", sync_raw)
        if not re.fullmatch(r"[0-9a-fA-F]*", cleaned) or len(cleaned) < 2 or len(cleaned) % 2 != 0:
            _err(f"\"frame.sync\" 必须为偶数个十六进制字符且至少 1 字节，实际为 {_actual(sync_raw)}")
        sync_bytes = bytes.fromhex(cleaned)

    checksum: Optional[ChecksumSpec] = None
    if "checksum" in frame_data:
        cs = frame_data["checksum"]
        if not isinstance(cs, dict):
            _err(f"\"frame.checksum\" 必须为 JSON 对象，实际为 {_actual(cs)}")
        extra = set(cs) - ALLOWED_CHECKSUM_KEYS
        if extra:
            _err(f"\"frame.checksum\" 中存在未知键：{', '.join(sorted(extra))}")
        if "type" not in cs:
            _err("\"frame.checksum.type\" 为必填")
        cs_type = cs["type"]
        if cs_type not in CHECKSUM_FUNCS:
            _err(f"\"frame.checksum.type\" 必须为 {sorted(CHECKSUM_FUNCS)} 之一，实际为 {_actual(cs_type)}")
        cs_range = cs.get("range", "frame")
        if cs_range not in ("frame", "payload"):
            _err(f"\"frame.checksum.range\" 必须为 \"frame\" 或 \"payload\"，实际为 {_actual(cs_range)}")
        cs_on_fail = cs.get("on_fail", "drop")
        if cs_on_fail not in ("drop", "keep"):
            _err(f"\"frame.checksum.on_fail\" 必须为 \"drop\" 或 \"keep\"，实际为 {_actual(cs_on_fail)}")
        checksum = ChecksumSpec(type=cs_type, range=cs_range, on_fail=cs_on_fail)

    fields_data = data.get("fields")
    if not isinstance(fields_data, list) or len(fields_data) == 0:
        _err("\"fields\" 必须为非空数组")

    fields: List[Field] = []
    seen_names = set()
    for i, fd in enumerate(fields_data):
        loc = f"fields[{i}]"
        if not isinstance(fd, dict):
            _err(f"{loc} 必须为 JSON 对象，实际为 {_actual(fd)}")

        extra = set(fd) - ALLOWED_FIELD_KEYS
        if extra:
            name_hint = f"（名称 {_actual(fd.get('name'))}）" if "name" in fd else ""
            _err(f"{loc}{name_hint} 存在未知键：{', '.join(sorted(extra))}")

        if "name" not in fd or not _is_nonempty_str(fd["name"]):
            _err(f"{loc} 的 \"name\" 必须为非空字符串，实际为 {_actual(fd.get('name'))}")
        f_name = fd["name"]
        loc_named = f"{loc}（名称 \"{f_name}\"）"
        if f_name in seen_names:
            _err(f"{loc_named} 的字段名与之前字段重复")
        seen_names.add(f_name)

        if "type" not in fd or fd["type"] not in FIELD_TYPES:
            _err(f"{loc_named} 的 \"type\" 必须为 {sorted(FIELD_TYPES)} 之一，实际为 {_actual(fd.get('type'))}")
        f_type = fd["type"]
        kind = FIELD_TYPES[f_type][2]

        needs_size = f_type in ("bytes", "char", "padding")
        if needs_size:
            if "size" not in fd or not _is_positive_int(fd["size"]):
                _err(f"{loc_named} 的 \"size\" 必须为正整数，实际为 {_actual(fd.get('size'))}")
            f_size = fd["size"]
        else:
            if "size" in fd:
                _err(f"{loc_named} 的类型 \"{f_type}\" 不得出现 \"size\"")
            f_size = None

        if f_type == "padding" and "count" in fd:
            _err(f"{loc_named} 的类型 \"padding\" 不得出现 \"count\"")
        f_count = fd.get("count", 1)
        if not _is_positive_int(f_count):
            _err(f"{loc_named} 的 \"count\" 必须为正整数，实际为 {_actual(f_count)}")

        has_scale_or_offset = ("scale" in fd) or ("offset" in fd)
        if has_scale_or_offset and kind not in NUMERIC_KINDS:
            bad_key = "scale" if "scale" in fd else "offset"
            _err(f"{loc_named} 的类型 \"{f_type}\" 不允许出现 \"{bad_key}\"")
        f_scale = fd.get("scale", 1)
        if "scale" in fd and not _is_plain_number(f_scale):
            _err(f"{loc_named} 的 \"scale\" 必须为数值，实际为 {_actual(f_scale)}")
        f_offset = fd.get("offset", 0)
        if "offset" in fd and not _is_plain_number(f_offset):
            _err(f"{loc_named} 的 \"offset\" 必须为数值，实际为 {_actual(f_offset)}")

        f_unit = fd.get("unit")
        if "unit" in fd and not isinstance(f_unit, str):
            _err(f"{loc_named} 的 \"unit\" 必须为字符串，实际为 {_actual(f_unit)}")

        f_endian = fd.get("endian")
        if "endian" in fd and f_endian not in ("little", "big"):
            _err(f"{loc_named} 的 \"endian\" 必须为 \"little\" 或 \"big\"，实际为 {_actual(f_endian)}")

        fields.append(Field(
            name=f_name,
            type=f_type,
            size=f_size,
            count=f_count,
            scale=float(f_scale),
            offset=float(f_offset),
            has_scale_or_offset=has_scale_or_offset,
            unit=f_unit,
            endian=f_endian,
        ))

    sync_len = len(sync_bytes) if sync_bytes is not None else 0
    fields_len = sum(f.total_len for f in fields)
    checksum_len = CHECKSUM_LENGTHS[checksum.type] if checksum is not None else 0
    frame_len = sync_len + fields_len + checksum_len
    if frame_len < 1:
        _err("协议帧长必须 >= 1")

    runs = _build_runs(fields, endian)

    return Protocol(
        name=name,
        description=description,
        endian=endian,
        sync=sync_bytes,
        checksum=checksum,
        fields=fields,
        frame_len=frame_len,
        sync_len=sync_len,
        checksum_len=checksum_len,
        _runs=runs,
    )
