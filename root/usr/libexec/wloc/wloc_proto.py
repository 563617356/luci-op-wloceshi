"""WLOC protobuf 帧解析与坐标改写。

逻辑 1:1 移植自 Yu9191/wloc 的 dist/wloc.js（MIT 协议逆向用途）。
原始实现是压缩过的 JS，这里拆成纯函数便于单测，也便于被 addon 复用。

坐标系说明：Apple WLOC 回包中的经纬度是 WGS-84 定点数，
以 varint 存储，实际值 = 原始整数 / 1e8。
"""

import struct

WIRE_VARINT = 0
WIRE_FIXED64 = 1
WIRE_BYTES = 2
WIRE_FIXED32 = 5

_VARINT_MAX_BYTES = 10


class WlocParseError(Exception):
    """protobuf 结构不合法时抛出。上层应据此放弃改包并放行原始响应。"""


class NoPatchNeeded(WlocParseError):
    """回包能解析，但目标坐标与原值一致，无需改动。

    与 WlocParseError 分开是因为语义不同：前者是"没找到可改的载荷"，
    属于异常；后者是"本来就对"，属正常情况，不该在日志里报警。
    """


def read_varint(buf, pos):
    """读 varint，返回 (值, 新位置)。结果按 64 位有符号解释。"""
    result = 0
    shift = 0
    start = pos
    while True:
        if pos >= len(buf):
            raise WlocParseError("truncated varint at %d" % start)
        if pos - start >= _VARINT_MAX_BYTES:
            raise WlocParseError("varint too long at %d" % start)
        byte = buf[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            if result >= 1 << 63:
                result -= 1 << 64
            return result, pos
        shift += 7


def encode_varint(value):
    """varint 编码。

    非负数走标准 7 位分组。负数按 protobuf 规范编码为 10 字节
    64 位补码——南纬/西经的定位坐标确实是负数，必须支持。
    """
    if value < 0:
        # int32 负值在 protobuf 中以 64 位补码形式占用 10 字节
        value += 1 << 64
        out = bytearray()
        while value >= 0x80:
            out.append((value & 0x7F) | 0x80)
            value >>= 7
        out.append(value)
        if len(out) < 10:
            out[-1] |= 0x80 << (7 * (10 - len(out)))
            out.extend(b"\x80" * (9 - len(out)))
        return bytes(out)

    out = bytearray()
    while value >= 0x80:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def parse_fields(buf):
    """把一段 protobuf 解析成字段列表。

    每个字段是 (field_no, wire_type, value, raw)。value 的含义随 wire_type：
      - VARINT    -> int
      - BYTES     -> bytes（已剥离长度前缀）
      - FIXED64/32-> bytes
    raw 为该字段在原 buffer 中的完整切片（含 tag 与长度前缀），
    改写时原样回填即可保持其余字段逐字节不变。
    """
    fields = []
    pos = 0
    n = len(buf)
    while pos < n:
        start = pos
        key, pos = read_varint(buf, pos)
        field_no = key >> 3
        wire_type = key & 0x07
        if field_no == 0:
            raise WlocParseError("invalid protobuf field 0 at %d" % start)

        if wire_type == WIRE_VARINT:
            value, pos = read_varint(buf, pos)
        elif wire_type == WIRE_BYTES:
            length, pos = read_varint(buf, pos)
            if pos + length > n:
                raise WlocParseError("length-delimited field overruns buffer at %d" % start)
            value = buf[pos:pos + length]
            pos += length
        elif wire_type == WIRE_FIXED64:
            if pos + 8 > n:
                raise WlocParseError("fixed64 overruns buffer at %d" % start)
            value = buf[pos:pos + 8]
            pos += 8
        elif wire_type == WIRE_FIXED32:
            if pos + 4 > n:
                raise WlocParseError("fixed32 overruns buffer at %d" % start)
            value = buf[pos:pos + 4]
            pos += 4
        else:
            raise WlocParseError("unsupported wire type %d" % wire_type)

        fields.append((field_no, wire_type, value, buf[start:pos]))

    return fields


def encode_field(field_no, wire_type, value):
    """编码单个字段，用于替换原字段。"""
    tag = encode_varint((field_no << 3) | wire_type)
    if wire_type == WIRE_VARINT:
        return tag + encode_varint(value)
    if wire_type in (WIRE_FIXED64, WIRE_FIXED32):
        return tag + value
    if wire_type == WIRE_BYTES:
        return tag + encode_varint(len(value)) + value
    raise WlocParseError("cannot encode wire type %d" % wire_type)


class Stats(object):
    """改包计数器，用于回传 LuCI 展示与日志。"""

    def __init__(self):
        self.locations = 0
        self.wifi = 0
        self.cell = 0
        self.skipped = 0
        self.dropped = 0

    def snapshot(self):
        return (self.locations, self.wifi, self.cell, self.skipped, self.dropped)

    def restore(self, snap):
        (self.locations, self.wifi, self.cell,
         self.skipped, self.dropped) = snap

    def delta_since(self, snap):
        now = self.snapshot()
        return sum(now[i] - snap[i] for i in range(3))

    def as_dict(self):
        return {
            "locations": self.locations,
            "wifi": self.wifi,
            "cell": self.cell,
            "skipped": self.skipped,
            "dropped": self.dropped,
        }


# WLoc8（OpenHRTT/wloc）在改写坐标时还会填这几个固定值，
# 源码注释写明用意是"主动填满可选定位字段，减少上游对缺失字段的异常判断"。
# 缺了它们可能导致系统走异常分支，定位不生效或行为不一致。
_EXTRA_LOCATION_FIELDS = (
    (4, 3),      # unknownValue4
    (10, 63),    # motionActivityType
    (11, 467),   # motionActivityConfidence
)


def patch_position(buf, lat, lon, accuracy, stats, fill_extra=True):
    """改写单个定位条目：field 1=纬度 field 2=经度 field 3=精度(米)。

    三个字段都是 varint 定点数，前置放大 1e8。
    仅当 field 1 与 field 2 同时存在才动手，避免误改非定位结构。

    fill_extra=True 时额外写入 _EXTRA_LOCATION_FIELDS 里的固定值，
    对齐 WLoc8 的行为；已存在的同号字段会被覆盖。
    """
    fields = parse_fields(buf)
    has_lat = has_lon = False
    for field_no, wire_type, _value, _raw in fields:
        if field_no == 1 and wire_type == WIRE_VARINT:
            has_lat = True
        elif field_no == 2 and wire_type == WIRE_VARINT:
            has_lon = True
    if not (has_lat and has_lon):
        return buf

    extra = dict(_EXTRA_LOCATION_FIELDS) if fill_extra else {}

    # 已存在的补充字段号：只在原条目确实带有时才覆盖，
    # 否则不追加——否则每次改写都会让 payload 变长，
    # 且"目标坐标等于原值"时也无法再被识别为无需改动。
    present = set()
    for field_no, wire_type, _value, _raw in fields:
        if wire_type == WIRE_VARINT:
            present.add(field_no)

    out = bytearray()
    for field_no, wire_type, _value, raw in fields:
        if field_no == 1 and wire_type == WIRE_VARINT:
            out += encode_field(1, WIRE_VARINT, round(1e8 * lat))
        elif field_no == 2 and wire_type == WIRE_VARINT:
            out += encode_field(2, WIRE_VARINT, round(1e8 * lon))
        elif field_no == 3 and wire_type == WIRE_VARINT:
            out += encode_field(3, WIRE_VARINT, accuracy)
        elif (fill_extra and field_no in extra
              and wire_type == WIRE_VARINT and field_no in present):
            out += encode_field(field_no, WIRE_VARINT, extra[field_no])
        else:
            out += raw

    stats.locations += 1
    return bytes(out)


_MAC_LEN = 17  # "xx:xx:xx:xx:xx:xx"


def _looks_like_mac(value):
    if not isinstance(value, bytes) or len(value) != _MAC_LEN:
        return False
    try:
        text = value.decode("ascii")
    except UnicodeDecodeError:
        return False
    parts = text.split(":")
    if len(parts) != 6:
        return False
    return all(1 <= len(p) <= 2 and all(c in "0123456789abcdefABCDEF" for c in p) for p in parts)


def patch_wifi_cell(buf, lat, lon, accuracy, stats):
    """WiFi 定位条目：先校验 field 1 是否为 MAC 地址，命中才递归改 field 2。

    这层校验是必要的——field 2 在其它子消息里也可能出现，
    不加校验会误伤非定位数据。
    """
    fields = parse_fields(buf)

    matched = False
    for field_no, wire_type, value, _raw in fields:
        if field_no == 1 and wire_type == WIRE_BYTES and _looks_like_mac(value):
            matched = True
            break
    if not matched:
        return buf

    changed = False
    out = bytearray()
    for field_no, wire_type, value, raw in fields:
        if field_no == 2 and wire_type == WIRE_BYTES:
            try:
                patched = patch_position(value, lat, lon, accuracy, stats)
            except WlocParseError:
                stats.skipped += 1
                out += raw
                continue
            if patched != value:
                changed = True
            out += encode_field(2, WIRE_BYTES, patched)
        else:
            out += raw

    if changed:
        stats.wifi += 1
    return bytes(out)


def patch_cell(buf, lat, lon, accuracy, stats):
    """基站定位条目：定位数据在 field 5。"""
    fields = parse_fields(buf)
    changed = False
    out = bytearray()
    for field_no, wire_type, value, raw in fields:
        if field_no == 5 and wire_type == WIRE_BYTES:
            try:
                patched = patch_position(value, lat, lon, accuracy, stats)
            except WlocParseError:
                stats.skipped += 1
                out += raw
                continue
            if patched != value:
                changed = True
            out += encode_field(5, WIRE_BYTES, patched)
        else:
            out += raw

    if changed:
        stats.cell += 1
    return bytes(out)


# 顶层计数/类型字段的置空。
#
# WLoc8（OpenHRTT/wloc）在 mutateResponseBody 里对
# numCellResults / numWifiResults / deviceType 三个字段做了 = nil。
#
# 但本项目不启用它：字段号无法从公开信息确证（其 .proto 未开源，
# 我逆向的字段表里 field 7 是有实际内容的字段，猜测置空会破坏数据）。
# 贸然丢弃比不丢弃风险更高。保持 _DROP_TOP_FIELDS 为空，
# 待抓包确认字段号后再启用。
_DROP_TOP_FIELDS = frozenset()


def patch_payload(buf, lat, lon, accuracy, stats, drop_fields=True):
    """顶层分派：field 2 = WiFi 列表，field 22 / 24 = 基站列表。

    drop_fields 参数保留给后续启用 _DROP_TOP_FIELDS 的情况；
    当前该集合为空，对输出无影响。
    """
    fields = parse_fields(buf)
    out = bytearray()
    for field_no, wire_type, value, raw in fields:
        if drop_fields and field_no in _DROP_TOP_FIELDS:
            stats.dropped += 1
            continue
        if wire_type == WIRE_BYTES and field_no == 2:
            out += encode_field(2, WIRE_BYTES, patch_wifi_cell(value, lat, lon, accuracy, stats))
        elif wire_type == WIRE_BYTES and field_no in (22, 24):
            out += encode_field(field_no, WIRE_BYTES, patch_cell(value, lat, lon, accuracy, stats))
        else:
            out += raw
    return bytes(out)


def _patch_frame_at(buf, base, lat, lon, accuracy, stats):
    """在 base 偏移处套一层帧头并改写 payload。

    帧结构：[base 字节前导][2 字节大端长度][payload][剩余]
    改写后长度可能变化，需要重写那 2 字节。
    """
    if len(buf) < base + 10:
        raise WlocParseError("body too short: %d, base=%d" % (len(buf), base))

    frame_len = (buf[base + 8] << 8) | buf[base + 9]
    if frame_len <= 0:
        raise WlocParseError("invalid empty frame length at %d" % base)
    if frame_len + base + 10 > len(buf):
        raise WlocParseError(
            "invalid frame length %d at %d for %d" % (frame_len, base, len(buf))
        )

    header = buf[:base + 8]
    payload = buf[base + 10:base + 10 + frame_len]
    tail = buf[base + 10 + frame_len:]

    saved = stats.snapshot()
    patched = patch_payload(payload, lat, lon, accuracy, stats)
    delta = stats.delta_since(saved)

    if len(patched) > 0xFFFF:
        stats.restore(saved)
        raise WlocParseError("patched payload too large: %d" % len(patched))
    if delta <= 0:
        stats.restore(saved)
        raise WlocParseError("frame parsed but no patchable wloc payload at %d" % base)
    if patched == payload:
        stats.restore(saved)
        raise NoPatchNeeded("target coordinates already match at base %d" % base)

    return header + struct.pack(">H", len(patched)) + patched + tail


_PREFERRED_BASES = (0, 2, 4, 6, 8, 10, 12, 14, 16)
_MAX_BASE_SCAN = 96
_MAX_RAW_SCAN = 256


def _patch_raw_scan(buf, lat, lon, accuracy, stats):
    """兜底：整段当裸 protobuf 逐字节滑窗尝试。"""
    last_error = None
    for offset in range(0, min(_MAX_RAW_SCAN, len(buf)) + 1):
        saved = stats.snapshot()
        try:
            patched = patch_payload(buf[offset:], lat, lon, accuracy, stats)
        except WlocParseError as exc:
            stats.restore(saved)
            last_error = exc
            continue
        if stats.delta_since(saved) > 0:
            if patched == buf[offset:]:
                stats.restore(saved)
                raise NoPatchNeeded("target coordinates already match at raw offset %d" % offset)
            return buf[:offset] + patched
        stats.restore(saved)
        last_error = "no patchable payload at raw offset %d" % offset
    raise WlocParseError("raw protobuf scan failed: %s" % last_error)


def patch_wloc_body(buf, lat, lon, accuracy):
    """入口：扫描并改写完整 WLOC 响应体。

    返回 (新字节, Stats)。找不到可改的载荷时抛 WlocParseError，
    调用方应原样放行——宁可定位不改，也不能让定位服务返回畸形数据。
    """
    if len(buf) < 10:
        raise WlocParseError("body too short: %d" % len(buf))

    stats = Stats()

    bases = list(_PREFERRED_BASES)
    limit = min(_MAX_BASE_SCAN, max(0, len(buf) - 10))
    for candidate in range(0, limit + 1):
        if candidate not in bases:
            bases.append(candidate)

    errors = []
    for base in bases:
        saved = stats.snapshot()
        try:
            return _patch_frame_at(buf, base, lat, lon, accuracy, stats), stats
        except NoPatchNeeded:
            # 已经能解析出定位数据且坐标一致，属于成功，不降级为错误。
            raise
        except WlocParseError as exc:
            stats.restore(saved)
            if len(errors) < 6:
                errors.append("@%d:%s" % (base, exc))

    try:
        return _patch_raw_scan(buf, lat, lon, accuracy, stats), stats
    except NoPatchNeeded:
        raise
    except WlocParseError as exc:
        errors.append("raw:%s" % exc)

    raise WlocParseError("no patchable wloc payload found; " + " | ".join(errors))


def is_gzip(data):
    return len(data) >= 2 and data[0] == 0x1F and data[1] == 0x8B
